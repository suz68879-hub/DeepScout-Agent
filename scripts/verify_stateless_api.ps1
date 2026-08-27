param(
    [ValidateRange(1, 1000)]
    [int]$Rounds = 100,
    [string]$RedisUrl = "redis://127.0.0.1:6379/15"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "rag_llm_server"
$baseTemp = Join-Path $backendRoot (".pytest-tmp-p2t09-" + [guid]::NewGuid().ToString("N"))
$previousRunFlag = [Environment]::GetEnvironmentVariable("RUN_MULTI_REPLICA_TEST")
$previousRounds = [Environment]::GetEnvironmentVariable("MULTI_REPLICA_ROUNDS")
$previousRedisUrl = [Environment]::GetEnvironmentVariable("MULTI_REPLICA_REDIS_URL")
$previousUvCache = [Environment]::GetEnvironmentVariable("UV_CACHE_DIR")

Push-Location $backendRoot
try {
    $env:RUN_MULTI_REPLICA_TEST = "1"
    $env:MULTI_REPLICA_ROUNDS = $Rounds.ToString()
    $env:MULTI_REPLICA_REDIS_URL = $RedisUrl
    $env:UV_CACHE_DIR = ".uv-cache"

    & uv run pytest -q -p no:cacheprovider `
        tests/integration/test_multi_replica.py `
        --basetemp $baseTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 multi-replica acceptance failed"
    }
    Write-Host "Phase 2 multi-replica acceptance passed ($Rounds rounds)."
}
finally {
    $env:RUN_MULTI_REPLICA_TEST = $previousRunFlag
    $env:MULTI_REPLICA_ROUNDS = $previousRounds
    $env:MULTI_REPLICA_REDIS_URL = $previousRedisUrl
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
            -not (Split-Path -Leaf $resolvedTemp).StartsWith(".pytest-tmp-p2t09-")
        ) {
            throw "Refusing to remove unexpected acceptance temp directory"
        }
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
