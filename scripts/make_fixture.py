"""Synthetic GoogleAdsService.Search response pages. Contains no real customer data."""
import json
from pathlib import Path

from app.source import RESOURCES

C = "1234567890"
prefix = f"customers/{C}"
resources = {k: [] for k in RESOURCES}
resources["customer"] = [{"resource_name": prefix, "id": C, "descriptive_name": "Demo Store", "currency_code": "USD", "time_zone": "America/New_York", "tracking_url_template": "https://tracker.example.net/click?url={lpurl}"}]
for ident, name, channel, bid, amount in [("10", "Search Shirts", "SEARCH", "MAXIMIZE_CONVERSIONS", "50000000"), ("20", "Display Remarketing", "DISPLAY", "MANUAL_CPC", "25000000"), ("30", "Performance Max Store", "PERFORMANCE_MAX", "MAXIMIZE_CONVERSION_VALUE", "100000000"), ("40", "Shopping Catalog", "SHOPPING", "MANUAL_CPC", "35000000")]:
    resources["campaign"].append({"resource_name": f"{prefix}/campaigns/{ident}", "id": ident, "name": name, "status": "ENABLED", "advertising_channel_type": channel, "bidding_strategy_type": bid, "campaign_budget": f"{prefix}/campaignBudgets/{ident}"})
    resources["campaign_budget"].append({"resource_name": f"{prefix}/campaignBudgets/{ident}", "id": ident, "amount_micros": amount, "period": "DAILY", "explicitly_shared": False})
for ident, campaign_id in [("100", "10"), ("200", "20"), ("400", "40")]:
    resources["ad_group"].append({"resource_name": f"{prefix}/adGroups/{ident}", "id": ident, "name": f"Group {ident}", "status": "ENABLED", "campaign": f"{prefix}/campaigns/{campaign_id}"})

def ad(ident, group, type_, urls, payload=None, status="ENABLED", **extra):
    creative = {"resource_name": f"{prefix}/ads/{ident}", "id": ident, "type": type_, "final_urls": urls, **extra}
    if payload: creative.update(payload)
    return {"resource_name": f"{prefix}/adGroupAds/{group}~{ident}", "ad_group": f"{prefix}/adGroups/{group}", "ad": creative, "status": status, "ad_strength": "GOOD" if type_ == "RESPONSIVE_SEARCH_AD" else "UNSPECIFIED", "policy_summary": {"approval_status": "APPROVED"}}

resources["ad_group_ad"] = [
    ad("1", "100", "RESPONSIVE_SEARCH_AD", ["https://Example.com/shirts/?utm_source=google&color=blue", "https://example.com/sale"], {"responsive_search_ad": {"headlines": [{"text": "Soft Cotton Shirts", "pinned_field": "HEADLINE_1"}, {"text": "Shop New Colors"}], "descriptions": [{"text": "Everyday comfort. Free shipping on qualifying orders."}], "path1": "shirts", "path2": "cotton"}}, final_url_suffix="utm_campaign=shirts", url_custom_parameters=[{"key": "campaign", "value": "shirts"}]),
    ad("2", "100", "RESPONSIVE_SEARCH_AD", ["https://example.com/shirts?color=blue&gclid=synthetic", "https://example.com/shirts/?color=blue&utm_medium=cpc"], {"responsive_search_ad": {"headlines": [{"text": "Your New Favorite Shirt"}], "descriptions": [{"text": "Browse our latest collection."}], "path1": "shirts"}}, status="PAUSED"),
    ad("3", "200", "RESPONSIVE_DISPLAY_AD", ["https://example.com/sale?fbclid=synthetic"], {"responsive_display_ad": {"headlines": [{"text": "Seasonal Sale"}], "long_headline": {"text": "Save on shirts this season"}, "descriptions": [{"text": "Limited-time offers"}], "marketing_images": [{"asset": f"{prefix}/assets/504"}], "business_name": "Demo Store"}}, final_mobile_urls=["https://m.example.com/sale/?utm_source=google"]),
    ad("4", "400", "SHOPPING_PRODUCT_AD", [], {"shopping_product_ad": {}}),
    ad("5", "100", "EXPANDED_DYNAMIC_SEARCH_AD", [], {"expanded_dynamic_search_ad": {"description": "Explore the collection", "description2": "Find your fit"}}),
    ad("6", "100", "CALL_AD", []),
    ad("7", "100", "RESPONSIVE_SEARCH_AD", ["https://example.com/removed"], status="REMOVED"),
    ad("8", "100", "TEXT_AD", ["not a URL", "https://example.com/sale"], {"text_ad": {"headline": "Browse Sale", "description1": "Save today", "description2": "Shop online"}}),
]
resources["asset_group"] = [{"resource_name": f"{prefix}/assetGroups/300", "id": "300", "name": "Shirts Asset Group", "campaign": f"{prefix}/campaigns/30", "status": "ENABLED", "ad_strength": "EXCELLENT", "path1": "shirts", "final_urls": ["https://example.com/shirts/?color=blue&utm_campaign=pmax"]}]
resources["ad_group_criterion"] = [
    {"resource_name": f"{prefix}/adGroupCriteria/100~1", "ad_group": f"{prefix}/adGroups/100", "criterion_id": "1", "type": "KEYWORD", "negative": False, "status": "ENABLED", "keyword": {"text": "cotton shirts", "match_type": "PHRASE"}},
    {"resource_name": f"{prefix}/adGroupCriteria/100~2", "ad_group": f"{prefix}/adGroups/100", "criterion_id": "2", "type": "KEYWORD", "negative": True, "status": "ENABLED", "keyword": {"text": "free", "match_type": "BROAD"}},
    {"resource_name": f"{prefix}/adGroupCriteria/100~3", "ad_group": f"{prefix}/adGroups/100", "criterion_id": "3", "type": "KEYWORD", "negative": False, "status": "REMOVED", "keyword": {"text": "old keyword", "match_type": "EXACT"}},
]
resources["campaign_criterion"] = [
    {"resource_name": f"{prefix}/campaignCriteria/10~1", "campaign": f"{prefix}/campaigns/10", "type": "LOCATION", "negative": False, "location": {"geo_target_constant": "geoTargetConstants/2840"}},
    {"resource_name": f"{prefix}/campaignCriteria/10~2", "campaign": f"{prefix}/campaigns/10", "type": "DEVICE", "device": {"type": "MOBILE"}, "bid_modifier": 1.1},
    {"resource_name": f"{prefix}/campaignCriteria/10~3", "campaign": f"{prefix}/campaigns/10", "type": "AD_SCHEDULE", "ad_schedule": {"day_of_week": "MONDAY", "start_hour": 9, "start_minute": "ZERO", "end_hour": 18, "end_minute": "ZERO"}},
    {"resource_name": f"{prefix}/campaignCriteria/10~4", "campaign": f"{prefix}/campaigns/10", "type": "KEYWORD", "negative": True, "keyword": {"text": "jobs", "match_type": "BROAD"}},
]
resources["campaign_shared_set"] = [{"resource_name": f"{prefix}/campaignSharedSets/10~600", "campaign": f"{prefix}/campaigns/10", "shared_set": f"{prefix}/sharedSets/600", "status": "ENABLED"}]
resources["shared_criterion"] = [{"resource_name": f"{prefix}/sharedCriteria/600~601", "shared_set": f"{prefix}/sharedSets/600", "keyword": {"text": "used", "match_type": "BROAD"}}]
resources["shared_criterion"].append({"resource_name": f"{prefix}/sharedCriteria/701~702", "shared_set": f"{prefix}/sharedSets/701", "keyword": {"text": "counterfeit", "match_type": "BROAD"}})
resources["customer_negative_criterion"] = [{"resource_name": f"{prefix}/customerNegativeCriteria/700", "type": "NEGATIVE_KEYWORD_LIST", "negative_keyword_list": {"shared_set": f"{prefix}/sharedSets/701"}}]
asset_specs = [("501", "SITELINK", {"sitelink_asset": {"link_text": "New Arrivals", "description1": "Fresh styles", "description2": "Explore now"}, "final_urls": ["https://example.com/new"]}), ("502", "CALLOUT", {"callout_asset": {"callout_text": "Free Returns"}}), ("503", "STRUCTURED_SNIPPET", {"structured_snippet_asset": {"header": "Styles", "values": ["Casual", "Formal"]}}), ("504", "IMAGE", {"image_asset": {"full_size": {"url": "https://cdn.example.com/shirt.jpg", "width_pixels": "1200", "height_pixels": "628"}}}), ("505", "TEXT", {"text_asset": {"text": "Find Your Everyday Shirt"}}), ("506", "TEXT", {"text_asset": {"text": "Quality fabrics, fresh colors."}})]
resources["asset"] = [{"resource_name": f"{prefix}/assets/{i}", "id": i, "type": t, **payload} for i,t,payload in asset_specs]
resources["customer_asset"] = [{"resource_name": f"{prefix}/customerAssets/502~CALLOUT", "asset": f"{prefix}/assets/502", "field_type": "CALLOUT", "status": "ENABLED"}]
resources["campaign_asset"] = [{"resource_name": f"{prefix}/campaignAssets/10~501~SITELINK", "campaign": f"{prefix}/campaigns/10", "asset": f"{prefix}/assets/501", "field_type": "SITELINK", "status": "ENABLED"}]
resources["ad_group_asset"] = [{"resource_name": f"{prefix}/adGroupAssets/100~503~STRUCTURED_SNIPPET", "ad_group": f"{prefix}/adGroups/100", "asset": f"{prefix}/assets/503", "field_type": "STRUCTURED_SNIPPET", "status": "ENABLED"}]
resources["asset_group_asset"] = [{"resource_name": f"{prefix}/assetGroupAssets/300~{i}~{field}", "asset_group": f"{prefix}/assetGroups/300", "asset": f"{prefix}/assets/{i}", "field_type": field, "status": "ENABLED"} for i, field in [("505", "HEADLINE"), ("506", "DESCRIPTION"), ("504", "MARKETING_IMAGE")]]
responses = {}
for name, rows in resources.items():
    # Two rows per page exercises fixture pagination and preserves Google response shape.
    chunks = [rows[i:i+2] for i in range(0, len(rows), 2)] or [[]]
    responses[name] = [{"results": [{name: row} for row in chunk], **({"next_page_token": f"fixture-page-{index+1}"} if index < len(chunks)-1 else {})} for index, chunk in enumerate(chunks)]
Path("fixtures/google_ads.json").write_text(json.dumps({"captured_at": "2026-10-17T12:00:00Z", "responses": responses}, indent=2) + "\n")
