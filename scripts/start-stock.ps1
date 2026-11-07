param([ValidateRange(1,65535)][int]$Port = 8430, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$address = "http://127.0.0.1:$Port"
$existing = $null
try { $existing = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 2 }
catch { $existing = $null }
if ($existing -and $existing.ok -and $existing.mode -eq 'historical_paper') {
    Write-Host "Stock Quant가 이미 실행 중입니다: $address"
    if ($OpenBrowser) { Start-Process $address }
    return
}
$configPath = Join-Path $projectRoot '.env'
# 예전 설치본은 .env.stock 을 쓴다. docker compose 는 .env 만 자동으로 읽으므로
# 새 이름이 표준이고, 이 줄은 옮기기 전 설치본을 위한 호환 처리다.
if (-not (Test-Path -LiteralPath $configPath)) {
    $legacy = Join-Path $projectRoot '.env.stock'
    if (Test-Path -LiteralPath $legacy) { $configPath = $legacy }
}
if (Test-Path -LiteralPath $configPath) {
    foreach ($line in Get-Content -LiteralPath $configPath -Encoding UTF8) {
        if ($line -match '^\s*([A-Z_]+)\s*=(.*)$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            if ($key -in @('ADMIN_PASSWORD','SESSION_SECRET','COOKIE_SECURE','STOCK_BROKER','TOSS_CLIENT_ID','TOSS_CLIENT_SECRET','KIS_ENV','KIS_APP_KEY','KIS_APP_SECRET','NEWS_AUTO_REFRESH','SQT_DB_PATH','SQT_CODEX_EXECUTABLE','SQT_AI_MODEL')) {
                [Environment]::SetEnvironmentVariable($key, $value, 'Process')
            }
        }
    }
}
if (-not $env:ADMIN_PASSWORD) {
    $securePassword = Read-Host '훈련 화면 관리자 비밀번호 (현재 실행에만 사용)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try { $env:ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
$env:PYTHONPATH = Join-Path $projectRoot 'core'
$env:PYTHONUTF8 = '1'
$env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-stock-cache'
$stockPython = Join-Path $projectRoot '.venv-stock\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $stockPython)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv .venv-stock --python 3.14
        if ($LASTEXITCODE -ne 0) { throw 'Python 가상환경 생성 실패' }
        uv pip install --python $stockPython -r core/requirements-stock.lock
    } else {
        python -m venv .venv-stock
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.14 또는 uv를 설치하세요.' }
        & $stockPython -m pip install -r core/requirements-stock.lock
    }
    if ($LASTEXITCODE -ne 0) { throw '의존성 설치 실패' }
}
# An interrupted first install can leave python.exe present without its packages.
& $stockPython -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ['uvicorn','fastapi','requests','exchange_calendars','bs4','feedparser','icalendar']) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '필수 패키지 설치를 완료합니다.'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python $stockPython -r core/requirements-stock.lock
    } else {
        & $stockPython -m ensurepip --upgrade
        & $stockPython -m pip install -r core/requirements-stock.lock
    }
    if ($LASTEXITCODE -ne 0) { throw '필수 패키지 설치 실패. 위 오류를 확인하세요.' }
}
$browserJob = $null
if ($OpenBrowser) {
    $browserJob = Start-Job -ArgumentList $address -ScriptBlock {
        param($url)
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1
                if ($health.ok -and $health.mode -eq 'historical_paper') { Start-Process $url; return }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
    }
}
Write-Host "Stock Quant: $address (AI 모의 운용·가상 주문)"
Write-Host '이 창을 닫으면 서버가 종료됩니다. 종료: Ctrl+C'
try {
    & $stockPython -m uvicorn sqt.api:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw "서버 실행 실패 (종료 코드 $LASTEXITCODE). 위 오류를 확인하세요." }
} finally {
    if ($browserJob) { Stop-Job $browserJob -ErrorAction SilentlyContinue; Remove-Job $browserJob -Force -ErrorAction SilentlyContinue }
}
