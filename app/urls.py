import http.client
import ipaddress
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


class URLRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lowercase_scheme_host: bool = True
    remove_trailing_slash: bool = True
    strip_fragment: bool = True
    sort_query_parameters: bool = True
    ignored_parameter_prefixes: list[str] = Field(default_factory=lambda: ["utm_"])
    ignored_parameters: list[str] = Field(default_factory=list)
    redirect_allowed_hosts: list[str] = Field(default_factory=list)
    max_redirects: int = Field(default=5, ge=0, le=10)

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate(json.loads(path.read_text()))


def normalize_url(url: str, rules: URLRules) -> str:
    if any(ord(c) < 32 for c in url) or "{" in url or "}" in url:
        raise ValueError("URL contains control characters or unresolved placeholders")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Landing page must be an absolute HTTP(S) URL without credentials")
    # Access port to validate it, including bracketed IPv6 syntax.
    _ = parsed.port
    host = parsed.netloc.lower() if rules.lowercase_scheme_host else parsed.netloc
    scheme = parsed.scheme.lower() if rules.lowercase_scheme_host else parsed.scheme
    ignored = {x.lower() for x in rules.ignored_parameters}
    prefixes = tuple(x.lower() for x in rules.ignored_parameter_prefixes)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in ignored and not (prefixes and k.lower().startswith(prefixes))]
    if rules.sort_query_parameters:
        pairs.sort()
    path = parsed.path.rstrip("/") if rules.remove_trailing_slash else parsed.path
    return urlunsplit((scheme, host, path, urlencode(pairs), "" if rules.strip_fragment else parsed.fragment))


def expand_tracker(template: str, final_url: str, parameters: list[dict]) -> str:
    result = template.replace("{lpurl}", quote(final_url, safe="") if not template.startswith("{lpurl}") else final_url)
    result = result.replace("{escapedlpurl}", quote(final_url, safe=""))
    for item in parameters:
        result = result.replace("{_" + item["key"] + "}", quote(item.get("value", ""), safe=""))
    if "{" in result or "}" in result:
        raise ValueError("Tracking template contains unsupported ValueTrack placeholders")
    return result


class RedirectResolver:
    """HEAD only; exact hostname allowlist, public DNS, pinned IP, no proxies."""
    def __init__(self, rules: URLRules):
        self.rules = rules

    def _head(self, url):
        parsed = urlsplit(url)
        normalize_url(url, self.rules)
        host = parsed.hostname.lower()
        if host not in {h.lower() for h in self.rules.redirect_allowed_hosts}:
            raise ValueError("Redirect hostname is not explicitly allowlisted")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in {80, 443}:
            raise ValueError("Redirect port is not allowed")
        addresses = sorted({x[4][0] for x in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("Redirect DNS resolved to a nonpublic address")
        connection = http.client.HTTPConnection(host, port, timeout=5)
        sock = socket.create_connection((addresses[0], port), timeout=5)
        try:
            if parsed.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection.sock = sock
            connection.request("HEAD", urlunsplit(("", "", parsed.path or "/", parsed.query, "")), headers={"Host": parsed.netloc, "User-Agent": "AdsLandingMapper/0.1"})
            response = connection.getresponse()
            return response.status, response.getheader("Location")
        finally:
            connection.close()
            sock.close()

    def resolve(self, url: str) -> tuple[str, list[str]]:
        hops = []
        for _ in range(self.rules.max_redirects + 1):
            if url in hops:
                raise ValueError("Redirect loop")
            hops.append(url)
            status, location = self._head(url)
            if status in {301, 302, 303, 307, 308} and location:
                url = urljoin(url, location)
                continue
            if not 200 <= status < 300:
                raise ValueError("Redirect destination did not accept HEAD")
            return url, hops
        raise ValueError("Redirect hop limit exceeded")
