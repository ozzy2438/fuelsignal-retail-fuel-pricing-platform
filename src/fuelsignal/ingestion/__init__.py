"""FuelSignal Data Ingestion Package.

Contains source-specific downloader modules that fetch data from
official public sources. All downloaders:
- Read source URLs from config/sources.yml
- Include timeout, retry, and error handling
- Preserve raw source artifacts
- Record retrieval metadata for audit
- Never store credentials in code
"""

from fuelsignal.ingestion.base import BaseIngester

__all__ = ["BaseIngester"]
