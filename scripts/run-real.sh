#!/usr/bin/env bash
# Same as run-real.ps1, for macOS / Linux / Git Bash:  ./scripts/run-real.sh [gemini|anthropic|openai|azure]
set -euo pipefail
cd "$(dirname "$0")/.."
PROVIDER="${1:-gemini}"
export ESG_PROVIDER="$PROVIDER" ESG_DATA_DIR=".esg_$PROVIDER"
case "$PROVIDER" in
  gemini)    [ -n "${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}" ] || { read -rsp "Gemini API key: " GEMINI_API_KEY; echo; export GEMINI_API_KEY; }
             unset ESG_GCP_PROJECT
             export ESG_EMBED_PER_MINUTE="${ESG_EMBED_PER_MINUTE:-90}" ESG_REQUESTS_PER_MINUTE="${ESG_REQUESTS_PER_MINUTE:-8}" ;;
  anthropic) [ -n "${ANTHROPIC_API_KEY:-}" ] || { read -rsp "Anthropic API key: " ANTHROPIC_API_KEY; echo; export ANTHROPIC_API_KEY; } ;;
  openai)    [ -n "${OPENAI_API_KEY:-}" ] || { read -rsp "OpenAI API key: " OPENAI_API_KEY; echo; export OPENAI_API_KEY; } ;;
  azure)     [ -n "${ESG_AZURE_ENDPOINT:-}" ] || { read -rp "Azure OpenAI endpoint: " ESG_AZURE_ENDPOINT; export ESG_AZURE_ENDPOINT; } ;;
  *) echo "provider must be gemini, anthropic, openai or azure"; exit 1 ;;
esac
pip install -q -e ".[$PROVIDER]"
esg-lens fetch
esg-lens run --all
echo "Done: see results/$PROVIDER/"
