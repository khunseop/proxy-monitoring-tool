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

---

## Phase 3 — 선택 (필요 시)

### 3-1. `/api/traffic-logs/collect` 비동기화

- **현재**: 동기 `def` 핸들러 안에서 ThreadPoolExecutor(≤4)로 SSH 수집 완료까지 블로킹 — 수 초~수십 초 요청 점유, FastAPI 스레드풀 고갈 위험.
- **A안(저위험, 프런트 무수정)**: `async def` + `asyncio.to_thread` gather — 스레드풀 고갈 완화. 응답은 완료 후 반환.
- **B안(효과 큼, 프런트 수정)**: 202 + `task_id` 즉시 반환 + 상태 폴링 엔드포인트. `traffic_logs.js` 수집 버튼 핸들러를 폴링 루프로 교체 필요.
- 권장: A 먼저. B는 5만 줄×여러 프록시 수집이 잦은 사용 패턴이 확인될 때.
- **검증**: 수집 중 다른 API 동시 호출 응답 지연 측정 비교.

### 3-2. 인메모리 캐시 위생

- `_INTERFACE_COUNTER_CACHE`, `_GLOBAL_TRAFFIC_COUNTER_CACHE`, `_MEM_CACHE`(resource_collector.py), `_live_state`(api/traffic_logs.py)는 삭제된 프록시 키가 남아 무한 증가 가능(항목이 작아 실해악 낮음).
- retention 주기(`_periodic_retention`)에서 존재하지 않는 proxy_id 키 제거 추가.

## 진행 원칙

- Phase 2-1(SNMP)은 수집 데이터 연속성에 영향 → **단독 커밋 + 수 주기 관찰 후** 다음 항목 진행.
- 2-2/2-3/2-4는 읽기 경로만이라 병행 가능. 2-3만 프런트 확인 필요.
- 각 단계 후 PyInstaller 재빌드 + 윈도우 스모크 테스트(시작 → 자동 수집 → 각 페이지 로드).
