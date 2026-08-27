[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "rag_llm_server"
$baseTemp = Join-Path $backendRoot (
    ".pytest-tmp-p3t11-" + [guid]::NewGuid().ToString("N")
)
$previousAppEnv = [Environment]::GetEnvironmentVariable("APP_ENV")
$previousBackend = [Environment]::GetEnvironmentVariable("STORAGE_BACKEND")
$previousUvCache = [Environment]::GetEnvironmentVariable("UV_CACHE_DIR")

if ([string]::IsNullOrWhiteSpace($env:CELERY_BROKER_TEST_URL)) {
    throw "CELERY_BROKER_TEST_URL is required; use an isolated test RabbitMQ endpoint"
}

Push-Location $backendRoot
try {
    $env:APP_ENV = "test"
    $env:STORAGE_BACKEND = "postgres"
    $env:UV_CACHE_DIR = ".uv-cache"

    & uv run pytest -q -p no:cacheprovider `
        tests/resilience/test_job_delivery.py `
        --basetemp $baseTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 3 job resilience acceptance failed"
    }
    Write-Host "Phase 3 job resilience acceptance passed (7 scenarios x 10 injections)."
}
finally {
    $env:APP_ENV = $previousAppEnv
    $env:STORAGE_BACKEND = $previousBackend
    $env:UV_CACHE_DIR = $previousUvCache
    Pop-Location

    if (Test-Path -LiteralPath $baseTemp) {
        $resolvedBackend = (Resolve-Path -LiteralPath $backendRoot).Path
        $resolvedTemp = (Resolve-Path -LiteralPath $baseTemp).Path
        if (
            -not $resolvedTemp.StartsWith(
                $resolvedBackend + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not (Split-Path -Leaf $resolvedTemp).StartsWith(
                ".pytest-tmp-p3t11-"
            )
        ) {
            throw "Refusing to remove unexpected acceptance temp directory"
        }
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
