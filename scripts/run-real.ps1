<#
  Test ESG Lens on real reports, on Windows, in one command:

      .\scripts\run-real.ps1                     # Gemini (default)
      .\scripts\run-real.ps1 -Provider anthropic # Claude (API key from console.anthropic.com)
      .\scripts\run-real.ps1 -Provider openai    # OpenAI (API key from platform.openai.com)
      .\scripts\run-real.ps1 -Provider azure     # Azure OpenAI (after `az login`)
      .\scripts\run-real.ps1 -Report metso-2025.pdf   # one report only (fastest first test)

  It downloads the reports of data\reports\catalog.json, then indexes and extracts each of them.
  Results: results\<provider>\<report>.csv (opens in Excel), .json, and SUMMARY.md.
  Your API key is asked once, hidden, kept only in this terminal session, never written to disk.
#>
param(
    [ValidateSet("gemini", "anthropic", "openai", "azure")] [string] $Provider = "gemini",
    [string] $Model = "",
    [string] $Report = ""   # e.g. -Report metso-2025.pdf : test one report only
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $env:VIRTUAL_ENV) {
    if (Test-Path ".venv\Scripts\Activate.ps1") { . .venv\Scripts\Activate.ps1 }
    else { throw "No .venv found: create it first (see README, Quick start)." }
}
if (Test-Path ".env") {
    Write-Warning ".env found (copied from .env.example?): its placeholder values would break the test. Renamed to .env.bak."
    Move-Item .env .env.bak -Force
}

function Read-Key([string] $name, [string] $url) {
    # Pasting into a hidden prompt fails in some terminals (Ctrl+V arrives as one control character),
    # so the simplest path is the clipboard: copy the key, press Enter.
    for ($try = 1; $try -le 3; $try++) {
        Write-Host "`n$name API key ($url)" -ForegroundColor Cyan
        Write-Host "Copy the key, then just press Enter here (or paste it, then Enter)."
        $secure = Read-Host "Key" -AsSecureString
        $typed = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        $key = if ($typed -and $typed.Trim().Length -ge 20) { $typed } else { (Get-Clipboard -Raw) }
        $key = ("$key" -replace '[\x00-\x20]', '')   # spaces, line breaks, control characters
        if ($key.Length -ge 20) {
            Write-Host ("Key received: " + $key.Substring(0, 4) + "..." + $key.Substring($key.Length - 4) + " (" + $key.Length + " characters)") -ForegroundColor Green
            return $key
        }
        Write-Warning "No valid key found (got $($key.Length) characters). Copy the whole key and try again."
    }
    throw "No API key provided."
}

# A key left in this terminal by an earlier failed attempt (e.g. one character) is discarded.
foreach ($k in "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY") {
    $v = [Environment]::GetEnvironmentVariable($k)
    if ($v -and $v.Trim().Length -lt 20) { Remove-Item "Env:$k" }
}

$env:ESG_PROVIDER = $Provider
$env:ESG_DATA_DIR = ".esg_$Provider"
switch ($Provider) {
    "gemini" {
        if (-not ($env:GEMINI_API_KEY -or $env:GOOGLE_API_KEY)) { $env:GEMINI_API_KEY = Read-Key "Gemini" "aistudio.google.com" }
        if ($Model) { $env:ESG_GEMINI_MODEL = $Model }
        Remove-Item Env:ESG_GCP_PROJECT -ErrorAction SilentlyContinue   # API key mode, not Vertex AI
        # Free tier: about 100 embedded texts and a few model calls per minute. Stay just under, so the
        # run never hits the limit (raise these if your key has billing enabled).
        if (-not $env:ESG_EMBED_PER_MINUTE) { $env:ESG_EMBED_PER_MINUTE = "90" }
        if (-not $env:ESG_REQUESTS_PER_MINUTE) { $env:ESG_REQUESTS_PER_MINUTE = "8" }
    }
    "anthropic" {
        if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = Read-Key "Anthropic" "console.anthropic.com" }
        if ($Model) { $env:ESG_ANTHROPIC_MODEL = $Model }
    }
    "openai" {
        if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = Read-Key "OpenAI" "platform.openai.com/api-keys" }
        if ($Model) { $env:ESG_OPENAI_MODEL = $Model }
    }
    "azure" {
        if (-not $env:ESG_AZURE_ENDPOINT) { $env:ESG_AZURE_ENDPOINT = Read-Host "Azure OpenAI endpoint (https://<resource>.openai.azure.com)" }
        if ($Model) { $env:ESG_AZURE_CHAT_DEPLOYMENT = $Model }
    }
}

pip install -q -e ".[$Provider]"
Write-Host "`n== 1/3 Checking the connection to $Provider ==" -ForegroundColor Cyan
esg-lens check
if ($LASTEXITCODE -ne 0) {
    # A bad key stays in this terminal session: forget it so that the next run asks again.
    Remove-Item Env:GEMINI_API_KEY, Env:GOOGLE_API_KEY, Env:ANTHROPIC_API_KEY, Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    Write-Host "`nStopped before indexing anything. Fix the problem above, then run .\scripts\run-real.ps1 again." -ForegroundColor Red
    exit 1
}
Write-Host "`n== 2/3 Downloading the reports ==" -ForegroundColor Cyan
esg-lens fetch
Write-Host "`n== 3/3 Indexing and extracting with $Provider ==" -ForegroundColor Cyan
Write-Host "Smallest report first. On a free key this takes 5 to 10 minutes per report: progress is shown, let it run."
if ($Report) { esg-lens run (Join-Path "data\reports" $Report) } else { esg-lens run --all }
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nSome reports failed (see FAILED above). Send the messages above to get them fixed." -ForegroundColor Yellow
    exit 1
}
Write-Host "`nDone. Open results\$Provider\ (the .csv files open in Excel), and check each value on the cited page." -ForegroundColor Green
