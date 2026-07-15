# 운영망 저장소 재구축 안내 (2026-07-15 git 이력 정리)

## 배경

배포용 `pmt.bundle`이 과도하게 커서(11MB+) 원인을 조사한 결과, 과거 커밋에 포함됐던
`pmt.db.corrupt`(18.5MB) 등 대용량 파일이 git 이력에 남아 있었음.
`git filter-repo`로 이력에서 아래 파일들을 영구 제거하고 force push 완료:

- `pmt.db.corrupt`
- `.playwright-mcp/` 산출물
- 테스트 스크린샷 PNG (`analysis-done.png`, `analysis-result.png`, `modal_test.png`, `settings_page.png`)

결과: 저장소 pack 약 4.8MB, `pmt.bundle` 약 4.9MB (기존 11MB).

## 운영망에서 해야 할 일 (1회)

이력이 재작성됐기 때문에 **기존 운영망 저장소에서는 `run_prod.bat`의
`git pull pmt.bundle`이 실패함** (non-fast-forward). 아래 절차로 1회 재구축 필요:

```bat
:: 1. 새 pmt.bundle을 운영 PC로 복사한 뒤, 기존 폴더 옆에서:
git clone pmt.bundle pmt-new

:: 2. 기존 폴더의 데이터/설정 파일을 새 폴더로 복사
::    (pmt.db, pmt.db-wal, pmt.db-shm, logs\, .env 등 git 미추적 파일 전부)

:: 3. 기존 폴더를 백업용으로 이름 변경 후, pmt-new를 기존 이름으로 변경

:: 4. 새 폴더에 pmt.bundle과 run_prod.bat이 있는지 확인 후 run_prod.bat 실행
```

재구축 이후의 배포는 기존과 동일하게 `deploy.bat` → 번들 복사 → `run_prod.bat` 흐름으로 진행하면 됨.
