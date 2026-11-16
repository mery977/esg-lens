# Real reports

Put public sustainability reports (PDF) here. They are git-ignored: do not redistribute companies' PDFs,
link to them instead.

To evaluate on a real report, add lines to a gold file (e.g. `data/gold/real.jsonl`) with the values you
read yourself in the report, then run `esg-lens eval data/gold/real.jsonl` with each provider:

    {"pdf": "data/reports/<company>-2025.pdf", "kpi": "scope1_emissions", "value": 123456, "unit": "tCO2e", "page": 42, "tolerance_pct": 1}

Use `"value": null` when the report does not disclose the indicator.
