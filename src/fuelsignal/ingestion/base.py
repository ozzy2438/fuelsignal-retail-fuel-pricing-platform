"""Base ingestion class with common download utilities."""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from fuelsignal.config import load_sources_config, load_project_config
from fuelsignal.logging import get_logger, log_pipeline_event
from fuelsignal.utils.hashing import generate_pipeline_run_id

logger = get_logger(__name__)


class BaseIngester(ABC):
    """Abstract base class for all data source ingesters.
    
    Provides common functionality:
    - Configuration loading
    - HTTP requests with retry logic
    - Metadata recording
    - Audit trail generation
    """
    
    def __init__(self, source_key: str):
        """Initialize ingester with source configuration.
        
        Args:
            source_key: Key in sources.yml (e.g., 'nsw_fuelcheck')
        """
        self.source_key = source_key
        self.sources_config = load_sources_config()
        self.project_config = load_project_config()
        
        self.source_config = self.sources_config["sources"].get(source_key)
        if not self.source_config:
            raise ValueError(
                f"Source '{source_key}' not found in sources.yml. "
                f"Available: {list(self.sources_config['sources'].keys())}"
            )
        
        pipeline_cfg = self.project_config.get("pipeline", {})
        self.timeout = pipeline_cfg.get("request_timeout_seconds", 60)
        self.max_retries = pipeline_cfg.get("max_retry_attempts", 3)
        self.retry_delay = pipeline_cfg.get("retry_delay_seconds", 30)
        self.run_id = generate_pipeline_run_id("ingest")
        
        self._metadata = {
            "source_name": self.source_config.get("name", source_key),
            "pipeline_run_id": self.run_id,
            "ingested_at": None,
            "source_url": None,
            "retrieval_duration_seconds": None,
            "record_count": None,
            "status": "pending",
        }
    
    def fetch_url(
        self,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> requests.Response:
        """Fetch a URL with retry logic and timeout.
        
        Args:
            url: URL to fetch.
            headers: Optional HTTP headers.
            params: Optional query parameters.
            
        Returns:
            Response object.
            
        Raises:
            requests.exceptions.RequestException: After all retries exhausted.
        """
        last_exception = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                log_pipeline_event(
                    logger, 
                    f"Fetching URL (attempt {attempt}/{self.max_retries})",
                    "bronze",
                    details={"url": url, "attempt": attempt}
                )
                
                start_time = time.time()
                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                
                duration = time.time() - start_time
                self._metadata["retrieval_duration_seconds"] = round(duration, 2)
                self._metadata["source_url"] = url
                self._metadata["ingested_at"] = datetime.now(timezone.utc).isoformat()
                
                log_pipeline_event(
                    logger,
                    f"Successfully fetched URL in {duration:.2f}s",
                    "bronze",
                    details={"url": url, "status_code": response.status_code}
                )
                
                return response
                
            except requests.exceptions.RequestException as e:
                last_exception = e
                log_pipeline_event(
                    logger,
                    f"Request failed (attempt {attempt}/{self.max_retries}): {str(e)[:200]}",
                    "bronze",
                    status="warning",
                )
                
                if attempt < self.max_retries:
                    delay = self.retry_delay * attempt  # Exponential backoff
                    log_pipeline_event(
                        logger,
                        f"Retrying in {delay}s...",
                        "bronze",
                    )
                    time.sleep(delay)
        
        self._metadata["status"] = "failed"
        raise last_exception
    
    @property
    def metadata(self) -> dict[str, Any]:
        """Return ingestion metadata for audit records."""
        return self._metadata.copy()
    
    @abstractmethod
    def fetch(self) -> Any:
        """Fetch data from the source. Must be implemented by subclasses."""
        ...
    
    @abstractmethod
    def to_raw_records(self, data: Any) -> list[dict]:
        """Convert fetched data to list of raw record dictionaries."""
        ...
