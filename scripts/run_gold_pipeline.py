"""Build and validate the live FuelSignal Gold analytical feature layer.

Reads only from the already-populated Silver layer (never re-scans Bronze). Builds
gold_station_daily_market first (the single source of truth for each station's daily
close price), then every other Gold table reads from that physical table rather than
re-aggregating Silver again - see docs/feature-engineering.md for the full dependency
chain and grain documentation, and docs/jump-label-definition.md for the price-jump
label methodology.
"""

# ruff: noqa: E501, S603, S607, S608

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from deploy_databricks_foundation import (  # noqa: E402
    DatabricksSqlClient,
    DeploymentError,
)
from run_ingestion_pipeline import (  # noqa: E402
    databricks_auth,
    git_commit_short,
    sql_literal,
)

from fuelsignal.config import load_env, load_project_config  # noqa: E402
from fuelsignal.gold.jump_labels import summarize_threshold_sensitivity  # noqa: E402
from fuelsignal.gold.schemas import GOLD_SCHEMAS, get_gold_ddl  # noqa: E402

CATALOG = "fuelsignal"
SCHEMA_PREFIX = "fuelsignal"


class GoldPipeline:
    """Orchestrate live Silver-to-Gold SQL transformations via a Databricks SQL warehouse."""

    def __init__(self, client: DatabricksSqlClient, run_id: str):
        self.client = client
        self.run_id = run_id
        self.silver = f"{CATALOG}.{SCHEMA_PREFIX}_silver"
        self.gold = f"{CATALOG}.{SCHEMA_PREFIX}_gold"
        self.monitoring = f"{CATALOG}.{SCHEMA_PREFIX}_monitoring"
        self.code_version = git_commit_short()

    def validate_prerequisites(self) -> None:
        """Require populated Silver tables; never recreate Bronze/Silver."""
        for table in ("silver_fuel_prices", "silver_station_master", "silver_competitor_pairs"):
            count = self.count(f"{self.silver}.{table}")
            if count == 0:
                raise RuntimeError(
                    f"{self.silver}.{table} is empty - run scripts/run_ingestion_pipeline.py first"
                )

    def reset_gold_tables(self) -> None:
        """Fully rebuild the Gold schema and data from Silver on every run.

        Unlike Bronze/Silver (append-only source-of-truth data, merged by content
        hash), Gold is a fully-derived analytical layer: every column here is
        reproducible from Silver at any time, so "idempotent" for Gold means "always
        rebuilds the same result from the same Silver input," not incremental merge.
        Tables are dropped and recreated from the current schema definitions
        (documented in data-contracts.md) so a schema change never leaves stale
        columns behind.
        """
        for table_name in GOLD_SCHEMAS:
            qualified = f"{self.gold}.{table_name}"
            exists = self.client.execute(f"SHOW TABLES IN {self.gold} LIKE '{table_name}'")
            if exists.get("result", {}).get("data_array"):
                self.client.execute(f"DROP TABLE {qualified}")
        for ddl in get_gold_ddl(self.gold).values():
            self.client.execute(ddl)

    def count(self, table: str) -> int:
        result = self.client.execute(f"SELECT count(*) FROM {table}")
        return int(result["result"]["data_array"][0][0])

    def build_station_daily_market(self) -> None:
        """Daily station price construction + local (5km) competitor positioning.

        Daily price rule: daily_close_price_cpl is the price at the final (latest
        observed_at) valid observation for that station/fuel/date - the documented
        canonical daily representative price used by every downstream Gold table.
        Local competitor stats use only same-day, same-fuel-type competitor prices
        (no forward/back-fill). silver_competitor_pairs is single-direction
        (station_id < competitor_station_id), so it is expanded to a symmetric
        adjacency list here before use.
        """
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_station_daily_market
            WITH daily AS (
              SELECT station_id, fuel_type, observed_date AS market_date,
                min_by(price_cpl, observed_at) daily_open_price_cpl,
                max_by(price_cpl, observed_at) daily_close_price_cpl,
                min(price_cpl) daily_min_price_cpl,
                max(price_cpl) daily_max_price_cpl,
                count(*) daily_observation_count,
                max(observed_at) last_observed_at
              FROM {self.silver}.silver_fuel_prices
              GROUP BY station_id, fuel_type, observed_date
            ),
            market AS (
              SELECT *,
                percentile_approx(daily_close_price_cpl, 0.5)
                  OVER (PARTITION BY fuel_type, market_date) AS market_median_price_cpl
              FROM daily
            ),
            all_pairs AS (
              SELECT station_id, competitor_station_id FROM {self.silver}.silver_competitor_pairs
              UNION ALL
              SELECT competitor_station_id AS station_id, station_id AS competitor_station_id
              FROM {self.silver}.silver_competitor_pairs
            ),
            pair_daily AS (
              SELECT d.station_id, d.fuel_type, d.market_date, d.daily_close_price_cpl AS own_price,
                cd.daily_close_price_cpl AS competitor_price
              FROM daily d
              JOIN all_pairs ap ON ap.station_id = d.station_id
              JOIN daily cd ON cd.station_id = ap.competitor_station_id
                AND cd.fuel_type = d.fuel_type AND cd.market_date = d.market_date
            ),
            competitor_stats AS (
              SELECT station_id, fuel_type, market_date,
                count(competitor_price) competitor_count,
                min(competitor_price) local_competitor_min_price_cpl,
                max(competitor_price) local_competitor_max_price_cpl,
                percentile_approx(competitor_price, 0.5) local_competitor_median_price_cpl,
                avg(competitor_price) local_competitor_mean_price_cpl,
                1 + sum(CASE WHEN competitor_price < own_price THEN 1 ELSE 0 END) AS rank_within_local_market,
                sum(CASE WHEN competitor_price < own_price THEN 1 ELSE 0 END) * 1.0 / count(*) AS station_price_percentile
              FROM pair_daily
              GROUP BY station_id, fuel_type, market_date, own_price
            )
            SELECT
              m.station_id, m.fuel_type, m.market_date,
              m.daily_open_price_cpl, m.daily_close_price_cpl, m.daily_min_price_cpl,
              m.daily_max_price_cpl, m.daily_observation_count, m.last_observed_at,
              m.market_median_price_cpl,
              m.daily_close_price_cpl - m.market_median_price_cpl AS price_vs_market_median_cpl,
              coalesce(cs.competitor_count, 0) AS competitor_count,
              cs.local_competitor_min_price_cpl, cs.local_competitor_max_price_cpl,
              cs.local_competitor_median_price_cpl, cs.local_competitor_mean_price_cpl,
              m.daily_close_price_cpl - cs.local_competitor_median_price_cpl AS station_vs_competitor_median_cpl,
              cs.station_price_percentile, cs.rank_within_local_market,
              {run_id} AS _pipeline_run_id, current_timestamp() AS ingested_at
            FROM market m
            LEFT JOIN competitor_stats cs
              ON m.station_id = cs.station_id AND m.fuel_type = cs.fuel_type
              AND m.market_date = cs.market_date
            """
        )

    def build_indicative_margin(self) -> None:
        """Retail-minus-TGP margin, ASOF-joined to the Sydney terminal series.

        TGP only covers U91 and DL - other retail fuel types will have NULL tgp_cpl by
        construction (see docs/feature-engineering.md for the measured unmatched rate).
        `tgp_cpl` uses a same-day-or-latest-prior-date ("ASOF") join since AIP does not
        publish for every calendar date; `price_tgp_spread_cpl` uses only an exact
        same-day match (NULL otherwise) so the two columns together show how much the
        ASOF fallback changes the result. This is an INDICATIVE margin only - it is not
        a realised P&L margin (no freight, opex, or franchise fees).
        """
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_indicative_margin
            WITH retail AS (
              SELECT station_id, fuel_type, market_date, daily_close_price_cpl
              FROM {self.gold}.gold_station_daily_market
            ),
            tgp_mapped AS (
              SELECT tgp_date, fuel_type, tgp_cpl
              FROM {self.silver}.silver_terminal_gate_prices
              WHERE city = 'Sydney'
            ),
            tgp_exact AS (
              SELECT r.station_id, r.fuel_type, r.market_date, t.tgp_cpl AS exact_tgp_cpl
              FROM retail r
              LEFT JOIN tgp_mapped t ON t.fuel_type = r.fuel_type AND t.tgp_date = r.market_date
            ),
            distinct_dates AS (
              SELECT DISTINCT fuel_type, market_date FROM retail
            ),
            asof AS (
              SELECT d.fuel_type, d.market_date,
                max(t.tgp_date) AS asof_tgp_date
              FROM distinct_dates d
              LEFT JOIN tgp_mapped t ON t.fuel_type = d.fuel_type AND t.tgp_date <= d.market_date
              GROUP BY d.fuel_type, d.market_date
            ),
            asof_value AS (
              SELECT a.fuel_type, a.market_date, t.tgp_cpl AS asof_tgp_cpl,
                CASE WHEN a.asof_tgp_date = a.market_date THEN 'exact_same_day'
                     WHEN a.asof_tgp_date IS NOT NULL THEN 'latest_prior_date'
                     ELSE 'unmatched' END AS tgp_match_type
              FROM asof a
              LEFT JOIN tgp_mapped t ON t.fuel_type = a.fuel_type AND t.tgp_date = a.asof_tgp_date
            ),
            margin AS (
              SELECT r.station_id, r.fuel_type, r.market_date, r.daily_close_price_cpl AS retail_price_cpl,
                av.asof_tgp_cpl AS tgp_cpl, av.tgp_match_type,
                r.daily_close_price_cpl - av.asof_tgp_cpl AS indicative_margin_cpl,
                r.daily_close_price_cpl - te.exact_tgp_cpl AS price_tgp_spread_cpl,
                row_number() OVER (
                  PARTITION BY r.station_id, r.fuel_type ORDER BY r.market_date
                ) AS rn
              FROM retail r
              LEFT JOIN asof_value av ON av.fuel_type = r.fuel_type AND av.market_date = r.market_date
              LEFT JOIN tgp_exact te ON te.station_id = r.station_id AND te.fuel_type = r.fuel_type
                AND te.market_date = r.market_date
            ),
            margin_avg AS (
              SELECT station_id, fuel_type, market_date, retail_price_cpl, tgp_cpl, tgp_match_type,
                indicative_margin_cpl, price_tgp_spread_cpl, rn,
                indicative_margin_cpl - avg(indicative_margin_cpl) OVER (
                  PARTITION BY station_id, fuel_type ORDER BY market_date
                  ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ) AS margin_vs_7d_avg_cpl
              FROM margin
            ),
            -- percent_rank() cannot take a custom ROWS BETWEEN frame (it always ranks
            -- over the whole partition, which would leak future observations into a
            -- "rolling 30-day" feature). Compute a genuinely trailing-only 30-observation
            -- percentile instead, via a bounded self-join on row_number.
            rolling_percentile AS (
              SELECT a.station_id, a.fuel_type, a.market_date,
                (sum(CASE WHEN b.indicative_margin_cpl <= a.indicative_margin_cpl THEN 1 ELSE 0 END) - 1)
                  / nullif(count(b.indicative_margin_cpl) - 1, 0) AS margin_percentile_30d
              FROM margin_avg a
              JOIN margin_avg b
                ON a.station_id = b.station_id AND a.fuel_type = b.fuel_type
                AND b.rn BETWEEN a.rn - 29 AND a.rn
              GROUP BY a.station_id, a.fuel_type, a.market_date, a.indicative_margin_cpl
            )
            SELECT ma.station_id, ma.fuel_type, ma.market_date, ma.retail_price_cpl, ma.tgp_cpl,
              ma.tgp_match_type, ma.indicative_margin_cpl, ma.price_tgp_spread_cpl,
              ma.margin_vs_7d_avg_cpl, rp.margin_percentile_30d,
              {run_id} AS _pipeline_run_id
            FROM margin_avg ma
            JOIN rolling_percentile rp
              ON ma.station_id = rp.station_id AND ma.fuel_type = rp.fuel_type AND ma.market_date = rp.market_date
            """
        )

    def build_market_cycle_features(self, jump_threshold_cpl: float) -> None:
        """Trailing-only SQL window features. Every value for date D uses only rows on
        or before D (ROWS BETWEEN N PRECEDING AND CURRENT ROW / LAG never look forward).
        """
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_market_cycle_features
            WITH base AS (
              SELECT m.station_id, m.fuel_type, m.market_date, m.daily_close_price_cpl,
                m.market_median_price_cpl, i.indicative_margin_cpl, i.tgp_cpl,
                dayofweek(m.market_date) AS day_of_week,
                CASE WHEN h.holiday_date IS NOT NULL THEN true ELSE false END AS is_public_holiday
              FROM {self.gold}.gold_station_daily_market m
              LEFT JOIN {self.gold}.gold_indicative_margin i
                ON m.station_id = i.station_id AND m.fuel_type = i.fuel_type AND m.market_date = i.market_date
              LEFT JOIN {self.silver}.silver_public_holidays h ON m.market_date = h.holiday_date
            ),
            windows AS (
              SELECT *,
                min(daily_close_price_cpl) OVER (w7) AS rolling_7d_min_price,
                max(daily_close_price_cpl) OVER (w7) AS rolling_7d_max_price,
                avg(daily_close_price_cpl) OVER (w7) AS rolling_7d_mean_price,
                stddev(daily_close_price_cpl) OVER (w7) AS rolling_7d_std_price,
                min(daily_close_price_cpl) OVER (w14) AS rolling_14d_min_price,
                max(daily_close_price_cpl) OVER (w14) AS rolling_14d_max_price,
                daily_close_price_cpl - lag(daily_close_price_cpl, 14) OVER (w_all) AS rolling_14d_price_change_cpl,
                daily_close_price_cpl - lag(daily_close_price_cpl, 1) OVER (w_all) AS own_daily_change_cpl,
                market_median_price_cpl - lag(market_median_price_cpl, 1) OVER (w_market) AS market_daily_change_cpl,
                tgp_cpl - lag(tgp_cpl, 7) OVER (w_all) AS tgp_7d_change_cpl,
                avg(indicative_margin_cpl) OVER (w7) - indicative_margin_cpl AS margin_compression_cpl
              FROM base
              WINDOW
                w7 AS (PARTITION BY station_id, fuel_type ORDER BY market_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
                w14 AS (PARTITION BY station_id, fuel_type ORDER BY market_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
                w_all AS (PARTITION BY station_id, fuel_type ORDER BY market_date),
                w_market AS (PARTITION BY fuel_type ORDER BY market_date)
            ),
            flags AS (
              SELECT *,
                CASE WHEN daily_close_price_cpl = rolling_14d_min_price THEN market_date ELSE NULL END AS is_local_min_date,
                CASE WHEN own_daily_change_cpl >= {jump_threshold_cpl} THEN market_date ELSE NULL END AS is_own_jump_date
              FROM windows
            ),
            carried AS (
              SELECT *,
                max(is_local_min_date) OVER (
                  PARTITION BY station_id, fuel_type ORDER BY market_date
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS last_local_min_date,
                max(is_own_jump_date) OVER (
                  PARTITION BY station_id, fuel_type ORDER BY market_date
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS last_own_jump_date
              FROM flags
            )
            SELECT station_id, fuel_type, market_date,
              rolling_7d_min_price, rolling_7d_max_price, rolling_7d_mean_price, rolling_7d_std_price,
              rolling_14d_min_price, rolling_14d_max_price, rolling_14d_price_change_cpl,
              datediff(market_date, last_local_min_date) AS days_since_local_minimum,
              datediff(market_date, last_own_jump_date) AS days_since_last_detected_jump,
              CASE WHEN rolling_14d_max_price > rolling_14d_min_price THEN
                (daily_close_price_cpl - rolling_14d_min_price) / (rolling_14d_max_price - rolling_14d_min_price)
                ELSE NULL END AS price_position_within_14d_range,
              market_median_price_cpl, market_daily_change_cpl, tgp_7d_change_cpl, margin_compression_cpl,
              day_of_week, is_public_holiday, {run_id} AS _pipeline_run_id
            FROM carried
            """
        )

    def build_competitor_positioning(self) -> None:
        """Detailed per-competitor-pair daily rows (drill-down grain, not the aggregate
        stats already stored on gold_station_daily_market)."""
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_competitor_positioning
            WITH all_pairs AS (
              SELECT station_id, competitor_station_id, distance_km FROM {self.silver}.silver_competitor_pairs
              UNION ALL
              SELECT competitor_station_id AS station_id, station_id AS competitor_station_id, distance_km
              FROM {self.silver}.silver_competitor_pairs
            ),
            joined AS (
              SELECT m.station_id, m.fuel_type, m.market_date, ap.competitor_station_id,
                sm.brand AS competitor_brand, ap.distance_km,
                m.daily_close_price_cpl AS station_price_cpl, cm.daily_close_price_cpl AS competitor_price_cpl
              FROM {self.gold}.gold_station_daily_market m
              JOIN all_pairs ap ON ap.station_id = m.station_id
              JOIN {self.gold}.gold_station_daily_market cm
                ON cm.station_id = ap.competitor_station_id AND cm.fuel_type = m.fuel_type
                AND cm.market_date = m.market_date
              LEFT JOIN {self.silver}.silver_station_master sm ON sm.station_id = ap.competitor_station_id
            )
            SELECT station_id, fuel_type, market_date, competitor_station_id, competitor_brand, distance_km,
              station_price_cpl, competitor_price_cpl, station_price_cpl - competitor_price_cpl AS price_difference_cpl,
              station_price_cpl <= min(competitor_price_cpl) OVER (
                PARTITION BY station_id, fuel_type, market_date
              ) AS is_cheapest_local,
              rank() OVER (
                PARTITION BY station_id, fuel_type, market_date ORDER BY competitor_price_cpl
              ) AS rank_in_local_market,
              {run_id} AS _pipeline_run_id
            FROM joined
            """
        )

    def build_daily_pricing_inputs(self) -> None:
        """Combine feature tables (never labels) into the final model-input table."""
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_daily_pricing_inputs
            SELECT
              m.station_id, m.fuel_type, m.market_date, m.daily_close_price_cpl,
              m.market_median_price_cpl, m.local_competitor_median_price_cpl,
              m.local_competitor_min_price_cpl, m.local_competitor_max_price_cpl,
              m.station_price_percentile, m.station_vs_competitor_median_cpl, m.rank_within_local_market,
              m.competitor_count, i.tgp_cpl, i.indicative_margin_cpl, c.margin_compression_cpl,
              c.rolling_7d_min_price, c.rolling_7d_max_price, c.rolling_7d_mean_price, c.rolling_7d_std_price,
              c.rolling_14d_min_price, c.rolling_14d_max_price, c.rolling_14d_price_change_cpl,
              c.days_since_local_minimum, c.days_since_last_detected_jump, c.price_position_within_14d_range,
              c.tgp_7d_change_cpl, c.day_of_week, c.is_public_holiday, {run_id} AS _pipeline_run_id
            FROM {self.gold}.gold_station_daily_market m
            LEFT JOIN {self.gold}.gold_market_cycle_features c
              ON m.station_id = c.station_id AND m.fuel_type = c.fuel_type AND m.market_date = c.market_date
            LEFT JOIN {self.gold}.gold_indicative_margin i
              ON m.station_id = i.station_id AND m.fuel_type = i.fuel_type AND m.market_date = i.market_date
            """
        )

    def build_jump_labels(self, jump_threshold_cpl: float) -> None:
        """Market-level (fuel_type x date) TARGET labels. Uses future information by
        design (jump_within_24h/48h look ahead via LEAD) - never join this table into a
        feature table."""
        run_id = sql_literal(self.run_id)
        self.client.execute(
            f"""
            INSERT INTO {self.gold}.gold_price_jump_labels
            WITH market AS (
              SELECT DISTINCT fuel_type, market_date, market_median_price_cpl
              FROM {self.gold}.gold_station_daily_market
            ),
            changes AS (
              SELECT fuel_type, market_date, market_median_price_cpl,
                market_median_price_cpl - lag(market_median_price_cpl, 1)
                  OVER (PARTITION BY fuel_type ORDER BY market_date) AS market_daily_change_cpl
              FROM market
            ),
            flagged AS (
              SELECT *,
                CASE WHEN market_daily_change_cpl >= {jump_threshold_cpl} THEN true ELSE false END AS jump_today
              FROM changes
            )
            SELECT fuel_type, market_date, market_median_price_cpl, market_daily_change_cpl,
              {jump_threshold_cpl} AS jump_threshold_cpl, jump_today,
              lead(jump_today, 1) OVER (PARTITION BY fuel_type ORDER BY market_date) AS jump_within_24h,
              coalesce(
                lead(jump_today, 1) OVER (PARTITION BY fuel_type ORDER BY market_date), false
              ) OR coalesce(
                lead(jump_today, 2) OVER (PARTITION BY fuel_type ORDER BY market_date), false
              ) AS jump_within_48h,
              {run_id} AS _pipeline_run_id
            FROM flagged
            """
        )

    def threshold_sensitivity_report(self, candidates: list[float]) -> list[dict[str, Any]]:
        """Compute live sensitivity stats per candidate threshold, per fuel type."""
        report = []
        for fuel_type in self.distinct_fuel_types():
            result = self.client.execute(
                f"""
                WITH market AS (
                  SELECT DISTINCT market_date, market_median_price_cpl
                  FROM {self.gold}.gold_station_daily_market
                  WHERE fuel_type = {sql_literal(fuel_type)}
                ),
                changes AS (
                  SELECT market_date,
                    market_median_price_cpl - lag(market_median_price_cpl, 1) OVER (ORDER BY market_date) AS chg
                  FROM market
                )
                SELECT {", ".join(
                    f"sum(CASE WHEN chg >= {t} THEN 1 ELSE 0 END) AS ge_{str(t).replace('.', '_')}"
                    for t in candidates
                )}, count(*) AS total_days
                FROM changes
                """
            )
            row = result["result"]["data_array"][0]
            for i, threshold in enumerate(candidates):
                event_count = int(row[i])
                total_days = int(row[-1])
                report.append(
                    {
                        "fuel_type": fuel_type,
                        "threshold_cpl": threshold,
                        "event_count": event_count,
                        "total_days": total_days,
                        "event_frequency": (
                            round(event_count / total_days, 4) if total_days else 0.0
                        ),
                    }
                )
        return report

    def distinct_fuel_types(self) -> list[str]:
        result = self.client.execute(
            f"SELECT DISTINCT fuel_type FROM {self.gold}.gold_station_daily_market ORDER BY fuel_type"
        )
        return [row[0] for row in result["result"]["data_array"]]

    def market_change_series(self, fuel_type: str) -> list[float | None]:
        """Ordered day-over-day market median changes for one fuel type (for cross-
        checking the live SQL threshold logic against the pure-Python module)."""
        result = self.client.execute(
            f"""
            SELECT market_daily_change_cpl FROM {self.gold}.gold_price_jump_labels
            WHERE fuel_type = {sql_literal(fuel_type)} ORDER BY market_date
            """
        )
        return [None if row[0] is None else float(row[0]) for row in result["result"]["data_array"]]

    def run_leakage_checks(self) -> dict[str, Any]:
        """Post-hoc SQL assertions that the built tables have no lookahead and no
        duplicate business keys. Window-frame leakage is impossible by construction
        (every rolling feature uses ROWS BETWEEN N PRECEDING AND CURRENT ROW / LAG,
        never FOLLOWING/LEAD - grep-verified below), so these checks focus on what SQL
        semantics alone cannot guarantee: key uniqueness and join-date ordering.
        """
        checks: dict[str, Any] = {}

        source_text = Path(__file__).read_text()
        feature_building_methods = (
            "build_station_daily_market",
            "build_indicative_margin",
            "build_market_cycle_features",
            "build_daily_pricing_inputs",
        )
        leaked_following = []
        for method in feature_building_methods:
            start = source_text.index(f"def {method}(")
            end = source_text.index("\n    def ", start + 1)
            body = source_text[start:end]
            if "FOLLOWING" in body or "LEAD(" in body.upper():
                leaked_following.append(method)
        checks["feature_methods_use_only_preceding_or_lag"] = leaked_following == []
        checks["feature_methods_with_following_or_lead"] = leaked_following

        for table, keys in (
            ("gold_station_daily_market", "station_id, fuel_type, market_date"),
            ("gold_market_cycle_features", "station_id, fuel_type, market_date"),
            ("gold_indicative_margin", "station_id, fuel_type, market_date"),
            ("gold_daily_pricing_inputs", "station_id, fuel_type, market_date"),
            ("gold_price_jump_labels", "fuel_type, market_date"),
        ):
            result = self.client.execute(
                f"""
                SELECT count(*) FROM (
                  SELECT {keys}, count(*) c FROM {self.gold}.{table} GROUP BY {keys} HAVING c > 1
                )
                """
            )
            checks[f"{table}_duplicate_keys"] = int(result["result"]["data_array"][0][0])

        result = self.client.execute(
            f"""
            SELECT count(*) FROM {self.gold}.gold_indicative_margin
            WHERE tgp_match_type = 'latest_prior_date'
            """
        )
        checks["margin_rows_using_prior_date_tgp_fallback"] = int(
            result["result"]["data_array"][0][0]
        )

        result = self.client.execute(
            f"""
            SELECT count(*) FROM {self.gold}.gold_daily_pricing_inputs f
            JOIN {self.gold}.gold_price_jump_labels l USING (fuel_type, market_date)
            """
        )
        checks["daily_pricing_inputs_joinable_to_labels_rowcount"] = int(
            result["result"]["data_array"][0][0]
        )
        return checks

    def write_gold_dq(self) -> dict[str, Any]:
        """Compute and persist Gold-layer DQ metrics into monitoring_data_quality_results."""
        metrics: dict[str, Any] = {}
        for table in GOLD_SCHEMAS:
            metrics[f"{table}_row_count"] = self.count(f"{self.gold}.{table}")

        result = self.client.execute(
            f"""
            SELECT count(DISTINCT station_id), min(market_date), max(market_date),
              count(DISTINCT fuel_type)
            FROM {self.gold}.gold_station_daily_market
            """
        )
        row = result["result"]["data_array"][0]
        metrics["distinct_stations"] = int(row[0])
        metrics["date_range"] = [row[1], row[2]]
        metrics["distinct_fuel_types"] = int(row[3])

        result = self.client.execute(
            f"""
            SELECT
              round(100.0 * sum(CASE WHEN tgp_cpl IS NOT NULL THEN 1 ELSE 0 END) / count(*), 2),
              round(100.0 * sum(CASE WHEN indicative_margin_cpl < -50 OR indicative_margin_cpl > 100
                THEN 1 ELSE 0 END) / count(*), 4),
              sum(CASE WHEN indicative_margin_cpl < -50 OR indicative_margin_cpl > 100 THEN 1 ELSE 0 END)
            FROM {self.gold}.gold_indicative_margin
            """
        )
        row = result["result"]["data_array"][0]
        metrics["pct_rows_with_valid_tgp"] = float(row[0])
        metrics["pct_implausible_margin"] = float(row[1])
        metrics["implausible_margin_row_count"] = int(row[2])

        result = self.client.execute(
            f"""
            SELECT round(100.0 * sum(CASE WHEN competitor_count > 0 THEN 1 ELSE 0 END) / count(*), 2)
            FROM {self.gold}.gold_station_daily_market
            """
        )
        metrics["pct_rows_with_competitor_coverage"] = float(result["result"]["data_array"][0][0])

        result = self.client.execute(
            f"""
            SELECT count(*) FROM {self.gold}.gold_market_cycle_features
            WHERE abs(rolling_14d_price_change_cpl) > 100
            """
        )
        metrics["extreme_14d_price_changes"] = int(result["result"]["data_array"][0][0])

        result = self.client.execute(
            f"""
            SELECT fuel_type, sum(CASE WHEN jump_today THEN 1 ELSE 0 END), count(*)
            FROM {self.gold}.gold_price_jump_labels GROUP BY fuel_type ORDER BY fuel_type
            """
        )
        metrics["jump_label_counts_by_fuel_type"] = {
            row[0]: {"jump_days": int(row[1]), "total_days": int(row[2])}
            for row in result["result"]["data_array"]
        }

        result = self.client.execute(
            f"""
            SELECT count(*) FROM (
              SELECT station_id, fuel_type, count(*) c FROM {self.gold}.gold_station_daily_market
              GROUP BY station_id, fuel_type HAVING c < 14
            )
            """
        )
        metrics["station_fuel_pairs_with_under_14_days_history"] = int(
            result["result"]["data_array"][0][0]
        )

        rows = []
        for name, value in metrics.items():
            if isinstance(value, int | float):
                rows.append((name, value))
        for metric_name, metric_value in rows:
            self.client.execute(
                f"""
                INSERT INTO {self.monitoring}.monitoring_data_quality_results
                SELECT {sql_literal(self.run_id)}, current_timestamp(), {sql_literal(self.gold)},
                  {sql_literal(metric_name)}, {sql_literal('Gold layer DQ metric')}, 'info',
                  cast(NULL AS BIGINT), cast(NULL AS BIGINT), cast(NULL AS BIGINT),
                  cast(NULL AS DOUBLE), cast(NULL AS DOUBLE), 'reported',
                  {sql_literal(json.dumps({"value": metric_value}))}
                """
            )
        return metrics


def main() -> int:
    load_env()
    run_id = f"gold-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    host, token = databricks_auth()
    client = DatabricksSqlClient(
        host=host, token=token, warehouse_id=os.environ.get("DATABRICKS_WAREHOUSE_ID") or None
    )
    pipeline = GoldPipeline(client, run_id)
    project_config = load_project_config()
    jump_threshold = project_config["jump_detection"]["min_jump_cpl"]
    candidate_thresholds = project_config["jump_detection"]["candidate_jump_thresholds_cpl"]

    try:
        pipeline.validate_prerequisites()
        pipeline.reset_gold_tables()

        print("Building gold_station_daily_market...", file=sys.stderr)
        pipeline.build_station_daily_market()

        print("Building gold_indicative_margin...", file=sys.stderr)
        pipeline.build_indicative_margin()

        print("Building gold_market_cycle_features...", file=sys.stderr)
        pipeline.build_market_cycle_features(jump_threshold)

        print("Building gold_competitor_positioning...", file=sys.stderr)
        pipeline.build_competitor_positioning()

        print("Building gold_daily_pricing_inputs...", file=sys.stderr)
        pipeline.build_daily_pricing_inputs()

        print("Building gold_price_jump_labels...", file=sys.stderr)
        pipeline.build_jump_labels(jump_threshold)

        print("Running threshold sensitivity report...", file=sys.stderr)
        sensitivity = pipeline.threshold_sensitivity_report(candidate_thresholds)

        print("Running leakage checks...", file=sys.stderr)
        leakage = pipeline.run_leakage_checks()

        print("Writing Gold DQ metrics...", file=sys.stderr)
        dq_metrics = pipeline.write_gold_dq()

        # Cross-check the live SQL-computed sensitivity against the pure-Python module:
        # both must agree on event_count for the reference fuel type at every candidate
        # threshold, confirming the SQL and the unit-tested Python logic implement the
        # same definition.
        print("Cross-checking SQL sensitivity against the Python module...", file=sys.stderr)
        reference_fuel_type = (
            "U91" if "U91" in pipeline.distinct_fuel_types() else sensitivity[0]["fuel_type"]
        )
        python_series = pipeline.market_change_series(reference_fuel_type)
        cross_check = {}
        for threshold in candidate_thresholds:
            python_result = summarize_threshold_sensitivity(python_series, threshold)
            sql_result = next(
                s
                for s in sensitivity
                if s["fuel_type"] == reference_fuel_type and s["threshold_cpl"] == threshold
            )
            cross_check[str(threshold)] = {
                "python_event_count": python_result.event_count,
                "sql_event_count": sql_result["event_count"],
                "agree": python_result.event_count == sql_result["event_count"],
            }

        summary = {
            "run_id": run_id,
            "jump_threshold_cpl": jump_threshold,
            "reference_fuel_type_for_cross_check": reference_fuel_type,
            "threshold_sensitivity": sensitivity,
            "python_sql_cross_check": cross_check,
            "leakage_checks": leakage,
            "gold_dq_metrics": dq_metrics,
        }
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0
    except (DeploymentError, OSError, RuntimeError, ValueError) as exc:
        print(f"Gold pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
