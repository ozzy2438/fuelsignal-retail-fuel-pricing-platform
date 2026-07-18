"""Tests for FuelCheck station identity matching helpers."""

import pytest

from fuelsignal.silver.station_matching import (
    names_agree,
    normalize_address_key,
    normalize_text,
    parse_nsw_address,
)


@pytest.mark.unit
def test_parse_nsw_address_primary_pattern() -> None:
    result = parse_nsw_address("307-313 Ocean Beach Road, UMINA BEACH NSW 2257")
    assert result.street == "307-313 Ocean Beach Road"
    assert result.suburb == "UMINA BEACH"
    assert result.postcode == "2257"


@pytest.mark.unit
def test_parse_nsw_address_fallback_no_comma() -> None:
    # Without a comma delimiter the street/suburb boundary is inherently ambiguous
    # (e.g. is it "94 Sturt Hwy" + "Balranald", or "94" + "Sturt Hwy Balranald"?).
    # The postcode is what matters for matching and is always parsed correctly.
    result = parse_nsw_address("94 Sturt Hwy BALRANALD NSW 2715")
    assert result.postcode == "2715"
    assert "BALRANALD" in result.suburb


@pytest.mark.unit
def test_parse_nsw_address_unparseable_returns_all_none() -> None:
    result = parse_nsw_address(
        "CNR BowCNR Bowen St and Redfern Sten St and Redfern St Macquarie ACT 2614"
    )
    assert result == (None, None, None)


@pytest.mark.unit
def test_parse_nsw_address_handles_none_and_empty() -> None:
    assert parse_nsw_address(None) == (None, None, None)
    assert parse_nsw_address("") == (None, None, None)


@pytest.mark.unit
def test_normalize_text_collapses_punctuation_and_case() -> None:
    assert normalize_text("  307-313 Ocean Beach Road!  ") == "307 313 OCEAN BEACH ROAD"
    assert normalize_text(None) == ""


@pytest.mark.unit
def test_normalize_address_key_is_deterministic_and_case_insensitive() -> None:
    key_a = normalize_address_key("United Umina", "307-313 Ocean Beach Road", "2257")
    key_b = normalize_address_key("united umina", "307-313 ocean beach road", "2257")
    assert key_a == key_b


@pytest.mark.unit
def test_normalize_address_key_excludes_name_from_key() -> None:
    key_a = normalize_address_key("United Petroleum Umina", "307-313 Ocean Beach Road", "2257")
    key_b = normalize_address_key("A Totally Different Name", "307-313 Ocean Beach Road", "2257")
    assert key_a == key_b


@pytest.mark.unit
def test_normalize_address_key_differs_for_different_addresses() -> None:
    key_a = normalize_address_key("Station A", "1 Main Street", "2000")
    key_b = normalize_address_key("Station A", "2 Main Street", "2000")
    assert key_a != key_b


@pytest.mark.unit
def test_names_agree_exact_and_substring() -> None:
    assert names_agree("United Petroleum Umina", "United Petroleum Umina")
    assert names_agree("United", "United Petroleum Umina")
    assert not names_agree("United", "Shell Coles Express")


@pytest.mark.unit
def test_names_agree_handles_empty() -> None:
    assert not names_agree(None, "United")
    assert not names_agree("United", "")
