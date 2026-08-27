param(
    [string]$PromtoolPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repoRoot "rag_llm_server"
$env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"
$env:PYTEST_ADDOPTS = "-p no:cacheprovider"

Push-Location $backendRoot
try {
    uv run pytest -q tests/observability
    if ($LASTEXITCODE -ne 0) {
        throw "Observability acceptance tests failed"
    }
    uv run python scripts/verify_observability_config.py
    if ($LASTEXITCODE -ne 0) {
        throw "Observability configuration validation failed"
    }
}
finally {
    Pop-Location
}

if (-not $PromtoolPath) {
    $promtool = Get-Command promtool -ErrorAction SilentlyContinue
    if ($promtool) {
        $PromtoolPath = $promtool.Source
    }
}
if (-not $PromtoolPath -or -not (Test-Path -LiteralPath $PromtoolPath)) {
    throw "promtool is required; pass -PromtoolPath or add it to PATH"
}

& $PromtoolPath check rules `
    (Join-Path $repoRoot "observability/prometheus/recording-rules.yaml") `
    (Join-Path $repoRoot "observability/prometheus/alerts.yaml")
if ($LASTEXITCODE -ne 0) {
    throw "Prometheus rule validation failed"
}
& $PromtoolPath test rules `
    (Join-Path $repoRoot "observability/tests/test_rules.yml")
if ($LASTEXITCODE -ne 0) {
    throw "Prometheus synthetic rule tests failed"
}

Write-Output "Phase 4 observability verification passed"
