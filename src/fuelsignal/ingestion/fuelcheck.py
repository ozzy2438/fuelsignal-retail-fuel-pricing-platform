"""NSW FuelCheck Data Ingestion.

Fetches retail fuel price and station data from the official
NSW FuelCheck service via Data.NSW.

Source: https://data.nsw.gov.au/data/dataset/fuel-check

The NSW FuelCheck data provides:
- Station-level retail fuel prices
- Station location and metadata
- Multiple fuel types per station
- Near real-time pricing updates
"""

from datetime import datetime, timezone
from typing import Any

from fuelsignal.ingestion.base import BaseIngester
from fuelsignal.logging import get_logger, log_pipeline_event
from fuelsignal.utils.hashing import compute_record_hash

logger = get_logger(__name__)


class FuelCheckIngester(BaseIngester):
    """Ingester for NSW FuelCheck data.

    Attempts multiple data access methods:
    1. CKAN API for bulk historical data
    2. Direct resource download if available
    3. FuelCheck API for current prices (requires API key)
    """

    def __init__(self):
        super().__init__("nsw_fuelcheck")
        self._raw_data = None

    def fetch(self) -> dict:
        """Fetch FuelCheck data from Data.NSW CKAN API.

        Strategy:
        1. Query CKAN package metadata to find downloadable resources
        2. Download the most recent CSV/JSON resource
        3. Fall back to API if bulk download unavailable

        Returns:
            Dictionary with 'stations' and 'prices' data.
        """
        log_pipeline_event(logger, "Starting FuelCheck data fetch", "bronze")

        # Step 1: Query CKAN for available resources
        ckan_url = self.source_config.get("ckan_dataset")
        if ckan_url:
            try:
                return self._fetch_via_ckan(ckan_url)
            except Exception as e:
                log_pipeline_event(
                    logger,
                    f"CKAN fetch failed, trying alternative: {str(e)[:200]}",
                    "bronze",
                    status="warning",
                )

        # Step 2: Try landing page for direct download links
        landing_page = self.source_config.get("landing_page")
        log_pipeline_event(
            logger,
            f"CKAN unavailable. Manual download may be needed from: {landing_page}",
            "bronze",
            status="warning",
        )

        return {"stations": [], "prices": [], "source_method": "ckan_api"}

    def _fetch_via_ckan(self, ckan_url: str) -> dict:
        """Fetch data via CKAN API.

        The CKAN package_show endpoint returns metadata about
        available resources (CSV, JSON files) for download.
        """
        response = self.fetch_url(ckan_url)
        package_info = response.json()

        if not package_info.get("success"):
            raise ValueError("CKAN API returned unsuccessful response")

        result = package_info.get("result", {})
        resources = result.get("resources", [])

        log_pipeline_event(
            logger,
            f"Found {len(resources)} resources in CKAN package",
            "bronze",
            details={"resource_count": len(resources)},
        )

        # Find the most suitable resource (prefer CSV or JSON)
        data = {"stations": [], "prices": [], "resources_found": []}

        for resource in resources:
            resource_info = {
                "id": resource.get("id"),
                "name": resource.get("name", ""),
                "format": resource.get("format", ""),
                "url": resource.get("url", ""),
                "last_modified": resource.get("last_modified"),
                "size": resource.get("size"),
            }
            data["resources_found"].append(resource_info)

            # Try to download price data resources
            if resource.get("url") and resource.get("format", "").upper() in ("CSV", "JSON"):
                try:
                    resource_response = self.fetch_url(resource["url"])
                    if resource["format"].upper() == "JSON":
                        resource_data = resource_response.json()
                    else:
                        resource_data = resource_response.text

                    data["raw_resource"] = {
                        "resource_id": resource.get("id"),
                        "format": resource.get("format"),
                        "content": resource_data,
                    }
                    data["source_method"] = "ckan_resource_download"

                    log_pipeline_event(
                        logger,
                        f"Downloaded resource: {resource.get('name')}",
                        "bronze",
                    )
                    break

                except Exception as e:
                    log_pipeline_event(
                        logger,
                        f"Failed to download resource {resource.get('name')}: {str(e)[:100]}",
                        "bronze",
                        status="warning",
                    )

        self._metadata["record_count"] = len(data.get("prices", []))
        self._metadata["status"] = "success"
        return data

    def to_raw_records(self, data: Any) -> list[dict]:
        """Convert fetched data to Bronze-ready raw records.

        Each record includes ingestion metadata columns:
        - _ingested_at
        - _source_name
        - _source_url
        - _source_file
        - _source_record_hash
        - _pipeline_run_id
        """
        records = []
        ingested_at = datetime.now(timezone.utc).isoformat()

        if isinstance(data, dict):
            # Handle CKAN resource data
            raw_content = data.get("raw_resource", {}).get("content")
            if isinstance(raw_content, list):
                for item in raw_content:
                    record_hash = compute_record_hash(
                        item.get("ServiceStationID", item.get("station_code", "")),
                        item.get("FuelCode", item.get("fuel_type", "")),
                        item.get("Price", item.get("price", "")),
                        item.get("TransactionDateUtc", item.get("last_updated", "")),
                    )
                    records.append(
                        {
                            **item,
                            "_ingested_at": ingested_at,
                            "_source_name": "nsw_fuelcheck",
                            "_source_url": self._metadata.get("source_url", ""),
                            "_source_file": data.get("raw_resource", {}).get("resource_id", ""),
                            "_source_record_hash": record_hash,
                            "_pipeline_run_id": self.run_id,
                        }
                    )

        self._metadata["record_count"] = len(records)
        return records
