# 157. 제니퍼 APM 벤치 잔여 결함 3건 — 존 사전 게이트의 APM 가로채기 · APM 대상 멀티턴 미승계 · 벤치 드라이버 파싱 답 어긋남

> **작성일**: 2026-10-13 · **v1.1**(2026-10-14 — W0~W4 완료 · 커밋 없음)
> **상태**: **완료**(2026-10-14 · 커밋 없음) — 게이트 권고안 일괄 채택 · 결함 3건 해소(E1 129/129 · 악화 0) · 결정 **D-334**(130 M-5 대상 칸 흡수) · 매뉴얼 U-55 · 통합 모듈 회귀 rc 0(원래 실패만) · 열린 질문은 §10 「남긴 것」
> **요청 원문(2026-10-13)**: *"결함은 후속 계획을 만들고 필요한 것은 설치하고 나머지는 권고에 맞게 진행하라."* → 계획 작성까지만.
> **발견 경위**: `plans/152`(제니퍼 APM 조회 벤치마크 · D-323) 외부망 E1 — `plans/159` 교정 뒤 턴 정확도 **97.7%(126/129)**. 남은 실패 3턴은 모두 159 이전 기준 run에서도 실패하던 결함이다(152 §9). 가짜 LLM이 정답 계획을 넣어도 나므로 few-shot으로 고칠 수 없다(`분류: 코드 교정 목록`).
> **성격**: 현행 실측 + 결정 충돌 정리 + 설계 + 작업 분해. 제품 결함 2건 + 측정 도구 결함 1건.
> **상위·인접 계획**: `plans/152`(발견 · 재측정 수단 §10) · `plans/159`(직전 교정 — 분 단위 기간) · `plans/130`(APM 대상 계약 · **M-5 멀티턴 승계 잔여** — 결함 ②와 겹침) · `plans/147`(APM 소스 사다리 · D-322) · `plans/132` v1.5(2단 다중 시스템 존 게이트 task 위임)
> **관련 결정**: D-323(벤치 — 이 계획이 측정 정확도를 고친다) · D-322(소스 승계 · 130 M-5 흡수는 **소스 칸만**) · D-319(명시 무존 소스 존 역질문 생략 · v1.5 부기 task 위임) · D-251(2단 기준 경로) · D-274(APM 게이트웨이 경계) · D-313 ①(APM 창 = 요청 해석) · D-296 ④(대상 상한 없음) · D-161 ②(경로 폐기 판정 4항) · D-162(신규 플래그 기본 off) · D-255(매뉴얼) · D-303(회귀) · D-127·D-240(실 LLM = 로컬 MLX)
> **실측 기준**: 2026-10-13 `main` HEAD `6810336` **+ 미커밋 작업 트리**(plans/159 변경 — `src/domain/time_*.py`·`src/orchestration/apm_query.py`·`src/prompts/input_parser.py` 등). `file:line`은 이 시점 값이다 — W0에서 다시 대조한다. 벤치 산출물: 159 E1 재측정 run(`e1b` — 턴별 판정·`trace.jsonl`).
> **병합 후 주의(2026-10-13)**: 작성 직후 `origin/main` 병합(`6402907`)으로 원격 커밋 `dbd8090`(「APM 벤치 코드 수정」 — 설정 에코 운영자 토큰 `_admin_token` 추가 · 내부망 W8 1회차에서 기동 검증 401 발견)이 들어와 `scripts/apm_bench/driver.py` 줄 번호가 약 +19 밀렸다(`:629` → 약 `:648` · `:687` → 약 `:706`). W0에서 다시 대조한다. 같은 커밋으로 152 W8 1회차가 내부망에서 시도됐음을 알 수 있다 — W4 재측정 전 W8 결과 반출본이 있으면 대조한다.
> **소스 id 변경 주의(2026-10-13 · `plans/161` · D-329)**: 소스가 `b0`·`cm_prd`·`cm_stg`로 바뀌며 `APM-SRC-*` 시나리오 문구가 바뀌었다(`APM-SRC-common`→`APM-SRC-cm-prd` · ambiguous 0턴 「김포랑 여의도 …」·1턴 답 「공동존 운영」 · inherit 0턴 「김포 {{inst_c}} 최근 1시간 이벤트」(cm_prd)). 아래 재현 문구·소스 id(`common`·`bank`)는 당시 기록이다 — 재현은 새 문구로 한다.
> **계획 시작 SHA**: `4073f7f`(2026-10-14 착수 시 확정 · 152·156·164 통합 진행과 같은 base)
> **회귀 대상 파일**(확정): `src/orchestration/{apm_query,result_aggregator}.py` · `src/api/routes/query.py` · `src/api/schemas.py`(주석) · `src/state.py`(주석) · `spec/SPEC-apm-question-coverage.md` · `scripts/apm_bench/driver.py`(W1·W2분 — 164와 공유) · `scripts/manual/content/user.md` · `src/static/manual/user.html` · `src/static/manual/help_index.json` · 테스트: `tests/test_api/test_plan157_zone_gate_apm.py` · `tests/test_orchestration/test_plan157_{apm_target_inherit,verify}.py` · `tests/test_orchestration/test_plan132_round3_verify.py` · `tests/test_scripts/test_apm_bench_driver_parse{,_verify}.py`

---

## 0. 요약

| # | 결함 | 성격 | 턴 | 근본 원인(한 줄) | 권고 처리 |
|---|---|---|---|---|---|
| ③ | 벤치 드라이버가 소스를 답하는 턴에 **답 문장**으로 가짜 파싱 답을 만든다 | 측정 도구 | `APM-SRC-ambiguous#1` | 라우트는 소스 답을 받으면 질의를 **원 질문으로 되돌려 재파싱**하는데, 가짜 LLM의 파싱 답은 답 문장(「은행존 제니퍼」)에서 만들어져 기간 슬롯이 사라진다 → `clarify:time` | **W1(맨 앞)** — 측정 정확도에 영향 |
| ① | 존 사전 게이트(라우트)가 「데이터 서버별 도메인 배치…」를 가로채 존을 되묻는다 | 제품(조건부) + 벤치 환경 | `APM-JSERVER-domains#0` | 라우트 게이트가 「활성 시스템 ≥ 2 ∧ 2단」일 때만 task 게이트에 미룬다. 벤치 본체는 **활성 DB가 0건**이라 시스템이 `apm` 1개 → 위임 불성립 | **폐쇄망(2단 · 폴스타 DB 활성 · APM 활성)에서는 재현되지 않는다** → 제품 불변 + 벤치 환경 정합(G-1 (a)) |
| ② | 앞 턴의 제니퍼 대상(인스턴스)이 후속 턴에 이어지지 않는다 | 제품 | `APM-SRC-inherit#1` | 승계 재료 `previous_entities`는 서버 식별자(hostname·server_name) 전용이고 인스턴스 이름만으로는 `TargetRef`가 승계 경로에서 탈락(`apm_only_target`) — **plans/130 M-5 잔여** | 130 잔여로 보내지 않고 **157에서 처리**(G-2 권고) — 소스 승계(147)와 같은 스레드 키에 대상 칸 추가 |

수용: 교정 뒤 E1 재측정에서 **3턴 해소 · 악화 0**(`--compare`), 목표 턴 정확도 **100%(129/129)**. 결함 ①이 벤치 환경 정합으로 처리되면 제품 코드 변경은 ② 한 군데(+ 조건부 매뉴얼)다.

---

## 1. 현행 실측

### 1.1 결함 ③ — 벤치 드라이버 (재현 · 원인)

**재현**(`e1b` run `trace.jsonl`): 시나리오 `APM-SRC-ambiguous`는 2턴이다.

| 턴 | 보낸 말 | 기대 | 관측 |
|---:|---|---|---|
| 0 | 「은행 쪽 {인스턴스} 응답시간 30분」 | 소스 되묻기(`clarify: source`) | 통과 — 분해가 `sources: []`로 내 소스 사다리 단 4 되묻기 |
| 1 | 「은행존 제니퍼」 | 원 질문을 은행 소스로 재실행(`apm.app_health` · 창 30분) | `clarify=time`(`slot_status: absent`) · 호출 0 → 창·판정 `window` 축 실패 |

**원인**: `scripts/apm_bench/driver.py:629`(`_run_scenario`)

```
ctx.fake.set_parse(parse_from_turn(turn.expect, turn.prompt, prompt, mapping=values))
```

가짜 입력 파싱 답은 **이번 턴의 보낸 말**에서 만든다. `parse_from_turn`(`fake_llm.py:193-215`)은 기대 창이 `range:N`이면 `time_slot(prompt)`로 기간 슬롯을 채우는데 답 문장 「은행존 제니퍼」에는 기간 표현이 없다 → `time_expr: null`. 그런데 라우트는 직전 턴의 소스 되묻기 대기가 있고 원문이 소스 단어 + 좁은 꼬리뿐이면 **질의를 원 질문으로 바꿔 다시 파싱**한다(`src/api/routes/query.py:1350` `_apm_source_text_answer` → `turn_query = pending["query"]` · D-322 ④). 재파싱된 원 질문(「…응답시간 30분」)에 대해 가짜 LLM이 준 파싱 답에는 슬롯이 없어 시간 되묻기로 끝난다.

> 같은 드라이버의 **자동 답 루프**(`driver.py:610-624` — 소스 되묻기가 정답이 아닌 턴에서 대상 자리표의 소스로 자동 답)는 주석대로 「파싱 답은 그대로 둔다」(턴 앞에서 한 번 건 파싱 답이 원 질문 것이라 맞다). 어긋나는 것은 **시나리오가 답 턴을 따로 둔 경우**뿐이다.

제품 결함이 아니다 — 실 LLM에서는 라우트가 원 질문을 재파싱하므로 정상이다. 측정 도구 결함이며 `window` 축을 거짓 실패로 만든다.

### 1.2 결함 ① — 존 사전 게이트

**재현**: `APM-JSERVER-domains#0` 「데이터 서버별 도메인 배치랑 설정값 알려줘」 → 응답 「조회할 존이 지정되지 않았습니다. …」(`clarify_kind: zone` · `elapsed 2ms` · 그래프 미진입 · `source_miss`/`code:route_zone_gate`).

**원인 경로**(`src/api/routes/query.py`):
1. `_zone_clarification_or_none`(`:2159-`)은 후속 턴이 아니고(`checkpoint_state is None`), `is_full_scan_query`(`src/utils/query_gen_common.py:515` — `_ALL_QUERY_KEYWORDS`에 「서버별」)가 참이고 질의에 「서버」가 있으며, 위치 표면어가 없고, 존 없는 소스 단독 지목(`names_only_zoneless_sources`, `src/routing/db_scope.py:296`)도 아니면 존 되묻기 페이로드를 낸다. 「데이터 서버별」은 제니퍼의 데이터 서버를 뜻하지만 질문에 「제니퍼」라는 소스 단어가 없다.
2. 유일한 우회는 `_zone_gate_deferred_to_tasks`(`:1656-1689`, 호출 `:2199`): **2단(`current_ladder().tier == "intent_orchestration"`) ∧ 활성 시스템 ≥ 2**일 때 라우트 게이트를 task 게이트에 미룬다. 활성 시스템 = `registry.systems_of(활성 DB id)` ∪ (서버 엔터티 간선 `owner`로 등장하는 활성 비DB 시스템 — 제니퍼는 간선 E1·E7의 owner `apm`).

**이 맥 실측**(저장소 루트에 `.env` 없음 — `.env.bak-inline-comments`·`.env.example`만 있다. 키 이름만 적는다): 벤치 본체는 프로파일 `tier2_intent`(`config/scenarios/profiles.yaml:91` — `ENABLE_DEEPAGENTS_PACKAGE`·`ENABLE_INTENT_ORCHESTRATION`·`ENABLE_SEMANTIC_ROUTING`)와 `MCP_SOURCE_ENDPOINTS`·`MCP_SOURCE_TOKENS`만 주입한다(`driver.py:687-717`) — **`ACTIVE_DB_IDS`는 주입하지 않는다**. 같은 설정으로 `load_config()`를 직접 열어 실측: 활성 DB `[]` · 활성 비DB `{'apm': 'apm_query'}` → 시스템 1개 → `False` 반환 → 라우트가 존을 묻는다.

**폐쇄망 재현 판정**(코드 + 설정 키만으로 · 152 보고의 「재현 미확인」을 닫는다):

| 폐쇄망 구성 | 시스템 수 | 사다리 | 라우트 게이트 | 결과 |
|---|---:|---|---|---|
| `ACTIVE_DB_IDS`에 폴스타 DB(`polestar_*`) + 제니퍼 엔드포인트 설정(2단 기본 — D-251) | 2(`polestar`, `apm`) | 2단 | 위임 | **재현 안 됨**(`names_only_zoneless_sources` 불요 · task 게이트가 대상이 전부 폴스타 존일 때만 묻는다 — APM task는 존 대상이 아니다) |
| 위 + ITAM 활성 | 3 | 2단 | 위임 | 재현 안 됨 |
| 사다리가 1단(`deep_agent`)·3단(`semantic_router`)으로 확정(예: 로컬 `.env` 실측 2026-10-07의 1단 — CLAUDE.md) | — | **2단 아님** | **위임 불성립** | **재현됨** — 시스템 수와 무관하게 라우트가 묻는다 |
| 제니퍼 엔드포인트 미설정(APM 비활성) + 폴스타만 | 1 | 2단 | 위임 불성립 | 게이트가 정상 동작(존을 가진 시스템의 전량 질문이므로 맞다) |

즉 **폐쇄망의 기준 경로(2단 · 폴스타 DB 활성 · 제니퍼 활성)에서는 이 결함이 나지 않는다.** 나는 곳은 (a) 활성 DB 0건인 벤치 환경(= E1 측정이 폐쇄망 구성을 못 닮은 것) (b) 1·3단 확정 환경이다. 로컬 `.env` 값은 읽지 않았다(파일이 없다) — W0에서 `.encenv`·폐쇄망 값 확인은 사용자 몫으로 둔다.

> 판정의 한계: 폐쇄망 `.env`의 실제 `ACTIVE_DB_IDS`·사다리 플래그 값은 이 맥에서 읽을 수 없다. 위 표는 **코드가 그 두 입력으로 결정한다**는 사실에 근거한다. W0에서 폐쇄망 기동 로그 1줄(`record_ladder_resolution`)과 활성 DB 목록으로 확인 요청서를 만든다.

### 1.3 결함 ② — APM 대상 미승계

**재현**(`e1b`): `APM-SRC-inherit` 2턴 — 0턴 「공동존 {인스턴스} 최근 1시간 이벤트」(대상 해소 1건 · `selected: [인스턴스]`) → 1턴 「느린 거래도 10분치」(기대 `target: ref` = 앞 턴 대상 승계 · 소스 `common`). 1턴 관측: 분해 `targets: []` · `untargeted: true` · 고지 「대상 서버 미지정 — 전체 인스턴스 2개(호스트 2대) 조회」 · 호출이 두 호스트 전부. 판정 `target` 축 `untargeted: true · other_target: true` → `target_wrong`/`code:resolve_apm_targets`. (소스 승계는 동작 — D-322 단 3으로 `common` 유지.)

**원인**:
- `src/orchestration/apm_query.py:797` `resolve_apm_targets` → 공용 `resolve_targets`(`src/utils/prior_targets.py`)의 승계 재료는 ① 선행 task 결과 `prior_targets`/`prior_rows` ② 이번 턴 식별자 ③ **`previous_entities`**(`context_resolver`가 직전 결과에서 뽑는 hostname·server_name 계열 — `src/nodes/context_resolver.py:126`).
- 제니퍼 인스턴스 대상은 분해 `targets: [{text, kind: instance}]`로 오고 해소 결과는 인스턴스 이름이다. 호스트 식별자를 가진 `TargetRef`만 승계된다 — 인스턴스 이름만 있는 항목은 `src/utils/prior_targets.py:453-456`에서 `REASON_APM_ONLY`(`apm_only_target`)로 탈락한다(주석: 「130 M-5」). 즉 승계 재료에 인스턴스가 **들어가지 않는다**.
- 설계상 직전 대상은 지시어 후속에서만 잇는다(`scoped_targets` `:~830` · `explicit_targets` — 「대상 없는 설정·관리 질문이 직전 서버로 조용히 좁혀지지 않게」). 그러나 「느린 거래도 10분치」는 지시어가 없는 **생략형** 후속이다.

**130과의 관계(겹침 확인)**: `plans/130-WIP-…` 머리 「잔여」에 **M-5 멀티턴 승계(`apm_source_id` 패싯)**가 있고 A-2(`TargetRef`가 `apm_instance_name`만으로 성립) 항목이 이 탈락의 출처다. 이후 `plans/147`(D-322)이 M-5의 **소스 칸(`apm_source_id`)만** 흡수했다(130 통지 2026-10-08 · 스레드 키 `apm_source_scope.last_target_sources`). **대상 칸(인스턴스 이름 승계)은 130에 그대로 남아 있다** → 결함 ②는 130 잔여 M-5와 **정확히 겹친다**. G-2로 올린다.

---

## 2. 결정 충돌

| 결정 | 관계 | 처리 |
|---|---|---|
| D-322 ④ / `plans/130` M-5 | 결함 ②의 소유 계획. 소스 승계는 구현, 대상 승계 잔여 | G-2 |
| D-319 · `plans/132` v1.5 (라우트 게이트 위임 조건) | 결함 ① 위임 조건을 바꾸는 안(G-1 (b))은 D-319 부기 개정 사안 | G-1 — 권고안은 제품 불변이라 충돌 없음 |
| D-251 (2단 기준) | 1·3단에서의 라우트 게이트 동작은 의도(비교 arm) | G-1 (b) 기각 근거 |
| `default_views`·`scoped_targets` 설계 의도(「대상 없는 질문이 직전 서버로 조용히 좁혀지지 않게」) | 생략형 승계는 이 의도와 긴장 | G-3에서 트리거를 좁힌다 |
| D-323 ⑥ 벤치 판정 5축 | ③ 수정은 판정 기준을 바꾸지 않고 입력(가짜 파싱 답)만 바로잡는다 | 충돌 없음 |

---

## 3. 사용자 확정 게이트 (권고안 = 첫 항)

> **확정 2026-10-14 · 사용자 *권고안 일괄 채택*** — G-1 (a) · G-2 (b) · G-3 (a) · G-4 (a) · G-5 (a) · G-6 (a). 결정 D-334(신규 · G-2 (b) — 130 M-5 대상 칸 흡수).

| # | 질문 | **권고** | 대안 |
|---|---|---|---|
| **G-1** | 결함 ① 존 게이트를 어떻게? | **(a) 제품 코드 불변 + 벤치 E1 환경 정합** — E1 본체 기동 시 `ACTIVE_DB_IDS`를 폐쇄망형(폴스타 DB 1~2개 id)으로 주입해 활성 시스템 ≥ 2가 되게 한다. 폐쇄망 기준 경로에서 재현되지 않으므로 제품 결함이 아니라 **측정 환경의 부정합**이다. 회귀 테스트로 「2단 · 폴스타 + 제니퍼 활성 → 라우트가 APM 질문을 묻지 않는다」를 고정 | (b) 라우트 게이트 위임 조건에 「APM 활성이면 사다리 불문 위임」 추가(1·3단 비교 arm 동작이 바뀜 — D-251·D-319 개정) · (c) 「데이터 서버」·「도메인 배치」 등 제니퍼 용어를 존 없는 소스 표면어로 인식(`names_only_zoneless_sources` 확장 — 유사어 오염 위험) |
| **G-2** | 결함 ②(APM 대상 미승계)를 130 잔여로 이관할까, 157에서 처리할까? | **(b) 157에서 처리** — 130은 잔여가 많고(실 PG/DB2 E6·업무 실데이터·3단 `targets` 미운반 등 내부망 의존) 오래 WIP이다. 이 결함은 외부망에서 벤치로 재현·검증되고 147이 소스 칸을 같은 스레드 키에 이미 실었다. 130 본문은 불변, **통지 한 줄**(「M-5 대상 칸 → 157」)만 메인 세션이 단다 | (a) 130 잔여로 이관(157은 ①③만) — 벤치 100% 목표가 130 일정에 묶인다 |
| **G-3** | 어떤 후속 턴에 직전 APM 대상을 잇는가(트리거)? | **(a) 결정적 조건 AND**: ① 같은 스레드에 직전 턴 APM 대상 기록이 있고 ② 이번 턴 task에 `targets`·호스트 식별자·업무/인스턴스 텍스트가 없고 ③ 선택된 보기가 **대상별 보기**(`default_views`가 비목록으로 보는 것 — `apm.slow_tx` 등. 인스턴스 목록·설정·관리 보기는 제외) ④ 이번 턴이 소스 변경 단어를 말하지 않음. 승계 시 고지 1줄(「직전 대상 {n}건을 이어서 조회했습니다」 — 사용자가 알아야 하는 좁힘이므로 의무) | (b) 지시어(「그 서버」)에만 — 현행 유지(벤치 시나리오의 「…도 10분치」는 계속 실패 · 시나리오를 바꾸는 쪽으로 해결) · (c) 분해 LLM이 `kind: "prior"`를 내게 함(LLM 비결정성 의존 — Known Mistakes 위반 소지 · 비권고) |
| **G-4** | 승계 재료의 저장 단위는? | **(a) 스레드 키 `apm_source_scope`에 `last_targets: [{text, kind, source_id}]` 칸 추가**(147이 `last_target_sources`를 둔 같은 곳 · 요청 스코프 초기화 규칙 동일) — `TargetRef` 계약(`prior_targets.py`)은 건드리지 않는다(폴스타 소비처 영향 0 · 130 A-2 완화는 130에 남는다) | (b) `TargetRef` 검증기 완화(130 A-2) + 폴스타 소비처 전수 점검 — 범위가 커 별건 |
| **G-5** | 결함 ③ 수정 방식? | **(a) 드라이버가 답 턴의 가짜 파싱 답을 직전(대기 중) 질문 기준으로 만든다** — 직전 턴 `expect.clarify == "source"`이면 그 턴의 `prompt_template`/`prompt`/`expect`로 `parse_from_turn` 호출. 제품 코드 무변경 | (b) 시나리오 YAML에 답 턴용 `parse_of: <턴>` 칸 추가(스키마·카탈로그 검증 변경 — 과함) |
| **G-6** | 기능 플래그를 둘까? | **(a) 두지 않는다** — ②는 결함 교정이고 G-3의 좁은 AND 조건이 현행 동작을 바꾸는 범위를 한정한다. 단 **목록·설정 보기·지시어 없는 단독 질문은 비트 동일**을 테스트로 고정. D-162 예외 근거를 결정 본문에 남긴다 | (b) `APM_PRIOR_TARGET_INHERIT_ENABLED` 기본 off(off = 현행 비트 동일) — 벤치 E1은 켜서 측정 |

게이트 확정 후 결정 등재(메인 세션): ②는 D-322 ④의 대상 칸 확장으로 **신규 D 1건**(G-2 (b) 채택 시) — 130 M-5 흡수 사실을 본문에 적는다.

---

## 4. 설계 (권고안 기준)

### 4.1 결함 ③ — 드라이버 (`scripts/apm_bench/driver.py`)

- `_run_scenario`에서 직전 턴의 `expect.clarify == "source"`이면 이번 턴(답 턴)의 가짜 파싱 답을 **직전 턴의 원 질문**으로 만든다:
  `parse_from_turn(prev.expect_with_this_turn_window?, prev.prompt_template, prev_prompt, mapping=values)` — 기대 창은 **답 턴의 `expect.window`**(시나리오가 `range:30`을 답 턴에 둠)를 쓰되 기간 표현(`time_slot`)은 **원 질문 문장**에서 뽑는다. 호스트 자리표 필터도 원 질문 템플릿 기준.
- 이 로직은 작은 순수 함수 하나(`parse_for_turn(scenario, idx, values)`)로 빼 단위 테스트한다(드라이버 전체 기동 없이).
- 자동 답 루프(`:610-624`)는 변경하지 않는다.
- 가드: 답 턴이 직전 턴 없이 `clarify: source`를 갖는 시나리오가 있는지 W0에서 전수(`testdata/apm_bench/scenarios.yaml`) — 있으면 같은 규칙 적용 대상에 넣는다.

### 4.2 결함 ① — 벤치 환경 정합 (G-1 (a))

- `_server_overrides`(`driver.py:687`)의 E1 주입값에 `ACTIVE_DB_IDS`(폴스타 존 DB id 1~2개)를 더한다. E1은 DB를 쓰지 않으므로 **연결 시도가 기동·질의 경로를 막지 않는지**(DBHub 계열 MCP·direct 백엔드 지연) W0에서 실측한다 — 막으면 대안으로 레지스트리만 읽는 `ACTIVE_DB_IDS` + `DB_BACKEND` 더미 조합을 쓰거나 G-1 (b)로 재상정한다.
- E2는 본체 `.env`가 정본이라 손대지 않는다.
- 회귀 테스트(`tests/test_api/test_plan157_zone_gate_apm.py`): 2단 + 활성 DB 폴스타 + APM 활성 구성에서 `_zone_clarification_or_none`이 「데이터 서버별 도메인 배치랑 설정값」을 **통과(None)**시킴 · 활성 DB 0건(APM만)에서는 현행대로 되묻는다(의도 동작 고정 — 존 없는 시스템만 활성이면 존 질문이 틀린 질문이 아니라 로컬 구성 한계임을 주석).
- 이 결과 `JSERVER-domains#0`이 task 게이트까지 가도 **APM task는 존 대상이 아니라 되묻지 않음**을 종단으로 확인한다.

### 4.3 결함 ② — 대상 승계 (`src/orchestration/apm_query.py` + 스레드 키 갱신부)

- 저장: APM task가 **조회한 대상**(해소된 인스턴스 `text`·`kind`·소스)을 `apm_source_scope.last_targets`에 기록한다(147이 `last_target_sources`를 기록하는 지점과 같은 곳 — W0에서 위치 확정). 요청 스코프 값이 아니라 스레드 키이므로 체크포인터 델타 병합 규칙을 따른다(소스 칸과 동일 처리 · 만료는 소스 칸과 동일).
- 읽기: 대상 해소 전에 G-3 AND 조건을 판정해, 참이면 `targets`에 저장된 항목을 **분해가 낸 것처럼** 채워 기존 대상 해소(`resolve_*` 인스턴스 경로)를 그대로 태운다 — 새 해소 경로를 만들지 않는다(130 M-1·M-2 검색기 재사용 · 소스는 D-322 사다리가 정한 것 안에서만).
- 고지: 승계가 일어난 턴에 의무 고지 1줄(neutral). 새 소스 단어가 말해지면 승계하지 않는다(G-3 ④).
- 가드: 이번 턴이 새 대상을 말하면 승계를 덮어쓴다 · 목록/설정 보기는 승계하지 않는다 · `reset_apm_source_scope`(× 해제)이면 `last_targets`도 비운다.
- 3단 경로에서 `targets`가 운반되지 않는 문제(130 잔여)는 이 계획 밖 — 2단 기준 경로만 다룬다.

### 4.4 매뉴얼 (D-255)

결함 ②는 사용자 동작이 바뀐다(「…도 10분치」처럼 대상을 생략한 후속 질문이 앞 대상을 잇는다). `scripts/manual/content/user.md`의 제니퍼 후속 질문·소스 승계 절에 한 문단 + 고지 문구를 더하고 빌드한다(`features.yaml` → `content/*.md` → 캡처 → `build` → `pytest tests/test_manual`). ①③은 대상 아님. 관리자 매뉴얼 영향 없음(확인).

---

## 5. 수용 사례

| # | 입력 | 기대 |
|---|---|---|
| A-1 | 벤치 `APM-SRC-ambiguous` 2턴(소스 되묻기 → 「은행존 제니퍼」) | 1턴: 원 질문 재파싱에 기간 슬롯 유지 · `apm.app_health` 30분 창 · 소스 `bank` · 판정 통과 |
| A-2 | 소스 되묻기가 정답이 아닌 시나리오(자동 답 루프) | 동작 불변(회귀 0) |
| B-1 | E1 `APM-JSERVER-domains#0` | 존 되묻기 없음 · `apm.jennifer_server` 계열 호출 · 통과 |
| B-2 | 활성 DB 0건 + APM만, 같은 문장 | 현행 동작 유지(고정 테스트) |
| C-1 | 「공동존 {인스턴스} 최근 1시간 이벤트」 → 「느린 거래도 10분치」 | 1턴: 대상 = 앞 턴 인스턴스 · 소스 `common` · 창 10분 · 고지 「직전 대상 … 이어서」 · fleet 전체 조회 아님 |
| C-2 | 「제니퍼 인스턴스 리스트」 → 「응답시간」(대상 없음) | G-3 ③: 목록 보기 뒤 — 승계 조건은 **직전 턴이 대상별 조회였을 때만**(목록 턴은 `last_targets`를 쓰지 않음) |
| C-3 | 앞 턴 대상 A → 「B 응답시간」 | B로 덮어씀(승계 안 함) |
| C-4 | 앞 턴 대상 A(공동존) → 「은행존 제니퍼 느린 거래」 | 새 소스 단어 → 승계 안 함(G-3 ④) |
| C-5 | `reset_apm_source_scope` 후 같은 후속 | 승계 안 함 |

---

## 6. 작업 분해

| Wave | 내용 | 완료 기준 |
|---|---|---|
| **W0** 실측 재대조 | §1 인용 재대조(159 미커밋 변경 포함 — 시작 SHA 확정) · 폐쇄망 확인 요청서(활성 DB·사다리 확정 로그 — 사용자) · E1 `ACTIVE_DB_IDS` 주입 가능성 실측(연결 시도 영향) · `last_target_sources` 기록 지점 · 답 턴 시나리오 전수 · 인스턴스-only 탈락 경로(`prior_targets.py:453`) 소비처 | §7 진행 기록 · 게이트 확정 반영 |
| **W1** 벤치 드라이버(결함 ③ · **맨 앞**) | `parse_for_turn` 순수 함수 + 드라이버 배선 · 단위 테스트(답 턴 분기 · 자동 답 불변) · `--dry-run`·`--coverage` 무영향 확인 | 테스트 통과 · E1 `SRC-ambiguous` 통과(부분 재측정 `--only` 또는 전체) |
| **W2** 존 게이트(결함 ①) | G-1 (a): E1 주입값 + 회귀 테스트(§4.2) · 실측 결과에 따라 대안 상정 | 테스트 통과 · `JSERVER-domains#0` 통과 |
| **W3** 대상 승계(결함 ②) | `last_targets` 기록·읽기·고지·가드(§4.3) · 단위 + 종단(`build_graph()` · 2단 · 가짜 LLM) 테스트(§5 C-1~C-5) | 테스트 통과 · 목록/설정 질의 비트 동일 |
| **W4** 재측정·매뉴얼·결정 | 아래 §7 재측정 → `--compare` · MLX 대표 소수 1회(아래) · 매뉴얼(§4.4) · 결정 등재 · INDEX/130 통지(메인 세션) · 계획 끝 `regress.py` 1회 | 3턴 해소 · 악화 0 · 게이트 통과 |

**MLX**: 바뀐 경로 대표 **1턴**만 — 두 평면 `mlx`(127.0.0.1)를 `python -m scripts.bench --show-env`/`--preflight`로 확인한 뒤 `python -m scripts.apm_bench --run --env e1 --llm mlx`로 `APM-SRC-inherit`(2턴) 1회(D-240 「바뀐 프롬프트·선택 경로만」). 하나라도 과금 평면이면 실행하지 않는다. 이 계획은 프롬프트를 바꾸지 않는다 — 분해가 `targets: []`를 내는 실제 LLM 동작에서 승계가 서는지가 확인 대상이다.

---

## 7. 검증 · 회귀

- **재현·수용(외부망 E1 · 가짜 LLM)**: `python -m scripts.apm_bench --run --env e1 --probe --out <out>/w157`(152 §10 ③ 형식) → 기준 run(`e1b`, 97.7%)과 `python -m scripts.apm_bench --compare <e1b> <w157> --out <out>/cmp157`. 수용: **`APM-SRC-ambiguous#1` · `APM-SRC-inherit#1` · `APM-JSERVER-domains#0` 해소 · 다른 턴 악화 0 · 턴 정확도 129/129(목표)**. 5축 중 `window`·`target`·`source` 변화를 보고에 적는다.
- **Wave 중간**: implementer는 자기 테스트 파일만 `pytest` + `python scripts/regress.py --no-tests --files …`(정적 게이트만). `src/state.py`·공개 시그니처를 건드리면 `[웨이브 회귀 필요]`가 뜰 수 있다(그 Wave 끝 모듈 회귀).
- **계획 끝 1회**(D-303 · team-lead): `python scripts/regress.py --base <계획 시작 SHA> --files <바꾼 파일…>` — 도구 마지막 범위 줄(`범위: 모듈 단위 — 전체 미실행`)을 보고에 그대로 옮긴다. 전체 회귀는 사용자 요청 시만(`[전체 회귀 권고]` 블록이 나오면 그대로 옮긴다).
- 정적 게이트: `arch_check.py --ci`(결함 ②의 `src/orchestration/apm_query.py` 변경은 계층 내) · `overfit_check.py --ci`(독스트링·테스트 문장에 폴스타 스키마 리터럴 금지 — 신규 테스트는 시나리오 자리표 사용).
- `tests/test_manual`(매뉴얼 갱신 시) · 벤치 테스트 `tests/test_scripts/test_apm_bench_*.py`.

---

## 8. 위험

- **E1 환경 변경(G-1 (a))이 다른 턴의 경로를 바꿀 수 있다**: 활성 DB를 주입하면 분해·소스 사다리·존 게이트 분기가 달라진다(활성 시스템 ≥ 2 → 위임 경로, 폴스타 DB 존재로 `names_only_zoneless_sources`·위치 표면어 해소 등). 전체 E1 재측정과 `--compare`로 악화 0을 확인한다. 막히면 G-1 재상정.
- **승계 오작동**: 조용히 직전 대상으로 좁히는 것은 설계가 막아 온 위험이다(`scoped_targets` 주석). G-3의 AND 조건 + 의무 고지 + 덮어쓰기·해제 가드로 한정하고 C-2~C-5 음성 사례를 고정한다. 검증 단계에서 「승계 후 사용자가 전체를 보고 싶을 때」 경로(새 단어로 덮어쓰기 · 칩 해제)가 매뉴얼에 닿는지 본다.
- **호출 예산**: 159 효과로 E1 공동존 APM 호출이 478 → 883으로 늘었다(분 단위 질문이 막히지 않고 조회에 닿은 결과 — 정상). 152 E2(내부망) 소스당 호출 예산 §3.7(**3,000회**) 대비 E1 기준 29% 수준이나 E1 호출은 목 환경 값이라 E2 환산이 아니다. E2는 `--skip-heavy` 1회차 + 실제 소스 수·턴 수로 예산 가드(`guard.py`)가 센다. 이 계획의 영향: ②는 대상 미승계 턴의 **전체 인스턴스 fleet 조회(호출 다수)를 대상 1건 조회로 줄인다** — 호출 감소 방향. ①③은 호출 수 불변(③은 오히려 실패 턴의 0호출이 정상 호출로 바뀌어 소폭 증가). 폐쇄망 1회차 전 W4에서 E1 소스별 호출 수를 `run.json`에서 다시 읽어 `plans/152` 부록 A 예산 점검표와 대조한다.
- **판정 변경 오해**: ③은 입력 교정이지 판정 완화가 아니다 — `window` 축 정의·합격선 불변.
- **폐쇄망 확인 불가분**: ①의 폐쇄망 비재현 판정은 설정 키·코드 분석이다. 사다리가 1·3단인 환경이 폐쇄망 일부에 있으면 그 환경에서는 제품 결함으로 남는다(G-1 (b) 재상정 조건).

---

## 9. 의사결정 정합

- 유지: D-251(2단 기준 · 1·3단 라우트 동작 불변) · D-319/132 v1.5(위임 조건) · D-322(소스 사다리 — 대상 칸은 확장, 단계 순서 불변) · D-296 ④(대상 상한 없음 — 승계 대상 수도 상한 없음) · D-313 ①(창은 요청 해석 — 승계는 창에 영향 없음) · D-323(판정 5축 불변).
- 충돌: 없음(권고안 기준). G-1 (b)·G-3 (c) 선택 시 각각 D-319·Known Mistakes(LLM 의존) 충돌 → 재협의.
- G-2 (b) 선택 시 `plans/130` M-5 잔여를 157이 흡수 — 130 머리 상태·잔여 문구는 메인 세션이 통지 행으로만 갱신(본문 불변 원칙).

## 10. 진행 기록

- 2026-10-13 — v1.0 작성(코드 0). 실측: `e1b` run 3턴 기록 · 결함별 원인 `file:line` · 존 게이트 폐쇄망 재현 판정(표 §1.2 — 2단·폴스타 활성·제니퍼 활성이면 비재현) · 130 M-5 대상 칸 잔여 확인.
- 2026-10-14 — **통합 진행**(사용자 *"apm_bench와 관련된 계획 152, 156, 157, 164 등의 계획을 종합적으로 판단하여 구현하라."* · 통합 team-lead · 시작 SHA `4073f7f`). **순서 근거**: W1(드라이버 측정 결함)을 맨 앞에 두어야 이후 E1 비교가 믿을 만하고, W1·W2가 `driver.py`를 고쳐 164(같은 파일의 반출 위생)보다 먼저 끝냈다. W3(제품 승계)는 156 제품 트랙과 병렬. W4 재측정은 156·164와 합쳐 최종 E1 1회로 대신했다.
- 2026-10-14 — **W1**(결함 ③): `driver.parse_for_turn` 순수 함수 — 소스 되묻기 답 턴의 가짜 파싱 답을 직전 원 질문 문장으로 만든다(기대 창은 답 턴 expect) · 자동 답 루프 불변 · 테스트 `test_apm_bench_driver_parse{,_verify}.py`. T-1(자동 답 소스 오배정)은 코드로 고치지 않고 164 키트 경고만 둔다.
- 2026-10-14 — **W2**(결함 ① G-1 (a)): E1 주입값 `ACTIVE_DB_IDS=polestar_cm_gp,polestar_cm_yd`(E2 불변 · 설정 에코 대조) · 실측: DB 연결은 기동 헬스체크 2건 `Connection refused`뿐, 질의 경로 무영향 · 회귀 테스트 `test_plan157_zone_gate_apm.py`(2단+존 DB+APM 통과 · 활성 DB 0건은 현행 되묻기). `APM-JSERVER-domains#0` 통과.
- 2026-10-14 — **W3**(결함 ② · D-334): `apm_source_scope.last_targets` 기록·결정적 AND 승계·의무 고지·덮어쓰기/해제 가드 · 응답 미노출(`_apply_source_fields`) · SPEC §7.5 · 매뉴얼 U-55. 교정 3라운드 — 3차: LLM이 같은 소스를 다시 낸 경우(`inherited` · 근거 단어 빈 `named`)를 소스 변경으로 보지 않음(`_same_sources` · D-334 부기). 알려진 한계(`zone_clarification` 답 턴 잔존)는 사용자 확정으로 그대로 둔다(D-334 주의 ④).
- 2026-10-14 — **W4 재측정**(152·156·164 포함 최종 E1 1회 · 가짜 LLM · `--probe`): run `20261014-161040-e1-4073f7f` **129/129(100%)** · 축 전부 100% · 관문 통과 · `--compare base_4073f7f` holdout 40/40 불변 · dev 96.6→100 · **악화 0** · 3턴 해소(`APM-JSERVER-domains#0` source_miss · `APM-SRC-ambiguous#1` window_wrong · `APM-SRC-inherit#1` target_wrong). 이탈: `--skip-heavy` 없이 무거운 등급 포함(기준 run과 같은 조건).
- 2026-10-14 — **MLX 1회**(두 평면 mlx 확인 · Qwen3.5-9B-OptiQ-4bit · 127.0.0.1:18471 · 자기 PID 종료): `--only APM-SRC-inherit` run `20261014-161101-e1-4073f7f` 턴 0/2 — 둘 다 `arg_hallucination`(n=100 · interval_minute — few-shot 사안) · target 축 2/2. #1은 ymp67_o로 좁혀졌지만 D-334 고지가 없다 — 4073f7f부터 있던 `previous_entities` hostname 승계(`resolve_apm_targets`) 경로라 D-334 결함 아님(#0이 호스트 식별자로 조회해 `last_targets` 미기록).
- 2026-10-14 — **계획 끝 모듈 회귀**(`regress.py --base 4073f7f --files <머리 16개>`): rc 1 — 실패 50 = `[원래 실패]` 48(라우트·스트림 테스트 — plan119·122_t4·stream 계열 · 4073f7f에서도 실패) + `[새 테스트 실패]` 2(`test_apm_bench_w2b.py` — 회귀 도중 164 W2b 교정 2라운드가 같은 테스트를 고치던 경합 · 단독 재실행 2 passed · 164 재회귀 733 passed 0 failed) · arch·overfit 통과 · ruff 225·mypy 219(이번 diff 줄 0) · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행` · 산출물 `logs/regress/20261014-163545-80908`.
- 2026-10-14 — **통합 모듈 회귀 1회**(메인 지시 — 152·156·157·164 기준 대비 전체 변경 63파일 · `regress.py --base 4073f7f --files …` · 모든 교정 반영 뒤): rc 0 · 본체 통과 9,467 · 실패 48(전부 `[원래 실패]` — 라우트·스트림·plan122_t4 등 4073f7f에서도 실패) · arch·overfit 통과 · ruff 293·mypy 219(이번 diff 줄 0) · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행` · 산출물 `logs/regress/20261014-164850-7453`.
- `plans/130` 통지: M-5 대상 칸 잔여는 D-334·이 계획이 흡수(130 본문 불변 · 통지 1줄).
- **남긴 것(열린 질문)**: ① 기존 `previous_entities` hostname 승계도 좁힘 고지를 붙일지(권고: `hostnames`가 conversation_context에서만 왔을 때 같은 고지) ② MLX `arg_hallucination`(n=100 · interval_minute)은 152 few-shot 루프 대상 ③ 3단 경로 `targets` 미운반(130 잔여).

## 11. 변경 이력

| 일자 | 버전 | 내용 |
|---|---|---|
| 2026-10-14 | v1.1 | W0~W4 완료 · D-334 · 상태 완료 · 파일명 태그 제거(`157-TODO-…` → `157-…`) |
| 2026-10-13 | v1.0 | 최초 작성 — 결함 3건 · 게이트 G-1~G-6 · W0~W4 |
