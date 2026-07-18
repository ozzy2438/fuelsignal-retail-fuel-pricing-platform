"""Data Quality Checks for FuelSignal.

Reusable quality check functions that validate data at each pipeline stage.
Invalid records are never silently deleted - they are written to
silver_data_quality_issues for review.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fuelsignal.logging import get_logger

logger = get_logger(__name__)


class QualityCheck:
    """Represents a single data quality check result."""

    def __init__(
        self,
        rule_name: str,
        source_table: str,
        target_table: str,
        severity: str = "warning",
    ):
        self.rule_name = rule_name
        self.source_table = source_table
        self.target_table = target_table
        self.severity = severity
        self.issues: list[dict] = []
        self.total_records = 0
        self.passed_records = 0
        self.failed_records = 0

    def add_issue(
        self,
        column_name: str,
        record_identifier: str,
        description: str,
        raw_value: Any = None,
        action: str = "flag",
    ) -> None:
        """Record a quality issue."""
        self.issues.append(
            {
                "issue_id": str(uuid.uuid4()),
                "pipeline_run_id": "",  # Set by caller
                "source_table": self.source_table,
                "target_table": self.target_table,
                "rule_name": self.rule_name,
                "severity": self.severity,
                "column_name": column_name,
                "record_identifier": str(record_identifier),
                "issue_description": description,
                "raw_value": str(raw_value)[:500] if raw_value is not None else None,
                "action_taken": action,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.failed_records += 1

    @property
    def pass_rate(self) -> float:
        """Calculate the pass rate."""
        if self.total_records == 0:
            return 0.0
        return (self.total_records - self.failed_records) / self.total_records

    @property
    def summary(self) -> dict:
        """Return a summary of this quality check."""
        return {
            "rule_name": self.rule_name,
            "source_table": self.source_table,
            "severity": self.severity,
            "total_records": self.total_records,
            "passed_records": self.total_records - self.failed_records,
            "failed_records": self.failed_records,
            "pass_rate": self.pass_rate,
            "issue_count": len(self.issues),
        }


def check_not_null(
    records: list[dict], column: str, identifier_col: str = "station_id"
) -> QualityCheck:
    """Check that a column has no null values."""
    check = QualityCheck(
        rule_name=f"{column}_not_null",
        source_table="bronze",
        target_table="silver",
        severity="critical",
    )
    check.total_records = len(records)

    for record in records:
        value = record.get(column)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            check.add_issue(
                column_name=column,
                record_identifier=record.get(identifier_col, "unknown"),
                description=f"Column '{column}' is null or empty",
                raw_value=value,
                action="quarantine",
            )

    return check


def check_price_bounds(
    records: list[dict],
    column: str = "price_cpl",
    min_cpl: float = 80.0,
    max_cpl: float = 300.0,
    identifier_col: str = "station_id",
) -> QualityCheck:
    """Check that prices are within plausible bounds."""
    check = QualityCheck(
        rule_name=f"{column}_plausible_bounds",
        source_table="bronze",
        target_table="silver",
        severity="warning",
    )
    check.total_records = len(records)

    for record in records:
        price = record.get(column)
        if price is not None:
            try:
                price_val = float(price)
                if price_val < min_cpl or price_val > max_cpl:
                    check.add_issue(
                        column_name=column,
                        record_identifier=record.get(identifier_col, "unknown"),
                        description=f"Price {price_val} outside bounds [{min_cpl}, {max_cpl}]",
                        raw_value=price,
                        action="flag",
                    )
            except (ValueError, TypeError):
                check.add_issue(
                    column_name=column,
                    record_identifier=record.get(identifier_col, "unknown"),
                    description="Price value is not numeric",
                    raw_value=price,
                    action="quarantine",
                )

    return check


def check_coordinates_nsw(
    records: list[dict],
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    identifier_col: str = "station_id",
) -> QualityCheck:
    """Check coordinates are within NSW."""
    check = QualityCheck(
        rule_name="coordinates_nsw_bounds",
        source_table="bronze",
        target_table="silver",
        severity="warning",
    )
    check.total_records = len(records)

    for record in records:
        lat = record.get(lat_col)
        lon = record.get(lon_col)

        if lat is None or lon is None:
            check.add_issue(
                column_name=f"{lat_col}/{lon_col}",
                record_identifier=record.get(identifier_col, "unknown"),
                description="Null coordinates",
                raw_value=f"lat={lat}, lon={lon}",
                action="flag",
            )
            continue

        try:
            lat_val = float(lat)
            lon_val = float(lon)
            if not (-37.5 <= lat_val <= -28.0 and 141.0 <= lon_val <= 154.0):
                check.add_issue(
                    column_name=f"{lat_col}/{lon_col}",
                    record_identifier=record.get(identifier_col, "unknown"),
                    description=f"Coordinates outside NSW: ({lat_val}, {lon_val})",
                    raw_value=f"lat={lat_val}, lon={lon_val}",
                    action="flag",
                )
        except (ValueError, TypeError):
            check.add_issue(
                column_name=f"{lat_col}/{lon_col}",
                record_identifier=record.get(identifier_col, "unknown"),
                description="Non-numeric coordinates",
                raw_value=f"lat={lat}, lon={lon}",
                action="quarantine",
            )

    return check


def check_duplicates(
    records: list[dict],
    key_columns: list[str],
    identifier_col: str = "station_id",
) -> QualityCheck:
    """Check for duplicate records based on key columns."""
    check = QualityCheck(
        rule_name=f"duplicate_{'_'.join(key_columns)}",
        source_table="bronze",
        target_table="silver",
        severity="warning",
    )
    check.total_records = len(records)

    seen_keys = {}
    for i, record in enumerate(records):
        key = tuple(str(record.get(col, "")) for col in key_columns)
        if key in seen_keys:
            check.add_issue(
                column_name="|".join(key_columns),
                record_identifier=record.get(identifier_col, f"row_{i}"),
                description=f"Duplicate key: {key}",
                raw_value=str(key),
                action="deduplicate_keep_latest",
            )
        else:
            seen_keys[key] = i

    return check
