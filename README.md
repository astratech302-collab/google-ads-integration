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

For a live connect flow, your service calls `/oauth/start` with its integration key,
sends the authorization URL to the customer, and associates the callback's connection
ID with its user. The backend callback returns JSON; an existing frontend can provide
the completion screen. State is single use, expires after ten minutes, and is bound to
the initiating integration principal; PKCE protects the code exchange.

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
