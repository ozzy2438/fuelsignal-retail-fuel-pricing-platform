"""Tests for the Gold pipeline orchestration script."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_gold_pipeline.py"
SCRIPTS_DIR = str(SCRIPT_PATH.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
SPEC = importlib.util.spec_from_file_location("run_gold_pipeline", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
gold_pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gold_pipeline
SPEC.loader.exec_module(gold_pipeline)


FEATURE_BUILDING_METHODS = (
    "build_station_daily_market",
    "build_indicative_margin",
    "build_market_cycle_features",
    "build_daily_pricing_inputs",
)


@pytest.mark.unit
def test_feature_building_methods_exist_on_gold_pipeline() -> None:
    # run_leakage_checks() string-matches these method names out of this file's own
    # source; if one gets renamed without updating that list, the leakage check would
    # silently stop covering it. Guard against that here.
    for method_name in FEATURE_BUILDING_METHODS:
        assert hasattr(gold_pipeline.GoldPipeline, method_name), method_name


@pytest.mark.unit
def test_feature_building_methods_source_has_no_following_or_lead() -> None:
    source_text = SCRIPT_PATH.read_text()
    for method in FEATURE_BUILDING_METHODS:
        start = source_text.index(f"def {method}(")
        end = source_text.index("\n    def ", start + 1)
        body = source_text[start:end]
        assert "FOLLOWING" not in body, method
        assert "LEAD(" not in body.upper(), method


@pytest.mark.unit
def test_build_jump_labels_is_allowed_to_use_lead() -> None:
    # The label builder is the one place LEAD() is expected and correct - it computes
    # forward-looking targets, not features.
    source_text = SCRIPT_PATH.read_text()
    start = source_text.index("def build_jump_labels(")
    end = source_text.index("\n    def ", start + 1)
    body = source_text[start:end]
    assert "lead(" in body.lower()


@pytest.mark.unit
def test_gold_schemas_cover_all_expected_tables() -> None:
    expected = {
        "gold_station_daily_market",
        "gold_market_cycle_features",
        "gold_competitor_positioning",
        "gold_indicative_margin",
        "gold_daily_pricing_inputs",
        "gold_price_jump_labels",
    }
    assert expected == set(gold_pipeline.GOLD_SCHEMAS.keys())


@pytest.mark.unit
def test_market_change_series_casts_to_float_or_none() -> None:
    class _FakeClient:
        def execute(self, statement: str) -> dict:
            return {"result": {"data_array": [[None], ["5.0"], ["-1.4"]]}}

    pipeline = gold_pipeline.GoldPipeline(_FakeClient(), "test-run")
    series = pipeline.market_change_series("U91")
    assert series == [None, 5.0, -1.4]
    assert all(value is None or isinstance(value, float) for value in series)
