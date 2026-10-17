"""Exact schema inventory from official Google Ads protobuf descriptors; no API calls."""
import json
from pathlib import Path

from google.ads.googleads.v23.resources.types.ad import Ad
from google.ads.googleads.v23.enums.types.ad_type import AdTypeEnum


def main():
    messages, enums = {}, {}

    def register(descriptor):
        if descriptor.full_name in messages: return
        fields = messages[descriptor.full_name] = {}
        for field in descriptor.fields:
            entry = {"repeated": field.is_repeated, "presence": field.has_presence}
            if field.message_type:
                entry["message"] = field.message_type.full_name
                register(field.message_type)
            elif field.enum_type:
                entry["enum"] = field.enum_type.full_name
                enums[field.enum_type.full_name] = [v.name for v in field.enum_type.values]
            else:
                entry["protobuf_type"] = field.type
            fields[field.name] = entry

    descriptor = Ad.pb().DESCRIPTOR
    register(descriptor)
    creatives = {f.name: f.message_type for f in descriptor.fields if f.name.endswith("_ad") and f.message_type}
    types = {}
    overrides = {"HTML5_UPLOAD_AD": "display_upload_ad", "DYNAMIC_HTML5_AD": "display_upload_ad", **{name: "video_ad" for name in ["IN_FEED_VIDEO_AD", "VIDEO_BUMPER_AD", "VIDEO_NON_SKIPPABLE_IN_STREAM_AD", "VIDEO_TRUEVIEW_IN_STREAM_AD", "YOUTUBE_AUDIO_AD"]}}
    for value in AdTypeEnum.AdType:
        field = overrides.get(value.name, value.name.lower())
        message = creatives.get(field)
        fields = messages[message.full_name] if message else {}
        types[value.name] = {"creative_field": field if message else None, "message": message.full_name if message else None, "available_fields": sorted(fields), "unavailable_common_copy_fields": [k for k in ["headlines", "descriptions", "path1", "path2"] if k not in fields], "note": "Use semantic equivalents such as headline_part1 or nested video copy where present; see messages registry. No creative payload exists in this API schema." if message is None else "Schema fields; live selectability verified using GoogleAdsFieldService."}
    result = {"api_version": "v23", "source": "Official google-ads 34.0.0 Python package protobuf descriptors", "ad_types": types, "common_ad_message": descriptor.full_name, "messages": messages, "enums": enums}
    Path("docs/field-inventory.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__": main()
