"""Parsing and unit normalisation of figures as they are written in real reports.

Handles "1,234,567", "1 234 567", "1.234.567", "12.5", "3,5", "3.4 million", and the units reports actually
use: tCO2e / ktCO2e / MtCO2e, kWh / MWh / GWh / TWh / GJ / TJ, m3 / megalitres, percentages.
Values are verified in the unit written in the report, then converted to one canonical unit per indicator.
"""

from __future__ import annotations

import re
import unicodedata

_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "mn": 1e6, "bn": 1e9}

NUMBER_WITH_UNIT = re.compile(
    r"(?<![\w.,])("
    r"\d{1,3}(?:[ ,.\u202f\u00a0]\d{3})+(?:[.,]\d+)?"  # with thousands separators
    r"|\d+(?:[.,]\d+)?"  # plain
    r")\s*(thousand|million|billion|mn|bn)?\s*"
    r"(%|percent|"
    r"[kKmM]?t\s?co2[- ]?e(?:q)?|[kKmM]?t\s?co2|"
    r"(?:kilo|mega)?tonnes?(?: of)? co2(?:[- ]?e(?:q)?| equivalent)?|"
    r"[kKmMgGtT]wh|[gGtT]j|m3|megalitres?|megaliters?|ml)?",
    re.IGNORECASE,
)

# canonical unit -> {unit as written (normalised) -> factor to canonical}
UNIT_FAMILIES: dict[str, dict[str, float]] = {
    "tCO2e": {"tCO2e": 1.0, "ktCO2e": 1e3, "MtCO2e": 1e6},
    "MWh": {"MWh": 1.0, "kWh": 1e-3, "GWh": 1e3, "TWh": 1e6, "GJ": 1 / 3.6, "TJ": 1000 / 3.6},
    "m3": {"m3": 1.0, "ML": 1e3},
    "%": {"%": 1.0},
}


def _canon_unit(raw: str) -> str | None:
    u = re.sub(r"\s+", " ", raw.strip().lower())
    if u in {"%", "percent"}:
        return "%"
    if "co2" in u:
        if u.startswith(("mt", "mega")):
            return "MtCO2e"
        if u.startswith(("kt", "kilo")):
            return "ktCO2e"
        return "tCO2e"
    energy = {"kwh": "kWh", "mwh": "MWh", "gwh": "GWh", "twh": "TWh", "gj": "GJ", "tj": "TJ"}
    if u in energy:
        return energy[u]
    if u == "m3":
        return "m3"
    if u.startswith("megalit") or u == "ml":
        return "ML"
    return raw.strip()


def normalise_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    return _canon_unit(unicodedata.normalize("NFKC", unit))


def same_family(unit: str | None, canonical: str | None) -> bool:
    return bool(unit and canonical and unit in UNIT_FAMILIES.get(canonical, {canonical: 1.0}))


def to_canonical(value: float, unit: str | None, canonical: str | None) -> tuple[float, str | None]:
    if unit and canonical and unit in UNIT_FAMILIES.get(canonical, {}):
        return value * UNIT_FAMILIES[canonical][unit], canonical
    return value, unit


def _to_float(raw: str) -> float:
    raw = re.sub(r"[\u202f\u00a0]", " ", raw)
    if " " in raw:  # 1 234 567,5 (French) or 1 234 567.5
        return float(raw.replace(" ", "").replace(",", "."))
    if raw.count(".") > 1 or (raw.count(".") == 1 and "," in raw and raw.index(".") < raw.index(",")):
        return float(raw.replace(".", "").replace(",", "."))  # 1.234.567 or 1.234,5 (European)
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        return float(raw.replace(",", ""))  # 1,234,567.5 (English)
    return float(raw.replace(",", "."))  # 12.5 / 3,5


def parse_value(text: str | None) -> tuple[float | None, str | None]:
    """Return (value, unit as written) from a figure such as "1,234 tCO2e" or "3.4 million MWh"."""
    if not text:
        return None, None
    text = unicodedata.normalize("NFKC", text)  # CO₂ -> CO2, m³ -> m3, narrow spaces
    m = NUMBER_WITH_UNIT.search(text)
    if not m:
        return None, None
    value = _to_float(m.group(1))
    if m.group(2):
        value *= _SCALE[m.group(2).lower()]
    return value, normalise_unit(m.group(3))


def digits_signature(value: float) -> str:
    # Mantissa digits, so 1,200,000 and "1.2 million" share the signature "12".
    mantissa = f"{abs(value):.6e}".split("e")[0].replace(".", "").rstrip("0")
    return (mantissa or "0")[:4]
