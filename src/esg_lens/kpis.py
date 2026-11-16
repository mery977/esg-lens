"""Catalogue of ESG indicators, aligned with common CSRD / ESRS and GRI disclosures.

Add an indicator here and the whole pipeline (retrieval, extraction, verification, evaluation) picks it up.
"""

from __future__ import annotations

from .schemas import KpiSpec

KPI_CATALOGUE: list[KpiSpec] = [
    KpiSpec(
        key="scope1_emissions",
        name="Scope 1 GHG emissions",
        unit_hint="tCO2e",
        queries=["Scope 1 greenhouse gas emissions", "direct GHG emissions tCO2e"],
        keywords=["scope 1", "scope-1", "scope i "],
    ),
    KpiSpec(
        key="scope2_emissions",
        name="Scope 2 GHG emissions (market-based)",
        unit_hint="tCO2e",
        queries=["Scope 2 market-based emissions", "indirect emissions purchased electricity"],
        keywords=["scope 2", "scope-2", "scope ii "],
        avoid_terms=["location-based", "location based"],
    ),
    KpiSpec(
        key="scope3_emissions",
        name="Scope 3 GHG emissions",
        unit_hint="tCO2e",
        queries=["Scope 3 value chain emissions", "other indirect GHG emissions"],
        keywords=["scope 3", "scope-3", "scope iii "],
    ),
    KpiSpec(
        key="energy_consumption",
        name="Total energy consumption",
        unit_hint="MWh",
        queries=["total energy consumption MWh", "energy consumed within the organisation"],
        keywords=[
            "energy consumption",
            "energy consumed",
            "energy use",
            "consumption of energy",
            "energy usage",
        ],
    ),
    KpiSpec(
        key="renewable_share",
        name="Share of renewable energy",
        unit_hint="%",
        queries=["share of renewable energy", "renewable electricity percentage"],
        keywords=["renewable"],
    ),
    KpiSpec(
        key="water_withdrawal",
        name="Water withdrawal",
        unit_hint="m3",
        queries=["total water withdrawal", "water consumption m3"],
        keywords=["water"],
    ),
    KpiSpec(
        key="women_in_management",
        name="Women in management positions",
        unit_hint="%",
        queries=["women in management positions", "female managers percentage gender diversity"],
        keywords=["women", "female"],
    ),
]

KPI_BY_KEY = {k.key: k for k in KPI_CATALOGUE}
