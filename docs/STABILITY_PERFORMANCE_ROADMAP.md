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

---

## Phase 2 — 중간 위험·중간 이상 효과 (다음 순서)

### 2-1. SNMP 다중 OID 단일 GET 통합

- **현재**: `snmp_get`(`resource_collector.py`)이 OID당 새 `Snmp` 세션 + 단일 get. 프록시당 (지표 수 + 인터페이스 OID 수)만큼 UDP 세션. 타임아웃 2초, 재시도 없음 → 일시 패킷 유실 시 지표 조용히 누락.
- **변경**:
  - 신규 `snmp_get_many(host, port, community, oids: List[str], timeout_sec=2, retries=1) -> Dict[str, float|None]`: 한 세션에서 `snmp.get([oid, ...])`. **구현 전 aiosnmp의 다중 OID `get` 시그니처를 로컬 스크립트로 확인할 것.** 응답 varbind OID는 선행 `.` 정규화(`lstrip('.')`) 후 요청 OID에 매핑.
  - `collect_for_proxy`: SNMP 지표들을 1회 GET으로, `collect_interface_mbps_from_oids`의 in/out OID들도 1회 GET으로. SSH 기반 mem/disk는 기존 별도 태스크 유지.
  - 실패 시 1회 재시도. 인터페이스 OID가 많으면(20개+) 10~16개 단위 청크 분할.
  - **SNMPv1 방어**: v1은 잘못된 OID 1개로 전체 GET 실패 → `error_status` 발생 시 개별 `snmp_get` 폴백 루프.
- **델타 캐시 호환**: `_GLOBAL_TRAFFIC_COUNTER_CACHE`/`_INTERFACE_COUNTER_CACHE` 구조·`monotonic()` 단일 타임스탬프 방식 유지 — 값 배분 후 델타 분기 코드는 손대지 않음. 오히려 지표 간 시각 오차가 줄어 mbps 정확도 향상.
- **위험**: 중간 — 장비별 SNMP 구현 편차(varbind 순서, PDU 크기).
- **검증**: 동일 장비에서 변경 전후 5주기 값 비교(첫 주기 0.0 → 둘째 주기 정상 델타 패턴 유지). 잘못된 OID 1개 섞어 나머지 지표 생존 확인.

### 2-2. `/api/traffic-logs/analyze`를 SQL GROUP BY로 전환

- **현재**: 전체 행 `.all()` 후 파이썬 Counter 집계(`traffic_logs.py` analyze 엔드포인트). 5만 행이면 ORM 객체 5만 개 → 메모리 수백 MB, 수 초 소요.
- **변경**: hosts/clients/statuses/proxies/summary를 `func.count/func.sum` + GROUP BY로. 음수 방어는 `case((col > 0, col), else_=0)`(SQLite에 2인자 max 없음). `action_names` 비교는 파이썬의 `.strip().lower()=='block'`과 일치하도록 `func.lower(func.trim(...))`. blocked/error 카운트 dict를 만들어 기존 `_detect_traffic_anomalies`(순수 파이썬)에 그대로 전달.
- **응답 JSON 구조 완전 동일 유지** → 프런트(`traffic_logs_analyze.js`) 무수정.
- **검증**: 동일 DB에서 변경 전후 응답 diff(총계, 상위 10개 host/client, anomalies 수). 빈 DB 케이스.

### 2-3. `/api/history` 시간 버킷 다운샘플링

- **현재**: `resource_usage.py`의 `/api/history`가 기간 내 전체 행 `.all()` — 90일이면 수십만 행 직렬화.
- **변경**:
  - 파라미터 추가: `max_points: int = Query(2000, ge=100, le=20000)`, `bucket_sec` 자동 산출(기간/max_points). 60초 미만이면 기존 경로(완전 하위호환).
  - 버킷 집계: `CAST(strftime('%s', collected_at) AS INTEGER) / bucket_sec` GROUP BY, 지표는 `AVG(...)`(피크 보존 필요 시 MAX 병행). 대표 시각 `MIN(collected_at)`.
  - **최대 함정**: `collected_at`은 KST aware로 저장 — SQLite에 오프셋 포함 문자열로 저장되므로 `strftime('%s', ...)`가 이를 처리하는지 실제 저장 포맷으로 반드시 확인. 불일치 시 `julianday`/`unixepoch()` 사용.
  - `interface_mbps`(JSON 문자열)는 SQL 집계 불가 → 버킷 내 마지막 행(`MAX(id)`) 값 대표.
  - 응답은 기존 `ResourceUsageSchema` 리스트 형태 유지 → 프런트 `resource_history.js` 무수정 동작. (선택: 요청에 `max_points=2000` 한 줄 추가)
- **검증**: 짧은/긴 기간 각각 스키마 동일성, sqlite3 CLI 수동 집계와 AVG spot-check, 차트 렌더 확인.

### 2-4. resource_analysis 대량 쿼리 1회화

- **현재**: `app/services/resource_analysis.py`의 `_base_query(...).all()`이 6곳 반복 — 같은 화면에서 지표별로 동일 대량 쿼리 재실행.
- **변경(최소)**: `.all()`을 `with_entities(proxy_id, collected_at, 필요한 metric 컬럼)`로 축소. 한 요청에서 여러 compute를 부르는 API 핸들러만 행을 한 번 조회해 `rows: Optional[List]` 인자로 전달.
- **계약**: 결과 수치 완전 불변. 변경 전후 각 분석 API 응답 JSON diff로 검증.

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
