"""WAS·미들웨어(APM) 1급 처리기 `apm_query` — plans/125 A-3 · 87 J5 본체 쪽 이관(D-281 ①).

**LLM 0회.** 분해 LLM 이 고른 보기(`views[]` — 레지스트리 `solutions[apm].views` 닫힌 어휘)를
게이트웨이 도구 고정 표로 바꾸고, 대상(hostname)·창을 코드가 정해 부른다(plans/125 §4.6 · Q-2).

- **활성일 때만 등록**: 엔드포인트(`MCP_SOURCE_ENDPOINTS` 의 `apm`)가 없으면 처리기도 분해 프롬프트
  줄도 없다 — 비활성 배포는 바이트 불변(신규 `enable_*` 0 · D-162 · D-251 ⑥). 고정 처리기 목록
  (`SUBAGENT_REGISTRY`)에는 넣지 않는다 — 활성일 때만 `active_extra_subagents`가 붙인다.
- **대상**: 선행 결과·이번 턴 식별자·직전 대상에서 hostname 을 고른다(`resolve_targets` 공용 규칙).
  hostname 이 필요한 보기인데 대상이 없으면 인스턴스 목록 보기(`apm.instances`)를 **코드가 먼저**
  부른다(첫 홉 삽입 — §4.2 · §4.3 ③).
- **창**(plans/134 M-2): 보기 창 의미(`ViewSpec.window`)대로 파서 기간(`time_range`)을 **자르지
  않고** 넘긴다(range). 현재값 전용 보기(current)에 기간을 말하면 그 사실을 고지한다. 하루 넘게
  지난 기간은 W6 전까지 조회하지 않고 사유를 남긴다 — **폴스타 값으로 대신하지 않는다**.
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
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.clients.source_mcp_client import SessionFactory, SourceMcpError, open_source_session
from src.config import AppConfig
from src.domain import disclosure as disc
from src.domain.result_refs import TABLE_LABELS_KEY, extract_result_refs, ref_label, ref_time_ms
from src.orchestration import apm_jobs as jobs
from src.orchestration.db_access import access_denied_result
from src.orchestration.entity_link import (
    AMBIGUOUS,
    LINKED,
    NOT_QUERIED,
    UNLINKED,
    LinkEntry,
    ledger_line,
    ledger_summary,
    link_business_names,
    link_hostnames,
)
from src.orchestration.investigation_audit import BACKEND_APM, audited_investigation
from src.orchestration.subagents import SubAgentSpec
from src.routing.db_authz import (
    SOURCE_ACCESS_DENIED_MESSAGE,
    authorized_db_ids,
    is_source_allowed,
)
from src.routing.registry import ViewArgSpec, ViewSpec, get_registry
from src.utils.json_extract import extract_json_from_response
from src.utils.prior_dependency import NOTE_BRIDGE
from src.utils.prior_targets import TargetRef, resolve_targets
from src.utils.query_gen_common import refers_to_demonstrative_server

logger = logging.getLogger(__name__)

#: 처리기 이름(분해 어휘 — 활성일 때만 렌더) · 시스템 코드(레지스트리 `solutions[apm]`).
APM_QUERY_AGENT = "apm_query"
APM_SYSTEM = "apm"
#: 보기를 고르지 않았고 **이번 턴 대상(식별자·선행 결과)이 있을 때**의 기본 보기(영역 기본 —
#: was_performance). 대상이 없으면 목록 보기(`INSTANCES_VIEW`)다(plans/132 N-4 · G-3 · D-293).
DEFAULT_VIEW = "apm.app_health"
#: 대상 없이 부를 수 있는 목록 보기 — 대상 미지정이면 코드가 먼저 부른다.
INSTANCES_VIEW = "apm.instances"
#: task 하나가 싣는 보기 상한(프롬프트 지시 1~2개 · 넘으면 앞에서 자른다).
MAX_VIEWS = 3
#: 거부·실패 사유 키(host_inspect 와 같은 이름 — 감사가 같은 키를 읽는다).
DEGRADED_KEY = "degraded_reason"
#: 결과 메타(감사·계획 요약·후속 조합이 읽는다).
META_KEY = "apm_query"
#: 창 밖 판정 — 기간 끝이 지금보다 이만큼 이전이면 창 밖으로 본다(보존 기간 미확인 — plans/125 U-3).
#: 폐지와 해상도 자동 선택은 W6(plans/134 M-7)이다.
_OUT_OF_WINDOW_AFTER = timedelta(days=1)
OUT_OF_WINDOW_NOTE = "하루 넘게 지난 기간은 아직 조회하지 않습니다 — 보존 기간 확인 전"
CURRENT_ONLY_NOTE = "현재값 기준입니다 — 현재값만 있는 보기라 요청 기간의 값이 아닙니다"
HOURLY_NOTE = "시 단위 통계라 요청 구간보다 넓은 정시 경계로 집계한 값입니다"
#: 결과에 싣는 결정적 줄(판정·집계)의 키 — 집계기가 최종 답에 그대로 붙인다(plans/134 M-1).
ANSWER_LINES_KEY = "answer_lines"
#: 문자열 조건 값 형식(SPEC §6.3 — 원천 허용값 미공개라 임의 enum 으로 줄이지 않는다). 첫 글자는
#: 게이트웨이(`_IDENT` — 영문 시작)와 같다(W2 검증 B4 — 본체가 통과시킨 값이 게이트웨이에서 보기
#: 전체를 실패시키지 않게). 길이 64는 게이트웨이 상한(정렬 기준·지표 128 · 오류 유형 64) 안쪽이다.
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
#: 계정 ID 형식(plans/134 W7 — 게이트웨이 경로 변수 `account`와 같은 형식 · SPEC §2.4).
_ACCOUNT = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
#: 대문자 토큰(plans/134 W7 — 게이트웨이 경로 변수 `token`과 같은 형식 · 오류 유형 이름).
_TOKEN = re.compile(r"^[A-Z0-9_]{1,64}$")
#: `opaque`(형식 미공개 값 — GUID 등) 길이 상한 — 게이트웨이 검증(1~256자)과 같다(plans/134 W5).
_OPAQUE_MAX = 256
#: `text` 조건에서 거부하는 문자 범주 — 제어(Cc: C0·DEL·C1) · 서식 제어(Cf: 방향 제어·폭 없는
#: 문자 등) · 줄·문단 구분(Zl U+2028 · Zp U+2029)(W2 검증 B5 · SPEC §6.1 「제어 문자 없음」).
_TEXT_REJECT_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
#: 보기 id 없이 낸 평면 조건 묶음을 처리기까지 옮기는 `view_args` 예약 키(W1 검증 L-1).
FLAT_VIEW_ARGS_KEY = "*"
#: 보기의 고정 고지 문구(plans/134 W2 — `ViewSpec.notices`의 kind → 문구).
_NOTICE_TEXT = {
    disc.APM_CHANGE_DETECTION: ("변경 감지 시각(데이터 서버가 소스 변경을 알아챈 시각)이며"
                                " 배포 시각으로 확정할 수 없습니다"),
    disc.APM_HOURLY_RESOLUTION: HOURLY_NOTE,
}
#: 채팅 표·LLM 입력에 싣는 셀 문자열 상한(자) — 전문은 CSV(`query_results`)·결과 파일에 남는다.
DISPLAY_CELL_MAX = 300
#: 표시용 행(`organized_data.rows`)을 줄였다는 결과 표지 — 집계기가 병합 표의 CSV 원천을
#: `query_results`(전문)로 만든다(`result_aggregator._DISPLAY_CUT_KEY`와 같은 값).
DISPLAY_CUT_KEY = "display_rows_cut"
#: 일반 현황 영역 — 보기를 고르지 않아도 기본 보기로 답한다(D-293 · SPEC §6.4 ③).
GENERAL_AREAS = frozenset({"was_performance", "was_instance"})
#: 선택 재시도 미해결 시 되물을 후보 보기 수(SPEC §6.4 ②).
MAX_SELECTION_CANDIDATES = 3
#: 봉투에서 (보기, 대상)별로 옮기는 집계 키(SPEC §7.1).
_AGGREGATE_KEYS = ("summary", "hourly", "errors_by_type", "window", "sources", "partial",
                   "artifact", "job", "hour_start", "hour_end")

#: 테스트가 모의 세션 공장을 끼운다(이 호스트 루트 파이썬에는 `mcp` 가 없다).
_SESSION_FACTORY: SessionFactory | None = None


# ── 활성 · 어휘 ──────────────────────────────────────────────────────────────

def apm_endpoint(app_config: Any) -> tuple[str, str | None] | None:
    """(URL, 토큰) 또는 None(비활성). 설정 대역(MagicMock)은 비활성으로 읽는다."""
    getter = getattr(getattr(app_config, "dbhub", None), "source_endpoint", None)
    if not callable(getter):
        return None
    try:
        endpoint = getter(APM_SYSTEM)
    except Exception:  # noqa: BLE001 — 설정 대역·깨진 설정은 비활성
        return None
    if isinstance(endpoint, tuple) and endpoint and isinstance(endpoint[0], str) and endpoint[0]:
        return endpoint
    return None


def apm_active(app_config: Any) -> bool:
    """APM 시스템이 활성인가(레지스트리 등재 + 엔드포인트 설정)."""
    return bool(get_registry().views_of(APM_SYSTEM)) and apm_endpoint(app_config) is not None


def apm_views() -> tuple[ViewSpec, ...]:
    """레지스트리 보기 표(선언 순서)."""
    return get_registry().views_of(APM_SYSTEM)


def known_view_ids() -> frozenset[str]:
    return frozenset(v.id for v in apm_views())


def sanitize_views(raw: Any) -> list[str]:
    """LLM 이 낸 보기 목록 → 닫힌 어휘만(순서 유지 · 중복 제거 · 상한 `MAX_VIEWS`)."""
    items = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    known = known_view_ids()
    kept: list[str] = []
    for item in items:
        code = str(item).strip() if isinstance(item, str) else ""
        if code in known and code not in kept:
            kept.append(code)
    return kept[:MAX_VIEWS]


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


_WINDOW_TEXT = {"range": "기간 지정 가능", "current": "현재값", "hourly": "시 단위 통계",
                "none": ""}


def _arg_text(arg: ViewArgSpec) -> str:
    form = {
        "int": "정수" + (f" {arg.min} 이상" if arg.min is not None else ""),
        "bool": "true/false",
        "enum": "|".join(arg.choices),
        "str": "이름",
        "str_list": "이름 목록",
        "catalog": f"{arg.catalog or ''} 지표 이름".strip(),
        "text": "문자열",
        "opaque": "문자열(공백 없이 그대로)",
        "account": "계정 ID",
        "token": "대문자 이름",
    }.get(arg.type, arg.type)
    if arg.type == "str_list" and arg.catalog:
        form = f"이름 목록({arg.catalog} 지표)"
    text = f"`{arg.name}` = {form}" + ("(필수)" if arg.required else "")
    return f"{text} — {arg.label}" if arg.label else text


def _target_text(view: ViewSpec) -> str:
    """보기 표의 대상 설명 — 종전 보기(대상 표현 없음)는 종전 문구 그대로다."""
    if view.required_input:
        return "대상 서버 필요"
    if view.target == "optional":
        return "서버를 말하면 그 서버 · 없으면 전체"
    if view.target == "reference":
        return "앞 결과의 행을 가리킴(ref)"
    return "대상 없이 전체 목록"


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


# ── 선택 조건(view_args — plans/134 M-3 · SPEC §6.3) ─────────────────────────

def _coerce_arg(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """조건 값 1개를 선언 형식으로 검증·정규화한다 — (통과 여부, 값)."""
    kind = spec.type
    if kind == "int":
        if isinstance(value, bool):
            return False, None
        text = value.strip() if isinstance(value, str) else ""
        if text and text.isascii() and text.isdigit():  # 「²」·전각 숫자는 거부(W1 검증 L-2)
            value = int(text)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int) or (spec.min is not None and value < spec.min):
            return False, None
        return True, value
    if kind == "bool":
        if isinstance(value, bool):
            return True, value
        text = value.strip().lower() if isinstance(value, str) else ""
        return (True, text == "true") if text in ("true", "false") else (False, None)
    if kind == "enum":
        text = value.strip().lower() if isinstance(value, str) else ""
        lookup = {c.lower(): c for c in spec.choices}
        return (True, lookup[text]) if text in lookup else (False, None)
    if kind in ("str", "catalog"):
        text = value.strip() if isinstance(value, str) else ""
        return (True, text) if _IDENT.match(text) else (False, None)
    if kind == "text":
        text = value.strip() if isinstance(value, str) else ""
        if not text or len(text) > 200 or any(
                unicodedata.category(c) in _TEXT_REJECT_CATEGORIES for c in text):
            return False, None
        return True, text
    if kind == "str_list":
        items = value if isinstance(value, list) else [value]
        clean = [v.strip() for v in items if isinstance(v, str)]
        if not clean or len(clean) != len(items) or not all(_IDENT.match(c) for c in clean):
            return False, None
        return True, clean
    if kind == "opaque":
        text = value.strip() if isinstance(value, str) else ""
        if not text or len(text) > _OPAQUE_MAX or any(
                c.isspace() or unicodedata.category(c) in _TEXT_REJECT_CATEGORIES for c in text):
            return False, None
        return True, text
    if kind == "account":
        text = value.strip() if isinstance(value, str) else ""
        return (True, text) if _ACCOUNT.match(text) and ".." not in text else (False, None)
    if kind == "token":  # 대소문자만 맞춘다(뜻을 바꾸지 않는다) — 게이트웨이 경로 변수 형식
        text = value.strip().upper() if isinstance(value, str) else ""
        return (True, text) if _TOKEN.match(text) else (False, None)
    return False, None


def validate_view_args(view: ViewSpec, raw: Any) -> tuple[dict[str, Any], list[str]]:
    """분해 `view_args[보기]` → (도구 인자, 버린 조건 설명).

    코드가 `ViewArgSpec`으로 형·범위·선택지를 검증한다. 모르는 이름·값은 버리고 그 사실을 돌려준다 —
    호출부가 `apm_unresolved_condition`으로 알린다(다른 조회로 성공 처리하지 않는다 · G-10).
    값이 `null`인 조건은 「미지정」이라 버린 조건으로 세지 않는다.
    """
    if raw is None or raw == {}:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"조건 형식이 아님({type(raw).__name__})"]
    specs = {a.name: a for a in view.args}
    out: dict[str, Any] = {}
    rejected: list[str] = []
    for name, value in raw.items():
        if value is None:  # 미지정 — 버린 조건이 아니다(무고지 · W1 검증 H-2)
            continue
        spec = specs.get(str(name))
        if spec is None:
            rejected.append(f"{str(name)[:40]}(이 보기에 없는 조건)")
            continue
        ok, clean = _coerce_arg(spec, value)
        if not ok:
            rejected.append(f"{spec.name}={str(value)[:40]!r}")
            continue
        out[spec.tool_arg or spec.name] = clean
    return out, rejected


# ── 창 ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WindowPlan:
    """보기 1개의 조회 창 — mode: current(현재·도구 기본) · window(구간) · out(조회 안 함).

    `kind`는 그 창에 붙는 고지 kind(예 `apm_current_only`)다 — 없으면 빈 문자열.
    """

    mode: str
    args: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    kind: str = ""


def _parse_bound(value: Any, *, end: bool) -> datetime | None:
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return day + timedelta(days=1) - timedelta(seconds=1) if end else day
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def plan_window(view: ViewSpec, time_range: Any, now: datetime) -> WindowPlan:
    """파서 기간(`time_range` {start, end})을 보기 창 의미(`ViewSpec.window`)대로 넘긴다.

    - range·hourly: 요청 기간을 **자르지 않고** `reference_time`·`lookback_minutes`로 넘긴다
      (plans/134 M-2 — 종전 창 상한 폐지). hourly 는 시 단위 고지를 붙인다.
    - current: 현재값만 있는 보기 — 기간을 말했으면 `apm_current_only`로 알린다.
    - none: 시간과 무관한 목록 — 기간을 쓰지 않는다.
    - 기간 끝이 하루 넘게 지났으면 조회하지 않는다(W6 M-7 전까지 · 보존 기간 확인 전).
    """
    if not isinstance(time_range, dict) or view.window == "none":
        return WindowPlan("current")
    start = _parse_bound(time_range.get("start"), end=False)
    end = _parse_bound(time_range.get("end"), end=True)
    if start is None and end is None:
        return WindowPlan("current")
    end = min(end or now, now)
    if now - end > _OUT_OF_WINDOW_AFTER:
        return WindowPlan("out", note=OUT_OF_WINDOW_NOTE)
    if view.window == "current":
        return WindowPlan("current", note=CURRENT_ONLY_NOTE, kind=disc.APM_CURRENT_ONLY)
    span = max(1, int(((end - (start or end)).total_seconds()) // 60))
    reference = None if now - end <= timedelta(minutes=1) else end.isoformat(timespec="seconds")
    args = {"reference_time": reference, "lookback_minutes": span}
    # hourly 보기의 시 단위 고지는 조회한 대상마다 붙인다(`_job_disclosures`) — 기간이 없어도
    # 시 경계로 모인다
    return WindowPlan("window", args)


# ── 대상 ──────────────────────────────────────────────────────────────────────

def _int_setting(app_config: Any, name: str, default: int) -> int:
    value = getattr(getattr(app_config, "composite", None), name, default)
    valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if valid else default


def default_views(isolated: dict[str, Any], max_targets: int) -> list[str]:
    """보기를 고르지 않은 task의 기본 보기(plans/132 N-4 · G-3).

    이번 턴 대상(사용자가 말한 식별자 · 선행 결과)이 있으면 응답시간 보기, 없으면 **인스턴스
    목록**이다. 「제니퍼 인스턴스 리스트」처럼 대상 없는 질문에 앞 10개 인스턴스의 응답시간을 내던
    것(실측 4/4 — 9B가 `views`를 비움)을 막는다. 직전 턴 대상은 보지 않는다 — 대상 없는 목록 질문이
    직전 서버로 좁혀지지 않게 한다(명시 보기 `apm.app_health`는 종전대로 직전 대상을 쓴다).
    """
    current = {**isolated, "conversation_context": {}}
    return [DEFAULT_VIEW] if resolve_apm_targets(current, max_targets) else [INSTANCES_VIEW]


def resolve_apm_targets(isolated: dict[str, Any], max_targets: int) -> list[TargetRef]:
    """선행 결과 → 이번 턴 식별자 → 직전 대상 순으로 대상을 고른다(공용 규칙 `resolve_targets`).

    선행 결과는 `prior_targets`(해소본) 또는 `prior_rows`(행)에서 온다 — 이 처리기는 신규라 조사
    대상 승계 플래그(`COMPOSITE_PRIOR_TARGETS_ENABLED`)와 무관하게 데이터 의존을 따른다
    (`input_from`).
    """
    from src.orchestration.process_query import _targets_from_prior_rows  # 지연 — 순환 방지

    parsed = isolated.get("parsed_requirements") or {}
    ctx = isolated.get("conversation_context") or {}
    prior = isolated.get("prior_targets") or _targets_from_prior_rows(
        isolated.get("prior_rows"), db_id=None, max_targets=max_targets,
    )
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        prior_targets=prior,
        previous_entities=ctx.get("previous_entities"),
        db_id=None,
        max_targets=max_targets,
    )
    return list(resolution.targets)


def _demonstrative(isolated: dict[str, Any]) -> bool:
    """턴 원문이 지시어로 직전 서버를 가리키는가(기존 판정 `refers_to_demonstrative_server`)."""
    text = isolated.get("original_user_query") or isolated.get("user_query") or ""
    return refers_to_demonstrative_server(str(text))


def scoped_targets(isolated: dict[str, Any], max_targets: int) -> list[TargetRef]:
    """대상 표현 `optional` 보기의 대상(plans/134 W7 · 계약 §4.1).

    이번 턴 식별자 → 선행 task 결과만 쓴다. 직전 턴 대상은 사용자가 지시어(「그 서버」)로 가리킬
    때만 잇는다 — 대상 없는 설정·관리 질문이 직전 서버로 조용히 좁혀지지 않게 한다(`default_views`와
    같은 이유 · 새 단어 목록 없이 기존 지시어 판정을 쓴다).
    """
    current = isolated if _demonstrative(isolated) else {**isolated, "conversation_context": {}}
    return resolve_apm_targets(current, max_targets)


def explicit_targets(isolated: dict[str, Any], max_targets: int) -> list[TargetRef]:
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
                                 previous_entities=previous, db_id=None, max_targets=max_targets)
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

    dict 항목만 받는다. 텍스트는 앞뒤 공백을 지우고, 비었거나 `TARGET_TEXT_MAX`자를 넘으면
    버린다. 모르는 종류는 `auto`다. 같은 텍스트(대소문자 무시)는 처음 것만 남긴다. 다시 걸러도
    결과가 같다(분해 정제 · 처리기 진입 두 곳에서 부른다 — 재계획 task도 처리기에서 걸러진다).
    """
    items = raw if isinstance(raw, (list, tuple)) else []
    kept: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_text, raw_kind = item.get("text"), item.get("kind")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if not text or len(text) > TARGET_TEXT_MAX or text.casefold() in seen:
            continue
        kind = raw_kind.strip().lower() if isinstance(raw_kind, str) else ""
        seen.add(text.casefold())
        kept.append({"text": text, "kind": kind if kind in TARGET_KINDS else "auto"})
    return kept


def _uses_target(view: ViewSpec, args: dict[str, Any] | None) -> bool:
    """대상으로 좁히는 보기인가 — 대상 필수 보기 · 첫 홉 목록 · 대상별로 부르는 `optional`."""
    if view.target == "optional":
        return _targeted(view, args)
    return view.target == "" and bool(view.required_input or view.first_hop)


def _target_texts(task: dict[str, Any], isolated: dict[str, Any], ledger: list[LinkEntry],
                  legacy_hosts: list[str], max_targets: int) -> list[dict[str, str]]:
    """이번 task의 대상 텍스트(plans/130 M-2 ①).

    분해 `targets` ∪ 파서 서버명 대상 중 등록명 간선(E2)이 잇지 못한 것(`unlinked`·`not_queried`·
    `ambiguous` → `auto` — 여러 hostname이라 잇지 않은 이름도 첫 홉으로 보내지 않는다 ·
    D-290 ⑥)이다. 파서 hostname 대상과 E2가 이은 대상은 종전 경로(hostname 호출)이고, 그것과
    같은 텍스트는 다시 해석하지 않는다(두 번 조회하지 않는다).
    """
    parsed = isolated.get("parsed_requirements") or {}
    names = [t.server_name for t in resolve_targets(
        filter_conditions=parsed.get("filter_conditions"), db_id=None,
        max_targets=max_targets).targets if t.server_name and not t.hostname]
    left = {e.key.casefold() for e in ledger
            if e.facet == "server_name" and e.status in (UNLINKED, NOT_QUERIED, AMBIGUOUS)}
    legacy = ({h.casefold() for h in legacy_hosts}
              | {e.key.casefold() for e in ledger if e.status == LINKED})
    unlinked = [{"text": n, "kind": "auto"} for n in names if n.casefold() in left]
    texts = sanitize_targets([*sanitize_targets(task.get("targets")), *unlinked])
    return [t for t in texts if t["text"].casefold() not in legacy]


# ── 실행 ──────────────────────────────────────────────────────────────────────

@dataclass
class _Call:
    view: ViewSpec
    hostname: str | None
    args: dict[str, Any]
    envelope: dict[str, Any] | None = None
    error: str | None = None
    #: 게이트웨이 작업 핸들(최신 상태) — 승격됐거나 결과 파일이 있을 때(134 W0-B)
    job: dict[str, Any] | None = None
    #: 조회 마감까지 끝나지 않아 작업으로 접수됐다(데이터 아님)
    accepted: bool = False
    #: 장부 저장소 종류(`redis`·`memory`)
    ledger: str | None = None
    #: 앞 결과 행 참조로 부른 호출의 참조 경과(종류 · 출처 · 번호 — plans/134 M-6 · 감사 입력)
    reference: dict[str, Any] | None = None
    #: 대상 텍스트를 해석한 인스턴스로 부른 호출의 대상(이름 · 소스 · 도메인 · id — plans/130 M-2)
    instance: dict[str, Any] | None = None


@dataclass(frozen=True)
class _JobScope:
    """이번 처리기 호출의 작업 맥락 — 소유자 · 스레드 · 호출 상한 · 재확인 마감."""

    sub: Any
    owner: str
    thread_id: Any
    call_timeout: float
    poll_until: float
    app_config: Any


def _envelope_error(envelope: dict[str, Any]) -> str | None:
    if envelope.get("error"):
        return f"{envelope.get('error')}: {str(envelope.get('reason') or '')[:160]}"
    return None


async def _run_calls(session: Any, calls: list[_Call], concurrency: int,
                     scope: _JobScope | None = None) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(call: _Call) -> None:
        async with sem:
            if scope is not None:
                # 호출 직전에 잰다 — 앞 호출이 쓴 시간만큼 조회 마감이 가까워졌다
                call.args["wait_seconds"] = jobs.wait_seconds(scope.call_timeout)
            try:
                call.envelope = await session.call_tool(call.view.tool, call.args)
            except SourceMcpError as e:
                call.error = str(e)
                return
            call.error = _envelope_error(call.envelope)

    await asyncio.gather(*(one(c) for c in calls))


def _call_args(view: ViewSpec, hostname: str | None, window: WindowPlan,
               scope: _JobScope, view_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """도구 인자 = 대상 + 창 + 고정 인자 + 검증된 선택 조건 + owner(SPEC §7.3 — wait_seconds는 호출
    직전에 더한다)."""
    args: dict[str, Any] = {
        "hostname": hostname, "thread_id": str(scope.thread_id) if scope.thread_id else None,
    }
    args.update(window.args)
    args.update(view.fixed_args)
    args.update(view_args or {})
    args["owner"] = scope.owner
    return args


def _refusal(message: str, reason: str, meta: dict[str, Any],
             disclosures: list[disc.Disclosure] | None = None) -> dict[str, Any]:
    """조회하지 못함 — 텍스트 결과(host_inspect 와 같은 규약 · plans/121 TP-1.5)."""
    out: dict[str, Any] = {"error": message, DEGRADED_KEY: reason, "final_response": message,
                           META_KEY: meta, "source_status": [meta["source_status"]]}
    if disclosures:
        out["disclosures"] = disclosures
    return out


def _blocked_refusal(label: str, blocked: dict[str, str], meta: dict[str, Any],
                     disclosures: list[disc.Disclosure]) -> dict[str, Any]:
    """필수 조건·앞 결과 참조를 되물었다 — 질문이 곧 답이다(다른 조회로 대신하지 않는다 · G-10)."""
    failed = "; ".join(f["reason"] for f in meta["failures"][:3])
    text = "\n".join([*blocked.values(),
                      *([f"{label}을(를) 조회하지 않았습니다 — {failed}"] if failed else [])])
    meta["source_status"] = _status(label, "not_queried", 0, "필수 조건·앞 결과 참조 미해결")
    return _refusal(text, "apm_unresolved_condition", meta, disclosures)


def _status(label: str, status: str, rows: int, reason: str = "") -> dict[str, Any]:
    return {"system": APM_SYSTEM, "label": label, "status": status, "rows": rows,
            "reason": reason}


# ── 보기 선택 재시도 (plans/134 M-8 · SPEC §6.4) ───────────────────────────────

@dataclass
class _Selection:
    """이번 task 가 조회할 보기 · 조건 · 선택 경과(메타) · 되묻기 문구.

    `unresolved`는 조회하지 않을 때(덮은 영역 없음), `uncovered`는 덮은 보기는 조회하고 못 덮은
    영역만 되물을 때의 문구다.
    """

    views: list[str]
    view_args: dict[str, Any]
    info: dict[str, Any]
    unresolved: str = ""
    uncovered: str = ""


_SELECTION_SYSTEM = """너는 WAS·미들웨어(APM) 조회 보기 선택기다. 아래 「보기 표」에서만 고른다.

- 사용자가 요청한 정보(영역): <areas>
- 그 정보를 답하는 보기 id 를 `views`에 넣는다. 표에 없는 id 는 쓰지 않는다.
- 맞는 보기가 없으면 `views`를 비운다 — 다른 정보를 보여 주는 보기(응답시간·목록 등)로 대신하지 않는다.
- 보기에 「조건(view_args)」이 있으면 사용자가 말한 조건만 `view_args`에 넣는다(보기 id → 조건 이름 → 값).
- 보기 표 「예」의 값은 형식을 보여 주는 예일 뿐이다 — 질문(사용자 원문 포함)에 없는 값은 `view_args`에 넣지 않는다.
- 기간·시간(「최근 3시간」·「오늘」)은 `view_args`가 아니다 — 조건 이름은 표의 「조건(view_args)」에 있는 것만 쓴다.
- 앞 결과의 몇 번째 행(「두 번째 트랜잭션」)을 가리키면 그 보기의 `ref`에 번호(1부터)를 넣는다. 번호를 말하지 않았으면 넣지 않는다.
- JSON 객체 하나만 출력한다: {"views": ["<보기 id>"], "view_args": {"<보기 id>": {"<조건 이름>": 값}}}

## 보기 표
<rows>
"""  # noqa: E501


def _apm_areas(task: dict[str, Any]) -> list[str]:
    """task 영역(`areas` — 분해 D-295) 중 APM 소유인 것(순서 유지)."""
    reg = get_registry()
    return [str(a) for a in task.get("areas") or []
            if APM_SYSTEM in reg.capability_owners(str(a))]


def _uncovered(views: list[str], areas: list[str], by_id: dict[str, ViewSpec]) -> list[str]:
    """고른 보기들이 덮지 못하는 영역 — 보기가 없으면 일반 현황 밖 영역(SPEC §6.4 ①)."""
    if not views:
        return [a for a in areas if a not in GENERAL_AREAS]
    covered = {by_id[v].capability for v in views if v in by_id}
    return [a for a in areas if a not in covered]


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
    if not isinstance(parsed, dict):
        return [], {}, "JSON 아님"
    views = sanitize_views(parsed.get("views"))
    return views, sanitize_view_args(parsed.get("view_args"), views), ""


async def _select_views(
    task: dict[str, Any], isolated: dict[str, Any], llm: Any, max_targets: int,
    by_id: dict[str, ViewSpec],
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
        return _Selection(planned or default_views(isolated, max_targets), args, info)
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
    pool = list(dict.fromkeys([*planned, *views])) or default_views(isolated, max_targets)
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


def _selected_args(views: list[str], planned: dict[str, Any], retried: dict[str, Any],
                   planned_views: list[str]) -> dict[str, Any]:
    """조회할 보기의 조건 묶음 — 분해가 고른 보기는 분해 조건만, 재시도가 더한 보기는 재시도 조건.

    재시도 조건이 분해가 고른 보기의 조건을 덮지 않는다(V-2 — 재시도가 보기 표 예문 값으로 분해가
    비운 필수 조건을 채워 되묻기를 건너뛰었다). 보기 id 없는 평면 묶음(`FLAT_VIEW_ARGS_KEY`)도
    처리기까지 옮긴다(W1 검증 L-1) — 재시도 것은 분해가 고른 보기가 없을 때만 쓴다.
    """
    keep = {*views, FLAT_VIEW_ARGS_KEY}
    by_plan = set(planned_views)
    fresh = {k: v for k, v in retried.items() if k in views and k not in by_plan}
    if FLAT_VIEW_ARGS_KEY in retried and not by_plan & set(views):
        fresh[FLAT_VIEW_ARGS_KEY] = retried[FLAT_VIEW_ARGS_KEY]
    return {**{k: v for k, v in planned.items() if k in keep}, **fresh}


def display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """채팅 표·LLM 입력용 행 — 긴 문자열 셀만 `DISPLAY_CELL_MAX`자로 줄인다(전문은 CSV·결과 파일).

    줄일 셀이 없는 행은 같은 객체 그대로다(종전 바이트).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not any(isinstance(v, str) and len(v) > DISPLAY_CELL_MAX for v in row.values()):
            out.append(row)
            continue
        out.append({k: (f"{v[:DISPLAY_CELL_MAX]}…(총 {len(v):,}자 — 전체는 CSV)"
                        if isinstance(v, str) and len(v) > DISPLAY_CELL_MAX else v)
                    for k, v in row.items()})
    return out


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


def _missing_required(view: ViewSpec, args: dict[str, Any], raw: Any) -> list[str]:
    """검증 뒤에도 없는 필수 조건 — `이름(없음)` 또는 `이름='값'(형식 밖)`."""
    given = raw if isinstance(raw, dict) else {}
    out: list[str] = []
    for spec in view.args:
        if not spec.required or (spec.tool_arg or spec.name) in args:
            continue
        value = given.get(spec.name)
        out.append(f"{spec.name}(없음)" if value is None
                   else f"{spec.name}={str(value)[:40]!r}(형식 밖)")
    return out


def _required_text(view: ViewSpec, missing: list[str]) -> str:
    """필수 조건 되묻기 — 무엇이 필요한지와 묻는 예(보기 예문의 질문 부분)."""
    names = {a.name: a.label or a.name for a in view.args}
    wanted = ", ".join(names.get(m.split("(")[0].split("=")[0], m) for m in missing)
    example = view.examples[0].split(" → ")[0] if view.examples else ""
    return (f"{_view_label(view)}: 필요한 조건({wanted})이 없거나 해석하지 못해 조회하지 않았습니다"
            f"({', '.join(missing)}). 값을 넣어 다시 물어 주세요"
            + (f"(예: {example})." if example else "."))


# ── 앞 결과 행 참조 (plans/134 M-6 · 계약 §4.2) ──────────────────────────────────

#: 참조 종류별 문구 — (무엇이 없는가 · 세는 이름 · 행에 없는 칸 · 먼저 할 일)
_REF_TEXT: dict[str, tuple[str, str, str, str]] = {
    "profile_ref": ("프로파일을 볼 트랜잭션이", "트랜잭션", "프로파일 참조",
                    "느린 트랜잭션·WAS 이벤트(오류 기록) 목록을 먼저 조회한 뒤"
                    " 몇 번째인지 함께 물어 주세요"),
    "active_ref": ("상세를 볼 실행 중 요청이", "실행 중 요청", "실행 중 요청 참조",
                   "실행 중 서비스 목록을 먼저 조회한 뒤 몇 번째인지 함께 물어 주세요"),
    "guid": ("GUID가 있는 거래가", "거래", "GUID",
             "GUID 값을 직접 적어 주시거나 트랜잭션 목록을 먼저 조회한 뒤"
             " 몇 번째인지 함께 물어 주세요"),
}
#: GUID 추적의 행 시각 기준 창 키(게이트웨이 `apm_transaction_trace` 인자 — 계약 §2.3).
_AROUND_KEY = "around_ms"


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


#: 표 이름(되묻기 문구)에 싣는 task 질의 길이(자) — 보기 라벨이 없을 때만 쓴다.
_TABLE_LABEL_MAX = 40


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


@dataclass
class _RefPick:
    """참조 보기 1개의 선택 결과 — 도구 인자 · 행 hostname · 경과 문구 · 되묻기 문구."""

    args: dict[str, Any] = field(default_factory=dict)
    hostname: str | None = None
    note: str = ""
    unresolved: str = ""
    reference: dict[str, Any] = field(default_factory=dict)


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


def _numbered(candidates: list[dict[str, Any]]) -> str:
    """쓸 수 있는 후보 ≤`MAX_SELECTION_CANDIDATES`개 — 「1번 「…」 · 2번 「…」 외 N건」.

    번호는 표시 순서다(값 없는 행도 자리를 지킨다).
    """
    usable = [(i, e) for i, e in enumerate(candidates, 1) if e.get("value") is not None]
    shown = " · ".join(f"{i}번 「{ref_label(e)}」" for i, e in usable[:MAX_SELECTION_CANDIDATES])
    more = (f" 외 {len(usable) - MAX_SELECTION_CANDIDATES}건"
            if len(usable) > MAX_SELECTION_CANDIDATES else "")
    return shown + more


def _choose_text(candidates: list[dict[str, Any]]) -> str:
    """되묻기 꼬리 — 쓸 수 있는 후보 ≤`MAX_SELECTION_CANDIDATES`개(번호 = 표시 순서)."""
    shown = _numbered(candidates)
    return f" 몇 번째인지 함께 다시 물어 주세요: {shown}" if shown else ""


def _tables_text(candidates: list[dict[str, Any]], names: list[str]) -> str:
    """표가 여럿일 때의 되묻기 꼬리 — 표(이름)마다 표 안 번호로 후보 ≤3개(plans/134 R-1)."""
    parts: list[str] = []
    for table in dict.fromkeys(e.get("table") for e in candidates):
        name = names[table] if isinstance(table, int) and table < len(names) else ""
        shown = _numbered([e for e in candidates if e.get("table") == table])
        parts.append(f"「{name or f'표 {len(parts) + 1}'}」 {shown or '(고를 행 없음)'}")
    return " / ".join(parts)


def _trace_window(plan: WindowPlan, entry: dict[str, Any] | None) -> dict[str, Any]:
    """GUID 추적 창 — 사용자 기간 > 참조 행 시각(±5분 · 게이트웨이 기본) > 게이트웨이 기본 60분."""
    if plan.mode == "window":
        return dict(plan.args)
    when = ref_time_ms(entry) if entry else None
    return {_AROUND_KEY: when} if when is not None else {}


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
    now = now or datetime.now()
    label = get_registry().system_label(APM_SYSTEM)
    by_id = {v.id: v for v in apm_views()}
    max_targets = _int_setting(app_config, "max_targets", 10)
    meta: dict[str, Any] = {"views": sanitize_views(task.get("views")), "hostnames": [],
                            "inserted_steps": [], "provenance": [], "failures": [], "notes": []}
    endpoint = apm_endpoint(app_config)
    if endpoint is None:
        meta["source_status"] = _status(label, "unavailable", 0, "엔드포인트 미설정")
        return _refusal(f"{label}이(가) 연결되어 있지 않아 조회하지 않았습니다.",
                        "source_unavailable", meta)

    selection = await _select_views(task, isolated, llm, max_targets, by_id)
    meta["selection"] = selection.info
    source = f"task:{task.get('task_id')}" if task.get("task_id") else "task"
    if selection.unresolved:
        meta["views"] = []
        meta["source_status"] = _status(label, "not_queried", 0, "요청 정보에 맞는 보기 미선택")
        notice = disc.make(disc.APM_UNRESOLVED_CONDITION, selection.unresolved, source=source)
        return _refusal(selection.unresolved, "apm_unresolved_selection", meta, [notice])
    views = selection.views
    meta["views"] = views
    task = {**task, "view_args": selection.view_args}
    notices: list[disc.Disclosure] = []
    if selection.uncovered:  # 덮은 보기는 조회하고 못 덮은 영역만 되묻는다(SPEC §6.4 ②)
        notices.append(disc.make(disc.APM_UNRESOLVED_CONDITION, selection.uncovered,
                                 source=source))
    ledger: list[LinkEntry] = []
    linked: dict[str, list[str]] = {}

    async def hosts_of(refs: list[TargetRef]) -> list[str]:
        """대상 → hostname(패싯 변환 — plans/125 E-3: hostname 없는 대상은 간선 표 경로 E2 등록명 →
        hostname). 같은 대상 묶음은 한 번만 변환한다(장부·삽입 단계 중복 없음)."""
        key = json.dumps([r.model_dump() for r in refs], sort_keys=True, default=str)
        if key not in linked:
            found, entries, steps = await link_hostnames(
                refs, consumer=APM_SYSTEM, app_config=app_config)
            linked[key] = found
            ledger.extend(entries)
            meta["inserted_steps"] += steps
        return list(linked[key])

    kinds = {by_id[v].target for v in views}
    # 종전 보기(대상 표현 없음)는 종전 규칙 그대로 — 선행 결과 → 이번 턴 식별자 → 직전 대상
    hostnames = await hosts_of(resolve_apm_targets(isolated, max_targets)) if "" in kinds else []
    scoped_hosts = (await hosts_of(scoped_targets(isolated, max_targets))
                    if "optional" in kinds else [])
    explicit_hosts = (await hosts_of(explicit_targets(isolated, max_targets))
                      if any(by_id[v].reference == "guid" for v in views) else [])

    parsed = isolated.get("parsed_requirements") or {}
    windows = {vid: plan_window(by_id[vid], parsed.get("time_range"), now) for vid in views}
    for vid, plan in windows.items():
        if plan.note:
            meta["notes"].append(f"{vid}: {plan.note}")
        if plan.kind:
            notices.append(disc.make(plan.kind, f"{_view_label(by_id[vid])}: {plan.note}",
                                     source=source))
    blocked: dict[str, str] = {}
    deferred: dict[str, list[str]] = {}
    bad_refs: dict[str, Any] = {}
    view_args = _plan_view_args(task, views, by_id, parsed, meta, notices, source,
                                composite=bool(isolated.get("is_composite")), blocked=blocked,
                                deferred=deferred, bad_refs=bad_refs)
    picks = {vid: _resolve_reference(by_id[vid], dict(view_args.get(vid) or {}), task, isolated,
                                     windows[vid], bad_refs.get(vid))
             for vid in views if by_id[vid].target == "reference" and vid not in blocked
             and windows[vid].mode != "out"}
    for vid in views:
        if by_id[vid].target != "reference":
            continue
        pick, dropped = picks.get(vid), deferred.get(vid, [])
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
    if blocked and all(v in blocked or windows[v].mode == "out" for v in views):
        # 되묻기만 남았다 — 게이트웨이를 열지 않는다(되묻기가 게이트웨이 가용성에 좌우되지 않게)
        meta["failures"] += [{"view": v, "hostname": None, "reason": windows[v].note}
                             for v in views if windows[v].mode == "out"]
        meta["hostnames"] = hostnames
        return _blocked_refusal(label, blocked, meta, disc.dedupe(notices))
    # 대상 텍스트(plans/130 M-2) — 대상으로 좁히는 보기가 있을 때만 해석한다. 없으면 종전과 같다.
    live = [v for v in views if v not in blocked and windows[v].mode != "out"]
    texts = (_target_texts(task, isolated, ledger, [*hostnames, *scoped_hosts], max_targets)
             if any(_uses_target(by_id[v], view_args.get(v)) for v in live) else [])
    resolved: list[_TargetText] = []
    instances: list[dict[str, Any]] = []
    answers: list[_Call] = []
    thread_id = isolated.get("thread_id")
    concurrency = _int_setting(app_config, "fanout_concurrency", 3)
    timeout = getattr(getattr(app_config, "dbhub", None), "source_call_timeout", 10.0)
    call_timeout = float(timeout) if isinstance(timeout, (int, float)) else 10.0
    scope = _JobScope(
        sub=isolated.get("user_id"), owner=jobs.gateway_owner(isolated.get("user_id")),
        thread_id=thread_id, call_timeout=call_timeout,
        poll_until=jobs.poll_deadline(app_config), app_config=app_config,
    )

    url, token = endpoint
    try:
        async with open_source_session(url, token, call_timeout=call_timeout, label=label,
                                       session_factory=_SESSION_FACTORY) as session:
            if texts:
                domain = (view_args.get(INSTANCES_VIEW) or {}).get("domain_id")
                resolved = await _resolve_target_texts(
                    session, texts, by_id.get(INSTANCES_VIEW), domain, scope, concurrency,
                    isolated, app_config, ledger, meta)
                instances = _merged_instances(
                    resolved, {h.casefold() for h in [*hostnames, *scoped_hosts]})
                notices += _target_notices(resolved, source)
                meta["targets"] = _targets_meta(resolved)
            needs_host = [v for v in views if by_id[v].required_input
                          and windows[v].mode != "out" and v not in blocked]
            # 대상 텍스트가 있었으면(해석 0건 포함) 첫 홉으로 가지 않는다(D-290 ⑥ — 말한 대상과
            # 무관한 인스턴스를 조회하지 않는다)
            if needs_host and not hostnames and not texts:
                hostnames, step = await _insert_instances_step(
                    session, max_targets, scope, by_id.get(INSTANCES_VIEW))
                meta["inserted_steps"].append(step)
                if step.get("error"):
                    # 대상 선정이 실패한 사유를 결과에 싣는다 — 없으면 "조회 대상이 없습니다"로 가려진다
                    meta["failures"].append(
                        {"view": INSTANCES_VIEW, "hostname": None, "reason": step["error"]})
            calls: list[_Call] = []
            for vid in views:
                view, plan = by_id[vid], windows[vid]
                if plan.mode == "out":
                    meta["failures"].append({"view": vid, "hostname": None, "reason": plan.note})
                    continue
                if vid in blocked:  # 필수 조건·참조 미해결 — 되묻기(고지는 위에서 실었다)
                    continue
                args = view_args.get(vid)
                if view.target == "reference":
                    # 앞 결과 행 참조 — 참조 칸·창은 고른 행에서(창 인자는 `_trace_window`).
                    # GUID 추적만 이번 턴 사용자가 말한 서버로 좁힌다(없으면 허용된 전
                    # 소스·도메인).
                    pick = picks[vid]
                    ref_hosts: list[str | None] = (
                        list(explicit_hosts) if view.reference == "guid" else [pick.hostname])
                    calls += [_Call(view, h, _call_args(view, h, WindowPlan("current"), scope,
                                                        pick.args), reference=pick.reference)
                              for h in ref_hosts or [None]]
                    continue
                if view.target == "optional":
                    # 선택 대상 — 대상이 있으면 대상별 · 없으면 hostname 없이 1회(첫 홉 삽입 없음).
                    # 소스 범위 kind(예 색상 경계)는 대상과 무관하게 1회(`targeted_choices`).
                    opt_hosts: list[str | None] = (
                        list(scoped_hosts) if _targeted(view, args) else [])
                    if texts and _targeted(view, args):
                        # 대상 텍스트 — 대상별 + 해석 인스턴스별(대상 없이 1회로 넓히지 않는다)
                        calls += [_Call(view, h, _call_args(view, h, plan, scope, args))
                                  for h in opt_hosts]
                        calls += _instance_calls(view, plan, scope, args, instances)
                        continue
                    calls += [_Call(view, h, _call_args(view, h, plan, scope, args))
                              for h in opt_hosts or [None]]
                    continue
                # 대상별 호출: 대상이 필요한 보기 · 대상으로 좁힐 수 있는 첫 홉 목록(인스턴스).
                # 대상이 없는 전체 보기(예 지표 목록)는 대상과 무관하게 한 번만 부른다
                # (plans/134 W2).
                if view.required_input or (view.first_hop and hostnames):
                    calls += [_Call(view, h, _call_args(view, h, plan, scope, args))
                              for h in hostnames]
                elif not (texts and view.first_hop):
                    calls.append(_Call(view, None, _call_args(view, None, plan, scope, args)))
                if texts and view.required_input:  # 해석 인스턴스마다 1호출(상한 없음 · D-296 ④)
                    calls += _instance_calls(view, plan, scope, args, instances)
                elif texts and view.first_hop and instances:  # 목록 질문 — 해석 행이 답이다
                    answers.append(_answer_call(
                        view, instances, [r.text for r in resolved if r.instances]))
            await _run_calls(session, calls, concurrency, scope)
            await _settle_jobs(session, calls, scope)
            calls += answers  # 게이트웨이에 다시 묻지 않는다(해석 검색이 이미 부른 행)
    except SourceMcpError as e:
        meta["source_status"] = _status(label, "unavailable", 0, str(e))
        return _refusal(f"{label}에 연결하지 못해 조회하지 않았습니다({e}). 다른 소스의 값으로 대신"
                        " 답하지 않았습니다.", "source_unavailable", meta, notices)

    # 조회한 대상 — 종전 보기의 대상(첫 홉 포함) 뒤에 선택 대상·참조 보기가 부른 서버를 잇는다
    meta["hostnames"] = list(dict.fromkeys(
        [*hostnames, *(c.hostname for c in calls if c.hostname)]))
    rows = _collect(calls, meta)
    unresolved = [r for r in resolved if not r.instances]
    # 대상 텍스트 해석 0건은 조회하지 못한 대상으로 센다(부분 결과 · 감사 degraded)
    meta["failures"] += [{"view": None, "hostname": None, "target": r.text,
                          "reason": f"대상 '{r.text}' 해석 0건"} for r in unresolved]
    for limit in (x for r in resolved for x in r.limits):
        if limit not in meta["limits"]:
            meta["limits"].append(limit)
    disclosures = disc.dedupe(
        [*notices, *_job_disclosures(calls, meta, str(task.get("task_id") or ""))])
    accepted = [c for c in calls if c.accepted]
    ledger += _apm_hop_ledger(calls)
    meta["link_ledger"] = [entry.as_dict() for entry in ledger]
    meta["link_summary"] = ledger_summary(ledger)
    line = ledger_line(ledger)
    if line:
        meta["notes"].insert(0, line)
    ok_calls = [c for c in calls if c.error is None]
    if calls and not ok_calls:
        meta["source_status"] = _status(label, "unavailable", 0,
                                        "; ".join(f["reason"] for f in meta["failures"][:3]))
        return _refusal(f"{label} 조회가 모두 실패했습니다 — " + meta["source_status"]["reason"],
                        "apm_calls_failed", meta, disclosures)
    if not calls and blocked:
        return _blocked_refusal(label, blocked, meta, disclosures)
    if not calls and unresolved and len(unresolved) == len(resolved):
        # 말한 대상을 하나도 풀지 못했다 — 「찾지 못함」과 후보가 답이다(D-290 ⑥ · 대신 조회 없음)
        meta["source_status"] = _status(label, "not_queried", 0, "대상 텍스트 해석 0건")
        text = "\n".join(d["text"] for d in disclosures if d["kind"] in (
            disc.APM_UNRESOLVED_CONDITION, disc.APM_PARTIAL_SOURCES))
        return _refusal(text, "apm_target_unresolved", meta, disclosures)
    if not calls:
        reason = "; ".join(f["reason"] for f in meta["failures"][:3]) or "조회 대상이 없습니다"
        meta["source_status"] = _status(label, "not_queried", 0, reason)
        return _refusal(f"{label}을(를) 조회하지 않았습니다 — {reason}", "apm_not_queried", meta,
                        disclosures)
    if accepted and not rows:
        return _accepted_answer(label, accepted, meta, disclosures)
    # 게이트웨이 봉투 partial(일부 소스·도메인·조각 실패)도 완료로 세지 않는다
    partial_calls = [c for c in calls if c.error is None and not c.accepted
                     and (c.envelope or {}).get("partial")]
    status = ("partial" if meta["failures"] or accepted or partial_calls
              else ("ok" if rows else "empty"))
    reasons = [f["reason"] for f in meta["failures"][:3]]
    if accepted:
        reasons.append(f"작업 접수 {len(accepted)}건(진행 중)")
    if partial_calls:
        reasons.append(f"일부 소스·구간 조회 실패 {len(partial_calls)}건(부분 결과)")
    meta["source_status"] = _status(label, status, len(rows), "; ".join(reasons))
    extra: dict[str, Any] = {}
    if disclosures:
        extra["disclosures"] = disclosures
    if accepted:
        extra["accepted_jobs"] = [c.job["job_id"] for c in accepted if c.job]
    lines = _answer_lines(meta)
    if lines:
        extra[ANSWER_LINES_KEY] = lines
    shown = display_rows(rows)
    if any(a is not b for a, b in zip(shown, rows)):
        extra[DISPLAY_CUT_KEY] = True
    return {
        **extra,
        "organized_data": {
            "summary": _summary(label, views, by_id, meta, len(rows)),
            # 채팅 표·LLM 입력은 긴 셀을 줄여 싣는다 — 전문은 CSV(`query_results`)·결과 파일
            "rows": shown,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        },
        "query_results": rows,
        META_KEY: meta,
        "source_status": [meta["source_status"]],
    }


async def _insert_instances_step(
    session: Any, max_targets: int, scope: _JobScope, view: ViewSpec | None,
) -> tuple[list[str], dict[str, Any]]:
    """대상 미지정 — 인스턴스 목록 보기를 먼저 불러 hostname 을 고른다(첫 홉 삽입 · LLM 0).

    목록 조회가 작업으로 승격되면 조회 마감까지 기다리고, 그래도 끝나지 않으면 그 작업을 취소하고
    사유를 남긴다 — 사용자가 맡긴 조회가 아니라 대상 선정 단계라 장부에 올리지 않는다.
    """
    step: dict[str, Any] = {
        "view": INSTANCES_VIEW, "reason": "대상 서버 미지정 — 인스턴스 목록으로 선정",
    }
    if view is None:
        step["error"] = "인스턴스 목록 보기가 레지스트리에 없다"
        return [], step
    call = _Call(view, None, {"thread_id": str(scope.thread_id) if scope.thread_id else None,
                              "owner": scope.owner})
    await _run_calls(session, [call], 1, scope)
    if call.error is None and jobs.is_live(call.envelope):
        await _await_first_hop(session, call, scope)
    if call.error:
        step["error"] = call.error
        return [], step
    hosts = list(dict.fromkeys(
        str(r.get("hostname")).strip() for r in (call.envelope or {}).get("rows") or []
        if isinstance(r, dict) and r.get("hostname") and r.get("match_confidence")
    ))
    step["hosts"] = len(hosts)
    if len(hosts) > max_targets:
        step["truncated"] = len(hosts) - max_targets
        hosts = hosts[:max_targets]
    return hosts, step


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


def _short_label(system: str) -> str:
    """시스템 표시 이름의 짧은 표기(괄호 설명 앞) — 고지 문구용."""
    label = get_registry().system_label(system)
    return label.split("(")[0].strip() or label


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
        logger.warning("%s 대상 해석 조회 실패: %s", APM_QUERY_AGENT, type(e).__name__)
        for call in calls:  # 답이 없거나 아직 작업 핸들뿐인 호출은 실패다(행 0건으로 읽지 않는다)
            if call.error is None and (call.envelope is None or jobs.is_live(call.envelope)):
                call.error = f"{type(e).__name__}: {str(e)[:120]}"


def _search_args(scope: _JobScope, domain_id: Any, **target: Any) -> dict[str, Any]:
    args: dict[str, Any] = {**target,
                            "thread_id": str(scope.thread_id) if scope.thread_id else None,
                            "owner": scope.owner}
    if domain_id is not None:
        args["domain_id"] = domain_id
    return args


async def _gateway_search(session: Any, arg: str, text: str, view: ViewSpec, domain_id: Any,
                          scope: _JobScope, apm: str) -> _Found:
    """게이트웨이 검색 1회 — `query`(인스턴스 이름·설명) 또는 `business`(업무명)."""
    found = _Found(f"{apm} 인스턴스 이름·설명" if arg == "query" else f"{apm} 업무명")
    call = _Call(view, None, _search_args(scope, domain_id, **{arg: text}))
    await _lookup(session, [call], 1, scope)
    if call.error:
        found.errors.append(f"{found.searched} 검색 실패 — {call.error[:160]}")
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
                found.errors.append(f"{call.hostname} 인스턴스 정합 실패 — {call.error[:120]}")
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

    id를 받지 않는 도구는 같은 소스의 같은 이름을 한 번만 부른다(중복 호출 없음).
    """
    by_id = view.tool in INSTANCE_ID_TOOLS
    seen: set[tuple[Any, ...]] = set()
    out: list[_Call] = []
    for inst in instances:
        key: tuple[Any, ...] = ((inst["source_id"], inst.get("domain_id"), inst["instance_id"])
                                if by_id else
                                (inst["source_id"], str(inst["instance_name"]).casefold()))
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


def _target_notices(resolved: list[_TargetText], source: str) -> list[disc.Disclosure]:
    """대상 텍스트 고지(plans/130 M-6 · 123 `disclosures[]`).

    - 해석: 「'텍스트' → APM 인스턴스 N개(근거: …)」 — 값 기반 키 연결 보고(`bridge` · 등급 중립).
    - 찾지 못함: 검색한 근거 + 유사 이름 후보(≤3 · 자동 채택 안 함) — `apm_unresolved_condition`.
    - 근거 조회 실패(다른 근거로 계속): `apm_partial_sources`.
    """
    apm = _short_label(APM_SYSTEM)
    out: list[disc.Disclosure] = []
    for res in resolved:
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
    minutes = call.args.get("lookback_minutes")
    if minutes:
        reference = call.args.get("reference_time")
        parts.append(f"{minutes}분 구간" + (f"(기준 {reference})" if reference else ""))
    return " · ".join(parts)


def _progress_text(handle: dict[str, Any]) -> str:
    """예상 시간 · 진행 — 게이트웨이 `estimate`·`progress` 그대로(모르면 모른다고 쓴다)."""
    raw_estimate, raw_progress = handle.get("estimate"), handle.get("progress")
    estimate: dict[str, Any] = raw_estimate if isinstance(raw_estimate, dict) else {}
    progress: dict[str, Any] = raw_progress if isinstance(raw_progress, dict) else {}
    seconds, api_calls = estimate.get("seconds"), estimate.get("api_calls")
    if isinstance(seconds, (int, float)) and seconds > 0:
        eta = (f"예상 약 {round(seconds / 60, 1):g}분" if seconds >= 120
               else f"예상 약 {seconds:g}초")
        if api_calls:
            eta += f"(API 호출 {api_calls}회)"
    else:
        eta = "예상 시간 미정"
    done, total = progress.get("done"), progress.get("total")
    unit = progress.get("label") or "API 호출"
    if isinstance(done, int):
        if isinstance(total, int) and total:
            return f"{eta} · 진행 {done}/{total} {unit}"
        return f"{eta} · 진행 {unit} {done}회"
    return eta


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


async def _await_job(session: Any, call: _Call, scope: _JobScope, store: Any) -> None:
    """작업 1개를 조회 마감까지 기다린다 — 끝나면 미리보기 봉투로 바꾸고, 아니면 접수로 둔다."""
    if call.job is None:
        return
    job_id = str(call.job["job_id"])
    status = await jobs.poll_job(session, job_id, scope.owner, until=scope.poll_until)
    if status is None:
        call.accepted = True
        return
    if status.get("error"):
        call.error = _envelope_error(status)
        return
    if store is not None:
        try:
            await store.update(job_id, jobs.ledger_fields(status))
        except Exception as e:  # noqa: BLE001 — 상태 캐시 갱신 실패가 답을 막지 않는다
            logger.warning("APM 작업 장부 갱신 실패(job_id=%s): %s", job_id[:8], e)
    call.job = jobs.job_handle(status) or call.job
    state = call.job.get("state")
    if state in jobs.LIVE_STATES:
        call.accepted = True
    elif state in jobs.READABLE_STATES:
        call.envelope = jobs.result_envelope(status)
    else:
        raw_error = call.job.get("error")
        err: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        call.error = f"{err.get('code') or state}: {str(err.get('reason') or '')[:160]}"


async def _await_first_hop(session: Any, call: _Call, scope: _JobScope) -> None:
    """대상 선정용 인스턴스 목록이 작업으로 승격됐다 — 마감까지 기다리고, 못 끝나면 취소한다."""
    handle = jobs.job_handle(call.envelope) or {}
    job_id = str(handle.get("job_id"))
    status = await jobs.poll_job(session, job_id, scope.owner, until=scope.poll_until)
    if status is not None and status.get("error"):
        call.error = _envelope_error(status)
        return
    state = jobs.job_state(status)
    if status is not None and state in jobs.READABLE_STATES:
        call.envelope = jobs.result_envelope(status)
        return
    if status is not None and state not in jobs.LIVE_STATES:
        err = (jobs.job_handle(status) or {}).get("error") or {}
        call.error = f"{err.get('code') or state}: {str(err.get('reason') or '')[:160]}"
        return
    try:
        await session.call_tool(jobs.CANCEL_TOOL, {"job_id": job_id, "owner": scope.owner})
    except SourceMcpError as e:
        logger.warning("인스턴스 목록 작업 취소 실패(job_id=%s): %s", job_id[:8], e)
    call.error = ("인스턴스 목록 조회가 처리 시간 안에 끝나지 않아 대상 서버를 고르지 못했습니다"
                  "(목록 작업은 취소했습니다)")


def _partial_text(call: _Call, envelope: dict[str, Any]) -> str:
    failed = [str(x).removeprefix("[한계]").strip() for x in envelope.get("limits") or []
              if isinstance(x, str) and ("실패" in x or "불가" in x)]
    tail = f" — {failed[0][:120]}" if failed else ""
    return f"{_scope_text(call)}: 일부 소스·도메인·구간 조회가 실패해 부분 결과입니다{tail}"


def _job_disclosures(calls: list[_Call], meta: dict[str, Any],
                     task_id: str) -> list[disc.Disclosure]:
    """작업 접수 · 결과 파일 · 부분 결과 고지(SPEC §7.5) — 작업 참조는 `ref`로 싣는다."""
    source = f"task:{task_id}" if task_id else "task"
    out: list[disc.Disclosure] = []
    handles: list[dict[str, Any]] = []
    for call in calls:
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
                " 카드의 「전체 결과 받기」로 내려받으세요.",
                source=source, ref=ref))
            meta["notes"].append(f"{call.view.id}: 결과가 커서 앞 {shown}행만 실었습니다"
                                 f"(전체 {total}행 — 결과 파일)")
        if envelope.get("partial"):
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


def _gateway_disclosures(envelope: dict[str, Any], source: str) -> list[disc.Disclosure]:
    """게이트웨이 봉투 고지(예 `apm_masked_fields`) — 고지 표에 등록된 kind만 싣는다.

    W1 검증 L-5.
    """
    out: list[disc.Disclosure] = []
    for item in envelope.get("disclosures") or []:
        kind = item.get("kind") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if kind in disc.KIND_TABLE and isinstance(text, str) and text.strip():
            out.append(disc.make(str(kind), text, source=source))
        else:
            logger.info("%s: 등록되지 않았거나 문구가 없는 게이트웨이 고지는 싣지 않음: %r",
                        APM_QUERY_AGENT, str(kind)[:40])
    return out


def _accepted_answer(label: str, accepted: list[_Call], meta: dict[str, Any],
                     disclosures: list[disc.Disclosure]) -> dict[str, Any]:
    """접수 답 — 데이터 답이 아니다(`source_status.status = "accepted"` · 하네스 미완료)."""
    lines = [d["text"] for d in disclosures if d["kind"] == disc.APM_JOB_ACCEPTED]
    body = [f"{label} 조회가 오래 걸려 작업으로 접수했습니다 — 아직 조회 결과가 아닙니다.",
            *(f"- {line}" for line in lines)]
    failed = [f for f in meta["failures"] if f.get("hostname")]
    if failed:
        shown = ", ".join(f"{f['hostname']}({f['reason'][:60]})" for f in failed[:3])
        body.append(f"조회하지 못한 대상 {len(failed)}건: {shown}")
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


_LEVEL_RANK = {"CRITICAL": 2, "WARNING": 1}
_CATEGORY_RANK = {"strong": 2, "medium": 1}


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 `(kind, source_id, instance_id)`는 가장 강한 판정 1건만 남긴다.

    게이트웨이 계약(SPEC-apm-gateway §4)과 같은 규칙이다 — 여러 대상·보기의 판정을 모은 뒤
    다시 건다.
    """
    best: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for sig in signals:
        key = (sig.get("kind"), sig.get("source_id"), sig.get("instance_id"))
        rank = (_LEVEL_RANK.get(str(sig.get("level")), 0),
                _CATEGORY_RANK.get(str(sig.get("category")), 0))
        cur = best.get(key)
        if cur is None or rank > (_LEVEL_RANK.get(str(cur.get("level")), 0),
                                  _CATEGORY_RANK.get(str(cur.get("category")), 0)):
            best[key] = sig
    return list(best.values())


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
    if isinstance(summary, dict) and summary.get("guid"):
        # GUID 추적의 기본 창 고지(V-3) — 게이트웨이 `[한계]` 문구 그대로 결정적 줄에 옮긴다
        agg["window_notes"] = [
            x.removeprefix("[한계]").strip() for x in env.get("limits") or []
            if isinstance(x, str) and _TRACE_DEFAULT_WINDOW_MARK in x]
    return agg


#: 게이트웨이 GUID 추적이 기간 없이 기본 창(앞 결과 시각 ±N분 · 최근 N분)을 쓸 때 `[한계]`에 싣는
#: 표지(SPEC-apm-gateway trace 창 규칙 · 계약 §4.2 「기본값이 쓰이면 고지가 답에 실린다」).
_TRACE_DEFAULT_WINDOW_MARK = "기간 미지정"


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
                 *(("instance_name", "instance_id", "source_ids") if call.instance else ()),
                 "targets"}
        used = {k: v for k, v in call.args.items() if k in names and v is not None}
        if used:
            entry["args"] = used
        if call.reference:  # 앞 결과 행 참조(plans/134 M-6) — 어느 결과의 몇 번째 행인가
            entry["reference"] = call.reference
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


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _stats_text(stats: dict[str, Any]) -> str:
    """창 집계 한 줄 — 호출 수 · 오류 · 오류율 · p50 · p95(있는 값만)."""
    parts: list[str] = []
    calls, errors, rate = (_num(stats.get(k)) for k in ("calls", "errors", "error_rate"))
    if calls is not None:
        parts.append(f"호출 {calls:,.0f}건")
    if errors is not None:
        parts.append(f"오류 {errors:,.0f}건")
    if rate is not None:
        parts.append(f"오류율 {rate:.1%}")
    for key, name in (("response_time_p50_ms", "p50"), ("response_time_p95_ms", "p95")):
        value = _num(stats.get(key))
        if value is not None:
            parts.append(f"{name} {value:,.0f}ms")
    return " · ".join(parts)


def _hourly_text(hourly: dict[str, Any]) -> str:
    parts: list[str] = []
    calls, failures, rate = (_num(hourly.get(k)) for k in ("calls", "failures", "failure_rate"))
    avg, peak = _num(hourly.get("response_time_avg_ms")), _num(hourly.get("max_response_time_ms"))
    if calls is not None:
        parts.append(f"호출 {calls:,.0f}건")
    if failures is not None:
        parts.append(f"실패 {failures:,.0f}건")
    if rate is not None:
        parts.append(f"실패율 {rate:.1%}")
    if avg is not None:
        parts.append(f"평균 {avg:,.0f}ms")
    if peak is not None:
        parts.append(f"최대 {peak:,.0f}ms")
    span = ""
    if hourly.get("hour_start") and hourly.get("hour_end"):
        span = f"({hourly['hour_start']}~{hourly['hour_end']})"
    return f"시 단위 합계{span} " + " · ".join(parts) if parts else ""


def _answer_lines(meta: dict[str, Any]) -> list[str]:
    """결정적 판정·집계 줄(plans/134 M-1) — LLM 산문에 맡기지 않고 최종 답에 그대로 싣는다.

    판정(label·level·evidence) · 창 집계(호출 수·오류율·p50·p95) · 시 단위 합계 · 오류 유형별 건수 ·
    실행 중 서비스 수. 값은 게이트웨이가 계산한 그대로이고 상한 없이 모두 싣는다.
    """
    lines: list[str] = []
    for sig in meta.get("was_signals") or []:
        where = " ".join(str(x) for x in (sig.get("hostname"), (
            f"#{sig['instance_id']}" if sig.get("instance_id") is not None else None)) if x)
        head = f"판정 {sig.get('label') or sig.get('kind')}({sig.get('level')})"
        evidence = f": {sig['evidence']}" if sig.get("evidence") else ""
        lines.append(f"{head} — {where}{evidence}" if where else f"{head}{evidence}")
    for agg in meta.get("aggregates") or []:
        scope = agg.get("scope") or agg.get("view")
        for win in agg.get("row_windows") or []:
            text = _stats_text(win)
            name = win.get("instance_name") or win.get("instance_id")
            if text:
                lines.append(f"{scope}{f' · {name}' if name else ''}: 구간 {text}")
        summary = agg.get("summary") if isinstance(agg.get("summary"), dict) else {}
        if _num(summary.get("failures")) is not None:
            # 시 단위 통계 합계(`apm_status_stats` — 실패·평균·최대 · 정시 경계로 넓힌 구간)
            text = _hourly_text({**summary, "hour_start": agg.get("hour_start"),
                                 "hour_end": agg.get("hour_end")})
            rows = _num(summary.get("row_count"))
            if text:
                lines.append(f"{scope}: {text}"
                             + (f" — 받은 {rows:,.0f}행의 합계" if rows is not None else ""))
        else:
            text = _stats_text(summary)
            if text:
                lines.append(f"{scope}: 구간 {text}")
        if _num(summary.get("total")) is not None:
            modes = summary.get("by_running_mode") or {}
            detail = " · ".join(f"{k} {v}건" for k, v in modes.items()) if isinstance(
                modes, dict) else ""
            lines.append(f"{scope}: 실행 중 {summary['total']}건"
                         + (f"(실행 모드별 {detail})" if detail else ""))
        hourly = agg.get("hourly") if isinstance(agg.get("hourly"), dict) else {}
        text = _hourly_text(hourly)
        if text:
            lines.append(f"{scope}: {text}")
        errors = [e for e in agg.get("errors_by_type") or []
                  if isinstance(e, dict) and e.get("error_type")]
        if errors:
            lines.append(f"{scope}: 오류 유형별 — " + " · ".join(
                f"{e['error_type']} {e.get('count', 0):,}건" for e in errors))
        if summary.get("guid") and _num(summary.get("transactions")) is not None:
            lines.append(_trace_line(summary, agg.get("window"), agg.get("window_notes") or []))
        lines += [_change_line(row) for row in agg.get("changes") or []]
    return lines


def _clock_text(ms: Any) -> str:
    """epoch ms → 이 서버 시간대의 ISO 시각(오프셋 포함 — 원천 시간대를 추측하지 않는다)."""
    value = _num(ms)
    if value is None or value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000).astimezone().isoformat(timespec="seconds")


def _trace_line(summary: dict[str, Any], window: Any = None,
                window_notes: list[str] | tuple[str, ...] = ()) -> str:
    """GUID 연계 거래 한 줄(plans/134 W5 · 계약 §4.3) — 게이트웨이 `summary` 값 그대로.

    「도메인 M곳」을 거래가 나온 도메인 수로 쓰고 조회·실패 도메인 수를 괄호에 둔다(조회 도메인 수만
    쓰면 「거쳐 간 도메인」으로 읽힌다). 조회 구간(봉투 `window`)과 기간을 말하지 않아 쓴 기본 창
    고지(`window_notes` — 게이트웨이 문구), 「같은 GUID일 뿐 호출 관계가 아님」은 0건이어도 싣는다
    (V-3 — 0건 답은 LLM을 거치지 않아 요약 입력의 `[한계]`가 답에 닿지 않는다).
    """
    count = _num(summary.get("transactions")) or 0
    queried, failed = _num(summary.get("domains_queried")), _num(summary.get("domains_failed"))
    hits = _num(summary.get("domains_with_hits"))
    head = f"GUID {summary.get('guid')}: 거래 {count:,.0f}건"
    if hits is not None:
        inner = " · ".join(p for p in (
            f"조회 {queried:,.0f}곳" if queried is not None else "",
            f"실패 {failed:,.0f}곳" if failed is not None else "") if p)
        head += f" · 도메인 {hits:,.0f}곳" + (f"({inner})" if inner else "")
    elif queried is not None:
        head += f" · 도메인 {queried:,.0f}곳" + (f"(실패 {failed:,.0f})" if failed is not None
                                              else "")
    first = _clock_text(summary.get("first_start_ms"))
    last = _clock_text(summary.get("last_end_ms"))
    if first or last:
        head += f" · {first or '?'} ~ {last or '?'}"
    span = (f"조회 구간 {window['start']} ~ {window['end']}"
            if isinstance(window, dict) and window.get("start") and window.get("end") else "")
    notes = "; ".join(window_notes)
    if span or notes:
        head += " · " + (f"{span}({notes})" if span and notes else span or f"조회 구간({notes})")
    head += " · 같은 GUID일 뿐 호출 관계가 아님"
    names = [str(n) for n in summary.get("instances") or [] if n not in (None, "")]
    if names:
        head += " · 인스턴스 " + ", ".join(names)
    return head


def _change_line(row: dict[str, Any]) -> str:
    """변경 감지 1건의 전후 비교 한 줄(plans/134 W6 A-2 · 계약 §4.3) — 값이 없으면 N/A.

    차이(`delta`)는 게이트웨이가 계산한 값을 그대로 쓴다(오류율 차이 = %p · 평균 응답 = %).
    """
    def part_of(key: str) -> dict[str, Any]:
        value = row.get(key)
        return value if isinstance(value, dict) else {}

    before, after, delta = part_of("before"), part_of("after"), part_of("delta")

    def diff(key: str, part: str) -> float | None:
        item = delta.get(key)
        return _num(item.get(part)) if isinstance(item, dict) else None

    def rate(value: Any) -> str:
        num = _num(value)
        return f"{num:.1%}" if num is not None else "N/A"

    def millis(value: Any) -> str:
        num = _num(value)
        return f"{num:,.0f}ms" if num is not None else "N/A"

    def count(value: Any) -> str:
        num = _num(value)
        return f"{num:,.0f}" if num is not None else "N/A"

    point, pct = diff("error_rate", "abs"), diff("avg_response_ms", "pct")
    point_text = f"{point:+.1f}%p" if point is not None else "N/A"
    pct_text = f"{pct:+.1f}%" if pct is not None else "N/A"
    name = row.get("instance_name") or row.get("instance_id") or "인스턴스"
    when = row.get("change_detected_at")
    when = when if isinstance(when, str) and when else _clock_text(row.get("change_detected_ms"))
    return (f"{name} 변경 감지 {when} — "
            f"오류율 {rate(before.get('error_rate'))} → {rate(after.get('error_rate'))}"
            f"({point_text}) · "
            f"평균 응답 {millis(before.get('avg_response_ms'))} → "
            f"{millis(after.get('avg_response_ms'))}({pct_text})"
            f" · 오류 기록 {count(before.get('error_records'))} →"
            f" {count(after.get('error_records'))}"
            f" · 호출 {count(before.get('calls'))} → {count(after.get('calls'))}")


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
            continue
        if step.get("hosts") is not None:
            tail = (f"(상한으로 {step['truncated']}대 제외 — 조회한 범위 안의 결과입니다)."
                    if step.get("truncated") else ".")
            parts.append(f"대상 서버를 지정하지 않아 인스턴스 목록에서 {step['hosts']}대를"
                         f" 골랐습니다{tail}")
        elif step.get("error"):
            parts.append(f"인스턴스 목록 조회 실패: {step['error']}")
    parts += meta["notes"][:3]
    parts += meta.get("limits", [])[:3]
    failed = [f for f in meta["failures"] if f.get("hostname") or f.get("instance")]
    if failed:
        shown = ", ".join(f"{f['hostname'] or f['instance'].get('instance_name')}"
                          f"({f['reason'][:60]})" for f in failed[:3])
        parts.append(f"조회하지 못한 대상 {len(failed)}건: {shown}")
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
    required_inputs=(),  # 대상이 없으면 인스턴스 목록 보기를 코드가 먼저 부른다(첫 홉)
    output_type="rows",
    key_facets_out=("hostname", "apm_instance_id", "apm_domain_id"),
    self_filters=("host", "time"),
    # 대상 상한·동시 호출은 설정값(`composite.max_targets`·`fanout_concurrency`) — 계약 값은 M-2
    # 픽스처 지연·J0-O 운영 실측 뒤(plans/125 §4.6)
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
