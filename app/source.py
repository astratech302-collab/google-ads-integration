import copy
import json
import logging
import random
import threading
import time
from datetime import datetime, timezone

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as api_errors
from google.auth.exceptions import RefreshError, TransportError
from google.protobuf.json_format import MessageToDict

from app.models import Account, Snapshot

# Google's request/response debug logging can include customer configuration.
logging.getLogger("google.ads.googleads.client").disabled = True
logging.getLogger("google.ads.googleads").setLevel(logging.CRITICAL)

RESOURCES = ["customer", "campaign", "campaign_budget", "bidding_strategy", "ad_group", "ad_group_ad", "asset_group", "ad_group_criterion", "campaign_criterion", "campaign_shared_set", "shared_criterion", "customer_negative_criterion", "ad_group_bid_modifier", "customer_asset", "campaign_asset", "ad_group_asset", "asset_group_asset", "asset", "asset_group_signal", "asset_group_listing_group_filter"]


class SourceError(Exception):
    pass


class AuthorizationExpired(SourceError):
    pass


class RetryableSourceError(SourceError):
    pass


class Pacer:
    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            delay = self.interval - (time.monotonic() - self.last)
            if delay > 0: time.sleep(delay)
            self.last = time.monotonic()


def retry(operation, max_retries=4, sleep=time.sleep):
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except RetryableSourceError:
            if attempt >= max_retries: raise
            sleep(min(30, 2 ** attempt) + random.uniform(0, 0.25))


def as_dict(message):
    return MessageToDict(type(message).pb(message), preserving_proto_field_name=True)


class MockSource:
    def __init__(self, settings):
        self.settings = settings

    def accounts(self, token):
        return [Account(customer_id="9999999999", name="Demo Manager", manager=True), Account(customer_id="1234567890", name="Demo Store", login_customer_id="9999999999"), Account(customer_id="2345678901", name="Demo Empty Account", login_customer_id="9999999999")]

    def snapshot(self, token, account):
        fixture = json.loads(self.settings.fixture_file.read_text())
        resources = {name: [row[name] for page in pages for row in page.get("results", [])] for name, pages in fixture["responses"].items()}
        if account.customer_id == "2345678901":
            resources = {k: v if k == "customer" else [] for k, v in resources.items()}
        return Snapshot(customer_id=account.customer_id, captured_at=fixture["captured_at"], source="mock", resources=copy.deepcopy(resources), collection_inventory={k: sorted({f"{k}.{field}" for row in v for field in row}) for k, v in resources.items()}, warnings=["MOCK DATA: not collected from a live Google Ads account."])


class GoogleSource:
    """Only Search, SearchGoogleAdsFields and ListAccessibleCustomers are exposed."""
    def __init__(self, settings):
        self.settings = settings
        self.pacer = Pacer(settings.min_request_interval)

    def client(self, token, login=None):
        config = {"client_id": self.settings.client_id, "client_secret": self.settings.client_secret.get_secret_value(), "refresh_token": token, "use_proto_plus": True}
        if self.settings.developer_token.get_secret_value():
            config["developer_token"] = self.settings.developer_token.get_secret_value()
        if login: config["login_customer_id"] = login
        # The official client refreshes credentials eagerly during construction.
        return self.call(lambda: GoogleAdsClient.load_from_dict(config, version=self.settings.api_version))

    def call(self, operation):
        def attempt():
            self.pacer.wait()
            try:
                return operation()
            except RefreshError as exc:
                if getattr(exc, "retryable", False):
                    raise RetryableSourceError("Google OAuth is temporarily unavailable") from None
                raise AuthorizationExpired("Google authorization expired or was revoked; reconnect") from None
            except GoogleAdsException as exc:
                names = [str(e.error_code) for e in exc.failure.errors]
                if any("authentication_error" in n for n in names):
                    raise AuthorizationExpired("Google authorization expired or was revoked; reconnect") from None
                if any("quota_error" in n or "internal_error" in n for n in names):
                    raise RetryableSourceError("Google Ads is temporarily unavailable or rate limited") from None
                raise SourceError("Google Ads rejected the read query; verify account permissions, Cloud-project API access and API version") from None
            except TransportError:
                raise RetryableSourceError("Google OAuth transport is temporarily unavailable") from None
            except (api_errors.ResourceExhausted, api_errors.ServiceUnavailable, api_errors.DeadlineExceeded, api_errors.InternalServerError):
                raise RetryableSourceError("Google Ads is temporarily unavailable or rate limited") from None
            except api_errors.Unauthenticated:
                raise AuthorizationExpired("Google authorization expired or was revoked; reconnect") from None
            except api_errors.GoogleAPICallError:
                raise SourceError("Google Ads read request failed") from None
        return retry(attempt, self.settings.max_retries)

    def search(self, client, customer_id, query):
        service = client.get_service("GoogleAdsService")
        token, rows = "", []
        while True:
            # Obtain one page per retried operation; no iterator RPC can bypass retry/pacing.
            def fetch():
                pager = service.search(request={"customer_id": customer_id, "query": query, "page_token": token}, retry=None, timeout=self.settings.request_timeout)
                return next(iter(pager.pages))
            page = self.call(fetch)
            rows.extend(as_dict(row) for row in page.results)
            token = page.next_page_token
            if not token: return rows

    def fields(self, client, resource):
        query = f"SELECT name, selectable, data_type WHERE name LIKE '{resource}.%' AND selectable = true"
        token, names = "", set()
        while True:
            def fetch():
                pager = client.get_service("GoogleAdsFieldService").search_google_ads_fields(request={"query": query, "page_token": token}, retry=None, timeout=self.settings.request_timeout)
                return next(iter(pager.pages))
            page = self.call(fetch)
            names.update(row.name for row in page.results if row.selectable and row.name.startswith(resource + "."))
            token = page.next_page_token
            if not token: break
        fields = sorted(names)
        if not fields:
            raise SourceError(f"No selectable configuration fields for {resource}")
        return fields

    def accounts(self, token):
        client = self.client(token)
        roots = self.call(lambda: client.get_service("CustomerService").list_accessible_customers(retry=None, timeout=self.settings.request_timeout).resource_names)
        accounts = {}
        for root in sorted(roots):
            root_id = root.split("/")[-1]
            rows = self.search(client, root_id, "SELECT customer.id, customer.descriptive_name, customer.manager, customer.status FROM customer")
            if not rows: continue
            c = rows[0]["customer"]
            accounts.setdefault(root_id, Account(customer_id=root_id, name=c.get("descriptive_name", root_id), manager=c.get("manager", False), status=c.get("status")))
            if c.get("manager"):
                manager_client = self.client(token, root_id)
                # customer_client lists all descendants, not only immediate children.
                descendants = self.search(manager_client, root_id, "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, customer_client.status FROM customer_client")
                for row in descendants:
                    child = row["customer_client"]
                    child_id = str(child["id"])
                    if child_id == root_id: continue
                    accounts.setdefault(child_id, Account(customer_id=child_id, name=child.get("descriptive_name", child_id), manager=child.get("manager", False), status=child.get("status"), login_customer_id=root_id))
        return [accounts[k] for k in sorted(accounts)]

    def snapshot(self, token, account):
        client = self.client(token, account.login_customer_id)
        inventory, resources = {}, {}
        for resource in RESOURCES:
            fields = self.fields(client, resource)
            inventory[resource] = fields
            # No metrics/segments, so the rows describe configuration without date filters.
            rows = self.search(client, account.customer_id, "SELECT " + ", ".join(fields) + " FROM " + resource)
            resources[resource] = [row[resource] for row in rows if resource in row]
        return Snapshot(customer_id=account.customer_id, captured_at=datetime.now(timezone.utc), source="google_ads", resources=resources, collection_inventory=inventory)
