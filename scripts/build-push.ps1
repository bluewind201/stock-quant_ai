<#
.SYNOPSIS
    PC 에서 멀티아키텍처 이미지를 빌드해 Docker Hub 에 올린다. NAS 는 pull 만 한다.

.DESCRIPTION
    NAS 에서 직접 빌드하면 npm/pip 때문에 느리고 메모리도 많이 먹는다.
    여기서 linux/amd64 + linux/arm64 를 함께 밀어두면 Docker 가 NAS CPU 에 맞는 것을 고른다.

    푸시 전에 항상 확인하는 것
      1) 테스트 통과
      2) 이미지 안에 .env / 계좌 DB / 인증 파일이 없는지

.EXAMPLE
    .\scripts\build-push.ps1 -Tag 2.2.0
    .\scripts\build-push.ps1 -Tag 2.2.0 -NoPush      # 로컬 빌드만 (푸시 전 확인용)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Tag,
    [string]$Repo = 'bluewind201/stock-quant',
    [string]$Platform = 'linux/amd64,linux/arm64',
    [switch]$NoPush,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Step($text) {
    Write-Host ''
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

<#
    PowerShell 5.1 은 네이티브 프로그램이 stderr 로 뱉은 줄을 ErrorRecord 로 감싼다.
    $ErrorActionPreference='Stop' 과 만나면 종료코드가 0 이어도 스크립트가 죽는다.
    docker buildx 는 진행 상황을 전부 stderr 로 보내므로 여기 걸린다.
    호출 동안만 Continue 로 낮추고 성공 여부는 $LASTEXITCODE 로만 판단한다.
#>
function Invoke-Native {
    param([Parameter(Mandatory)][string]$What,
          [Parameter(Mandatory)][string]$Exe,
          [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What 실패 (exit $LASTEXITCODE)" }
}

Step '사전 확인'
Invoke-Native '도커 연결' docker @('version', '--format', '{{.Server.Version}}')
Write-Host "저장소 : ${Repo}:${Tag} (+ latest)"
Write-Host "플랫폼 : $Platform"
if ($NoPush) { Write-Host '푸시   : 안 함 (로컬 빌드만)' -ForegroundColor Yellow }

if (-not $SkipTests) {
    Step '테스트'
    $python = Join-Path $root '.venv-stock\Scripts\python.exe'
    if (Test-Path -LiteralPath $python) {
        $env:PYTHONPATH = Join-Path $root 'core'
        $env:PYTHONUTF8 = '1'
        Invoke-Native '테스트' $python @('-m', 'pytest', 'core/tests', '-q')
    } else {
        Write-Host '.venv-stock 없음 — Docker(python:3.14-slim) 컨테이너에서 테스트를 실행합니다.' -ForegroundColor Yellow
        $mount = ($root -replace '\\', '/') + ':/work'
        Invoke-Native '컨테이너 테스트' docker @('run', '--rm', '-v', $mount, '-w', '/work', '-e', 'PYTHONPATH=/work/core', 'python:3.14-slim', 'sh', '-c', 'pip install -q --no-cache-dir --root-user-action=ignore -r core/requirements-stock.lock pytest httpx && pytest -o cache_dir=/tmp/pytest_cache core/tests -q')
    }
}

# 기본 docker 드라이버는 멀티플랫폼 빌드를 못 한다.
$builder = 'sqt-builder'
$ErrorActionPreference = 'Continue'
$builders = & docker buildx ls 2>$null | Out-String
$ErrorActionPreference = 'Stop'
if ($builders -notmatch [regex]::Escape($builder)) {
    Step "buildx 빌더 생성 ($builder)"
    Invoke-Native 'buildx create' docker @('buildx', 'create', '--name', $builder, '--driver', 'docker-container', '--use')
} else {
    Invoke-Native 'buildx use' docker @('buildx', 'use', $builder)
}

if (-not $NoPush) {
    Step 'Docker Hub 로그인 확인'
    $config = Join-Path $env:USERPROFILE '.docker\config.json'
    $loggedIn = $false
    if (Test-Path -LiteralPath $config) {
        try {
            $json = Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($json.auths -and ($json.auths.PSObject.Properties.Name -match 'docker\.io')) { $loggedIn = $true }
            elseif ($json.credsStore) {
                $credOut = & ("docker-credential-" + $json.credsStore) list 2>$null | Out-String
                if ($credOut -match 'docker\.io') { $loggedIn = $true }
            }
        } catch { $loggedIn = $false }
    }
    if ($loggedIn) { Write-Host '이미 로그인되어 있습니다.' }
    else {
        Write-Host 'docker login 이 필요합니다.' -ForegroundColor Yellow
        Invoke-Native 'docker login' docker @('login')
    }
}

Step "빌드: ${Repo}:${Tag}"
$buildArgs = @('buildx', 'build', '--platform', $Platform, '-t', "${Repo}:${Tag}", '-t', "${Repo}:latest")
if ($NoPush) {
    # docker-container 드라이버는 멀티플랫폼을 --load 로 꺼낼 수 없다.
    if ($Platform -like '*,*') { $buildArgs += '--output=type=cacheonly' } else { $buildArgs += '--load' }
} else {
    $buildArgs += '--push'
}
$buildArgs += '.'
Invoke-Native '이미지 빌드' docker $buildArgs

Step '푸시본 검사 — 시크릿이 들어가지 않았는지'
$probe = "${Repo}:${Tag}"
if ($NoPush -and $Platform -like '*,*') {
    Write-Host '멀티플랫폼 -NoPush 는 로컬에 이미지를 남기지 않아 검사를 건너뜁니다.' -ForegroundColor Yellow
} else {
    Invoke-Native '검사용 pull' docker @('pull', '--platform', 'linux/amd64', '-q', $probe)
    $ErrorActionPreference = 'Continue'
    $leaked = & docker run --rm --entrypoint sh $probe -c "find / -xdev \( -name '.env*' -o -name '*.sqlite3' -o -name 'auth.json' \) -not -path '*/site-packages/*' 2>/dev/null | head -20"
    $ErrorActionPreference = 'Stop'
    if ($leaked) {
        Write-Host '이미지 안에서 다음 파일이 발견됐습니다:' -ForegroundColor Red
        $leaked | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        throw '시크릿으로 보이는 파일이 이미지에 포함됐습니다. 확인 후 다시 푸시하세요.'
    }
    Write-Host '.env / DB / 인증 파일 없음 — 정상' -ForegroundColor Green
}

Step '완료'
Write-Host "  ${Repo}:${Tag}" -ForegroundColor Green
Write-Host "  ${Repo}:latest" -ForegroundColor Green
if (-not $NoPush) {
    Write-Host ''
    Write-Host 'NAS 에서 실행할 명령:' -ForegroundColor Cyan
    Write-Host '  cd /volume1/docker/stock-quant'
    Write-Host '  docker compose pull'
    Write-Host '  docker compose up -d --force-recreate'
}
