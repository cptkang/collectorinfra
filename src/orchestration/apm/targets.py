"""대상 해소·정제 · 직전 대상 승계 · 소스별 상태 행 · 대상 텍스트 해석.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from src.domain import disclosure as disc
from src.orchestration import apm_jobs as jobs
from src.orchestration.apm.calls import (
    _Call,
    _call_args,
    _exception_error,
    _JobScope,
    _run_calls,
    _status,
)
from src.orchestration.apm.common import (
    APM_SYSTEM,
    DEFAULT_VIEW,
    INSTANCES_VIEW,
    MAX_SELECTION_CANDIDATES,
)
from src.orchestration.apm.jobs_notices import _await_first_hop
from src.orchestration.apm.selection import SOURCE_LABEL_KEY
from src.orchestration.apm.window import WindowPlan
from src.orchestration.entity_link import AMBIGUOUS, LINKED, NOT_QUERIED, UNLINKED, LinkEntry
from src.routing import apm_source_select as source_select
from src.routing.db_authz import authorized_db_ids
from src.routing.registry import SourceSpec, ViewSpec
from src.utils.prior_targets import TargetRef, resolve_targets
from src.utils.query_gen_common import refers_to_demonstrative_server

logger = logging.getLogger("src.orchestration.apm_query")

#: APM 대상 해소의 상한 인자 — 상한이 없다(D-296 ④ · plans/134 M-5). 공용 해소 함수
#: (`resolve_targets`)의 상한 인자에 넘기는 값이다(폴스타 쪽 공유 설정 `composite.max_targets`는
#: 그대로).
_NO_CAP = sys.maxsize


# ── 대상 ──────────────────────────────────────────────────────────────────────

def _int_setting(app_config: Any, name: str, default: int) -> int:
    """`composite.<name>` 양의 정수 설정 — 아니면 기본값이다. 설정된 값(수·문자열)이 잘못됐으면
    경고 1줄을 남긴다(값은 비밀이 아니다 · V6-8 ②). 설정 대역(MagicMock)은 경고하지 않는다."""
    value = getattr(getattr(app_config, "composite", None), name, default)
    valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
    if not valid and isinstance(value, (int, float, str)):
        logger.warning("composite.%s=%r 은 양의 정수가 아니라 기본값 %d을 씁니다", name, value,
                       default)
    return value if valid else default


def default_views(isolated: dict[str, Any]) -> list[str]:
    """보기를 고르지 않은 task의 기본 보기(plans/132 N-4 · G-3).

    이번 턴 대상(사용자가 말한 식별자 · 선행 결과)이 있으면 응답시간 보기, 없으면 **인스턴스
    목록**이다. 「제니퍼 인스턴스 리스트」처럼 대상 없는 질문에 앞 10개 인스턴스의 응답시간을 내던
    것(실측 4/4 — 9B가 `views`를 비움)을 막는다. 직전 턴 대상은 보지 않는다 — 대상 없는 목록 질문이
    직전 서버로 좁혀지지 않게 한다(명시 보기 `apm.app_health`는 종전대로 직전 대상을 쓴다).
    """
    current = {**isolated, "conversation_context": {}}
    return [DEFAULT_VIEW] if resolve_apm_targets(current) else [INSTANCES_VIEW]


def resolve_apm_targets(isolated: dict[str, Any]) -> list[TargetRef]:
    """선행 결과 → 이번 턴 식별자 → 직전 대상 순으로 대상을 고른다(공용 규칙 `resolve_targets`).

    선행 결과는 `prior_targets`(해소본) 또는 `prior_rows`(행)에서 온다 — 이 처리기는 신규라 조사
    대상 승계 플래그(`COMPOSITE_PRIOR_TARGETS_ENABLED`)와 무관하게 데이터 의존을 따른다
    (`input_from`). 대상 수 상한은 없다(D-296 ④ · plans/134 M-5 — 종전 `max_targets` 절단 폐지).
    """
    from src.orchestration.process_query import _targets_from_prior_rows  # 지연 — 순환 방지

    parsed = isolated.get("parsed_requirements") or {}
    ctx = isolated.get("conversation_context") or {}
    prior = isolated.get("prior_targets") or _targets_from_prior_rows(
        isolated.get("prior_rows"), db_id=None, max_targets=_NO_CAP,
    )
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        prior_targets=prior,
        previous_entities=ctx.get("previous_entities"),
        db_id=None,
        max_targets=_NO_CAP,
    )
    return list(resolution.targets)


def _demonstrative(isolated: dict[str, Any]) -> bool:
    """턴 원문이 지시어로 직전 서버를 가리키는가(기존 판정 `refers_to_demonstrative_server`)."""
    text = isolated.get("original_user_query") or isolated.get("user_query") or ""
    return refers_to_demonstrative_server(str(text))


def scoped_targets(isolated: dict[str, Any]) -> list[TargetRef]:
    """대상 표현 `optional` 보기의 대상(plans/134 W7 · 계약 §4.1).

    이번 턴 식별자 → 선행 task 결과만 쓴다. 직전 턴 대상은 사용자가 지시어(「그 서버」)로 가리킬
    때만 잇는다 — 대상 없는 설정·관리 질문이 직전 서버로 조용히 좁혀지지 않게 한다(`default_views`와
    같은 이유 · 새 단어 목록 없이 기존 지시어 판정을 쓴다).
    """
    current = isolated if _demonstrative(isolated) else {**isolated, "conversation_context": {}}
    return resolve_apm_targets(current)


def explicit_targets(isolated: dict[str, Any]) -> list[TargetRef]:
    """GUID 추적(`reference: guid`)의 좁힘 대상 — 이번 턴 사용자가 말한 서버만(plans/134 W5).

    - 선행 task 결과는 쓰지 않는다 — 그 행은 GUID 참조의 원천이다. 그 서버로 좁히면 연계 추적이 그
      서버 도메인 안으로 줄어 다른 도메인의 연계 거래를 놓친다.
    - 복합 계획의 파서 식별자는 다른 task 몫일 수 있어 쓰지 않는다(파서 `limit → n`과 같은 이유) —
      좁히지 않으면 허용된 전 소스·도메인을 본다(놓치는 쪽이 아니다).
    - 직전 턴 대상은 지시어로 가리킬 때만.
    """
    if isolated.get("is_composite"):
        return []
    parsed = isolated.get("parsed_requirements") or {}
    ctx = isolated.get("conversation_context") or {}
    previous = ctx.get("previous_entities") if _demonstrative(isolated) else None
    resolution = resolve_targets(filter_conditions=parsed.get("filter_conditions"),
                                 previous_entities=previous, db_id=None, max_targets=_NO_CAP)
    return list(resolution.targets)


def _targeted(view: ViewSpec, args: dict[str, Any] | None) -> bool:
    """`optional` 보기를 대상별로 부르는가 — `targeted_choices`가 있는 enum 조건 값으로 정한다
    (예 이벤트 룰은 도메인 범위 · 색상 경계는 소스 범위라 대상과 무관하게 1회)."""
    for spec in view.args:
        if spec.targeted_choices:
            return (args or {}).get(spec.tool_arg or spec.name) in spec.targeted_choices
    return True


# ── 대상 텍스트 (plans/130 M-1·M-2 — 인스턴스 이름·업무명) ──────────────────────────

#: 분해 `targets`의 종류 — 종류는 분해 LLM이 고르고 해석은 코드가 한다(D-004). `auto`는 인스턴스
#: 이름 먼저, 0건이면 업무명이다(종류 판단이 흔들려도 받친다 — R-5).
TARGET_KINDS = ("instance", "business", "auto")
#: 대상 텍스트 길이 상한(자) — 게이트웨이 검색어 상한과 같다.
TARGET_TEXT_MAX = 200
#: 인스턴스 id 인자를 받는 게이트웨이 도구 — 나머지 도구는 인스턴스 이름 + 소스로만 좁힌다
#: (테스트가 게이트웨이 입력 스키마와 대조한다).
INSTANCE_ID_TOOLS = frozenset({
    "apm_app_health", "apm_runtime_health", "apm_resource_pool", "apm_slow_transactions",
    "apm_active_services", "apm_status_stats", "apm_metrics",
})
#: 해석 근거 → 고지 문구(`{apm}` = APM 시스템 이름 · `{owner}` = 업무명 간선 소유 시스템 이름).
_EVIDENCE_TEXT = {
    "instance_name": "{apm} 인스턴스 이름",
    "business_map": "업무 수동 매핑",
    "domain": "{apm} 도메인 이름",
    "business": "{apm} 업무 정의",
    "instance_text": "{apm} 인스턴스 이름·설명",
    "name": "{owner} 등록명",
    "description": "{owner} 비고",
}


def sanitize_targets(raw: Any) -> list[dict[str, str]]:
    """분해 `targets` → `[{text, kind}]` — 형태만 정제한다(해석은 처리기 · plans/130 M-1).

    dict 항목만 받는다. 텍스트는 앞뒤 공백을 지우고, 비었으면 버린다. `TARGET_TEXT_MAX`자를 넘으면
    앞 `TARGET_TEXT_MAX`자 + 「…」로 줄여 남긴다 — 버리면 대상을 말한 턴이 첫 홉(임의 인스턴스)으로
    가므로 처리기가 「대상 있음」으로 세고 해석 없이 고지한다(V130-7 · 길이로 다시 판정 ·
    `_too_long`). 모르는 종류는 `auto`다. 같은 텍스트(대소문자 무시)는 처음 것만 남긴다. 다시
    걸러도 결과가 같다(분해 정제 · 재계획 · 처리기 진입에서 부른다).
    """
    items = raw if isinstance(raw, (list, tuple)) else []
    kept: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_text, raw_kind = item.get("text"), item.get("kind")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if _too_long(text):
            text = text[:TARGET_TEXT_MAX] + "…"
        if not text or text.casefold() in seen:
            continue
        kind = raw_kind.strip().lower() if isinstance(raw_kind, str) else ""
        seen.add(text.casefold())
        kept.append({"text": text, "kind": kind if kind in TARGET_KINDS else "auto"})
    return kept


def _too_long(text: str) -> bool:
    """게이트웨이 검색어 상한을 넘는 대상 텍스트인가 — 해석하지 않고 고지만 한다(V130-7)."""
    return len(text) > TARGET_TEXT_MAX


def _shown(text: str) -> str:
    """고지·실패 사유에 싣는 대상 텍스트 — 길이 초과 텍스트는 앞 30자만."""
    return text[:30] + "…" if _too_long(text) else text


def _uses_target(view: ViewSpec, args: dict[str, Any] | None) -> bool:
    """대상으로 좁히는 보기인가 — 대상 필수 보기 · 첫 홉 목록 · 대상별로 부르는 `optional`."""
    if view.target == "optional":
        return _targeted(view, args)
    return view.target == "" and bool(view.required_input or view.first_hop)


def _target_texts(task: dict[str, Any], isolated: dict[str, Any], ledger: list[LinkEntry],
                  legacy_hosts: list[str]) -> list[dict[str, str]]:
    """이번 task의 대상 텍스트(plans/130 M-2 ①).

    분해 `targets` ∪ 파서 서버명 대상 중 등록명 간선(E2)이 잇지 못한 것(`unlinked`·`not_queried`·
    `ambiguous` → `auto` — 여러 hostname이라 잇지 않은 이름도 첫 홉으로 보내지 않는다 ·
    D-290 ⑥)이다. 파서 hostname 대상과 E2가 이은 대상은 종전 경로(hostname 호출)이고, 그것과
    같은 텍스트는 다시 해석하지 않는다(두 번 조회하지 않는다). 파서 서버명은 상한 없이 모은다 —
    E2에 가지 않은 이름도 텍스트다(D-296 ④ · V130-6 · 종전 경로 상한은 plans/134 M-5로 폐지).
    """
    parsed = isolated.get("parsed_requirements") or {}
    conditions = parsed.get("filter_conditions") or []
    names = [t.server_name for t in resolve_targets(
        filter_conditions=conditions, db_id=None,
        max_targets=len(conditions)).targets if t.server_name and not t.hostname]
    left = {e.key.casefold() for e in ledger
            if e.facet == "server_name" and e.status in (UNLINKED, NOT_QUERIED, AMBIGUOUS)}
    seen = {e.key.casefold() for e in ledger if e.facet == "server_name"}
    legacy = ({h.casefold() for h in legacy_hosts}
              | {e.key.casefold() for e in ledger if e.status == LINKED})
    unlinked = [{"text": n, "kind": "auto"} for n in names
                if n.casefold() in left or n.casefold() not in seen]
    texts = sanitize_targets([*sanitize_targets(task.get("targets")), *unlinked])
    return [t for t in texts if t["text"].casefold() not in legacy]


def _named_texts(task: dict[str, Any], live: list[ViewSpec], meta: dict[str, Any],
                 notices: list[disc.Disclosure], source: str) -> tuple[list[str], list[str]]:
    """이름 보기(`target: named`)에 실을 이름 — (말한 이름, 실을 이름)(plans/134 W3·W4 · B-1).

    이번 task 대상 텍스트(분해 `targets`) 중 인스턴스 이름(`kind: instance`)이 아닌 것이다 —
    서비스·업무 이름의 종류 판단은 게이트웨이 해석에 맡긴다. 게이트웨이 검색어 상한을 넘는 이름은
    싣지 않고 알린다(V130-7과 같은 규칙). 이름 보기와 서비스로 좁히는 전 대상 보기(V34-4)를
    고르지 않았으면 빈 목록이다.
    """
    if not any(v.target == "named" or _service_scoped(v) for v in live):
        return [], []
    given = [t["text"] for t in sanitize_targets(task.get("targets")) if t["kind"] != "instance"]
    for text in (t for t in given if _too_long(t)):
        meta["failures"].append({"view": None, "hostname": None, "target": _shown(text),
                                 "reason": f"대상 '{_shown(text)}' 이름 길이 초과"})
        notices.append(disc.make(
            disc.APM_UNRESOLVED_CONDITION,
            f"대상 이름이 너무 길어({TARGET_TEXT_MAX}자 초과) 조회하지 않았습니다"
            f"('{_shown(text)}'). 다른 대상으로 대신 조회하지 않았습니다.", source=source))
    return given, [t for t in given if not _too_long(t)]


def _service_scoped(view: ViewSpec) -> bool:
    """서비스로 좁히는 전 대상 보기인가 — `target: none` + `service` 조건(순위·전 대상 이벤트)."""
    return view.target == "none" and any((a.tool_arg or a.name) == "service" for a in view.args)


def _service_names(given: Any, texts: list[str]) -> list[str]:
    """전 대상 보기의 서비스 이름 — 조건 `service` + 대상 텍스트(대소문자 무시 중복 제거).

    V34-4.
    """
    names: list[str] = []
    for name in [*([given] if isinstance(given, str) and given else []), *texts]:
        if name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names


def _suggestion_names(envelope: dict[str, Any]) -> list[str]:
    """게이트웨이 봉투 `suggestions`(≤3 — 자동 채택 안 함)의 이름(문자열 · 이름 칸 있는 항목)."""
    names: list[str] = []
    for item in envelope.get("suggestions") or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = next((str(item[k]) for k in ("name", "domain_name", "business_name",
                                                 "instance_name", "text") if item.get(k)), "")
        else:
            name = ""
        if name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names[:MAX_SELECTION_CANDIDATES]


# ── 직전 대상 승계 (plans/157 W3 · 결함 ② — 130 M-5 대상 칸) ─────────────────────

#: 스레드 키 `apm_source_scope` 안의 승계 재료 칸 — `[{text, kind, source_id}]`. 처리기는 메타
#: `source_selection`에 싣고 집계기가 스레드 키로 옮긴다(`last_target_sources`와 같은 지점 · 같은
#: 초기화 — 칩 해제·스코프 해제가 키째 비운다).
LAST_TARGETS_KEY = "last_targets"


def _per_target_views(views: list[str], by_id: dict[str, ViewSpec]) -> bool:
    """고른 보기가 모두 대상별 보기인가 — 대상이 필수인 종전 보기(`required_input`)만이다.

    인스턴스 목록(첫 홉)·전체 보기(지표 목록)·선택 대상(`optional` — 설정·관리)·이름·참조 보기는
    아니다(`default_views`·`scoped_targets`가 직전 대상으로 좁히지 않는 보기와 같은 구분). 승계
    재료를 쓰는 조건이자 읽는 조건이다(G-3 ③ · C-2).
    """
    return bool(views) and all(
        by_id[v].target == "" and bool(by_id[v].required_input) for v in views)


def inherited_targets(task: dict[str, Any], isolated: dict[str, Any], views: list[str],
                      by_id: dict[str, ViewSpec],
                      decision: source_select.SourceDecision) -> list[dict[str, str]]:
    """대상을 생략한 후속 턴이 이을 직전 APM 대상 — 분해 `targets` 모양(G-3 (a) · 결정적 AND).

    ① 직전 턴이 APM을 조회했고(`previous_sources`) 스레드 키에 그 턴의 대상 기록이 있다
    ② 이번 턴 task에 대상 텍스트(`targets`)·서버 식별자·선행 결과 대상이 없다 — 복합 계획이면
       잇지 않는다(형제 task가 말한 대상을 이 task가 빠뜨렸을 수 있다 · `explicit_targets`와 같은
       보수 규칙)
    ③ 고른 보기가 모두 대상별 보기다(`_per_target_views`)
    ④ 이번 턴이 소스를 바꾸지 않았다 — 사다리 단 3(승계)이거나, 소스 단어 없이 분해 LLM
       `sources`만으로 정한 소스(단 2 · 근거 단어 0)가 승계 값 안에 있다. 분해는 앞 턴 맥락의
       소스를 다시 내곤 한다(실측 E1 `APM-SRC-inherit#1` — `sources: [cm_prd]`) — 같은 소스 재언급은
       변경이 아니다. 소스 단어·화면 선택·되묻기 답이면 아니다
    하나라도 아니면 빈 목록(현행 그대로). 소스 사다리가 고른 소스 밖의 기록은 버린다.
    """
    scope = isolated.get("apm_source_scope")
    scope = scope if isinstance(scope, dict) else {}
    if decision.action != "query" or not _same_sources(decision, scope):
        return []
    if not _per_target_views(views, by_id) or isolated.get("is_composite"):
        return []
    if sanitize_targets(task.get("targets")) or resolve_apm_targets(
            {**isolated, "conversation_context": {}}):
        return []
    ctx = isolated.get("conversation_context") or {}
    if APM_SYSTEM not in (ctx.get("previous_sources") or []):
        return []
    items = scope.get(LAST_TARGETS_KEY)
    allowed = set(decision.source_ids)
    return sanitize_targets([
        {"text": item.get("text"), "kind": item.get("kind")}
        for item in items if isinstance(item, dict) and item.get("source_id") in allowed
    ] if isinstance(items, list) else [])


def _same_sources(decision: source_select.SourceDecision, scope: dict[str, Any]) -> bool:
    """이번 턴 소스 결정이 소스를 바꾸지 않았는가(G-3 ④ — `inherited_targets`)."""
    if decision.basis == "inherited":
        return True
    if decision.basis != "named" or decision.evidence:  # 소스 단어 · 화면 선택 · 되묻기 답
        return False
    ids = {str(i) for i in scope.get("ids") or ()}
    return bool(ids) and (source_select.ALL_SOURCES in ids or set(decision.source_ids) <= ids)


def _inherited_notice(targets: list[dict[str, str]], source: str) -> disc.Disclosure:
    """승계 고지(의무·중립) — 사용자가 알아야 하는 좁힘이다(G-3 (a))."""
    names = [_shown(t["text"]) for t in targets]
    shown = ", ".join(names[:5]) + (f" 외 {len(names) - 5}개" if len(names) > 5 else "")
    return disc.make(disc.APM_UNTARGETED_SCOPE,
                     f"직전 대상 {len(targets)}건({shown})을 이어서 조회했습니다 — 다른 대상은"
                     " 이름을 말해 주세요.", source=source)


def _last_targets(instances: list[dict[str, Any]]) -> list[dict[str, str]]:
    """이번 턴이 조회한 해석 인스턴스 → 승계 재료 `[{text, kind, source_id}]`(이름·소스로 한 번)."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for inst in instances:
        key = (str(inst["source_id"]), str(inst["instance_name"]).casefold())
        if key not in seen:
            seen.add(key)
            out.append({"text": str(inst["instance_name"]), "kind": "instance",
                        "source_id": str(inst["source_id"])})
    return out


def _with_source_labels(rows: list[dict[str, Any]],
                        specs: tuple[SourceSpec, ...]) -> list[dict[str, Any]]:
    """`source_id`가 있는 행 바로 뒤에 소스 라벨 칸을 더한다(모르는 id는 원문)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if "source_id" not in row or SOURCE_LABEL_KEY in row:
            out.append(row)
            continue
        labelled: dict[str, Any] = {}
        for key, value in row.items():
            labelled[key] = value
            if key == "source_id":
                labelled[SOURCE_LABEL_KEY] = source_select.source_label(str(value), specs)
        out.append(labelled)
    return out


def _source_failure_codes(items: Any) -> dict[str, str]:
    """게이트웨이 봉투 `sources[]`에서 실패한 소스 → 사유 **코드**(「코드: 문구」의 앞만).

    문구에는 게이트웨이 설정 소스 목록 같은 내부 값이 실릴 수 있어 옮기지 않는다(W0 ②).
    """
    out: dict[str, str] = {}
    for item in items if isinstance(items, list) else ():
        if isinstance(item, dict) and item.get("source_id") and item.get("status") not in (
                None, "ok"):
            code = str(item.get("reason") or item.get("status")).split(":", 1)[0].strip()
            out.setdefault(str(item["source_id"]), code[:60])
    return out


def _source_status_rows(rows: list[dict[str, Any]], meta: dict[str, Any], used: tuple[str, ...],
                        specs: tuple[SourceSpec, ...],
                        first_hop_failed: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """소스별 상태 행(plans/147 §4.5) — 조회 n건 · 실패(사유 코드) · 선택 밖.

    실패 = 보기 호출 봉투(`aggregates[].sources[]`) 또는 첫 홉(부하 순위·인스턴스 목록) 봉투의 소스
    상태가 실패이고 그 소스 행이 0건(첫 홉에서 빠진 소스는 대상이 없어 이후 호출에 실리지 않는다).
    사유는 코드만 옮긴다. 미연결 소스(사용 가능 밖)는 행을 내지 않는다.
    """
    available = source_select.get_available_apm_sources()
    counts: dict[str, int] = {}
    for row in rows:
        sid = row.get("source_id")
        if isinstance(sid, str):
            counts[sid] = counts.get(sid, 0) + 1
    failed: dict[str, str] = dict(first_hop_failed or {})
    for agg in meta.get("aggregates") or []:
        for sid, code in _source_failure_codes(agg.get("sources")).items():
            failed.setdefault(sid, code)
    out: list[dict[str, Any]] = []
    for spec in specs:
        if available is not None and spec.id not in available:
            continue
        n = counts.get(spec.id, 0)
        if spec.id not in used:
            status, reason = "not_selected", "선택 밖"
        elif spec.id in failed and not n:
            status, reason = "unavailable", failed[spec.id]
        else:
            status, reason = ("ok" if n else "empty"), ""
        out.append({**_status(spec.label or spec.id, status, n, reason), "source_id": spec.id})
    return out


# ── 대상 텍스트 해석 (plans/130 M-2·M-4·M-6 · LLM 0 · 상한 없음 — D-296 ④) ─────────────

@dataclass
class _Found:
    """근거 1종의 조회 결과 — (인스턴스 행, 근거 문구) · 유사 이름 · 실패 사유 · 한계 문구."""

    searched: str
    rows: list[tuple[dict[str, Any], list[str]]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    #: 조회가 답을 받았다(0건 포함) — 모두 실패면 「찾지 못함」이 아니라 「확인하지 못함」이다
    answered: bool = False


@dataclass
class _TargetText:
    """대상 텍스트 1건의 해석 결과 — 인스턴스는 (소스, 도메인, 인스턴스 id)로 한 번만 담는다."""

    text: str
    kind: str
    instances: dict[tuple[Any, Any, Any], dict[str, Any]] = field(default_factory=dict)
    #: 인스턴스 키 → 근거 문구(합집합 · 처음 나온 순서 — G-3 ①)
    evidence: dict[tuple[Any, Any, Any], list[str]] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    answered: bool = False

    def take(self, found: _Found) -> None:
        """근거 하나를 합친다 — 소스·인스턴스 id·이름이 없는 행은 대상으로 부를 수 없어 버린다."""
        for row, labels in found.rows:
            if not row.get("source_id") or row.get("instance_id") is None \
                    or not row.get("instance_name"):
                continue
            key = (row.get("source_id"), row.get("domain_id"), row.get("instance_id"))
            if key not in self.instances:
                self.instances[key] = dict(row)
                self.evidence[key] = []
            elif row.get("hostname") and not self.instances[key].get("hostname"):
                self.instances[key]["hostname"] = row["hostname"]
            self.evidence[key] += [x for x in labels if x not in self.evidence[key]]
        if found.answered:  # 실패한 검색은 「검색:」에 넣지 않는다(실패 고지로 따로 싣는다)
            self.searched.append(found.searched)
        self.failures += found.errors
        self.limits += [x for x in found.limits if x not in self.limits]
        self.answered = self.answered or found.answered
        self.suggestions = self.suggestions or found.suggestions


def _authorized_db_ids(isolated: dict[str, Any], app_config: Any) -> list[str]:
    """업무명 간선(E6)이 조회할 수 있는 DB — 활성 DB ∩ 이번 턴 사용자 DB 권한(D-232).

    관리자는 권한 목록과 무관하게 활성 DB 전체다(`authorized_db_ids` 규약).
    """
    getter = getattr(getattr(app_config, "multi_db", None), "get_active_db_ids", None)
    active = list(getter() or []) if callable(getter) else []
    return authorized_db_ids(active, isolated.get("allowed_db_ids"), isolated.get("user_role"))


async def _lookup(session: Any, calls: list[_Call], concurrency: int, scope: _JobScope) -> None:
    """해석용 목록 조회 — 작업으로 승격되면 마감까지 기다리고 못 끝나면 취소한다(첫 홉과 같은
    규칙 · 사용자가 맡긴 조회가 아니라 장부에 올리지 않는다). 예외는 호출 사유로 남긴다."""
    try:
        await _run_calls(session, calls, concurrency, scope)
        live = [c for c in calls if c.error is None and jobs.is_live(c.envelope)]
        if live:
            await asyncio.gather(*(_await_first_hop(session, c, scope) for c in live))
    except Exception as e:  # noqa: BLE001 — 근거 하나의 실패가 다른 근거를 막지 않는다
        # 사유는 「예외종류 (요청 rid)」, 원문은 로그 1줄(plans/184 S4b)
        reason = _exception_error(e, "대상 해석")
        for call in calls:  # 답이 없거나 아직 작업 핸들뿐인 호출은 실패다(행 0건으로 읽지 않는다)
            if call.error is None and (call.envelope is None or jobs.is_live(call.envelope)):
                call.error = reason


def _search_args(scope: _JobScope, domain_id: Any, **target: Any) -> dict[str, Any]:
    args: dict[str, Any] = {**target,
                            "thread_id": str(scope.thread_id) if scope.thread_id else None,
                            "owner": scope.owner}
    if domain_id is not None:
        args["domain_id"] = domain_id
    if scope.source_ids:  # 고른 소스(plans/134 M-5) — 해석 검색도 그 소스 안에서만
        args["source_ids"] = list(scope.source_ids)
    return args


def _merged_instances(resolved: list[_TargetText], covered: set[str]) -> list[dict[str, Any]]:
    """해석 인스턴스 합집합 — (소스, 도메인, 인스턴스 id)로 한 번 · 대상 텍스트·근거를 칸으로 단다.

    종전 경로가 부르는 서버(hostname)에 있는 인스턴스는 그 호출이 덮으므로 뺀다(두 번 조회 없음).
    """
    merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    texts: dict[tuple[Any, Any, Any], list[str]] = {}
    for res in resolved:
        for key, row in res.instances.items():
            host = str(row.get("hostname") or "").casefold()
            if host and host in covered:
                continue
            if key not in merged:
                merged[key] = {**row, "match_evidence": []}
                texts[key] = []
            if res.text not in texts[key]:
                texts[key].append(res.text)
            merged[key]["match_evidence"] += [x for x in res.evidence[key]
                                              if x not in merged[key]["match_evidence"]]
    return [{**row, "target_text": ", ".join(texts[key]),
             "match_evidence": " · ".join(row["match_evidence"])} for key, row in merged.items()]


def _instance_calls(view: ViewSpec, plan: WindowPlan, scope: _JobScope,
                    args: dict[str, Any] | None, instances: list[dict[str, Any]]) -> list[_Call]:
    """해석 인스턴스마다 1호출 — 인스턴스 이름 + 소스(+ 인스턴스 id를 받는 도구면 id).

    중복은 실제로 보내는 인자(소스 · 이름 · id를 받는 도구면 id)로 가린다 — 데이터 도구는
    `domain_id`를 받지 않아 두 도메인의 같은 이름·같은 id는 한 호출이 둘 다 돌려준다(V130-2).
    """
    by_id = view.tool in INSTANCE_ID_TOOLS
    seen: set[tuple[Any, ...]] = set()
    out: list[_Call] = []
    for inst in instances:
        key = (inst["source_id"], str(inst["instance_name"]).casefold(),
               inst["instance_id"] if by_id else None)
        if key in seen:
            continue
        seen.add(key)
        call_args = _call_args(view, None, plan, scope, args)
        call_args.update({"instance_name": inst["instance_name"],
                          "source_ids": [inst["source_id"]]})
        if by_id:
            call_args["instance_id"] = inst["instance_id"]
        out.append(_Call(view, None, call_args, instance={
            k: inst.get(k) for k in ("instance_name", "source_id", "domain_id", "instance_id")}))
    return out


def _answer_call(view: ViewSpec, instances: list[dict[str, Any]], texts: list[str]) -> _Call:
    """인스턴스 목록 보기 + 대상 텍스트 — 해석한 인스턴스 행이 답이다(목록 전체를 부르지 않는다).

    게이트웨이에 다시 묻지 않는 합성 호출이다 — 출처의 인자는 대상 텍스트(`targets`)다.
    """
    envelope = {"rows": instances, "row_count": len(instances), "tool": view.tool, "limits": []}
    return _Call(view, None, {"targets": texts}, envelope=envelope)


def _target_ref(row: dict[str, Any]) -> dict[str, Any]:
    """해석 인스턴스 → 대상 계약(`TargetRef` · plans/130 M-5 — 서버가 없어도 유효)."""
    return TargetRef(
        hostname=str(row.get("hostname") or "").strip() or None,
        apm_instance_name=str(row["instance_name"]),
        apm_instance_id=str(row["instance_id"]),
        apm_domain_id=None if row.get("domain_id") is None else str(row["domain_id"]),
        apm_source_id=str(row["source_id"]),
    ).model_dump()


def _targets_meta(resolved: list[_TargetText]) -> dict[str, Any]:
    """대상 텍스트 해석 경과(감사·계획 요약) — 텍스트별 근거 수 · 후보 · 실패와 인스턴스 계약."""
    texts: list[dict[str, Any]] = []
    instances: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for res in resolved:
        counts: dict[str, int] = {}
        for labels in res.evidence.values():
            for label in labels:
                counts[label] = counts.get(label, 0) + 1
        texts.append({"text": res.text, "kind": res.kind, "searched": list(res.searched),
                      "instances": len(res.instances), "evidence": counts,
                      "suggestions": list(res.suggestions), "failures": list(res.failures)})
        for key, row in res.instances.items():
            instances.setdefault(key, _target_ref(row))
    return {"texts": texts, "instances": list(instances.values())}
