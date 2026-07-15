@echo off
chcp 65001 >nul
cls
setlocal enabledelayedexpansion

echo ============================================
echo  PMT 배포 스크립트 (git pull + bundle 생성)
echo ============================================
echo.

cd /d "%~dp0"

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [오류] 이 폴더는 git 저장소가 아닙니다. deploy.bat 위치를 확인하세요.
    goto :fail
)

echo [1/3] 로컬 변경 사항 확인 중...
set DIRTY=
for /f "delims=" %%i in ('git status --porcelain') do set DIRTY=1
if defined DIRTY (
    echo [알림] 커밋되지 않은 로컬 변경 사항이 있어 임시로 보관합니다 ^(git stash^).
    git stash push -u -m "deploy.bat auto-stash %date% %time%"
    if errorlevel 1 (
        echo [오류] stash에 실패했습니다. 수동으로 git status를 확인한 뒤 다시 실행하세요.
        goto :fail
    )
    echo        저장된 변경 사항은 "git stash list"로 확인, "git stash pop"으로 복원할 수 있습니다.
) else (
    echo        로컬 변경 사항 없음.
)

echo.
echo [2/3] git pull 실행 중...
set PULL_OK=
for /l %%a in (1,1,3) do (
    if not defined PULL_OK (
        git pull
        if not errorlevel 1 (
            set PULL_OK=1
        ) else (
            echo        pull 실패 ^(시도 %%a/3^). 5초 후 재시도합니다...
            ping -n 6 127.0.0.1 >nul
        )
    )
)
if not defined PULL_OK (
    echo [오류] git pull이 3회 모두 실패했습니다. 위 메시지를 확인하고 네트워크/충돌 여부를 점검한 뒤 다시 실행하세요.
    goto :fail
)

echo.
echo [3/3] pmt.bundle 생성 중...
git bundle create pmt.bundle --all
if errorlevel 1 (
    echo [오류] pmt.bundle 생성에 실패했습니다.
    goto :fail
)
echo        완료.

echo.
echo ============================================
echo  배포 준비 완료.
echo  pmt.bundle을 운영망으로 옮긴 뒤
echo  run_prod.bat을 실행하세요.
echo ============================================
goto :end

:fail
echo.
echo 배포가 중단되었습니다.
pause
exit /b 1

:end
pause
