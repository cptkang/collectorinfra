"""text2sql·라우팅 설정 — 동의어 매칭·결정적 SQL 조합·라우터·복합 질의·의도 프레임.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class SynonymMatchConfig(BaseSettings):
    """동의어 매칭 보강 설정 (Plan 61 트랙 B / D-075).

    전 기능 기본 OFF — 활성 전에는 기존 정확일치 매칭 경로가 바이트 단위로
    무변경이어야 한다(회귀 0). 접근 경로: cfg.synonym.* (env_prefix="SYNONYM_").
    """

    fuzzy_match: bool = False            # E5-1 유연 매칭(자모·편집거리·부분어) on/off
    value_retrieval: bool = False        # E5-2 실측 값 검색·주입 on/off
    # E5-4 임베딩 의미 검색(D-084): 정확→퍼지(E5-1)→임베딩 계단의 마지막 단. ON이어도
    # 백엔드 요건(아래) 미충족·의존성(pip install .[semantic]) 미반입이면 경고 1회 후 무매칭.
    semantic_match: bool = False
    # 임베딩 백엔드: local(기본 — sentence-transformers 모델 인프로세스 CPU 상주) |
    # vllm(별도 vLLM 서버에 임베딩 모델을 로딩해 OpenAI 호환 /v1/embeddings로 호출)
    semantic_backend: Literal["local", "vllm"] = "local"
    # local 백엔드: 오프라인 반입한 sentence-transformers 다국어 모델 경로. 빈 값 = 미가용.
    semantic_model_path: str = ""
    # vllm 백엔드: vLLM /v1 엔드포인트(예: http://vllm-host:8000/v1)와 서빙 임베딩 모델명.
    # 둘 중 하나라도 빈 값이면 미가용.
    semantic_vllm_base_url: str = ""
    semantic_vllm_model: str = ""
    # vllm 백엔드 SSL 인증서 검증(D-060 계열 — 사설 인증서 vLLM 대응 시 false)
    semantic_vllm_verify_ssl: bool = True
    # 임베딩 코사인 확정 임계(미만은 확정 매핑 아닌 후보 제시/LLM 위임)
    semantic_confidence_min: float = 0.65
    match_confidence_min: float = 0.85   # 퍼지 매칭 신뢰도 임계(이하는 확정 매핑 아닌 후보 제시)
    # E5-3: D-051 유사어 보완 테이블 상한(기존 schema_analyzer 모듈 상수 하드코딩을 config로 노출).
    # 낮추면 토큰↓·리콜↓ — 고정 규칙이 아니라 E1 하네스로 튜닝하는 파라미터(Death of Schema Linking).
    max_synonym_supplement_tables: int = 15
    # E5-3 사전 위생·거버넌스(D-075): 유사어 메타(등록출처·사용횟수·최종사용일·신뢰도) 추적 +
    # 동일 용어 다중 컬럼 충돌 우선순위 규칙. 기본 OFF — 활성 전 유사어 저장/매칭 경로 무변경(회귀 0).
    governance: bool = False
    # 장기 미사용 감쇠/정리 임계(일). governance ON + 명시적 prune 호출 시에만 적용.
    decay_days: int = 180

    model_config = {"env_prefix": "SYNONYM_", "env_file": ".env", "extra": "ignore"}


class Text2SQLConfig(BaseSettings):
    """Text-to-SQL 결정적 조합 설정 (Plan 61 트랙 C / D-076).

    전 기능 기본 OFF — 활성 전에는 기존 SQL 생성 경로가 바이트 단위로 무변경이어야
    한다(회귀 0). 접근 경로: cfg.text2sql.* (env_prefix="TEXT2SQL_").

    트랙 A(다중 후보 E2~E4) 착수(Plan 61 D-073/D-074)로 semantic_fallback 기본값을
    `candidate_then_human`으로 전환했다(계획 §5·§9-9). multi_candidate가 OFF이면
    candidate_then_human/human은 현행 LLM 자유생성으로 우아하게 강등한다(회귀 0 — 커버리지
    밖 라우팅은 semantic_compose가 ON일 때만 발동하므로 기본 OFF 상태 경로는 무변경).
    """

    semantic_compose: bool = False       # E6 결정적 조합 경로 전체 스위치
    # 커버리지 밖 라우팅(3단 폴백, §9-9). multi_candidate OFF이면 candidate_then_human/human은
    # 현행 LLM 자유생성으로 강등(회귀 0).
    semantic_fallback: Literal["candidate_then_human", "llm", "human"] = "candidate_then_human"
    fallback_confidence_min: float = 0.0  # 3단 폴백 2차 게이트(트랙 A 선택 신뢰도 임계, 미달 시 사람검토 강등)

    # === 트랙 A: 다중 후보 생성·선택 (E2~E4, D-073/D-074) — 기본 OFF, 옵트인 증분 ===
    multi_candidate: bool = False        # E2/E4 다중 후보 경로 전체 스위치
    candidate_count: int = 3             # 후보 수 N (비용 무제한 §9-4 — E1로 이득 곡선 측정 후 상향)
    # 다양화 방식. multi_prompt(현행/divide&conquer/실행계획 CoT — §9-2 사용자 결정) | temperature(보조)
    candidate_strategies: Literal["temperature", "multi_prompt"] = "multi_prompt"
    complexity_gate: bool = False        # E3 복잡 질의만 다중 후보(품질 목적 — 비용 컷 아님 §9-4)
    # E4 선택 전략. hybrid(결과일관성 1차 + LLM 쌍대비교 병용 — §9-3) | consistency | llm
    selection: Literal["consistency", "llm", "hybrid"] = "hybrid"

    # === Plan 63 P3(D-090): 무선언(프로필/시맨틱 모델 없음) DB의 LLM 어휘 매핑 폴백 ===
    # 폴스타 등 프로필 보유 DB는 선언 우선(어댑터/프로필)으로 동작 불변. 프로필이 없는 DB는
    # 공통 LLM 경로가 SQL을 직접 생성한다(P4-2 검증). 이 플래그가 ON이면 무선언 DB의 기간
    # 표현에 **범용 기간 해석 힌트**(스키마의 시간 컬럼으로 매핑 — 폴스타 리터럴 없음)를 추가
    # 주입한다. 기본 OFF = 추가 주입 없음(호출 증가 0, 현행 동작 무변경).
    generic_llm_mapping: bool = False

    # === Plan 67 트랙 N / N2(D-133): 질의 이력 검색 기반 few-shot 동적 선택 ===
    # ON이면 폴백(LLM 1방) 프롬프트의 고정 few-shot(프로필 query_examples) 대신 검증된
    # 질의-SQL 이력에서 유사도 상위 예시를 골라 주입한다. 기본 OFF = 프롬프트 바이트 무변경.
    query_history_fewshot: bool = False
    query_history_top_k: int = 3           # 주입할 상위 예시 수(토큰 증가 상한)
    # 어휘·퍼지 유사도 확정 임계(미만은 무적중 → 기존 고정 few-shot 유지). 임베딩 승격은
    # IP-4 계측 후 별도 판단(계획서 §3.3-N2 "측정 선행").
    query_history_min_score: float = 0.35

    # === Plan 67 트랙 S / S2(D-128): 단계적 컬럼 도출 루프 ===
    # ON이면 NL→SMQ를 1방 선택 대신 도구 기반 다회 탐색 루프로 도출한다(요구 분해 → 필드별
    # 카탈로그·유사어·값 확인 → SMQ 누적). 커버리지 판정·SQL 조립은 결정적 유지(D-076·D-067).
    # 기본 OFF = 기존 1방 경로 바이트 무변경.
    stepwise_derivation: bool = False
    stepwise_max_rounds: int = 6            # tool-calling 라운드 상한(무한 루프 차단)
    stepwise_max_tool_calls: int = 24       # 누적 tool 호출 상한(토큰 폭증 차단)
    # 루프 전체 타임아웃(초). per-call 타임아웃만으로는 다회 왕복을 막지 못한다
    # (Known Mistakes "장시간 실행 경로는 전체 타임아웃 가드 필수").
    stepwise_timeout_seconds: float = 60.0

    # === Plan 67 트랙 N / N4(D-133): 계층 taxonomy 상위어 모호성 처리 ===
    # ON이면 상위어만 언급한 질의("사용률 보여줘")에서 SMQ가 하위 하나로 좁혀졌을 때 나머지
    # 형제를 결정적으로 채운다(전체 제시 — 조용한 오답 방지). 하위어를 명시한 질의는 판정에서
    # 걸러져 동작 불변이고, 기본 OFF면 확장 자체가 없다(회귀 0). 상위어 선언은 카탈로그 정본
    # (config/knowledge/{db_id}/catalog.yaml의 taxonomy)에 있다.
    hypernym_ambiguity: bool = False

    # === Plan 67 R1 잔여(D-131): 폴스타 프롬프트 잔여 블록 정본 렌더 ===
    # ON이면 SQL 예제의 EAV 속성·지표·알람 조인·등급 CASE를 카탈로그 정본에서 렌더하고,
    # `hi` 설정 피벗 서브쿼리 조인 키를 값 컬럼(ipaddress) → 서버 식별자(id)로 교정한다
    # (같은 게이트로 값 컬럼 조인 validator도 등록 — 예제와 검증은 함께 움직인다).
    # 기본 OFF = 프롬프트 sha256 무변경·validator 7종 유지(회귀 0).
    prompt_knowledge_render: bool = False

    # === 활성 알람 결정적 조립 (기본 OFF 옵트인, 2026-09-02 폐쇄망 실측 기반) ===
    # ON이면 "활성 + 심각도 + 목록/건수" 알람 질의의 SQL을 코드가 직접 조립한다(LLM 우회,
    # 미인식 질의는 현행 LLM 폴백). LLM 생성이 턴마다 다른 축소 조인(resource_type INNER,
    # ACK 필터)을 만들어 건수가 요동하던 문제(같은 질의 3회 → B0 46/1170/1174)의 근본
    # 해결 — 폼필 피벗 D-068과 동일 판단. 기본 OFF = 현행 경로 무변경.
    alarm_deterministic: bool = False

    # === Plan 69 P4-3: 멀티 DB 경로 검증 강화 (기본 OFF 옵트인) ===
    # ON이면 멀티 DB 경로가 간이 검증(_validate_sql_simple) 대신 단일 경로와 같은
    # full validator(테이블·컬럼 존재, EAV 금지 조인, 어댑터 훅 포함)를 소비한다.
    # 거부 사유는 로그로 계측 — 위양성 실측 후 기본 전환은 별도 판단(계획서 §0.3-4).
    multi_full_validation: bool = False

    # === Plan 69 P3-2: 단일/멀티 경로 대칭 (기본 OFF 옵트인) ===
    # ON이면 한쪽 경로에만 있던 주입을 반대편에도 넣는다 — (a) 멀티 어댑터 시스템 템플릿
    # (b) 단일 스키마 한정 규칙(D-057) (c) 멀티 값 인덱스·폼필 피벗 블록 (d) 멀티 선행
    # 스코프 결정적 전달(D-099). 갭별 발동은 "[경로대칭] (x)" 로그로 실측한다.
    # 기본 OFF = 양 경로 프롬프트 바이트 무변경(계획서 §0.3-3).
    path_parity: bool = False

    # === D-159: 멀티 DB 경로 관련 테이블 게이트 (공동존 토큰 폭증 근본 수정) ===
    # ON이면 멀티 경로 스키마를 프로필 allowed_tables + 이번 질의 매칭 유사어 테이블로
    # 좁힌다(단일 경로 schema_analyzer 게이트의 멀티 대칭 — Plan 52 §1.5 미이행분).
    # 프로필 부재 DB·필터 결과 공집합은 전량 유지(현행 불변). 기본 ON — OFF는 폐쇄망
    # P1 장애(공동존 cm_gp 136,707tok > FabriX 95,232 한도) 재현을 의미하므로
    # 비상 복귀용 kill-switch로만 쓴다.
    multi_relevant_gate: bool = True

    # === D-159: 데이터 평면 프롬프트 토큰 예산 (W-6 커밋이 예고한 "절단 상한" 후속) ===
    # FabriX(GptOss) 데이터 평면 입력 한도(실측 95,232tok)에 대한 보수 예산. 초과 시
    # 재료(유사어·설명)→샘플 순으로 절단하고, 그래도 넘으면 호출 없이 명시 실패한다
    # (절단·실패 모두 로그 가시화 — 침묵 강등 금지). 0 이하면 가드 비활성.
    prompt_token_budget: int = 90_000

    # === [D-176 후속1] 0건 응답 원인 진단 (plans/82 §6.1~§6.8 · Wave 8) ===
    # ON이면 결과가 0건일 때만 조건 누적 COUNT 프로브를 돌려 **어느 조건에서 끊겼는지**를
    # 응답에 낸다(MFS/XSS). 결과가 있으면 프로브 호출 0회라 정상 경로 비용은 0이고,
    # OFF면 0건 응답 문구가 바이트 단위로 무변경이다.
    # **기본 ON**(사용자 결정 2026-08-28) — 현행 0건 응답이 결함(어느 조건에서 끊겼는지
    # 말하지 않는다)이고 발동이 0건일 때로 한정돼 정상 경로 비용이 0이기 때문이다.
    # 되돌리려면 `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED=false`.
    empty_diagnosis_enabled: bool = True
    # 프로브 상한 K. 모든 MFS 열거는 NP-hard이나 K 고정이면 다항이다(EMPTY-MFS-01).
    # 초과분은 측정하지 않고 **절단 사실을 응답에 노출**한다(침묵 절단 금지).
    empty_diagnosis_max_probes: int = 5

    # === [D-176 후속2] 급증(기간 대비 상승) 조건 결정적 조립 (plans/82 §6.9~§6.13 · Wave 9) ===
    # ON이면 "갑자기 80% 이상 상승" 류 질의를 LLM 자유 생성 대신 코드가 비교 SQL로 조립한다.
    # OFF면 `_try_deterministic`이 급증을 시도조차 하지 않는다(현행 경로 무변경).
    # **기본 ON**(사용자 결정 2026-08-28) — 진입 조건이 좁아(급증 어휘 AND 절대 임계 명시 AND
    # 파일시스템 축 AND 폴스타 어댑터 DB AND 비재시도 턴) 해당 없는 질의는 조립 자체를 시도하지
    # 않는다. 되돌리려면 `TEXT2SQL_SPIKE_CONDITION_ENABLED=false`.
    spike_condition_enabled: bool = True
    # 질의에 수치가 없을 때 쓰는 기본 차분 임계(%p). 선언 파일(config/change_terms.yaml)이
    # 정본이고 이 값은 **운영 override**다 — 사용 시 값을 응답에 노출한다(§6.12 ②).
    spike_default_delta_pp: float = 20.0

    # === plans/119 Q-5 · D-267 ④: 단일 경로 테이블 선택 LLM 생략 (기본 OFF 옵트인 · D-162) ===
    # ON이면 schema_analyzer가 이번 질의의 유사어 테이블 신호(D-051 매칭)가 수동 프로필
    # `allowed_tables` 안에 있을 때 테이블 선택 LLM을 부르지 않고 그 선언 집합을 쓴다. 그때는
    # 필터·보충(보충 = 프로필 테이블만 · plans/114 P-4①) 결과가 LLM 출력과 무관하게 같은 집합이다.
    # 신호가 선언 집합 밖이거나 알람 의도·프로필 부재면 종전대로 LLM을 부른다.
    # 기본 OFF = 현행 경로와 비트 동일. arm 측정으로 효과를 확인한 뒤 전환을 판단한다.
    schema_table_select_skip_enabled: bool = False

    # === plans/139 W4 · D-308: 정의 기반 테이블 선별 상한 K (사용자 확정 2026-10-06) ===
    # 프로필에 테이블 정의(`table_definitions`)가 있는 DB에서만 쓴다 — LLM 선별 결과를 이 개수에서
    # 자르고(LLM 순서), 다리 테이블은 남은 자리만큼만 더한다. 어휘 대체도 상위 이 개수까지다.
    # 정의가 없는 DB에는 영향이 없다.
    schema_table_select_max: int = 8

    # === plans/143 W6 · D-316 ③: 조회 템플릿 결정적 조립 (기본 ON — 예외 근거 아래) ===
    # ON이면 `config/knowledge/{db_id}/query_templates.yaml`이 있는 DB에서 LLM이 템플릿 ID·슬롯만
    # 고르고 코드가 SQL을 조립한다(실패 시 사유를 남기고 기존 LLM SQL 생성으로 폴백).
    # **기본 ON 예외**: 발동이 템플릿 파일 존재에 묶여 있어 파일이 없는 DB(폴스타 등)는 LLM 호출 0 ·
    # 프롬프트·상태 비트 동일이다 — 「기본 off = 현행 동일」과 같은 효과. 끄려면
    # `TEXT2SQL_TEMPLATE_ASSEMBLY=false`.
    template_assembly: bool = True

    model_config = {"env_prefix": "TEXT2SQL_", "env_file": ".env", "extra": "ignore"}


class RouterConfig(BaseSettings):
    """라우터 2단 분리·신뢰도 설정 (Plan 79 트랙 B·C · WU-D2 · env_prefix="ROUTER_").

    이름은 `plans/79` §6.2가 이미 선언한 것을 그대로 쓴다(계획서에 없는 이름을 새로 만들지
    않는다). **전부 기본값이 현행 동작**이며 **기동 시 1회 해석**한다(78 P14 — 요청 시점에
    출력 형식을 바꾸면 프롬프트 접두부가 흔들려 KV 캐시가 무효화된다).

    ⚠ **`two_stage_enabled=True`는 S-1·S-2 이후에만 켠다.** 구조는 세웠으나 아래가 전부
    미검증이다(SPEC-router-two-stage 「미검증으로 남는 것」):
      M-1 트랙 B가 이득인지 손해인지 — 근거가 **모델 크기 종속**(1.5B −33.6 / 9B +11.2)
      M-2 2단 분리의 컨텍스트 대역폭 손실 — 완화책은 넣었으나 효과 미측정
      M-3 조기 차단 임계 — 자기보고 값에 근거 없음(그래서 기본 off)
      M-4 비용 — 호출 1회 → 2회. 지연·토큰·KV 캐시 영향 미측정
    """

    # 트랙 B — intent/DB 2단 분리. 기본 off = 기존 단일 호출 경로가 비트동일하게 실행된다.
    two_stage_enabled: bool = False

    # 트랙 C — 의도 신뢰도의 **소스**. 지금은 자기보고뿐이다(FabriX KBGenAI는 logprobs
    # 원천 불가). 라우터 평면 이동 후 "logprob"으로 바꾸면 교체점은 `_intent_confidence` 하나다.
    confidence_source: Literal["self_report", "logprob"] = "self_report"

    # B-2-1 조기 차단 — 1단계 저신뢰 시 2단계 호출 없이 중단. **기본 off.**
    # 근거 없는 임계를 하나 더 만들지 않기 위해서다 — S-3(WU-01)가 `MIN_RELEVANCE_SCORE=0.3`
    # 에 대해 고정한 문제가 자기보고 확신도에도 그대로 있다.
    early_stop_enabled: bool = False

    # 조기 차단 임계. **미설정(None)이면 차단하지 않는다** — 기본값을 숫자로 두면
    # early_stop_enabled만 켜도 근거 없는 값이 즉시 판단에 관여한다.
    min_confidence: Optional[float] = None  # noqa: UP045

    # A-6(WU-21) — `unknown` 클래스. **기본 off = 프롬프트가 현행과 바이트 동일**이다
    # (정의 줄·예시 두 자리 모두 조건부 — `plans/80` 계약 C-A).
    # 켜면 라우터가 *분류 불가*를 표현할 수 있고, 시스템은 답을 지어내는 대신
    # **되묻는다**(기존 `status="clarification"` 규약 재사용 — 신규 UI 0).
    unknown_enabled: bool = False

    # plans/102 §3.1 · D-224 ① — 답변 영역(capability) 소유. **기본 off = 프롬프트·라우팅이 현행과
    # 바이트 동일**이다. 켜면 라우터·분해 프롬프트에 소유표와 `capabilities`·`chain` 출력 필드가
    # 렌더되고, 코드가 LLM 구조화 출력의 답변 영역만 보고 정본 시스템을 판정·교정한다(D-004 —
    # 질의 원문 키워드 스캔 없음). 빈 분류·LLM 실패 폴백도 사유를 응답에 표기한다.
    capability_ownership_enabled: bool = False

    # plans/132 W5 · G-10~G-12 — 소스 선택 기억(사용자가 칩·정정으로 확인한 「질의 → 소스」
    # 사례)의 sliding TTL(일). **0 = 기능 off — 읽기·쓰기 0, 현행과 비트 동일**(신규 `enable_*`
    # 없이 TTL 값으로 켠다 · D-162). 기억은 소스가 모호할 때(소유 소스 2개+ · 명시 없음)만 칩
    # 대신 쓴다 — 명시 소스·단독 소유 판정은 뒤집지 않는다. 운영값은 측정 뒤 정한다(양식 기억
    # `QUERY_FORM_MEMORY_TTL_DAYS` 선례).
    source_memory_ttl_days: int = 0

    model_config = {"env_prefix": "ROUTER_", "env_file": ".env", "extra": "ignore"}


class CompositeConfig(BaseSettings):
    """복합 질의 호스트 조사 설정 (Plan 78 §6.2 · 전부 기본 off / 보수값).

    미설정 시 현행 동작과 **비트 동일**해야 한다(회귀 0 — Plan 80 §5.4-③).
    플래그는 **기동 시 1회 해석**한다(78 P14 — 요청 시점 변경 금지, KV 캐시 무효화 방지).

    접근 경로: cfg.composite.* (env_prefix="COMPOSITE_").
    """

    # W1 — 선행 결과 → 조사 대상 전달(채팅·이벤트 공통)
    prior_targets_enabled: bool = False
    # W1-3 해석 3단(LLM 컬럼 지목). off면 1·2단만 — 비용·비결정성 최소화
    target_column_llm_enabled: bool = False
    # W1-3-2 / W2 — fan-out 상한. 초과분은 절단하고 절단 사실을 결과에 실는다
    max_targets: int = 10
    # W2 — 동시 조사 수 / 대상별 타임아웃 / fan-out 전체 타임아웃
    fanout_concurrency: int = 3
    # plans/134 W6 ④ — APM 대상 필수 보기에 서버를 말하지 않았을 때 현재 부하(TPS) 상위 N개
    # 인스턴스만 조회한다(2026-10-07 사용자 결정 — 전 인스턴스 조회는 운영 수백 대에서 느리고
    # 답이 읽히지 않는다). 서버·업무를 말했거나 분해가 `full`을 냈으면 종전대로 전부다.
    apm_untargeted_top_n: int = 20
    target_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 45.0
    # W2-8 — 단기 조사 캐시 TTL(Tier 2). **기본 0 = 끔.**
    #
    # `plans/78` §6.2는 60을 적었으나 그러면 "실시간 프로세스 조회"가 기본 설정에서 조용히
    # 캐시된다 — `plans/80` §5.4-③(플래그 기본값은 현행 동작·비트동일)에 걸리고, 충돌 시
    # §5가 우선한다(80 §5 머리말). 운영자가 명시로 켠다(SPEC 정정 C-6).
    snapshot_ttl_seconds: int = 0
    # W3 — 조사 경로 진입(구 COMPOSITE_HOST_DIAGNOSTICS_ENABLED)
    investigation_enabled: bool = False
    # W6 — 조사 감사. 기본 on(감사는 끄는 것이 예외다)
    audit_enabled: bool = True

    # === [D-203] 복합 질의 순차 의존 계약 (plans/88 §4.1·§4.3·§4.9) — **기본 on**(2026-09-10 사용자 확정) ===
    # `plans/80` §5.4-③(기본 off = 비트 동일)의 **명시적 예외**. 근거: ①현행(off) 동작 자체가 결함 — 선행 조회가
    # 0건이면 후속이 스코프 없이 전체 서버를 조회하는 침묵 오류(plans/88 §3 R-1) ②결정적 판정이라 LLM 호출 수 불변
    # ③Gemini 실 실행 14건 통과(plans/88 §11.5) ④`.env`는 git 미추적이라 배포마다 유실될 수 있어 코드 기본값으로
    # 고정(사용자 지시 2026-09-10). off로 되돌리면 판정은 로그로만 남고 실행·상태·응답은 종전과 비트 동일이다.
    # 선행 결과 게이트: input_from 선행이 실패·0건·식별 컬럼 부재면 후속을 실행하지 않고 사유를 남긴다.
    sequential_gate_enabled: bool = True
    # 사후 대조: 후속 결과에서 선행 스코프 밖 서버 행을 제거하고 미조회 서버를 표기한다.
    scope_postcheck_enabled: bool = True
    # DB별 스코프 분할: prior_rows의 _source_db로 후속 멀티 DB 조회의 IN 목록·대상 DB를 나눈다.
    prior_scope_by_db_enabled: bool = True
    # === [D-203 2차] 분해 계약 · 3·4단 순차 러너 — **기본 on**(2026-09-10 사용자 확정 · 단계 4·5·7 실 실행 11건: 정상 분해 무개입·오탐 0) ===
    # 분해 DAG 결정적 검증(중복 id·미존재 참조·input_from⊄depends_on 보정·순환) + 위반 시 되먹임 1회.
    plan_dag_validation_enabled: bool = True
    # 순차 표지("…를 찾아 그 서버들의…")가 있는데 단일 task로 분해되면 재분해 1회(위 검증과 예산 공유 = 총 1회).
    sequential_replan_enabled: bool = True
    # 3단(semantic_router)·4단(legacy) 빌드에 `sequential_runner` 2-pass 노드를 등록한다. HITL 승인 플래그가
    # 켜져 있으면 진입하지 않는다(승인 게이트 우회 금지 — plans/88 §4.7).
    sequential_fallback_tiers_enabled: bool = True
    # === [plans/111 C-3] 복합 질의 task 프레임 계약 — **기본 off**(plans/80 §5.4-③ 비트 동일) ===
    # on이면 분해 LLM이 task마다 자유문 `sub_query` 대신 원문 조각 `spans`를 내고
    # `sub_query`는 코드가 조각을 원문 순서로 이어 만든다 — 원문에 없는 값(`'critical'`·
    # SQL 문장)이 task 질의로 들어갈 수 없다. 조각이 원문(또는 직전 맥락) 밖이거나 원문의
    # 숫자를 덮지 못하면 원문 단일 task로 폴백하고 사유를 남긴다. 계획의 **모든 분기 출구**
    # (사전 처리 조기 반환 포함)에 알람·프로세스 에이전트 교정을 적용한다(111 D-1).
    task_frame_enabled: bool = False
    # === [D-203 후속 · plans/88 R-E · 2026-09-10 사용자 확정 (c)] 1단 선행 스코프 — 값 일치 우선 · 없으면 직전 1건 ===
    # off(현행)면 1단은 성공한 선행 결과를 **전부** 합집합으로 주입한다(4번째 호출이 3번째 결과 10대를 가리켜도 54대).
    # on이면 후속 sub_query에 식별자가 열거되면(G1) 그 값을 가진 선행 결과만, 없으면 가장 최근 성공 선행 1건만 주입한다.
    # 선별 밖 서버가 섞일 위험은 양쪽 모두 0. **기본 on**(1단 실 재검증 통과 · 2026-09-10 사용자 확정). **만료일 2027-03-10**(D-161 C1) — off 분기 삭제 판정.
    prior_scope_latest_only: bool = True

    # ── Plan 81 (D-175) 호스트 가용성 사전 판정 ──────────────────────
    # **기본 on** — 이 파일의 다른 플래그와 정반대다(G-1 사용자 확정 2026-08-28).
    # `plans/80` §5.4-③("기본값은 현행 동작과 비트 동일")의 **명시적 예외**이며, 근거는
    # 현행 동작 자체가 결함이라는 것이다: 전원이 꺼진 서버에 "프로세스 0건"(정상 결과로 서술)
    # 또는 "잠시 후 다시 시도"(재시도해도 같음)를 돌려준다(`plans/81` §1.1 실측).
    # off 경로의 비트 동일성은 테스트로 계속 고정한다.
    availability_precheck_enabled: bool = True
    # 판정이 `unavailable`일 때 수집을 **생략**할지. off면 문구만 병기하고 조회는 진행한다
    # (관찰 모드 — 게이트가 정상 조회를 막는지 현장에서 확인할 때 쓴다).
    availability_block_on_unavailable: bool = True
    # Tier 2 신선도 신호(measurement 왕복 +1). 기본 off — §2-2 "추가 왕복 0" 원칙 유지.
    availability_staleness_enabled: bool = False

    # === [D-176 후속3] 존 순회 대상 소재 탐색 (plans/82 §4.3~§4.4 · Wave 5) ===
    # ON이면 대상 존이 미해소일 때 인가된 폴스타 존을 순회해 서버 소재를 찾는다.
    # **기본 ON**(사용자 결정 2026-08-28) — 현행은 `_resolve_db_id` 실패 시 *"위치를
    # 지정해 주세요"* 라는 막다른 안내로 끝나는데, 운영 `.env`는 세 존 전부 프로세스 API가
    # 매핑돼 있어 **물을 이유가 없는 것을 묻고 있었다**. 비용은 존당 SELECT 1회·LLM 0회
    # (3존 ≈ 150ms 실측). 되돌리려면 `COMPOSITE_HOST_DISCOVERY_ENABLED=false`.
    host_discovery_enabled: bool = True
    # 첫 히트에서 순회를 멈출지(U4). **기본 off = 전수 순회** — 동명 호스트가 여러 존에
    # 있을 수 있고 첫 히트로 끊으면 그 사실이 은폐된다. 응답 ~100ms를 아끼려 할 때만 켠다.
    discovery_early_exit: bool = False
    # 탐색 결과 캐시 TTL(초 · U12). **0건·조회 실패는 캐시하지 않는다**(host_sweep 가드) —
    # 캐시하면 방금 등록한 서버가 TTL 동안 "없는 서버"가 된다.
    discovery_cache_ttl_seconds: float = 60.0

    # === [D-176 후속4] 범위 사전 선택 역질문 (plans/82 §5.3~§5.5 · Wave 6.5) ===
    # ON이면 실행 그룹이 2개 이상일 때 "범위를 좁히시겠습니까?"를 묻는다.
    # **시간 임계는 두지 않는다**(U11 사용자 확정) — 근거 없는 잠정 상수가 무기한 실동작한
    # 전례(D-174 ②)를 만들지 않는 대신, **발동률 관측**으로 습관화를 통제한다.
    # "전체 조회"가 항상 첫 선택지·기본값이라 답하지 않아도 진행된다(U10).
    scope_select_enabled: bool = True

    model_config = {"env_prefix": "COMPOSITE_", "env_file": ".env", "extra": "ignore"}


#: 정규 질의 모드 — off(무동작) · shadow(프레임·렌더 기록만) · augment(해석 블록 병기).
#: replace(대체)는 G-2에 따라 소비자별 측정 뒤에 연다 — 지금은 받지 않는다.
CANONICAL_QUERY_MODES: frozenset[str] = frozenset({"off", "shadow", "augment"})
#: 재작성 게이트·검증 모드. 검증 enforce는 G-5(측정 뒤 사용자 확인) 전까지 받지 않는다.
REWRITE_GATE_MODES: frozenset[str] = frozenset({"off", "shadow", "enforce"})
REWRITE_VERIFY_MODES: frozenset[str] = frozenset({"off", "shadow"})


class IntentFrameConfig(BaseSettings):
    """의도 프레임·정규 질의·재작성 게이트/검증 (plans/107 W1~W3·W5).

    **전부 기본 off = 현행과 비트 동일**(plans/80 §5.4-③). `INTENT_FRAME_ENABLED`가 꺼져 있으면
    프레임을 만들지도 기록하지도 않는다. 켜면 섀도(기록만)부터 시작하고, 소비자 프롬프트를 바꾸는
    것은 `CANONICAL_QUERY_MODE=augment` + `CANONICAL_QUERY_CONSUMERS`에 이름을 올린 소비자뿐이다.

    env_prefix를 두지 않는 이유: plans/107 §5.1이 확정한 환경변수명이 서로 다른 접두
    (`INTENT_FRAME_*`·`CANONICAL_QUERY_*`·`REWRITE_*`)라 필드마다 별칭으로 매핑한다.
    `CANONICAL_QUERY_CONSUMERS`는 CSV 문자열이다(`.env` list 필드의 JSON 배열 함정 회피).
    받지 않는 모드 값은 기동 시 경고를 남기고 off로 본다(침묵 강등 금지).
    """

    enabled: bool = Field(default=False, validation_alias=AliasChoices("INTENT_FRAME_ENABLED"))
    canonical_query_mode: str = Field(
        default="off", validation_alias=AliasChoices("CANONICAL_QUERY_MODE"),
    )
    canonical_query_consumers: str = Field(
        default="", validation_alias=AliasChoices("CANONICAL_QUERY_CONSUMERS"),
    )
    rewrite_gate_mode: str = Field(
        default="off", validation_alias=AliasChoices("REWRITE_GATE_MODE"),
    )
    rewrite_verify_mode: str = Field(
        default="off", validation_alias=AliasChoices("REWRITE_VERIFY_MODE"),
    )

    model_config = {"env_file": ".env", "extra": "ignore"}

    @staticmethod
    def _checked(value: str, allowed: frozenset[str], name: str) -> str:
        normalized = (value or "off").strip().lower()
        if normalized in allowed:
            return normalized
        # 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I4)
        logging.getLogger("src.config").warning(
            "%s=%r 은(는) 지원하지 않는 값이다(허용: %s) — off로 본다",
            name, value, ", ".join(sorted(allowed)),
        )
        return "off"

    def query_mode(self) -> str:
        """정규 질의 모드(검증된 값)."""
        return self._checked(
            self.canonical_query_mode, CANONICAL_QUERY_MODES, "CANONICAL_QUERY_MODE",
        )

    def gate_mode(self) -> str:
        """재작성 게이트 모드(검증된 값)."""
        return self._checked(self.rewrite_gate_mode, REWRITE_GATE_MODES, "REWRITE_GATE_MODE")

    def verify_mode(self) -> str:
        """재작성 검증 모드(검증된 값)."""
        return self._checked(self.rewrite_verify_mode, REWRITE_VERIFY_MODES, "REWRITE_VERIFY_MODE")

    def consumers(self) -> frozenset[str]:
        """병기를 켤 소비자 이름 집합(CSV 파싱)."""
        raw = self.canonical_query_consumers or ""
        return frozenset(name.strip() for name in raw.split(",") if name.strip())
