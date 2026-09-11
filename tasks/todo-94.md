# todo-94 — 기능·성능 시나리오 자동 실행 하네스

정본 스펙: `plans/94-WIP-feature-perf-scenario-suite.md` · 작업 계획: `tasks/plan-94.md`

## 완료 (Wave S0~S4 · 전부 무과금)

- [x] **S0 커버리지 매트릭스** — `docs/30_scenario_coverage.md` (95행)
  - Acceptance: 계획서 전건이 `프롬프트 트리거 가능/불가/미분류` 로 분류되고, 분모가 기계 판독된다
  - Verify: `analyze.parse_coverage_doc()` 가 95행을 읽고 `coverage_gap.md` 가 사유 5종으로 분류 — 실행 확인
  - Files: `docs/30_scenario_coverage.md`
- [x] **S1 카탈로그 이관** — 문서 145턴 → YAML 139 시나리오
  - Acceptance: `docs/29` A~K 113턴 + SYN 32건이 기계 판독 YAML이 되고 ID가 보존된다
  - Verify: `python -m scripts.scenario --dry-run` → 카탈로그 OK
  - Files: `scripts/scenario/import_docs.py`, `testdata/scenarios/{a..l}_*.yaml`
- [x] **S1b R군 신규 작성** — R1 10 · R2 10 · R3 12 · R4 12 (+ 대조군 34) = 78 시나리오
  - Acceptance: 계획서 §3.7의 **모든 축**이 최소 1건씩 있고, R2·R3·R4 전건이 대조군 쌍을 갖는다
  - Verify: `test_catalog.py::test_V16_*` 3건 + 로더 상호 참조 검증
  - Files: `testdata/scenarios/r{1,2,3,4}_*.yaml`
- [x] **S2 러너 골격** — 카탈로그·클라이언트·단언기·서버·모의서버·오케스트레이션
  - Acceptance: `--dry-run`·`--mock` 가 전 경로를 돌고 JSONL 이 적재된다. 플랫폼 분기 W1~W9
  - Verify: `--mock` 실행 확인 + `test_runner_cli.py` 13건
  - Files: `scripts/scenario/{__main__,catalog,client,assertions,server,mockserver,runner,_serve}.py`
- [x] **S3 리포트 생성기** — 11개 절 고정 순서 + `summary.json`
  - Acceptance: p95는 표본 20건 이상일 때만. 수동 검토·제외 목록을 비우지 않는다
  - Verify: `test_report_analyze.py::test_V8_*` 2건 · `test_V9_*`
  - Files: `scripts/scenario/report.py`
- [x] **S4 분석기** — 제안 문서 6종
  - Acceptance: 어떤 저장소 파일도 수정하지 않는다. 반복 3회 미만이면 전건 보류
  - Verify: `test_V10_분석기는_저장소_파일을_수정하지_않는다` · `test_V18_*` 2건
  - Files: `scripts/scenario/analyze.py`
- [x] **수용 기준 테스트 224건** — **V1~V20 전건 커버**
  - Acceptance: 계획서 §10의 수용 기준 20개가 전부 테스트로 고정되고, 하네스 10모듈이 전부 덮인다
  - Verify: `python -m pytest tests/test_scenario/ -q` → **222 passed, 2 skipped**
    (skip 2건은 V19 Windows 전용 — POSIX 에서 통과로 세지 않는다)
  - Files: `tests/test_scenario/` 12파일

| 파일 | 건수 | 덮는 것 |
|---|---:|---|
| `test_catalog.py` | 14 | V1·V2·V16 · 정규식 로드 검증 · 정본 카탈로그 |
| `test_assertions.py` | 57 | V15·V17 · **단언 키 19종 전수**(통과/불합격 쌍) · 대응 등급 10종 · 기능/성능 2축 분리 |
| `test_client.py` | 16 | **SSE 파서**(노드 지연·ttfb·hang·상태 유도) · W2 |
| `test_server.py` | 15 | V4·V5·V13 · W5·W6 · 기동 검증 3종 |
| `test_report_analyze.py` | 14 | V8·V9·V10·V18 · **11개 절 순서 고정** · 불안정 · 실패 분류 |
| `test_runner_cli.py` | 14 | V11 · D-127 게이트 · 기동 실패 노출 · W3·W4·W5 |
| `test_cli_surface.py` | 15 | `--mock`·`--report`·`--analyze` · 기본 동작 순서 · 비대화 승인 · resume 오타 |
| `test_file_coverage.py` | 11 | **V7 전 칼럼 검증** · V3 커버리지 역집계 |
| `test_import_docs.py` | 11 | 문서 145턴 파싱 · G군 멀티턴 묶음 |
| `test_integration_mock.py` | 11 | **V6·V12·V20** — 모의 서버 자식 프로세스 왕복 |
| `test_platform_gates.py` | 9 | V14·V19 · W4·W8 |
| `test_misc_units.py` | 17 | 프로파일 로더 · provenance · 모의 해석기 |
| `test_windows_encoding.py` | 12 | **W10 cp949 입력 디코딩** · provenance 무예외 · netsh 파싱 |

## 남은 작업 (사람 판단 또는 승인 필요)

### A. 사용자 확정 게이트 (§12) — 코드는 권고안 기본값으로 동작 중
- [ ] **G-1** 정본 실행 환경 (폐쇄망 fabrix / 개발망 gemini)
- [ ] **G-3** 폐쇄망 인증 — 전용 벤치 계정 발급. 미확정 상태에서는 설정 에코가 `미확인` 으로 남는다
- [ ] **G-4** 과금 승인 단위 — "스위트 1회 = 승인 1건"은 D-127 "건마다 승인"의 해석 확장이라 **명시 동의 필요**
- [ ] **G-10** R군 대응 등급 정책 — 확정 전까지 R군 판정은 전건 `manual`(코드 기본값 `policy_confirmed: false`)
- [ ] **G-11** R군 1차 실행 범위 · **G-12** Windows 지원 등급(1급으로 구현 완료)

### B. 사람이 채워야 하는 단언 (S1 잔여 · 기계로는 못 한다)
- [ ] **139건의 `expect` 단언 작성** — 현재 전부 `manual_review` 원문만 들어 있다.
      옮긴 만큼만 자동 판정 대상이 되고, 나머지는 리포트 9절에 계속 남는다(침묵 누락 없음).
- [ ] **H·I군 25건의 실제 양식 파일** — 지금은 자리표(`fixtures/form_sample.xlsx`)를 가리킨다
- [ ] **R군 목표 대비 잔여** — 작성 44건 / 계획서 목표 75건 (R1 10/20 · R2 10/15 · R3 12/20 · R4 12/20)
- [ ] **docs/30 `미분류` 28건** — 사람이 트리거 가능 여부를 확정해야 분모가 완성된다

### B-2. 테스트 작성 중 실측으로 드러나 고친 것 (2026-09-11 2차)

- [x] **V7이 요구한 '전 칼럼 검증'을 구현이 못 잡았다** — `filled_rows` 가 *아무 칸이나 차 있으면*
      채워진 행으로 셌다. 선언한 칼럼이 **하나라도 비면** 채워진 것이 아니도록 고쳤다.
      일부만 채운 산출물이 합격하던 구멍이다(Known Mistakes: 미리보기 일부 검증 금지).
- [x] **비대화 환경에서 `--run` 이 EOFError 로 죽었다** — 승인을 못 받으면 멈추는 것이 맞다.
      `--yes` 안내와 함께 130 으로 종료한다(D-127).
- [x] **`--resume` 오타가 조용히 새 런을 만들었다** — 이어 돌린 줄 알고 처음부터 다시 도는
      사고가 난다. 대상 부재를 1로 거부한다.
- [x] **기동 예외가 프로파일을 리포트에서 통째로 지웠다** — 사라진 프로파일은 "돌지 않았다"가
      아니라 "없었다"로 읽힌다. 예외를 사유로 적재하고 시나리오를 제외 목록에 남긴다.
- [x] **F-01 단언 이관** — 계획서 §3.2 정본 예시대로 2턴 HITL(역질문 -> `selected_db_ids`)로
      다시 쓰고 `mock:` 블록을 붙여 V6 이 실제로 왕복을 검증하게 했다.

### B-3. 프롬프트 추출 결함 (2026-09-11 3차 · "테스트 프롬프트는 어디에 있냐" 질문에서 드러남)

이관 파서가 **프롬프트 칼럼을 고정 인덱스(2번째)로 집었다.** 실측하면 `docs/29`의 표 구조는
군마다 다르다. 43건이 영향권이었다.

| 군 | 실제 헤더 | 파서가 담던 것 | 조치 |
|---|---|---|---|
| **H** 17건 | `ID │ 양식 칼럼 구성 │ **입력 질의** │ …` | 양식 파일 경로(`testdata/templates/...xlsx`) | 헤더로 칼럼 탐색 → 정정 |
| **F** 8건 | `ID │ **입력(1턴)** │ 예상 │ **입력(2턴)** │ 예상` | 1턴만, 2턴 누락 | 2턴은 **구조화 필드**(`selected_db_ids=[…]`)라 자동 생성 금지 |
| **I** 8건 | `ID │ **시나리오**(산문) │ 예상` | 산문을 프롬프트로 | `prompt_authored: false` |
| **K** 10건 | `ID │ **방법**(다른 군 반복) │ …` | *"B-01~B-06 각 5회 반복"* | `prompt_authored: false` |

추가로 **셀 안의 이스케이프 `\|`가 칼럼 구분자로 잘려** H군 칼럼이 전부 밀려 있었다.

- [x] 파서를 **헤더 인식**으로 교체 (`_prompt_columns`) · `\|` 보호
- [x] 신규 필드 **`prompt_authored`**(기본 true) — false면 러너가 **사유와 함께 건너뛴다**.
      산문을 LLM 에 보내면 무의미한 결과에 돈만 나간다. 조용히 흘리지도, 몰래 실행하지도 않는다.
- [x] H·I·K 재생성 · F는 손으로 쓴 F-01 보존 후 병합
- [x] 회귀 감시 테스트 8건 — 정본 카탈로그에 표 파싱 찌꺼기(파일 경로·이스케이프 잔여)가 없는지
- [ ] **프롬프트 미작성 21건**(F 3 · I 8 · K 10)을 사람이 실제 프롬프트로 작성.
      I-04·I-05·I-06 은 원문이 이미 프롬프트라 플래그만 떼면 된다.
      **K군은 프롬프트가 아니라 실행 방법이다** — 러너의 `--repeat`·동시성 옵션으로 표현하는 것이
      맞는지 재검토 필요(계획서 §3.1이 `k_load.yaml K군 10`을 둔 전제와 어긋난다).

### B-4. 폐쇄망 Windows 실행 사고 (2026-09-11 · 사용자 실행 보고)

`python -m scripts.scenario --run --profile baseline` 승인 직후 **시나리오 한 건도 실행되기 전에 사망**.

```
Exception in thread Thread-5 (_readerthread):
  UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc0 in position 0
AttributeError: 'NoneType' object has no attribute 'strip'
  server.py:120 platform_provenance -> ).stdout.strip()
```

**원인은 내 코드다.** 한글 출력을 위해 **내가 가이드에서 요구한 `PYTHONUTF8=1`** 이
`subprocess.run(text=True)` 의 기본 디코딩을 UTF-8 로 만들었는데, 한국어 Windows 의
`powercfg`·`netsh`·`git` 은 **cp949** 로 쓴다. 결정적으로 **그 디코딩이 subprocess 의
reader 스레드에서 일어나** 예외가 거기서 터지므로 `except (OSError, SubprocessError)` 에
잡히지 않고 `stdout` 이 조용히 `None` 이 된다. 이어진 `.strip()` 이 런 전체를 죽였다.

기존 실수 이력(`docs/18:82`, 2026-07-16)은 **출력**의 cp949(`UnicodeEncodeError`)만 다뤘고,
내 W5 요구사항도 출력만 막았다 — **읽기 쪽이 무방비**였다.

- [x] 공통 헬퍼 **`run_capture`** — 바이트로 받아 utf-8 -> locale -> cp949 -> replace 폴백.
      **절대 예외를 던지지 않는다**
- [x] 취약 지점 4곳 전부 교체: `powercfg`·`netsh`(server.py) · `git rev-parse`·`git status`(runner.py)
- [x] `platform_provenance` 이중 방어 — **provenance 수집은 어떤 경우에도 런을 죽이지 않는다**
- [x] Windows provenance 에 **`console_codepage`** 추가(W6 — 측정 조건 재구성)
- [x] 회귀 감시 2건 — 하네스 소스에 `capture_output=True`+`text=True` 조합이나
      `.stdout.strip()` 체이닝이 남아 있으면 테스트 실패
- [x] 테스트 12건(`test_windows_encoding.py`) — cp949 바이트 · `stdout=None` · 타임아웃 ·
      netsh 파싱 · provenance 무예외
- [x] `docs/18_known_mistakes.md` 등재 · 계획서 부록 A.1-10 · **요구사항 W10** 추가
- [ ] **사용자 단말 동기화 필요** — 실행 위치가 `C:\AIOps\infra-collector\infra-collector-agent`
      로 이 저장소와 다른 체크아웃이다. 고친 `scripts/scenario/` 를 반영해야 재실행된다.

### C. 실 서버 대조가 필요한 구현 (무과금 경로로는 검증 불가)
- [ ] **teardown 3종** — 유사어 등록 해제 · 스키마 캐시 삭제 · 폼필 기억 삭제.
      현재는 `teardown_unsupported` 로 **기록만** 하고 리포트 10절에 노출한다(조용히 넘기지 않는다).
      엔드포인트는 있다(`DELETE /admin/schema-cache/{db_id}` 등) — 실 서버 대조 후 배선한다.
- [ ] **`llm_calls`·`tokens`·`retries`** — 응답에 실리지 않는다. 트레이스·감사 로그 대조 경로가 필요하다.
      현재는 `None` 으로 남고 분석기가 "대조 필요"로 표기한다.
- [ ] **`column_mapping` 수집** — `/query/{id}/mapping-report`(Markdown) 파싱이 필요하다.
      현재 `column_must_not_map` 은 **실행 SQL 기준**으로만 판정한다(D-200형 회귀는 잡힌다).
- [ ] **`--estimate` 의 LLM 호출 가정치** — 턴당 6회는 **가정**이다. S5 실측으로 대체한다.

### D. 과금 Wave (사용자 승인 후)
- [ ] **S5 개발망 소규모 실행** — 3~4개 군 + R군 표본 10건 + Windows 단말 1회
- [ ] **S6 폐쇄망 FabriX 전 스위트**
- [ ] **S7 반영** — `docs/29`를 카탈로그 생성본으로 전환 · FI 승격 · **D-212 정식 등재** · INDEX 갱신

## 검증 로그

```
python -m pytest tests/test_scenario/ -q        -> 222 passed, 2 skipped
python scripts/arch_check.py --ci               -> 위반 0 (기존 WARN 1건은 src/tools/validation.py, 무관)
python scripts/overfit_check.py --ci            -> 신규 유입 없음
python -m scripts.scenario --dry-run            -> 군 16개, 시나리오 217건
python -m scripts.scenario --mock --only ...    -> 리포트/분석 산출 확인
```

### 전체 회귀 책임 소재 (clean worktree 대조 · CLAUDE.md 절차)

`git worktree add <dir> HEAD` 로 클린 사본을 만들고 **내 산출물만 얹어** 돌린 결과와,
병행 세션의 `src/` 수정이 포함된 작업 트리 결과를 대조했다.

| 대상 | 결과 |
|---|---|
| HEAD + **내 파일만** (clean worktree) | `26 failed, 6273 passed, 29 skipped, 5 errors` |
| 작업 트리 **내 테스트 제외**(`--ignore=tests/test_scenario`) | `26 failed, 6391 passed, 29 skipped, 5 errors` |
| 작업 트리 **내 테스트 포함** | `26 failed, 6593 passed, 31 skipped, 5 errors` |

**세 실행의 실패 집합이 완전히 동일하다**(양방향 diff — 신규 0 · 소실 0) → **본 작업이 만든
신규 실패는 0이다**(V14 충족). 통과 수 차이 202건과 skip 2건이 정확히 내 테스트 수와 같다.
26건은 HEAD 시점부터 있던 것이고(로컬 샌드박스 DB 행수·스냅샷·프롬프트 렌더 계열),
이 작업은 DB에 쓰기를 하지 않으며(D-003) 러너는 모의 서버로만 돌았다.

**중간에 27건이 한 번 관측됐다.** 원인 추적 중 `run_meta` 가 부르는 `load_config()` 가
`lru_cache(maxsize=1)`(`config.py:1383`)인데 내 테스트가 캐시를 채우고 치우지 않는 것을
발견해 격리했고, 이후 27건은 재현되지 않았다. **인과를 증명하지는 못했다** — 그 한 건이
캐시 누수였는지 불안정 테스트였는지는 미확정이며, 격리 자체는 인과와 무관하게 옳다.

**절차 실수(기록용)**: 첫 회귀를 `pytest ... | tail -8` 로 돌려 **실패 목록이 저장되지 않아**
원인 특정이 불가능했고 재실행이 필요했다. 장시간 회귀는 요약이 아니라 `FAILED` 전건을
파일로 남긴 뒤 집합 대조할 것.
