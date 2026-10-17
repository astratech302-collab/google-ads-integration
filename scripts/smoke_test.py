"""Run the complete mock integration flow against a locally hosted server."""
import argparse
import os

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, headers={"X-API-Key": os.environ.get("ADS_TEST_API_KEY", "local-dev-key")}, timeout=120) as client:
        assert client.get("/health").json()["mode"] == "mock", "Smoke script requires mock mode"
        response = client.post("/connections/mock"); response.raise_for_status()
        connection_id = response.json()["connection_id"]
        path = "/connections/" + connection_id
        try:
            response = client.get(path + "/accounts"); response.raise_for_status()
            response = client.put(path + "/accounts", json={"customer_ids": ["1234567890"]}); response.raise_for_status()
            response = client.post(path + "/reports", json={"customer_id": "1234567890"}); response.raise_for_status()
            payload = response.json()
            report_path = path + "/reports/" + payload["report_id"]
            for format_ in ["json", "csv", "table"]:
                export = client.get(report_path, params={"format": format_}); export.raise_for_status()
            print(f"Passed: {len(payload['report']['landing_pages'])} landing pages, {len(payload['report']['unmapped_ads'])} unmapped records; JSON, CSV and table exports.")
        finally:
            client.delete(path).raise_for_status()


if __name__ == "__main__": main()
