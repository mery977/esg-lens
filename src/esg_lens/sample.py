"""Generates a synthetic sustainability report for a FICTIONAL company.

It contains realistic traps (previous-year figures, location- vs market-based Scope 2, energy in GWh,
an undisclosed indicator) so the evaluation measures something meaningful. No real company data is used.
"""

from __future__ import annotations

from pathlib import Path

PAGES = [
    [
        "Northwind Materials (fictional company) - Sustainability Report 2025",
        "This report covers the consolidated activities of the group for the financial year 2025.",
        "It is prepared with reference to the ESRS and GRI standards. All figures are illustrative.",
    ],
    [
        "Climate change (ESRS E1)",
        "In 2025, our Scope 1 emissions were 48,250 tCO2e, compared with 52,100 tCO2e in 2024.",
        "Scope 2 location-based emissions reached 31,400 tCO2e in 2025.",
        "Scope 2 market-based emissions amounted to 12,900 tCO2e in 2025, thanks to renewable power contracts.",
        "Scope 3 emissions across the value chain totalled 1.2 million tCO2e in 2025.",
    ],
    [
        "Energy",
        "Total energy consumption within the group was 410 GWh in 2025 (2024: 432 GWh).",
        "The share of renewable energy in our electricity mix reached 64% in 2025.",
        "We aim to reach 90% renewable electricity by 2030.",
    ],
    [
        "Own workforce (ESRS S1)",
        "Women held 38% of management positions at the end of 2025, up from 35% in 2024.",
        "The group employed 12,400 people in 27 countries.",
        "Water-related risks are assessed at site level; detailed water figures will be published next year.",
    ],
]


def build_sample_report(path: str | Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Northwind Materials - Sustainability Report 2025 (fictional)")
    for lines in PAGES:
        y = 780
        c.setFont("Helvetica-Bold", 14)
        c.drawString(56, y, lines[0])
        c.setFont("Helvetica", 10.5)
        for line in lines[1:]:
            y -= 28
            c.drawString(56, y, line)
        c.showPage()
    c.save()
    return path
