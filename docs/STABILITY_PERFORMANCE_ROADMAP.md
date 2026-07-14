# 안정성·성능 개선 로드맵

작성일: 2026-07-14. 1차 안정화 및 Phase 1 완료 후, 남은 개선 항목(Phase 2·3)을 순차 진행하기 위한 문서.

## 완료된 작업

### 1차 안정화 (2026-07-14)

- 시작 시 WAL/SHM 선삭제 제거 + `integrity_check` → 조건부 `quick_check`(24시간 마커) — `app/main.py`
- 복구 경로: backup API + 결과 quick_check 검증 + iterdump 폴백
- `pmt_uvicorn.log` RotatingFileHandler(5MB×3) — `run_app.py`
- 수집 성공 로그 DEBUG 강등, 사이클 요약 1줄만 INFO
- retention 태스크에 `wal_checkpoint(TRUNCATE)` + 조건부 VACUUM(freelist 20% 또는 7일, 최소 24시간 간격) — `background_collector.py`
- `traffic_logs` retention(7일 + 고아 proxy_id) — `resource_collector.py`
- frozen 빌드 CWD 고정(exe 옆에 DB/로그) — `run_app.py`
- 작업 스케줄러 등록 스크립트 — `scripts/install_scheduled_task.ps1`

### Phase 1 (2026-07-14)

- resource_collector mem/disk SSH를 SSHPool 경유로 전환(`max_retries=1`) — `resource_collector.py`
- SSHPool 유휴 연결 정리(600초) + shutdown 시 `close_all()` — `ssh.py`, `main.py`
- live 폴링 SSH 2회 → 1회(wc + tail 단일 명령 결합) — `api/traffic_logs.py`
- `/healthz`에 `collection_tasks`/`last_collect_success_at`/`last_collect_result` 노출
- (취소) `(proxy_id, id)` 복합 인덱스: SQLite에서 `id`는 rowid 별칭이라 기존 단일 인덱스 `ix_traffic_logs_proxy_id`가 이미 (proxy_id, rowid) 순서를 보장 — EXPLAIN QUERY PLAN으로 단일 proxy 조회가 정렬 없이 처리됨을 확인, 복합 인덱스는 중복이라 추가하지 않음. 다중 proxy IN 조회의 정렬은 인덱스로 회피 불가(LIMIT로 상한 제어됨).

### 테스트 강화 (2026-07-14)

Phase 2 리팩터링의 "변경 전후 결과 동일" 계약을 보증하는 테스트를 선행 구축 (총 64개, `pytest tests/`):

- `test_traffic_logs_list_api.py` — 목록 API 페이징·정렬·필터·검색 계약 (Phase 2 인덱스/쿼리 변경 대비)
- `test_traffic_logs_analyze_api.py` — 분석 API 집계 수치를 정확히 고정 (**Phase 2-2 SQL 전환의 기준선**): blocked 대소문자·공백 처리, 음수 바이트 클램프, anomaly 탐지 포함
- `test_resource_usage_api.py` — `/api/history` 필터·정렬·스키마 계약 (**Phase 2-3 다운샘플링의 하위호환 기준선**)
- `test_resource_analysis.py` — percentile 보간·시간대 밴드 수치 고정 (**Phase 2-4 기준선**)
- `test_ssh_and_collector.py` — SSHPool 유휴 정리, 수집기 파싱 계약, `calculate_mbps` 카운터 랩/리셋 (**Phase 2-1 SNMP 통합의 델타 계산 기준선**), live 엔드포인트 SSH 1회 왕복
- `test_db_recovery.py` — 크래시 후 WAL 데이터 보존, 손상 DB 복구, quick_check 마커
- `test_retention.py` — resource_usage/traffic_logs retention
- `tests/conftest.py` — 테스트가 실제 `./pmt.db`/`./logs`를 건드리지 않도록 env 격리 추가

### Phase 2 (2026-07-15)

- **2-1. SNMP 다중 OID 단일 GET** — `snmp_get_many()` 신규(`resource_collector.py`): 16개 단위 청크, 일시 실패 1회 재시도, `SnmpErrorStatus`(v1식 프로토콜 오류) 시 개별 GET 폴백으로 생존 지표 회수. `collect_for_proxy`의 스칼라 지표와 `collect_interface_mbps_from_oids`의 인터페이스 OID를 각각 GET 1회로 통합. 델타 캐시 구조·`monotonic()` 단일 타임스탬프 방식은 유지 (테스트: `test_snmp_collect.py`).
- **2-2. analyze SQL GROUP BY 전환** — `traffic_logs.py`: 전체 행 로드+Counter → SQL 집계. 음수 클램프는 `case((col > 0, col), else_=0)`, blocked는 `lower(trim(action_names))=='block'`, NULL/0 상태코드는 "Unknown"으로 병합. 응답 JSON 구조 동일, anomaly 탐지는 기존 파이썬 함수 재사용 (계약 테스트로 수치 동일성 확인됨).
- **2-3. /api/history 다운샘플링** — `max_points`(기본 2000) 파라미터 추가, 버킷 폭 60초 미만이면 원본 경로(완전 하위호환). 확인된 사실: `DateTime(timezone=True)`여도 SQLite에는 **오프셋 없는 KST 벽시계 문자열**로 저장됨 → `strftime('%s')`가 +9h 상수 오프셋을 갖지만 버킷 경계가 균일해 그룹핑에 안전. `interface_mbps`·메타는 버킷 내 `MAX(id)` 행 대표.
- **2-4. resource_analysis 튜플 로드** — `_fetch_rows()`로 `with_entities(proxy_id, collected_at, metric...)`만 로드, ORM 전체 엔티티 생성 제거. 결과 수치 불변(전 함수 계약 테스트 통과). 참고: API 핸들러가 요청당 compute 1개만 호출하므로 rows 공유 인자는 불필요해 도입하지 않음.

테스트: 78개 통과 (`pytest tests/`). 프런트 무수정 — `/api/history`의 `max_points`는 서버 기본값 2000 적용.

### Phase 3 (2026-07-15)

- **3-1. collect 비동기화 (A안)** — `traffic_logs.py`: `async def` + `asyncio.to_thread` + Semaphore(4). 수집이 수십 초 걸려도 FastAPI 동기 핸들러용 스레드풀을 점유하지 않아 다른 API 응답 지연 방지. 응답 스키마 불변(프런트 무수정). B안(202 + task_id 폴링)은 대량 수집이 잦은 사용 패턴이 확인되면 그때 검토.
- **3-2. 인메모리 캐시 위생** — `cleanup_stale_caches()`(resource_collector): 삭제된 프록시의 카운터 캐시 키 + 만료된 mem 캐시 항목 제거. `cleanup_live_state()`(api/traffic_logs): 삭제된 프록시의 live 오프셋 제거. 둘 다 1시간 retention 주기에서 호출.

테스트: 84개 통과. **로드맵 전 항목 완료.**

## 운영 반영 시 참고

- PyInstaller 재빌드(`scripts/build_windows.ps1`) 후 윈도우 스모크 테스트(시작 → 자동 수집 → 각 페이지 로드).
- SNMP 통합(2-1)은 실장비에서 수 주기 값 연속성 관찰 권장 (cpu/mem/mbps 패턴, 첫 주기 0.0 → 이후 정상 델타).
- 향후 추가 개선이 필요해지면 후보: 다운샘플링에 MAX 병행(피크 보존), collect B안(백그라운드 job), 트래픽/세션 라이브의 WebSocket 전환, 데이터 계층화(시간/일 평균 테이블).
