.PHONY: install test lint demo eval api ui docker fetch real

install:   ## install with every provider and the dev tools
	pip install -e ".[all]"
test:
	pytest -q
lint:
	ruff check src tests ui && ruff format --check src tests ui
demo:      ## generate the fictional report and run the agents on it
	esg-lens demo
eval:      ## evaluation on the gold set (provider from ESG_PROVIDER)
	esg-lens eval data/gold/sample.jsonl --out eval_report.json
api:
	uvicorn esg_lens.api:app --reload
ui:
	streamlit run ui/app.py
docker:
	docker build -t esg-lens . && docker run --rm -p 8080:8080 --env-file .env esg-lens
fetch:     ## download the public reports of data/reports/catalog.json
	esg-lens fetch
real:      ## run the agents on every downloaded report (provider from ESG_PROVIDER)
	esg-lens run --all
