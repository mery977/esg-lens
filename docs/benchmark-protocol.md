# Benchmark protocol

The goal is a small but **trustworthy** benchmark: every gold value checked by a human against the
source PDF, rules written down before looking at any model output.

## 1. Selecting reports

- 10 to 20 public sustainability statements published under the CSRD (first wave: financial year 2024,
  published in 2025), or recent GRI reports where CSRD ones are not yet available.
- Mix sectors (energy, industry, banking, retail, tech) and countries; include at least a few French
  companies and at least one report where an indicator is not disclosed.
- Keep only the link to each PDF in `data/reports/SOURCES.md` (company, year, URL, date downloaded).
  Do not commit the PDFs: they belong to the companies.

## 2. Annotation rules (decide them once, apply them everywhere)

| Indicator | Rule |
|---|---|
| Reporting year | The most recent financial year of the report |
| Scope | The consolidated group total, not a segment or a site |
| Scope 2 | Market-based; if only location-based is reported, mark market-based as not disclosed |
| Scope 3 | Total of the categories reported (as stated by the company) |
| Energy | Total energy consumption, converted to MWh |
| Percentages | As reported, 0–100 |
| Not disclosed | `"value": null` — also when the figure exists only in a chart without a number |
| Restated figures | Use the figure for the current year as published in this report |

Write any rule you add here before annotating further.

## 3. Annotation workflow

1. `esg-lens annotate data/reports/<file>.pdf --out data/gold/csrd.jsonl` writes draft lines
   (`"reviewed": false`) with the page and quote the system found.
2. Open the PDF at that page. Check the value, unit, year and page. Search the PDF yourself for the
   indicator when the draft says "not disclosed".
3. Fix the line, delete the `draft_*` fields, set `"reviewed": true`.
4. Drafts never count in the evaluation, so model suggestions cannot leak into the gold set unchecked.

To limit bias, annotate with the offline provider (`ESG_PROVIDER=fake`) so no benchmarked model
produces the drafts, and re-check 20 % of lines a few days later (report the agreement rate).

## 4. Running

```bash
cp bench/pricing.example.json bench/pricing.json   # fill current prices for your exact models
esg-lens bench data/gold/csrd.jsonl --providers openai,azure,gemini,anthropic --out bench
```

Report the model names and versions, the date of the run, and `bench/LEADERBOARD.md`.

## 5. Known limitations (state them when you publish)

- Small sample: differences of a few points between models are not significant.
- One annotator (unless you can get a second one).
- Text extraction with `pypdf`: figures that only exist in tables or images can be missed by every
  model; note these cases separately.
