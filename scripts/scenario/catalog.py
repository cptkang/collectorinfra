"""시나리오 카탈로그 로더·검증 (plans/94 §3).

카탈로그가 **실행 정본**이다. 문서(docs/29)가 아니라 이 YAML이 러너의 입력이며,
`plans` 역추적 필드가 "plans 폴더의 기능들을 테스트한다"는 요건을 기계 검증 가능하게 만든다.

로더는 **의심스러운 것을 통과시키지 않는다**. 조용히 넘어간 결함은 리포트에서
"합격"으로 보이기 때문이다 - 거부 사유는 전부 사람이 읽을 수 있는 문장으로 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from . import REPO_ROOT

SCENARIO_DIR = REPO_ROOT / "testdata" / "scenarios"
PROFILES_PATH = REPO_ROOT / "config" / "scenarios" / "profiles.yaml"
DB_REGISTRY_PATH = REPO_ROOT / "config" / "db_registry.yaml"

# 대응 등급 (plans/94 §3.8). 8등급 + 금지 3종. `empty_template`(plans/123 V-3 · 123·G-2 (b))는 0건
# 응답을 「조건에 해당하는 데이터가 없습니다」류 템플릿으로 끝낸 것이다 — 대상 없음·미래 기간·조건
# 충돌을 짚는 `guide` 와 가른다. 응답 등급이지 판정 어휘(`func_verdict`)가 아니다(D-241 ① 불변).
RESPONSE_MODES: frozenset[str] = frozenset(
    {"answer", "correct", "clarify", "guide", "partial", "refuse", "error", "empty_template"}
)
FORBIDDEN_MODES: frozenset[str] = frozenset({"silent_wrong", "hang", "crash"})

# `probe`(plans/122 K-1 · G-2) = 관측 전용 — 기계 판정 대상이 아니다. 판정 어휘는 늘리지 않는다
# (D-241 ①): 러너는 돌리고 평가기도 종전대로 판정하지만, 정상군(`kind: normal`)만 고르는 소비처
# (합격 가능 가드 `_normal_closed` · 벤치 `sweep.load_normal_catalog`)에서 자동으로 빠진다.
KINDS: frozenset[str] = frozenset(
    {"normal", "compound", "misuse", "mistake", "misconception", "control", "probe"}
)
# 대조군 쌍을 강제하는 kind (V16).
#
# 가드를 새로 넣게 만드는 축만 강제한다. 오용·실수·착각의 처방은 거의 전부 "거부하거나
# 되묻는 규칙"이고, 그 규칙은 정상 동작까지 함께 막는 과잉 거부를 낳는다(R12). 대조군이
# 없으면 그 부작용이 같은 리포트에서 보이지 않는다.
# compound(R1)는 제외한다 - 분해 정확도 축이라 가드를 유발하지 않는다.
CONTROL_REQUIRED_KINDS: frozenset[str] = frozenset({"misuse", "mistake", "misconception"})

ENDPOINTS: frozenset[str] = frozenset({"stream", "plain", "file", "file_stream"})
ENVS: frozenset[str] = frozenset({"closed", "sandbox", "both"})
CACHE_STATES: frozenset[str] = frozenset({"cold", "warm"})

# 러너 동작 어휘(D-217). 질의로는 만들 수 없는 절차·선행 상태를 러너가 직접 수행한다.
ACTION_KINDS: frozenset[str] = frozenset({"seed_reload_idempotency"})
SETUP_KINDS: frozenset[str] = frozenset({"synonym_add"})

# 계획 구조 단언 `plan`(plans/121 TP-0.3)의 하위 키. 오타 키는 조용히 무시되면 단언을 쓴 줄
# 알았는데 아무것도 안 하는 상태가 된다 - 로드 시점에 거부한다.
PLAN_KEYS: frozenset[str] = frozenset(
    {"min_tasks", "max_tasks", "agents", "edges", "plan_path", "replan_max"}
)
# 담당(agent) 어휘 - 2단 레지스트리(`src/orchestration/subagents.py` 의 SUBAGENT_REGISTRY) 키와
# 같다. 하네스는 제품 모듈을 import 하지 않으므로 사본이고, 낡지 않게 테스트가 대조한다
# (tests/test_scenario/test_plan121_plan_assertions.py).
PLAN_AGENTS: frozenset[str] = frozenset({
    "data_query", "process_query", "alarm_query", "cache_management",
    "synonym_registration", "general_inference", "host_inspect",
})
# 간선 한쪽의 "아무 담당". 소비 담당만 계약이고 생산 담당이 둘 중 하나일 때 쓴다(R1-05).
PLAN_ANY_AGENT = "*"

# --- plans/122 단언 하위 키(로드 시점 거부 · PLAN_KEYS 선례) ------------------------------------
# 정의 밖 하위 키는 평가기가 조용히 무시한다 - 단언을 쓴 줄 알았는데 아무것도 안 하는 상태가 된다.
# `file` 의 앞 다섯은 종전 키(2026-09-29 카탈로그 실사용: columns · filled_rows · optional_columns ·
# 평가기가 읽는 sheets · filled_columns), 뒤는 H-3·H-4 확장이다.
FILE_KEYS: frozenset[str] = frozenset({
    "sheets", "columns", "filled_rows", "filled_columns", "optional_columns",
    "value_range", "unique_by", "columns_differ", "empty_columns", "column_equals",
    "style_preserved", "docx",
    # 머리글 위치(plans/122 H-4 보완) — 제목 행 아래 머리글 · 2단 머리글 양식. 이름 규칙은
    # `assertions._header_view`.
    "header_row", "header_rows",
})
# `value_range` 별칭 목록 형식의 항목 키(plans/122 H-1·H-4 보완).
VALUE_RANGE_ITEM_KEYS: frozenset[str] = frozenset({"columns", "range"})
FILLED_ROWS_KEYS: frozenset[str] = frozenset({"min"})
# `.docx` 산출 단언(H-3) — `file.docx` 아래. xlsx 하위 키와 함께 쓰지 않는다.
DOCX_KEYS: frozenset[str] = frozenset({"no_placeholders", "tables", "styles_preserved"})
DOCX_TABLE_KEYS: frozenset[str] = frozenset({"index", "min_rows", "first_row"})
# 결과 행 단언(H-1) — 러너가 받은 `/query/{id}/download-csv` 행을 본다.
# `matches_db_row_sum`(plans/123 CT-6 · E-04) — 결과 행 수 = 감사 로그 DB별 행 수 합(병합 소실
# 탐지).
RESULT_KEYS: frozenset[str] = frozenset(
    {"columns", "filled_columns", "value_range", "unique_by", "allow_empty", "matches_db_row_sum"}
)
# 스트림 지연 단언(H-5) — 각 값은 {max: ms}.
STREAM_KEYS: frozenset[str] = frozenset({"ttft_ms", "max_event_gap_ms"})
# `period_covers` 하위 키 — 형식 넷 중 하나만 쓴다(plans/122 H-2).
#   절대 기간(Y-5)    {from, to} — 종전 형식(판정 바이트 동일)
#   상대 기간(H-2)    {relative: last_month|this_month} · {relative: last_n_months, n: N}
#   연도 없는 월 범위  {month_span: {from: 월, to: 월}} — 「11월~2월」(연 넘김은 해석기)
#   날짜 한정 없음     {unbounded: true} — 「~한 적이 있는」(C-07)
# 상대 기간·월 범위의 창은 시간 해석기(`src/domain/time_spec.relative_window`)가
# 턴 송신 시각으로 정한다.
PERIOD_KEYS: frozenset[str] = frozenset(
    {"from", "to", "relative", "n", "month_span", "unbounded"}
)
RELATIVE_PERIODS: frozenset[str] = frozenset({"last_month", "this_month", "last_n_months"})
MONTH_SPAN_KEYS: frozenset[str] = frozenset({"from", "to"})
# `status_any` 값 — 클라이언트 `_derive_status` 가 낼 수 있는 상태(done `status=partial` 포함).
TURN_STATUSES: frozenset[str] = frozenset(
    {"completed", "clarification", "error", "awaiting_approval", "partial"}
)
FORM_MEMORY_PANEL_STATES: frozenset[str] = frozenset({"present", "absent"})
# 순차 의존 경과 노트 종류(`dependency_notes[].kind`) - `src/utils/prior_dependency.py` 의 `NOTE_*`
# 사본이다. 하네스는 제품 모듈을 import 하지 않으므로 낡지 않게 테스트가 대조한다
# (tests/test_scenario/test_plan122_judge_h5.py).
DEPENDENCY_NOTE_KINDS: frozenset[str] = frozenset({
    "gate", "trace", "truncation", "postcheck", "sufficiency", "scope_db", "decompose",
    "ownership", "routing_fallback", "bridge", "probe", "source_unavailable",
    "structure_missing", "descriptions_missing",
})
# 응답 고지 kind → 대응 등급(plans/123 V-1 · W-8) — `src/domain/disclosure.py` `KIND_TABLE` 의
# `grade` 사본이다. 하네스는 제품 모듈을 import 하지 않으므로 낡지 않게 테스트가 대조한다
# (tests/test_scenario/test_plan123_judge.py). `neutral`(121 `NOTE_*` · 122 시간 notes)·
# `auxiliary`(생성기 메모)는 등급을 정하지 않는다. `disclosures_contains` 단언의 어휘이기도 하다.
DISCLOSURE_KIND_GRADES: dict[str, str] = {
    # 123 — task 단위
    "row_limit_reached": "partial", "validation_budget": "error", "non_sql": "error",
    "deadline": "error", "sql_blocked": "refuse", "query_failed": "error",
    "condition_changed": "correct", "entity_not_found": "guide", "generator_note": "auxiliary",
    # 123 — 턴 단위
    "scope_narrowed": "neutral", "scope_partial": "partial", "unregistered_zone": "correct",
    "unit_suspect": "correct",
    # 123 — 섀도(S-2 · S-6 — on 전에는 응답에 실리지 않는다)
    "blank_input": "guide", "sql_input": "refuse", "write_request": "refuse",
    "prompt_injection": "refuse", "credential_request": "refuse", "condition_conflict": "guide",
    # 121 `NOTE_*` · 122 `TimeResolution.notes` — 등급 중립
    **{kind: "neutral" for kind in (
        "gate", "trace", "truncation", "postcheck", "sufficiency", "scope_db", "decompose",
        "ownership", "routing_fallback", "bridge", "probe", "source_unavailable",
        "structure_missing", "descriptions_missing",
        "default_period", "current_month_excluded", "empty_range", "period_in_progress",
        "future_period", "display_grain_unaligned", "year_inferred", "multiple_periods",
    )},
}
# 불변식(plans/123 V-4) — 이름 → 활성화를 좌우하는 소유 제품 항목. 판정기(`invariants.py`)가 모든
# 턴에 계산해 `invariant_violations` 칸(트리아지)에 싣고, 군 헤더 `invariants:` 가 `active_from`(첫
# run 표지)을 적은 불변식만 판정(`func_verdict`)에 넣는다 — 소유 제품 수정이 랜딩되기 전에 남의
# 결함으로 불합격을 만들지 않는다.
INVARIANTS: dict[str, str] = {
    "limit_disclosed": "123·W-1",
    "zone_coverage_named": "123·W-2·W-5",
    "empty_template_misuse": "123·S-4",
    "overgeneralization": "121·TP-4.4 ②",
    "demonstrative_bulk": "123·G-6 · 106·H1 · 123·S-7(b)",
    "upload_misattributed": "295feee(D-264 ④ 교정)",
}

# 턴 인증 방식(H-6). None = 러너 토큰. "none" = 인증 헤더 없이 보낸다(401 기대 가드).
AUTH_MODES: frozenset[str] = frozenset({"none"})
# 러너 생성 업로드(H-6) — 저장소에 둘 수 없는 입력(한도 초과 크기 · 거부 확장자).
UPLOAD_GENERATE_EXTS: frozenset[str] = frozenset({".xlsx", ".csv", ".xls"})
UPLOAD_GENERATE_MAX_BYTES = 64 * 1024 * 1024   # 67108864 — 러너가 메모리에 만드는 상한

# 요구 소스(plans/122 C-4b · D-276 ②) = 레지스트리 `databases[].db_id` ∪ 비SQL 시스템 id.
# 레지스트리는 SQL DB 만 등재하고 비SQL 시스템은 `solutions[].backend` 가 sql 이 아닌 항목뿐인데
# (2026-09-29 실측 0건 - apm·dpm 은 주석), Prometheus 는 mcp_server 의 PromQL 도구(plans/92)로만
# 붙어 레지스트리에 없다. 그래서 비SQL id 를 여기 둔다 - 레지스트리 선언이 생기면 그것도 함께
# 받는다.
NON_SQL_SOURCES: frozenset[str] = frozenset({"prometheus"})

# ID 형식: 군 문자 + 일련번호. docs/29 의 A-01 · SYN-A-01 · 대조군 접미 R4-01C 를 모두 받는다.
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+[a-z]?){1,2}$")


class CatalogError(Exception):
    """카탈로그가 실행에 쓸 수 없는 상태다. 사유 전건을 담는다."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"카탈로그 거부 {len(errors)}건")


@dataclass(frozen=True)
class Turn:
    """시나리오 1턴. `send`는 요청 본문, `expect`는 단언 선언.

    `endpoint`는 **턴 단위 재지정**이다(기본은 시나리오 값). 폼필 HITL 은 1턴이 파일
    업로드(`file_stream`)이고 답변 턴은 JSON(`stream`)으로 `form_fill_answers`를 보낸다 -
    `/query/file` 은 그 필드를 Form 파라미터로 받지 않기 때문이다(query.py 의 Form 목록).
    턴마다 엔드포인트를 못 바꾸면 I군(폼필 HITL) 8건은 표현 자체가 불가능하다.
    """

    send: dict[str, Any]
    expect: dict[str, Any]
    endpoint: Optional[str] = None
    # 역질문 자동 응답(D-216) 허용 여부. `false` 면 역질문을 답하지 않고 그대로 판정한다 -
    # "여기서 물으면 회귀"인 턴(F-06 3턴 존 승계)에서 자동 응답이 회귀를 가리지 않게 한다.
    auto_answer: bool = True
    # 인증 헤더 방식(plans/122 H-6). None = 러너 토큰 · "none" = 헤더 없이 보낸다(401 기대 가드).
    auth: Optional[str] = None


@dataclass(frozen=True)
class Group:
    """군 헤더. 성능 목표는 군 단위로 정의된다(§2-2)."""

    id: str
    name: str
    latency_target_ms: int
    # G-10 (b): 대응 등급 정책이 사용자 확정되기 전에는 R군 판정을 내리지 않는다.
    # false면 금지 등급 3종만 불합격이고 나머지는 전부 manual(관측)로 남는다.
    policy_confirmed: bool = False
    source: Optional[str] = None
    # 군 기본 요구 소스(plans/122 C-4b). 시나리오가 선언하지 않으면 이것을 물려받는다.
    requires_sources: tuple[str, ...] = ()
    # 불변식 활성 선언(plans/123 V-4) — 이름 → `active_from`(판정에 넣기 시작하는 run 표지 · None
    # 이면 트리아지만). 판정 계약 지문에 들어간다 — 활성 전환은 계약 변경이다.
    invariants: dict[str, Optional[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """시나리오 1건."""

    id: str
    group: str
    plans: list[int]
    title: str
    turns: list[Turn]
    kind: str = "normal"
    env: str = "both"
    profile: str = "baseline"
    cache_state: str = "warm"
    endpoint: str = "stream"
    perf: dict[str, Any] = field(default_factory=dict)
    response_modes: list[str] = field(default_factory=list)
    pair_with: Optional[str] = None
    teardown: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    repeat: Optional[int] = None
    upload: Optional[str] = None
    # 원문이 산문이라 실제 프롬프트가 아직 없는 초안. 러너가 사유와 함께 건너뛴다.
    # 산문을 LLM 에 보내면 무의미한 결과에 돈만 나간다 - 조용히 흘리지 않고 리포트에 남긴다.
    prompt_authored: bool = True
    notes: Optional[str] = None
    # 무과금 모의 실행(--mock)에서 서버가 돌려줄 응답. 없으면 일반 canned 응답이 나간다.
    mock: Optional[dict[str, Any]] = None
    # 역질문 자동 응답의 명시값(D-216). 키: selected_db_ids · form_fill_answers · approval.
    # 없으면 config/scenarios/auto_answer.yaml 의 기본 정책을 쓴다.
    auto_answer: dict[str, Any] = field(default_factory=dict)
    # 부하 묶음(K군 · D-217). 자기 턴 대신 참조 시나리오를 새 스레드로 반복(replay)하거나
    # 동시에(concurrent) 돈다. 이때 `turns` 는 보내지 않고 판정 메모로만 남는다.
    replay: dict[str, Any] = field(default_factory=dict)      # {scenarios: [ID], repeat: n}
    concurrent: dict[str, Any] = field(default_factory=dict)  # {scenarios: [ID], sessions: [n]}
    # 질의가 아닌 러너 동작(SYN-F-05 시드 재적재 멱등성). 있으면 턴을 보내지 않는다.
    action: dict[str, Any] = field(default_factory=dict)
    # 턴 전에 러너가 만드는 선행 상태(K-10 고의 오매핑 유사어). 끝나면 같은 것만 되돌린다.
    setup: list[dict[str, Any]] = field(default_factory=list)
    # teardown unregister_synonym 이 지울 단어(A-10). 유사어 사전 스냅샷 차이 중 이 단어만 지운다 -
    # 공유 Redis 에서 같은 시각 다른 출처가 더한 단어까지 지우지 않기 위해서다.
    unregister_words: list[str] = field(default_factory=list)
    # teardown forget_form_memory 가 지울 폼필 확인 이력 필드(plans/120 V-4 · I-02~I-06).
    # 업로드 양식 시그니처의 이력 스냅샷 차이 중 이 필드만 지운다 - 원래 있던 필드·다른 주체가
    # 더한 필드는 남긴다.
    forget_form_fields: list[str] = field(default_factory=list)
    # 조회에 필요한 소스(plans/122 C-4b · D-276 ②) — 레지스트리 db_id 또는 비SQL 시스템 id. 비면 선언 없음
    # (D-216 ③대로 실행·보류). 군 헤더 선언을 물려받고 시나리오 선언이 있으면 그것이 이긴다.
    requires_sources: list[str] = field(default_factory=list)
    # 러너가 실행 시 만드는 업로드 파일(plans/122 H-6) — {ext: ".xlsx"|".csv"|".xls", size_bytes: int}.
    # 저장소에 둘 수 없는 입력(12MB 초과 파일·거부 확장자)을 위한 것이다. `upload` 와 함께 쓰지 않는다.
    upload_generate: dict[str, Any] = field(default_factory=dict)
    source_file: Optional[str] = None

    @property
    def target_ms(self) -> Optional[int]:
        value = self.perf.get("target_ms")
        return int(value) if value is not None else None

    @property
    def is_r_group(self) -> bool:
        return self.kind in {"compound", "misuse", "mistake", "misconception"}

    @property
    def is_bundle(self) -> bool:
        """자기 턴을 보내지 않고 참조 시나리오를 도는 부하 묶음인가."""
        return bool(self.replay or self.concurrent)


@dataclass
class Catalog:
    """군·시나리오·프로파일의 묶음."""

    groups: dict[str, Group] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)

    def by_id(self, scenario_id: str) -> Optional[Scenario]:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        return None

    def plans_index(self) -> dict[int, list[str]]:
        """계획서 번호 -> 시나리오 ID 역집계. 커버리지 판정의 재료다(§6.4)."""
        index: dict[int, list[str]] = {}
        for scenario in self.scenarios:
            for plan in scenario.plans:
                index.setdefault(plan, []).append(scenario.id)
        return index

    def select(
        self,
        groups: Optional[list[str]] = None,
        only: Optional[list[str]] = None,
        env: Optional[str] = None,
        kinds: Optional[list[str]] = None,
    ) -> list[Scenario]:
        """실행 대상을 고른다. `env` 불일치는 건너뛴 사유와 함께 리포트 10절에 남는다."""
        picked = list(self.scenarios)
        if only:
            wanted = {item.strip() for item in only if item.strip()}
            picked = [s for s in picked if s.id in wanted]
        if groups:
            prefixes = tuple(g.strip().upper() for g in groups if g.strip())
            picked = [s for s in picked if s.group.upper().startswith(prefixes)]
        if kinds:
            picked = [s for s in picked if s.kind in set(kinds)]
        if env:
            picked = [s for s in picked if s.env in (env, "both")]
        return picked


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, dict[str, str]]:
    """플래그 프로파일을 읽는다. 값은 전부 문자열이어야 한다(환경변수 주입)."""
    if not path.exists():
        raise CatalogError([f"프로파일 파일이 없다: {path}"])
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise CatalogError([f"{path}: 최상위 'profiles' 매핑이 필요하다"])

    errors: list[str] = []
    result: dict[str, dict[str, str]] = {}
    for name, mapping in profiles.items():
        if mapping is None:
            mapping = {}
        if not isinstance(mapping, dict):
            errors.append(f"프로파일 '{name}': 매핑이 아니다")
            continue
        bad = [k for k, v in mapping.items() if not isinstance(v, str)]
        if bad:
            errors.append(
                f"프로파일 '{name}': 환경변수 값은 문자열이어야 한다 - {', '.join(sorted(bad))}"
            )
            continue
        result[str(name)] = {str(k): v for k, v in mapping.items()}
    if errors:
        raise CatalogError(errors)
    if "baseline" not in result:
        raise CatalogError(["프로파일 'baseline'이 없다 - 운영 설정 기준선이 사라진다"])
    return result


def _parse_turns(raw_turns: Any, scenario_id: str, errors: list[str]) -> list[Turn]:
    turns: list[Turn] = []
    if not isinstance(raw_turns, list) or not raw_turns:
        errors.append(f"{scenario_id}: 'turns'가 비었다 - 보낼 것이 없는 시나리오는 실행되지 않는다")
        return turns
    for index, item in enumerate(raw_turns, start=1):
        if not isinstance(item, dict):
            errors.append(f"{scenario_id} 턴{index}: 매핑이 아니다")
            continue
        send = item.get("send")
        if not isinstance(send, dict) or not send:
            errors.append(f"{scenario_id} 턴{index}: 'send'가 비었다")
            continue
        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            errors.append(f"{scenario_id} 턴{index}: 'expect'가 매핑이 아니다")
            continue
        turn_endpoint = item.get("endpoint")
        if turn_endpoint is not None and turn_endpoint not in ENDPOINTS:
            errors.append(
                f"{scenario_id} 턴{index}: endpoint '{turn_endpoint}' 는 정의 밖이다 "
                f"({', '.join(sorted(ENDPOINTS))})"
            )
            turn_endpoint = None
        auto_answer = item.get("auto_answer", True)
        if not isinstance(auto_answer, bool):
            errors.append(
                f"{scenario_id} 턴{index}: auto_answer 는 true|false 여야 한다 (현재 {auto_answer!r})"
            )
            auto_answer = True
        auth = item.get("auth")
        if auth is not None and auth not in AUTH_MODES:
            errors.append(
                f"{scenario_id} 턴{index}: auth '{auth}' 는 정의 밖이다 "
                f"({', '.join(sorted(AUTH_MODES))} · 생략 = 러너 토큰)"
            )
            auth = None
        turns.append(Turn(
            send=send, expect=expect, endpoint=turn_endpoint, auto_answer=auto_answer,
            auth=(str(auth) if auth is not None else None),
        ))
    return turns


# 정규식을 받는 단언 키. 로드 시점에 컴파일해 본다.
_REGEX_KEYS = ("sql_must_match", "sql_must_not_match")


def _validate_patterns(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """정규식을 **로드 시점에** 컴파일한다.

    실행 도중 re.error 가 나면 그 시점까지의 측정이 통째로 날아가고, 폐쇄망에서는
    그 손실이 그대로 재실행 비용이다. 1단(--dry-run)에서 잡는 것이 가장 싸다.
    (실측 2026-09-11: `(?i)` 를 표현식 중간에 둔 패턴이 Python 3.11+ 에서 거부된다)
    """
    for index, turn in enumerate(turns, start=1):
        for key in _REGEX_KEYS:
            for pattern in turn.expect.get(key) or []:
                try:
                    re.compile(str(pattern))
                except re.error as exc:
                    errors.append(
                        f"{scenario_id} 턴{index} {key}: 정규식 컴파일 실패 - {pattern!r} ({exc})"
                    )


def _validate_contain_any(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """`response_must_contain_any` 는 선택지 목록이다(plans/120 U-4).

    항목은 문구 하나 또는 문구 묶음(전부 포함해야 성립)이다. 모양이 틀리면 평가기가 문자열을
    한 글자씩 선택지로 읽어 거의 모든 응답을 통과시킨다.
    """
    for index, turn in enumerate(turns, start=1):
        options = turn.expect.get("response_must_contain_any")
        if options is None:
            continue
        valid = isinstance(options, list) and bool(options) and all(
            (isinstance(option, str) and option.strip())
            or (isinstance(option, list) and option
                and all(isinstance(item, str) and item.strip() for item in option))
            for option in options
        )
        if not valid:
            errors.append(
                f"{scenario_id} 턴{index} response_must_contain_any: 비지 않은 목록이어야 하고 "
                f"항목은 문구 또는 문구 목록이다 - {options!r}"
            )


def _plan_errors(spec: Any) -> list[str]:
    """`plan` 단언 1건의 모양 오류(plans/121 TP-0.3). 빈 목록이면 통과다."""
    if not isinstance(spec, dict) or not spec:
        return [f"비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = sorted(str(key) for key in spec if key not in PLAN_KEYS)
    if unknown:
        errors.append(f"정의 밖 키 {unknown} - 허용: {', '.join(sorted(PLAN_KEYS))}")
    for key, low in (("min_tasks", 1), ("max_tasks", 1), ("replan_max", 0)):
        value = spec.get(key)
        if value is not None and not (
            isinstance(value, int) and not isinstance(value, bool) and value >= low
        ):
            errors.append(f"{key} 는 {low} 이상의 정수여야 한다 - {value!r}")
    agents = spec.get("agents")
    if agents is not None and not (
        isinstance(agents, list) and agents
        and all(isinstance(a, str) and a in PLAN_AGENTS for a in agents)
    ):
        errors.append(
            f"agents 는 담당 이름의 비지 않은 목록이어야 한다 - {agents!r} "
            f"(허용: {', '.join(sorted(PLAN_AGENTS))})"
        )
    edges = spec.get("edges")
    ends = PLAN_AGENTS | {PLAN_ANY_AGENT}
    if edges is not None and not (
        isinstance(edges, list) and edges and all(
            isinstance(edge, list) and len(edge) == 2
            and all(isinstance(end, str) and end in ends for end in edge)
            for edge in edges
        )
    ):
        errors.append(
            f"edges 는 [생산 담당, 소비 담당] 쌍의 비지 않은 목록이어야 한다 - {edges!r} "
            f"(담당 이름 또는 '{PLAN_ANY_AGENT}')"
        )
    path = spec.get("plan_path")
    if path is not None and not (
        (isinstance(path, str) and path.strip())
        or (isinstance(path, list) and path
            and all(isinstance(p, str) and p.strip() for p in path))
    ):
        errors.append(f"plan_path 는 경로 코드 또는 그 목록이어야 한다 - {path!r}")
    if errors:
        return errors
    low_n, high_n = spec.get("min_tasks"), spec.get("max_tasks")
    if low_n is not None and high_n is not None and low_n > high_n:
        errors.append(f"min_tasks({low_n}) 가 max_tasks({high_n}) 보다 크다")
    if agents is not None and high_n is not None and len(agents) > high_n:
        errors.append(f"agents {len(agents)}개가 max_tasks({high_n}) 를 넘는다 - 성립할 수 없다")
    return errors


def _validate_plan(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """계획 구조 단언을 **로드 시점에** 검사한다 - 틀린 선언이 실행 뒤에야 드러나지 않게."""
    for index, turn in enumerate(turns, start=1):
        if "plan" not in turn.expect:
            continue
        for error in _plan_errors(turn.expect["plan"]):
            errors.append(f"{scenario_id} 턴{index} plan: {error}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_list(value: Any) -> bool:
    """비지 않은 문자열 목록."""
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)


def _column_refs(value: Any) -> bool:
    """열 참조 목록 — 항목은 열 이름 또는 별칭 목록(`[열 | [별칭…]]` · 하나라도 있으면 그 열)."""
    return isinstance(value, list) and bool(value) and all(
        _text(item) or _text_list(item) for item in value
    )


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unknown_keys(spec: dict[str, Any], allowed: frozenset[str]) -> str | None:
    unknown = sorted(str(key) for key in spec if key not in allowed)
    return f"정의 밖 키 {unknown} - 허용: {', '.join(sorted(allowed))}" if unknown else None


def _bounds_ok(bounds: Any) -> bool:
    """[하한, 상한] 숫자 쌍 · 하한 ≤ 상한."""
    return (isinstance(bounds, list) and len(bounds) == 2
            and all(_number(b) for b in bounds) and bounds[0] <= bounds[1])


def _value_range_errors(spec: Any) -> list[str]:
    """`value_range` — 양 끝 포함 · 하한 ≤ 상한. 두 형식 중 하나다.

    - `{열: [하한, 상한]}` — 종전 형식(항목 검사·문구 그대로 · 두 형식 모두 아니면 형식 안내)
    - `[{columns: [별칭…], range: [하한, 상한]}]` — 별칭 목록(plans/122 H-1·H-4 보완). 머리글에 있는
      첫 별칭을 쓰고, 하나도 없으면 불합격이다(`assertions._value_range_failures`)
    """
    if isinstance(spec, list) and spec:
        errors = []
        for item in spec:
            if not (isinstance(item, dict) and set(item) == VALUE_RANGE_ITEM_KEYS
                    and _text_list(item["columns"]) and _bounds_ok(item["range"])):
                errors.append(
                    "value_range 목록 항목은 {columns: [별칭…](비지 않은 열 이름 목록), "
                    f"range: [하한, 상한](숫자 · 하한 ≤ 상한)}} 이어야 한다 - {item!r}"
                )
        return errors
    if not (isinstance(spec, dict) and spec):
        return ["value_range 는 {열: [하한, 상한]} 매핑 또는 "
                f"[{{columns: [별칭…], range: [하한, 상한]}}] 목록이어야 한다 - {spec!r}"]
    return [
        f"value_range.{column}: [하한, 상한] 숫자 쌍(하한 ≤ 상한)이어야 한다 - {bounds!r}"
        for column, bounds in spec.items()
        if not (_text(column) and _bounds_ok(bounds))
    ]


def _true_only(key: str, value: Any) -> list[str]:
    """켜기 전용 플래그 — `false` 는 선언하지 않은 것과 같아 오해만 남는다."""
    return [] if value is True else [f"{key} 는 true 만 쓴다(끄려면 키를 뺀다) - {value!r}"]


def _file_errors(spec: Any) -> list[str]:
    """`file` 단언의 모양 오류(plans/122 H-3·H-4 · 종전 하위 키 포함)."""
    if not (isinstance(spec, dict) and spec):
        return [f"비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = _unknown_keys(spec, FILE_KEYS)
    if unknown:
        errors.append(unknown)
    for key in ("sheets", "columns", "filled_columns", "optional_columns", "empty_columns"):
        if key in spec and not _text_list(spec[key]):
            errors.append(f"{key} 는 열(시트) 이름의 비지 않은 목록이어야 한다 - {spec[key]!r}")
    filled = spec.get("filled_rows")
    if filled is not None:
        low = filled.get("min") if isinstance(filled, dict) else None
        if not isinstance(filled, dict) or _unknown_keys(filled, FILLED_ROWS_KEYS) \
                or not (isinstance(low, int) and not isinstance(low, bool)):
            errors.append(f"filled_rows 는 {{min: 정수}} 여야 한다 - {filled!r}")
    if "value_range" in spec:
        errors.extend(_value_range_errors(spec["value_range"]))
    if "unique_by" in spec and not _column_refs(spec["unique_by"]):
        errors.append(f"unique_by 는 열 참조의 비지 않은 목록이어야 한다 - {spec['unique_by']!r}")
    errors.extend(_header_row_errors(spec))
    pairs = spec.get("columns_differ")
    if pairs is not None and not (
        isinstance(pairs, list) and pairs
        and all(isinstance(pair, list) and len(pair) == 2 and all(_text(c) for c in pair)
                for pair in pairs)
    ):
        errors.append(f"columns_differ 는 [열, 열] 쌍의 비지 않은 목록이어야 한다 - {pairs!r}")
    equals = spec.get("column_equals")
    if equals is not None and not (
        isinstance(equals, dict) and equals
        and all(_text(c) and isinstance(v, (str, int, float)) and not isinstance(v, bool)
                for c, v in equals.items())
    ):
        errors.append(f"column_equals 는 {{열: 값}} 매핑이어야 한다 - {equals!r}")
    if "style_preserved" in spec:
        errors.extend(_true_only("style_preserved", spec["style_preserved"]))
    if "docx" in spec:
        mixed = sorted(key for key in spec if key != "docx")
        if mixed:
            errors.append(
                f"docx 는 xlsx 하위 키 {mixed} 와 함께 쓰지 않는다 - 산출물은 한 형식이다"
            )
        errors.extend(_docx_errors(spec["docx"]))
    return errors


def _header_row_errors(spec: dict[str, Any]) -> list[str]:
    """`file.header_row: N` · `file.header_rows: [N, …]`(1-based) 모양 오류(plans/122 H-4 보완).

    둘은 함께 쓰지 않는다. `header_rows` 는 오름차순(중복 없음)이다 — 위에서 아래로 상위·하위
    이름을 잇는다.
    """
    errors: list[str] = []
    if "header_row" in spec and "header_rows" in spec:
        errors.append("header_row 와 header_rows 는 함께 쓰지 않는다 - 한 줄이면 header_row, "
                      "2단이면 header_rows")
    if "header_row" in spec and not _positive_int(spec["header_row"]):
        errors.append("header_row 는 1 이상의 정수(1-based 행 번호)여야 한다"
                      f" - {spec['header_row']!r}")
    rows = spec.get("header_rows")
    if "header_rows" in spec and not (
        isinstance(rows, list) and rows and all(_positive_int(n) for n in rows)
        and all(a < b for a, b in zip(rows, rows[1:]))
    ):
        errors.append("header_rows 는 1 이상 정수의 오름차순 목록(1-based 행 번호)이어야 한다"
                      f" - {rows!r}")
    return errors


def _docx_errors(spec: Any) -> list[str]:
    """`file.docx` 모양 오류(H-3)."""
    if not (isinstance(spec, dict) and spec):
        return [f"docx 는 비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = _unknown_keys(spec, DOCX_KEYS)
    if unknown:
        errors.append(f"docx {unknown}")
    for key in ("no_placeholders", "styles_preserved"):
        if key in spec:
            errors.extend(_true_only(f"docx.{key}", spec[key]))
    tables = spec.get("tables")
    if tables is None:
        return errors
    if not (isinstance(tables, list) and tables):
        return errors + [f"docx.tables 는 비지 않은 목록이어야 한다 - {tables!r}"]
    for item in tables:
        if not isinstance(item, dict) or _unknown_keys(item, DOCX_TABLE_KEYS):
            errors.append(f"docx.tables 항목은 {sorted(DOCX_TABLE_KEYS)} 키만 쓴다 - {item!r}")
            continue
        index, rows, first = item.get("index"), item.get("min_rows"), item.get("first_row")
        if not (isinstance(index, int) and not isinstance(index, bool) and index >= 0):
            errors.append(f"docx.tables.index 는 0 이상의 정수여야 한다(0 = 첫 표) - {item!r}")
        if rows is None and first is None:
            errors.append(
                f"docx.tables 항목은 min_rows·first_row 중 하나 이상을 선언한다 - {item!r}"
            )
        if rows is not None and not _positive_int(rows):
            errors.append(f"docx.tables.min_rows 는 1 이상의 정수여야 한다 - {item!r}")
        if first is not None and not (isinstance(first, list) and first
                                      and all(isinstance(c, str) for c in first)):
            errors.append(f"docx.tables.first_row 는 셀 문구의 목록이어야 한다 - {item!r}")
    return errors


def _result_errors(spec: Any) -> list[str]:
    """`result` 단언 모양 오류(H-1)."""
    if not (isinstance(spec, dict) and spec):
        return [f"비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = _unknown_keys(spec, RESULT_KEYS)
    if unknown:
        errors.append(unknown)
    for key in ("columns", "filled_columns", "unique_by"):
        if key in spec and not _column_refs(spec[key]):
            errors.append(
                f"{key} 는 열 참조(열 이름 또는 별칭 목록)의 비지 않은 목록이어야 한다"
                f" - {spec[key]!r}"
            )
    if "value_range" in spec:
        errors.extend(_value_range_errors(spec["value_range"]))
    if "allow_empty" in spec and not isinstance(spec["allow_empty"], bool):
        errors.append(f"allow_empty 는 true|false 여야 한다 - {spec['allow_empty']!r}")
    if "matches_db_row_sum" in spec:
        errors.extend(_true_only("matches_db_row_sum", spec["matches_db_row_sum"]))
    return errors


def _stream_errors(spec: Any) -> list[str]:
    """`stream` 단언 모양 오류(H-5) — 각 값은 {max: 양수 ms}."""
    if not (isinstance(spec, dict) and spec):
        return [f"비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = _unknown_keys(spec, STREAM_KEYS)
    if unknown:
        errors.append(unknown)
    for key in sorted(STREAM_KEYS & set(spec)):
        bound = spec[key]
        if not (isinstance(bound, dict) and set(bound) == {"max"}
                and _number(bound["max"]) and bound["max"] > 0):
            errors.append(f"{key} 는 {{max: 양수 ms}} 여야 한다 - {bound!r}")
    return errors


#: `period_covers` 형식 → 그 형식이 쓰는 하위 키(plans/122 H-2). 둘 이상의 형식을 섞으면 거부한다.
_PERIOD_FORMS: tuple[tuple[str, frozenset[str]], ...] = (
    ("from·to", frozenset({"from", "to"})),
    ("relative", frozenset({"relative", "n"})),
    ("month_span", frozenset({"month_span"})),
    ("unbounded", frozenset({"unbounded"})),
)


def _period_errors(spec: Any) -> list[str]:
    """`period_covers` 모양 오류. 하나라도 빠지면 평가기가 조용히 건너뛰어(판정 0) 통과로 샌다.

    - 절대 기간: from·to 둘 다(YYYY-MM-DD · from < to) — 종전 규칙·문구 그대로
    - 상대 기간(H-2): relative 는 last_month·this_month·last_n_months · n 은 last_n_months 에서만
      (1 ≤ n ≤ 해석기 월 상한 `N_MAX["month"]`)
    - 월 범위: month_span 은 {from: 1~12, to: 1~12}
    - 날짜 한정 없음: unbounded 는 true 만
    """
    if not isinstance(spec, dict):
        return [f"매핑이어야 한다 - {spec!r}"]
    unknown = _unknown_keys(spec, PERIOD_KEYS)
    if unknown:
        return [unknown]
    forms = [name for name, keys in _PERIOD_FORMS if keys & set(spec)]
    if len(forms) > 1:
        return [f"형식 {forms} 을 섞어 쓰지 않는다 - from·to | relative | month_span | unbounded"
                f" 중 하나 - {spec!r}"]
    if forms == ["relative"]:
        return _relative_period_errors(spec)
    if forms == ["month_span"]:
        return _month_span_errors(spec["month_span"])
    if forms == ["unbounded"]:
        return _true_only("unbounded", spec["unbounded"])
    try:
        start, end = date.fromisoformat(str(spec["from"])), date.fromisoformat(str(spec["to"]))
    except (KeyError, ValueError):
        return [f"from·to 는 둘 다 YYYY-MM-DD 여야 한다(to 는 배타 경계) - {spec!r}"]
    return [] if start < end else [f"from 이 to 보다 앞서야 한다 - {spec!r}"]


def _relative_period_errors(spec: dict[str, Any]) -> list[str]:
    """`{relative: …, n: …}` 모양 오류(plans/122 H-2). n 상한은 해석기 정본(`N_MAX`)을 쓴다."""
    from src.domain.time_spec import N_MAX

    kind = spec.get("relative")
    if kind not in RELATIVE_PERIODS:
        return [f"relative 는 {'|'.join(sorted(RELATIVE_PERIODS))} 중 하나다 - {kind!r}"]
    if kind != "last_n_months":
        return [] if "n" not in spec else [f"n 은 relative=last_n_months 에서만 쓴다 - {spec!r}"]
    n, cap = spec.get("n"), N_MAX["month"]
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= cap):
        return [f"relative=last_n_months 는 n(1~{cap} 정수)이 필요하다 - {n!r}"]
    return []


def _month_span_errors(span: Any) -> list[str]:
    """`month_span: {from: 월, to: 월}` 모양 오류 — 둘 다 1~12 정수(plans/122 H-2)."""
    if not (isinstance(span, dict) and set(span) == MONTH_SPAN_KEYS and all(
        isinstance(span[key], int) and not isinstance(span[key], bool) and 1 <= span[key] <= 12
        for key in MONTH_SPAN_KEYS
    )):
        return [f"month_span 은 {{from: 1~12, to: 1~12}} 정수 월이어야 한다 - {span!r}"]
    return []


def _expect_shape_errors(expect: dict[str, Any]) -> list[str]:
    """plans/122 단언 키와 `file`·`period_covers` 하위 키 검사. 빈 목록이면 통과다."""
    errors: list[str] = []
    for key, check in (("file", _file_errors), ("result", _result_errors),
                       ("stream", _stream_errors), ("period_covers", _period_errors)):
        if key in expect:
            errors.extend(f"{key}: {error}" for error in check(expect[key]))
    if "sql_executed" in expect and not isinstance(expect["sql_executed"], bool):
        errors.append(f"sql_executed 는 true|false 여야 한다 - {expect['sql_executed']!r}")
    if "node_path_must_not" in expect and not _text_list(expect["node_path_must_not"]):
        errors.append(f"node_path_must_not 는 노드 이름의 비지 않은 목록이어야 한다 - "
                      f"{expect['node_path_must_not']!r}")
    for key, vocab in (("status_any", TURN_STATUSES),
                       ("dependency_notes_contains", DEPENDENCY_NOTE_KINDS),
                       ("disclosures_contains", frozenset(DISCLOSURE_KIND_GRADES))):
        if key in expect and not (
            _text_list(expect[key]) and all(item in vocab for item in expect[key])
        ):
            errors.append(f"{key} 는 비지 않은 목록이고 값은 {', '.join(sorted(vocab))}"
                          f" 중 하나다 - {expect[key]!r}")
    panel = expect.get("form_memory_panel")
    if "form_memory_panel" in expect and panel not in FORM_MEMORY_PANEL_STATES:
        errors.append(f"form_memory_panel 은 present|absent 여야 한다 - {panel!r}")
    return errors


def _validate_expect_shapes(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """단언 하위 키를 **로드 시점에** 검사한다(plans/122 · PLAN_KEYS 선례).

    오타 키가 조용히 무시되면 단언을 쓴 줄 알았는데 아무것도 안 하는 상태가 된다.
    """
    for index, turn in enumerate(turns, start=1):
        for error in _expect_shape_errors(turn.expect):
            errors.append(f"{scenario_id} 턴{index} {error}")


def _db_id_list(value: Any) -> list[str]:
    """비지 않은 db_id 문자열 목록이면 중복을 뺀 사본, 아니면 빈 목록."""
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        return list(dict.fromkeys(value))
    return []


def oracle_targets(spec: Any, expect: dict[str, Any]) -> list[str]:
    """오라클을 돌릴 DB — spec `db_ids` → 턴 `expect.db_ids` 순(plans/122 O-1). 없으면 빈 목록.

    시스템이 고른 DB(관측 `db_ids`)는 쓰지 않는다 — 따라가면 오라우팅을 정답으로 삼는다.
    로더(엔진 커버리지 검사)와 러너(실행)가 **같은 함수로** 대상을 정한다 — 둘이 갈리면 로더가
    검사하지 않은 DB 에서 오라클이 돈다.
    """
    own = _db_id_list(spec.get("db_ids")) if isinstance(spec, dict) else []
    return own or _db_id_list(expect.get("db_ids"))


def _validate_oracle(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """`expect.oracle` 을 **로드 시점에** 검사한다(plans/122 O-2 · §9.2 「조용한 통과 방지」).

    정본 SQL 파일 존재·대상 DB 엔진 커버리지·정의 밖 하위 키를 `oracle.validate_oracle_spec` 이
    본다. 대상 DB 는 러너와 같은 `oracle_targets` 로 정한다. `oracle` 모듈은 지연 import 한다 —
    오라클 검사가 레지스트리·정본 파일을 읽으므로 `oracle:` 선언이 있는 턴에서만 부른다.
    """
    for index, turn in enumerate(turns, start=1):
        if "oracle" not in turn.expect:
            continue
        from .oracle import validate_oracle_spec

        spec = turn.expect["oracle"]
        targets = oracle_targets(spec, turn.expect)
        for error in validate_oracle_spec(spec, scenario_id=scenario_id, db_ids=targets or None):
            errors.append(f"{scenario_id} 턴{index} oracle: {error}")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _id_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(str(v).strip() for v in value)


def _parse_runner_steps(raw: dict[str, Any], scenario_id: str, errors: list[str]) -> dict[str, Any]:
    """replay·concurrent·action·setup·unregister_words·forget_form_fields 를 읽는다(D-217).

    틀린 선언은 조용히 무시하지 않는다.
    """
    parsed: dict[str, Any] = {}
    for key in ("replay", "concurrent", "action"):
        value = raw.get(key) or {}
        if not isinstance(value, dict):
            errors.append(f"{scenario_id}: {key} 가 매핑이 아니다")
            value = {}
        parsed[key] = value
    replay, concurrent, action = parsed["replay"], parsed["concurrent"], parsed["action"]
    if replay:
        if not _id_list(replay.get("scenarios")):
            errors.append(f"{scenario_id}: replay.scenarios 가 비었다")
        if not _positive_int(replay.get("repeat", 1)):
            errors.append(f"{scenario_id}: replay.repeat 는 1 이상의 정수여야 한다")
    if concurrent:
        if not _id_list(concurrent.get("scenarios")):
            errors.append(f"{scenario_id}: concurrent.scenarios 가 비었다")
        sessions = concurrent.get("sessions")
        if not (isinstance(sessions, list) and sessions and all(_positive_int(n) for n in sessions)):
            errors.append(f"{scenario_id}: concurrent.sessions 는 1 이상의 정수 목록이어야 한다")
    if action and action.get("kind") not in ACTION_KINDS:
        errors.append(
            f"{scenario_id}: action.kind '{action.get('kind')}' 는 정의 밖이다 "
            f"({', '.join(sorted(ACTION_KINDS))})"
        )
    if sum(bool(v) for v in (replay, concurrent, action)) > 1:
        errors.append(f"{scenario_id}: replay·concurrent·action 은 하나만 선언한다")

    setup = raw.get("setup") or []
    if not isinstance(setup, list):
        errors.append(f"{scenario_id}: setup 이 목록이 아니다")
        setup = []
    for index, step in enumerate(setup, start=1):
        if not isinstance(step, dict) or step.get("kind") not in SETUP_KINDS:
            errors.append(
                f"{scenario_id} setup{index}: kind 는 {', '.join(sorted(SETUP_KINDS))} 이어야 한다"
            )
        elif not (step.get("db_id") and step.get("column") and _id_list(step.get("words"))):
            errors.append(f"{scenario_id} setup{index}: synonym_add 는 db_id·column·words 가 필요하다")
    parsed["setup"] = [dict(step) for step in setup if isinstance(step, dict)]

    words = raw.get("unregister_words")
    wants_unregister = "unregister_synonym" in (raw.get("teardown") or [])
    if wants_unregister and not _id_list(words):
        errors.append(
            f"{scenario_id}: teardown unregister_synonym 에는 지울 단어 목록 unregister_words 가 필요하다"
        )
    elif words is not None and not wants_unregister:
        errors.append(f"{scenario_id}: unregister_words 는 teardown unregister_synonym 과 함께만 쓴다")
    parsed["unregister_words"] = [str(word).strip() for word in words] if _id_list(words) else []

    # plans/120 V-4 - 폼필 확인 이력은 양식 시그니처 단위라 사용자 스코프가 없다. 선언한 필드만
    # 지우고, 시그니처는 업로드 양식에서 뜬다 - 둘 중 하나라도 없으면 지울 범위가 정해지지 않는다.
    fields = raw.get("forget_form_fields")
    wants_forget = "forget_form_memory" in (raw.get("teardown") or [])
    if wants_forget and not _id_list(fields):
        errors.append(
            f"{scenario_id}: teardown forget_form_memory 에는 지울 필드 목록 "
            "forget_form_fields 가 필요하다"
        )
    elif fields is not None and not wants_forget:
        errors.append(
            f"{scenario_id}: forget_form_fields 는 teardown forget_form_memory 와 함께만 쓴다"
        )
    if wants_forget and not raw.get("upload"):
        errors.append(
            f"{scenario_id}: teardown forget_form_memory 는 upload(양식 파일)가 있어야 한다 - "
            "이력 키(양식 시그니처)를 거기서 뜬다"
        )
    parsed["forget_form_fields"] = [str(f).strip() for f in fields] if _id_list(fields) else []
    return parsed


def _parse_sources(value: Any, where: str, errors: list[str]) -> tuple[str, ...]:
    """`requires_sources` 선언(plans/122 C-4b)의 모양 검사.

    알 수 없는 id 는 `load_catalog` 가 거부한다.
    """
    if value is None:
        return ()
    if not _text_list(value) or len(set(value)) != len(value):
        errors.append(
            f"{where}: requires_sources 는 소스 id 의 비지 않은 목록이어야 한다(중복 없음)"
            f" - {value!r}"
        )
        return ()
    return tuple(str(item).strip() for item in value)


def _parse_invariants(value: Any, where: str, errors: list[str]) -> dict[str, Optional[str]]:
    """군 헤더 `invariants:`(plans/123 V-4) — `{불변식 이름: active_from 표지 | null}`.

    이름은 `INVARIANTS` 어휘다(오타가 조용히 트리아지로 새지 않게). `active_from` 은 비지 않은
    문자열(판정에 넣기 시작하는 run 표지 — 예 `R5`) 또는 null(트리아지만)이다.
    """
    if value is None:
        return {}
    if not isinstance(value, dict) or not value:
        errors.append(f"{where}: invariants 는 {{불변식: active_from | null}} 매핑이어야 한다"
                      f" - {value!r}")
        return {}
    parsed: dict[str, Optional[str]] = {}
    for name, active_from in value.items():
        if name not in INVARIANTS:
            errors.append(f"{where}: 알 수 없는 불변식 '{name}'"
                          f" - 허용: {', '.join(sorted(INVARIANTS))}")
            continue
        if active_from is not None and not _text(active_from):
            errors.append(f"{where}: invariants.{name} 는 run 표지 문자열 또는 null 이어야 한다"
                          f" - {active_from!r}")
            continue
        parsed[str(name)] = str(active_from).strip() if active_from is not None else None
    return parsed


def _parse_upload_generate(
    raw: dict[str, Any], scenario_id: str, endpoints: set[str], errors: list[str]
) -> dict[str, Any]:
    """러너 생성 업로드(plans/122 H-6) `{ext, size_bytes}` 를 읽는다. 러너 쪽 동작은 runner 소관."""
    spec = raw.get("upload_generate")
    if spec is None:
        return {}
    where = f"{scenario_id}: upload_generate"
    if not isinstance(spec, dict) or set(spec) != {"ext", "size_bytes"}:
        errors.append(f"{where} 는 {{ext, size_bytes}} 두 키만 쓴다 - {spec!r}")
        return {}
    ext, size = spec["ext"], spec["size_bytes"]
    if ext not in UPLOAD_GENERATE_EXTS:
        allowed = ", ".join(sorted(UPLOAD_GENERATE_EXTS))
        errors.append(f"{where}.ext '{ext}' 는 정의 밖이다 ({allowed})")
    if not (_positive_int(size) and size <= UPLOAD_GENERATE_MAX_BYTES):
        errors.append(
            f"{where}.size_bytes 는 1~{UPLOAD_GENERATE_MAX_BYTES} 정수여야 한다 - {size!r}"
        )
    if raw.get("upload"):
        errors.append(f"{where} 와 upload 는 함께 쓰지 않는다 - 올릴 파일이 둘이 된다")
    if not endpoints & {"file", "file_stream"}:
        errors.append(f"{where} 는 endpoint 가 file|file_stream 인 시나리오에만 쓴다")
    return dict(spec)


def _parse_scenario(
    raw: Any, group_id: str, source: Path, errors: list[str],
    group_sources: tuple[str, ...] = (),
) -> Optional[Scenario]:
    if not isinstance(raw, dict):
        errors.append(f"{source.name}: 시나리오 항목이 매핑이 아니다")
        return None

    scenario_id = str(raw.get("id") or "").strip()
    if not scenario_id:
        errors.append(f"{source.name}: 'id' 없는 시나리오가 있다")
        return None
    if not _ID_RE.match(scenario_id):
        errors.append(f"{scenario_id}: ID 형식이 아니다 (예: C-02 · SYN-A-01 · R4-01C)")

    # V1 - plans 빈 리스트 금지. 이 필드가 계획서 커버리지 요건을 떠받친다(§3.2).
    plans_raw = raw.get("plans")
    plans: list[int] = []
    if not isinstance(plans_raw, list) or not plans_raw:
        errors.append(
            f"{scenario_id}: 'plans'가 비었다 - 어느 계획서의 기능을 검증하는지 "
            "역추적할 수 없는 시나리오는 커버리지 분모를 부풀린다"
        )
    else:
        for item in plans_raw:
            try:
                plans.append(int(item))
            except (TypeError, ValueError):
                errors.append(f"{scenario_id}: plans 항목이 계획서 번호가 아니다 - {item!r}")

    kind = str(raw.get("kind") or "normal")
    if kind not in KINDS:
        errors.append(f"{scenario_id}: kind '{kind}' 는 정의 밖이다 ({', '.join(sorted(KINDS))})")

    env = str(raw.get("env") or "both")
    if env not in ENVS:
        errors.append(f"{scenario_id}: env '{env}' 는 정의 밖이다 ({', '.join(sorted(ENVS))})")

    endpoint = str(raw.get("endpoint") or "stream")
    if endpoint not in ENDPOINTS:
        errors.append(f"{scenario_id}: endpoint '{endpoint}' 는 정의 밖이다")

    cache_state = str(raw.get("cache_state") or "warm")
    if cache_state not in CACHE_STATES:
        errors.append(f"{scenario_id}: cache_state '{cache_state}' 는 cold|warm 이어야 한다")

    modes = raw.get("response_modes") or []
    if not isinstance(modes, list):
        errors.append(f"{scenario_id}: response_modes 가 리스트가 아니다")
        modes = []
    else:
        unknown = [m for m in modes if m not in RESPONSE_MODES]
        if unknown:
            errors.append(
                f"{scenario_id}: 정의 밖 대응 등급 {unknown} - "
                f"허용: {', '.join(sorted(RESPONSE_MODES))}"
            )

    turns = _parse_turns(raw.get("turns"), scenario_id, errors)
    _validate_patterns(turns, scenario_id, errors)
    _validate_contain_any(turns, scenario_id, errors)
    _validate_plan(turns, scenario_id, errors)
    _validate_expect_shapes(turns, scenario_id, errors)
    _validate_oracle(turns, scenario_id, errors)

    perf = raw.get("perf") or {}
    if not isinstance(perf, dict):
        errors.append(f"{scenario_id}: perf 가 매핑이 아니다")
        perf = {}
    applies = perf.get("applies_to_turn")
    if applies is not None and turns and not (1 <= int(applies) <= len(turns)):
        errors.append(
            f"{scenario_id}: perf.applies_to_turn={applies} 이 턴 범위(1~{len(turns)}) 밖이다"
        )

    upload = raw.get("upload")
    upload_generate = _parse_upload_generate(
        raw, scenario_id, {endpoint} | {t.endpoint for t in turns if t.endpoint}, errors,
    )
    if endpoint in ("file", "file_stream") and not upload and raw.get("upload_generate") is None:
        errors.append(f"{scenario_id}: endpoint={endpoint} 인데 'upload'(양식 파일 경로)가 없다")
    # 시나리오 선언이 있으면 그것이 이기고, 없으면(키 없음 · null) 군 헤더 값을 물려받는다
    # (plans/122 C-4b).
    requires_sources = (
        _parse_sources(raw["requires_sources"], scenario_id, errors)
        if raw.get("requires_sources") is not None else group_sources
    )

    # 역질문 자동 응답 명시값(D-216). 오타 키는 조용히 무시되면 기본 정책으로 답해 버린다.
    auto_answer = raw.get("auto_answer") or {}
    if not isinstance(auto_answer, dict):
        errors.append(f"{scenario_id}: auto_answer 가 매핑이 아니다")
        auto_answer = {}
    allowed_answer_keys = ("selected_db_ids", "form_fill_answers", "approval")
    unknown_answer_keys = sorted(set(auto_answer) - set(allowed_answer_keys))
    if unknown_answer_keys:
        errors.append(
            f"{scenario_id}: auto_answer 의 정의 밖 키 {unknown_answer_keys} - "
            f"허용: {', '.join(allowed_answer_keys)}"
        )
    steps = _parse_runner_steps(raw, scenario_id, errors)

    return Scenario(
        id=scenario_id,
        group=group_id,
        plans=plans,
        title=str(raw.get("title") or scenario_id),
        turns=turns,
        kind=kind,
        env=env,
        profile=str(raw.get("profile") or "baseline"),
        cache_state=cache_state,
        endpoint=endpoint,
        perf=perf,
        response_modes=[str(m) for m in modes],
        pair_with=(str(raw["pair_with"]) if raw.get("pair_with") else None),
        teardown=[str(t) for t in (raw.get("teardown") or [])],
        depends_on=[str(d) for d in (raw.get("depends_on") or [])],
        repeat=(int(raw["repeat"]) if raw.get("repeat") is not None else None),
        upload=(str(upload) if upload else None),
        prompt_authored=bool(raw.get("prompt_authored", True)),
        notes=(str(raw["notes"]) if raw.get("notes") else None),
        mock=(raw.get("mock") if isinstance(raw.get("mock"), dict) else None),
        auto_answer=dict(auto_answer),
        replay=dict(steps["replay"]),
        concurrent=dict(steps["concurrent"]),
        action=dict(steps["action"]),
        setup=steps["setup"],
        unregister_words=steps["unregister_words"],
        forget_form_fields=steps["forget_form_fields"],
        requires_sources=list(requires_sources),
        upload_generate=upload_generate,
        source_file=source.name,
    )


def _parse_file(path: Path, errors: list[str]) -> tuple[Optional[Group], list[Scenario]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(f"{path.name}: YAML 파싱 실패 - {exc}")
        return None, []
    if not isinstance(raw, dict):
        errors.append(f"{path.name}: 최상위가 매핑이 아니다")
        return None, []

    group_raw = raw.get("group")
    if not isinstance(group_raw, dict) or not group_raw.get("id"):
        errors.append(f"{path.name}: 'group.id' 가 없다")
        return None, []

    group_id = str(group_raw["id"]).strip()
    # V2 - 군 목표 누락 거부. 목표가 없으면 성능 판정이 조용히 n/a 로 빠진다.
    target = group_raw.get("latency_target_ms")
    if target is None:
        errors.append(
            f"{path.name}: 군 '{group_id}' 에 latency_target_ms 가 없다 - "
            "목표 없는 군은 성능 판정이 전부 n/a 가 되어 불합격이 사라진다"
        )
        target = 0
    group = Group(
        id=group_id,
        name=str(group_raw.get("name") or group_id),
        latency_target_ms=int(target),
        policy_confirmed=bool(group_raw.get("policy_confirmed", False)),
        source=(str(group_raw["source"]) if group_raw.get("source") else None),
        requires_sources=_parse_sources(
            group_raw.get("requires_sources"), f"{path.name} 군 '{group_id}'", errors,
        ),
        invariants=_parse_invariants(
            group_raw.get("invariants"), f"{path.name} 군 '{group_id}'", errors,
        ),
    )

    scenarios: list[Scenario] = []
    for item in raw.get("scenarios") or []:
        parsed = _parse_scenario(item, group_id, path, errors, group.requires_sources)
        if parsed is not None:
            scenarios.append(parsed)
    return group, scenarios


def known_source_ids(path: Path = DB_REGISTRY_PATH) -> frozenset[str]:
    """`requires_sources` 에 쓸 수 있는 id — 레지스트리 DB 전체 ∪ 비SQL 시스템(plans/122 C-4b).

    레지스트리의 비SQL 시스템 선언(`solutions[]` 중 backend 가 sql 이 아닌 항목)도 함께 받는다.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    databases = {
        str(entry["db_id"]) for entry in raw.get("databases") or []
        if isinstance(entry, dict) and entry.get("db_id")
    }
    non_sql = {
        str(entry["code"]) for entry in raw.get("solutions") or []
        if isinstance(entry, dict) and entry.get("code")
        and str(entry.get("backend") or "sql") != "sql"
    }
    return frozenset(databases | non_sql | NON_SQL_SOURCES)


def _validate_sources(catalog: Catalog, registry_path: Path, errors: list[str]) -> None:
    """알 수 없는 요구 소스 id 를 거부한다 — 오타 id 는 어느 run 에서도 활성이 아니라 그 시나리오가
    **영영 선택되지 않는다**(조용한 제외). 군에서 물려받은 id 는 군에서 한 번만 알린다."""
    if not any(g.requires_sources for g in catalog.groups.values()) and not any(
        s.requires_sources for s in catalog.scenarios
    ):
        return
    try:
        known = known_source_ids(registry_path)
    except (OSError, yaml.YAMLError) as exc:
        errors.append(
            f"requires_sources 를 검증할 레지스트리를 읽지 못했다: {registry_path} ({exc})"
        )
        return
    allowed = ", ".join(sorted(known))
    for group in catalog.groups.values():
        unknown = sorted(set(group.requires_sources) - known)
        if unknown:
            errors.append(
                f"군 '{group.id}': requires_sources 의 알 수 없는 소스 {unknown} - 허용: {allowed}"
            )
    for scenario in catalog.scenarios:
        header = catalog.groups.get(scenario.group)
        inherited = set(header.requires_sources) if header else set()
        unknown = sorted(set(scenario.requires_sources) - known - inherited)
        if unknown:
            errors.append(
                f"{scenario.id}: requires_sources 의 알 수 없는 소스 {unknown} - 허용: {allowed}"
            )


def _cross_validate(catalog: Catalog, errors: list[str]) -> None:
    seen: dict[str, str] = {}
    for scenario in catalog.scenarios:
        # V2 - ID 중복 거부. 중복이 있으면 재개(resume) 키가 충돌해 한 건이 조용히 사라진다.
        if scenario.id in seen:
            errors.append(
                f"{scenario.id}: ID 중복 ({seen[scenario.id]} · {scenario.source_file})"
            )
        else:
            seen[scenario.id] = scenario.source_file or "?"

        # V2 - 프로파일 미정의 거부.
        if scenario.profile not in catalog.profiles:
            errors.append(
                f"{scenario.id}: 프로파일 '{scenario.profile}' 가 "
                f"{PROFILES_PATH.name} 에 없다"
            )

        if scenario.group not in catalog.groups:
            errors.append(f"{scenario.id}: 군 '{scenario.group}' 헤더가 없다")

        for dep in scenario.depends_on:
            if catalog.by_id(dep) is None:
                errors.append(f"{scenario.id}: depends_on '{dep}' 가 카탈로그에 없다")

        # 부하 묶음의 참조는 실재하는 **질의 시나리오**여야 한다 - 묶음이 묶음을 부르면 끝이 없다.
        for key in ("replay", "concurrent"):
            for ref_id in (getattr(scenario, key).get("scenarios") or []):
                ref = catalog.by_id(str(ref_id))
                if ref is None:
                    errors.append(f"{scenario.id}: {key} 참조 '{ref_id}' 가 카탈로그에 없다")
                elif ref.is_bundle or ref.action or not ref.prompt_authored:
                    errors.append(f"{scenario.id}: {key} 참조 '{ref_id}' 는 질의 시나리오가 아니다")

    # V16 - 대조군 쌍 강제.
    for scenario in catalog.scenarios:
        if scenario.kind in CONTROL_REQUIRED_KINDS and not scenario.pair_with:
            errors.append(
                f"{scenario.id}: kind={scenario.kind} 인데 pair_with 가 비었다 - "
                "대조군 없이는 가드가 정상 동작까지 막는 과잉 거부를 같은 리포트에서 볼 수 없다"
            )
            continue
        if not scenario.pair_with:
            continue
        partner = catalog.by_id(scenario.pair_with)
        if partner is None:
            errors.append(f"{scenario.id}: pair_with '{scenario.pair_with}' 가 카탈로그에 없다")
            continue
        if partner.pair_with != scenario.id:
            errors.append(
                f"{scenario.id} <-> {partner.id}: pair_with 가 서로를 가리키지 않는다 "
                f"(상대는 '{partner.pair_with}')"
            )
        if scenario.kind in CONTROL_REQUIRED_KINDS and partner.kind != "control":
            errors.append(
                f"{scenario.id}: 짝 '{partner.id}' 의 kind 가 control 이 아니다 "
                f"(현재 '{partner.kind}') - 대조군은 정상 동작이어야 의미가 있다"
            )


def load_catalog(
    scenario_dir: Path = SCENARIO_DIR,
    profiles_path: Path = PROFILES_PATH,
    registry_path: Path = DB_REGISTRY_PATH,
) -> Catalog:
    """카탈로그 전건을 읽고 검증한다. 하나라도 문제가 있으면 CatalogError로 거부한다.

    `registry_path` 는 `requires_sources` 검증에만 쓴다(선언이 하나도 없으면 읽지 않는다).
    """
    errors: list[str] = []
    profiles: dict[str, dict[str, str]] = {}
    try:
        profiles = load_profiles(profiles_path)
    except CatalogError as exc:
        errors.extend(exc.errors)

    catalog = Catalog(profiles=profiles)
    if not scenario_dir.exists():
        raise CatalogError(errors + [f"시나리오 디렉터리가 없다: {scenario_dir}"])

    files = sorted(p for p in scenario_dir.glob("*.yaml") if not p.name.startswith("_"))
    if not files:
        raise CatalogError(errors + [f"시나리오 파일이 0건이다: {scenario_dir}"])

    for path in files:
        group, scenarios = _parse_file(path, errors)
        if group is None:
            continue
        if group.id in catalog.groups:
            errors.append(f"군 '{group.id}' 헤더가 두 파일에 있다 ({path.name})")
        catalog.groups[group.id] = group
        catalog.scenarios.extend(scenarios)

    _cross_validate(catalog, errors)
    _validate_sources(catalog, registry_path, errors)
    if errors:
        raise CatalogError(errors)
    return catalog


# --- 판정 계약 지문 (plans/122 J-1 ⑤ · §13.3 G-17 · D-276 ③) -----------------------------------
# 판정 계약 = (리포트 정의 버전, 카탈로그 지문). 두 run 의 지문이 다르면 같은 계약으로 판정된 것이
# 아니다 - 비교는 J-4 재판정 뒤에만 한다. 지문은 **판정에 영향을 줄 수 있는 것 전부**를 담고, 아래에
# 적은 것만 뺀다(제외 목록 방식 - 칸이 새로 생기면 기본으로 지문에 들어가 계약 변화를 놓치지
# 않는다).
JUDGEMENT_DIGEST_EXCLUDED: dict[str, str] = {
    "title": "표시용 제목 - 판정 입력이 아니다",
    "notes": "사람이 읽는 메모",
    "plans": "계획서 역추적(커버리지 분모) - 턴 판정과 무관하다",
    "source_file": "시나리오를 담은 파일 이름 - 같은 군을 다른 파일로 옮겨도 판정은 같다",
    "requires_sources": "선택 전용(C-4b) - 어느 run 에서 도는지만 정하고 판정은 바꾸지 않는다",
}
#: 군 헤더에서 빼는 칸. 남는 것은 id · latency_target_ms(성능 판정) · policy_confirmed(등급 판정).
JUDGEMENT_DIGEST_GROUP_EXCLUDED: dict[str, str] = {
    "name": "표시용 이름",
    "source": "이관 출처 메모",
    "requires_sources": "선택 전용(C-4b)",
}


def judgement_digest(catalog: Catalog) -> str:
    """카탈로그의 판정 계약 지문 — 판정에 영향을 주는 내용만 정규화한 sha256 앞 16자.

    시나리오는 id 순으로, 매핑 키는 정렬해 직렬화한다(YAML 파일 순서·키 순서와 무관). 러너가
    `run.json` 에, 리포트가 `summary.meta.judgement_contract` 에 싣는다(plans/122 J-1 ⑤).
    """
    scenarios = []
    for scenario in sorted(catalog.scenarios, key=lambda s: s.id):
        data = asdict(scenario)
        for key in JUDGEMENT_DIGEST_EXCLUDED:
            data.pop(key, None)
        scenarios.append(data)
    groups = {}
    for group_id, group in sorted(catalog.groups.items()):
        data = asdict(group)
        for key in JUDGEMENT_DIGEST_GROUP_EXCLUDED:
            data.pop(key, None)
        groups[group_id] = data
    payload = json.dumps(
        {"groups": groups, "scenarios": scenarios},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
