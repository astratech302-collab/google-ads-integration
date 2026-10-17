from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Account(StrictModel):
    customer_id: str = Field(pattern=r"^\d{10}$")
    name: str
    manager: bool = False
    login_customer_id: str | None = None
    status: str | None = None


class Selection(StrictModel):
    customer_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("customer_ids")
    @classmethod
    def ids(cls, values):
        if any(len(v) != 10 or not v.isascii() or not v.isdigit() for v in values):
            raise ValueError("Customer IDs must contain exactly ten digits without hyphens")
        return sorted(set(values))


class ReportRequest(StrictModel):
    customer_id: str = Field(pattern=r"^\d{10}$")
    follow_redirects: bool = False


class AdConfiguration(StrictModel):
    record_id: str
    entity_kind: Literal["ad", "asset_group"]
    ad_id: str | None
    asset_group_id: str | None = None
    ad_type: str
    status: str | None
    policy_approval: str | None
    ad_strength: str | None
    headlines: list[str]
    descriptions: list[str]
    display_path: list[str]
    final_urls: list[str]
    final_mobile_urls: list[str]
    tracking: dict[str, Any]
    campaign: dict[str, Any]
    ad_group: dict[str, Any] | None
    keywords: list[dict[str, Any]]
    negative_keywords: list[dict[str, Any]]
    targeting: dict[str, Any]
    extensions: list[dict[str, Any]]
    url_resolutions: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any]
    data_gaps: list[str]


class LandingPage(StrictModel):
    landing_page: str
    original_urls: list[str]
    normalized_key: str
    ad_count: int
    ads: list[AdConfiguration]


class UnmappedAd(StrictModel):
    record_id: str
    ad_id: str | None
    reason: str
    configuration: AdConfiguration


class Report(StrictModel):
    schema_version: str = "1.0"
    customer_id: str
    generated_at: datetime
    source: Literal["mock", "google_ads"]
    landing_pages: list[LandingPage]
    unmapped_ads: list[UnmappedAd]
    warnings: list[str]
    collection_inventory: dict[str, list[str]]


class ReportCreated(StrictModel):
    report_id: str
    report: Report


class Snapshot(StrictModel):
    customer_id: str = Field(pattern=r"^\d{10}$")
    captured_at: datetime
    source: Literal["mock", "google_ads"]
    resources: dict[str, list[dict[str, Any]]]
    collection_inventory: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
