# Google Ads configuration inventory

Reviewed before extraction implementation, 2026-10-17. Baseline: Google Ads API v23.
The live collector also reads GoogleAdsFieldService metadata before generating queries;
that metadata is authoritative for selectable fields in the configured API version.
No metric/segment fields are requested. Raw selected resource configuration is retained.

## Common fields and joins

| Target | API field / resource | Availability and interpretation |
|---|---|---|
| Identity, type | `ad_group_ad.ad.id`, `.type`, `.resource_name` | Standard ads; asset groups have their own ID and resource name |
| Status, policy, strength | `ad_group_ad.status`, `.policy_summary.approval_status`, `.ad_strength` | Strength may be UNSPECIFIED/UNKNOWN or absent for ineligible formats; record a gap, not a score |
| Destinations | `ad_group_ad.ad.final_urls`, `.final_mobile_urls`, `.final_app_urls` | May be empty; dynamic/feed formats do not promise static destinations |
| Tracking | `ad_group_ad.ad.final_url_suffix`, `.tracking_url_template`, `.url_custom_parameters` | Templates/suffixes also exist at customer/campaign/ad-group/keyword levels; custom parameters are not on Customer.; preserve each scope, do not claim an ad-level blank means no tracking |
| Campaign | `campaign.id`, `.name`, `.status`, `.advertising_channel_type`, `.bidding_strategy_type`, `.bidding_strategy`, strategy-specific messages, `.campaign_budget` | Strategy type is not the strategy's full settings; retain raw campaign and portfolio bidding resource |
| Budget | `campaign_budget.amount_micros`, `.total_amount_micros`, `.period`, `.explicitly_shared`, `customer.currency_code` | Convert amounts by 1,000,000; do not sum shared budgets across ads |
| Ad group | `ad_group.id`, `.name`, `.status`, bidding and URL settings | Not applicable to asset groups |
| Keywords, negatives | `ad_group_criterion.keyword.text`, `.match_type`, `.negative`; `campaign_criterion.keyword`; `shared_criterion` joined through `campaign_shared_set`; `customer_negative_criterion.negative_keyword_list.shared_set` joined to `shared_criterion` | Keywords are scoped targeting, not an individual ad's served search terms; PMAX signals are not positive keywords |
| Locations / exclusions | `campaign_criterion.location`, `.negative`, `.proximity`, campaign geo settings | Raw criterion contains geo constant resource IDs; geo names require an extra lookup |
| Devices | `campaign_criterion.device`, `.bid_modifier`; `ad_group_bid_modifier` where supported | Absence is no explicit criterion, not exclusion of all devices |
| Schedule | `campaign_criterion.ad_schedule`, `.bid_modifier`, `customer.time_zone` | No explicit schedule is different from missing collection; inherited account timezone retained |
| Linked assets | `customer_asset`, `campaign_asset`, `ad_group_asset`, `asset_group_asset` → `asset` | Sitelinks: `sitelink_asset`; callouts: `callout_asset`; snippets: `structured_snippet_asset`; images: `image_asset`. Retain association scope/status; association does not guarantee serving |

Sources: [Ad](https://developers.google.com/google-ads/api/reference/rpc/v23/Ad),
[AdGroupAd](https://developers.google.com/google-ads/api/reference/rpc/v23/AdGroupAd),
[Campaign](https://developers.google.com/google-ads/api/fields/v23/campaign),
[CampaignBudget](https://developers.google.com/google-ads/api/reference/rpc/v23/CampaignBudget),
[AdGroupCriterion](https://developers.google.com/google-ads/api/fields/v23/ad_group_criterion),
[CampaignCriterion](https://developers.google.com/google-ads/api/reference/rpc/v23/CampaignCriterion),
[Asset](https://developers.google.com/google-ads/api/fields/v23/asset).

## Ad types

In the table, paths are relative to `ad_group_ad.ad`. Common fields above apply
unless absent in the returned row. A missing value is reported separately from a
field that the schema cannot provide. All creative submessage configuration selected
by metadata remains in `raw`; the common copy arrays are a convenience view.

| Ad type | Creative configuration / copy available | Structural gaps |
|---|---|---|
| RESPONSIVE_SEARCH_AD | `responsive_search_ad.headlines`, `.descriptions`, `.path1`, `.path2`; pinned fields in text assets | No single served combination in configuration |
| EXPANDED_TEXT_AD | `expanded_text_ad.headline_part1/2/3`, `.description`, `.description2`, `.path1/2` | Deprecated format; strength may be absent |
| TEXT_AD | `text_ad.headline`, `.description1/2` | No path1/path2 fields |
| EXPANDED_DYNAMIC_SEARCH_AD | `expanded_dynamic_search_ad.description/description2` | Headlines, display URL, destination generated at serving; not static configuration |
| RESPONSIVE_DISPLAY_AD | `responsive_display_ad.headlines`, `.long_headline`, `.descriptions`, image/logo/video references, business name | No display paths |
| LEGACY_RESPONSIVE_DISPLAY_AD | `legacy_responsive_display_ad.short_headline`, `.long_headline`, `.description`, legacy media references | No display paths; legacy media may use MediaFile rather than Asset |
| IMAGE_AD | `image_ad` image metadata / media fields | No text headlines/descriptions/paths |
| HTML5_UPLOAD_AD / DYNAMIC_HTML5_AD | `display_upload_ad` product type and media bundle | Uploaded creative has no structured headline/description/path fields |
| CALL_AD | No `call_ad` payload in v23 `Ad` despite the enum value | Copy/phone fields unavailable in this version; no static paths; may have no web URL |
| APP_AD | `app_ad.headlines`, `.descriptions`, media references | App/store destinations may not be web landing URLs; no display paths |
| APP_ENGAGEMENT_AD | `app_engagement_ad.headlines`, `.descriptions`, media references | App deep links; no display paths |
| APP_PRE_REGISTRATION_AD | `app_pre_registration_ad.headlines`, `.descriptions`, media references | Store destinations; no display paths |
| LEGACY_APP_INSTALL_AD | `legacy_app_install_ad` copy and media metadata | Legacy format; no display paths |
| DEMAND_GEN_MULTI_ASSET_AD | `demand_gen_multi_asset_ad.headlines`, `.descriptions`, image/logo references | No display paths |
| DEMAND_GEN_CAROUSEL_AD | `demand_gen_carousel_ad.headline`, `.description`, carousel-card asset references | Card destinations/copy require linked assets; no display paths |
| DEMAND_GEN_PRODUCT_AD | `demand_gen_product_ad.headline`, `.description`, logo/business fields | Feed destinations need feed access; no display paths |
| DEMAND_GEN_VIDEO_RESPONSIVE_AD | `demand_gen_video_responsive_ad.headlines`, `.long_headlines`, `.descriptions`, video/logo references | No display paths |
| VIDEO_RESPONSIVE_AD | `video_responsive_ad.headlines`, `.long_headlines`, `.descriptions`, video/image references | No display paths |
| VIDEO_AD / IN_FEED_VIDEO_AD / VIDEO_BUMPER_AD / VIDEO_NON_SKIPPABLE_IN_STREAM_AD / VIDEO_TRUEVIEW_IN_STREAM_AD / YOUTUBE_AUDIO_AD | `video_ad` format-specific nested messages, video reference | Copy varies by nested format; missing common copy reported, no universal path fields |
| SHOPPING_PRODUCT_AD / SHOPPING_SMART_AD | `shopping_product_ad` / `shopping_smart_ad` marker messages | Product copy/images/destinations from feed; no static headline/description/paths |
| SHOPPING_COMPARISON_LISTING_AD | `shopping_comparison_listing_ad.headline`, comparison destination fields | No descriptions/paths; product destinations from feed |
| HOTEL_AD / TRAVEL_AD | `hotel_ad` / `travel_ad` marker messages | Hotel/travel feed and dynamic destinations/copy; no static text or paths |
| SMART_CAMPAIGN_AD | `smart_campaign_ad.headlines`, `.descriptions` | No display paths; keyword themes are not standard keyword criteria |
| LOCAL_AD | `local_ad.headlines`, `.descriptions`, media references, `.path1/2` if exposed by metadata | Dynamic/local destinations may be unavailable |
| UNKNOWN / UNSPECIFIED / future type | Preserve any selectable fields | Explicit unsupported-type gap; no guessed fields |
| PERFORMANCE_MAX_ASSET_GROUP (service record type) | `asset_group.final_urls`, `.final_mobile_urls`, `.path1/2`, `.ad_strength`, `.status`; text/image/video through `asset_group_asset` | No ad ID/ad group/ad-level policy approval or positive keyword list; URL expansion and feed destinations can exceed configured URLs |

Sources: [AdType enum](https://developers.google.com/google-ads/api/reference/rpc/v23/AdTypeEnum.AdType),
[creative union](https://developers.google.com/google-ads/api/reference/rpc/v23/Ad),
[RSA](https://developers.google.com/google-ads/api/reference/rpc/v23/ResponsiveSearchAdInfo),
[RDA](https://developers.google.com/google-ads/api/reference/rpc/v23/ResponsiveDisplayAdInfo),
[AssetGroup](https://developers.google.com/google-ads/api/reference/rpc/v23/AssetGroup),
[asset-group links](https://developers.google.com/google-ads/api/fields/v23/asset_group_asset).

The checked-in `field-inventory.json` expands every ad creative to its exact protobuf
field tree from the official client, including zero-field marker messages. It is
schema availability, not a promise that every field is GAQL selectable or populated.
Live `collection_inventory` records actual selected fields. Query failures abort the
report rather than returning a falsely complete result. Empty/not-set target fields
are always represented in `data_gaps`. Raw creative retains nested/format-specific
fields that cannot be flattened faithfully.
