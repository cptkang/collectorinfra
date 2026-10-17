"""WAS·미들웨어(APM) 1급 처리기 `apm_query` — plans/125 A-3 · 87 J5 본체 쪽 이관(D-281 ①).

**LLM 0회.** 분해 LLM 이 고른 보기(`views[]` — 레지스트리 `solutions[apm].views` 닫힌 어휘)를
게이트웨이 도구 고정 표로 바꾸고, 대상(hostname)·창을 코드가 정해 부른다(plans/125 §4.6 · Q-2).

- **활성일 때만 등록**: 엔드포인트(`MCP_SOURCE_ENDPOINTS` 의 `apm`)가 없으면 처리기도 분해 프롬프트
  줄도 없다 — 비활성 배포는 바이트 불변(신규 `enable_*` 0 · D-162 · D-251 ⑥). 고정 처리기 목록
  (`SUBAGENT_REGISTRY`)에는 넣지 않는다 — 활성일 때만 `active_extra_subagents`가 붙인다.
- **대상**: 선행 결과·이번 턴 식별자·직전 대상에서 hostname 을 고른다(`resolve_targets` 공용
  규칙 · 상한 없음 — D-296 ④). hostname 이 필요한 보기인데 대상이 없으면 인스턴스 목록 보기
  (`apm.instances`)를 **코드가 먼저** 부른다(첫 홉 삽입 — §4.2 · §4.3 ③) — 절단 없이 전
  인스턴스다(호스트 없는 인스턴스는 이름·소스·id 대상 · plans/134 M-5).
- **복수 보기·대상**(plans/134 M-5 · W3): 보기 수 상한이 없다. 한 보기의 대상이 2개 이상이면
  게이트웨이 `targets` 배치 1호출이고(작업 1개 — 예상·진행·부분 실패가 작업 기록에), 봉투
  `batch[i]`·`target_index` 행으로 대상별 가상 호출을 복원해 집계·판정·출처·실패를 종전처럼
  대상별로 남긴다. 대상 1개는 종전 호출 모양 그대로다. 이름 보기(`target: named`)는 서비스·업무
  이름 목록을 한 번에 싣고(해석은 게이트웨이), 이름을 줬는데 하나도 못 찾으면 「찾지 못함」이
  답이다(다른 조회로 대신하지 않는다 · D-290 ⑥).
- **소스 선택**(plans/134 M-5): 분해 `sources`(레지스트리 `solutions[apm].sources` id)를 코드가
  검증해 그 task의 모든 게이트웨이 호출에 `source_ids`로 싣는다. 모르는 id는 버리고 알리며, 준
  id가 전부 무효면 조회하지 않고 되묻는다(전 소스로 넓히지 않는다).
- **창**(plans/134 M-2 · W6 M-7): 보기 창 의미(`ViewSpec.window`)대로 요청 시간 해석(state
  `time_resolution`의 `event` — D-309 단일 출처 · 반개구간)을 **자르지 않고** 넘긴다(range). 기간이
  없거나 「현재·지금」이면 현재값이다. 현재값 전용 보기(current)에 기간을 말하면 그 사실을 고지한다.
  오래된 기간도 조회한다(종전 「하루 넘게 지난 기간」 거부 폐지) — 행이 없으면 「그 기간에 행 없음
  · 보존 기간 미확인」까지만 말한다. 해석이 없으면(옛 체크포인트) 파서 `time_range`(ISO)를 쓴다.
  추세 간격을 말하지 않은 긴 구간은 검증된 허용값(`ViewArgSpec.verified_values`)에서 고른다.
- **두 기간 비교**(plans/134 W6 A-1 · `window: compare`): 기준·비교 구간을 코드가 해석에서 정한다
  (`plan_compare_window` — 진행 중 · 롤링 · 지난 기간별 규칙) — 계산은 게이트웨이
  `apm_period_compare`.
- **선택 조건**(plans/134 M-3): 분해의 `view_args`를 보기 선언(`ViewArgSpec`)으로 검증해 도구 인자로
  싣는다. 모르는 이름·값은 버리고 `apm_unresolved_condition`으로 알린다. 파서 `limit`은 보기에
  `n`이 있고 조건에 없을 때 `n`이 된다. 「전체」는 계획 LLM이 낸 `full`뿐이다(단어 매칭 없음).
- **보기 선택 재시도**(plans/134 M-8 · SPEC §6.4): 분해 `views`가 task 영역(`areas` 중 APM 소유)을
  덮지 못하면 보기 카탈로그만 담은 짧은 선택 프롬프트로 같은 LLM 을 1회 더 부른다. 그래도 못
  덮으면 조회하지 않고 후보 보기(≤3)를 들어 되묻는다(`apm_unresolved_condition`). 원문 단어로 보기를
  고르지 않는다(132 계약). 일반 현황(영역 없음·응답시간·인스턴스)의 기본 보기는 종전대로다.
- **집계 운반**(plans/134 M-1): 봉투의 `summary`·`hourly`·`errors_by_type`·`was_signals`·`window`·
  `sources`·`partial`을 (보기, 대상)별로 `meta["aggregates"]`·`meta["was_signals"]`에 옮긴다.
  판정·창 집계·시 단위 합계·오류 유형별 건수는 결정적 줄(`answer_lines`)로 만들어 최종 답에
  싣는다.
- **실패**: 게이트웨이 미가용·도구 오류는 조회 안 함/행 0 으로 끝내고 사유를 싣는다. 재계획하지
  않는다(121 §4.7 「소스 불가」).
- **장기 작업**(plans/134 W0-B · SPEC-apm-question-coverage §3.8): 도구 인자에
  `owner`·`wait_seconds`를 싣는다. 작업 핸들이 오면 장부에 올리고 조회 마감까지 상태를 다시
  본다 — 끝나면 미리보기 행으로 종전처럼 답하고, 못 끝나면 **접수 답**
  (`source_status.status = "accepted"` · 고지 `apm_job_accepted` + `ref`)이다. 인라인보다 큰
  결과는 `apm_full_result_file`, 일부 실패는 `apm_partial_sources` 고지
  (`src/orchestration/apm_jobs.py`).
- **대상 텍스트**(plans/130 M-1·M-2·M-4): 분해 `targets`(인스턴스 이름·업무명 — 종류는 LLM,
  해석은 코드 · D-004)와 등록명 간선이 잇지 못한 파서 서버명을 게이트웨이 검색(`query`·`business`)
  ∥ 업무명 간선(E6 → E1r)으로 인스턴스로 풀고, 해석한 인스턴스마다 보기를 부른다(상한 없음 ·
  D-296 ④). 텍스트가 있었는데 0건이면 첫 홉으로 가지 않고 「찾지 못함」과 후보를 답한다
  (D-290 ⑥). 텍스트가 없으면 종전과 같다.
- **대상 표현**(plans/134 W5·W7 · `ViewSpec.target`): 비면 종전(필수 대상·첫 홉).
  `optional`은 이번 턴 식별자·선행 task 결과가 있으면 대상별, 없으면 hostname 없이 1회다
  (첫 홉 삽입 없음 · 직전 턴 대상은 사용자가 지시어로 가리킬 때만). `reference`는 앞 결과 행의
  참조 칸(`profile_ref`·`active_ref`·`guid`)을 고른다(M-6) — 후보는 같은 계획의 선행 task 행
  (`prior_result_rows`) → 직전 턴 결과(`conversation_context.previous_result_refs`)이고,
  순번(`view_args.ref`)은 계획 LLM이 내고 코드가 범위·종류를 검증한다(단일 후보 = 그 행 ·
  여럿 = 되묻기 · 없음 = 조회하지 않고 사유). 필수 조건(`ViewArgSpec.required` — 예 PID)이
  없거나 무효면 그 보기는 조회하지 않고 되묻는다.
- **경계**: 게이트웨이 패키지 import 0(D-274 ③) · 인스턴스 정합 재구현 0(D-274 ⑤ — 게이트웨이 도구를
  부른다) · 반환 봉투는 해석만 하고 바꾸지 않는다. 벤더 리터럴은 레지스트리 데이터에만 둔다.

계층: orchestration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.clients.source_mcp_client import SessionFactory, SourceMcpError, open_source_session
from src.config import AppConfig
from src.domain import disclosure as disc
from src.domain.query_time import QueryTime
from src.domain.result_refs import TABLE_LABELS_KEY, extract_result_refs, ref_label
from src.observability import run_capture
from src.orchestration import apm_jobs as jobs
from src.orchestration.agent_spec import SubAgentSpec
from src.orchestration.db_access import access_denied_result
from src.orchestration.entity_link import (
    LINKED,
    UNLINKED,
    LinkEntry,
    ledger_line,
    ledger_summary,
    link_business_names,
    link_hostnames,
)
from src.orchestration.investigation_audit import BACKEND_APM, audited_investigation
from src.routing.db_authz import (
    SOURCE_ACCESS_DENIED_MESSAGE,
    is_source_allowed,
)
from src.routing import apm_source_select as source_select
from src.routing.registry import ViewSpec, get_registry
from src.utils.json_extract import extract_json_from_response
from src.utils.prior_dependency import NOTE_BRIDGE, NOTE_TRACE
from src.utils.prior_targets import TargetRef
# 거부·실패 사유 키 · 표시용 행 축약 표지 — 정본 1곳(plans/166 X3) · 이 모듈 이름으로 재노출한다.
from src.utils.task_result_keys import DEGRADED_KEY as DEGRADED_KEY
from src.utils.task_result_keys import DISPLAY_CUT_KEY as DISPLAY_CUT_KEY
from src.utils.user_error import request_id

# plans/180 W1에서 `apm/`로 옮긴 정의 — 이 모듈 본문이 쓰는 이름과, 테스트·스크립트가
# 옛 이름으로 읽는 재노출(`aq.<이름>`·`from … apm_query import …`)만 남겼다
# (plans/166 단계 8 — D-337 ① 제거 사이클).
from src.orchestration.apm.common import (  # noqa: F401
    _AGGREGATE_KEYS as _AGGREGATE_KEYS,
    _NOTICE_TEXT as _NOTICE_TEXT,
    ANSWER_LINES_KEY as ANSWER_LINES_KEY,
    apm_endpoint as apm_endpoint,
    APM_QUERY_AGENT as APM_QUERY_AGENT,
    APM_SYSTEM as APM_SYSTEM,
    COMPARE_ASK_NOTE as COMPARE_ASK_NOTE,
    COMPARE_ELAPSED_NOTE as COMPARE_ELAPSED_NOTE,
    COMPARE_OVERLAP_NOTE as COMPARE_OVERLAP_NOTE,
    DEFAULT_VIEW as DEFAULT_VIEW,
    DISPLAY_CELL_MAX as DISPLAY_CELL_MAX,
    EMPTY_PERIOD_NOTE as EMPTY_PERIOD_NOTE,
    FLAT_VIEW_ARGS_KEY as FLAT_VIEW_ARGS_KEY,
    INSTANCES_VIEW as INSTANCES_VIEW,
    MAX_SELECTION_CANDIDATES as MAX_SELECTION_CANDIDATES,
    META_KEY as META_KEY,
    PERIOD_ONLY_ASK_NOTE as PERIOD_ONLY_ASK_NOTE,
    PERIOD_ONLY_RANKING_METRICS as PERIOD_ONLY_RANKING_METRICS,
    RANKING_VIEW as RANKING_VIEW,
    sanitize_sources as sanitize_sources,
)
from src.orchestration.apm.args import (  # noqa: F401
    _arg_text as _arg_text,
    _COERCERS as _COERCERS,
    _missing_required as _missing_required,
    _NAMED_NOUN as _NAMED_NOUN,
    _target_text as _target_text,
    _WINDOW_TEXT as _WINDOW_TEXT,
    validate_view_args as validate_view_args,
)
from src.orchestration.apm.window import (  # noqa: F401
    _period_list as _period_list,
    auto_interval as auto_interval,
    plan_compare_window as plan_compare_window,
    plan_window as plan_window,
    WindowPlan as WindowPlan,
)
from src.orchestration.apm.selection import (  # noqa: F401
    _audit_source_ask as _audit_source_ask,
    _selected_args as _selected_args,
    _Selection as _Selection,
    _SELECTION_SYSTEM as _SELECTION_SYSTEM,
    _source_clarification as _source_clarification,
    _uncovered as _uncovered,
    SOURCE_CLARIFICATION_KEY as SOURCE_CLARIFICATION_KEY,
    SOURCE_LABEL_KEY as SOURCE_LABEL_KEY,
)
from src.orchestration.apm.calls import (  # noqa: F401
    _batch_parents as _batch_parents,
    _batched as _batched,
    _blocked_refusal as _blocked_refusal,
    _Call as _Call,
    _CODE_PHRASE_RE as _CODE_PHRASE_RE,
    _envelope_error as _envelope_error,
    _EXC_TYPE_RE as _EXC_TYPE_RE,
    _FIRST_HOP_TIMEOUT_ERROR as _FIRST_HOP_TIMEOUT_ERROR,
    _GATEWAY_CODE_RE as _GATEWAY_CODE_RE,
    _JobScope as _JobScope,
    _refusal as _refusal,
    _run_calls as _run_calls,
    _status as _status,
    _unbatched as _unbatched,
    ROWS_IN_FILE_KEY as ROWS_IN_FILE_KEY,
)
from src.orchestration.apm.jobs_notices import (  # noqa: F401
    _await_first_hop as _await_first_hop,
    _await_job as _await_job,
    _gateway_disclosures as _gateway_disclosures,
    _in_file_tail as _in_file_tail,
    _progress_text as _progress_text,
)
from src.orchestration.apm.targets import (  # noqa: F401
    _authorized_db_ids as _authorized_db_ids,
    _demonstrative as _demonstrative,
    _EVIDENCE_TEXT as _EVIDENCE_TEXT,
    _Found as _Found,
    _inherited_notice as _inherited_notice,
    _int_setting as _int_setting,
    _last_targets as _last_targets,
    _lookup as _lookup,
    _merged_instances as _merged_instances,
    _named_texts as _named_texts,
    _per_target_views as _per_target_views,
    _search_args as _search_args,
    _service_scoped as _service_scoped,
    _shown as _shown,
    _source_failure_codes as _source_failure_codes,
    _source_status_rows as _source_status_rows,
    _suggestion_names as _suggestion_names,
    _target_texts as _target_texts,
    _targets_meta as _targets_meta,
    _TargetText as _TargetText,
    _too_long as _too_long,
    _uses_target as _uses_target,
    _with_source_labels as _with_source_labels,
    default_views as default_views,
    explicit_targets as explicit_targets,
    inherited_targets as inherited_targets,
    INSTANCE_ID_TOOLS as INSTANCE_ID_TOOLS,
    LAST_TARGETS_KEY as LAST_TARGETS_KEY,
    resolve_apm_targets as resolve_apm_targets,
    sanitize_targets as sanitize_targets,
    scoped_targets as scoped_targets,
    TARGET_KINDS as TARGET_KINDS,
    TARGET_TEXT_MAX as TARGET_TEXT_MAX,
)
from src.orchestration.apm.reference import (  # noqa: F401
    _choose_text as _choose_text,
    _REF_TEXT as _REF_TEXT,
    _RefPick as _RefPick,
    _TABLE_LABEL_MAX as _TABLE_LABEL_MAX,
    _tables_text as _tables_text,
    _trace_window as _trace_window,
)
from src.orchestration.apm.first_hop import (  # noqa: F401
    _first_hop_kind as _first_hop_kind,
    _first_hop_text as _first_hop_text,
    _host_sources as _host_sources,
    _queried_instances as _queried_instances,
    _step_call as _step_call,
    _target_rows as _target_rows,
)
from src.orchestration.apm.context import (  # noqa: F401
    _RunCtx as _RunCtx,
)
from src.orchestration.apm.builders import (  # noqa: F401
    _build_calls as _build_calls,
    _CALL_BUILDERS as _CALL_BUILDERS,
)
from src.orchestration.apm.collect import (  # noqa: F401
    _answer_lines as _answer_lines,
    _dedupe_signals as _dedupe_signals,
    _num as _num,
    _TRACE_DEFAULT_WINDOW_MARK as _TRACE_DEFAULT_WINDOW_MARK,
    display_rows as display_rows,
)

logger = logging.getLogger(__name__)

#: 테스트가 모의 세션 공장을 끼운다(이 호스트 루트 파이썬에는 `mcp` 가 없다).
_SESSION_FACTORY: SessionFactory | None = None
#: `apm_jobs._call`도 이 값을 호출 시점에 읽는다(전역 이름 조회 — monkeypatch 반영 · plans/183).
jobs.session_factory_provider = lambda: _SESSION_FACTORY


# ── 활성 · 어휘 ──────────────────────────────────────────────────────────────

def apm_active(app_config: Any) -> bool:
    """APM 시스템이 활성인가(레지스트리 등재 + 엔드포인트 설정)."""
    return bool(get_registry().views_of(APM_SYSTEM)) and apm_endpoint(app_config) is not None


def apm_views() -> tuple[ViewSpec, ...]:
    """레지스트리 보기 표(선언 순서)."""
    return get_registry().views_of(APM_SYSTEM)


def known_view_ids() -> frozenset[str]:
    return frozenset(v.id for v in apm_views())


def sanitize_views(raw: Any) -> list[str]:
    """LLM 이 낸 보기 목록 → 닫힌 어휘만(순서 유지 · 중복 제거 · 개수 상한 없음 — plans/134 M-5)."""
    items = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    known = known_view_ids()
    kept: list[str] = []
    for item in items:
        code = str(item).strip() if isinstance(item, str) else ""
        if code in known and code not in kept:
            kept.append(code)
    return kept


def sanitize_view_args(raw: Any, views: list[str]) -> dict[str, Any]:
    """분해 `view_args`의 형태만 정제한다 — 고른 보기 id의 조건 묶음만 남긴다.

    값·형식 검증은 처리기(`validate_view_args`)가 보기 선언으로 하고, 버린 조건을 고지한다(M-3) —
    그래서 고른 보기의 묶음은 형식이 틀려도 여기서 버리지 않는다(침묵 탈락 금지).
    보기 id가 아닌 키(보기 id 없이 낸 평면 조건 `{"level": "fatal"}`)는 `FLAT_VIEW_ARGS_KEY` 아래로
    모아 처리기까지 옮긴다 — 조회할 보기가 하나면 그 보기 것으로 읽고, 여럿이면 버리고 알린다
    (W1 검증 L-1).
    """
    if not isinstance(raw, dict):
        return {}
    known = known_view_ids()
    out: dict[str, Any] = {str(vid): (dict(args) if isinstance(args, dict) else args)
                           for vid, args in raw.items()
                           if str(vid) in views and args not in (None, {}, [], "")}
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if str(key) == FLAT_VIEW_ARGS_KEY and isinstance(value, dict):
            flat.update({str(k): v for k, v in value.items()})
        elif str(key) not in known:
            flat[str(key)] = value
    if flat:
        out[FLAT_VIEW_ARGS_KEY] = flat
    return out


def render_agent_line() -> str:
    """분해 프롬프트의 담당 목록 한 줄(활성일 때만 삽입)."""
    return f"- **{APM_QUERY_AGENT}**: {APM_QUERY_SPEC.purpose}"


def render_source_line() -> str:
    """분해 프롬프트의 소스 선택 안내 한 줄(plans/134 M-5 — 레지스트리 소스 표 파생 · D-053).

    소스가 2개 이상 선언됐을 때만 렌더한다(하나뿐이면 고를 것이 없다 — 빈 문자열).
    """
    sources = get_registry().sources_of(APM_SYSTEM)
    if len(sources) < 2:
        return ""
    listed = " · ".join(f"`{s.id}`({s.label or s.id})" for s in sources)
    # 지목이 없을 때 무엇을 조회할지는 처리기의 소스 사다리가 정한다(plans/147 · D-322 — 단어 ·
    # 대화 선택 · 되묻기) — 프롬프트는 레지스트리 기준 그대로다(렌더 결정성)
    return (f"- 사용자가 조회할 APM 소스를 지목했으면(「{sources[0].label or sources[0].id}만」)"
            f" `sources`에 소스 id를 넣습니다: {listed}. 지목하지 않았으면 비웁니다.\n")


def render_view_rows() -> str:
    """분해 프롬프트의 보기 표 — 레지스트리 파생(사본 금지 · D-053).

    보기마다 창 의미와 선택 조건(`view_args`)·예문을 렌더한다(plans/134 M-3 · 활성 배포에서만).
    """
    reg = get_registry()
    labels = {c.code: c.label for c in reg.capability_specs()}
    lines = []
    for view in apm_views():
        parts = [_target_text(view)]
        parts += [p for p in (_WINDOW_TEXT.get(view.window, ""), view.limit) if p]
        text = view.label or labels.get(view.capability, view.capability)
        line = f"- `{view.id}`: {text} ({' · '.join(parts)})"
        if view.args:
            line += "\n  - 조건(view_args): " + "; ".join(_arg_text(a) for a in view.args)
        if view.examples:
            line += "\n  - 예: " + " / ".join(view.examples)
        lines.append(line)
    return "\n".join(lines)


# ── 대상 텍스트 (plans/130 M-1·M-2 — 인스턴스 이름·업무명) ──────────────────────────

def _unscoped_notices(task: dict[str, Any], isolated: dict[str, Any], live: list[ViewSpec],
                      meta: dict[str, Any], source: str) -> list[disc.Disclosure]:
    """전 대상 보기가 말한 서버로 좁히지 않았다는 의무 고지(V34-4).

    이번 턴 식별자(복합 계획 제외 · 지시어면 직전 대상 — `explicit_targets`)와 인스턴스 이름 대상
    텍스트가 있으면 그 보기는 전체(또는 서비스) 범위로 조회하고 그 사실을 알린다.
    """
    views = [v for v in live if _service_scoped(v)]
    if not views:
        return []
    spoken = [str(t.hostname or t.server_name or t.ip) for t in explicit_targets(isolated)
              if t.hostname or t.server_name or t.ip]
    spoken += [t["text"] for t in sanitize_targets(task.get("targets")) if t["kind"] == "instance"]
    spoken = list(dict.fromkeys(_shown(x) for x in spoken))
    if not spoken:
        return []
    shown = ", ".join(spoken[:5]) + (f" 외 {len(spoken) - 5}개" if len(spoken) > 5 else "")
    out: list[disc.Disclosure] = []
    for view in views:
        meta.setdefault("unresolved", []).append(
            {"view": view.id, "conditions": [f"target={x}" for x in spoken], "queried": True})
        out.append(disc.make(
            disc.APM_UNRESOLVED_CONDITION,
            f"{_view_label(view)}: 말한 서버({shown})로는 좁히지 않았습니다 — 서버별 값은"
            " 응답시간 등 서버 보기로 물어 주세요.", source=source))
    return out


def _named_unresolved(calls: list[_Call], meta: dict[str, Any], source: str,
                      ) -> tuple[list[_Call], list[disc.Disclosure]]:
    """이름을 줬는데 행 0건 + 게이트웨이 미해결 고지인 이름 보기 호출 — 「찾지 못함」(D-290 ⑥).

    다른 조회로 대신하지 않는다. 그 이름은 조회하지 못한 대상으로 센다(부분 결과 · 감사
    degraded). 미해결 문구는 게이트웨이 고지(`apm_unresolved_condition`)가 싣고, 여기서는
    「대신 조회하지 않음」과 유사 이름 후보를 더한다.
    """
    missing: list[_Call] = []
    out: list[disc.Disclosure] = []
    for call in calls:
        env = call.envelope or {}
        if not call.named or call.error or call.accepted or env.get("rows"):
            continue
        if not any(isinstance(d, dict) and d.get("kind") == disc.APM_UNRESOLVED_CONDITION
                   for d in env.get("disclosures") or []):
            continue
        missing.append(call)
        names = ", ".join(call.named)
        meta["failures"].append({"view": call.view.id, "hostname": None, "target": names,
                                 "reason": f"대상 '{names}' 해석 0건"})
        arg = call.view.target_arg or "service"  # 전 대상 보기는 서비스로 좁힌다(V34-4)
        noun = _NAMED_NOUN.get(arg, arg)
        hints = _suggestion_names(env)
        tail = (f" 비슷한 이름: {' · '.join(hints)} — 자동으로 고르지 않았습니다." if hints else "")
        out.append(disc.make(
            disc.APM_UNRESOLVED_CONDITION,
            f"{_view_label(call.view)}: 말한 {noun}('{names}')을(를) 찾지 못해 다른 {noun}로 대신"
            f" 조회하지 않았습니다.{tail}", source=source))
    return missing, out


# ── 실행 ──────────────────────────────────────────────────────────────────────

#: 사유를 게이트웨이가 인자·대상 검사로 직접 짓는 오류 코드(SPEC-apm-gateway §3.2) — 사유까지
#: 보인다(모르는 지표 후보 · 대상 불일치). 그 밖 코드(제니퍼 응답·연결·작업 실패)는 코드만.
_GUIDANCE_CODES: frozenset[str] = frozenset({
    "invalid_argument", "instance_unresolved", "profile_ref_mismatch",
})


def _call_failure_text(label: str, failures: list[tuple[str | None, str]]) -> str:
    """호출 실패의 답 본문 문구 — 대상·오류 코드와 요청 번호만, 사유 원문은 로그(plans/163 S3+).

    사유에는 제니퍼 응답 원문과 예외 문자열이 섞인다. 오류 코드(소문자 식별자)는 게이트웨이 계약
    값이라 그대로 보이고, 게이트웨이가 직접 지은 안내 사유(`_GUIDANCE_CODES`)는 사유까지 보인다.

    Args:
        label: 소스 표시명
        failures: (대상 — 없으면 None, 호출 오류 원문) 목록
    """
    rid = request_id()
    logger.warning("%s 호출 실패 (요청 %s): %s", label, rid,
                   "; ".join(f"{who or '-'}: {err}" for who, err in failures))
    shown: list[str] = []
    for who, err in failures[:3]:
        text = ": ".join(x for x in (who, _shown_call_error(err)) if x)
        if text:
            shown.append(text)
    tail = f"(요청 {rid} · 상세 사유는 서버 로그)"
    return f"{'; '.join(shown)} {tail}" if shown else tail


def _shown_call_error(err: str) -> str | None:
    """호출 오류 중 화면에 보일 부분 — 안내 코드면 사유까지, 그 밖은 코드만(코드 없으면 None).

    코드가 원문 없이 지은 사유(`_FIRST_HOP_TIMEOUT_ERROR`·`_CODE_PHRASE_RE`)는 그대로, 예외
    종류 이름 머리는 종류 이름만 보인다.
    """
    if err == _FIRST_HOP_TIMEOUT_ERROR or _CODE_PHRASE_RE.fullmatch(err):
        return err
    exc = _EXC_TYPE_RE.match(err)
    if exc:
        return exc.group(1)
    code = _GATEWAY_CODE_RE.match(err)
    if not code:
        return None
    return err if code.group(1) in _GUIDANCE_CODES else code.group(1)


def _public_call_error(err: str, *, where: str) -> str:
    """단계·고지에 담는 호출 오류 — 코드(안내 코드는 사유까지)와 요청 번호만, 원문은 로그.

    첫 홉(`step["error"]`·`rank_error`)·결과 파일 읽기·대상 해석 검색의 사유는 고지·결정적 요약
    (요약 LLM 입력)·조회하지 않은 답에 실린다(plans/163 S3+).

    Args:
        err: 호출 오류 원문
        where: 로그에 남길 단계 이름
    """
    rid = request_id()
    logger.warning("%s %s 호출 오류 (요청 %s): %s", APM_QUERY_AGENT, where, rid, err)
    shown = _shown_call_error(err)
    return f"{shown} (요청 {rid})" if shown else f"호출 실패 (요청 {rid})"


def _failed_targets_text(label: str, failed: list[tuple[str, str]]) -> str:
    """「조회하지 못한 대상 N건」 줄 — 대상(오류 코드)과 요청 번호만, 원문은 로그(plans/163 S3+).

    접수 답(`_accepted_answer`)과 결정적 요약(`_summary` — 요약 LLM 입력)이 같이 쓴다.

    Args:
        label: 소스 표시명
        failed: (대상 이름, 호출 오류 원문) 목록
    """
    rid = request_id()
    logger.warning("%s 조회하지 못한 대상 (요청 %s): %s", label, rid,
                   "; ".join(f"{who}: {err}" for who, err in failed))
    shown = ", ".join(
        f"{who}({err[:60]})" if err else who
        for who, err in ((who, _shown_call_error(err)) for who, err in failed[:3])
    )
    return f"조회하지 못한 대상 {len(failed)}건: {shown} (요청 {rid} · 상세 사유는 서버 로그)"


# ── 보기 선택 재시도 (plans/134 M-8 · SPEC §6.4) ───────────────────────────────

def _apm_areas(task: dict[str, Any]) -> list[str]:
    """task 영역(`areas` — 분해 D-295) 중 APM 소유인 것(순서 유지)."""
    reg = get_registry()
    return [str(a) for a in task.get("areas") or []
            if APM_SYSTEM in reg.capability_owners(str(a))]


def _area_text(areas: list[str]) -> str:
    labels = {c.code: c.label for c in get_registry().capability_specs()}
    return " · ".join(labels.get(a) or a for a in areas)


async def _retry_selection(
    llm: Any, task: dict[str, Any], areas: list[str], isolated: dict[str, Any],
) -> tuple[list[str], dict[str, Any], str]:
    """보기 카탈로그만 담은 짧은 프롬프트로 LLM 1회 — (보기, 조건, 오류 사유).

    task 질의와 함께 사용자 원문을 준다 — 조건 값이 실제로 말한 것인지 LLM이 원문으로 보게 한다
    (V-2: 재시도가 보기 표 예문 값을 조건으로 내던 결함).
    """
    if llm is None or not hasattr(llm, "ainvoke"):
        return [], {}, "LLM 없음"
    system = (_SELECTION_SYSTEM.replace("<areas>", _area_text(areas))
              .replace("<rows>", render_view_rows()))
    question = str(task.get("sub_query") or "")
    original = str(isolated.get("original_user_query") or "").strip()
    if original and original != question.strip():
        question = f"{question}\n\n사용자 원문: {original}"
    try:
        response = await llm.ainvoke([SystemMessage(content=system),
                                      HumanMessage(content=question)])
        parsed = extract_json_from_response(getattr(response, "content", response))
    except Exception as e:  # noqa: BLE001 — 재시도 실패는 미해결로 끝낸다(사유는 메타·로그)
        return [], {}, f"{type(e).__name__}: {str(e)[:120]}"
    run_capture.emit("apm_view_reselect",
                     {"parsed": parsed, "areas": areas, "thread_id": isolated.get("thread_id")})
    if not isinstance(parsed, dict):
        return [], {}, "JSON 아님"
    views = sanitize_views(parsed.get("views"))
    return views, sanitize_view_args(parsed.get("view_args"), views), ""


async def _select_views(
    task: dict[str, Any], isolated: dict[str, Any], llm: Any, by_id: dict[str, ViewSpec],
) -> _Selection:
    """분해 보기와 task 영역을 대조해 조회할 보기를 정한다(SPEC §6.4).

    1. 분해 보기가 APM 영역을 모두 덮거나, 보기가 없고 영역이 일반 현황뿐이면 종전대로(분해 보기 ·
       기본 보기 — D-293).
    2. 덮지 못하면 보기 카탈로그만 담은 선택 프롬프트로 같은 LLM 을 1회 부른다(D-299 ⑤).
    3. 재시도 보기가 영역을 모두 덮으면 그 보기로 조회한다.
    4. 분해와 재시도가 영역 밖의 같은 보기를 냈으면(합의) 영역 라벨보다 보기를 믿고 그 보기(와
       영역을 덮는 보기)를 조회한다(`result=agreed`).
    5. 일부 영역만 덮으면(분해·재시도 보기 중 요청 영역의 보기 — 둘 다 없으면 기본 보기) 덮은 보기는
       조회하고, 못 덮은 영역만 후보 보기(≤3 · 영역마다 최소 1개)를 들어 되묻는다 — 정상 답을 버리지
       않는다.
    6. 덮은 영역이 없으면 조회하지 않고 되묻는다 — 명시 기능을 응답시간·목록 보기로 바꾸지 않는다.
       원문 단어로 보기를 고르지 않는다(132 계약).
    """
    planned = sanitize_views(task.get("views"))
    raw_args = task.get("view_args")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    areas = _apm_areas(task)
    info: dict[str, Any] = {"areas": areas, "planned": planned, "retried": False}
    missing = _uncovered(planned, areas, by_id)
    if not missing:
        info["result"] = "planned" if planned else "default"
        return _Selection(planned or default_views(isolated), args, info)
    started = time.monotonic()
    views, retry_args, error = await _retry_selection(llm, task, areas, isolated)
    info.update(retried=True, latency_ms=round((time.monotonic() - started) * 1000, 1),
                retried_views=views)
    if error:
        info["error"] = error
    if views and not _uncovered(views, areas, by_id):
        info["result"] = "retried"
        logger.info("%s 보기 선택 재시도 성공: 영역=%s 분해=%s → %s (%.0fms)", APM_QUERY_AGENT,
                    areas, planned, views, info["latency_ms"])
        return _Selection(views, _selected_args(views, args, retry_args, planned), info)
    pool = list(dict.fromkeys([*planned, *views])) or default_views(isolated)
    covering = [v for v in pool if v in by_id and by_id[v].capability in areas]
    # 분해와 재시도(두 LLM 출력)가 영역 라벨 밖의 같은 보기로 모이면 라벨보다 보기를 믿는다
    # (W2 검증 B7 — 영역 라벨 하나가 틀려 합의한 정답 보기를 버리던 거짓 미해결)
    agreed = [v for v in views if v in planned]
    off_label = [v for v in agreed if by_id[v].capability not in areas]
    if off_label:
        chosen = list(dict.fromkeys([*covering, *agreed]))
        info.update(result="agreed", agreed=agreed)
        logger.info("%s 보기 선택 합의: 영역=%s 분해=%s 재시도=%s → %s · 영역 밖 합의 보기=%s"
                    " (%.0fms)", APM_QUERY_AGENT, areas, planned, views, chosen, off_label,
                    info["latency_ms"])
        return _Selection(chosen, _selected_args(chosen, args, retry_args, planned), info)
    covered = {by_id[v].capability for v in covering}
    still = [a for a in areas if a not in covered]
    candidates = _candidates(still)
    info["candidates"] = [v.id for v in candidates]
    label = get_registry().system_label(APM_SYSTEM)
    choose = (" 다음 중 원하는 것을 골라 다시 물어 주세요: "
              + " · ".join(f"「{_view_label(v)}」" for v in candidates)) if candidates else ""
    if covering:
        info.update(result="partial", uncovered=still)
        logger.info("%s 보기 선택 일부 해결: 영역=%s 분해=%s 재시도=%s → %s · 못 덮음=%s (%.0fms)",
                    APM_QUERY_AGENT, areas, planned, views, covering, still, info["latency_ms"])
        text = (f"요청하신 정보 중 {_area_text(still)}에 맞는 {label} 조회를 정하지 못해 그 부분은"
                " 조회하지 않았습니다." + choose)
        return _Selection(covering, _selected_args(covering, args, retry_args, planned), info,
                          uncovered=text)
    info["result"] = "unresolved"
    logger.info("%s 보기 선택 미해결: 영역=%s 분해=%s 재시도=%s 사유=%s (%.0fms)",
                APM_QUERY_AGENT, areas, planned, views, error or "영역 미충족",
                info["latency_ms"])
    text = (f"요청하신 정보({_area_text(still)})에 맞는 {label} 조회를 정하지 못해 조회하지"
            " 않았습니다." + choose)
    return _Selection([], {}, info, unresolved=text)


def _candidates(areas: list[str]) -> list[ViewSpec]:
    """못 덮은 영역의 후보 보기 ≤`MAX_SELECTION_CANDIDATES` — 영역마다 1개씩 돌아가며 고른다.

    영역이 여럿이면 각 영역이 최소 1개(영역이 상한보다 많으면 앞 영역부터 1개씩)를 얻는다 — 보기 표
    순서로 앞에서 잘라 한 영역에 쏠리지 않게 한다(W2 검증 B3). 영역 안 순서는 보기 표 순서다.
    """
    per_area = [[v for v in apm_views() if v.capability == a] for a in areas]
    picked: list[ViewSpec] = []
    for rank in range(max((len(options) for options in per_area), default=0)):
        for options in per_area:
            if rank < len(options) and len(picked) < MAX_SELECTION_CANDIDATES:
                picked.append(options[rank])
    return picked


def _plan_view_args(
    task: dict[str, Any], views: list[str], by_id: dict[str, ViewSpec], parsed: dict[str, Any],
    meta: dict[str, Any], notices: list[disc.Disclosure], source: str, *, composite: bool = False,
    blocked: dict[str, str] | None = None, deferred: dict[str, list[str]] | None = None,
    bad_refs: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """보기별 검증된 선택 조건(plans/134 M-3 · SPEC §6.3).

    - 분해 `view_args`를 `validate_view_args`로 거른다. 버린 조건(모르는 이름·형식 밖 값)은
      `apm_unresolved_condition`으로 알리고 **보기는 조회한다** — 선택 조건이 무효라고 정상 조회를
      막지 않는다(W1 검증 H-2). `null`은 미지정(무고지).
    - **필수 조건**(`ViewArgSpec.required` — plans/134 W7 예 `process_id`)이 없거나 무효면 그 보기만
      조회하지 않고 되묻는다(`blocked[보기] = 되묻기 문구` · 의무 고지). 미지정 조건의 기본값
      (`ViewArgSpec.default`)은 검증 뒤에 채운다. 참조 보기의 버린 조건은 `deferred`로 넘겨 참조
      선택 뒤에 고지하고, 그중 순번(`ref`)의 원값은 `bad_refs`에 남긴다(낸 순번이 형식 밖이면
      후보가 하나여도 되묻는다 — R-2).
    - 보기 id 없는 평면 조건(`FLAT_VIEW_ARGS_KEY`)은 조회할 보기가 하나면 그 보기 것으로 읽고(같은
      이름은 보기 묶음이 이긴다), 그 밖이면 버리고 알린다(W1 검증 L-1).
    - 파서 `limit`(사용자가 말한 개수)은 보기에 `n`이 있고 조건에 `n`·`full`이 없을 때 `n`이 된다.
      단일 task 계획에서만이다 — 파서 값은 질의 전체 것이라 복합 계획에서는 다른 task 몫일 수
      있다(그때는 계획 LLM이 그 task에 낸 `n`만 쓴다).
    """
    raw_task = task.get("view_args")
    raw_all: dict[str, Any] = dict(raw_task) if isinstance(raw_task, dict) else {}
    flat = raw_all.pop(FLAT_VIEW_ARGS_KEY, None)
    flat = {k: v for k, v in flat.items() if v is not None} if isinstance(flat, dict) else None
    if flat:
        bundle = raw_all.get(views[0]) if len(views) == 1 else None
        if len(views) == 1 and (bundle is None or isinstance(bundle, dict)):
            raw_all[views[0]] = {**flat, **(bundle or {})}
        else:
            dropped = [f"{str(name)[:40]}(보기 미지정)" for name in flat]
            meta.setdefault("unresolved", []).append({"view": None, "conditions": dropped,
                                                      "queried": True})
            notices.append(disc.make(
                disc.APM_UNRESOLVED_CONDITION,
                f"보기를 정하지 않은 조건({', '.join(dropped)})은 어느 보기의 조건인지 정할 수 없어"
                " 쓰지 않았습니다.", source=source))
    limit = None if composite else parsed.get("limit")
    has_limit = isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1
    planned: dict[str, dict[str, Any]] = {}
    for vid in views:
        view = by_id[vid]
        args, rejected = validate_view_args(view, raw_all.get(vid))
        missing = _missing_required(view, args, raw_all.get(vid))
        if missing and blocked is not None:
            blocked[vid] = _required_text(view, missing)
            meta.setdefault("unresolved", []).append({"view": vid, "conditions": missing,
                                                      "queried": False})
            notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, blocked[vid], source=source))
            continue
        arg_names = {a.name for a in view.args}
        if has_limit and "n" in arg_names and "n" not in args and not args.get("full"):
            args["n"] = limit
        for spec in view.args:
            if spec.default is not None:
                args.setdefault(spec.tool_arg or spec.name, spec.default)
        if args:
            planned[vid] = args
        if not rejected:
            continue
        if deferred is not None and view.target == "reference":
            # 참조 보기는 조회 여부가 참조 선택 뒤에 정해진다 — 고지는 그때 싣는다(「빼고
            # 조회했습니다」와 되묻기가 함께 나가지 않게)
            deferred[vid] = rejected
            raw = raw_all.get(vid)
            raw_ref = raw.get("ref") if isinstance(raw, dict) else None
            if bad_refs is not None and raw_ref is not None and "ref" not in args:
                bad_refs[vid] = raw_ref
            continue
        listed = ", ".join(rejected)
        meta.setdefault("unresolved", []).append({"view": vid, "conditions": rejected,
                                                  "queried": True})
        notices.append(disc.make(
            disc.APM_UNRESOLVED_CONDITION,
            f"{_view_label(view)}: 해석하지 못한 조건({listed})은 빼고 조회했습니다.",
            source=source))
    unknown = sorted(set(map(str, raw_all)) - set(views))
    if unknown:
        logger.info("%s: 고르지 않은 보기의 조건은 쓰지 않음: %s", APM_QUERY_AGENT, unknown)
    return planned


def _required_text(view: ViewSpec, missing: list[str]) -> str:
    """필수 조건 되묻기 — 무엇이 필요한지와 묻는 예(보기 예문의 질문 부분)."""
    names = {a.name: a.label or a.name for a in view.args}
    wanted = ", ".join(names.get(m.split("(")[0].split("=")[0], m) for m in missing)
    example = view.examples[0].split(" → ")[0] if view.examples else ""
    return (f"{_view_label(view)}: 필요한 조건({wanted})이 없거나 해석하지 못해 조회하지 않았습니다"
            f"({', '.join(missing)}). 값을 넣어 다시 물어 주세요"
            + (f"(예: {example})." if example else "."))


def _plan_sources(task: dict[str, Any], label: str, meta: dict[str, Any],
                  notices: list[disc.Disclosure], source: str) -> tuple[tuple[str, ...], str]:
    """분해 `sources` → 검증된 소스 id(plans/134 M-5) — (쓸 소스, 되묻기 문구).

    - 고르지 않았으면 `((), "")` — 소스를 좁히지 않는다(종전).
    - 레지스트리 소스 표(`solutions[apm].sources`)에 없는 id는 버리고 `apm_unresolved_condition`으로
      알린다(남은 소스로 조회).
    - **준 id가 전부 무효면 조회하지 않고 되묻는다** — 전 소스로 넓히지 않는다(선택지 = 소스 표).
    - 소스 표가 없는 배포(단일 설정)는 고를 소스가 없어 분해에 소스 칸을 렌더하지 않는다 — 들어온
      값은 쓰지 않고 비의무 고지 1줄을 남긴다(R34-7).
    """
    given = sanitize_sources(task.get("sources"))
    if not given:
        return (), ""
    known = {s.id: s for s in get_registry().sources_of(APM_SYSTEM)}
    if not known:
        logger.info("%s: 소스 표 없는 배포 — 분해 sources %s 는 쓰지 않음", APM_QUERY_AGENT, given)
        notices.append(disc.make(  # 비의무 — 지목을 조용히 버리지 않는다(R34-7)
            NOTE_TRACE, f"이 배포는 {_short_label(APM_SYSTEM)} 소스를 가려 조회하지 않아 지목한"
            f" 소스({', '.join(g[:40] for g in given)})는 쓰지 않았습니다.", source=source))
        return (), ""
    valid = tuple(s for s in given if s in known)
    bad = [s for s in given if s not in known]
    meta["source_selection"] = {"given": given, "used": list(valid)}
    if bad:
        meta.setdefault("unresolved", []).append(
            {"view": None, "conditions": [f"sources={b[:40]!r}" for b in bad],
             "queried": bool(valid)})
    shown = ", ".join(b[:40] for b in bad)
    if valid:
        if bad:
            used = " · ".join(known[s].label or s for s in valid)
            notices.append(disc.make(
                disc.APM_UNRESOLVED_CONDITION,
                f"모르는 {label} 소스({shown})는 빼고 {used}만 조회했습니다.", source=source))
        return valid, ""
    choices = " · ".join(f"「{s.label or s.id}」" for s in known.values())
    return (), (f"지목한 {label} 소스({shown})를 찾지 못해 조회하지 않았습니다 — 다른 소스로 넓혀"
                f" 조회하지 않았습니다. 다음 중 골라 다시 물어 주세요: {choices}")


# ── 소스 선택 사다리 (plans/147 · D-322) ───────────────────────────────────────

def _source_decision(task: dict[str, Any], isolated: dict[str, Any],
                     llm_ids: tuple[str, ...]) -> source_select.SourceDecision:
    """격리 입력에서 사다리 입력을 모아 소스를 정한다(plans/147 §4.1).

    단어 원문 = task 질의에 소스 단어가 있으면 task 질의만, 없으면 턴 원문(`original_user_query` —
    `user_query`는 task 질의로 덮인다) + task 질의. 여러 소스에 걸리는 단어만 쓴 글 답 턴은 그 답
    문구만 본다(라우트가 질의를 되묻기 원 질문으로 바꿨다 — 원 질문 단어를 다시 세지 않는다).
    """
    scope = isolated.get("apm_source_scope")
    scope = scope if isinstance(scope, dict) else {}
    inherited = (source_select.SourceScope(ids=tuple(str(i) for i in scope["ids"]),
                                           basis=str(scope.get("basis") or ""))
                 if scope.get("ids") else None)
    prior = (tuple(str(i) for i in scope.get("last_target_sources") or ())
             if scope and _demonstrative(isolated) else ())
    answer = isolated.get("apm_source_answer_text")
    sub_query = str(task.get("sub_query") or "")
    specs = get_registry().sources_of(APM_SYSTEM)
    if answer:
        text = str(answer)
    elif source_select.match_source_terms(sub_query, specs).source_ids:
        # task 질의에 소스 단어가 있으면 그것만 본다 — 복합 질의의 다른 task 쪽 위치어(「김포 서버
        # CPU와 레거시 WAS」의 「김포」)가 이 task의 지목을 흐리지 않게
        text = sub_query
    else:  # task 질의가 원문 단어를 잃었다(단일 task 분해) — 턴 원문으로 본다
        text = " ".join(str(x) for x in (isolated.get("original_user_query"), sub_query) if x)
    return source_select.select_apm_sources(
        specs,
        available=source_select.get_available_apm_sources(),
        screen_ids=tuple(str(i) for i in isolated.get("selected_apm_source_ids") or ()),
        screen_basis=str(isolated.get("apm_source_basis") or "selected"),
        text=text, llm_ids=llm_ids, inherited=inherited, prior_target_ids=prior,
    )


async def _apply_source_decision(
    decision: source_select.SourceDecision, task: dict[str, Any], isolated: dict[str, Any],
    label: str, meta: dict[str, Any], notices: list[disc.Disclosure], source: str,
) -> dict[str, Any] | None:
    """사다리 결과를 메타·고지에 싣는다 — 조회면 None, 되묻기·미연결이면 조회 0 거부 결과.

    거부 결과는 `error`를 남겨 재계획 대상에서 빠진다(D-293 ⑤ 소스 불가 종결과 같은 결).
    """
    specs = get_registry().sources_of(APM_SYSTEM)
    given = (meta.get("source_selection") or {}).get("given")
    scope = decision.scope_update
    meta["source_selection"] = {
        **({"given": given} if given else {}),
        "action": decision.action, "basis": decision.basis, "used": list(decision.source_ids),
        "evidence": list(decision.evidence),
        "scope": {"ids": list(scope.ids), "basis": scope.basis} if scope else None,
    }
    if decision.action == "query":
        available = source_select.get_available_apm_sources()
        usable = [s.id for s in specs if available is None or s.id in available]
        narrowed = len(decision.source_ids) < len(usable)
        for i, text in enumerate(decision.notices):  # 좁힘 고지(의무·중립)가 있으면 맨 앞이다
            kind = (disc.APM_UNTARGETED_SCOPE if narrowed and i == 0
                    else disc.APM_UNRESOLVED_CONDITION)
            notices.append(disc.make(kind, text, source=source))
        return None
    missing = [disc.make(disc.APM_UNRESOLVED_CONDITION, n, source=source)
               for n in decision.notices]
    if decision.action == "unavailable":  # 미연결 소스만 걸렸다 — 다른 소스로 넓히지 않는다
        meta["source_status"] = _status(label, "unavailable", 0, "미연결 소스")
        text = "\n".join(f"{n}." for n in decision.notices)
        return _refusal(text, "source_unavailable", meta, disc.dedupe([*notices, *missing]))
    meta["source_status"] = _status(label, "not_queried", 0, "소스 되묻기")
    text = "\n".join([*(f"{n}." for n in decision.notices), decision.question])
    asked = disc.make(disc.APM_UNRESOLVED_CONDITION, decision.question, source=source)
    out = _refusal(text, "apm_unresolved_condition", meta,
                   disc.dedupe([*notices, *missing, asked]))
    out[SOURCE_CLARIFICATION_KEY] = _source_clarification(decision, specs, isolated, task)
    await _audit_source_ask(isolated, len(decision.choices))
    return out


# ── 앞 결과 행 참조 (plans/134 M-6 · 계약 §4.2) ──────────────────────────────────

def reference_views_only(task: dict[str, Any]) -> bool:
    """앞 결과 행 참조 보기(`target: reference`)만 고른 APM task인가.

    그런 task는 선행 결과를 고를 행의 원천으로만 쓰고 대상 서버로 좁히지 않는다 — 순차 게이트의
    「선행 결과 N대로 대상을 한정」 경과 노트가 사실과 다르고(plans/134 검증 문구 지적), 집계기의
    서버 키 병합 좁히기가 목록의 다른 서버 행을 지운다(V-1).
    """
    if task.get("agent") != APM_QUERY_AGENT:
        return False
    by_id = {v.id: v for v in apm_views()}
    views = sanitize_views(task.get("views"))
    return bool(views) and all(by_id[v].target == "reference" for v in views)


def table_label(views: Any, fallback: Any = "") -> str:
    """결과 표 이름(표가 여럿일 때의 되묻기 문구 — R-1).

    고른 APM 보기 라벨이고, 보기가 없으면 task 질의 앞부분이다.
    """
    by_id = {v.id: v for v in apm_views()}
    labels = [_view_label(by_id[v]) for v in sanitize_views(views)]
    if labels:
        return " · ".join(labels)
    text = str(fallback or "").strip()
    return text[:_TABLE_LABEL_MAX] + ("…" if len(text) > _TABLE_LABEL_MAX else "")


def _ref_candidates(kind: str, task: dict[str, Any], isolated: dict[str, Any],
                    ) -> tuple[list[dict[str, Any]], str, list[str]]:
    """참조 후보(표시 순서) · 출처 문구 · 표 이름 — ① 같은 계획의 선행 task 행 → ② 직전 턴 결과.

    ①은 선행 task의 원 행(`prior_result_rows` — 식별 키만 남긴 `prior_rows`에는 참조 칸이
    없다)이다. `input_from` 선행 task 행에 이 종류의 칸이 없으면 `depends_on`만 건 선행 APM
    task 행을 본다(V-5). 둘 다 없으면 ②로 간다. ②는 `context_resolver`가 직전 턴 결과에서 추린
    스레드 상태다(다른 스레드의 참조는 닿지 않는다).

    후보는 표마다 번호를 센다(R-1) — ①은 선행 task마다 한 표(`prior_result_tables`의 보기로 이름)고,
    ②는 직전 턴 집계기가 남긴 표 경계를 따른다. 후보의 `table`이 표 이름 목록의 자리다.
    """
    prior = isolated.get("prior_result_rows")
    if isinstance(prior, dict):
        info = isolated.get("prior_result_tables") or {}
        for tids in (task.get("input_from") or [], task.get("depends_on") or []):
            found: list[dict[str, Any]] = []
            names: list[str] = []
            for tid in dict.fromkeys(tids):
                rows = prior.get(tid) or prior.get(str(tid)) or []
                entries = extract_result_refs(rows).get(kind) or []
                if not entries:
                    continue
                meta = info.get(tid) or info.get(str(tid)) or {}
                found += [{**e, "table": len(names)} for e in entries]
                names.append(table_label(meta.get("views"), meta.get("sub_query")))
            if found:
                return found, "앞 작업 결과", names
    ctx = isolated.get("conversation_context") or {}
    refs = ctx.get("previous_result_refs") if isinstance(ctx, dict) else None
    found = list((refs or {}).get(kind) or []) if isinstance(refs, dict) else []
    if not found:
        return [], "", []
    made, now = (refs or {}).get("turn"), ctx.get("turn_count")
    gap = now - made if isinstance(made, int) and isinstance(now, int) else 1
    names = [str(x) for x in (refs or {}).get(TABLE_LABELS_KEY) or []]
    return found, "직전 답의 결과" if gap <= 1 else f"{gap}턴 전 답의 결과", names


def _resolve_reference(view: ViewSpec, args: dict[str, Any], task: dict[str, Any],
                       isolated: dict[str, Any], plan: WindowPlan,
                       bad_ref: Any = None) -> _RefPick:
    """참조 보기의 행을 고른다(계약 §4.2) — 순번(`ref`)은 계획 LLM 값 · 코드는 범위·종류만 본다.

    - 후보가 표 둘 이상에 걸쳐 있으면 어느 표인지 되묻는다(표마다 표 안 번호 · R-1).
    - `ref`를 냈는데 형식 밖이었으면(`bad_ref` — 「5번째」·0·-1) 후보 수와 무관하게 되묻는다(R-2).
    - `ref` 있음: 1 ≤ ref ≤ 후보 수이고 그 행에 참조 값이 있어야 한다(아니면 되묻기).
    - `ref` 없음: 쓸 수 있는 후보가 하나면 그 행 · 여럿이면 되묻기(후보 ≤3 라벨) · 없으면 조회하지
      않고 사유(다른 보기로 대신하지 않는다).
    - GUID 추적은 사용자가 적은 `guid`가 순번보다 앞선다.
    인가는 처리기 진입(`is_source_allowed` — 이번 턴 권한)이 이미 다시 판정했고, 게이트웨이가 행의
    hostname ↔ 도메인 정합을 다시 검사한다.
    """
    kind = view.reference
    label = _view_label(view)
    missing, noun, field_text, hint = _REF_TEXT[kind]
    ref = args.pop("ref", None)
    if kind == "guid" and args.get("guid"):
        guid = args.pop("guid")
        given = {"guid": guid, **_trace_window(plan, None)}
        return _RefPick(args={**args, **given}, note=f"{label}: GUID {guid}(직접 지정)",
                        reference={"kind": kind, "origin": "given", "fields": sorted(given)})
    candidates, origin, names = _ref_candidates(kind, task, isolated)
    if not candidates:
        return _RefPick(unresolved=(f"{label}: 앞 결과에 {missing} 없어 조회하지 않았습니다"
                                    f" — {hint}."))
    tables = list(dict.fromkeys(e.get("table") for e in candidates))
    if len(tables) > 1:
        return _RefPick(unresolved=(
            f"{label}: {origin}에 {noun} 후보가 표 {len(tables)}개에 걸쳐 있어 어느 표의"
            " 몇 번째인지 정하지 못해 조회하지 않았습니다. 원하는 목록만 다시 조회한 뒤 몇 번째인지"
            " 함께 물어 주세요: " + _tables_text(candidates, names)))
    if bad_ref is not None:
        return _RefPick(unresolved=(f"{label}: {origin}의 {noun} {len(candidates)}건 중 몇 번째인지"
                                    " 정하지 못해 조회하지 않았습니다." + _choose_text(candidates)))
    if ref is None:
        usable = [(i, e) for i, e in enumerate(candidates, 1) if e.get("value") is not None]
        if not usable:
            return _RefPick(unresolved=f"{label}: {origin}에 {missing} 없어 조회하지 않았습니다 —"
                                       f" {hint}.")
        if len(usable) > 1:
            return _RefPick(unresolved=(
                f"{label}: {origin}에 {noun} 후보가 {len(usable)}건이라 어느 것인지 정하지 못해"
                " 조회하지 않았습니다." + _choose_text(candidates)))
        n, entry = usable[0]
    elif ref > len(candidates):
        return _RefPick(unresolved=(f"{label}: {origin}의 {noun}은(는) {len(candidates)}건이라"
                                    f" {ref}번째가 없어 조회하지 않았습니다."
                                    + _choose_text(candidates)))
    else:
        n, entry = ref, candidates[ref - 1]
        if entry.get("value") is None:
            return _RefPick(unresolved=(f"{label}: {origin} {ref}번째 행에는 {field_text}가 없어"
                                        " 조회하지 않았습니다." + _choose_text(candidates)))
    value = entry["value"]
    hostname = None
    if kind == "guid":
        extra = {"guid": value, **_trace_window(plan, entry)}
    else:
        hostname = entry.get("hostname")
        if kind == "profile_ref" and not hostname:
            # 프로파일 도구는 hostname 으로 도메인 정합을 다시 본다(필수 인자) — 행에 서버가
            # 없으면 부를 수 없다
            return _RefPick(unresolved=(f"{label}: {origin} {n}번째 행에 서버(hostname) 정보가"
                                        " 없어 조회하지 못했습니다 — 서버 이름을 넣어 그 목록을"
                                        " 다시 조회한 뒤 물어 주세요."))
        extra = dict(value)  # 행의 참조 칸을 그대로 넘긴다(계약 — 키를 고르거나 바꾸지 않는다)
    # `fields`는 감사 `commands`에 싣는 참조 유래 인자 이름이다(어느 거래를 조회했는가)
    reference = {"kind": kind, "origin": origin, "n": n, "fields": sorted(extra)}
    return _RefPick(args={**args, **extra}, hostname=str(hostname) if hostname else None,
                    note=f"{label}: {origin} {n}번째 — {ref_label(entry)}", reference=reference)


async def run_apm_query(
    task: dict[str, Any],
    isolated: dict[str, Any],
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """APM 게이트웨이 보기를 부른다(handler 규약).

    LLM 은 보기 선택 재시도(SPEC §6.4 — 분해 보기가 요청 영역을 덮지 못할 때 1회)에만 쓴다.

    Returns:
        성공: `organized_data`·`query_results`(행) + `apm_query`(보기·대상·삽입 단계·출처·실패)
        + `source_status`. 실패: 텍스트 결과 `{error, degraded_reason, final_response}` + 메타.
    """
    # 관측 소스 인가(plans/125 A-7 · D-272 ⑩) — 실행 경계에서 판정한다(2단 오케스트레이터 ·
    # 3단 계획 루프가 같은 처리기를 부른다). 거부 문구·결과에는 소스 이름을 싣지 않는다(D-264 ②).
    role = isolated.get("user_role")
    if not is_source_allowed(APM_SYSTEM, isolated.get("allowed_sources"), role):
        logger.info("%s 인가 거부: 관측 소스 권한 없음(역할=%s)", APM_QUERY_AGENT, role)
        return access_denied_result(SOURCE_ACCESS_DENIED_MESSAGE)
    ctx = _new_run_ctx(task, isolated, app_config, now)
    endpoint = apm_endpoint(app_config)
    if endpoint is None:
        ctx.meta["source_status"] = _status(ctx.label, "unavailable", 0, "엔드포인트 미설정")
        return _refusal(f"{ctx.label}이(가) 연결되어 있지 않아 조회하지 않았습니다.",
                        "source_unavailable", ctx.meta)
    refused = await _select_step(ctx, llm)
    if refused is None:
        refused = await _source_step(ctx)
    if refused is not None:
        return refused
    await _target_hosts_step(ctx)
    _window_step(ctx)
    _view_args_step(ctx)
    _reference_step(ctx)
    if ctx.blocked and all(v in ctx.blocked for v in ctx.views):
        # 되묻기만 남았다 — 게이트웨이를 열지 않는다(되묻기가 게이트웨이 가용성에 좌우되지 않게)
        ctx.meta["hostnames"] = ctx.hostnames
        return _blocked_refusal(ctx.label, ctx.blocked, ctx.meta, disc.dedupe(ctx.notices))
    _call_scope_step(ctx)
    refused = await _session_step(ctx, endpoint)
    if refused is not None:
        return refused
    _collect_step(ctx)
    refused = _decide_outcome(ctx)
    return refused if refused is not None else _success_result(ctx)


def _new_run_ctx(task: dict[str, Any], isolated: dict[str, Any], app_config: AppConfig,
                 now: datetime | None) -> _RunCtx:
    """요청 시간·라벨·보기 표·메타 뼈대로 단계 상태를 연다."""
    # 요청 시간 해석(D-309) — 있으면 그 기준 시각(KST aware)이 창의 기준이다. 없으면(옛 체크포인트·
    # 해석 off) 종전 naive 현재 시각과 파서 `time_range`다(aware·naive를 섞지 않는다)
    query_time = QueryTime.from_state(isolated.get("time_resolution"))
    now = query_time.anchor_at if query_time is not None else (now or datetime.now())
    label = get_registry().system_label(APM_SYSTEM)
    by_id = {v.id: v for v in apm_views()}
    meta: dict[str, Any] = {"views": sanitize_views(task.get("views")), "hostnames": [],
                            "inserted_steps": [], "provenance": [], "failures": [], "notes": []}
    return _RunCtx(task=task, isolated=isolated, app_config=app_config, query_time=query_time,
                   now=now, label=label, by_id=by_id, meta=meta)


async def _select_step(ctx: _RunCtx, llm: BaseChatModel) -> dict[str, Any] | None:
    """보기 선택 — 못 고르면 되묻기 거부, 고르면 보기·조건을 싣는다."""
    task, meta = ctx.task, ctx.meta
    selection = await _select_views(task, ctx.isolated, llm, ctx.by_id)
    meta["selection"] = selection.info
    source = f"task:{task.get('task_id')}" if task.get("task_id") else "task"
    ctx.source = source
    if selection.unresolved:
        meta["views"] = []
        meta["source_status"] = _status(ctx.label, "not_queried", 0, "요청 정보에 맞는 보기 미선택")
        notice = disc.make(disc.APM_UNRESOLVED_CONDITION, selection.unresolved, source=source)
        return _refusal(selection.unresolved, "apm_unresolved_selection", meta, [notice])
    ctx.views = selection.views
    meta["views"] = ctx.views
    ctx.task = {**task, "view_args": selection.view_args}
    if selection.uncovered:  # 덮은 보기는 조회하고 못 덮은 영역만 되묻는다(SPEC §6.4 ②)
        ctx.notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, selection.uncovered,
                                     source=source))
    return None


async def _source_step(ctx: _RunCtx) -> dict[str, Any] | None:
    """소스 선택 — 분해 `sources` 검증과 소스 선택 사다리 · 대상 생략 후속의 직전 대상 승계."""
    label, meta, notices, source = ctx.label, ctx.meta, ctx.notices, ctx.source
    # 소스 선택(plans/134 M-5) — 준 id가 전부 무효면 조회하지 않고 되묻는다(전 소스로 넓히지 않는다)
    plan_notices: list[disc.Disclosure] = []
    source_ids, ask_source = _plan_sources(ctx.task, label, meta, plan_notices, source)
    # 소스 선택 사다리(plans/147 · D-322) — 소스 ≥2 배포에서만 성립(`unscoped`면 위 종전 결과
    # 그대로)
    decision = _source_decision(ctx.task, ctx.isolated, source_ids)
    ctx.decision = decision
    if decision.action == "unscoped":
        notices += plan_notices
        if ask_source:
            meta["source_status"] = _status(label, "not_queried", 0, "지목한 소스 미해결")
            notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, ask_source, source=source))
            return _refusal(ask_source, "apm_unresolved_condition", meta, disc.dedupe(notices))
        ctx.source_ids = source_ids
        return None
    # 사다리가 분해 `sources` 검증 결과를 덮는다 — 그 고지·되묻기는 싣지 않는다(중복 고지 없음)
    refused = await _apply_source_decision(decision, ctx.task, ctx.isolated, label, meta, notices,
                                           source)
    if refused is not None:
        return refused
    ctx.source_ids = decision.source_ids
    # 대상을 생략한 후속 — 직전 대상을 분해가 낸 것처럼 싣고 종전 대상 해석을 그대로 탄다
    # (plans/157 W3 · 새 해석 경로 없음)
    inherited = inherited_targets(ctx.task, ctx.isolated, ctx.views, ctx.by_id, decision)
    if inherited:
        logger.info("%s 직전 대상 승계: %d건", APM_QUERY_AGENT, len(inherited))
        ctx.task = {**ctx.task, "targets": inherited}
        notices.append(_inherited_notice(inherited, source))
    return None


async def _hosts_of(ctx: _RunCtx, refs: list[TargetRef]) -> list[str]:
    """대상 → hostname(패싯 변환 — plans/125 E-3: hostname 없는 대상은 간선 표 경로 E2 등록명 →
    hostname). 같은 대상 묶음은 한 번만 변환한다(장부·삽입 단계 중복 없음)."""
    key = json.dumps([r.model_dump() for r in refs], sort_keys=True, default=str)
    if key not in ctx.linked:
        found, entries, steps = await link_hostnames(
            refs, consumer=APM_SYSTEM, app_config=ctx.app_config)
        ctx.linked[key] = found
        ctx.ledger.extend(entries)
        ctx.meta["inserted_steps"] += steps
    return list(ctx.linked[key])


async def _target_hosts_step(ctx: _RunCtx) -> None:
    """보기 종류별 대상 hostname — 종전 보기 · 선택 대상 보기 · GUID 참조 보기."""
    by_id, views, isolated = ctx.by_id, ctx.views, ctx.isolated
    kinds = {by_id[v].target for v in views}
    # 종전 보기(대상 표현 없음)는 종전 규칙 그대로 — 선행 결과 → 이번 턴 식별자 → 직전 대상.
    # 이번 턴에 대상 텍스트(분해 `targets`)를 말했으면 직전 턴 대상은 지시어로 가리킬 때만 잇는다
    # (`scoped_targets` 규칙 — 말한 대상 대신·함께 직전 서버를 조회하지 않는다 · D-290 ⑥ · V130-1)
    named = bool(sanitize_targets(ctx.task.get("targets")))
    pick_targets = scoped_targets if named else resolve_apm_targets
    ctx.hostnames = await _hosts_of(ctx, pick_targets(isolated)) if "" in kinds else []
    ctx.scoped_hosts = (await _hosts_of(ctx, scoped_targets(isolated))
                        if "optional" in kinds else [])
    ctx.explicit_hosts = (await _hosts_of(ctx, explicit_targets(isolated))
                          if any(by_id[v].reference == "guid" for v in views) else [])


def _window_step(ctx: _RunCtx) -> None:
    """보기별 창 계획 — 비교 구간 미해결 보기는 막고 되묻는다."""
    by_id, meta, notices, source = ctx.by_id, ctx.meta, ctx.notices, ctx.source
    ctx.parsed = ctx.isolated.get("parsed_requirements") or {}
    ctx.windows = {vid: plan_window(by_id[vid], ctx.parsed.get("time_range"), ctx.now,
                                    query_time=ctx.query_time)
                   for vid in ctx.views}
    blocked = ctx.blocked
    for vid, plan in ctx.windows.items():
        if plan.mode == "ask":  # 비교 구간 미해결 — 그 보기만 조회하지 않고 되묻는다(A-1)
            blocked[vid] = f"{_view_label(by_id[vid])}: {plan.note}"
            meta.setdefault("unresolved", []).append({"view": vid, "conditions": ["period"],
                                                      "queried": False})
            notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, blocked[vid], source=source))
            continue
        if plan.note:
            meta["notes"].append(f"{vid}: {plan.note}")
        if plan.kind:
            notices.append(disc.make(plan.kind, f"{_view_label(by_id[vid])}: {plan.note}",
                                     source=source))


def _view_args_step(ctx: _RunCtx) -> None:
    """보기 인자 · 추세 간격 자동 선택 · 기간 전용 순위 지표의 기간 되묻기."""
    by_id, meta, notices, source = ctx.by_id, ctx.meta, ctx.notices, ctx.source
    windows, blocked = ctx.windows, ctx.blocked
    ctx.view_args = _plan_view_args(ctx.task, ctx.views, by_id, ctx.parsed, meta, notices, source,
                                    composite=bool(ctx.isolated.get("is_composite")),
                                    blocked=blocked, deferred=ctx.deferred,
                                    bad_refs=ctx.bad_refs)
    view_args = ctx.view_args
    for vid in ctx.views:  # 추세 간격 자동 선택(M-7) — 보기 단위라 배치가 1호출로 유지된다
        windows[vid], interval_note = auto_interval(by_id[vid], windows[vid], view_args.get(vid))
        if interval_note:
            meta["notes"].append(f"{vid}: {interval_note}")
            notices.append(disc.make(NOTE_TRACE, f"{_view_label(by_id[vid])}: {interval_note}",
                                     source=source))
    metric = (view_args.get(RANKING_VIEW) or {}).get("metric")
    if (RANKING_VIEW in windows and RANKING_VIEW not in blocked
            and metric in PERIOD_ONLY_RANKING_METRICS and windows[RANKING_VIEW].mode != "window"):
        # 기간 전용 지표인데 기간이 없다 — 그 보기만 되묻는다(침묵 대체 금지 · W6 A-4)
        blocked[RANKING_VIEW] = f"{_view_label(by_id[RANKING_VIEW])}: {PERIOD_ONLY_ASK_NOTE}"
        meta.setdefault("unresolved", []).append(
            {"view": RANKING_VIEW, "conditions": ["period"], "queried": False})
        notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, blocked[RANKING_VIEW],
                                 source=source))


def _reference_step(ctx: _RunCtx) -> None:
    """앞 결과 행 참조 보기 — 행 선택 · 미해결이면 막고 되묻기 · 해석 못 한 조건 고지."""
    by_id, meta, notices, source = ctx.by_id, ctx.meta, ctx.notices, ctx.source
    views, blocked = ctx.views, ctx.blocked
    ctx.picks = {vid: _resolve_reference(by_id[vid], dict(ctx.view_args.get(vid) or {}), ctx.task,
                                         ctx.isolated, ctx.windows[vid], ctx.bad_refs.get(vid))
                 for vid in views if by_id[vid].target == "reference" and vid not in blocked}
    for vid in views:
        if by_id[vid].target != "reference":
            continue
        pick, dropped = ctx.picks.get(vid), ctx.deferred.get(vid, [])
        queried = pick is not None and not pick.unresolved
        if dropped:
            meta.setdefault("unresolved", []).append(
                {"view": vid, "conditions": dropped, "queried": queried})
        tail = f"해석하지 못한 조건({', '.join(dropped)})은" if dropped else ""
        if pick is not None and pick.unresolved:
            text = pick.unresolved + (f" {tail} 쓰지 않았습니다." if tail else "")
            blocked[vid] = text
            meta.setdefault("unresolved", []).append(
                {"view": vid, "conditions": ["reference"], "queried": False})
            notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, text, source=source))
            continue
        if tail:  # 조회하면 「빼고 조회」 · 창 밖이라 조회하지 않으면 「쓰지 않음」
            notices.append(disc.make(
                disc.APM_UNRESOLVED_CONDITION,
                f"{_view_label(by_id[vid])}: {tail} " + ("빼고 조회했습니다." if queried
                                                        else "쓰지 않았습니다."),
                source=source))
        if pick is not None and pick.note:
            meta["notes"].append(pick.note)


def _call_scope_step(ctx: _RunCtx) -> None:
    """조회 전 준비 — 대상 텍스트 · 이름 대상 · 소스 범위 고지 · 동시성 · 작업 맥락."""
    by_id, isolated, app_config = ctx.by_id, ctx.isolated, ctx.app_config
    # 대상 텍스트(plans/130 M-2) — 대상으로 좁히는 보기가 있을 때만 해석한다. 없으면 종전과 같다.
    ctx.live = [v for v in ctx.views if v not in ctx.blocked]
    live = ctx.live
    ctx.texts = (_target_texts(ctx.task, isolated, ctx.ledger, [*ctx.hostnames, *ctx.scoped_hosts])
                 if any(_uses_target(by_id[v], ctx.view_args.get(v)) for v in live) else [])
    ctx.named_given, ctx.named_texts = _named_texts(ctx.task, [by_id[v] for v in live], ctx.meta,
                                                    ctx.notices, ctx.source)
    ctx.notices += _unscoped_notices(ctx.task, isolated, [by_id[v] for v in live], ctx.meta,
                                     ctx.source)
    thread_id = isolated.get("thread_id")
    ctx.concurrency = _int_setting(app_config, "fanout_concurrency", 3)
    timeout = getattr(getattr(app_config, "dbhub", None), "source_call_timeout", 10.0)
    ctx.call_timeout = float(timeout) if isinstance(timeout, (int, float)) else 10.0
    ctx.scope = _JobScope(
        sub=isolated.get("user_id"), owner=jobs.gateway_owner(isolated.get("user_id")),
        thread_id=thread_id, call_timeout=ctx.call_timeout,
        poll_until=jobs.poll_deadline(app_config), app_config=app_config,
        source_ids=ctx.source_ids,
    )


async def _session_step(ctx: _RunCtx, endpoint: tuple[str, str | None]) -> dict[str, Any] | None:
    """게이트웨이 세션 — 대상 텍스트 해석 · 첫 홉 · 호출 구성 · 실행. 연결 실패면 거부를 낸다."""
    label = ctx.label
    url, token = endpoint
    try:
        async with open_source_session(url, token, call_timeout=ctx.call_timeout, label=label,
                                       session_factory=_SESSION_FACTORY) as session:
            if ctx.texts:
                await _resolve_texts_step(ctx, session)
            await _first_hop_step(ctx, session)
            calls = _build_calls(ctx)
            # 한 보기의 대상 2개 이상 = `targets` 배치 1호출(plans/134 M-5 · 계약 A-2) → 대상별 복원
            wire = _batched(calls, ctx.scope)
            await _run_calls(session, wire, ctx.concurrency, ctx.scope)
            await _settle_jobs(session, wire, ctx.scope)
            calls = _unbatched(wire, ctx.meta)
            calls += ctx.answers  # 게이트웨이에 다시 묻지 않는다(해석 검색이 이미 부른 행)
    except SourceMcpError as e:
        # 연결 예외 원문은 답 본문에 싣지 않는다 — 요청 번호만, 원문은 로그(plans/163 S3+)
        rid = request_id()
        logger.warning("APM 게이트웨이 연결 실패 (요청 %s · %s): %s", rid, label, e)
        ctx.meta["source_status"] = _status(label, "unavailable", 0, str(e))
        return _refusal(f"{label}에 연결하지 못해 조회하지 않았습니다(요청 {rid}). "
                        "다른 소스의 값으로 대신 답하지 않았습니다.",
                        "source_unavailable", ctx.meta, ctx.notices)
    ctx.calls = calls
    return None


async def _resolve_texts_step(ctx: _RunCtx, session: Any) -> None:
    """대상 텍스트 → 인스턴스(게이트웨이 검색) · 대상 고지 · 대상 메타."""
    domain = (ctx.view_args.get(INSTANCES_VIEW) or {}).get("domain_id")
    ctx.resolved = await _resolve_target_texts(
        session, ctx.texts, ctx.by_id.get(INSTANCES_VIEW), domain, ctx.scope, ctx.concurrency,
        ctx.isolated, ctx.app_config, ctx.ledger, ctx.meta)
    ctx.instances = _merged_instances(
        ctx.resolved, {h.casefold() for h in [*ctx.hostnames, *ctx.scoped_hosts]})
    ctx.notices += _target_notices(ctx.resolved, ctx.source)
    ctx.meta["targets"] = _targets_meta(ctx.resolved)


async def _first_hop_step(ctx: _RunCtx, session: Any) -> None:
    """대상 미지정 첫 홉 — 대상이 필요한 보기에 서버도 대상 텍스트도 없을 때만."""
    by_id, view_args, meta = ctx.by_id, ctx.view_args, ctx.meta
    needs_host = [v for v in ctx.views if by_id[v].required_input and v not in ctx.blocked]
    # 대상 텍스트가 있었으면(해석 0건 포함) 첫 홉으로 가지 않는다(D-290 ⑥ — 말한 대상과
    # 무관한 인스턴스를 조회하지 않는다)
    if not (needs_host and not ctx.hostnames and not ctx.texts):
        return
    # 서버 미지정 = 현재 부하 상위 N개(plans/134 W6 ④) · 분해가 `full`을 냈으면 전부
    full = any((view_args.get(v) or {}).get("full") is True for v in needs_host)
    top_n = None if full else _int_setting(ctx.app_config, "apm_untargeted_top_n", 20)
    ctx.hostnames, ctx.hostless, step = await _untargeted_step(session, ctx.scope, by_id, top_n)
    ctx.host_sources = step.pop("_host_sources", None) or {}
    ctx.first_hop_failed = step.pop("_source_failures", None) or {}
    ctx.first_hop = step
    meta["inserted_steps"].append(step)
    if step.get("error"):
        # 대상 선정이 실패한 사유를 결과에 싣는다 — 없으면 "조회 대상이 없습니다"로 가려진다
        meta["failures"].append(
            {"view": step["view"], "hostname": None, "reason": step["error"]})
    elif step.get("read_error"):  # 첫 홉 결과 파일을 끝까지 못 읽음 — 부분(의무 고지)
        ctx.notices.append(disc.make(disc.APM_PARTIAL_SOURCES, _first_hop_text(step),
                                     source=ctx.source))
        what = "부하 순위" if step.get("mode") == "ranking" else "인스턴스 목록"
        meta["failures"].append({"view": step["view"], "hostname": None,
                                 "reason": f"{what} {step['read_error']}"})
    elif step.get("instances"):
        # 범위 고지 — 상위 N만이면 의무 중립 · 실패가 섞이면(목록 앞 N · 잠정 순위) 의무
        # 부분 · 전부면 비의무(M-5 · 0개면 내지 않음 R34-11)
        ctx.first_hop_notice = len(ctx.notices)
        ctx.notices.append(disc.make(_first_hop_kind(step), _first_hop_text(step),
                                     source=ctx.source))


def _collect_step(ctx: _RunCtx) -> None:
    """수집 — 행 · 첫 홉 범위 고지 보정 · 미해석 대상 실패 · 고지 · 연결 장부."""
    calls, meta, notices, source = ctx.calls, ctx.meta, ctx.notices, ctx.source
    # 조회한 대상 — 종전 보기의 대상(첫 홉 포함) 뒤에 선택 대상·참조 보기가 부른 서버를 잇는다
    meta["hostnames"] = list(dict.fromkeys(
        [*ctx.hostnames, *(c.hostname for c in calls if c.hostname)]))
    ctx.rows = _collect(calls, meta)
    first_hop = ctx.first_hop
    if first_hop is not None and ctx.first_hop_notice is not None:
        actual = _queried_instances(calls)
        if actual > (first_hop.get("instances") or 0) and first_hop.get("mode") == "ranking":
            # 상위 N의 hostname 대상이 같은 호스트의 순위 밖 인스턴스까지 불렀다 — 실제 수를 적는다
            first_hop["queried"] = actual
            notices[ctx.first_hop_notice] = disc.make(_first_hop_kind(first_hop),
                                                      _first_hop_text(first_hop), source=source)
    ctx.unresolved = [r for r in ctx.resolved if not r.instances]
    # 대상 텍스트 해석 0건은 조회하지 못한 대상으로 센다(부분 결과 · 감사 degraded)
    meta["failures"] += [{"view": None, "hostname": None, "target": _shown(r.text),
                          "reason": f"대상 '{_shown(r.text)}' "
                                    + ("이름 길이 초과" if _too_long(r.text) else "해석 0건")}
                         for r in ctx.unresolved]
    for limit in (x for r in ctx.resolved for x in r.limits):
        if limit not in meta["limits"]:
            meta["limits"].append(limit)
    ctx.missing_named, named_notices = _named_unresolved(calls, meta, source)
    ctx.notices += named_notices
    ctx.disclosures = disc.dedupe(
        [*ctx.notices, *_job_disclosures(calls, meta, str(ctx.task.get("task_id") or ""))])
    ctx.accepted = [c for c in calls if c.accepted]
    ctx.ledger += _apm_hop_ledger(calls)
    meta["link_ledger"] = [entry.as_dict() for entry in ctx.ledger]
    meta["link_summary"] = ledger_summary(ctx.ledger)
    line = ledger_line(ctx.ledger)
    if line:
        meta["notes"].insert(0, line)


def _decide_outcome(ctx: _RunCtx) -> dict[str, Any] | None:
    """세션 뒤 거부 사슬(순서가 의미다) — 거부면 그 결과, 성공으로 갈 때는 None."""
    calls, meta, label, disclosures = ctx.calls, ctx.meta, ctx.label, ctx.disclosures
    ok_calls = [c for c in calls if c.error is None]
    if calls and not ok_calls:
        meta["source_status"] = _status(label, "unavailable", 0,
                                        "; ".join(f["reason"] for f in meta["failures"][:3]))
        shown = _call_failure_text(label, [(f.get("hostname") or f.get("target"),
                                            str(f.get("reason") or "")) for f in meta["failures"]])
        return _refusal(f"{label} 조회가 모두 실패했습니다 — {shown}",
                        "apm_calls_failed", meta, disclosures)
    if not calls and ctx.blocked:
        return _blocked_refusal(label, ctx.blocked, meta, disclosures)
    lost = {id(c) for c in ctx.missing_named}
    named_lost = bool(ctx.missing_named) or bool(ctx.named_given and not ctx.named_texts)
    texts_lost = bool(ctx.unresolved) and len(ctx.unresolved) == len(ctx.resolved)
    if (named_lost or texts_lost) and all(c.error or id(c) in lost for c in calls):
        return _target_unresolved_refusal(ctx)
    if not calls:
        reason = "; ".join(f["reason"] for f in meta["failures"][:3]) or "조회 대상이 없습니다"
        meta["source_status"] = _status(label, "not_queried", 0, reason)
        return _refusal(f"{label}을(를) 조회하지 않았습니다 — {reason}", "apm_not_queried", meta,
                        disclosures)
    if ctx.accepted and not ctx.rows:
        return _accepted_answer(label, ctx.accepted, meta, disclosures)
    return None


def _target_unresolved_refusal(ctx: _RunCtx) -> dict[str, Any]:
    """말한 대상(인스턴스 이름·업무명 · 서비스·업무 이름)을 하나도 풀지 못했다 — 「찾지 못함」과
    후보가 답이다(D-290 ⑥ · 대신 조회 없음)."""
    label, disclosures = ctx.label, ctx.disclosures
    ctx.meta["source_status"] = _status(label, "not_queried", 0, "대상 텍스트 해석 0건")
    text = "\n".join(d["text"] for d in disclosures if d["kind"] in (
        disc.APM_UNRESOLVED_CONDITION, disc.APM_PARTIAL_SOURCES))
    errors = [(c.hostname, c.error) for c in ctx.calls if c.error]
    if errors:  # 함께 고른 다른 보기의 실패도 답에 싣는다(원문은 로그 — plans/163 S3+)
        text += f"\n{label} 조회 실패 {len(errors)}건 — " + _call_failure_text(label, errors)
    return _refusal(text, "apm_target_unresolved", ctx.meta, disclosures)


def _success_result(ctx: _RunCtx) -> dict[str, Any]:
    """성공(부분 포함) 결과 — 상태 판정 · 고지 · 소스별 상태 행 · 표시용 행."""
    calls, meta, label, rows = ctx.calls, ctx.meta, ctx.label, ctx.rows
    accepted, disclosures, by_id = ctx.accepted, ctx.disclosures, ctx.by_id
    # 게이트웨이 봉투 partial(일부 소스·도메인·조각 실패)도 완료로 세지 않는다
    partial_calls = [c for c in calls if c.error is None and not c.accepted
                     and any((c.envelope or {}).get(k) for k in ("partial", "provisional"))]
    # 결과 파일에만 행이 있는 대상은 0건(empty)이 아니다(R34-1)
    in_file = any(p.get(ROWS_IN_FILE_KEY) for p in meta["provenance"])
    status = ("partial" if meta["failures"] or accepted or partial_calls
              else ("ok" if rows or in_file else "empty"))
    reasons = [f["reason"] for f in meta["failures"][:3]]
    if accepted:
        reasons.append(f"작업 접수 {len(accepted)}건(진행 중)")
    if partial_calls:
        reasons.append(f"일부 소스·구간 조회 실패 {len(partial_calls)}건(부분 결과)")
    meta["source_status"] = _status(label, status, len(rows), "; ".join(reasons))
    if status == "empty" and any(ctx.windows[v].args.get("reference_time") or by_id[v].window
                                 == "compare" for v in ctx.live):
        # 지난 기간에 행이 없다 — 보존 기간 만료로 단정하지 않는다(M-7 · 보존 기간은 W10)
        meta["notes"].append(EMPTY_PERIOD_NOTE)
        disclosures = disc.dedupe([*disclosures, disc.make(NOTE_TRACE, EMPTY_PERIOD_NOTE,
                                                           source=ctx.source)])
    extra: dict[str, Any] = {}
    if disclosures:
        extra["disclosures"] = disclosures
    if accepted:
        extra["accepted_jobs"] = [c.job["job_id"] for c in accepted if c.job]
    lines = _answer_lines(meta)
    if lines:
        extra[ANSWER_LINES_KEY] = lines
    source_rows: list[dict[str, Any]] = []
    # 소스 ≥2 배포(plans/147 §4.5) — 소스 라벨 칸 · 소스별 상태 행
    if ctx.decision.action == "query":
        rows, source_rows = _source_rows_step(ctx, rows, extra)
    shown = display_rows(rows)
    if any(a is not b for a, b in zip(shown, rows)):
        extra[DISPLAY_CUT_KEY] = True
    return {
        **extra,
        "organized_data": {
            "summary": _summary(label, ctx.views, by_id, meta, len(rows)),
            # 채팅 표·LLM 입력은 긴 셀을 줄여 싣는다 — 전문은 CSV(`query_results`)·결과 파일
            "rows": shown,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        },
        "query_results": rows,
        META_KEY: meta,
        "source_status": [meta["source_status"], *source_rows],
    }


def _source_rows_step(ctx: _RunCtx, rows: list[dict[str, Any]], extra: dict[str, Any]
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """소스 ≥2 배포 — 행에 소스 라벨 칸 · 소스별 상태 행 · 일부 소스 실패 고지 · 승계 재료.

    Returns:
        (소스 라벨을 단 행, 소스별 상태 행)
    """
    meta, label, decision = ctx.meta, ctx.label, ctx.decision
    specs = get_registry().sources_of(APM_SYSTEM)
    rows = _with_source_labels(rows, specs)
    source_rows = _source_status_rows(rows, meta, decision.source_ids, specs,
                                      ctx.first_hop_failed)
    down = [r for r in source_rows if r["status"] == "unavailable"]
    if down:  # 고른 소스 일부 실패 — 완료로 세지 않고 어느 소스인지 고지한다(의무)
        meta["source_status"] = _status(
            label, "partial", len(rows),
            "; ".join(x for x in (meta["source_status"].get("reason"),
                                  f"일부 소스 조회 실패 {len(down)}건") if x))
        extra["disclosures"] = disc.dedupe([*extra.get("disclosures", []), *(
            disc.make(disc.APM_PARTIAL_SOURCES,
                      f"{r['label']}는 조회하지 못했습니다({r['reason']}) — 그 소스의 결과는"
                      " 빠졌습니다", source=ctx.source) for r in down)])
    # 지시어 후속의 승계 재료(단 3) — 이번 턴 행이 온 소스(선언 순서)
    found = {r.get("source_id") for r in rows}
    meta["source_selection"]["last_target_sources"] = [s.id for s in specs if s.id in found]
    # 대상 생략 후속의 승계 재료(plans/157 W3) — 대상별 보기로 조회한 해석 인스턴스만(목록·설정
    # 보기 턴은 싣지 않는다 · C-2). 없으면 키를 싣지 않는다(종전 스코프 바이트 불변)
    last = _last_targets(ctx.instances) if _per_target_views(ctx.views, ctx.by_id) else []
    if last:
        meta["source_selection"][LAST_TARGETS_KEY] = last
    return rows, source_rows


async def _untargeted_step(
    session: Any, scope: _JobScope, by_id: dict[str, ViewSpec], top_n: int | None,
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """대상 미지정 첫 홉(plans/134 W6 ④ — 2026-10-07 사용자 결정 · LLM 0).

    `top_n`이 있으면 현재 부하(TPS) 순위(`apm_fleet` ranking · 실시간 — 기간 인자 없음)로 상위 N개
    인스턴스만 대상으로 삼는다. 순위가 실패하거나 부를 수 있는 대상이 0건이면 **조용히 전체로
    넓히지 않고** 인스턴스 목록(종전 첫 홉)의 앞 N개만 대상으로 삼고 그 사실(부하 순이 아님)을
    단계에 남긴다. `top_n`이 None(분해가 `full`을 냄)이면 종전대로 전 인스턴스다.
    """
    if top_n is None:
        hosts, hostless, step = await _insert_instances_step(session, scope,
                                                             by_id.get(INSTANCES_VIEW))
        step["mode"] = "full"
        return hosts, hostless, step
    hosts, hostless, step = await _ranking_step(session, scope, by_id.get(RANKING_VIEW), top_n)
    if hosts or hostless:
        return hosts, hostless, step
    why = step.get("error") or ("순위 행에 부를 수 있는 대상 없음" if step.get("instances")
                                else "순위에 오른 인스턴스 0개")
    hosts, hostless, step = await _insert_instances_step(session, scope,
                                                         by_id.get(INSTANCES_VIEW), limit=top_n)
    step["mode"] = "list"
    step["rank_error"] = str(why)[:120]
    return hosts, hostless, step


async def _ranking_step(
    session: Any, scope: _JobScope, view: ViewSpec | None, top_n: int,
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """대상 미지정 — 현재 부하(TPS) 내림차순 상위 `top_n`개 인스턴스를 대상으로 고른다(④).

    순위 행의 hostname은 게이트웨이가 정합한 값이다(없으면 이름·소스·id 대상 — 종전 첫 홉 규칙).
    단계에 전체 인스턴스 수(`fleet_total`)·순위 밖 수(`unranked`)·잠정(`provisional`)을 남긴다.
    """
    step: dict[str, Any] = {"view": RANKING_VIEW, "mode": "ranking", "top_n": top_n,
                            "reason": "대상 서버 미지정 — 현재 부하(TPS) 상위 인스턴스 조회"}
    if view is None:
        step["error"] = "순위 보기가 레지스트리에 없다"
        return [], [], step
    args: dict[str, Any] = {**view.fixed_args, "metric": "tps", "order": "desc", "n": top_n,
                            "thread_id": str(scope.thread_id) if scope.thread_id else None,
                            "owner": scope.owner}
    if scope.source_ids:
        args["source_ids"] = list(scope.source_ids)
    _step_call(step, view, args)
    call = _Call(view, None, args)
    await _run_calls(session, [call], 1, scope)
    if call.error is None and jobs.is_live(call.envelope):
        await _await_first_hop(session, call, scope)
    if call.error:
        step["error"] = _public_call_error(call.error, where=f"첫 홉({view.tool})")
        return [], [], step
    env = call.envelope or {}
    rows = [r for r in env.get("rows") or [] if isinstance(r, dict)]
    total_rows = env.get("total_row_count")
    if (isinstance(total_rows, int) and not isinstance(total_rows, bool)
            and total_rows > len(rows) and len(rows) < top_n):
        # 순위가 인라인 행 상한을 넘어 결과 파일로 갔다 — 상위 N까지 끝까지 읽는다(V34-2 규칙)
        rows, short = await _read_result_file(session, env, scope, rows, total_rows)
        if short:
            step["read_error"] = short
    rows = rows[:top_n]
    raw_summary = env.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    hosts, hostless, unaddressed = _target_rows(rows, host_ok=lambda _row: True)
    # 호스트별 소스 — 호출부가 꺼내 (호스트 · 소스) 대상으로 부른다(메타에는 남기지 않는다 · V6-3)
    step["_host_sources"] = _host_sources(rows)
    # 실패 소스(plans/147 §4.5) — 호출부가 꺼내 소스별 상태 행에 쓴다(메타에는 남기지 않는다)
    step["_source_failures"] = _source_failure_codes(env.get("sources"))
    total = _num(summary.get("instances_total"))
    step.update({"hosts": len(hosts), "instances": len(rows),
                 "fleet_total": int(total) if total is not None else len(rows)})
    unranked = _num(summary.get("instances_unranked"))
    if unranked:
        step["unranked"] = int(unranked)
    if env.get("provisional"):
        step["provisional"] = int(_num(summary.get("domains_failed")) or 0)
    if hostless:
        step["hostless"] = len(hostless)
    if unaddressed:
        step["unaddressed"] = unaddressed
    return hosts, hostless, step


async def _insert_instances_step(
    session: Any, scope: _JobScope, view: ViewSpec | None, *, limit: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """대상 미지정 — 인스턴스 목록 보기를 먼저 불러 대상을 고른다(첫 홉 삽입 · LLM 0).

    `limit`이 없으면 **절단 없이 전부**다(plans/134 M-5 · D-296 ④). `limit`은 부하 순위를 받지
    못했을 때의 폴백(④)이다 — 목록 순서 앞 `limit`개만 대상이고 전체 수를 `listed`로 남긴다.
    호스트가 정합된 인스턴스는 hostname 대상(서버당 하나 — 그 서버의 인스턴스를 게이트웨이가
    정합한다), 호스트가 없는 인스턴스는 (이름 · 소스 · id) 대상이다 — 조용히 빠지는 인스턴스가
    없다. 이름·소스·id가 없어 부를 수 없는 행은 단계에 수(`unaddressed`)로 남겨 고지한다.

    목록 조회가 작업으로 승격되면 조회 마감까지 기다리고, 그래도 끝나지 않으면 그 작업을 취소하고
    사유를 남긴다 — 사용자가 맡긴 조회가 아니라 대상 선정 단계라 장부에 올리지 않는다.

    목록이 인라인 행 상한을 넘어 결과 파일로 가면(`total_row_count` > 실은 행) 나머지를 결과 파일
    청크(`apm_job_read`)로 끝까지 읽는다(V34-2). 끝까지 읽지 못하면 읽은 범위와 사유를 단계에 남긴다
    (`total` · `read_error` — 호출부가 「전체」라고 쓰지 않고 부분 결과로 알린다).

    Returns:
        (hostname 대상, 호스트 없는 인스턴스 대상, 단계 기록)
    """
    step: dict[str, Any] = {
        "view": INSTANCES_VIEW, "reason": "대상 서버 미지정 — 전체 인스턴스 조회",
    }
    if view is None:
        step["error"] = "인스턴스 목록 보기가 레지스트리에 없다"
        return [], [], step
    args: dict[str, Any] = {"thread_id": str(scope.thread_id) if scope.thread_id else None,
                            "owner": scope.owner}
    if scope.source_ids:
        args["source_ids"] = list(scope.source_ids)
    _step_call(step, view, args)
    call = _Call(view, None, args)
    await _run_calls(session, [call], 1, scope)
    if call.error is None and jobs.is_live(call.envelope):
        await _await_first_hop(session, call, scope)
    if call.error:
        step["error"] = _public_call_error(call.error, where=f"첫 홉({view.tool})")
        return [], [], step
    env = call.envelope or {}
    rows = [r for r in env.get("rows") or [] if isinstance(r, dict)]
    raw_total = env.get("total_row_count")
    total = raw_total if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None
    if total is not None and total > len(rows) and (limit is None or len(rows) < limit):
        rows, short = await _read_result_file(session, env, scope, rows, total)
        step["total"] = total
        if short:
            step["read_error"] = short
    if limit is not None:  # 폴백 — 목록 앞 `limit`개(결과 파일 나머지는 읽지 않는다)
        step["listed"] = max(total, len(rows)) if total is not None else len(rows)
        rows = rows[:limit]
    hosts, hostless, unaddressed = _target_rows(
        rows, host_ok=lambda row: bool(row.get("match_confidence")))
    step["_source_failures"] = _source_failure_codes(env.get("sources"))
    step["hosts"] = len(hosts)
    step["instances"] = len(rows)
    if hostless:
        step["hostless"] = len(hostless)
    if unaddressed:
        step["unaddressed"] = unaddressed
    return hosts, hostless, step


async def _read_result_file(session: Any, env: dict[str, Any], scope: _JobScope,
                            inline: list[dict[str, Any]], total: int,
                            ) -> tuple[list[dict[str, Any]], str]:
    """결과 파일로 간 목록의 행 전부 — 청크(`apm_job_read`)를 처음부터 끝까지 읽는다(V34-2).

    Returns:
        (행, 못 읽은 사유) — 끝까지 읽었으면 사유는 빈 문자열이다. 중간에 실패하면 읽은 행(인라인
        미리보기보다 적으면 미리보기)과 사유를 돌려준다(조용히 자르지 않는다).
    """
    raw_artifact = env.get("artifact")
    artifact: dict[str, Any] = raw_artifact if isinstance(raw_artifact, dict) else {}
    job_id = artifact.get("job_id") or (jobs.job_handle(env) or {}).get("job_id")
    chunks = artifact.get("chunks") or []
    if not job_id or not isinstance(chunks, list) or not chunks:
        return inline, "결과 파일 정보(작업·청크)가 봉투에 없음"
    out: list[dict[str, Any]] = []
    for index in range(len(chunks)):
        reason = ""
        try:
            part = await session.call_tool(jobs.READ_TOOL, {
                "job_id": str(job_id), "owner": scope.owner, "chunk": index})
        except SourceMcpError as e:
            reason = str(e)
        else:
            reason = _envelope_error(part) or ""
        if reason:
            reason = _public_call_error(reason, where="결과 파일 읽기")
            return (out if len(out) >= len(inline) else inline,
                    f"결과 파일 청크 {index + 1}/{len(chunks)} 읽기 실패 — {reason}")
        out += [r for r in part.get("rows") or [] if isinstance(r, dict)]
    if len(out) < total:
        return out, f"결과 파일에서 {len(out)}행만 읽음(전체 {total}행)"
    return out, ""


# ── 대상 텍스트 해석 (plans/130 M-2·M-4·M-6 · LLM 0 · 상한 없음 — D-296 ④) ─────────────

def _short_label(system: str) -> str:
    """시스템 표시 이름의 짧은 표기(괄호 설명 앞) — 고지 문구용."""
    label = get_registry().system_label(system)
    return label.split("(")[0].strip() or label


async def _gateway_search(session: Any, arg: str, text: str, view: ViewSpec, domain_id: Any,
                          scope: _JobScope, apm: str) -> _Found:
    """게이트웨이 검색 1회 — `query`(인스턴스 이름·설명) 또는 `business`(업무명)."""
    found = _Found(f"{apm} 인스턴스 이름·설명" if arg == "query" else f"{apm} 업무명")
    call = _Call(view, None, _search_args(scope, domain_id, **{arg: text}))
    await _lookup(session, [call], 1, scope)
    if call.error:
        found.errors.append(f"{found.searched} 검색 실패 — "
                            + _public_call_error(call.error, where="대상 해석 검색"))
        return found
    env = call.envelope or {}
    found.answered = True
    for row in env.get("rows") or []:
        if not isinstance(row, dict):
            continue
        kinds = (row.get("match_kinds") or [row.get("match_kind")]) if arg == "business" \
            else ["instance_name"]
        labels = [_EVIDENCE_TEXT[str(k)].format(apm=apm, owner="") for k in kinds
                  if str(k) in _EVIDENCE_TEXT]
        found.rows.append((row, labels or [found.searched]))
    found.suggestions = list(dict.fromkeys(
        str(s["instance_name"]) for s in env.get("suggestions") or []
        if isinstance(s, dict) and s.get("instance_name")))[:MAX_SELECTION_CANDIDATES]
    found.limits = [x for x in env.get("limits") or [] if isinstance(x, str)]
    return found


async def _business_hosts(session: Any, text: str, view: ViewSpec, domain_id: Any,
                          scope: _JobScope, concurrency: int, isolated: dict[str, Any],
                          app_config: Any, ledger: list[LinkEntry],
                          meta: dict[str, Any]) -> _Found:
    """업무명 간선 E6(등록명·비고 → hostname · 간선 소유자 어댑터) → 게이트웨이 정합 E1r
    (hostname → 인스턴스). 그 서버에 인스턴스가 없으면(`instance_unresolved`) 0건이다."""
    found = _Found("업무명 → 서버")
    try:
        hits, entries, steps = await link_business_names(
            [text], app_config=app_config,
            authorized_db_ids=_authorized_db_ids(isolated, app_config))
    except Exception as e:  # noqa: BLE001 — 간선 실패여도 게이트웨이 근거로 계속한다
        logger.warning("%s 업무명 간선(E6) 실패: %s", APM_QUERY_AGENT, type(e).__name__)
        found.errors.append(f"업무명 → 서버 조회 실패({type(e).__name__})")
        return found
    ledger.extend(entries)
    meta["inserted_steps"] += steps
    owner_code = next((str(s["owner"]) for s in steps if s.get("owner")), "")
    owner = _short_label(owner_code) if owner_code else ""
    found.searched = f"{owner} 등록명·비고" if owner else found.searched
    found.answered = any(e.status in (LINKED, UNLINKED) for e in entries)
    errors = [str(x) for s in steps for x in s.get("errors") or []]
    if errors:
        found.errors.append(f"{found.searched} 조회 {'일부 ' if found.answered else ''}실패"
                            f" — {'; '.join(errors)[:160]}")
    if any(s.get("truncated") for s in steps):  # 간선 행 상한 — 침묵하지 않는다(V130-3)
        found.errors.append(f"{found.searched} 조회 상한에 닿아 일부만 확인 — 빠진 서버가 있을 수"
                            " 있습니다")
    hosts: dict[str, list[str]] = {}
    for hit in hits.get(text) or []:
        labels = hosts.setdefault(str(hit.get("hostname")), [])
        label = _EVIDENCE_TEXT.get(str(hit.get("field")), "{owner} 등록명").format(
            apm="", owner=owner)
        if label not in labels:
            labels.append(label)
    calls = [_Call(view, h, _search_args(scope, domain_id, hostname=h)) for h in hosts]
    if calls:
        await _lookup(session, calls, concurrency, scope)
    for call in calls:
        if call.error:
            if "instance_unresolved" not in call.error:
                found.errors.append(f"{call.hostname} 인스턴스 정합 실패 — "
                                    + _public_call_error(call.error, where="인스턴스 정합"))
            continue
        for row in (call.envelope or {}).get("rows") or []:
            if isinstance(row, dict):  # 정합 행에는 hostname 칸이 없다 — 그 서버의 인스턴스다
                found.rows.append(({**row, "hostname": row.get("hostname") or call.hostname},
                                   hosts[str(call.hostname)]))
    return found


async def _resolve_target_texts(
    session: Any, texts: list[dict[str, str]], view: ViewSpec | None, domain_id: Any,
    scope: _JobScope, concurrency: int, isolated: dict[str, Any], app_config: Any,
    ledger: list[LinkEntry], meta: dict[str, Any],
) -> list[_TargetText]:
    """대상 텍스트 → APM 인스턴스(plans/130 M-2 ②).

    - `instance`·`auto` → 게이트웨이 인스턴스 이름 검색(`query`).
    - `business`, 또는 `auto`인데 인스턴스 0건 → 게이트웨이 업무명(`business`) ∥ 업무명 간선
      (E6 → E1r). 근거마다 따로 시도하고(한 근거 실패가 다른 근거를 막지 않는다) 합집합에 근거를
      붙인다(G-3 ①). 합치는 순서는 게이트웨이 → 간선으로 고정한다(결정적).
    - 인스턴스 목록 보기의 검증된 `domain_id`가 있으면 검색에 싣는다(AND).
    """
    apm = _short_label(APM_SYSTEM)
    out: list[_TargetText] = []
    for item in texts:
        res = _TargetText(item["text"], item["kind"])
        out.append(res)
        if _too_long(res.text):  # 게이트웨이 검색어 상한 밖 — 부르지 않는다(고지만 · V130-7)
            continue
        if view is None:
            res.failures.append("인스턴스 목록 보기가 레지스트리에 없다")
            continue
        if res.kind in ("instance", "auto"):
            res.take(await _gateway_search(session, "query", res.text, view, domain_id, scope,
                                           apm))
        if res.kind == "business" or (res.kind == "auto" and not res.instances):
            by_gateway, by_edge = await asyncio.gather(
                _gateway_search(session, "business", res.text, view, domain_id, scope, apm),
                _business_hosts(session, res.text, view, domain_id, scope, concurrency,
                                isolated, app_config, ledger, meta))
            res.take(by_gateway)
            res.take(by_edge)
    return out


def _target_notices(resolved: list[_TargetText], source: str) -> list[disc.Disclosure]:
    """대상 텍스트 고지(plans/130 M-6 · 123 `disclosures[]`).

    - 해석: 「'텍스트' → APM 인스턴스 N개(근거: …)」 — 값 기반 키 연결 보고(`bridge` · 등급 중립).
    - 찾지 못함: 검색한 근거 + 유사 이름 후보(≤3 · 자동 채택 안 함) — `apm_unresolved_condition`.
    - 근거 조회 실패(다른 근거로 계속): `apm_partial_sources`.
    """
    apm = _short_label(APM_SYSTEM)
    out: list[disc.Disclosure] = []
    for res in resolved:
        if _too_long(res.text):
            out.append(disc.make(
                disc.APM_UNRESOLVED_CONDITION,
                f"대상 이름이 너무 길어({TARGET_TEXT_MAX}자 초과) {apm} 인스턴스를 조회하지"
                f" 않았습니다('{_shown(res.text)}'). 다른 인스턴스로 대신 조회하지 않았습니다.",
                source=source))
            continue
        if res.instances:
            counts = _targets_meta([res])["texts"][0]["evidence"]
            basis = " · ".join(f"{label} {n}" for label, n in counts.items())
            out.append(disc.make(NOTE_BRIDGE, f"'{res.text}' → {apm} 인스턴스"
                                 f" {len(res.instances)}개" + (f"(근거: {basis})" if basis else ""),
                                 source=source))
        else:
            how = " · ".join(res.searched)
            head = (f"'{res.text}'에 해당하는 {apm} 인스턴스를 찾지 못해 조회하지 않았습니다"
                    + (f"(검색: {how})" if how else "") if res.answered else
                    f"'{res.text}'에 해당하는 {apm} 인스턴스를 확인하지 못해 조회하지 않았습니다"
                    "(검색 실패)")
            tail = (f" 비슷한 이름: {' · '.join(res.suggestions)} — 자동으로 고르지 않았습니다."
                    if res.suggestions else "")
            out.append(disc.make(disc.APM_UNRESOLVED_CONDITION,
                                 f"{head}. 다른 인스턴스로 대신 조회하지 않았습니다.{tail}",
                                 source=source))
        out += [disc.make(disc.APM_PARTIAL_SOURCES, f"'{res.text}' 대상 해석: {failure}",
                          source=source) for failure in res.failures]
    return out


# ── 장기 작업 (plans/134 W0-B · SPEC-apm-question-coverage §3.8) ───────────────

def _view_label(view: ViewSpec) -> str:
    labels = {c.code: c.label for c in get_registry().capability_specs()}
    return view.label or labels.get(view.capability, view.id)


def _scope_text(call: _Call) -> str:
    """고지 문구의 조회 범위 — 보기 · 대상 · 창."""
    parts = [_view_label(call.view)]
    if call.hostname:
        parts.append(call.hostname)
    elif call.instance:  # 해석 인스턴스로 부른 호출(plans/130 M-2)
        parts.append(str(call.instance.get("instance_name")))
    elif call.members:  # 다건 대상 배치(plans/134 M-5)
        parts.append(f"대상 {len(call.members)}개")
    elif call.named:  # 이름 보기(서비스·업무)
        parts.append(", ".join(call.named))
    minutes = call.args.get("lookback_minutes")
    if minutes:
        reference = call.args.get("reference_time")
        parts.append(f"{minutes}분 구간" + (f"(기준 {reference})" if reference else ""))
    return " · ".join(parts)


async def _settle_jobs(session: Any, calls: list[_Call], scope: _JobScope) -> None:
    """작업 핸들이 있는 봉투를 장부에 올리고, 진행 중 작업은 조회 마감까지 상태를 다시 본다.

    장부 등록은 실행 인가(`is_source_allowed`)를 지난 뒤다(SPEC §7.6).
    """
    store = None
    for call in calls:
        handle = None if call.error else jobs.job_handle(call.envelope)
        if handle is None or call.envelope is None:
            continue
        call.job = handle
        store = store or jobs.store_for(scope.app_config)
        try:
            call.ledger = await jobs.register(
                store, call.envelope, sub=scope.sub, thread_id=scope.thread_id,
                tool=call.view.tool, view=call.view.id, view_label=_view_label(call.view),
                hostname=call.hostname, scope=_scope_text(call),
            )
        except Exception as e:  # noqa: BLE001 — 장부 실패여도 접수·결과 파일 사실은 고지한다
            logger.warning("APM 작업 장부 등록 실패(job_id=%s): %s", handle["job_id"][:8], e)
    live = [c for c in calls if c.job is not None and jobs.is_live(c.envelope)]
    if live:
        await asyncio.gather(*(_await_job(session, c, scope, store) for c in live))


def _partial_text(call: _Call, envelope: dict[str, Any]) -> str:
    failed = [str(x).removeprefix("[한계]").strip() for x in envelope.get("limits") or []
              if isinstance(x, str) and ("실패" in x or "불가" in x)]
    tail = f" — {failed[0][:120]}" if failed else ""
    return f"{_scope_text(call)}: 일부 소스·도메인·구간 조회가 실패해 부분 결과입니다{tail}"


def _job_disclosures(calls: list[_Call], meta: dict[str, Any],
                     task_id: str) -> list[disc.Disclosure]:
    """작업 접수 · 결과 파일 · 부분 결과 고지(SPEC §7.5) — 작업 참조는 `ref`로 싣는다.

    배치(plans/134 M-5)는 작업·결과 파일이 배치 단위라 배치 호출에서, 부분 결과·게이트웨이 고지·보기
    고정 고지는 대상별 가상 호출에서 싣는다(대상별 실패는 `meta["failures"]`).
    """
    source = f"task:{task_id}" if task_id else "task"
    out: list[disc.Disclosure] = []
    handles: list[dict[str, Any]] = []
    for call in [*calls, *_batch_parents(calls)]:
        if call.error:
            continue
        handle = call.job
        ref = {disc.REF_APM_JOB_ID: str(handle["job_id"])} if handle else None
        if handle:
            handles.append({"job_id": handle["job_id"], "view": call.view.id,
                            "hostname": call.hostname, "state": handle.get("state"),
                            "accepted": call.accepted, "ledger": call.ledger})
        if call.accepted:
            out.append(disc.make(
                disc.APM_JOB_ACCEPTED,
                f"{_scope_text(call)}: 오래 걸리는 조회라 작업으로 실행 중입니다"
                f"({_progress_text(handle or {})}). 끝나면 작업 카드에서 결과를 보고 내려받을 수"
                " 있습니다.",
                source=source, ref=ref))
            continue
        envelope = call.envelope or {}
        total = envelope.get("total_row_count")
        shown = len(envelope.get("rows") or [])
        if (ref and envelope.get("artifact") and isinstance(total, int)
                and not isinstance(total, bool) and total > shown):
            out.append(disc.make(
                disc.APM_FULL_RESULT_FILE,
                f"{_scope_text(call)}: 화면·CSV는 앞 {shown:,}행입니다 — 전체 {total:,}행은 작업"
                " 카드의 「전체 결과 받기」로 내려받으세요." + _in_file_tail(call),
                source=source, ref=ref))
            meta["notes"].append(f"{call.view.id}: 결과가 커서 앞 {shown}행만 실었습니다"
                                 f"(전체 {total}행 — 결과 파일)")
        if call.members:  # 배치 봉투 — 나머지 고지는 대상별 가상 호출이 싣는다
            continue
        if envelope.get("partial") or envelope.get("provisional"):
            out.append(disc.make(disc.APM_PARTIAL_SOURCES, _partial_text(call, envelope),
                                 source=source))
        out += _gateway_disclosures(envelope, source)
        kinds = list(call.view.notices)
        if call.view.window == "hourly" or isinstance(envelope.get("hourly"), dict):
            kinds.append(disc.APM_HOURLY_RESOLUTION)
        for kind in dict.fromkeys(kinds):
            out.append(disc.make(kind, f"{_scope_text(call)}: {_NOTICE_TEXT.get(kind, '')}",
                                 source=source))
    if handles:  # 작업이 없는 조회는 메타 모양이 종전과 같다
        meta["jobs"] = handles
        meta["accepted_jobs"] = [h["job_id"] for h in handles if h["accepted"]]
    return disc.dedupe(out)


def _accepted_answer(label: str, accepted: list[_Call], meta: dict[str, Any],
                     disclosures: list[disc.Disclosure]) -> dict[str, Any]:
    """접수 답 — 데이터 답이 아니다(`source_status.status = "accepted"` · 하네스 미완료)."""
    lines = [d["text"] for d in disclosures if d["kind"] == disc.APM_JOB_ACCEPTED]
    body = [f"{label} 조회가 오래 걸려 작업으로 접수했습니다 — 아직 조회 결과가 아닙니다.",
            *(f"- {line}" for line in lines)]
    failed = [(str(f["hostname"]), str(f.get("reason") or ""))
              for f in meta["failures"] if f.get("hostname")]
    if failed:
        body.append(_failed_targets_text(label, failed))
    if any(c.ledger == "memory" for c in accepted):
        body.append("작업 기록이 서버 메모리에만 있어 서버가 다시 시작되면 작업 카드에서 결과를"
                    " 받을 수 없습니다.")
    meta["source_status"] = _status(label, "accepted", 0, f"작업 접수 {len(accepted)}건(진행 중)")
    return {
        "final_response": "\n".join(body),
        META_KEY: meta,
        "source_status": [meta["source_status"]],
        "disclosures": disclosures,
        "accepted_jobs": list(meta.get("accepted_jobs") or []),
    }


def _apm_hop_ledger(calls: list[_Call]) -> list[LinkEntry]:
    """hostname → WAS 인스턴스 정합(간선 E1r · 게이트웨이 소유) 결과를 장부에 옮긴다.

    호스트당 1행이다.
    """
    entries: dict[str, LinkEntry] = {}
    for call in calls:
        if not call.hostname or not call.view.required_input or call.hostname in entries:
            continue
        env = call.envelope or {}
        resolution = env.get("instance_resolution")
        if call.error is None and isinstance(resolution, dict):
            matched = bool(resolution.get("matched"))
            entries[call.hostname] = LinkEntry(
                call.hostname, "apm_instance", "E1r", LINKED if matched else UNLINKED,
                grade=str(resolution.get("confidence")) if matched else None,
                reason="" if matched else str(resolution.get("reason") or ""))
        elif call.error and "instance_unresolved" in call.error:
            entries[call.hostname] = LinkEntry(call.hostname, "apm_instance", "E1r", UNLINKED,
                                               reason=call.error[:120])
    return list(entries.values())


def _aggregate_of(call: _Call, env: dict[str, Any]) -> dict[str, Any]:
    """봉투의 행 밖 집계를 (보기, 대상) 한 항목으로(plans/134 M-1 — 덮어쓰지 않는다)."""
    agg: dict[str, Any] = {"view": call.view.id, "tool": env.get("tool") or call.view.tool,
                           "hostname": call.hostname, "scope": _scope_text(call)}
    for key in _AGGREGATE_KEYS:
        if env.get(key) not in (None, [], {}):
            agg[key] = env[key]
    windows = [
        {"instance_id": r.get("instance_id"), "instance_name": r.get("instance_name"),
         **r["window"]}
        for r in env.get("rows") or []
        if isinstance(r, dict) and isinstance(r.get("window"), dict)
        and r["window"].get("calls") is not None
    ]
    if windows:
        agg["row_windows"] = windows
    # 변경 전후 비교 행(plans/134 W6 A-2 · `apm_change_impact`) — 결정적 줄 재료
    changes = [r for r in env.get("rows") or []
               if isinstance(r, dict) and isinstance(r.get("delta"), dict)
               and r.get("change_detected_ms") is not None]
    if changes:
        agg["changes"] = changes
    summary = env.get("summary")
    if isinstance(env.get("coverage"), dict) or (isinstance(summary, dict)
                                                 and "instances_ranked" in summary):
        # 전 대상 순위·이벤트(plans/134 W3 · 계약 A-6) — 결정적 줄의 건수(스풀이면 전체 행 수)
        total = env.get("total_row_count")
        agg["row_count"] = total if isinstance(total, int) and not isinstance(total, bool) \
            else env.get("row_count")
        if call.args.get("lookback_minutes") is not None:  # 기간 순위를 요청했다(W6 A-4)
            agg["period_requested"] = True
    if isinstance(summary, dict) and summary.get("guid"):
        # GUID 추적의 기본 창 고지(V-3) — 게이트웨이 `[한계]` 문구 그대로 결정적 줄에 옮긴다
        agg["window_notes"] = [
            x.removeprefix("[한계]").strip() for x in env.get("limits") or []
            if isinstance(x, str) and _TRACE_DEFAULT_WINDOW_MARK in x]
    return agg


def _merge_traces(group: list[tuple[_Call, dict[str, Any], list[dict[str, Any]]]],
                  ) -> dict[str, Any]:
    """같은 GUID를 좁힘 서버마다 부른 집계를 한 항목으로 합친다(V-4).

    `group`은 (호출, 집계, 중복 제거 뒤 남긴 행)이다. 거래·거래가 나온 도메인은 남긴 행으로 세고,
    조회 도메인은 서버 정합 결과(`instance_resolution.instance_refs`)의 (소스, 도메인) 합집합이다
    — 정합 결과가 없는 호출이 있으면 모른다(None · 줄에서 뺀다). 실패 도메인은 호출별 합이다
    (조회 도메인 수를 넘지 않게).
    """
    call, first, _ = group[0]
    summaries = [agg["summary"] for _, agg, _ in group]
    kept = [row for _, _, rows in group for row in rows]
    scopes = [{(r.get("source_id"), r.get("domain_id"))
               for r in ((c.envelope or {}).get("instance_resolution") or {}).get(
                   "instance_refs") or [] if isinstance(r, dict)} for c, _, _ in group]
    queried = len(set().union(*scopes)) if all(scopes) else None
    failed = sum(int(_num(s.get("domains_failed")) or 0) for s in summaries)
    starts = [v for v in (_num(s.get("first_start_ms")) for s in summaries) if v is not None]
    ends = [v for v in (_num(s.get("last_end_ms")) for s in summaries) if v is not None]
    summary = {
        **summaries[0],
        "transactions": len(kept),
        "domains_with_hits": len({(r.get("source_id"), r.get("domain_id")) for r in kept}),
        "domains_queried": queried,
        "domains_failed": min(failed, queried) if queried is not None else failed,
        "first_start_ms": min(starts) if starts else None,
        "last_end_ms": max(ends) if ends else None,
        "instances": list(dict.fromkeys(
            n for s in summaries for n in s.get("instances") or [] if n not in (None, ""))),
    }
    hosts = [c.hostname for c, _, _ in group if c.hostname]
    merged = {**first, "hostname": None, "summary": summary,
              "scope": " · ".join([_view_label(call.view), *hosts]),
              "window_notes": list(dict.fromkeys(
                  n for _, agg, _ in group for n in agg.get("window_notes") or []))}
    if any(agg.get("partial") for _, agg, _ in group):
        merged["partial"] = True
    return merged


def _collect(calls: list[_Call], meta: dict[str, Any]) -> list[dict[str, Any]]:
    """봉투 → 행 · 출처(보기·도구·대상·기준 시각·창·정합) · 실패 사유 · 집계·판정(M-1)."""
    rows: list[dict[str, Any]] = []
    limits: list[str] = []
    meta["aggregates"] = []
    signals: list[dict[str, Any]] = []
    # GUID 추적은 좁힘 서버마다 1호출이라 같은 도메인의 서버 둘이면 같은 거래가 두 번 온다(V-4) —
    # 행은 (소스, 도메인, txid)로 호출 사이에서도 한 번만 싣고, 집계는 GUID마다 하나로 합친다
    seen_tx: set[tuple[Any, Any, str]] = set()
    traces: dict[str, list[tuple[_Call, dict[str, Any], list[dict[str, Any]]]]] = {}
    for call in calls:
        if call.error:
            failure: dict[str, Any] = {"view": call.view.id, "hostname": call.hostname,
                                       "reason": call.error}
            if call.instance:
                failure["instance"] = call.instance
            meta["failures"].append(failure)
            continue
        if call.accepted:  # 접수 — 데이터가 아니다(`_job_disclosures`가 따로 싣는다)
            continue
        env = call.envelope or {}
        resolution = env.get("instance_resolution") if isinstance(env.get("instance_resolution"),
                                                                    dict) else None
        entry: dict[str, Any] = {
            "view": call.view.id, "tool": env.get("tool") or call.view.tool,
            "hostname": call.hostname, "queried_at": env.get("queried_at"),
            "window": env.get("window"), "rows": env.get("row_count"),
            "confidence": (resolution or {}).get("confidence"),
        }
        # 고정 인자 + 검증된 조건(감사 `commands` — W1 검증 L-6). 없으면 종전 모양 그대로다.
        names = {*call.view.fixed_args, *(a.tool_arg or a.name for a in call.view.args),
                 *((call.reference or {}).get("fields") or []),
                 # 대상 텍스트(plans/130): 해석 인스턴스 인자 · 해석 행 답(`_answer_call`)의 텍스트
                 *(("instance_name", "instance_id") if call.instance else ()),
                 # 고른 소스 · 이름 보기의 이름 목록(plans/134 M-5 — 없으면 종전 모양)
                 *((call.view.target_arg,) if call.view.target_arg else ()),
                 "source_ids", "targets"}
        used = {k: v for k, v in call.args.items() if k in names and v is not None}
        if used:
            entry["args"] = used
        if call.reference:  # 앞 결과 행 참조(plans/134 M-6) — 어느 결과의 몇 번째 행인가
            entry["reference"] = call.reference
        if env.get(ROWS_IN_FILE_KEY):  # 배치 결과 파일에만 있는 이 대상의 행 수(R34-1)
            entry[ROWS_IN_FILE_KEY] = env[ROWS_IN_FILE_KEY]
        if call.instance:
            entry["instance"] = call.instance
        meta["provenance"].append(entry)
        for limit in env.get("limits") or []:
            if isinstance(limit, str) and limit not in limits:
                limits.append(limit)
        agg = _aggregate_of(call, env)
        meta["aggregates"].append(agg)
        signals += [{**sig, "hostname": sig.get("hostname") or call.hostname,
                     "view": call.view.id}
                    for sig in env.get("was_signals") or [] if isinstance(sig, dict)]
        is_trace = call.view.reference == "guid"
        kept: list[dict[str, Any]] = []
        for row in env.get("rows") or []:
            if not isinstance(row, dict):
                continue
            if is_trace and row.get("txid") not in (None, ""):
                key = (row.get("source_id"), row.get("domain_id"), str(row["txid"]))
                if key in seen_tx:
                    continue
                seen_tx.add(key)
            out = dict(row)
            if call.hostname and "hostname" not in out:
                out["hostname"] = call.hostname
            kept.append(out)
        rows += kept
        guid = (agg.get("summary") or {}).get("guid") if is_trace else None
        if guid:
            traces.setdefault(str(guid), []).append((call, agg, kept))
    for group in traces.values():
        if len(group) > 1:
            first = group[0][1]
            dropped = {id(agg) for _, agg, _ in group[1:]}
            merged = _merge_traces(group)
            meta["aggregates"] = [merged if a is first else a for a in meta["aggregates"]
                                  if id(a) not in dropped]
    meta["limits"] = limits
    meta["was_signals"] = _dedupe_signals(signals)
    return rows


def _summary(label: str, views: list[str], by_id: dict[str, ViewSpec], meta: dict[str, Any],
             row_count: int) -> str:
    """결정적 요약(LLM 0) — 조회 범위 · 기준 시각 · 잘림 · 실패."""
    reg = get_registry()
    cap_labels = {c.code: c.label for c in reg.capability_specs()}
    view_text = ", ".join(
        f"{by_id[v].label or cap_labels.get(by_id[v].capability, v)}({v})" for v in views
    )
    queried = sorted({p["queried_at"] for p in meta["provenance"] if p.get("queried_at")})
    hosts = len(meta["hostnames"])
    targets = meta.get("targets")
    parts = [f"{label} 조회 — {view_text}: 대상 {hosts}대"
             + (f" · 해석 인스턴스 {len(targets['instances'])}개" if targets else "")
             + f" · {row_count}행" + (f"(기준 시각 {queried[-1]})." if queried else ".")]
    for step in meta["inserted_steps"]:
        if step.get("edge"):
            if step.get("error") or step.get("errors"):
                parts.append(f"{step['edge']} 변환 실패: "
                             + "; ".join([step.get("error") or ""] + (step.get("errors") or []))
                             .strip("; "))
            if step.get("truncated"):
                parts.append(f"{step['edge']} 변환이 조회 상한에 닿아 일부만 확인했습니다.")
            continue
        if step.get("hosts") is not None:  # 첫 홉(plans/134 M-5 — 절단 없이 전 인스턴스)
            parts.append(f"{_first_hop_text(step)}.")
        elif step.get("error"):
            parts.append(f"인스턴스 목록 조회 실패: {step['error']}")
    parts += meta["notes"][:3]
    parts += meta.get("limits", [])[:3]
    failed = [(str(f.get("hostname") or f["instance"].get("instance_name")),
               str(f.get("reason") or ""))
              for f in meta["failures"] if f.get("hostname") or f.get("instance")]
    if failed:
        parts.append(_failed_targets_text(label, failed))
    lines = _answer_lines(meta)
    if lines:  # 결정적 판정·집계(plans/134 M-1) — 요약 입력에도 싣는다
        parts.append("판정·집계: " + " / ".join(lines))
    return " ".join(parts)


APM_QUERY_SPEC = SubAgentSpec(
    APM_QUERY_AGENT,
    "WAS·미들웨어(APM 게이트웨이) 조회 — 인스턴스·응답시간·TPS·에러율·JVM 힙·GC·실행 중 서비스·"
    "느린 트랜잭션·WAS 이벤트·트랜잭션 프로파일·GUID 연계 거래·변경 전후 비교·제니퍼 설정·환경변수·"
    "사용자 계정",
    audited_investigation(run_apm_query, BACKEND_APM),
    purpose=(
        "WAS·미들웨어(APM) 조회 — WAS 인스턴스·응답시간·TPS·에러율·JVM 힙·GC·커넥션 풀·"
        "실행 중 서비스·느린 트랜잭션·**WAS 이벤트**(폴스타 서버 알람이 아님)·트랜잭션 프로파일·"
        "GUID 연계 거래·소스 변경 전후 비교·제니퍼 설정(이벤트 룰·PID→인스턴스·데이터 서버·"
        "로드된 클래스)·WAS 환경변수(JVM 옵션)·제니퍼 사용자 계정·실행 중 요청 상세"
    ),
    backend="mcp",
    input_slots=("entity_set", "time_window"),
    # 대상이 없으면 부하(TPS) 순위 상위 N(실패하면 인스턴스 목록 앞 N)을 코드가 먼저 부른다(첫 홉 ·
    # plans/134 W6 ④)
    required_inputs=(),
    output_type="rows",
    key_facets_out=("hostname", "apm_instance_id", "apm_domain_id"),
    self_filters=("host", "time"),
    # 대상 수 상한 없음(D-296 ④ · plans/134 M-5 — 다건은 게이트웨이 배치 작업) · 동시 호출은 설정값
    # (`composite.fanout_concurrency`) — 계약 값은 M-2 픽스처 지연·J0-O 운영 실측 뒤(plans/125 §4.6)
    prerequisites=("source_endpoint",),
)


def active_extra_subagents(app_config: Any) -> dict[str, SubAgentSpec]:
    """활성인 조건부 처리기(엔드포인트가 설정된 비SQL 시스템) — 비활성이면 빈 dict."""
    return {APM_QUERY_AGENT: APM_QUERY_SPEC} if apm_active(app_config) else {}


__all__ = [
    "APM_QUERY_AGENT",
    "APM_QUERY_SPEC",
    "active_extra_subagents",
    "apm_active",
    "plan_window",
    "run_apm_query",
    "sanitize_targets",
    "sanitize_views",
]
