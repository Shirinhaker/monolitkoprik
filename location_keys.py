"""Canonical, safe administrative district keys.

The human-facing ``users.district`` value is deliberately kept as entered or
returned by geocoding.  This module produces the separate, comparison-safe
``district_key`` used for location matching.
"""

import re
import unicodedata

from district_catalog import DISTRICT_NAMES


APOSTROPHES = ("'", "’", "‘", "ʻ", "ʼ", "`", "´")
_DISTRICT_SUFFIX = re.compile(
    r"(?:\s+)(?:tumani|tuman|t\.|district|rayoni|rayon|shahri|shahar|city)$",
    re.IGNORECASE,
)
_ADMIN_CENTER_ALIAS = re.compile(r"\s*\([^()]*\)\s*$")
_ADMIN_CENTER_VALUE = re.compile(r"\(([^()]*)\)\s*$")
_PLACEHOLDERS = {
    "joylashuvim",
    "joylashuv",
    "joriy manzilim",
    "manzilim",
    "manzil",
    "address",
    "location",
    "current location",
    "unknown",
    "nomalum",
    "aniq manzil topilmadi",
}
_ADDRESS_MARKERS = ("kocha", "kochasi", "street", "avenue", "uy", "house")


def _display_text(value):
    return " ".join(str(value or "").strip().split())


def _normalized_candidate(value):
    display = _display_text(value)
    if not display or "," in display or any(char.isdigit() for char in display):
        return ""
    text = unicodedata.normalize("NFKC", display).casefold()
    for mark in APOSTROPHES:
        text = text.replace(mark, "")
    text = " ".join(text.split())
    if text in _PLACEHOLDERS:
        return ""
    if any(re.search(r"(?:^|\s)" + marker + r"(?:\s|$)", text) for marker in _ADDRESS_MARKERS):
        return ""
    text = _ADMIN_CENTER_ALIAS.sub("", text).strip()
    text = _DISTRICT_SUFFIX.sub("", text).strip()
    if text in _PLACEHOLDERS:
        return ""
    return text


def _build_district_aliases():
    """Build the allowlist from the backend-owned structured catalog."""
    aliases = {}
    for display in DISTRICT_NAMES:
        key = _normalized_candidate(display)
        if not key:
            continue
        aliases[key] = key
        center = _ADMIN_CENTER_VALUE.search(display)
        if center:
            center_key = _normalized_candidate(center.group(1))
            if center_key:
                aliases[center_key] = key
    return aliases


_DISTRICT_ALIASES = _build_district_aliases()


def canonical_district_key(value):
    """Return a catalog-backed district key, or ``''`` for non-district input.

    Commas, numbers, street markers, placeholders, and names absent from the
    platform's district selector are rejected.  Parenthetical administrative
    centres (for example Gazalkent) resolve to their owning district key.
    """
    return _DISTRICT_ALIASES.get(_normalized_candidate(value), "")


def safe_district_display(value):
    """Preserve a valid display district; drop placeholders and addresses."""
    display = _display_text(value)
    return display if canonical_district_key(display) else ""
