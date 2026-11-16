<#
  Start the ESG Lens interface on Windows, in one command:   .\scripts\ui.ps1
  Starts the API (port 8000) in the background, then the Streamlit app (opens your browser).
  Close with Ctrl+C: the API is stopped too.

  The "Real reports" tab shows the saved runs of results\ and needs no API key.
  To analyse new reports live with a model:  .\scripts\ui.ps1 -Provider gemini   (or anthropic, openai)
#>
param([ValidateSet("fake", "gemini", "anthropic", "openai", "azure")] [string] $Provider = "fake")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:VIRTUAL_ENV) { . .venv\Scripts\Activate.ps1 }

$env:ESG_PROVIDER = $Provider
$env:ESG_DATA_DIR = if ($Provider -eq "fake") { ".esg_lens" } else { ".esg_$Provider" }
pip install -q -e ".[ui]"

$api = Start-Process -FilePath "uvicorn" -ArgumentList "esg_lens.api:app", "--port", "8000" -PassThru -NoNewWindow
try {
    Start-Sleep -Seconds 3
    streamlit run ui/app.py
} finally {
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
