# PMT 자동 실행/재시작 작업 스케줄러 등록 스크립트
#
# 사용법 (PowerShell, 관리자 권한 불필요 — 현재 사용자 계정으로 등록):
#   .\scripts\install_scheduled_task.ps1 -ExePath "C:\PMT\PMT.exe"
#
# 동작:
#   - 로그온 시 PMT.exe 자동 실행
#   - 프로세스가 비정상 종료(크래시)하면 1분 간격으로 최대 3회 자동 재시작
#   - WorkingDirectory를 exe 폴더로 고정 (pmt.db / logs 분산 방지)
#
# 제거:
#   Unregister-ScheduledTask -TaskName "PMT" -Confirm:$false

param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [string]$TaskName = "PMT"
)

if (-not (Test-Path $ExePath)) {
    Write-Error "실행 파일을 찾을 수 없습니다: $ExePath"
    exit 1
}

$exeDir = Split-Path -Parent (Resolve-Path $ExePath)

$action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $exeDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "작업 스케줄러에 '$TaskName' 등록 완료."
Write-Host "  실행 파일: $ExePath"
Write-Host "  작업 폴더: $exeDir"
Write-Host "  로그온 시 자동 시작, 크래시 시 1분 간격 최대 3회 재시작"
Write-Host ""
Write-Host "지금 바로 시작하려면: Start-ScheduledTask -TaskName '$TaskName'"

# 참고(대안): 주기적 watchdog 방식이 필요하면 아래처럼 5분마다 확인 트리거를 추가할 수 있다.
#   $watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date) `
#       -RepetitionInterval (New-TimeSpan -Minutes 5)
#   (이미 실행 중이면 작업 스케줄러의 "새 인스턴스 시작 안 함" 기본 정책으로 중복 실행 방지됨)
