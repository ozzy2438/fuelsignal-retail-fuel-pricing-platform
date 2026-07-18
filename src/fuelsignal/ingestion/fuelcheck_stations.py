"""NSW FuelCheck live station-reference ingestion via the official OAuth2 API.

The bulk CKAN historical price archive (see ``fuelcheck.py``) has never carried
station coordinates. Coordinates are only available from the live FuelCheck API,
which requires a registered application at https://api.nsw.gov.au (Fuel API
product) and OAuth2 client-credentials authentication.

Confirmed live flow (2026-07-18, tested against production):

1. ``GET {TOKEN_URL}?grant_type=client_credentials`` with an HTTP Basic
   ``Authorization`` header built from ``FUELCHECK_API_KEY``:``FUELCHECK_API_SECRET``.
   Must be a GET request - the documented POST form fails silently (the gateway
   echoes the request body back instead of issuing a token).
2. ``GET {REFDATA_URL}`` with ``Authorization: Bearer <token>``, ``apikey``,
   ``transactionid`` (a UUID), and ``requesttimestamp`` (``dd/MM/yyyy HH:mm:ss``)
   headers. The API rejects requests missing ``transactionid``/``requesttimestamp``
   with ``HeadersError``.

The response's ``stations.items`` array carries an official station ``code``,
``name``, ``brand``, free-text ``address``, and a ``location`` object with
``latitude``/``longitude`` - this is the only source of truth for coordinates
used elsewhere in the pipeline (station matching, competitor pairs).
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from fuelsignal.logging import get_logger, log_pipeline_event
from fuelsignal.silver.station_matching import parse_nsw_address
from fuelsignal.utils.hashing import compute_record_hash, generate_pipeline_run_id

logger = get_logger(__name__)

TOKEN_URL = "https://api.onegov.nsw.gov.au/oauth/client_credential/accesstoken"  # noqa: S105
REFDATA_URL = "https://api.onegov.nsw.gov.au/FuelCheckRefData/v2/fuel/lovs"
SOURCE_NAME = "nsw_fuelcheck_api_reference"


class FuelCheckAuthError(RuntimeError):
    """Raised when the FuelCheck OAuth2 flow fails or credentials are missing."""


def _authorization_header() -> str:
    """Build the Basic auth header, preferring a pre-built value if provided."""
    header = os.environ.get("FUELCHECK_AUTHORIZATION_HEADER", "").strip()
    if header:
        return header

    key = os.environ.get("FUELCHECK_API_KEY", "").strip()
    secret = os.environ.get("FUELCHECK_API_SECRET", "").strip()
    if not key or not secret:
        raise FuelCheckAuthError(
            "FUELCHECK_API_KEY/FUELCHECK_API_SECRET are not set. Register an application at "
            "https://api.nsw.gov.au (Fuel API product) to obtain credentials, then set them "
            "in .env."
        )
    return "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()


class FuelCheckStationReferenceIngester:
    """Fetch official NSW FuelCheck station reference data (code, brand, coordinates)."""

    source_name = SOURCE_NAME

    def __init__(self) -> None:
        self.run_id = generate_pipeline_run_id("ingest")
        self._metadata: dict[str, Any] = {
            "source_name": self.source_name,
            "pipeline_run_id": self.run_id,
            "token_url": TOKEN_URL,
            "data_url": REFDATA_URL,
            "status": "pending",
        }

    def _fetch_token(self) -> str:
        response = requests.get(
            TOKEN_URL,
            params={"grant_type": "client_credentials"},
            headers={"Accept": "application/json", "Authorization": _authorization_header()},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise FuelCheckAuthError(
                "FuelCheck OAuth2 token response did not include an access_token"
            )
        self._metadata["token_expires_in_seconds"] = payload.get("expires_in")
        return token

    def fetch(self) -> dict[str, Any]:
        """Fetch the full reference dataset (brands, fuel types, stations)."""
        log_pipeline_event(logger, "Requesting FuelCheck OAuth2 access token", "bronze")
        token = self._fetch_token()

        api_key = os.environ.get("FUELCHECK_API_KEY", "").strip()
        transaction_id = str(uuid.uuid4())
        request_timestamp = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M:%S")

        log_pipeline_event(logger, "Requesting FuelCheck station reference data", "bronze")
        response = requests.get(
            REFDATA_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "apikey": api_key,
                "transactionid": transaction_id,
                "requesttimestamp": request_timestamp,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()

        stations = payload.get("stations", {}).get("items", [])
        self._metadata.update(
            {
                "status": "success",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "record_count": len(stations),
                "transaction_id": transaction_id,
            }
        )
        log_pipeline_event(
            logger,
            f"Fetched {len(stations)} official station reference records",
            "bronze",
            details={"record_count": len(stations)},
        )
        return payload

    def to_raw_records(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert the reference payload's station list into Bronze-ready records."""
        import json

        ingested_at = datetime.now(timezone.utc).isoformat()
        records = []
        for item in payload.get("stations", {}).get("items", []):
            address = item.get("address")
            parsed = parse_nsw_address(address)
            location = item.get("location") or {}
            record_hash = compute_record_hash(
                item.get("code"), item.get("name"), address, item.get("brand")
            )
            records.append(
                {
                    "station_code": item.get("code"),
                    "station_name": item.get("name"),
                    "brand": item.get("brand"),
                    "address": address,
                    "suburb": parsed.suburb,
                    "state": item.get("state"),
                    "postcode": parsed.postcode,
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                    "station_type": "retail",
                    "raw_json": json.dumps(item, sort_keys=True),
                    "_ingested_at": ingested_at,
                    "_source_name": self.source_name,
                    "_source_url": REFDATA_URL,
                    "_source_file": "fuelcheck_referencedata_lovs",
                    "_source_record_hash": record_hash,
                    "_pipeline_run_id": self.run_id,
                }
            )
        self._metadata["record_count"] = len(records)
        return records

    @property
    def metadata(self) -> dict[str, Any]:
        return self._metadata.copy()
