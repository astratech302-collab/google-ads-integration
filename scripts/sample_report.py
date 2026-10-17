"""Generate deterministic example artifacts without credentials or network access."""
import json
from pathlib import Path

from app.config import Settings
from app.mapping import build_report, export_csv
from app.models import ReportCreated
from app.source import MockSource
from app.urls import URLRules


def main():
    settings = Settings(_env_file=None)
    source = MockSource(settings)
    account = next(a for a in source.accounts("mock") if a.customer_id == "1234567890")
    report = build_report(source.snapshot("mock", account), URLRules.load(settings.normalization_file))
    Path("examples").mkdir(exist_ok=True)
    Path("examples/sample-report.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    Path("examples/sample-report.csv").write_text(export_csv(report), newline="")
    Path("docs/report-schema.json").write_text(json.dumps(ReportCreated.model_json_schema(), indent=2, sort_keys=True) + "\n")
    print(f"Generated {len(report.landing_pages)} landing-page groups and {len(report.unmapped_ads)} unmapped records.")


if __name__ == "__main__": main()
