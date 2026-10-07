"""벤치 서버 진입점 — 측정 수신기를 설치하고 하네스와 같은 방식으로 앱을 띄운다.

plans/135 §3.6 · W3.

`scripts/scenario/_serve.py`와 같다(리로드 없음 · 단일 프로세스). 다른 점은 앱을 띄우기 전에
`src.observability.run_capture`에 수신기를 꽂는 것 하나다. 설치는 **이 진입점에서만** 한다(D-301 ⑥).

수신기 규칙(§3.6): 받은 상태 객체를 보관하지 않는다. 그 자리에서 `thread_id`·테이블 이름·컬럼
**이름**· 컬럼별 의미 보유 여부·표본 행 유무(불린)·구조 정보 유무만 복사해 한 줄 쓰고 놓는다. **값과
사용자 필드(`user_id`·`user_department`)는 읽지 않는다.**

plans/139 W6-d: DB 별로 프롬프트 크기·선별 결과도 옮긴다 — **숫자와 짧은 열거만**(추정 토큰 수 ·
예산 단계 · 백엔드가 보고한 토큰 수 · 선별 출처 · 선별 수 · 재생성 종결 사유). 선별된 테이블 이름
목록·사유 문구는 옮기지 않는다. 상태에 없으면 null 이다.

수신 파일 경로는 벤치 부모 프로세스가 환경변수 `ITAM_BENCH_CAPTURE_PATH`로 넘긴다(세션 임시 디렉터리
· 실행 종료 시 부모가 지운다). 없으면 설치하지 않는다.

plans/143 W1 · D-316 ⑤ — **자산 사용 표지**. DB 별 칸에 둘을 더한다(실렸을 때만 칸이 생긴다 — 자산이
없는 DB의 레코드 모양은 그대로다).

- `assets`: 이번 턴 그 DB 스키마에 붙은 구조 메타(`_structure_meta`)의 지식 자산 —
  `{키: {"fp": 내용 해시 앞 12자, "n": 건수}}`. 키는 `ASSET_MARKER_KEYS`(`query_guide`·
  `query_examples`·`query_rules`·`table_definitions`)이고 단일·멀티 같은 자리에서 읽는다. LLM SQL
  생성이 실제로 불렸는지는 `template.outcome`(`assembled`면 생성 생략)·`budget_stage`로 함께 본다.
- `template`: 상태 `template_assembly`의 그 DB 항목 — `outcome`·`template_id`·`slot_names`·`reason`·
  `final_sql_from_template`(bool — 조립 뒤 같은 요청에서 LLM이 SQL을 다시 만들면 False)만,
  열거·식별자·bool 모양 검사를 통과한 것만(슬롯 **값**은 상태에도 없다).

**표지 불가**(상태에 없다): DB 전용 규칙 섹션(`prompt_template.yaml` — 어댑터가 파일에서
직접 읽는다) · 질의 이력 few-shot 치환 여부. 이 둘은 run 단위 자산 지문(`run.json` `assets`)으로
갈음한다. 컬럼 설명(K3)·유사어는 별도 표지 없이 기존 칸(`with_meaning` — 단일 경로만)으로 본다.

plans/143 W8 — **자산 끄기**(`ITAM_BENCH_ASSET_ABLATION`). 벤치 서버 프로세스에서만 지정 자산 하나를
비운다(요청 단위 오버라이드가 없어 run 단위 · 제품 코드 무변경): 구조 메타 키는 수동 프로필·승인
적용본 로더 결과에서 그 키만 빼고, `prompt_template`은 생성 템플릿 어댑터의 섹션을 없음으로,
`query_templates`는 `TEXT2SQL_TEMPLATE_ASSEMBLY=false`로 끈다. 모르는 키는 기동을 멈춘다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CAPTURE_ENV = "ITAM_BENCH_CAPTURE_PATH"
CAPTURE_KIND = "task_pipeline_state"

#: 상태 계약(plans/139 W2 `prompt_budget.stage` · W4 `table_selection.source`)의 열거값.
#: 그 밖의 값은 null 로 옮긴다.
BUDGET_STAGES: frozenset[str] = frozenset({"within", "materials", "samples", "exceeded"})
SELECTION_SOURCES: frozenset[str] = frozenset({"llm", "lexical", "none"})
#: 재생성 종결 사유(`regen_stop.reason`)는 코드 열거(`backend_limit` 등)만 옮긴다.
#: 문구 모양이면 null 이다.
_REASON_CODE = re.compile(r"^[a-z][a-z_]{0,39}$")

#: 자산 사용 표지 대상 — 구조 메타 키(plans/143 W1).
ASSET_MARKER_KEYS: tuple[str, ...] = (
    "query_guide",
    "query_examples",
    "query_rules",
    "table_definitions",
)
#: 템플릿 표지 모양 — ID는 글자로 시작(숫자·IP 같은 값 모양 탈락) · 슬롯 이름은 도메인 계약과 같다.
_TEMPLATE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SLOT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

ABLATION_ENV = "ITAM_BENCH_ASSET_ABLATION"
#: 끌 수 있는 자산 — 구조 메타 키 + DB 전용 규칙 섹션 + 결정적 조립 템플릿. 컬럼 설명·유사어는 Redis
#: 에 이미 적재돼 있어 벤치가 쓰기 없이 뺄 수 없다(지원 안 함).
ABLATION_KEYS: tuple[str, ...] = (*ASSET_MARKER_KEYS, "prompt_template", "query_templates")
ABLATION_DB_ID = "itam"
TEMPLATE_SWITCH_ENV = "TEXT2SQL_TEMPLATE_ASSEMBLY"


def _schema_shape(schema: Any, descriptions: Any) -> dict[str, Any]:
    """스키마 상태 → 이름·불린만. `descriptions`가 None 이면 의미 보유를 모른다(멀티 DB 경로)."""
    described: set[str] | None = None
    if isinstance(descriptions, Mapping):
        described = {str(key).casefold() for key in descriptions}
    tables: dict[str, Any] = {}
    raw_tables = schema.get("tables") if isinstance(schema, Mapping) else None
    for name, table in (raw_tables or {}).items():
        if not isinstance(table, Mapping):
            continue
        columns = [
            str(col.get("name"))
            for col in table.get("columns") or []
            if isinstance(col, Mapping) and col.get("name")
        ]
        bare = str(name).rsplit(".", 1)[-1]
        with_meaning = (
            None
            if described is None
            else [
                col
                for col in columns
                if f"{bare}.{col}".casefold() in described
                or f"{name}.{col}".casefold() in described
            ]
        )
        tables[str(name)] = {
            "columns": columns,
            "with_meaning": with_meaning,
            "sample_rows": bool(table.get("sample_data")),
        }
    structure = bool(schema.get("_structure_meta")) if isinstance(schema, Mapping) else False
    return {"tables": tables, "structure_meta": structure}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _member(value: Any, allowed: Collection[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _prompt_shape(state: Mapping[str, Any], db_id: str, *, single: bool) -> dict[str, Any]:
    """DB 하나의 프롬프트 크기·선별 결과 → 숫자·짧은 열거만(plans/139 W6-d).

    예산(`prompt_budget`)·백엔드 보고(`validation_result.backend_error`)·종결 사유(`regen_stop`)는
    단일 경로 상태의 몫이다. 멀티 DB 상태는 DB 별 종결 사유(`regen_stops`)와 선별만 읽는다.
    선별(`table_selection`)은 두 경로 모두 DB id 키다.
    """
    budget = _mapping(state.get("prompt_budget")) if single else {}
    backend = (
        _mapping(_mapping(state.get("validation_result")).get("backend_error")) if single else {}
    )
    selection = _mapping(_mapping(state.get("table_selection")).get(db_id))
    selected = selection.get("selected")
    stop = (
        _mapping(state.get("regen_stop"))
        if single
        else _mapping(_mapping(state.get("regen_stops")).get(db_id))
    )
    reason = stop.get("reason")
    return {
        "prompt_tokens_est": _count(budget.get("estimated_tokens")),
        "budget_stage": _member(budget.get("stage"), BUDGET_STAGES),
        "backend_reported_tokens": _count(backend.get("given")),
        "selection_source": _member(selection.get("source"), SELECTION_SOURCES),
        "selected_count": len(selected) if isinstance(selected, (list, tuple)) else None,
        "stop_reason": reason if isinstance(reason, str) and _REASON_CODE.match(reason) else None,
    }


def fingerprint(value: Any) -> str:
    """내용 해시 앞 12자(글은 그대로 · 그 밖은 키 정렬 JSON) — 값은 남기지 않는다."""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _asset_shape(schema: Any) -> dict[str, Any]:
    """구조 메타의 지식 자산 → `{키: {"fp", "n"}}`(비었거나 없는 키는 싣지 않는다)."""
    meta = _mapping(_mapping(schema).get("_structure_meta"))
    out: dict[str, Any] = {}
    for key in ASSET_MARKER_KEYS:
        value = meta.get(key)
        if isinstance(value, str):
            count = 1 if value.strip() else 0
        elif isinstance(value, (list, tuple, Mapping)):
            count = len(value)
        else:
            count = 0
        if count:
            out[key] = {"fp": fingerprint(value), "n": count}
    return out


def _template_shape(state: Mapping[str, Any], db_id: str) -> dict[str, Any] | None:
    """상태 `template_assembly[db_id]` → 열거·식별자 모양 검사를 통과한 칸만. 없으면 None."""
    from src.domain import query_templates as qt

    entry = _mapping(_mapping(state.get("template_assembly")).get(db_id))
    outcome = _member(entry.get("outcome"), {qt.OUTCOME_ASSEMBLED, qt.OUTCOME_FALLBACK})
    if outcome is None:
        return None
    reasons = {v for k, v in vars(qt).items() if k.startswith("REASON_") and isinstance(v, str)}
    template_id = entry.get("template_id")
    slots = entry.get("slot_names")
    final = entry.get("final_sql_from_template")
    return {
        "outcome": outcome,
        "template_id": template_id
        if isinstance(template_id, str) and _TEMPLATE_ID.match(template_id)
        else None,
        "slot_names": [
            s for s in slots if isinstance(s, str) and _SLOT_NAME.match(s)
        ]
        if isinstance(slots, (list, tuple))
        else [],
        "reason": _member(entry.get("reason"), reasons),
        "final_sql_from_template": final if isinstance(final, bool) else None,
    }


def _usage_shape(state: Mapping[str, Any], db_id: str, schema: Any) -> dict[str, Any]:
    """자산 사용 표지 — 실렸을 때만 칸을 만든다(plans/143 W1)."""
    out: dict[str, Any] = {}
    assets = _asset_shape(schema)
    if assets:
        out["assets"] = assets
    template = _template_shape(state, db_id)
    if template is not None:
        out["template"] = template
    return out


def schema_context_record(kind: str, state: Any) -> dict[str, Any] | None:
    """수신한 task 상태 → 스키마 맥락 레코드. 대상 종류가 아니면 None."""
    if kind != CAPTURE_KIND or not isinstance(state, Mapping):
        return None
    thread_id = state.get("thread_id")
    dbs: dict[str, Any] = {}
    db_schemas = state.get("db_schemas")
    if state.get("is_multi_db") and isinstance(db_schemas, Mapping):
        # 선별·종결만 남은 DB(스키마 없이 끝난 DB)도 칸을 둔다
        db_ids = dict.fromkeys(
            [
                *db_schemas,
                *_mapping(state.get("table_selection")),
                *_mapping(state.get("regen_stops")),
            ]
        )
        for db_id in db_ids:
            dbs[str(db_id)] = {
                **_schema_shape(db_schemas.get(db_id), None),
                **_prompt_shape(state, str(db_id), single=False),
                **_usage_shape(state, str(db_id), db_schemas.get(db_id)),
            }
    else:
        active = str(state.get("active_db_id") or "")
        dbs[active] = {
            **_schema_shape(state.get("schema_info"), state.get("column_descriptions")),
            **_prompt_shape(state, active, single=True),
            **_usage_shape(state, active, state.get("schema_info")),
        }
    return {
        "kind": kind,
        "thread_id": thread_id if isinstance(thread_id, str) else None,
        "dbs": dbs,
    }


class FileSink:
    """수신 레코드를 JSONL 한 줄씩 덧붙인다(스레드 안전). 상태 객체는 보관하지 않는다."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, kind: str, payload: Any) -> None:
        record = schema_context_record(kind, payload)
        if record is None:
            return
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, open(self._path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def install_from_env() -> bool:
    """환경변수가 있으면 수신기를 설치한다. 설치했으면 True."""
    path = os.environ.get(CAPTURE_ENV)
    if not path:
        return False
    from src.observability import run_capture

    run_capture.install(FileSink(Path(path)))
    return True


def _without(meta: Any, key: str) -> Any:
    return {k: v for k, v in meta.items() if k != key} if isinstance(meta, Mapping) else meta


def _patch(*targets: tuple[Any, str, Any]) -> Callable[[], None]:
    """속성을 바꿔 끼우고 되돌리는 함수를 돌려준다."""
    originals = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]
    for owner, name, value in targets:
        setattr(owner, name, value)

    def undo() -> None:
        for owner, name, value in originals:
            setattr(owner, name, value)

    return undo


def install_ablation(key: str, db_id: str = ABLATION_DB_ID) -> Callable[[], None]:
    """자산 하나를 벤치 서버 프로세스에서만 끈다(plans/143 W8) → 되돌리는 함수.

    Raises:
        ValueError: 모르는 키(`ABLATION_KEYS` 밖)
    """
    if key not in ABLATION_KEYS:
        raise ValueError(f"모르는 자산 키 {key!r} — 지원: {', '.join(ABLATION_KEYS)}")
    if key == "query_templates":
        previous = os.environ.get(TEMPLATE_SWITCH_ENV)
        os.environ[TEMPLATE_SWITCH_ENV] = "false"

        def undo_env() -> None:
            if previous is None:
                os.environ.pop(TEMPLATE_SWITCH_ENV, None)
            else:
                os.environ[TEMPLATE_SWITCH_ENV] = previous

        return undo_env
    if key == "prompt_template":
        from src.db_adapters.generated import GeneratedTemplateAdapter

        section = GeneratedTemplateAdapter.section

        def no_section(self: Any, target: str | None) -> str | None:
            return None if target == db_id else section(self, target)

        return _patch((GeneratedTemplateAdapter, "section", no_section))
    # 구조 메타 키 — 질의 경로의 두 출처(①수동 프로필 ②승인 적용본) 결과에서 그 키만 뺀다.
    # `src.nodes`가 같은 이름의 노드 함수를 재노출하므로 모듈은 import_module 로 잡는다.
    import importlib

    from src.schema_cache.cache_manager import SchemaCacheManager

    schema_analyzer = importlib.import_module("src.nodes.schema_analyzer")
    load_manual = schema_analyzer._load_manual_profile
    applied = SchemaCacheManager.get_applied_structure_meta

    def manual_without(target: str) -> Any:
        profile = load_manual(target)
        return _without(profile, key) if target == db_id else profile

    async def applied_without(self: Any, target: str) -> Any:
        meta = await applied(self, target)
        return _without(meta, key) if target == db_id else meta

    return _patch(
        (schema_analyzer, "_load_manual_profile", manual_without),
        (SchemaCacheManager, "get_applied_structure_meta", applied_without),
    )


def install_ablation_from_env() -> str | None:
    """환경변수가 있으면 그 자산을 끈다 → 끈 키(없으면 None). 모르는 키면 ValueError(기동 중단)."""
    key = (os.environ.get(ABLATION_ENV) or "").strip()
    if not key:
        return None
    install_ablation(key)
    return key


def main() -> None:
    # 끄기를 앱·설정 로드보다 먼저 건다(템플릿 스위치는 설정 로드 때 읽힌다)
    install_ablation_from_env()
    install_from_env()
    from scripts.scenario._serve import main as serve

    serve()


if __name__ == "__main__":
    main()
