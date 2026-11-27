# TradingAgents 每日 ETF 跑批脚本（Windows PowerShell 7+）
# 用法: pwsh -File daily_run.ps1 [-Date yyyy-MM-dd] [-Tickers 513750.SS,159949.SZ,...]
#   -Tickers 不传则跑完整 33 只；传了则只跑指定的代码（逗号分隔）
# 依赖: .venv 已建好、.env 已配置、WorkBuddy 本地网关已运行
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [string]$RunDir = ".workbuddy\daily_run",
    [string[]]$Tickers = @()
)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$statusFile = Join-Path $RunDir "status_$Date.txt"
$analysts = "market,news,fundamentals"

$fullTickers = @(
    "516020.SS","512400.SS","588710.SS","588160.SS","513750.SS","513090.SS",
    "512880.SS","516860.SS","588830.SS","159840.SZ","159857.SZ","159611.SZ",
    "516630.SS","159326.SZ","563230.SS","517520.SS","159707.SZ","159208.SZ",
    "159949.SZ","512690.SS","513970.SS","512100.SS","510500.SS","510300.SS",
    "512710.SS","159928.SZ","159570.SZ","159688.SZ","588200.SS","159732.SZ",
    "159559.SZ","512980.SS","159869.SZ"
)
# 指定 -Tickers 时只跑指定池；否则跑完整 33 只
# 兼容两种传法：-Tickers a,b,c（pwsh -File 传逗号串） 与 -Tickers a b c（逗号/空格均可）
$normalized = @()
foreach ($item in $Tickers) {
    if ($null -eq $item) { continue }
    foreach ($part in ([string]$item).Split(',')) {
        $code = $part.Trim()
        if ($code) { $normalized += $code }
    }
}
$targets = if ($normalized.Count -gt 0) { $normalized } else { $fullTickers }

Write-Host ">>> BATCH START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 分析日 $Date 共 $($targets.Count) 只"

$targets | ForEach-Object -Parallel {
    $t = $_
    $date = $using:Date
    $runDir = $using:RunDir
    $stamp = $using:stamp
    $statusFile = $using:statusFile
    $analysts = $using:analysts
    $logFile = Join-Path $runDir "${t}_${stamp}.log"
    cmd /c "run.bat $t $date $analysts" *> $logFile
    $exit = $LASTEXITCODE
    Add-Content -Path $statusFile -Value "$t exit=$exit $(Get-Date -Format 'yyyy-MM-dd_HH:mm:ss')"
    Write-Host "[$t] exit=$exit $(Get-Date -Format 'HH:mm:ss')"
} -ThrottleLimit 4

$ok = (Select-String -Path $statusFile -Pattern "exit=0").Count
$fail = (Select-String -Path $statusFile -Pattern "exit=1").Count
Write-Host ">>> BATCH DONE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 成功 $ok 失败 $fail"
Write-Host ">>> 状态文件: $statusFile"
