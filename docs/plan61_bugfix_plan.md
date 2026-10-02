# Plan 61 E1 측정 발견 버그 — 개선 계획 (2026-07-15)

> E1 측정 게이트 실행(orchestration 경로 최초 실접속 EX 측정) 중 발견된 버그의 수정 계획.
> 측정 상세: E1 측정 보고서(세션 scratchpad `e1_measurement_report.md`) 참조.
> 상태 표기: ☑ 완료 / ▶ 진행 / ☐ 대기 / ⚠ 사용자 결정 필요

## B1. ☑ E1 하네스 실접속 경로 버그 3건 (`scripts/eval_text2sql.py`)

| # | 증상 | 원인 | 수정 |
|---|------|------|------|
| 1 | orchestration/multidb 경로 전건 스킵(`KeyError: parsed_requirements`) | 예측기가 input_parser를 거치지 않고 `_run_single_db_pipeline`/`multi_db_executor` 직접 호출 | 두 경로에 input_parser 선행 실행 + state 병합 |
| 2 | graph 경로 전건 스킵(Checkpointer thread_id 요구) | `graph.ainvoke(state)`에 configurable 미전달 | `thread_id=eval-{item.id}` 전달 |
| 3 | A/B baseline=variant 동일 실행 | generic 축 `base_flags={}` — env 미세팅이라 `.env`에 켜진 값이 baseline에 잔존 | `base_flags={axis: False}` 명시 세팅 |

- 검증: mock·dry-run 동작 불변, `tests/text2sql/test_ex_harness.py` 56건 통과, 실접속 15건 채점 성공(스킵 1).
- 함의: 계획서 §12의 "실접속 graceful 스킵"은 실접속 실행이 한 번도 성공한 적 없었다는 뜻 → §12 갱신 필요(팀리드 #8).

## B2. ☑ config 임포트 시점 고정 (`src/config.py`) — *핵심* (2026-07-15 수정 완료)

- **증상**: `os.environ` 변경 + `load_config.cache_clear()` 후에도 nested config(`cfg.text2sql.*` 등)가 옛 값 유지. in-process A/B(v3·v4) 전량 무효화, env를 조작하는 테스트의 전체 스위트 격리 실패(신규 19건) 원인 추정.
- **원인**: `AppConfig`의 nested 필드가 `text2sql: Text2SQLConfig = Text2SQLConfig()` — **클래스 정의(모듈 임포트) 시점에 인스턴스가 생성**되어 그때의 env로 고정. pydantic v2는 인스턴스 기본값을 딥카피할 뿐 재평가하지 않음.
- **수정(완료)**: 전 nested 필드(17개)를 `Field(default_factory=...)`로 전환 → `AppConfig()` 인스턴스화 시점(=cache_clear 후 재로드 시점)에 env 재평가.
- **검증(완료)**: ① env 플립 회귀 테스트 `tests/test_config_env_reload.py` 2건 통과, ② 전체 스위트 실패 목록 수정 전후 동일(기존 실패 비증가 = 회귀 0). ※ 신규 19건 실패의 원인은 B2가 아니라 B3(별도 오염원)로 판명.
- **부수 효과**: 운영(기동 시 env 고정)은 동작 불변 — 임포트 시 1회 → 첫 load_config() 시 1회로 인스턴스화 시점만 이동.

## B3. ☑ 전체-스위트 대량 실패 — e2e 이벤트 루프 오염 (2026-07-15 수정 완료)

- **실측**: 전체 800건 실패 중 **831개 traceback 라인이 단일 유형** `RuntimeError: Runner.run() cannot be called from a running event loop`. `tests/e2e` 제외 실행 시 **49 failed / 2298 passed**로 급감. 최소 재현: e2e 테스트 1건 + async 테스트 파일 1개 조합으로 재현됨.
- **원인**: tests/e2e(pytest-playwright)가 알파벳 순으로 가장 먼저 실행되며 메인 프로세스에 이벤트 루프를 실행 상태로 잔류 → `asyncio_mode=auto` 하에서 후속 pytest-asyncio 테스트 약 750건 전멸. **"기존 환경 의존 실패 781건"으로 오인됐던 것의 실체**. 신규 테스트 19건 실패도 이 유형(격리 실행 통과가 그 증거).
- **수정(완료)**: `tests/e2e/conftest.py`에 `collect_ignore_glob` 가드 — 기본 수집 제외, `RUN_E2E=1`로 옵트인(`RUN_E2E=1 pytest tests/e2e`). e2e는 본 환경에서 자체적으로도 브라우저 의존 실패 상태였음.
- **후속(☐)**: playwright↔asyncio_mode=auto 상호작용의 근본 원인 격리(어느 fixture가 루프를 남기는지), e2e를 CI 별도 잡으로 분리.
- **최종 검증(2026-07-15)**: B1~B3 적용 후 기본 스위트 `pytest tests/ -q` = **49 failed / 2298 passed / 5 errors**(수정 전 800 failed / 1536 passed). Plan 61 신규 테스트(candidate_pipeline·multi_candidate·synonym_governance·metadata·config_env_reload) **전체 스위트 맥락에서 전부 통과**. 잔존 49+5는 전부 수정 전에도 있던 환경 의존 실패(test_e2e_polestar 17, test_api 6, test_plan33 5 — polestar_pg.yaml 부재 등)로 Plan 61 무관 — 별도 정리 백로그(B5).

## B4. ☑ 골드셋 규약 모순 + 샌드박스 이중 모집단 (사용자 결정 (b) — 2026-07-15 수정 완료)

### 결정·조치 (사용자 지시: "(b) 진행하되 전체 DB 스키마를 상세히 분석하여 수정")
- **스키마 전수 분석 결과**:
  - `cmm_resource` 앵커 구조: 합성 더미 40대(02_insert)는 앵커=`platform.server` 행(가족에 server.Server 없음), 실캡처 hostapo01/02(05_insert_excel_data, 실 DB 익스포트)·Plan61 픽스처(07)·Plan52 노이즈(06)는 앵커=`server.Server` 행(자기참조 platform_resource_id). **실캡처에는 management.MonitorGroup은 있어도 platform.% 행이 0개** → 실 폴스타 형상은 server.Server 앵커로 판정.
  - 두 장부 hostname 중복 0(완전 서로소), 자식 리소스(Cpus/Memory/FS/Disks) 160개는 platform 앵커에, 24개는 server.Server 앵커에 소속. config(core_config_prop)는 각 앵커의 resource_conf_id로 연결(40대 중 30대만 OSType/Vendor 등 속성 보유).
  - Plan 52 노이즈 4행은 `server.Server`+DTIME NULL 필수(NAME 식별) — 불변 유지.
- **수정**: `testdata/pg/init/08_plan61_population_pairing.sql` 신설(멱등, ID 9670001~9671999 격리, 06/07 불변) — ① platform 앵커 40대에 server.Server 쌍둥이 추가, ② server.Server 앵커 10대(실캡처 2·P61 4·노이즈 4)에 platform.server 쌍둥이 추가. 쌍둥이는 hostname/ip/name/avail_status 복사 + **resource_conf_id 공유**(EAV 값 동일) + platform_resource_id=가족 앵커.
- **검증**: 두 장부 50=50, hostname 대칭차 0(양방향), gp-001 gold_sql(장부A) vs 시맨틱 피벗(장부B) **결과집합 EX 동치 True**. 골드셋 gp-001 gold_smq의 모순 필터(platform.server%) 제거(`filters: []`) — dry-run 검증 0에러, 골든 회귀 36건 통과.
- **후속 확인 권고(실 DB 접근 가능 시)**: 프로필 query_examples의 `LIKE 'platform.server%'` 술어가 실 gp에서 실제 행을 반환하는지 1회 실측 — 실캡처 증거상 실 폴스타에 platform.% 행이 없을 가능성이 있으며, 그 경우 프로필 예시(gp/yd/b0 각 7곳)가 샌드박스 초기 합성 규약의 산물일 수 있다(프롬프트 가이드 정비 대상).
- **효과 재측정(E1 v6, 프로세스 분리 2회 반복)**: server_config 카테고리 — semantic ON **7/7 만점**(2회 재현, 페어 정렬 전 4/7), OFF 6/7·7/7. 시맨틱 컴파일 3건 전부 정답 전환 — **EX가 규약이 아닌 생성 품질을 측정하게 됨**(교란 해소 실증). complex/performance/alarm은 여전히 0~1/3 — 트랙 A·커버리지 확장의 실제 타깃으로 확정. 사이드 이펙트 검증: 폴스타 직조회 테스트(test_e2e_polestar) 17 failed로 수정 전과 동일(추가 회귀 0), 골든 회귀 36건 통과, 골드셋 dry-run 0에러.

### (참고) 원 문제 기술

- **사실관계**: gp-001~003 gold_sql은 `platform.server%`(프로필 query_examples 규약 — 실DB 검증됨), gold_smq·시맨틱 모델은 `server.Server`(D-050/D-068 EAV 피벗 규약). **실DB에선 같은 서버의 두 표현이지만, 샌드박스에선 레거시 더미(40행, platform.server%)와 픽스처 계열(10행, server.Server)이 서로소** → 어느 경로로 생성하든 EX 실패가 구조적으로 발생.
- **옵션**:
  - (a) 골드셋을 server.Server 규약으로 통일 — 시맨틱 경로와 정합하나, 프로필 예시를 모방하는 LLM 자유생성이 불리해짐(반대 방향 편향).
  - (b) 픽스처에서 두 모집단을 페어 정렬(레거시 더미에 server.Server 계열 부여 또는 픽스처 서버에 platform 행 부여) — 실DB와 같은 등가성 재현, 타 테스트 영향 검토 필요.
  - (c) gp-001~003을 픽스처 서버 한정 필터로 재작성 — 국소적이나 "전체 서버" 대표성 상실.
- **권고**: (b)가 실DB 의미론에 가장 충실. 단 실 gp DB에서 platform 행과 server.Server 행의 configuration 공유 구조를 실측 확인 후 진행.
- 부수: gold_smq↔gold_sql 모순(gp-001)은 어느 옵션이든 통일 필요. 골드셋 질의가 전부 정형 표현이라 fuzzy(E5-1) EX 효과 측정 불가 → 표기 변형 항목 추가.

## B5. 후속(관측성·정비)

- ☐ 실측 경로 SMQ 노출: query_generator가 `compile_from_nl`의 smq를 state에 저장 → 하네스가 실측 경로에서도 SMQ 정확도 채점 가능(현재 별도 스크립트로만 측정: 1/6).
- ☑ **Redis 유사어 사전 시딩(2026-07-15 구현 완료)**: `scripts/synonym_seeds.py`(derive/load/export) + `synonym_loader.load_seed_yaml/export_seed_yaml` + 시드 4종(`config/synonym_seeds/`). 시딩 후 **질의 수준 잔여 미매칭 38.5%→0%**(정확 히트 12→22/26) — **E5-4 착수 근거 현 골드셋 기준 소멸**(실사용 로그 재측정 후 최종 판단). 테스트 7건, 기존 회귀 0, arch_check 0. 절차: `docs/synonym_seed_migration_guide.md`, 설계: `docs/synonym_seed_migration_review.md`.
- 전체 스위트 기존 실패 781건(HEAD 기준, 환경 의존 — `polestar_pg.yaml` 부재 참조, `.env` 로컬 플래그 ON 의존 등): Plan 61 범위 밖, 별도 정리 계획 필요.
