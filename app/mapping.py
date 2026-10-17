import csv
import io
from http.client import HTTPException
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.models import AdConfiguration, LandingPage, Report, Snapshot, UnmappedAd
from app.urls import RedirectResolver, URLRules, expand_tracker, normalize_url


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unique(rows):
    return [json.loads(k) for k in sorted({stable(x) for x in rows})]


def normalize_micros(value):
    """Keep raw micros and add decimal currency-unit siblings recursively."""
    if isinstance(value, list):
        return [normalize_micros(x) for x in value]
    if not isinstance(value, dict):
        return value
    result = {k: normalize_micros(v) for k, v in value.items()}
    for key, item in value.items():
        if key.endswith("_micros") and item is not None:
            result[key[:-7]] = str(Decimal(str(item)) / Decimal(1_000_000))
    return result


def text_values(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [s for item in value for s in text_values(item)]
    if isinstance(value, dict):
        return text_values(value.get("text", ""))
    return []


def copy_fields(creative):
    heads, descriptions, paths = [], [], []
    for key, value in creative.items():
        if key in {"headlines", "headline", "headline1", "headline2", "headline_part1", "headline_part2", "headline_part3", "long_headline", "long_headlines", "short_headline"}:
            heads.extend(text_values(value))
        elif key in {"descriptions", "description", "description1", "description2", "description3", "description4"}:
            descriptions.extend(text_values(value))
        elif key in {"path1", "path2", "breadcrumb1", "breadcrumb2"}:
            paths.extend(text_values(value))
        elif isinstance(value, dict):
            h, d, p = copy_fields(value)
            heads.extend(h); descriptions.extend(d); paths.extend(p)
    return heads, descriptions, paths


def references(value):
    if isinstance(value, str) and "/assets/" in value and value.startswith("customers/"):
        return {value}
    if isinstance(value, list):
        return set().union(*(references(x) for x in value)) if value else set()
    if isinstance(value, dict):
        return references(list(value.values()))
    return set()


def build_report(snapshot: Snapshot, rules: URLRules, follow_redirects=False, resolver=None) -> Report:
    resources = snapshot.resources
    get = lambda name: resources.get(name, [])
    indexes = {name: {x.get("resource_name"): x for x in get(name)}
               for name in ["campaign", "ad_group", "campaign_budget", "bidding_strategy", "asset"]}
    customer = next(iter(get("customer")), {})
    warnings = list(snapshot.warnings)
    warnings.append("Configured destinations only; served URLs, keyword URL overrides, asset destinations and URL expansion may differ.")
    entities = [("ad", row) for row in get("ad_group_ad") if row.get("status") != "REMOVED"]
    entities += [("asset_group", row) for row in get("asset_group") if row.get("status") != "REMOVED"]
    buckets = defaultdict(lambda: {"originals": set(), "ads": {}})
    unmapped = []
    seen = set()
    for kind, row in sorted(entities, key=lambda item: stable(item)):
        gaps = []
        creative = row.get("ad", {}) if kind == "ad" else row
        group = indexes["ad_group"].get(row.get("ad_group")) if kind == "ad" else None
        campaign = indexes["campaign"].get((group or row).get("campaign"), {})
        cid = str(campaign.get("id", ""))
        gid = str((group or {}).get("id", ""))
        record_id = row.get("resource_name") or f"{kind}:{cid}:{gid}:{creative.get('id', '')}"
        if record_id in seen:
            continue
        seen.add(record_id)
        ad_type = creative.get("type", "UNKNOWN") if kind == "ad" else "PERFORMANCE_MAX_ASSET_GROUP"
        asset_group_ref = row.get("resource_name") if kind == "asset_group" else None
        linked = []
        for link_resource, parent_field, parent in [
            ("customer_asset", None, None), ("campaign_asset", "campaign", campaign.get("resource_name")),
            ("ad_group_asset", "ad_group", (group or {}).get("resource_name")),
            ("asset_group_asset", "asset_group", asset_group_ref),
        ]:
            for link in get(link_resource):
                if parent_field and (not parent or link.get(parent_field) != parent):
                    continue
                if link.get("status") == "REMOVED":
                    continue
                asset = indexes["asset"].get(link.get("asset"))
                linked.append({"scope": link_resource, "type": link.get("field_type"), "association": link, "asset": asset})
                if asset is None:
                    gaps.append(f"extensions.asset: linked asset unavailable ({link_resource})")
        # Creative image/video/card assets are linked by resource name too.
        visited_assets = set()
        pending_assets = references(creative)
        while pending_assets:
            ref = min(pending_assets); pending_assets.remove(ref)
            if ref in visited_assets:
                continue
            visited_assets.add(ref)
            asset = indexes["asset"].get(ref)
            linked.append({"scope": "creative", "type": (asset or {}).get("type"), "association": {"asset": ref}, "asset": asset})
            if asset:
                pending_assets |= references(asset) - visited_assets
            else:
                gaps.append("extensions.asset: creative asset reference unavailable")
        linked = unique(linked)
        heads, descriptions, paths = copy_fields(creative)
        if kind == "asset_group":
            for item in linked:
                text = (item["asset"] or {}).get("text_asset", {}).get("text")
                if text and item["scope"] == "asset_group_asset":
                    if item["type"] in {"HEADLINE", "LONG_HEADLINE"}: heads.append(text)
                    if item["type"] == "DESCRIPTION": descriptions.append(text)
            gaps += ["ad_id: asset groups are not standard ads", "ad_group: not applicable to Performance Max", "policy_approval: no ad-level policy summary on asset groups; inspect linked asset policies", "keywords: positive keywords unavailable for Performance Max; signals are not keyword targeting", "landing_pages: URL expansion and product-feed destinations may exceed asset_group.final_urls"]
        if "SHOPPING" in ad_type or ad_type == "DEMAND_GEN_PRODUCT_AD":
            gaps += ["keywords: positive keyword targeting is not available for Shopping/product-feed ads", "landing_pages: product-feed access unavailable; product destinations are not stored on the ad", "product_configuration: feed copy and images unavailable without feed access"]
        if ad_type == "EXPANDED_DYNAMIC_SEARCH_AD":
            gaps.append("headlines/final_urls: generated at serving time for Dynamic Search Ads")
        if ad_type in {"HOTEL_AD", "TRAVEL_AD"}:
            gaps.append("landing_pages: dynamic travel/feed destinations unavailable in static ad configuration")
        if ad_type == "CALL_AD":
            gaps.append("creative: CALL_AD has no creative payload in the v23 Ad schema")
        if ad_type in {"UNKNOWN", "UNSPECIFIED"}:
            gaps.append("ad_type: unknown or unsupported format; inspect raw configuration")
        shared_sets = {x.get("shared_set") for x in get("campaign_shared_set") if x.get("campaign") == campaign.get("resource_name") and x.get("status") != "REMOVED"}
        account_negative_sets = {x.get("negative_keyword_list", {}).get("shared_set") for x in get("customer_negative_criterion")} - {None}
        criteria = []
        for name, scope in [("campaign_criterion", "campaign"), ("ad_group_criterion", "ad_group"), ("customer_negative_criterion", "customer"), ("shared_criterion", "shared_set")]:
            for criterion in get(name):
                parent = {"campaign": campaign.get("resource_name"), "ad_group": (group or {}).get("resource_name")}.get(scope)
                if scope in {"campaign", "ad_group"} and (not parent or criterion.get(scope) != parent): continue
                if scope == "shared_set" and criterion.get("shared_set") not in shared_sets | account_negative_sets: continue
                if criterion.get("status") == "REMOVED": continue
                criteria.append({"scope": "customer" if scope == "shared_set" and criterion.get("shared_set") in account_negative_sets else scope, "configuration": criterion})
        keywords, negatives = [], []
        for item in criteria:
            c = item["configuration"]
            if c.get("keyword"):
                entry = {**c["keyword"], "scope": item["scope"], "status": c.get("status"), "configuration": c}
                (negatives if c.get("negative") or item["scope"] in {"customer", "shared_set"} else keywords).append(entry)
        targeting = {"criteria": unique(criteria), "locations": [], "devices": [], "ad_schedule": [], "time_zone": customer.get("time_zone"), "ad_group_bid_modifiers": []}
        for item in criteria:
            c = item["configuration"]
            for field, target in [("location", "locations"), ("proximity", "locations"), ("device", "devices"), ("ad_schedule", "ad_schedule")]:
                if field in c: targeting[target].append(item)
        if asset_group_ref:
            targeting["asset_group_signals"] = unique([x for x in get("asset_group_signal") if x.get("asset_group") == asset_group_ref])
            targeting["listing_group_filters"] = unique([x for x in get("asset_group_listing_group_filter") if x.get("asset_group") == asset_group_ref])
        targeting["ad_group_bid_modifiers"] = unique([x for x in get("ad_group_bid_modifier") if x.get("ad_group") == (group or {}).get("resource_name")]) if group else []
        for target in ["locations", "devices", "ad_schedule"]:
            targeting[target] = unique(targeting[target])
        if any(c["configuration"].get("location") for c in criteria):
            gaps.append("targeting.locations.names: geo target resource IDs retained; human-readable geo lookup not collected")
        if ad_type == "SMART_CAMPAIGN_AD":
            gaps.append("keywords: Smart campaign keyword themes are not standard keyword criteria")
        budget = indexes["campaign_budget"].get(campaign.get("campaign_budget"), {})
        if campaign.get("bidding_strategy") and campaign.get("bidding_strategy") not in indexes["bidding_strategy"]:
            gaps.append("campaign.bidding_strategy_resource: portfolio strategy may be owned by a manager; settings unavailable in this account")
        campaign_view = {"id": campaign.get("id"), "name": campaign.get("name"), "status": campaign.get("status"), "channel_type": campaign.get("advertising_channel_type"), "bidding": campaign.get("bidding_strategy_type"), "bidding_strategy_resource": indexes["bidding_strategy"].get(campaign.get("bidding_strategy")), "budget": str(Decimal(str(budget["amount_micros"])) / 1_000_000) if "amount_micros" in budget else None, "currency_code": customer.get("currency_code"), "budget_configuration": normalize_micros(budget), "configuration": normalize_micros(campaign)}
        group_view = {"id": group.get("id"), "name": group.get("name"), "status": group.get("status"), "configuration": normalize_micros(group)} if group else None
        scopes = {"customer": customer, "campaign": campaign, "ad_group": group or {}, "ad": creative if kind == "ad" else {}}
        tracking = {k: creative.get(k) if kind == "ad" else None for k in ["final_url_suffix", "tracking_url_template", "url_custom_parameters"]}
        tracking["scopes"] = {scope: {k: source.get(k) for k in ["final_url_suffix", "tracking_url_template", "url_custom_parameters"]} for scope, source in scopes.items()}
        template = next((source.get("tracking_url_template") for source in reversed(list(scopes.values())) if source.get("tracking_url_template")), None)
        tracking["effective_tracking_url_template"] = template
        tracking["tracker_url"] = template
        tracking["resolution_enabled"] = follow_redirects
        finals = sorted(set(creative.get("final_urls", [])))
        mobile = sorted(set(creative.get("final_mobile_urls", [])))
        approval = row.get("policy_summary", {}).get("approval_status") if kind == "ad" else None
        strength = row.get("ad_strength")
        for field, value in {"ad_id" if kind == "ad" else "asset_group_id": creative.get("id"), "status": row.get("status"), "policy_approval": approval, "ad_strength": strength, "headlines": heads, "descriptions": descriptions, "display_path": paths, "final_urls": finals, "final_mobile_urls": mobile, "tracking.final_url_suffix": tracking["final_url_suffix"], "tracking.tracking_url_template": tracking["tracking_url_template"], "tracking.url_custom_parameters": tracking["url_custom_parameters"]}.items():
            if value is None or value == "" or value == [] or value in ("UNKNOWN", "UNSPECIFIED"):
                gaps.append(f"{field}: unavailable, not set, or not applicable for {ad_type}")
        for field, value in campaign_view.items():
            if field in {"id", "name", "status", "channel_type", "bidding", "budget", "currency_code"} and value is None:
                gaps.append(f"campaign.{field}: unavailable in returned configuration")
        if kind == "ad":
            if group_view is None: gaps.append("ad_group: parent configuration unavailable")
            else:
                for field in ["id", "name", "status"]:
                    if group_view[field] is None: gaps.append(f"ad_group.{field}: unavailable in returned configuration")
        for name in ["ad_group_criterion", "campaign_criterion", "shared_criterion", "customer_negative_criterion", "customer_asset", "campaign_asset", "ad_group_asset", "asset_group_asset", "asset"]:
            if name not in resources: gaps.append(f"{name}: resource was not collected")
        config = AdConfiguration(record_id=record_id, entity_kind=kind, ad_id=str(creative["id"]) if kind == "ad" and "id" in creative else None, asset_group_id=str(creative["id"]) if kind == "asset_group" and "id" in creative else None, ad_type=ad_type, status=row.get("status"), policy_approval=approval, ad_strength=strength, headlines=heads, descriptions=descriptions, display_path=paths, final_urls=finals, final_mobile_urls=mobile, tracking=tracking, campaign=campaign_view, ad_group=group_view, keywords=unique(keywords), negative_keywords=unique(negatives), targeting=targeting, extensions=unique(linked), raw=normalize_micros(row), data_gaps=[])
        mapped = []
        for original in sorted(set(finals + mobile)):
            try:
                key = normalize_url(original, rules)
                if follow_redirects:
                    parameters = {x["key"]: x for source in scopes.values() for x in source.get("url_custom_parameters", [])}
                    start = expand_tracker(template, original, list(parameters.values())) if template else original
                    resolved, hops = (resolver or RedirectResolver(rules)).resolve(start)
                    key = normalize_url(resolved, rules)
                    config.url_resolutions.append({"original_url": original, "tracker_url": start if template else None, "resolved_url": resolved, "hops": hops})
                mapped.append((key, original))
            except (ValueError, OSError, HTTPException):
                # Failure must not discard a known configured landing page.
                gaps.append("landing_pages: URL invalid or redirect resolution failed")
                config.url_resolutions.append({"original_url": original, "error": "URL invalid or redirect resolution failed"})
                try: mapped.append((normalize_url(original, rules), original))
                except ValueError: pass
        if not mapped:
            reason = "No resolvable HTTP(S) final URL; destination may be dynamic, app-only, or feed-based"
            gaps.append("landing_pages: " + reason)
            config.data_gaps = sorted(set(gaps))
            unmapped.append(UnmappedAd(record_id=record_id, ad_id=config.ad_id, reason=reason, configuration=config))
        else:
            config.data_gaps = sorted(set(gaps))
            for key, original in mapped:
                buckets[key]["originals"].add(original)
                buckets[key]["ads"][record_id] = config
    pages = []
    for key, bucket in sorted(buckets.items()):
        originals = sorted(bucket["originals"])
        ads = [v for _, v in sorted(bucket["ads"].items())]
        pages.append(LandingPage(landing_page=originals[0], original_urls=originals, normalized_key=key, ad_count=len(ads), ads=ads))
    return Report(customer_id=snapshot.customer_id, generated_at=snapshot.captured_at, source=snapshot.source, landing_pages=pages, unmapped_ads=sorted(unmapped, key=lambda x: x.record_id), warnings=sorted(set(warnings)), collection_inventory=snapshot.collection_inventory)


def export_csv(report: Report) -> str:
    stream = io.StringIO(newline="")
    fields = ["normalized_key", "original_urls", "record_id", "ad_id", "ad_type", "status", "campaign_id", "ad_group_id", "reason", "configuration_json"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    rows = [(p.normalized_key, p.original_urls, ad, "") for p in report.landing_pages for ad in p.ads]
    rows += [("", [], item.configuration, item.reason) for item in report.unmapped_ads]
    for key, originals, ad, reason in rows:
        row = dict(zip(fields, [key, stable(originals), ad.record_id, ad.ad_id or "", ad.ad_type, ad.status or "", ad.campaign.get("id") or "", (ad.ad_group or {}).get("id") or "", reason, stable(ad.model_dump(mode="json"))]))
        # Neutralize spreadsheet formulas while preserving the full JSON configuration.
        writer.writerow({k: "'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in row.items()})
    return stream.getvalue()
