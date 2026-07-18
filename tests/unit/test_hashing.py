"""Unit tests for hashing utilities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fuelsignal.utils.hashing import compute_record_hash, generate_pipeline_run_id


class TestComputeRecordHash:
    """Test deterministic record hashing."""

    def test_same_inputs_same_hash(self):
        """Same inputs should produce same hash."""
        h1 = compute_record_hash("station_1", "E10", "165.9", "2024-01-01")
        h2 = compute_record_hash("station_1", "E10", "165.9", "2024-01-01")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        """Different inputs should produce different hashes."""
        h1 = compute_record_hash("station_1", "E10", "165.9")
        h2 = compute_record_hash("station_2", "E10", "165.9")
        assert h1 != h2

    def test_hash_is_sha256_hex(self):
        """Hash should be 64 character hex string (SHA-256)."""
        h = compute_record_hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_none_handling(self):
        """None values should be handled consistently."""
        h1 = compute_record_hash(None, "test")
        h2 = compute_record_hash(None, "test")
        assert h1 == h2

    def test_whitespace_handling(self):
        """Leading/trailing whitespace should be stripped."""
        h1 = compute_record_hash("  test  ")
        h2 = compute_record_hash("test")
        assert h1 == h2


class TestGeneratePipelineRunId:
    """Test pipeline run ID generation."""

    def test_format(self):
        """Run ID should follow expected format."""
        run_id = generate_pipeline_run_id("bronze")
        assert run_id.startswith("bronze_")
        parts = run_id.split("_")
        assert len(parts) >= 3  # prefix_date_uuid

    def test_unique(self):
        """Each call should produce a unique ID."""
        ids = {generate_pipeline_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_custom_prefix(self):
        """Custom prefix should be used."""
        run_id = generate_pipeline_run_id("silver")
        assert run_id.startswith("silver_")
