# Google Ads landing-page mapper

A read-only FastAPI backend for connecting customer Google Ads accounts, selecting
accounts to share, and grouping ad configuration by landing page. Python, Pydantic,
the official Google Ads client, and pytest provide typed service contracts and tests
that run without Google credentials. There is no frontend application; OpenAPI
provides the connect/account/report controls, and a report endpoint returns an HTML table.

## Run locally

Use Python 3.12+ (tested with Python 3.14). From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
cp .env.example .env
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://localhost:8000/docs, click **Authorize**, and enter `local-dev-key`.
Mock mode is the default; it never contacts Google or needs a developer token.

In a second terminal, exercise account discovery, selection, reporting, all three
exports and disconnection against that server:

```sh
.venv/bin/python scripts/smoke_test.py
```

To run in Docker instead:

```sh
docker compose up --build -d
```

This binds port 8000 to loopback and persists encrypted local data in a Docker volume.
The local compose file deliberately runs mock mode. For a live local server, use
`.env` and the Python command above. For AWS, see [the hosting guide](docs/aws-hosting.md).

## Backend integration

Send `X-API-Key` on every endpoint except `/health`, `/oauth/callback`, and OpenAPI
pages. Configure `ADS_API_KEYS` as a JSON object of integration principal → secret;
use a separate principal for each tenant, or have your own backend authorize users
before calling this service. A shared key intentionally shares all its connections.
Never put an integration API key in browser code or a URL.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/connections/mock` | Create a synthetic connection in mock mode |
| POST | `/oauth/start` | Return authorization URL; open it in the customer's browser |
| GET | `/oauth/callback` | Consume state + code; return opaque connection ID |
| GET | `/connections/{id}/accounts` | Discover direct and MCC descendant accounts |
| PUT | `/connections/{id}/accounts` | Choose accounts using `{"customer_ids":["1234567890"]}` |
| POST | `/connections/{id}/reports` | Run a report using `{"customer_id":"1234567890","follow_redirects":false}` |
| GET | `/connections/{id}/reports/{report_id}` | Download stored JSON |
| GET | same path + `?format=csv` | Download CSV with full configuration JSON per row |
| GET | same path + `?format=table` | Return an escaped HTML table ordered by landing-page group |
| DELETE | `/connections/{id}` | Revoke locally, delete reports, attempt Google grant revocation |

The report action returns `{"report_id":"...","report":{...}}`. Save the connection
ID and report ID in your calling service. IDs alone do not grant access. Manager
accounts appear in discovery but cannot be selected for a report; select their
non-manager children. Changing the selection withdraws access to old reports for
unselected accounts. Discovery refreshes available accounts but never selects new ones.

Reports run synchronously. HTTP 422 means invalid input, 401 means reconnect or
invalid integration credentials, 403 means account not shared, 404 hides nonexistent
or other-principal connections, 502 means Google rejected a read, and 503 means
transient/quota retries were exhausted. No partial report is persisted on collection
failure. Request bodies and raw Google errors are not logged or echoed on failures.

### Where this fits in an external service

For example, a marketing SaaS or agency portal can use this backend to show a customer
which ads point at each landing page. Your application owns customer login, tenant
permissions, the account picker, and the report UI. This backend owns Google consent,
encrypted Google credentials, account discovery, and report collection/mapping.

```text
Customer browser  <-->  Your application backend  <-->  This mapper API  <-->  Google Ads
       |                  customer/tenant checks         X-API-Key
       +--> Google consent --> registered OAuth callback
```

Host one mapper deployment for your service; you do not need a deployment per
customer. Configure `ADS_API_KEYS`, for example:

```dotenv
ADS_API_KEYS={"customer-acme":"replace-with-acme-secret-at-least-32-characters","customer-beta":"replace-with-beta-secret-at-least-32-characters"}
```

Generate actual secrets using the command in [Google OAuth and production access](#google-oauth-and-production-access).
These principals are configured by the operator; there is no tenant/key provisioning
API. Adding a principal requires updating the configuration and restarting the mapper.
Keep principal names stable: stored connections belong to that name, so rotating its
secret under the same name preserves ownership.

Alternatively, your backend can use one service principal for all customers. In that
case the mapper key can access every connection it created, so your backend must check
the logged-in user's tenant against its own connection records on **every** request.
Never accept an arbitrary connection ID from a browser as proof of ownership, even
with separate tenant keys.

Store a mapping in your application's database such as:

| Your application field | Purpose |
|---|---|
| `tenant_id` / customer ID | Which customer owns this integration |
| `mapper_principal` | Which server-side key to use |
| `connection_id` | Opaque ID returned after connection |
| Selected Google Ads customer IDs | Account choices shown in your UI; the mapper enforces its saved selection |
| `report_id` and account ID | Retrieve a previously generated report |

Google Ads `customer_id` is a ten-digit advertising account ID, without hyphens;
it is separate from your application's customer or tenant ID. Google refresh tokens
stay in the mapper, not in your application's customer records.

### Customer journey in your product

1. **Connect Google Ads.** After authenticating the customer in your application,
   your backend calls `POST /oauth/start` with the appropriate mapper key. Send the
   returned `authorization_url` to the browser and open it. The customer signs into
   Google and grants consent; they do not need to create their own OAuth client or
   provide your application with Google credentials. Your service operator completes
   the Google project setup described below.
2. **Complete the connection.** Google redirects to `ADS_REDIRECT_URI`. The mapper
   exchanges the code and returns `{"connection_id":"...","connected":true}`.
   Save the connection against the authenticated customer using the callback handoff
   described below.
3. **Choose accounts to share.** Your backend calls `GET /connections/{id}/accounts`
   and your UI shows the accessible accounts. The customer chooses eligible
   non-manager accounts, then your backend sends their IDs in
   `PUT /connections/{id}/accounts`. Discovery must happen before selection. The
   selection controls what your service may report on; it does not change Google's
   OAuth grant or the user's Google Ads role.
4. **Generate a landing-page report.** Your backend calls
   `POST /connections/{id}/reports` for one selected account at a time. Show a loading
   state while the synchronous request completes. Save `report_id` and use the
   returned `report` to render groups of landing pages and their ad configurations.
   Include `unmapped_ads` and `data_gaps` so missing destinations or fields remain
   visible. This is a configuration report; it does not include spend, clicks,
   conversions, performance history, or website content analysis.
5. **View or export.** Retrieve saved reports as JSON, CSV, or HTML through your
   backend. The HTML endpoint returns a basic standalone table, with framing disabled;
   build your dashboard from JSON or serve an authorized download. A direct browser
   link to the mapper cannot supply `X-API-Key`. Fetch exports through your backend.
6. **Refresh or disconnect.** A saved report is a snapshot; run another POST to
   refresh it. There is no built-in schedule, report-list endpoint, webhook, or job
   polling API. On disconnect, call `DELETE /connections/{id}`, clear your app's
   mapping, and show whether Google revocation was confirmed. This deletes local
   credentials and reports. Copies exported to your service need your own retention
   and deletion handling. On expired Google authorization, offer the connect flow
   again and save the new connection ID.

### OAuth callback handoff for a customer-facing app

The current callback returns JSON in the browser. It does **not** redirect to your
application, send a webhook, or bind a connection to your application's user session.
`POST /oauth/start` takes no custom state or return URL. State is single use, expires
after ten minutes, and is bound to the initiating integration principal; PKCE protects
the code exchange. That principal binding does not identify an individual user when
several customers share a key.

For a local demonstration, you can copy the callback's connection ID into your
server-side test client. For a seamless customer-facing flow, implement a callback
route in **your application backend**:

1. When starting consent, extract the generated `state` from `authorization_url` and
   store a short-lived pending record tying it to the authenticated user and tenant.
   Keep the mapper's state unchanged; do not log the URL or state.
2. Set `ADS_REDIRECT_URI` to that application's public HTTPS callback URL and register
   the exact same URL in Google's OAuth client. That route must forward the Google
   `state` and `code` (or denial `error`) server-to-server to the mapper's
   `GET /oauth/callback`. The mapper uses the configured redirect URI during token
   exchange, even though your application receives the browser redirect.
3. Before forwarding, validate the pending state against the initiating authenticated
   session and tenant, including any required reauthentication after the Google
   redirect. Consume the pending attempt once, let the mapper validate its own state,
   and save a successful `connection_id` only to that tenant. Do not blindly attach
   a connection ID submitted by a browser to the current user.
4. Redirect to a fixed completion screen in your app. On denial, expiry, or exchange
   failure, show a retry action that starts a new consent attempt. Disable callback
   query logging on your application's proxy/server too.

This handoff route and customer UI are integration work in your application; they
are not bundled in this repository.

### Try the external-service calls in mock mode

With the local server running, run this from the repository root using
`.venv/bin/python`. It uses the installed `httpx` dependency and the default mock key.
In your real backend, load the base URL and tenant key from server-side configuration
and load the connection ID from your authenticated customer's stored mapping.

```python
import httpx

with httpx.Client(
    base_url="http://localhost:8000",
    headers={"X-API-Key": "local-dev-key"},
    timeout=120,
) as mapper:
    def checked(response):
        response.raise_for_status()
        return response

    # Mock only. In live mode, use the OAuth journey above instead.
    connection_id = checked(mapper.post("/connections/mock")).json()["connection_id"]
    path = f"/connections/{connection_id}"
    try:
        accounts = checked(mapper.get(f"{path}/accounts")).json()
        print("Available accounts:", accounts)

        # Fixture account; in your product use the customer's account-picker choices.
        customer_id = "1234567890"
        checked(mapper.put(f"{path}/accounts", json={"customer_ids": [customer_id]}))
        created = checked(mapper.post(f"{path}/reports", json={
            "customer_id": customer_id,
            "follow_redirects": False,
        })).json()
        report_id = created["report_id"]
        print("Landing-page groups:", len(created["report"]["landing_pages"]))
        print("Unmapped records:", len(created["report"]["unmapped_ads"]))

        report_path = f"{path}/reports/{report_id}"
        saved_report = checked(mapper.get(report_path)).json()
        csv_export = checked(mapper.get(report_path, params={"format": "csv"})).text
        html_table = checked(mapper.get(report_path, params={"format": "table"})).text
        # Return authorized data/downloads to your customer from your own backend.
    finally:
        # Demo cleanup only; keep the connection for future use in your real app.
        checked(mapper.delete(path))
```

The included [smoke test](scripts/smoke_test.py) automates the same lifecycle.
See the [JSON sample](examples/sample-report.json) and
[report schema](docs/report-schema.json) when building your customer report UI.
For production, use HTTPS, live credentials, persistent encrypted storage, and the
single-replica hosting requirements in the [hosting guide](docs/aws-hosting.md).

## Google OAuth and production access

**Current onboarding differs from older developer-token tutorials.** As documented by
Google on October 17, 2026, developer tokens were sunset September 9, 2026. New API
access applications belong to the **Google Cloud project**, not the manager API Center.
A project with Test access can query test accounts only. Apply for suitable production
access on the Google Ads API Overview page of the project that owns your OAuth client.
Legacy `ADS_DEVELOPER_TOKEN` is optional and accepted for compatibility. See
[Google's developer-token migration and signup guidance](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)
and [API access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).

1. Create/select a Google Cloud project, enable Google Ads API, and complete the
   applicable API access onboarding in Cloud Console. Configure its OAuth consent
   screen and test users; complete verification when required for your intended use.
2. Create an OAuth **Web application** client. Register exactly
   `http://localhost:8000/oauth/callback` for local use, and your HTTPS callback for AWS.
3. Request the single scope `https://www.googleapis.com/auth/adwords`. Google has
   **no read-only OAuth scope**. Have customers grant their Google Ads user the
   **Read-only** role. The collector calls only Search, SearchGoogleAdsFields and
   ListAccessibleCustomers; it has no Ads mutation route or method.
4. Set `ADS_MODE=live`, `ADS_CLIENT_ID`, `ADS_CLIENT_SECRET`, `ADS_REDIRECT_URI`, a stable
   `ADS_ENCRYPTION_KEY`, and random integration keys of at least 32 characters.
   Generate a Fernet key and an integration secret locally:

   ```sh
   .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

   Store these securely; do not commit `.env` or change the encryption key without
   migrating stored ciphertext. The mock-only auto-generated key is stored beside
   the SQLite DB with mode 0600; live mode always requires an explicit key.
5. Restart, call `/oauth/start`, grant consent, then discover and select accounts.
   External OAuth applications in Testing may receive short-lived refresh tokens;
   reconnect when Google expires or revokes them. See [OAuth access model](https://developers.google.com/google-ads/api/docs/oauth/access-model)
   and [web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

Refresh tokens, OAuth verifiers, account selections, and report content are encrypted
using Fernet before SQLite storage. Access tokens remain inside Google's in-memory
credentials. Deleting a connection erases its local credential and reports immediately;
if Google's revocation endpoint fails, the response explicitly says remote revocation
was not confirmed. The user can also revoke directly in Google Account permissions.
Externally revoked/expired credentials produce a sanitized 401 and invalidate local
access. Server and reverse-proxy access logs must stay off for OAuth callback URLs.

## Collection and field coverage

The [human-readable inventory](docs/field-inventory.md) lists target fields, joins,
and known gaps by ad type. The [exact schema inventory](docs/field-inventory.json)
contains every creative message field from the official v23 protobuf schema. The
live collector queries GoogleAdsFieldService before extraction and includes the
actual selectable fields in each report's `collection_inventory`. It preserves the
selected raw configuration and reports absent target values explicitly in `data_gaps`.
The default API version is pinned to v23; metadata collection adapts field selection,
but changing versions also requires regenerating/reviewing the schema inventory and
retesting against that API version before rollout.

Configuration is fetched separately for customers, campaigns, budgets, portfolio
strategies, ad groups, ads, asset groups, criteria, shared negatives, bid modifiers,
linked assets and PMAX signals/listing filters. Rows are joined by resource names;
repeated fields remain arrays, with ad-copy arrays flattened to strings while pins
and other creative detail remain in `raw`. No date range or performance metrics are
used. Removed ads/asset groups/criteria/links are excluded; paused ads are included.
Every API page is paced and retried with bounded exponential backoff and jitter.
The server does not expose an arbitrary GAQL endpoint.

Budget currency values are decimal strings, e.g. `"50"`, to avoid float precision
loss; `currency_code`, raw micros and budget period are retained. Nested `*_micros`
values keep their originals and gain decimal-unit sibling fields. Shared budgets
remain shared and should not be summed once per ad.

Known limitations:

- Performance Max uses `entity_kind="asset_group"`, a real `asset_group_id`, and a
  null `ad_id`/`ad_group`. Text/images come from linked assets. No positive keyword
  list or ad-level policy summary exists; URL expansion/feed destinations can exceed
  configured asset-group URLs. Signals are retained as signals.
- Shopping and product-feed formats have no feed connector in this MVP. When no web
  final URL is returned they go into `unmapped_ads`, carrying their complete collected
  configuration and a feed-access gap. Dynamic Search Ads and travel/app-only formats
  also remain unmapped when they have no configured HTTP(S) URL.
- Strength is not meaningful for every format. Unspecified/missing strength is a gap.
  CALL_AD remains an enum in v23 but has no creative payload in that version.
- Asset links are **scoped associations**, including paused links, not proof that an
  extension served. Sitelink/card URLs remain under `extensions`; the primary mapping
  uses the ad's or asset group's final and mobile URLs. Legacy MediaFile content is
  not downloaded. Configurations do not enumerate generated combinations or media bytes.
- Geo target resource IDs are retained; human-readable names are an explicit lookup
  gap. No device/schedule rows means no explicit criteria, not that targeting is empty
  at serving time. Keyword URL overrides and all collected keyword tracking stay in
  criterion configuration; they are not substituted into the primary ad-URL mapping.
- A portfolio strategy owned by an MCC may not appear in the client account's
  `bidding_strategy` resource. Its reference/type remain, and unavailable settings
  are explicitly flagged.
- Each report collects several resources sequentially, so it is not an atomic Google
  account snapshot. The in-process pacer should be coordinated externally if scaled.
  Very large accounts need asynchronous jobs, object storage and report paging.

## URL rules and determinism

Edit [config/url-normalization.json](config/url-normalization.json) and restart.
Defaults preserve scheme, hostname, case-sensitive path and meaningful query
parameters. Scheme/host become lowercase; trailing slashes and fragments are removed;
tracking parameters are stripped case-insensitively; remaining query pairs are sorted,
including duplicates and blank values. Ports and HTTP versus HTTPS remain distinct.
Original strings remain in each ad's URL arrays and each page's `original_urls`.
`landing_page` is a deterministic representative original URL. An ad is listed only
once per normalized group even if several of its URLs collapse to that group.

Redirect following defaults to false and makes no network request. To opt in, configure
an exact `redirect_allowed_hosts` allowlist, then send `follow_redirects=true`. The
resolver uses HEAD, validates public DNS at each hop, pins the resolved IP for the
connection, permits only HTTP(S) on ports 80/443, and enforces a hop limit. Templates
support `{lpurl}`, `{escapedlpurl}`, and available custom parameters; other ValueTrack
placeholders remain an explicit resolution gap. It does not simulate Google parallel
tracking, append all inherited suffixes, execute JavaScript, or fetch page bodies.
Failures preserve the known configured destination and add a gap. Successful resolution
groups by the resolved key and records original/tracker/resolved URLs and hops.

The same `Snapshot` and URL configuration produce byte-identical report JSON: stable
resource ordering, deduplication and sorted gaps/keys; no mapping-time clock or randomness.
`generated_at` is the snapshot's capture time (fixed in fixtures). Two fresh live runs
naturally have different capture times; connection/report IDs are opaque random IDs
outside the mapping contract.

## Test and sample output

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.sample_report
.venv/bin/python scripts/generate_inventory.py
```

Tests cover schema-valid synthetic GAQL response pages, normalization, meaningful query
parameters, multi-URL mapping/deduplication, micros precision, PMAX/Shopping gaps,
inherited targeting/assets, input/output validation, exports, account ownership,
encrypted storage, OAuth PKCE/state, revocation, pagination retries and redirect safety.

Generated samples: [JSON](examples/sample-report.json), [CSV](examples/sample-report.csv).
The sample has **3 landing-page groups and 3 unmapped records**. Live Google consent,
project access, and account queries require your credentials and have not been tested
against a real account. Docker/AWS deployment also needs validation in your environment.
