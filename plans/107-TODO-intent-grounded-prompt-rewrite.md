# 107. 의도 확정 후 프롬프트 재작성 — IntentFrame 기반 정규 질의(canonical query) 생성 · 원문/해석 이중 채널 · 소비자별 전환

> **작성일**: 2026-09-20
> **상태**: 계획(미구현) — 사용자 확정 게이트 G-1~G-8 대기(§11) · 파일명 `-TODO`
> **성격**: 현행 실측 + 구현 계획(코드 0건)
> **요청 취지(사용자 지시 원문, 2026-09-20)**: *"106-TODO-harness-intent-understanding.md 계획에서 이 프로젝트 안에서는 사용자 의도 파악 후 프롬프트를 재 작성하는 부분이 없다. 이런 처리를 진행하기 위한 계획을 정리하라."*
>
> **상위/연결 계획**: **`plans/106`**(의도 파악 하네스 — **H3을 이 계획으로 이관** · H1 되묻기 재개 · H2 해석 표시 · H8 실행 브리프와 같은 원천을 공유) ·
> `plans/79`(의도 추출 출력 계약 — E-3c `input_parser` 타입 계약 · 라우터 `sub_query_context`) · `plans/50`(멀티턴 압축 신호 M3) · `plans/75`(존 역질문 · LIMIT 원문 승격 · **자연어 재조합 금지 원칙** §4) ·
> `plans/73`(폼필 역질문 구조화 답변 · D-151) · `plans/88`(순차 의존 — 선행 결과 참조 sub_query) · `plans/90`(스코프 표시·승계) · `plans/102`(교차 시스템) · `plans/103`(3단 동등성)
> **관련 결정**: **D-004**(LLM 전용 라우팅 · 원문 키워드 분류 금지) · D-053(사본 금지) · **D-055·D-056**(지시어 후속 · hostname filter 주입) · D-066(LIMIT 원문 승격) ·
> D-127(과금 승인) · D-143·D-205(존 역질문·스코프 승계) · **D-151**(폼필 구조화 답변) · **D-153 후속1**(지시어 있을 때만 직전 엔티티 주입) · **D-154**(혼합 존 열거 재작성)
> **신규 결정 예약**: 없음 — G-1 확정 뒤 채번(`docs/02_decision.md` 「채번 이력」 등재 필요 · D-161 부기).
> **실측 기준**: 브랜치 `multiintent` HEAD `783a4b5`(2026-09-18) 작업 트리. 코드는 읽기만 했다(수정 0 · LLM 호출 0).
>
> **근거 표기**: **실측** = 파일을 열어 확인 · **코드** = `파일:라인` · **추정** = 검증하지 않은 추론

---

## 0. 요약

### 0.1 전제 정정 — "재작성이 없다"가 아니라 **"흩어져 있고, 정본이 없다"**

실측하면 재작성은 **이미 다섯 곳**에서 일어난다(§2.1). 없는 것은 **의도 확정 결과를 하나의 구조(정본)로 묶고, 거기서 프롬프트를 파생하는 단계**다.

| 현상 | 실측 |
|---|---|
| 재작성 지점 | ①라우터 `sub_query_context`(LLM · DB별 · 위치어 제거) ②플래너 `sub_query`(LLM · 태스크별 · 지시어→구체값) ③존 플레이스홀더·혼합 존 열거 치환(결정적 · D-154) ④지시어 hostname의 `filter_conditions` 주입(결정적 · D-056) ⑤`input_parser`의 맥락 반영 파싱(LLM · 멀티턴) |
| 공통 결함 | **정본 없음** — 각 지점이 서로 다른 입력에서 서로 다른 텍스트를 만든다 · LLM 재작성 2곳은 **자유 서술**이라 슬롯 누락·추가를 검출하지 못한다(오염 실측 2건) · **`user_query`가 원문/재작성 두 의미로 겹쳐 쓰인다**(`subagents.py:1255`) |
| 원문 의존 소비자 | 원문 표면어를 읽는 **결정적 판정 함수 10종**(§2.3)이 `user_query`를 입력으로 쓴다 — `user_query`를 재작성문으로 바꾸면 이들이 **조용히 다르게 동작**한다 |
| 되묻기 답변 병합 | 존 선택(`selected_db_ids`)·폼필(`form_fill_answers`) 두 종만 **구조화 필드**로 병합된다. 106 H1이 새로 묻는 슬롯(기간·지표·대상)의 답을 **원 질의와 합치는 일반 경로가 없다** |

### 0.2 결론

| 질문 | 답 |
|---|---|
| 무엇을 만드나 | **`IntentFrame`**(확정된 의도의 구조 정본 · 슬롯별 출처 표시) → 결정적 **병합**(발화·맥락·되묻기 답변·기본값) → 결정적 **렌더러**가 프롬프트 텍스트를 파생 → **검증**(슬롯 보존) → 소비자에게 전달 |
| 원문은 | **덮어쓰지 않는다.** 원문(`original_query`)과 해석(`canonical_query`)을 **별도 채널**로 두고, 소비자마다 어느 쪽을 읽을지 명시한다 |
| 재작성 방식 | **기본은 대체가 아니라 병기(augment)** — LLM 프롬프트에 "사용자 원문" + "확정된 해석(구조 블록)"을 함께 넣는다. 원문을 버리는 대체(replace)는 측정 후 소비자별로만(G-2) |
| LLM은 | 렌더는 **템플릿(LLM 0회)**. 자연문 다듬기는 옵션이며, 쓰면 **결정적 검증 통과분만** 채택하고 실패 시 템플릿으로 폴백 |
| 기존 재작성 5곳은 | 지우지 않는다. ③④는 병합 규칙으로 **흡수**, ①②는 LLM 산출을 유지하되 **프레임 대조 검증**을 붙이고, 장기적으로 프레임 파생으로 대체 여부 판단(G-5) |
| 순서 | W0 골든셋(오염 실측 사례 회귀 포함) → W1 프레임·병합(**섀도**) → W2 렌더·검증(섀도) → W3 소비자 1곳씩 전환 → W4 되묻기 재개 일반화(106 H1 연동) → W5 라우터·플래너 사후 검증 → W6 LLM 다듬기(옵션) |

### 0.3 권고 한 줄

**"프롬프트를 LLM에게 다시 쓰게 한다"가 아니라 "확정된 슬롯을 구조로 모으고, 프롬프트는 그 구조에서 찍어낸다."** 원문은 항상 옆에 남긴다.

---

## 1. 배경과 범위

### 1.1 왜 필요한가

- **106 H1(슬롯 게이트)이 되물은 뒤** — 답을 받아 원 질의와 합쳐 다시 실행해야 한다. 지금은 존 선택만 이 경로가 있다.
- **106 H2(해석 표시)·H8(실행 브리프)** — "무엇으로 해석했는가"를 보여주려면 해석이 **하나의 구조**로 존재해야 한다. 지금은 해석이 라우터 출력·플래너 출력·파서 출력에 흩어져 있다.
- **멀티턴 후속 질의** — "그 서버 메모리는?" 같은 발화는 직전 맥락과 합쳐져야 완결된다. 지금은 플래너 LLM이 자유 서술로 합치며, 그 과정에서 **오염이 두 번 실측됐다**(§2.4).
- **문헌·상용 사례** — Deep Research(명확화 → 상세 지시문 재작성 → 실행), Mediator-Assistant(모호 입력 → 명확 지시 재구성), RECAP(대화 → 과업 인지형 의도 표현). 공통점은 **실행 모델이 받는 입력이 "확정된 의도"** 라는 것이다(`plans/106` 부록 A B-1·B-2·P-6).

### 1.2 범위

- **대상**: 공통 전단(`context_resolver → input_parser → field_mapper`) 뒤, 라우팅·실행 앞의 **의도 확정 표현**과 그것을 소비하는 LLM 프롬프트.
- **비대상**: SQL 생성 로직 자체 · 라우터 분류 지시문(`plans/79` 트랙 A) · 되묻기 **판정**(`plans/106` H1 — 이 계획은 판정 **이후**의 병합·재작성을 소유).
- **이관**: `plans/106` 트랙 H3(후속 턴 독립 질의 재작성 — 조건부)은 **이 계획으로 이관**한다(`plans/INDEX.md` 이관 조항 · D-208 구조 — 이관처가 태그를 단다). 106 H3의 설계 제약(섀도 · 원문 슬롯 우선 · diff 감사)은 이 계획의 원칙 P-2·P-4·P-6으로 승계한다.

---

## 2. 현행 실측

### 2.1 재작성 지점 전수

| # | 지점 | 방식 | 입력 → 출력 | 위치(실측) | 비고 |
|---|---|---|---|---|---|
| R1 | 라우터 `sub_query_context` | **LLM 자유 서술** | 원문 → DB별 "순수 조회 의도"(위치·DB 정보 제거) | 프롬프트 규칙 `src/prompts/semantic_router.py:62-77` · 단일 DB에서 SQL 생성 입력으로 사용 `src/orchestration/subagents.py:1238-1242` · 멀티 DB `src/nodes/multi_db_executor.py:649` | 위치가 SQL WHERE로 누출되는 것을 막는 목적(§4.9.6 디멘전 7) |
| R2 | 플래너 `sub_query` | **LLM 자유 서술** | 원문 + 압축 맥락 → 태스크별 지시(지시어를 구체 값으로 치환 · 선행 결과 참조) | `src/prompts/intent_planner.py:57-79` · 맥락 블록 `src/orchestration/intent_planner.py:580-613` | 오염 실측 2건(§2.4) |
| R3 | 존 표기 치환 | **결정적** | 원문 → 'ㅇㅇ존' 플레이스홀더 치환 · 미선택 존 열거 제거(D-154) | `src/api/routes/query.py:855-870` · `src/utils/query_gen_common.py:1847` | 주석: *"결정적 문자열 치환(LLM 재해석 아님)"* |
| R4 | 지시어 hostname 주입 | **결정적(슬롯 수준)** | 직전 서버 hostname → `filter_conditions` | `src/orchestration/subagents.py:344-352` `_inject_demonstrative_hostname` | 텍스트가 아니라 **슬롯**을 고친다 — 이 계획이 일반화하려는 방식의 선례 |
| R5 | `input_parser` 맥락 파싱 | LLM 구조화 | 원문 + 직전 SQL·결과 요약·테이블 → 10키 | `src/nodes/input_parser.py:100-118 · 219-225` | 재작성이 아니라 **해석**이지만 맥락이 슬롯에 섞여 들어오는 첫 지점 |

### 2.2 원문 보존 장치 — 이미 여러 겹이다

| 장치 | 위치 | 의미 |
|---|---|---|
| `parsed_requirements.original_query` | `src/nodes/input_parser.py:124 · 254 · 355` | 파서가 원문을 복사해 둔다 |
| `original_user_query` | `src/orchestration/subagents.py:830-833` | *"호출부가 user_query를 sub_query로 덮어써도 게이트 판정·재전송 페이로드는"* 원문을 쓰기 위해 |
| LIMIT 원문 승격 | `src/state.py:125-127` · `subagents.py:770-776` | *"오케스트레이션이 user_query를 sub_query/sub_query_context로 교체하기 전에 원문으로 계산해 승격"* (Plan 75 §3 · D-066 후속) |
| 원문 우선 폴백 | `multi_db_executor.py:1389 · 1410 · 1999` | `parsed_requirements.get("original_query") or sub_query_context` |

→ **해석**: 저장소는 이미 "`user_query`가 덮어써진다"는 사실을 알고, **소비자마다 개별로** 원문을 되살려 왔다. 이 계획은 그 개별 대응을 **채널 분리 한 번**으로 바꾼다.

### 2.3 `user_query` 원문 표면어에 의존하는 결정적 함수 (재작성 시 영향권)

| 함수 | 정의 위치 | 호출(예) |
|---|---|---|
| `resolve_spike_request` | `src/domain/change_terms.py:105` | `src/nodes/query_generator.py:579` |
| `resolve_comparison_periods` | `src/utils/query_gen_common.py:291` | `query_generator.py:583` |
| `matched_filesystem_term` | `src/domain/change_terms.py:145` | `query_generator.py:590` |
| `resolve_absolute_threshold` | `src/domain/change_terms.py:164` | `query_generator.py:593` |
| `matched_other_metric_terms` | `src/domain/change_terms.py:152` | `query_generator.py:628` |
| `has_all_scope_keyword` | `src/utils/query_gen_common.py:456` | `multi_db_executor.py:2388` |
| `is_realtime_usage_query` | `src/utils/query_gen_common.py:603` | `subagents.py:814` |
| `resolve_effective_limit` | `src/utils/query_gen_common.py:538` | `query_generator.py:480` · `multi_db_executor.py:346` |
| `refers_to_demonstrative_server` | `src/utils/query_gen_common.py:1688` | `subagents.py:341` · `intent_planner.py:611` |
| 시간 범위 해석(`parsed_time_range` 병용) | `query_generator.py:485` | — |

- `user_query`를 읽는 파일은 **22개**(실측 grep — `output_generator` 6 · `multi_db_executor` 6 · `input_parser` 5 · `subagents` 4 …).
- **이 표가 §4.4 소비자 채널 표의 출발점이다.** 이 함수들은 **원문 채널에 남긴다**(재작성문에는 "최근 1시간(기본값)" 같은 렌더 문구가 섞여 표면어 판정이 흔들린다 — **추정**, W0에서 확인).

### 2.4 재작성 오염 실측 사례 (회귀 케이스로 고정할 것)

| 일자 | 사례 | 원인 | 현 조치 |
|---|---|---|---|
| 2026-07-16 | "은행존 알람" → "김포 은행 공동존…"으로 재작성 · gp 오라우팅 | 플래너 LLM이 **명시 위치와 직전 위치를 병합** | 명시 위치가 있으면 직전 위치 줄을 입력에서 제거(`intent_planner.py:585-590`) |
| 2026-08-04 | 전량 조회 후 "OS 종류…확인" → 샘플 **4개 서버로 축소** 재작성 | 직전 엔티티(상한 샘플)를 **스코프로 오인** | 지시어 있을 때만 직전 엔티티 주입(D-153 후속1 · `intent_planner.py:607-613`) |
| 2026-08-05 | 상호배타 재선택 후 원문의 **미선택 존 위치어가 SQL WHERE로 누출** | 원문이 그대로 흐름 | 결정적 열거 치환(D-154 · R3) |

→ 세 사례 모두 **"누가 이기는가(우선순위)"가 텍스트 안에 암묵적으로 있었다**는 것이 원인이다. 이 계획의 병합 규칙(§4.3)은 그 우선순위를 **코드로 명시**한다.

### 2.5 되묻기 답변의 전달 원칙 — **자연어 재조합 금지**

`src/api/schemas.py:39-49` 주석(실측):
- 존 선택: *"자연어 재조합 금지 원칙 — 선택 결과는 이 구조화 필드로만 전달되어 semantic_router/intent_planner의 결정적 고정으로 주입된다"*(Plan 75 §4)
- 폼필: *"자연어 재조합·LLM 파싱 없이 이 필드로만 전달되어 결정적 검증(존재성)·적용을 거친다"*(Plan 73 §11 · D-151)

→ **이 원칙이 이 계획의 설계를 결정한다.** 되묻기 답변을 원문 뒤에 문장으로 이어 붙여 LLM에 다시 파싱시키는 방식(문헌의 흔한 구현)은 **이 저장소 원칙과 충돌**한다. 답변은 **구조화 필드 → 프레임 슬롯**으로만 들어간다(§4.6).

---

## 3. 문제 정의

| ID | 문제 | 근거 |
|---|---|---|
| P1 | 확정된 의도의 **단일 정본이 없다** — 파서 10키 · 라우터 targets/sub_query_context · 플래너 tasks/sub_query가 각자 해석을 들고 있다 | §2.1 |
| P2 | LLM 재작성(R1·R2)은 **자유 서술**이라 슬롯 누락·추가·병합을 검출하지 못한다 | §2.4 |
| P3 | `user_query` 한 필드가 **원문과 재작성문 두 의미**를 오간다 — 소비자별 개별 복구가 누적 | §2.2 · §2.3 |
| P4 | 되묻기 답변의 **일반 병합 경로 부재**(존·폼필 전용 2종) | §2.5 |
| P5 | 재작성 전후를 **감사할 수 없다** — 무엇이 어떻게 바뀌었는지 기록 없음(106 G-P4 실측과 동일 원인) | `src/security/audit_logger.py:66-146` |
| P6 | 우선순위 규칙(명시 위치 최우선 · 지시어 조건부 승계 · 미선택 존 제거)이 **프롬프트 문장과 코드 주석에 분산** | §2.4 |

---

## 4. 설계

### 4.1 원칙

| # | 원칙 | 이유 |
|---|---|---|
| P-1 | **정본은 구조, 텍스트는 파생** — 재작성문은 언제나 `IntentFrame`에서 렌더된다 | P1·P2 해소. 텍스트 diff가 아니라 **슬롯 diff**로 검증 가능 |
| P-2 | **원문 불변** — 신규 코드는 `user_query`를 덮어쓰지 않는다. 원문 채널·해석 채널을 분리한다 | P3. §2.3 결정적 함수 보호 |
| P-3 | **병합은 결정적 코드** — 발화·맥락·답변·기본값의 우선순위를 함수로 명시 | P6 · §2.4 오염 원인 제거 |
| P-4 | **섀도 먼저** — 프레임·렌더는 먼저 기록만 하고 소비자는 바꾸지 않는다 | 회귀 0 출발(CLAUDE.md 플래그 원칙) |
| P-5 | **병기(augment) 기본** — 원문을 버리지 않고 해석 블록을 옆에 붙인다 | 원문 뉘앙스 보존 · 오염 시에도 원문이 남아 LLM이 교차 확인 가능(**추정** — W3에서 측정) |
| P-6 | **모든 재작성은 감사된다** — 프레임 해시 · 슬롯 출처 · 원문↔해석 diff | P5 |
| P-7 | **되묻기 답변은 구조화 필드로만** 프레임에 들어간다 | §2.5 기존 원칙 준수 |
| P-8 | 원문 키워드 분류 금지(D-004)는 그대로 — 프레임 구축은 **파서·라우터 구조화 산출 + 레지스트리**만 입력으로 쓴다 | D-004 |

### 4.2 `IntentFrame` 스키마 (초안)

```python
# src/domain/intent_frame.py  (domain 계층 · 순수 · 가칭)
class SlotValue(BaseModel):
    value: Any
    source: Literal[
        "utterance",            # 이번 턴 발화(파서)
        "clarification_answer", # 되묻기 구조화 답변(106 H1 · 존 선택 · 폼필)
        "context_inherited",    # 직전 턴 승계(context_resolver 압축 신호)
        "default",              # 기본값 정책(106 H1.5) — 106 H2 '가정' 표시 대상
        "registry",             # 레지스트리·결정표 파생(존→DB 등)
    ]
    evidence: str | None = None # 예: "previous_entities[0]" · "selected_db_ids"

class IntentFrame(BaseModel):
    frame_version: int = 1
    intent: str                                  # 라우터 허용 집합(plans/79 E-1)에서 파생 — 사본 금지(D-053)
    goal_class: str | None = None                # 106 H7 최종 목표 등급(확정 전 None)
    targets: dict[str, SlotValue] = {}           # zone / db_ids / hosts / ip ...
    metrics: SlotValue | None = None
    time_range: SlotValue | None = None
    filters: list[dict] = []                     # filter_conditions(자유 형식 유지 — E-3c D3)
    aggregation: SlotValue | None = None
    limit: SlotValue | None = None               # 원문 승격값(D-066) 우선
    output: SlotValue | None = None              # text / excel / word
    depends_on: list[str] = []                   # 순차 의존(plans/88) — 선행 task_id 참조
    unresolved: list[dict] = []                  # [{slot, reason}] — state.py 관례
    original_query: str                          # 원문(불변)
```

- **입력**: `parsed_requirements`(E-3c 타입 계약 — `src/nodes/schemas.py:20` `ParsedRequirements` 재사용) + 라우터 결과 + `conversation_context` 압축 신호 + 구조화 답변 필드 + 레지스트리.
- **E-3c와의 관계**: `ParsedRequirements`는 **LLM 출력 계약**, `IntentFrame`은 **병합 후 확정 의도**다. 전자를 후자로 **승격**하되 필드를 복사 정의하지 않는다(가능한 한 참조 — D-053).

### 4.3 병합 규칙 — 우선순위를 코드로

```python
def merge_frame(parsed_now, prior_frame, answers, context, defaults, registry) -> IntentFrame
```

| 순위 | 출처 | 규칙 | 기존 근거 |
|---:|---|---|---|
| 1 | **되묻기 구조화 답변** | 답한 슬롯은 무조건 확정 | Plan 75 §4 · D-151 |
| 2 | **이번 발화 명시값** | 발화에 있는 위치·식별자·기간은 승계값을 **대체**(병합 금지) | 2026-07-16 사례 · `intent_planner.py:585-590` |
| 3 | 직전 턴 승계 — **위치/DB** | 이번 발화에 위치 슬롯이 **비었을 때만** | D-143·D-205 스코프 승계 |
| 3′ | 직전 턴 승계 — **엔티티(서버)** | 파서/라우터가 **지시 참조**를 구조로 표시한 경우에만 · 상한 샘플은 **스코프로 쓰지 않음** | D-153 후속1 · 2026-08-04 사례 · D-055/056 |
| 4 | 기본값 정책 | 106 H1.5 표 · `source="default"`로 표시 | 106 H2 |
| 5 | 레지스트리 파생 | 존 → DB, 선택 DB → 존 라벨 | R3(D-154) 흡수 |

- **3′의 "지시 참조" 판정**: 현재 `refers_to_demonstrative_server`(원문 표면어)를 쓴다. 이 계획은 판정 결과를 **프레임 병합 입력으로 받기만** 하고 판정 방식은 바꾸지 않는다(D-004와의 관계는 현행 유지 — G-6에서 파서 구조화 필드로의 이전 여부 결정).
- **미선택 존 제거(D-154)**: 텍스트 치환이 아니라 **프레임의 `targets.zone`이 선택값으로 확정**되므로 렌더 결과에 미선택 존이 나타날 수 없다(구조적 해소). R3 텍스트 치환은 **원문 채널용으로 유지**(처리 현황 표시 등 기존 소비자).

### 4.4 이중 채널과 소비자 전환 표

| 채널 | 필드(신설) | 내용 | 쓰는 쪽 |
|---|---|---|---|
| **원문** | `original_query`(기존 값 승격 · 불변) | 사용자 입력 그대로(R3 치환본은 `display_query`로 분리 — G-7) | §2.3 결정적 함수 10종 · LIMIT 승격 · 감사 · 이력 few-shot 검색(`select_history_fewshot`) · 재전송 페이로드 |
| **해석** | `intent_frame` · `canonical_query` · `canonical_block` | 프레임과 그 렌더 | LLM 프롬프트(아래 표) · 106 H2 해석 표시 · 106 H8 브리프 |
| (호환) | `user_query` | **현행 의미 유지** — 이 계획은 새로 덮어쓰지 않는다 | 기존 전 소비자(전환 전까지) |

**LLM 프롬프트 소비자 — 전환 후보와 순서(W3)**

| 순서 | 소비자 | 현재 입력 | 전환 형태 | 선정 이유 |
|---:|---|---|---|---|
| 1 | `output_generator` 응답 서술 | `original_query or user_query`(`output_generator.py:643` 등) | **병기** — "확정된 해석" 블록 추가 | 조회 결과를 바꾸지 않는다(서술만) — 가장 안전한 첫 소비자 |
| 2 | `general_inference` | `user_query` | 병기 | DB 미접근 경로 · 영향 작음 |
| 3 | `query_generator` 프롬프트 | `user_query` + `parsed_requirements` | **병기** — 구조 블록을 `parsed_requirements` 옆에 | SQL 품질에 직접 영향 · `plans/61`·`67` 측정 체계로 회귀 판정 |
| 4 | 멀티 DB 대상별 입력 | `sub_query_context`(R1) | 프레임 파생 `canonical_query(target)`와 **대조 후 채택**(§4.7) | 라우터 LLM 산출 검증 |
| 5 | 플래너 태스크 입력 | `sub_query`(R2) | 태스크별 부분 프레임 첨부 + 대조(§4.7) | 오염 실측 경로 |

- 소비자 전환은 **설정 목록으로 1곳씩**: `CANONICAL_QUERY_CONSUMERS=output_generator,general_inference,...`.
- 전환은 소비자 코드가 `get_prompt_query(state, consumer=...)` 한 함수로 채널을 고르게 해 **분기를 한곳에 모은다**(현재 흩어진 `original_query or sub_query_context or user_query` 폴백 체인을 대체).

### 4.5 렌더러 — 한 프레임, 네 가지 출력

| 출력 | 형태 | 소비자 | LLM |
|---|---|---|---|
| **`canonical_block`** | 구조 블록(한국어 라벨 고정 순서) | LLM 프롬프트 병기용 | 0 |
| **`canonical_query`** | 완결 자연문 1문장 | 대체(replace) 모드 · 라우터·플래너 대조 | 0(템플릿) / 옵션 다듬기 |
| 해석 표시 문구 | 짧은 한 줄 + 가정 표시 | 106 H2 | 0 |
| 실행 브리프 | `ExecutionBrief` | 106 H8 | 0 |

```text
# canonical_block 예 (템플릿 · LLM 0회)
[확정된 해석]
- 의도: data_query / 목표: 현황 파악
- 대상: 공동존 김포 운영(polestar_cm_gp) · 서버 web01 (출처: 직전 턴 승계)
- 지표: CPU 사용률
- 기간: 최근 1시간 (기본값 — 사용자 미지정)
- 집계: 평균
- 산출: 표
[사용자 원문]
그 서버 CPU는?
```

- 라벨·순서·기본값 표기 문구는 **`src/prompts/`의 상수 1곳**(계층: prompts)에서 관리하고 `scripts/prompt_render_diff.py --ci`에 편입한다.
- **위치 정보 누출 방지(R1 목적 승계)**: `canonical_query(target)`는 대상 DB가 이미 고정된 뒤 쓰이므로 **위치 라벨을 넣지 않는 변형**을 제공한다(§4.9.6 디멘전 7과 같은 목적). 블록형은 "대상" 줄을 두되 SQL 생성 프롬프트 규칙이 "대상 줄은 라우팅 정보이며 WHERE에 쓰지 말 것"을 명시 — **프롬프트 변경이므로 Ask first**.
- 공용 계층에 폴스타 스키마 리터럴을 넣지 않는다(`overfit_check`) — 라벨은 레지스트리 표시명에서 가져온다.

### 4.6 되묻기 재개 경로 일반화 (106 H1 연동)

```
[턴 N]  질의 → 프레임 구축 → 106 H1 판정: missing_slot(time_range)
        → status="clarification" 페이로드에 {pending_frame_ref, asked_slot, options} 포함
        → pending_frame은 체크포인터 스레드 상태에 저장 (클라이언트로 원문 재조합 금지)
[턴 N+1] 사용자 선택 → 요청 body의 구조화 필드 clarification_answers={slot: value}
        → merge_frame(prior=pending_frame, answers=...) → 렌더 → 실행
```

- 신규 요청 필드 `clarification_answers: dict[str, Any] | None`(`src/api/schemas.py` — `selected_db_ids`·`form_fill_answers`와 같은 계열). 값은 **옵션 목록 중 선택**만 허용(자유 입력은 G-4) — 결정적 검증(존재성·허용 집합) 후 슬롯에 들어간다.
- 존 선택(`selected_db_ids`)은 **기존 경로 유지** — 프레임 병합에서는 `clarification_answer` 출처로 읽기만 한다(두 경로 공존 · 이행은 G-5).
- 체크포인터는 **델타 병합**이므로 `pending_frame`은 요청 스코프에서 명시 초기화·소거한다(CLAUDE.md Known Mistakes — 요청 스코프 상태 초기화).

### 4.7 LLM 재작성(R1·R2) 사후 검증

지우지 않고 **프레임과 대조**한다.

| 검사 | 방법(결정적) | 실패 시 |
|---|---|---|
| 슬롯 보존 | 프레임의 식별자·지표·기간 값이 재작성문에 **정규화 일치**로 존재 | `sub_query_context` 대신 `canonical_query(target)` 사용 + 사유 기록 |
| 위치 누출 | 대상이 고정된 뒤의 재작성문에 **미선택 존 위치어**가 없는가(표면어 목록은 기존 `LOCATION_HINT_TERMS` · `src/utils/query_gen_common.py:1624` 재사용) | 동일 |
| 스코프 축소 | 재작성문의 식별자 집합 ⊆ 프레임 대상 · **프레임에 없는 식별자 추가 금지** | 동일(2026-08-04 사례 직접 차단) |
| 병합 오염 | 이번 발화 명시 위치 ≠ 재작성문 위치 | 동일(2026-07-16 사례 직접 차단) |

- 이 검사는 **라우팅 결정(`db_id` 선택)을 바꾸지 않는다** — D-004(라우팅은 LLM)와 충돌하지 않는다. 바꾸는 것은 선택된 DB에 넘길 **텍스트**뿐이다.
- 운영 초기에는 **검사만 하고 교체하지 않는 섀도 모드**(`REWRITE_VERIFY_MODE=shadow`)로 실패율부터 잰다.

### 4.8 선택: LLM 자연문 다듬기

- 용도: `canonical_query`가 템플릿이라 어색해 LLM 성능이 떨어지는 경우에 한해(W3 측정 결과로 판단).
- 평면: 기존 워커/라우터 평면 1회(FabriX) — 신규 모델 없음. D-127(개발·검증 과금 경로 건별 승인).
- 채택 조건: §4.7 네 검사 **전부 통과**. 실패 시 템플릿. 결과는 `rewrite_trace.polish={used, passed, reasons}`로 기록.
- 기본 off: `REWRITE_LLM_POLISH_ENABLED=false`.

### 4.9 감사 — `rewrite_trace`

```json
{
  "frame_hash": "…", "frame_version": 1,
  "slots": {"time_range": {"value": "1h", "source": "default"}, "...": "..."},
  "renderer": "block_v1", "mode": "shadow|augment|replace",
  "consumers": ["output_generator"],
  "verify": {"r1": "pass", "r2": "fail:scope_shrink"},
  "polish": {"used": false},
  "prompt_rev": "…"
}
```

- `plans/106` H5(`clarification_decision`)·H5.2(`intent_signal`)·H9(`prompt_rev`)와 **같은 레코드 설계 차수**에 합친다(감사 스키마 1회 변경).
- 원문 전문은 기존 `user_request` 감사에만 있고, `rewrite_trace`에는 **슬롯 값과 출처만** 남긴다(PII 정책 · D-183).

### 4.10 계층 배치 (`arch_check` 기준)

| 모듈 | 계층 | 내용 |
|---|---|---|
| `src/domain/intent_frame.py` | domain | `IntentFrame`·`SlotValue`·`merge_frame`(순수) · 검증 함수(§4.7 순수 부분) |
| `src/prompts/canonical_query.py` | prompts | 렌더 템플릿·라벨 상수 |
| `src/nodes/intent_frame_builder.py` 또는 기존 노드 내 호출 | application | 파서·라우터 결과 → 프레임 구축 · state 기록 |
| `src/orchestration/subagents.py`·`intent_planner.py` | orchestration | R1·R2 대조 배선 · 태스크별 부분 프레임 |
| `src/api/schemas.py`·`routes/query.py` | interface | `clarification_answers` 필드 · pending_frame 재개 |

- **노드 신설 vs 기존 노드 내 호출**은 G-3. 권고는 **`field_mapper` 직후 공통 전단에서 1회 구축**(사다리 전 단 공통 — 1·2·3단 대칭) 후 라우터 결과로 **보강**(라우터 뒤 1회 갱신). 신규 노드는 `build_graph()` 배선 변경이 필요하다.

---

## 5. 실행 단위 (W0~W6)

| WU | 내용 | 산출물 | 과금 | 선행 |
|---|---|---|---|---|
| **W0** | 골든셋·회귀 케이스 | `testdata/routing_gold/rewrite.yaml`(가칭) — ①§2.4 오염 3건 재현 ②지시어 후속 ③위치 전환 ④되묻기 재개 ⑤멀티 의도·순차 의존(plans/88) ⑥멀티 DB(불변식: `plans/79` §1.1 멀티 DB 축소 금지). **파서·라우터 출력 스냅샷 동봉**으로 병합·렌더를 LLM 없이 채점 | 0 | — |
| **W1** | `IntentFrame` · `merge_frame` · 섀도 구축 | domain 모듈 · state 필드 `intent_frame` · 단위 테스트(W0 스냅샷) | 0 | W0 |
| **W2** | 렌더러 · 검증 · `rewrite_trace` | prompts 모듈 · 감사 필드 · `prompt_render_diff` 편입 | 0 | W1 |
| **W3** | 소비자 전환(§4.4 순서 1→5, 1곳씩) | `get_prompt_query()` · `CANONICAL_QUERY_CONSUMERS` | 실 파이프라인 채점은 건별 승인 | W2 |
| **W4** | 되묻기 재개 일반화 | `clarification_answers` · pending_frame · 프론트 카드(106 H1과 공동) | 0 | W1 · 106 H1 |
| **W5** | R1·R2 사후 검증(섀도 → 교체) | `REWRITE_VERIFY_MODE` | 0(검증) | W2 |
| **W6** | LLM 다듬기(옵션) | `REWRITE_LLM_POLISH_ENABLED` | 건별 승인 | W3 측정 결과 |

```
W0 ─▶ W1 ─▶ W2 ─┬─▶ W3 (소비자 1곳씩)
                ├─▶ W5 (R1·R2 섀도 검증)
                └─▶ W4 (106 H1과 동시)
                         W3 결과 ─▶ W6(필요 시)
```

### 5.1 설정 (전부 기본 off = 현행 비트 동일)

```bash
INTENT_FRAME_ENABLED=false            # 프레임 구축(섀도 기록 포함)
CANONICAL_QUERY_MODE=off              # off | shadow | augment | replace
CANONICAL_QUERY_CONSUMERS=            # 전환 소비자 CSV (§4.4)
REWRITE_VERIFY_MODE=off               # off | shadow | enforce  (R1·R2 대조)
REWRITE_LLM_POLISH_ENABLED=false
CLARIFICATION_ANSWERS_ENABLED=false   # W4 — 106 H1과 함께 켠다
```

- 기동 시 1회 해석(KV 캐시 원칙) · 설정 그룹 배치는 G-3.

### 5.2 수용 기준

| 단계 | 기준 |
|---|---|
| 공통 | off 경로 `pytest -q` 회귀 0 · `arch_check --ci` · `overfit_check --ci` · `prompt_render_diff --ci` 통과 |
| W1 | W0 전 케이스에서 **병합 결과 슬롯 = 기대 슬롯**(결정적) · §2.4 3건 **오염 재현 0** |
| W2 | 렌더 결과 슬롯 보존 100%(자기 검증) · 렌더 결정성(같은 프레임 → 같은 텍스트) |
| W3 | 소비자별 기존 평가 대비 **비열화**(`eval_routing` `multi_preserved` · `eval_text2sql` · `plans/94` 시나리오) — 기준값은 전환 전 기준선 측정 후 G-8 |
| W4 | 되묻기 재개 후 **재질의 없이** 실행 완료 · 답변 외 슬롯 불변 |
| W5 | 섀도 기간 R1·R2 검증 실패율 보고 → enforce 전환은 사용자 승인 |

---

## 6. 다른 계획·결정과의 관계

| 대상 | 관계 |
|---|---|
| **`plans/106`** | H3 **이관**(§1.2). H1이 판정 → **107이 재개·병합**. H2·H8은 107의 렌더러 출력을 쓴다(원천 공유 — 사본 금지). H5·H5.2·H9 감사 레코드와 `rewrite_trace`는 **한 차수**에 합친다 |
| `plans/79` | E-3c `ParsedRequirements`가 프레임의 입력 계약. 라우터 출력 계약(E-1·E-2)은 그대로 — 107은 라우팅 **결과 이후**만 다룬다. 트랙 A 프롬프트 측정(S-1)과 간섭하지 않도록 **프롬프트 변경(W3 병기 블록)은 S-1 이후** |
| `plans/50` | `conversation_context` 압축 신호가 3·3′ 순위의 입력 |
| `plans/75` | 존 역질문 재개·LIMIT 원문 승격·자연어 재조합 금지 원칙을 **일반화** — 기존 경로를 대체하지 않고 공존 |
| `plans/88` | 순차 의존 태스크는 `depends_on`으로 표현. 선행 결과가 후속 대상이 되는 슬롯은 `source="registry"`가 아니라 **실행 결과 참조**(`evidence="task:t1"`)로 둔다(G-6) |
| `plans/102` | 교차 시스템 질의는 `targets`에 시스템별 슬롯을 두고 `entity_locator` 결과를 `registry` 출처로 병합 |
| `plans/103` | 3단 LangGraph 동등성 — 프레임을 공통 전단에서 만들면 1·2·3단이 **같은 확정 의도**를 받는다(동등성의 전제가 하나 줄어든다) |
| D-004 | 프레임 구축·검증은 라우팅 결정을 바꾸지 않는다. 원문 키워드 분류 없음(§4.3 3′의 기존 표면어 판정은 현행 유지 · G-6) |
| D-053 | 렌더 출력 4종은 **같은 프레임에서 파생** — 해석 표시·브리프·재작성문이 서로 사본이 되지 않는다 |
| D-151 · Plan 75 §4 | 되묻기 답변은 구조화 필드로만(§4.6) |

---

## 7. 위험

| ID | 위험 | 영향 | 완화 |
|---|---|---|---|
| R-1 | 병기 블록이 프롬프트를 길게 만든다 | 토큰·지연 증가 · FabriX 입력 한도 | 블록은 슬롯 수만큼(수십 토큰 수준 — **추정**) · 소비자별 켜기 · `plans/50` B3 상한과 합산 측정 |
| R-2 | 병기 시 원문과 해석이 **충돌**하면 LLM이 원문을 따를 수 있다 | 해석 무효화 | 블록에 "해석은 확정값이며 원문보다 우선" 문구 — **프롬프트 변경이므로 Ask first** · W3 측정 |
| R-3 | 프레임 필드와 파서 자유 형식(`filter_conditions`) 간 불일치 | 병합 누락 | E-3c D3처럼 **중첩 항목은 느슨하게** 받고 상위 슬롯만 타입화 |
| R-4 | 소비자 전환 중 `user_query`·`original_query`·`canonical_query` 3채널 혼재 | 복잡도 증가 | `get_prompt_query()` 단일 진입 · 전환 완료 소비자 목록을 config로 가시화 · 잔여를 이 계획 태그로 추적 |
| R-5 | pending_frame 상태 누수(다른 요청으로 승계) | 잘못된 재개 | 요청 스코프 명시 초기화 · 재개 요청에 `pending_frame_ref` 일치 검증 |
| R-6 | 템플릿 자연문이 어색해 SQL 생성 품질 저하 | 품질 열화 | 기본은 병기(원문 유지) · replace는 측정 후 소비자별 · W6 다듬기 옵션 |
| R-7 | 경로 비대칭(3단만 적용) | 사다리 단별 거동 차이 | 공통 전단 1회 구축(G-3 권고) · 2단 R2 대조 포함 |

---

## 8. 되돌리기

- 모든 단계가 플래그 뒤에 있다. `CANONICAL_QUERY_MODE=off`면 프레임이 있어도 **어떤 소비자도 읽지 않는다**.
- `user_query` 의미를 바꾸지 않으므로(원칙 P-2) 되돌릴 때 **데이터 이행이 없다**.
- 감사 필드 추가는 **추가 전용**(기존 필드 변경 없음).

---

## 9. 착수 전 확인 · 미확인 사항

| ID | 항목 | 확인 방법 |
|---|---|---|
| U-1 | §2.3 결정적 함수가 재작성문(렌더 문구 포함)을 받았을 때 판정이 달라지는지 | W0 스냅샷에 원문/재작성문 두 입력을 넣어 함수별 결과 비교(비과금) |
| U-2 | `user_query`를 읽는 22개 파일의 **실제 의미**(원문 기대 / 재작성문 기대) 분류 | 파일별 호출부 정독 — 결과를 §4.4 표에 반영 |
| U-3 | 존 역질문 재개 시 원 질의·선택값이 체크포인터/요청 중 어디로 흐르는지 전 경로 | `routes/query.py:807 · 949 · 1130` 주변 정독 |
| U-4 | 프론트가 `clarification_answers` 제출용 카드를 존 역질문 카드 변형으로 그릴 수 있는지 | `src/static/` 클라이언트 분기 실측(106 U-9와 동일 작업) |
| U-5 | 병기 블록 추가 시 각 프롬프트의 토큰 증가량 | `prompt_render_diff` 렌더 결과 길이 비교 |
| U-6 | 순차 의존(`plans/88`)에서 선행 결과를 프레임 슬롯으로 참조할 때 D-203 선행 결과 게이트와의 순서 | `sequential_runner` 정독 |

---

## 10. 근거 문헌·사례 (상세는 `plans/106` 부록 A)

| # | 근거 | 이 계획에서 쓴 곳 |
|---|---|---|
| 106 P-6 | OpenAI Deep Research — 경량 모델이 명확화 후 **상세 지시문으로 재작성**, 연구 모델은 재작성본을 받음 | §1.1 · 렌더러의 "실행 모델은 확정 의도를 받는다" |
| 106 B-1 | Mediator-Assistant(arXiv:2602.07338) — 모호 입력을 명확한 지시로 재구성해 실행 모델에 전달 | §1.1 · 병합 규칙의 동기 |
| 106 B-2 | RECAP(arXiv:2509.04472) — 대화를 과업 인지형 의도 표현으로 재작성 · 의도 전환·다중 의도 | W0 케이스 범주 |
| 106 A-3 | SAGE-Agent(arXiv:2511.08798) — 불확실성을 스키마 슬롯 공간에서 다룸 | 프레임 = 슬롯 공간의 구조화 |
| — | Ma, X., et al. *Query Rewriting for Retrieval-Augmented Large Language Models.* EMNLP 2023 | https://aclanthology.org/2023.emnlp-main.322.pdf — **서지만 확인**(검색 결과) · 재작성기를 별도 단계로 두는 선례 |
| — | *The Case for Intent-Based Query Rewriting*(VLDB 2025 Workshop · arXiv:2511.20419) | **서지만 확인** · 의도 기반 재작성 |

> 문헌은 "재작성이 유효하다"는 방향을 주지만, **LLM 자유 재작성**을 기본으로 한다. 이 계획이 **구조 → 템플릿 렌더 + 원문 병기**를 기본으로 택한 것은 문헌이 아니라 **이 저장소의 오염 실측(§2.4)과 자연어 재조합 금지 원칙(§2.5)** 때문이다.

---

## 11. 사용자 확정 게이트

| ID | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | 채택 범위 | (a) W0~W2 섀도까지 (b) + W3 소비자 1·2(서술 계열) (c) + W4 되묻기 재개 (d) 전체 | **(b)+(c)** — 조회 결과에 영향 없는 소비자와, 106 H1에 반드시 필요한 재개 경로부터 |
| G-2 | 재작성 적용 형태 | 병기(augment) / 대체(replace) / 후속 턴만 대체 | **병기 기본** · 대체는 측정 후 소비자별 |
| G-3 | 프레임 구축 위치 · 설정 그룹 | 공통 전단 신규 노드 / 기존 노드(`field_mapper` 뒤 · 라우터 뒤) 내 호출 | 기존 노드 내 호출(배선 변경 최소) + 라우터 뒤 1회 보강 |
| G-4 | 되묻기 답변 입력 형태 | 선택지만 / 선택지 + 자유 입력(자유 입력은 파서 재호출) | **선택지만**(자연어 재조합 금지 원칙) |
| G-5 | 기존 재작성 R1·R2 처분 시점 · 존 선택 경로 이행 | 섀도 검증 유지 / enforce 전환 / 프레임 파생으로 대체 | 섀도 → 실패율 보고 후 결정 |
| G-6 | 지시 참조 판정(표면어 함수)을 파서 구조화 필드로 옮길지 · 순차 의존 슬롯 표현 | 현행 유지 / 이전 | 현행 유지(이 계획 범위 밖) |
| G-7 | R3 치환본을 `display_query`로 분리할지 | 예/아니오 | 예 — 원문 채널을 순수 원문으로 유지 |
| G-8 | W3 소비자별 비열화 판정 기준값 | 기준선 측정 후 결정 | — |

---

## 부록. 변경 이력

| 일자 | 버전 | 내용 |
|---|---|---|
| 2026-09-20 | v1 | 최초 작성 — 재작성 지점 5곳·원문 보존 장치·원문 의존 결정적 함수 10종·오염 실측 3건 실측 · `IntentFrame`·병합 우선순위·이중 채널·렌더러 4종·R1/R2 사후 검증·되묻기 재개 일반화 설계 · W0~W6 · 게이트 G-1~G-8 · `plans/106` H3 이관 수용 |
