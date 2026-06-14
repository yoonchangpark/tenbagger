# aiva 개발 자동화 런처
# - 백그라운드로 원격(tenbagger/aiva-clean)을 주기적으로 자동 pull
# - uvicorn --reload 로 코드(.py) 변경 시 서버 자동 재시작
# 결과: 코드가 푸시되면 로컬이 알아서 받아오고 서버가 알아서 재시작된다.
#       사용자는 브라우저 새로고침만 하면 됨. (dashboard.html은 매 요청마다 새로 읽힘)
#
# 사용법: 이 폴더에서  dev.bat  더블클릭  (또는 PowerShell에서  .\dev.ps1)
# 종료:   해당 창에서 Ctrl + C

param(
    [string]$Remote = "tenbagger",   # 로컬 원격 이름 (다르면 -Remote 로 지정)
    [string]$Branch = "aiva-clean",
    [int]$PullInterval = 20,         # 자동 pull 주기(초)
    [switch]$NoPull                  # 자동 pull 끄고 reload만 쓰고 싶을 때
)

Set-Location $PSScriptRoot
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " aiva 개발 서버 (auto-reload" -NoNewline -ForegroundColor Cyan
if (-not $NoPull) { Write-Host " + auto-pull $Remote/$Branch ${PullInterval}s" -NoNewline -ForegroundColor Cyan }
Write-Host ")" -ForegroundColor Cyan
Write-Host " http://localhost:8000   (종료: Ctrl+C)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

$pullJob = $null
if (-not $NoPull) {
    # 백그라운드 잡: 원격에 새 커밋이 있으면 ff-only 로만 당겨온다(로컬 충돌 시 안전하게 스킵)
    $pullJob = Start-Job -ScriptBlock {
        param($dir, $remote, $branch, $interval)
        Set-Location $dir
        while ($true) {
            git fetch $remote $branch 2>&1 | Out-Null
            $local  = (git rev-parse HEAD 2>$null)
            $remref = (git rev-parse "$remote/$branch" 2>$null)
            if ($remref -and ($local -ne $remref)) {
                git merge --ff-only "$remote/$branch" 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Output "[auto-pull] 새 코드 반영 -> $remref (uvicorn 이 .py 변경을 감지해 재시작합니다)"
                } else {
                    Write-Output "[auto-pull] ff-only 실패: 로컬에 커밋 안 한 변경이 있을 수 있음 -> 이번 주기 스킵"
                }
            }
            Start-Sleep -Seconds $interval
        }
    } -ArgumentList $PSScriptRoot, $Remote, $Branch, $PullInterval
}

try {
    # uvicorn 기본 reload 감시 대상은 *.py. dashboard.html 은 매 요청 재로딩되어 재시작 불필요.
    py -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
}
finally {
    if ($pullJob) {
        Write-Host ""
        Write-Host "[aiva] 종료 - auto-pull 백그라운드 정리 중..." -ForegroundColor Yellow
        # 종료 직전, 그동안 쌓인 auto-pull 메시지를 한 번 출력
        Receive-Job $pullJob -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
        Stop-Job   $pullJob -ErrorAction SilentlyContinue
        Remove-Job $pullJob -ErrorAction SilentlyContinue
    }
}
