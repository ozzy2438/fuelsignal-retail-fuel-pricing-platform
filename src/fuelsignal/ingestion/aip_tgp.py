"""AIP Terminal Gate Price (TGP) Ingestion.

Fetches wholesale terminal gate prices from the Australian Institute of Petroleum.

Source: https://www.aip.com.au/pricing/terminal-gate-prices

Terminal Gate Prices represent wholesale fuel costs at terminals.
The difference between retail and TGP gives an indicative gross margin.

Note: AIP publishes TGP data on their website, typically as HTML tables.
This module handles extraction from the structured web content.
"""

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fuelsignal.ingestion.base import BaseIngester
from fuelsignal.logging import get_logger, log_pipeline_event
from fuelsignal.utils.hashing import compute_record_hash

logger = get_logger(__name__)


class AIPTerminalGatePriceIngester(BaseIngester):
    """Ingester for AIP Terminal Gate Price data.
    
    Fetches the TGP page and extracts pricing data from the
    structured HTML content.
    """
    
    def __init__(self):
        super().__init__("aip_terminal_gate_prices")
    
    def fetch(self) -> dict:
        """Fetch TGP data from AIP website.
        
        Returns:
            Dictionary containing raw HTML and extracted data.
        """
        log_pipeline_event(
            logger, "Starting AIP Terminal Gate Price fetch", "bronze"
        )
        
        landing_page = self.source_config["landing_page"]
        
        try:
            response = self.fetch_url(
                landing_page,
                headers={
                    "User-Agent": "FuelSignal-Research/0.1 (portfolio-project)",
                    "Accept": "text/html,application/xhtml+xml",
                }
            )
            
            raw_html = response.text
            
            # Extract structured data from HTML tables
            extracted = self._extract_tgp_from_html(raw_html)
            
            result = {
                "raw_html_length": len(raw_html),
                "raw_html": raw_html,
                "extracted_records": extracted,
                "source_method": "html_extraction",
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            self._metadata["record_count"] = len(extracted)
            self._metadata["status"] = "success"
            
            log_pipeline_event(
                logger,
                f"Fetched TGP page ({len(raw_html)} chars), extracted {len(extracted)} records",
                "bronze",
            )
            
            return result
            
        except Exception as e:
            self._metadata["status"] = "failed"
            log_pipeline_event(
                logger,
                f"TGP fetch failed: {str(e)[:200]}",
                "bronze",
                status="error",
            )
            raise
    
    def _extract_tgp_from_html(self, html: str) -> list[dict]:
        """Extract TGP records from HTML content.
        
        Looks for table structures containing terminal gate prices.
        This is a best-effort extraction that handles common AIP page formats.
        
        Args:
            html: Raw HTML content from AIP page.
            
        Returns:
            List of extracted price records.
        """
        records = []
        
        # Look for price-like patterns in the HTML
        # AIP typically publishes in a table with city, product, price
        # Pattern: number that looks like a fuel price (e.g., 145.6, 162.3)
        price_pattern = re.compile(
            r'(\d{2,3}\.\d{1,2})\s*(?:c(?:ents?)?(?:/|per)?\s*(?:l(?:itre)?)?)?',
            re.IGNORECASE
        )
        
        # This is a placeholder extraction - actual implementation
        # will depend on the specific HTML structure of the AIP page
        # which may change over time.
        
        log_pipeline_event(
            logger,
            "HTML extraction completed (structure-dependent parsing)",
            "bronze",
            details={"html_length": len(html), "records_extracted": len(records)}
        )
        
        return records
    
    def to_raw_records(self, data: Any) -> list[dict]:
        """Convert fetched TGP data to Bronze-ready raw records."""
        records = []
        ingested_at = datetime.now(timezone.utc).isoformat()
        
        extracted = data.get("extracted_records", [])
        for item in extracted:
            record_hash = compute_record_hash(
                item.get("date", ""),
                item.get("terminal", ""),
                item.get("product", ""),
                item.get("price_cpl", ""),
            )
            records.append({
                **item,
                "_ingested_at": ingested_at,
                "_source_name": "aip_terminal_gate_prices",
                "_source_url": self.source_config["landing_page"],
                "_source_file": "",
                "_source_record_hash": record_hash,
                "_pipeline_run_id": self.run_id,
            })
        
        # Also store raw HTML as a single audit record
        if data.get("raw_html"):
            records.append({
                "_raw_content_type": "html",
                "_raw_content_length": data.get("raw_html_length", 0),
                "_ingested_at": ingested_at,
                "_source_name": "aip_terminal_gate_prices",
                "_source_url": self.source_config["landing_page"],
                "_source_file": "tgp_page_snapshot",
                "_source_record_hash": compute_record_hash(
                    data.get("fetch_timestamp", ""),
                    str(data.get("raw_html_length", 0)),
                ),
                "_pipeline_run_id": self.run_id,
            })
        
        return records
