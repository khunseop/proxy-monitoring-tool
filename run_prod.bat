@echo off
chcp 65001 >nul
cls
setlocal enabledelayedexpansion

echo ============================================
echo  PMT 운영망 업데이트 + 실행 스크립트
echo  (pmt.bundle 반영 + 서버 실행)
echo ============================================
echo.

cd /d "%~dp0"

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [오류] 이 폴더는 git 저장소가 아닙니다. run_prod.bat 위치를 확인하세요.
    goto :fail
)

if not exist "pmt.bundle" (
    echo [오류] pmt.bundle 파일을 찾을 수 없습니다. 개발망에서 만든 pmt.bundle을 이 폴더에 복사하세요.
    goto :fail
)

echo [1/3] 로컬 변경 사항 확인 중...
set DIRTY=
for /f "delims=" %%i in ('git status --porcelain') do set DIRTY=1
if defined DIRTY (
    echo [알림] 커밋되지 않은 로컬 변경 사항이 있어 임시로 보관합니다 ^(git stash^).
    git stash push -u -m "run_prod.bat auto-stash %date% %time%"
    if errorlevel 1 (
        echo [오류] stash에 실패했습니다. 수동으로 git status를 확인한 뒤 다시 실행하세요.
        goto :fail
    )
    echo        저장된 변경 사항은 "git stash list"로 확인, "git stash pop"으로 복원할 수 있습니다.
) else (
    echo        로컬 변경 사항 없음.
)

echo.
echo [2/3] pmt.bundle로부터 업데이트 반영 중...
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set CURRENT_BRANCH=%%b
git pull "%~dp0pmt.bundle" %CURRENT_BRANCH%
if errorlevel 1 (
    echo [오류] pmt.bundle 반영에 실패했습니다. 충돌 여부를 확인한 뒤 다시 실행하세요.
    goto :fail
)
echo        완료. ^(브랜치: %CURRENT_BRANCH%^)

echo.
echo [3/3] 서버 실행 중...
echo   python run_app.py  ^(기본 http://localhost:8712^)
echo ============================================
python run_app.py
goto :end

:fail
echo.
echo 업데이트/실행이 중단되었습니다.
pause
exit /b 1

:end
pause
