# 외부 헬스 프로브 — 무한대기 구간에서 서버 이벤트 루프 생존 여부를 타임스탬프로 기록한다.
# 브라우저의 30초 헬스체크는 백그라운드 탭 스로틀링 때문에 측정 도구로 부적합하므로
# 별도 창에서 이 스크립트를 상시 실행해 둔다. (PowerShell 5.1 호환)
#
# 사용: powershell -ExecutionPolicy Bypass -File scripts\health_probe.ps1
param(
    [string]$Url = "http://localhost:8000/api/v1/health",
    [int]$IntervalSec = 10,
    [string]$LogPath = "logs\health_probe.log"
)

$dir = Split-Path $LogPath
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }

Write-Host "[probe] $Url -> $LogPath ($IntervalSec s 주기, Ctrl+C로 종료)"
while ($true) {
    $t = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        $sw.Stop()
        Add-Content -Path $LogPath -Encoding UTF8 -Value "$t OK $($r.StatusCode) $($sw.ElapsedMilliseconds)ms"
    } catch {
        $sw.Stop()
        Add-Content -Path $LogPath -Encoding UTF8 -Value "$t FAIL $($sw.ElapsedMilliseconds)ms $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
