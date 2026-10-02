"""DB 활성화 준비도 판정 C1~C10 (plans/104 §3.8.3 · D-227).

**무엇을 하나.** 신규 시스템을 `ACTIVE_DB_IDS`에 넣기 전에 갖춰야 할 항목(MCP 소스 · 레지스트리 ·
라우팅 설명 · 스키마 한정 · 스키마 캐시 · 구조 정보 · 환경)과 권장 항목(컬럼 설명 · 유사어), 정보
항목(권한·존)을 결정적으로 판정한다. D-214 ⑤(활성화 순서 불변식)를 화면 판정으로 대체한다.

- 입력은 호출부(서비스)가 모은 값이다 — 이 모듈은 I/O·LLM 0이고 스키마 리터럴을 모른다.
- `ok`는 3값이다: True(충족) · False(미충족) · None(확인 못 함 — 필수 항목이면 미충족으로 센다).
- 레지스트리는 **실행 중 프로세스의** 값이다 — 파일에만 있고 재기동 전이면 C2가 미충족이다(N7).

계층: domain — 순수 함수 · 표준 라이브러리만.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlparse

Grade = Literal["required", "recommended", "info"]

#: 로컬 샌드박스로 보는 호스트(R7 · `plans/95` §4.6.2 — 운영 정본 재료로 쓰지 않는다)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

#: DB2만 스키마 한정이 필수다(대문자 한정 규칙 — CLAUDE.md 방언 분기)
_SCHEMA_REQUIRED_ENGINES = frozenset({"db2"})


@dataclass(frozen=True)
class ReadinessInputs:
    """준비도 판정 입력.

    Attributes:
        db_id: 대상 DB(= MCP 소스명)
        mcp_available: MCP 서버에 연결해 소스 목록을 받았는지
        mcp_listed: `list_sources`에 이 소스가 있는지
        mcp_type: MCP 소스 엔진(`postgresql`·`db2`·`mariadb`) — 모르면 None
        mcp_health: `health_check` 결과 — 확인하지 않았으면 None
        registry_entry: 실행 중 레지스트리 항목
            `{db_id, enabled, engine, description, db_schema, zone}`
        cache_exists: 스키마 캐시 `:meta` 존재
        cache_table_count: 캐시 테이블 수
        snapshot_table_count: 마지막 스냅샷 테이블 수(스냅샷 없으면 None)
        cache_env: 등록 상태 schema 단계의 env(기록 없으면 None)
        structure_source: 구조 정보 출처 `manual`(수동 프로필) · `approved`(승인본) · None
        approved_env: 적용 승인본의 env
        current_env: 현재 `DBHUB_SERVER_URL`
        description_scope_tables: 설명 범위 테이블 수
        description_applied_tables: 설명 적용 테이블 수
        description_excluded_tables: 명시 제외 테이블 수
        seed_loaded_count: 유사어 시드 로드 건수
        llm_synonym_applied_count: LLM 유사어 적용 건수
        default_allowed_db_ids: `AUTH_DEFAULT_ALLOWED_DB_IDS` 파싱값
    """

    db_id: str
    mcp_available: bool
    mcp_listed: bool
    mcp_type: str | None
    mcp_health: bool | None
    registry_entry: Mapping[str, Any] | None
    cache_exists: bool
    cache_table_count: int
    snapshot_table_count: int | None
    cache_env: str | None
    structure_source: str | None
    approved_env: str | None
    current_env: str
    description_scope_tables: int
    description_applied_tables: int
    description_excluded_tables: int
    seed_loaded_count: int
    llm_synonym_applied_count: int
    default_allowed_db_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReadinessItem:
    """준비도 항목 1건.

    Attributes:
        code: C1~C10
        label: 화면 표시 이름
        grade: required(필수) · recommended(권장) · info(정보)
        ok: True 충족 · False 미충족 · None 확인 못 함(또는 정보 항목)
        detail: 판정 근거·다음 행동 안내
    """

    code: str
    label: str
    grade: Grade
    ok: bool | None
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    """준비도 판정 결과."""

    db_id: str
    items: tuple[ReadinessItem, ...]
    required_met: int
    required_total: int
    recommended_met: int
    recommended_total: int

    @property
    def ready(self) -> bool:
        """필수 항목이 전부 충족됐는지."""
        return self.required_met == self.required_total

    def unmet_required(self) -> list[ReadinessItem]:
        """충족되지 않은(확인 못 함 포함) 필수 항목."""
        return [i for i in self.items if i.grade == "required" and i.ok is not True]

    def to_dict(self) -> dict[str, Any]:
        """JSON 응답용 딕셔너리(`summary`는 목록 준비도 열 문구)."""
        return {
            "db_id": self.db_id,
            "items": [asdict(i) for i in self.items],
            "required_met": self.required_met,
            "required_total": self.required_total,
            "recommended_met": self.recommended_met,
            "recommended_total": self.recommended_total,
            "ready": self.ready,
            "summary": (
                f"필수 {self.required_met}/{self.required_total} · "
                f"권장 {self.recommended_met}/{self.recommended_total}"
            ),
        }


def is_local_sandbox_env(env: str) -> bool:
    """env(`DBHUB_SERVER_URL`)의 호스트가 로컬 루프백·와일드카드면 True.

    Args:
        env: 예 ``http://localhost:9099/sse``

    Returns:
        호스트가 localhost · 127.0.0.1 · ::1 · 0.0.0.0이면 True
    """
    text = str(env or "").strip()
    if not text:
        return False
    host = urlparse(text if "://" in text else f"//{text}").hostname
    return (host or "").lower() in _LOCAL_HOSTS


def _norm_env(env: str | None) -> str:
    """env 비교용 정규화(앞뒤 공백·끝 `/` 제거)."""
    return str(env or "").strip().rstrip("/")


def _env_label(env: str | None) -> str:
    """env 표시 문구(로컬 샌드박스면 표기)."""
    if not env:
        return "(기록 없음)"
    return f"{env} (로컬 샌드박스)" if is_local_sandbox_env(env) else env


def _norm_engine(engine: Any) -> str:
    """엔진 이름 비교용 정규화."""
    return str(engine or "").strip().lower()


def _c1_mcp(inp: ReadinessInputs) -> ReadinessItem:
    """C1 MCP 소스 — 목록에 있고 health_check 성공."""
    label = "MCP 소스"
    if not inp.mcp_available:
        return ReadinessItem("C1", label, "required", None,
                             "MCP 서버에 연결하지 못해 확인하지 못했습니다.")
    if not inp.mcp_listed:
        return ReadinessItem(
            "C1", label, "required", False,
            "MCP list_sources에 소스가 없습니다 — mcp_server config.toml [[sources]]·"
            "연결 문자열 추가와 MCP 재기동은 사람이 합니다.",
        )
    if inp.mcp_health is None:
        return ReadinessItem("C1", label, "required", None,
                             "소스는 목록에 있으나 연결 확인(health_check)을 하지 않았습니다.")
    if not inp.mcp_health:
        return ReadinessItem("C1", label, "required", False,
                             "소스는 목록에 있으나 health_check가 실패했습니다.")
    return ReadinessItem("C1", label, "required", True,
                         f"목록에 있음 · 연결 정상 (type={inp.mcp_type or '미상'})")


def _c2_registry(inp: ReadinessInputs) -> ReadinessItem:
    """C2 레지스트리 — 실행 중 레지스트리에 있음 · enabled · engine = MCP type."""
    label = "레지스트리 등록"
    entry = inp.registry_entry
    if entry is None:
        return ReadinessItem(
            "C2", label, "required", False,
            "실행 중 레지스트리에 없습니다 — config/db_registry.yaml 반영 후 "
            "앱 재기동이 필요합니다.",
        )
    if not entry.get("enabled", True):
        return ReadinessItem("C2", label, "required", False,
                             "레지스트리에 있으나 enabled=false입니다.")
    engine = _norm_engine(entry.get("engine"))
    mcp_type = _norm_engine(inp.mcp_type)
    if not mcp_type:
        return ReadinessItem(
            "C2", label, "required", None,
            f"등록됨(engine={engine or '미상'}) · MCP type을 몰라 대조하지 못했습니다.",
        )
    if engine != mcp_type:
        return ReadinessItem(
            "C2", label, "required", False,
            f"엔진 불일치: 레지스트리 engine={engine or '(비어 있음)'} · MCP type={mcp_type}",
        )
    return ReadinessItem("C2", label, "required", True, f"등록됨 · engine={engine}")


def _c3_description(inp: ReadinessInputs) -> ReadinessItem:
    """C3 라우팅 설명 — 레지스트리 description 비어 있지 않음."""
    label = "라우팅 설명"
    entry = inp.registry_entry
    if entry is None:
        return ReadinessItem("C3", label, "required", False,
                             "레지스트리 미등록 — 설명을 확인할 수 없습니다.")
    if not str(entry.get("description") or "").strip():
        return ReadinessItem(
            "C3", label, "required", False,
            "레지스트리 description이 비어 있습니다 — 라우터가 빈 설명을 렌더합니다.",
        )
    return ReadinessItem("C3", label, "required", True, "레지스트리 description 있음")


def _c4_schema(inp: ReadinessInputs) -> ReadinessItem:
    """C4 스키마 한정 — DB2만 db_schema 필수 · 그 외 엔진은 값만 표시(해당 없음 = 충족)."""
    label = "스키마 한정"
    entry = inp.registry_entry or {}
    engine = _norm_engine(entry.get("engine")) or _norm_engine(inp.mcp_type)
    db_schema = str(entry.get("db_schema") or "").strip()
    shown = db_schema or "(비어 있음)"
    if not engine:
        return ReadinessItem("C4", label, "required", None,
                             f"엔진을 몰라 판정하지 못했습니다 · db_schema={shown}")
    if engine not in _SCHEMA_REQUIRED_ENGINES:
        return ReadinessItem("C4", label, "required", True,
                             f"해당 없음(engine={engine}) · db_schema={shown}")
    if not db_schema:
        return ReadinessItem(
            "C4", label, "required", False,
            f"engine={engine}은 db_schema가 필요합니다(대문자 스키마 한정 규칙).",
        )
    return ReadinessItem("C4", label, "required", True,
                         f"db_schema={db_schema} (engine={engine} · 대문자 한정)")


def _c5_cache(inp: ReadinessInputs) -> ReadinessItem:
    """C5 스키마 캐시 — 캐시 존재 · 테이블 수 = 마지막 스냅샷 테이블 수."""
    label = "스키마 캐시"
    if not inp.cache_exists:
        return ReadinessItem("C5", label, "required", False,
                             "스키마 캐시가 없습니다 — 스키마 수집·캐시 등록이 필요합니다.")
    if inp.snapshot_table_count is None:
        return ReadinessItem(
            "C5", label, "required", False,
            f"캐시 테이블 {inp.cache_table_count}개 · 스냅샷이 없어 대조하지 못했습니다 — "
            "점검 또는 등록을 실행하세요.",
        )
    if inp.cache_table_count != inp.snapshot_table_count:
        return ReadinessItem(
            "C5", label, "required", False,
            f"캐시 테이블 {inp.cache_table_count}개 ≠ 스냅샷 {inp.snapshot_table_count}개 — "
            "재등록이 필요합니다.",
        )
    return ReadinessItem("C5", label, "required", True,
                         f"캐시 테이블 {inp.cache_table_count}개 = 스냅샷")


def _c6_structure(inp: ReadinessInputs) -> ReadinessItem:
    """C6 구조 정보 — 수동 프로필 또는 승인본(빈 패턴 포함)."""
    label = "구조 정보"
    if inp.structure_source == "manual":
        return ReadinessItem("C6", label, "required", True, "수동 프로필")
    if inp.structure_source == "approved":
        return ReadinessItem("C6", label, "required", True, "승인본(빈 패턴 포함)")
    return ReadinessItem(
        "C6", label, "required", False,
        "수동 프로필·승인본이 없습니다 — 「DB 구조」 탭에서 분석·승인이 필요합니다.",
    )


def _c7_env(inp: ReadinessInputs) -> ReadinessItem:
    """C7 환경 — 캐시와 승인본(수동 프로필이면 제외)의 env = 현재 env.

    구조 정보가 없으면 승인본 대조는 생략한다(C6에서 이미 미충족 — 이중 계상 방지).
    """
    label = "환경"
    current = _norm_env(inp.current_env)
    problems: list[str] = []
    if not inp.cache_env:
        problems.append("캐시 등록 env 기록 없음")
    elif _norm_env(inp.cache_env) != current:
        problems.append(f"캐시 env={_env_label(inp.cache_env)}")
    structure_note = ""
    if inp.structure_source == "manual":
        structure_note = " · 구조 정보는 수동 프로필이라 대조 제외"
    elif inp.structure_source == "approved":
        if not inp.approved_env:
            problems.append("승인본 env 기록 없음")
        elif _norm_env(inp.approved_env) != current:
            problems.append(f"승인본 env={_env_label(inp.approved_env)}")
    else:
        structure_note = " · 구조 정보 없음(C6)이라 승인본 대조 생략"
    if problems:
        return ReadinessItem(
            "C7", label, "required", False,
            f"현재 env={_env_label(inp.current_env)}와 다릅니다: " + " · ".join(problems)
            + structure_note,
        )
    return ReadinessItem("C7", label, "required", True,
                         f"현재 env={_env_label(inp.current_env)}와 일치{structure_note}")


def _c8_descriptions(inp: ReadinessInputs) -> ReadinessItem:
    """C8 컬럼 설명 — 범위 내 적용 + 명시 제외 = 범위 테이블 수."""
    label = "컬럼 설명"
    scope = inp.description_scope_tables
    applied = inp.description_applied_tables
    excluded = inp.description_excluded_tables
    if scope <= 0:
        return ReadinessItem("C8", label, "recommended", False,
                             "설명 범위 테이블이 선정되지 않았습니다.")
    counts = f"범위 {scope} · 적용 {applied} · 제외 {excluded}"
    if applied + excluded >= scope:
        return ReadinessItem("C8", label, "recommended", True, counts)
    return ReadinessItem("C8", label, "recommended", False,
                         f"{counts} — 미적용 {scope - applied - excluded}개")


def _c9_synonyms(inp: ReadinessInputs) -> ReadinessItem:
    """C9 유사어 — 시드 로드 건수 또는 LLM 유사어 적용 건수 > 0."""
    label = "유사어"
    counts = f"시드 로드 {inp.seed_loaded_count} · LLM 적용 {inp.llm_synonym_applied_count}"
    ok = inp.seed_loaded_count > 0 or inp.llm_synonym_applied_count > 0
    return ReadinessItem("C9", label, "recommended", ok, counts)


def _c10_access(inp: ReadinessInputs) -> ReadinessItem:
    """C10 권한·존 — 정보 항목(ok=None). 기본 허용 목록 포함 여부와 존 배정을 안내한다."""
    label = "권한·존"
    # 2026-09-21(plans/104 C-4 · D-232): 이 설정은 **신규 가입자의 초기 허용 목록**으로 실제
    # 적용된다. 빈 값이면 신규 가입자는 조회 가능 DB가 없고(안전 실패), 기존 사용자의
    # `None`(전체 허용)은 그대로다. 관리자 역할은 목록과 무관하게 전체를 본다.
    if inp.default_allowed_db_ids:
        included = "포함" if inp.db_id in inp.default_allowed_db_ids else "미포함"
        allowed = f"AUTH_DEFAULT_ALLOWED_DB_IDS에 {included}"
    else:
        allowed = "AUTH_DEFAULT_ALLOWED_DB_IDS 비어 있음"
    allowed += (
        " — 신규 가입자는 이 값으로 초기화됩니다(빈 값이면 조회 가능 DB 없음)."
        " 기존 사용자와 관리자 역할은 영향을 받지 않으며, 개별 권한은 관리자 페이지"
        " 「사용자 관리」 탭에서 부여합니다."
    )
    zone = str((inp.registry_entry or {}).get("zone") or "").strip()
    if inp.registry_entry is None:
        zone_note = "존: 레지스트리 미등록이라 확인 못 함"
    elif zone:
        zone_note = f"존: {zone}"
    else:
        zone_note = "존 미배정 — 멀티 DB 그룹 실행에서 존 그룹 뒤 잔여 그룹(존 무관)으로 실행됩니다"
    return ReadinessItem("C10", label, "info", None, f"{allowed} · {zone_note}")


def evaluate_readiness(inputs: ReadinessInputs) -> ReadinessReport:
    """준비도 C1~C10을 판정한다.

    Args:
        inputs: 호출부가 모은 판정 입력

    Returns:
        ReadinessReport — 필수 7(C1~C7) · 권장 2(C8·C9) · 정보 1(C10)
    """
    items = (
        _c1_mcp(inputs),
        _c2_registry(inputs),
        _c3_description(inputs),
        _c4_schema(inputs),
        _c5_cache(inputs),
        _c6_structure(inputs),
        _c7_env(inputs),
        _c8_descriptions(inputs),
        _c9_synonyms(inputs),
        _c10_access(inputs),
    )
    required = [i for i in items if i.grade == "required"]
    recommended = [i for i in items if i.grade == "recommended"]
    return ReadinessReport(
        db_id=inputs.db_id,
        items=items,
        required_met=sum(1 for i in required if i.ok is True),
        required_total=len(required),
        recommended_met=sum(1 for i in recommended if i.ok is True),
        recommended_total=len(recommended),
    )
