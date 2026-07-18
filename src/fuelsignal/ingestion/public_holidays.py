"""NSW Public Holidays Ingestion.

Fetches official NSW public holiday data for use as a feature
in pricing models (holidays affect demand patterns).

Primary source: NSW Industrial Relations
https://www.industrialrelations.nsw.gov.au/public-holidays/public-holidays-in-nsw

Alternative: data.gov.au Australian Holidays dataset
https://data.gov.au/data/dataset/australian-holidays
"""

from datetime import datetime, timezone
from typing import Any

from fuelsignal.ingestion.base import BaseIngester
from fuelsignal.logging import get_logger, log_pipeline_event
from fuelsignal.utils.hashing import compute_record_hash

logger = get_logger(__name__)

# Known NSW public holidays for 2024-2026 as fallback
# Source: NSW Industrial Relations official calendar
NSW_PUBLIC_HOLIDAYS = [
    # 2024
    {"date": "2024-01-01", "name": "New Year's Day", "is_national": True},
    {"date": "2024-01-26", "name": "Australia Day", "is_national": True},
    {"date": "2024-03-29", "name": "Good Friday", "is_national": True},
    {"date": "2024-03-30", "name": "Saturday before Easter Sunday", "is_national": False},
    {"date": "2024-03-31", "name": "Easter Sunday", "is_national": True},
    {"date": "2024-04-01", "name": "Easter Monday", "is_national": True},
    {"date": "2024-04-25", "name": "Anzac Day", "is_national": True},
    {"date": "2024-06-10", "name": "King's Birthday", "is_national": True},
    {"date": "2024-08-05", "name": "Bank Holiday", "is_national": False},
    {"date": "2024-10-07", "name": "Labour Day", "is_national": False},
    {"date": "2024-12-25", "name": "Christmas Day", "is_national": True},
    {"date": "2024-12-26", "name": "Boxing Day", "is_national": True},
    # 2025
    {"date": "2025-01-01", "name": "New Year's Day", "is_national": True},
    {"date": "2025-01-27", "name": "Australia Day (observed)", "is_national": True},
    {"date": "2025-04-18", "name": "Good Friday", "is_national": True},
    {"date": "2025-04-19", "name": "Saturday before Easter Sunday", "is_national": False},
    {"date": "2025-04-20", "name": "Easter Sunday", "is_national": True},
    {"date": "2025-04-21", "name": "Easter Monday", "is_national": True},
    {"date": "2025-04-25", "name": "Anzac Day", "is_national": True},
    {"date": "2025-06-09", "name": "King's Birthday", "is_national": True},
    {"date": "2025-08-04", "name": "Bank Holiday", "is_national": False},
    {"date": "2025-10-06", "name": "Labour Day", "is_national": False},
    {"date": "2025-12-25", "name": "Christmas Day", "is_national": True},
    {"date": "2025-12-26", "name": "Boxing Day", "is_national": True},
    # 2026
    {"date": "2026-01-01", "name": "New Year's Day", "is_national": True},
    {"date": "2026-01-26", "name": "Australia Day", "is_national": True},
    {"date": "2026-04-03", "name": "Good Friday", "is_national": True},
    {"date": "2026-04-04", "name": "Saturday before Easter Sunday", "is_national": False},
    {"date": "2026-04-05", "name": "Easter Sunday", "is_national": True},
    {"date": "2026-04-06", "name": "Easter Monday", "is_national": True},
    {"date": "2026-04-25", "name": "Anzac Day", "is_national": True},
    {"date": "2026-06-08", "name": "King's Birthday", "is_national": True},
    {"date": "2026-08-03", "name": "Bank Holiday", "is_national": False},
    {"date": "2026-10-05", "name": "Labour Day", "is_national": False},
    {"date": "2026-12-25", "name": "Christmas Day", "is_national": True},
    {"date": "2026-12-26", "name": "Boxing Day", "is_national": True},
]


class PublicHolidaysIngester(BaseIngester):
    """Ingester for NSW public holiday data.

    Attempts to fetch from data.gov.au API first,
    falls back to curated known holiday list.
    """

    def __init__(self):
        super().__init__("nsw_public_holidays")

    def fetch(self) -> dict:
        """Fetch public holiday data.

        Strategy:
        1. Try data.gov.au API for structured holiday data
        2. Fall back to curated list from official NSW Government calendar
        """
        log_pipeline_event(logger, "Starting public holidays fetch", "bronze")

        # Try data.gov.au API
        alt_api = self.source_config.get("alternative_api")
        if alt_api:
            try:
                response = self.fetch_url(alt_api)
                api_data = response.json()

                if api_data.get("success"):
                    resources = api_data.get("result", {}).get("resources", [])
                    for resource in resources:
                        if resource.get("format", "").upper() in ("CSV", "JSON"):
                            try:
                                res_response = self.fetch_url(resource["url"])
                                return {
                                    "holidays": (
                                        res_response.json()
                                        if resource["format"].upper() == "JSON"
                                        else res_response.text
                                    ),
                                    "source_method": "data_gov_au_api",
                                    "resource_url": resource["url"],
                                }
                            except Exception as e:
                                log_pipeline_event(
                                    logger,
                                    f"Failed to download holiday resource: {str(e)[:100]}",
                                    "bronze",
                                    status="warning",
                                )
                                continue
            except Exception as e:
                log_pipeline_event(
                    logger,
                    f"data.gov.au API unavailable: {str(e)[:100]}. Using curated list.",
                    "bronze",
                    status="warning",
                )

        # Fallback: use curated known holidays
        log_pipeline_event(
            logger,
            "Using curated NSW public holiday list (source: NSW Industrial Relations)",
            "bronze",
        )

        self._metadata["record_count"] = len(NSW_PUBLIC_HOLIDAYS)
        self._metadata["status"] = "success"
        self._metadata["source_url"] = self.source_config["landing_page"]

        return {
            "holidays": NSW_PUBLIC_HOLIDAYS,
            "source_method": "curated_official_list",
        }

    def to_raw_records(self, data: Any) -> list[dict]:
        """Convert holiday data to Bronze-ready records."""
        records = []
        ingested_at = datetime.now(timezone.utc).isoformat()

        holidays = data.get("holidays", [])

        if isinstance(holidays, list):
            for holiday in holidays:
                record_hash = compute_record_hash(
                    holiday.get("date", ""),
                    holiday.get("name", ""),
                )
                records.append(
                    {
                        "date": holiday.get("date"),
                        "holiday_name": holiday.get("name"),
                        "state": "NSW",
                        "is_national": holiday.get("is_national", False),
                        "_ingested_at": ingested_at,
                        "_source_name": "nsw_public_holidays",
                        "_source_url": self.source_config["landing_page"],
                        "_source_file": data.get("source_method", ""),
                        "_source_record_hash": record_hash,
                        "_pipeline_run_id": self.run_id,
                    }
                )

        self._metadata["record_count"] = len(records)
        return records
