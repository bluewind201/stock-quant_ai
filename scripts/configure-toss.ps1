param([switch]$Start)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot '.env'

function Read-Secret([string]$Prompt) {
    $secureValue = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
function Set-ConfigValue([string]$Text, [string]$Key, [string]$Value) {
    # Single quotes also prevent Compose from expanding dollar signs in credentials.
    if ($Value -match "[\r\n']") { throw '입력값에 줄바꿈이나 작은따옴표를 사용할 수 없습니다.' }
    $replacement = $Key + "='" + $Value + "'"
    $pattern = '(?m)^\s*' + [regex]::Escape($Key) + '=.*$'
    if ([regex]::IsMatch($Text,$pattern)) {
        return [regex]::Replace($Text,$pattern,[System.Text.RegularExpressions.MatchEvaluator]{ param($match) $replacement })
    }
    return $Text.TrimEnd() + "`r`n" + $replacement + "`r`n"
}

Write-Host '토스증권 웹 → 설정 → Open API에서 발급한 키를 입력합니다.'
Write-Host '허용 IP 관리에는 이 PC/NAS에서 외부로 나갈 때 사용하는 공인 IP를 등록하세요.'
$clientId = (Read-Host 'client_id').Trim()
$clientSecret = (Read-Secret 'client_secret (화면에 표시되지 않음)').Trim()
if (-not $clientId -or -not $clientSecret) { throw '두 값 모두 필요합니다. 설정 파일을 변경하지 않았습니다.' }

if (Test-Path -LiteralPath $configPath) {
    $configText = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
} else {
    $configText = Get-Content -LiteralPath (Join-Path $projectRoot '.env.example') -Raw -Encoding UTF8
}
$configText = Set-ConfigValue $configText 'STOCK_BROKER' 'toss'
$configText = Set-ConfigValue $configText 'TOSS_CLIENT_ID' $clientId
$configText = Set-ConfigValue $configText 'TOSS_CLIENT_SECRET' $clientSecret
$adminMatch = [regex]::Match($configText,'(?m)^ADMIN_PASSWORD=(.*)$')
$adminValue = $adminMatch.Groups[1].Value.Trim().Trim('"').Trim("'")
if (-not $adminValue) {
    $adminPassword = Read-Secret '훈련 화면 관리자 비밀번호 (토스 로그인 비밀번호와 별개)'
    if (-not $adminPassword) { throw '관리자 비밀번호가 필요합니다. 설정 파일을 변경하지 않았습니다.' }
    $configText = Set-ConfigValue $configText 'ADMIN_PASSWORD' $adminPassword
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($configPath,$configText,$utf8)
$clientSecret = $null
$adminPassword = $null
Write-Host '토스 설정을 .env에 저장했습니다. 서버 재시작 후 데이터 · 전략 → 토스 연결 확인을 누르세요.'
if ($Start) { & (Join-Path $PSScriptRoot 'start-stock.ps1') }
