import base64
import hashlib
import hmac
import html
import json
import secrets
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.mapping import build_report, export_csv
from app.models import Account, Report, ReportCreated, ReportRequest, Selection
from app.source import AuthorizationExpired, GoogleSource, MockSource, RetryableSourceError, SourceError
from app.store import Store
from app.urls import URLRules

SCOPE = "https://www.googleapis.com/auth/adwords"


def create_app(settings: Settings | None = None, source=None):
    settings = settings or Settings()
    store = Store(settings.database, settings.encryption_key.get_secret_value() if settings.encryption_key else None)
    source = source or (MockSource(settings) if settings.mode == "mock" else GoogleSource(settings))
    rules = URLRules.load(settings.normalization_file)
    app = FastAPI(title="Google Ads Landing Page Mapper", version="0.1.0", description="Read-only backend. Authenticate using X-API-Key. Mock mode works without Google credentials.")
    app.state.store = store
    app.state.source = source
    app.state.settings = settings
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def owner(key: Annotated[str | None, Depends(key_header)]):
        for principal, expected in settings.api_keys.items():
            if key and hmac.compare_digest(key.encode(), expected.get_secret_value().encode()):
                return principal
        raise HTTPException(401, "Valid integration API key required")

    Owner = Annotated[str, Depends(owner)]

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse(status_code=422, content={"detail": [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]})

    @app.exception_handler(SourceError)
    async def source_error(request, exc):
        return JSONResponse(status_code=503 if isinstance(exc, RetryableSourceError) else 502, content={"detail": str(exc)})

    def connection(connection_id, principal):
        data = store.connection(connection_id, principal)
        if data is None: raise HTTPException(404, "Connection not found")
        if data.get("revoked"): raise HTTPException(401, "Access revoked; reconnect to Google Ads")
        if data.get("mode") != settings.mode: raise HTTPException(409, "Connection belongs to a different data-source mode")
        return data

    def authorized_call(connection_id, principal, operation):
        try:
            return operation()
        except AuthorizationExpired:
            store.revoke(connection_id, principal)
            raise HTTPException(401, "Google authorization expired or was revoked; reconnect") from None

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": settings.mode}

    @app.post("/connections/mock", status_code=201)
    def mock_connect(principal: Owner):
        if settings.mode != "mock": raise HTTPException(404, "Mock connections disabled")
        return {"connection_id": store.create_connection(principal, "mock-refresh-token", "mock"), "mode": "mock"}

    @app.post("/oauth/start")
    def oauth_start(principal: Owner):
        if settings.mode != "live": raise HTTPException(409, "Use /connections/mock in mock mode")
        verifier = secrets.token_urlsafe(48)
        state = store.new_state(principal, verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": settings.client_id, "redirect_uri": settings.redirect_uri, "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), "expires_in": 600}

    @app.get("/oauth/callback")
    def oauth_callback(state: str = Query(min_length=20, max_length=200), code: str | None = Query(default=None, max_length=4096), error: str | None = Query(default=None, max_length=200)):
        if settings.mode != "live": raise HTTPException(404, "OAuth disabled in mock mode")
        saved = store.pop_state(state)
        if saved is None: raise HTTPException(400, "OAuth state invalid, expired, or already used")
        if error or not code: raise HTTPException(400, "Google access was declined; start the connect flow again")
        try:
            with httpx.Client(timeout=20, trust_env=False) as client:
                response = client.post("https://oauth2.googleapis.com/token", data={"client_id": settings.client_id, "client_secret": settings.client_secret.get_secret_value(), "code": code, "code_verifier": saved["verifier"], "grant_type": "authorization_code", "redirect_uri": settings.redirect_uri})
            if response.status_code != 200: raise HTTPException(400, "Google token exchange failed; restart the connect flow")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Google token exchange unavailable; restart the connect flow") from None
        if not payload.get("refresh_token"): raise HTTPException(400, "No refresh token granted; reconnect with offline consent")
        if payload.get("scope") and SCOPE not in payload["scope"].split(): raise HTTPException(400, "Required Google Ads scope was not granted")
        return {"connection_id": store.create_connection(saved["owner"], payload["refresh_token"], "live"), "connected": True}

    @app.get("/connections/{connection_id}/accounts", response_model=list[Account])
    def accounts(connection_id: str, principal: Owner):
        data = connection(connection_id, principal)
        result = authorized_call(connection_id, principal, lambda: source.accounts(data["refresh_token"]))
        connection(connection_id, principal)
        data["accounts"] = [account.model_dump() for account in result]
        # Selection never grows merely because a new account becomes accessible.
        allowed = {a.customer_id for a in result if not a.manager}
        data["selected"] = [x for x in data["selected"] if x in allowed]
        if not store.update(connection_id, principal, data):
            raise HTTPException(401, "Access revoked; reconnect")
        return result

    @app.put("/connections/{connection_id}/accounts")
    def select_accounts(connection_id: str, selection: Selection, principal: Owner):
        data = connection(connection_id, principal)
        available = {a["customer_id"] for a in data["accounts"] if not a["manager"] and a.get("status") not in {"CANCELED", "CLOSED", "SUSPENDED"}}
        if not set(selection.customer_ids).issubset(available): raise HTTPException(403, "Discover accounts first and select accessible non-manager accounts")
        data["selected"] = selection.customer_ids
        if not store.update(connection_id, principal, data):
            raise HTTPException(401, "Access revoked; reconnect")
        return {"customer_ids": data["selected"]}

    @app.post("/connections/{connection_id}/reports", status_code=201, response_model=ReportCreated)
    def run_report(connection_id: str, request: ReportRequest, principal: Owner):
        data = connection(connection_id, principal)
        if request.customer_id not in data["selected"]: raise HTTPException(403, "Account has not been selected for sharing")
        account = next((Account.model_validate(a) for a in data["accounts"] if a["customer_id"] == request.customer_id), None)
        if account is None: raise HTTPException(403, "Account is no longer available; discover accounts again")
        if request.follow_redirects and not rules.redirect_allowed_hosts: raise HTTPException(422, "Configure redirect_allowed_hosts before enabling redirect following")
        snapshot = authorized_call(connection_id, principal, lambda: source.snapshot(data["refresh_token"], account))
        if snapshot.customer_id != request.customer_id:
            raise HTTPException(502, "Data source returned a different customer ID")
        report = build_report(snapshot, rules, request.follow_redirects)
        current = connection(connection_id, principal)
        if request.customer_id not in current["selected"]: raise HTTPException(403, "Account sharing was withdrawn during collection")
        report_id = store.save_report(connection_id, principal, report.model_dump(mode="json"))
        if report_id is None: raise HTTPException(403, "Access or account sharing was withdrawn during collection")
        return ReportCreated(report_id=report_id, report=report)

    @app.get("/connections/{connection_id}/reports/{report_id}")
    def get_report(connection_id: str, report_id: str, principal: Owner, format: Literal["json", "csv", "table"] = "json"):
        data = connection(connection_id, principal)
        payload = store.report(report_id, connection_id)
        if payload is None: raise HTTPException(404, "Report not found")
        report = Report.model_validate(payload)
        if report.customer_id not in data["selected"]: raise HTTPException(403, "Account sharing was withdrawn")
        if format == "csv":
            return Response(export_csv(report), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="ads-report.csv"'})
        if format == "table":
            rows = []
            for page in report.landing_pages:
                for ad in page.ads:
                    cells = [page.normalized_key, ad.record_id, ad.ad_type, ad.campaign.get("name") or "", ad.status or "", "; ".join(ad.data_gaps)]
                    rows.append("<tr>" + "".join("<td>" + html.escape(str(cell)) + "</td>" for cell in cells) + "</tr>")
            for item in report.unmapped_ads:
                rows.append("<tr>" + "".join("<td>" + html.escape(str(cell)) + "</td>" for cell in ["Unmapped", item.record_id, item.configuration.ad_type, item.configuration.campaign.get("name") or "", item.configuration.status or "", item.reason]) + "</tr>")
            return HTMLResponse('<!doctype html><html><head><meta charset="utf-8"><title>Landing page report</title><style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}tr:nth-child(even){background:#f5f5f5}</style></head><body><h1>Landing page report</h1><table><thead><tr><th>Landing page</th><th>Record</th><th>Type</th><th>Campaign</th><th>Status</th><th>Gaps</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></body></html>", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})
        return Response(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="ads-report.json"'})

    @app.delete("/connections/{connection_id}")
    def revoke(connection_id: str, principal: Owner):
        data = store.connection(connection_id, principal)
        if data is None: raise HTTPException(404, "Connection not found")
        store.revoke(connection_id, principal)
        confirmed = settings.mode == "mock" or data.get("revoked", False)
        if settings.mode == "live" and data.get("refresh_token"):
            try:
                with httpx.Client(timeout=15, trust_env=False) as client:
                    response = client.post("https://oauth2.googleapis.com/revoke", data={"token": data["refresh_token"]})
                confirmed = response.status_code == 200
            except httpx.HTTPError:
                confirmed = False
        return {"local_access_revoked": True, "google_revocation_confirmed": confirmed, "message": "Local tokens and reports deleted. If Google revocation was not confirmed, remove access in your Google Account permissions."}

    return app
