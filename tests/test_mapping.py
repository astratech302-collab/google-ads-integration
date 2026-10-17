import csv
import io
import json
import random
from collections import Counter
from pathlib import Path

import pytest
from google.ads.googleads.v23.services.types.google_ads_service import GoogleAdsRow
from google.protobuf.json_format import ParseDict

from app.mapping import build_report, export_csv, normalize_micros
from app.models import Report
from app.urls import RedirectResolver, URLRules, expand_tracker, normalize_url


def test_fixture_has_only_real_google_fields():
    fixture = json.loads(Path("fixtures/google_ads.json").read_text())
    for pages in fixture["responses"].values():
        for page in pages:
            for row in page["results"]:
                ParseDict(row, GoogleAdsRow.pb()())


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://Example.COM/a/?utm_source=x&Color=blue&gclid=a#top", "https://example.com/a?Color=blue"),
    ("https://example.com/?q=&b=2&a=1&a=2", "https://example.com?a=1&a=2&b=2&q="),
    ("http://example.com:8080/Case/?fbclid=x", "http://example.com:8080/Case"),
    ("https://example.com/a?uTm_source=x&MSCLKID=y&variant=red", "https://example.com/a?variant=red"),
])
def test_normalization(url, expected, rules):
    assert normalize_url(url, rules) == expected
    assert normalize_url(expected, rules) == expected


@pytest.mark.parametrize("url", ["javascript:alert(1)", "not-url", "https://u:p@example.com/", "https://example.com/{lpurl}", "https://example.com:bad/", "https://example.com/a\n"])
def test_invalid_urls(url, rules):
    with pytest.raises(ValueError): normalize_url(url, rules)


def test_configurable_rules():
    rules = URLRules(remove_trailing_slash=False, strip_fragment=False, sort_query_parameters=False, ignored_parameter_prefixes=[], ignored_parameters=["noise"])
    assert normalize_url("https://Example.com/a/?utm_source=x&noise=y#top", rules) == "https://example.com/a/?utm_source=x#top"


def test_mapping_acceptance(snapshot, rules):
    report = build_report(snapshot, rules)
    pages = {p.normalized_key: p for p in report.landing_pages}
    shirts = pages["https://example.com/shirts?color=blue"]
    assert shirts.ad_count == 3
    assert {a.ad_id for a in shirts.ads} == {"1", "2", None}
    assert len(shirts.original_urls) == 4
    assert {a.ad_id for a in pages["https://example.com/sale"].ads} == {"1", "3", "8"}
    assert len(pages["https://m.example.com/sale"].ads) == 1
    assert {a.ad_id for a in report.unmapped_ads} == {"4", "5", "6"}
    for p in report.landing_pages:
        assert p.ad_count == len({a.record_id for a in p.ads})
    entries = [a for p in report.landing_pages for a in p.ads] + [u.configuration for u in report.unmapped_ads]
    assert {a.ad_id for a in entries if a.ad_id} == {"1", "2", "3", "4", "5", "6", "8"}
    assert all(u.reason and u.configuration.data_gaps for u in report.unmapped_ads)
    assert all(a.data_gaps for a in entries)
    assert Report.model_validate_json(report.model_dump_json()) == report


def test_parent_targeting_assets_and_tracking(snapshot, rules):
    report = build_report(snapshot, rules)
    ad = next(a for p in report.landing_pages for a in p.ads if a.ad_id == "1")
    assert ad.campaign["budget"] == "50"
    assert ad.campaign["currency_code"] == "USD"
    assert ad.keywords[0]["text"] == "cotton shirts"
    assert {k["text"] for k in ad.negative_keywords} == {"free", "jobs", "used", "counterfeit"}
    assert {e["type"] for e in ad.extensions} == {"SITELINK", "CALLOUT", "STRUCTURED_SNIPPET"}
    assert ad.headlines[0] == "Soft Cotton Shirts"
    assert ad.raw["ad"]["responsive_search_ad"]["headlines"][0]["pinned_field"] == "HEADLINE_1"
    assert ad.tracking["effective_tracking_url_template"].startswith("https://tracker.")
    assert not ad.tracking["resolution_enabled"]
    assert ad.targeting["locations"] and ad.targeting["devices"] and ad.targeting["ad_schedule"]
    pmax = next(a for p in report.landing_pages for a in p.ads if a.entity_kind == "asset_group")
    assert pmax.ad_id is None and pmax.asset_group_id == "300" and pmax.ad_group is None
    assert pmax.headlines == ["Find Your Everyday Shirt"]
    assert any("keywords" in gap for gap in pmax.data_gaps)
    display = next(a for p in report.landing_pages for a in p.ads if a.ad_id == "3")
    assert any(e["asset"].get("image_asset") for e in display.extensions if e["asset"])


def test_determinism_and_duplicate_rows(snapshot, rules):
    expected = build_report(snapshot, rules).model_dump_json()
    for rows in snapshot.resources.values(): random.Random(7).shuffle(rows)
    assert build_report(snapshot, rules).model_dump_json() == expected
    snapshot.resources["ad_group_ad"] *= 2
    assert build_report(snapshot, rules).model_dump_json() == expected


def test_micros_precision():
    assert normalize_micros({"nested": [{"amount_micros": "9007199254740993"}]})["nested"][0]["amount"] == "9007199254.740993"


def test_csv_keeps_full_configuration_and_unmapped(snapshot, rules):
    report = build_report(snapshot, rules)
    rows = list(csv.DictReader(io.StringIO(export_csv(report))))
    assert len(rows) == sum(p.ad_count for p in report.landing_pages) + len(report.unmapped_ads)
    assert sum(bool(r["reason"]) for r in rows) == 3
    assert all(json.loads(r["configuration_json"])["record_id"] == r["record_id"] for r in rows)


def test_no_network_by_default(snapshot, rules, monkeypatch):
    monkeypatch.setattr(RedirectResolver, "resolve", lambda *args: pytest.fail("unexpected network call"))
    build_report(snapshot, rules)


def test_opt_in_resolution_and_failure(snapshot, rules):
    class Resolver:
        def resolve(self, url): return "https://example.com/resolved", [url, "https://example.com/resolved"]
    report = build_report(snapshot, rules, True, Resolver())
    assert len(report.landing_pages) == 1
    assert report.landing_pages[0].normalized_key == "https://example.com/resolved"
    assert all(a.url_resolutions for a in report.landing_pages[0].ads)
    class Broken:
        def resolve(self, url): raise ValueError("private detail")
    report = build_report(snapshot, rules, True, Broken())
    assert len(report.landing_pages) == 3
    assert "private detail" not in report.model_dump_json()
    assert any("resolution failed" in g for a in report.landing_pages[0].ads for g in a.data_gaps)


def test_tracker_expansion():
    assert expand_tracker("https://t.example/?u={lpurl}&c={_x}", "https://example.com/?a=1", [{"key": "x", "value": "a b"}]) == "https://t.example/?u=https%3A%2F%2Fexample.com%2F%3Fa%3D1&c=a%20b"
    with pytest.raises(ValueError): expand_tracker("https://t.example/{campaignid}", "https://example.com", [])


def test_redirect_blocks_private_dns(monkeypatch):
    rules = URLRules(redirect_allowed_hosts=["example.com"])
    monkeypatch.setattr("app.urls.socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="nonpublic"): RedirectResolver(rules).resolve("https://example.com")
    with pytest.raises(ValueError, match="allowlisted"): RedirectResolver(rules).resolve("https://elsewhere.example")
