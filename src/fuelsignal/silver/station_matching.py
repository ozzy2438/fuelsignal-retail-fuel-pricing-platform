"""Station identity matching between the FuelCheck live reference API and bulk history.

The official FuelCheck bulk price-history archive (CKAN monthly downloads) has never
carried an official station code or coordinates - only free-text name/address/suburb/
postcode/brand. The live FuelCheck reference API (FuelCheckRefData/v2/fuel/lovs) carries
an official station code and coordinates, but is not keyed the same way.

Because one side of the join has no official code at all, the only viable deterministic
join key is a normalized address, which this module builds and parses.
"""

import re
from typing import NamedTuple

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]")

# Primary pattern: "<street>, <suburb> NSW <postcode>" (covers ~99% of live records).
_ADDRESS_PRIMARY_RE = re.compile(
    r"^(?P<street>.*),\s*(?P<suburb>[A-Za-z' \-]+?)\s+(?P<state>NSW|ACT)\s+(?P<postcode>\d{4})$"
)
# Fallback: no comma before the suburb, or "NEW SOUTH WALES" spelled out.
_ADDRESS_FALLBACK_RE = re.compile(
    r"^(?P<street>.*?)\s+(?P<suburb>[A-Za-z' \-]+?)\s+"
    r"(?:NSW|NEW SOUTH WALES)(?:\s+AUSTRALIA)?\s+(?P<postcode>\d{4})$"
)


class ParsedAddress(NamedTuple):
    street: str | None
    suburb: str | None
    postcode: str | None


def parse_nsw_address(address: str | None) -> ParsedAddress:
    """Parse a free-text FuelCheck address into street/suburb/postcode.

    Returns all-None fields when the address does not match a known pattern
    rather than guessing - callers should treat this as "unparsed", not an error.
    """
    if not address:
        return ParsedAddress(None, None, None)

    text = address.strip()
    for pattern in (_ADDRESS_PRIMARY_RE, _ADDRESS_FALLBACK_RE):
        match = pattern.match(text)
        if match:
            return ParsedAddress(
                street=match.group("street").strip().rstrip(",").strip() or None,
                suburb=_WHITESPACE_RE.sub(" ", match.group("suburb")).strip().upper() or None,
                postcode=match.group("postcode"),
            )
    return ParsedAddress(None, None, None)


def normalize_text(value: str | None) -> str:
    """Uppercase, strip punctuation, and collapse whitespace for matching keys."""
    if not value:
        return ""
    upper = value.strip().upper()
    no_punct = _NON_ALNUM_RE.sub(" ", upper)
    return _WHITESPACE_RE.sub(" ", no_punct).strip()


def normalize_address_key(name: str | None, address: str | None, postcode: str | None) -> str:
    """Build a deterministic normalized matching key for station crosswalk.

    Uses address + postcode as the primary signal (street text is far more stable
    across sources than station display names, which vary by brand rebrand or
    abbreviation). Station name is intentionally excluded from the key itself -
    it is used separately as a match-confidence signal, not as part of the key,
    since brand names get reformatted between the bulk archive and the live API.
    """
    normalized_address = normalize_text(address)
    normalized_postcode = normalize_text(postcode)
    return f"{normalized_address}|{normalized_postcode}"


def names_agree(name_a: str | None, name_b: str | None) -> bool:
    """Check whether two station names are the same or one contains the other."""
    norm_a = normalize_text(name_a)
    norm_b = normalize_text(name_b)
    if not norm_a or not norm_b:
        return False
    return norm_a == norm_b or norm_a in norm_b or norm_b in norm_a
