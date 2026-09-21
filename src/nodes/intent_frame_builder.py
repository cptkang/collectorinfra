"""의도 프레임 빌더·재작성 감사·정규 질의 채널 (plans/107 W1·W2·W3·W5).

도메인 모델(``src.domain.intent_frame``)은 순수하다 — 이 모듈이 상태·레지스트리를 plain 값으로
바꿔 넣고, 렌더(``src.prompts.canonical_query``)와 감사(``src.security.audit_logger``)를 잇는다.

**D-225 ⑦** — 여기 함수들은 SQL 생성 소비자(``query_generator``·``multi_db_executor``)가 부르고,
그 두 노드는 사다리 1·2·3단이 모두 지난다. 단별 차이는 호출 지점뿐이다(1단 전용 판정 없음).

**꺼져 있으면 아무것도 하지 않는다** — ``INTENT_FRAME_ENABLED=false``면 ``observe_rewrite``는 빈
dict, ``get_prompt_query``는 받은 텍스트를 그대로 돌려준다(현행과 비트 동일).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from src.config import AppConfig
from src.domain.intent_frame import (
    IntentFrame,
    merge_frame,
    rewrite_needed,
    verify_rewrite,
)
from src.prompts.canonical_query import RENDERER_VERSION, render_canonical_block
from src.utils.query_gen_common import HOST_IDENTIFIER_FIELDS, refers_to_demonstrative_server

logger = logging.getLogger(__name__)

# 병기 소비자 이름(CANONICAL_QUERY_CONSUMERS 값) — §4.4 전환 순서.
CONSUMER_OUTPUT_GENERATOR = "output_generator"
CONSUMER_GENERAL_INFERENCE = "general_inference"
CONSUMER_QUERY_GENERATOR = "query_generator"
KNOWN_CONSUMERS: frozenset[str] = frozenset({
    CONSUMER_OUTPUT_GENERATOR, CONSUMER_GENERAL_INFERENCE, CONSUMER_QUERY_GENERATOR,
})
# 감사 기록 지점(병기 소비자가 아니다) — 멀티 DB 경로의 SQL 생성 노드.
CONSUMER_MULTI_DB = "multi_db_executor"

#: 대상 DB 출처(`db_scope_source`) 중 발화 밖에서 온 것. 나머지(hint·classified·미기재)는
#: 라우터가 이번 발화에서 고른 DB라 발화 출처다 — 라우팅 결과를 "파생"으로 세면 모든 질의가
#: 게이트를 통과하지 못한다.
_INHERITED_DB_SCOPE = "inherited"
_PLANNED_DB_SCOPE = "planned"


# ──────────────────────────────────────────────
# 프레임 구축 (W1)
# ──────────────────────────────────────────────

def raw_query_of(state: Mapping[str, Any]) -> str:
    """병기 블록 원문 줄·프레임 원문에 쓸 문자열.

    우선순위: ``display_query``(존 표기 치환본 — 미선택 존 위치어를 다시 싣지 않는다, D-154)
    → ``raw_user_query`` → ``original_user_query``(오케스트레이션 격리 입력) → ``user_query``.
    """
    for key in ("display_query", "raw_user_query", "original_user_query", "user_query"):
        value = state.get(key)
        if value:
            return str(value)
    return ""


def _host_values(filters: list[Any]) -> list[str]:
    hosts: list[str] = []
    for item in filters or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("field", "")).lower() not in {f.lower() for f in HOST_IDENTIFIER_FIELDS}:
            continue
        value = item.get("value")
        for v in value if isinstance(value, (list, tuple)) else [value]:
            if v is not None and str(v).strip():
                hosts.append(str(v).strip())
    return hosts


def _prior_entities(state: Mapping[str, Any]) -> list[str]:
    ctx = state.get("conversation_context") or {}
    out: list[str] = []
    for entity in ctx.get("previous_entities") or []:
        if isinstance(entity, dict) and entity.get("value"):
            out.append(str(entity["value"]))
    return out


def build_intent_frame(state: Mapping[str, Any]) -> IntentFrame:
    """상태에서 프레임을 만든다 — 파서·라우터 구조화 산출과 레지스트리만 쓴다(D-004).

    - 서버 식별자는 원문에 **나타난 것만** 발화 출처다. 원문에 없는데 직전 엔티티와 같은 값은
      맥락 파싱(R5)이 섞어 넣은 것이라 승계 후보로 돌리고, 지시 참조일 때만 병합된다(규칙 3′).
    - 대상 DB 출처는 ``db_scope_source``(구조화 키)를 따른다. 존 선택 답변이 있으면 답변이 이긴다.
    - 사용자가 말하지 않은 시스템 기본값(LIMIT 상향 등)은 슬롯으로 만들지 않는다 — 기본값 정책
      (106 H1.5)이 생기기 전까지 ``default`` 출처는 비어 있다.
    """
    raw = raw_query_of(state)
    parsed = state.get("parsed_requirements") or {}
    filters = list(parsed.get("filter_conditions") or [])

    utterance: dict[str, Any] = {}
    answers: dict[str, Any] = {}
    inherited: dict[str, Any] = {}
    registry: dict[str, Any] = {}

    raw_norm = "".join(raw.split()).lower()
    prior = set(_prior_entities(state))
    for host in _host_values(filters):
        in_text = "".join(host.split()).lower() in raw_norm
        bucket = inherited if (not in_text and host in prior) else utterance
        bucket.setdefault("targets.hosts", []).append(host)
    # 지시 참조로 직전 엔티티를 가리켰는데 파서가 식별자를 싣지 않은 경우 — 승계 후보로 둔다.
    if prior and "targets.hosts" not in utterance and "targets.hosts" not in inherited:
        inherited["targets.hosts"] = sorted(prior)

    selected = state.get("selected_db_ids") or []
    db_ids = [
        t["db_id"] for t in state.get("target_databases") or []
        if isinstance(t, dict) and t.get("db_id")
    ]
    scope_source = state.get("db_scope_source")
    if selected or scope_source == "selected":
        answers["targets.db_ids"] = list(selected or db_ids)
    elif db_ids and scope_source == _INHERITED_DB_SCOPE:
        inherited["targets.db_ids"] = db_ids
    elif db_ids and scope_source == _PLANNED_DB_SCOPE:
        registry["targets.db_ids"] = db_ids
    elif db_ids:
        utterance["targets.db_ids"] = db_ids

    if parsed.get("query_targets"):
        utterance["metrics"] = list(parsed["query_targets"])
    for slot in ("time_range", "aggregation", "limit"):
        if parsed.get(slot) not in (None, "", [], {}):
            utterance[slot] = parsed[slot]
    output = parsed.get("output_format")
    if output and output != "text":
        utterance["output"] = output

    return merge_frame(
        original_query=raw,
        intent=str(state.get("routing_intent") or ""),
        utterance=utterance,
        answers=answers,
        inherited=inherited,
        demonstrative=refers_to_demonstrative_server(raw),
        registry=registry,
        filters=filters,
    )


def foreign_location_terms(db_ids: list[str]) -> list[str]:
    """대상 DB **밖**을 배타적으로 가리키는 위치 표면어(레지스트리 정본에서 해소).

    대상 DB 쪽 표면어와 겹치는 어휘는 뺀다(겹치면 대상 안의 정상 언급이다).
    """
    if not db_ids:
        return []
    from src.routing.registry import get_registry

    try:
        hints = get_registry().location_db_hints()
    except Exception as exc:  # 레지스트리 부재는 검증을 건너뛸 사유이지 실패가 아니다
        logger.warning("위치 표면어 해소 실패 — 위치 누출 검사를 건너뛴다: %s", exc)
        return []
    own = {t for d in db_ids for t in hints.get(d, ())}
    return sorted({t for d, terms in hints.items() if d not in db_ids for t in terms} - own)


# ──────────────────────────────────────────────
# 재작성 감사 (W2·W5)
# ──────────────────────────────────────────────

def build_rewrite_trace(
    state: Mapping[str, Any], config: AppConfig, *, consumer: str, frame: IntentFrame | None = None,
) -> dict[str, Any]:
    """§4.9 감사 레코드. 원문 전문은 싣지 않는다(슬롯 값과 출처만 — D-183).

    ``verify``는 채널별이다 — ``user_query``(오케스트레이션이 넣은 R1/R2 재작성문)와
    ``original_query``(LLM 프롬프트가 읽는 R6). 재작성이 없으면(원문과 같으면) 그 채널은 뺀다.
    검증은 **섀도**다 — 결과를 기록만 하고 어떤 텍스트도 바꾸지 않는다(G-5).
    """
    frame = frame or build_intent_frame(state)
    needed, reason = rewrite_needed(frame)
    ifc = config.intent_frame
    verify: dict[str, str] = {}
    if ifc.verify_mode() == "shadow":
        raw = raw_query_of(state)
        db_slot = frame.targets.get("db_ids")
        foreign = foreign_location_terms(list(db_slot.value) if db_slot else [])
        prior = _prior_entities(state)
        channels = {
            "user_query": state.get("user_query"),
            "original_query": (state.get("parsed_requirements") or {}).get("original_query"),
        }
        for channel, text in channels.items():
            if text and str(text) != raw:
                verify[channel] = verify_rewrite(
                    frame, str(text), foreign_location_terms=foreign, prior_entities=prior,
                )
    return {
        "frame_hash": frame.frame_hash(),
        "frame_version": frame.frame_version,
        "slots": frame.slot_dict(),
        "renderer": RENDERER_VERSION,
        "mode": ifc.query_mode(),
        "consumers": sorted(ifc.consumers() & KNOWN_CONSUMERS),
        "consumer": consumer,
        "db_id": state.get("active_db_id"),
        "gate": {"needed": needed, "reason": reason, "mode": ifc.gate_mode()},
        "verify": verify,
        "verify_mode": ifc.verify_mode(),
        "is_composite": bool(state.get("is_composite")),
    }


#: thread_id → [(기록 시각, trace)] — 라우트가 done 페이로드를 만들 때 꺼낸다.
#: 1·2단은 파이프라인 격리 상태가 최종 state로 합쳐지지 않으므로 이 경로로 모은다.
#: 키 수·키당 레코드 수·키 수명 세 방향으로 bound한다(값 bound만으론 키 누수를 못 막는다).
_TRACES: OrderedDict[str, list[tuple[float, dict[str, Any]]]] = OrderedDict()
_MAX_THREADS = 256
_MAX_TRACES_PER_THREAD = 32
_TRACE_TTL_SEC = 900.0


def _sweep(now: float) -> None:
    for key in [k for k, v in _TRACES.items() if not v or now - v[-1][0] > _TRACE_TTL_SEC]:
        _TRACES.pop(key, None)
    while len(_TRACES) > _MAX_THREADS:
        _TRACES.popitem(last=False)


def record_rewrite_trace(thread_id: str | None, trace: dict[str, Any]) -> None:
    """요청 스코프 보관소에 적재한다(스레드 식별자가 없으면 보관하지 않는다)."""
    if not thread_id:
        return
    now = time.monotonic()
    bucket = _TRACES.setdefault(thread_id, [])
    bucket.append((now, trace))
    del bucket[:-_MAX_TRACES_PER_THREAD]
    _TRACES.move_to_end(thread_id)
    _sweep(now)


def pop_rewrite_traces(thread_id: str | None) -> list[dict[str, Any]]:
    """이번 턴에 쌓인 레코드를 꺼내고 비운다(done 페이로드 1회 소비)."""
    if not thread_id:
        return []
    return [trace for _, trace in _TRACES.pop(thread_id, [])]


async def observe_rewrite(state: Mapping[str, Any], config: AppConfig, *, consumer: str) -> dict[str, Any]:
    """프레임을 만들고 재작성 감사를 남긴다(섀도 — 프롬프트는 바꾸지 않는다).

    Returns:
        state 델타 ``{"intent_frame", "rewrite_trace"}`` — 꺼져 있으면 빈 dict
    """
    ifc = getattr(config, "intent_frame", None)
    if ifc is None or ifc.enabled is not True:
        return {}
    try:
        frame = build_intent_frame(state)
        trace = build_rewrite_trace(state, config, consumer=consumer, frame=frame)
    except Exception as exc:  # 섀도 관측 실패가 조회를 막아선 안 된다 — 사유는 남긴다
        logger.warning("의도 프레임 관측 실패(consumer=%s): %s", consumer, exc)
        return {}
    record_rewrite_trace(state.get("thread_id"), trace)
    from src.security.audit_logger import log_rewrite_trace

    try:
        await log_rewrite_trace(
            trace, user_id=state.get("user_id"), thread_id=state.get("thread_id"),
        )
    except Exception as exc:
        logger.warning("재작성 감사 기록 실패(consumer=%s): %s", consumer, exc)
    return {"intent_frame": frame.to_dict(), "rewrite_trace": trace}


# ──────────────────────────────────────────────
# 정규 질의 채널 (W3)
# ──────────────────────────────────────────────

def get_prompt_query(state: Mapping[str, Any], config: AppConfig, *, consumer: str, current: str) -> str:
    """소비자 프롬프트에 넣을 질의 텍스트를 한 곳에서 고른다(§4.4).

    기본은 ``current``(소비자가 지금 쓰는 텍스트) 그대로다. 아래를 **모두** 만족할 때만 해석
    블록을 병기한다: ``INTENT_FRAME_ENABLED`` · ``CANONICAL_QUERY_MODE=augment`` · 소비자가
    ``CANONICAL_QUERY_CONSUMERS``에 있음 · 복합 계획이 아님 · (게이트 enforce면) 재작성 필요.

    복합 계획은 병기하지 않는다 — 전체 프레임을 실으면 task 스코프 축소(D-092·D-094)를
    무력화해 하위 결과로 전체 질문에 답한 듯 서술하는 환각이 되살아난다(§4.4 1번 행).

    Args:
        state: 에이전트 상태
        config: 앱 설정
        consumer: 소비자 이름(``KNOWN_CONSUMERS``)
        current: 소비자가 지금 쓰는 질의 텍스트

    Returns:
        소비자 프롬프트용 질의 텍스트
    """
    ifc = getattr(config, "intent_frame", None)
    if ifc is None or ifc.enabled is not True:
        return current
    if ifc.query_mode() != "augment" or consumer not in ifc.consumers():
        return current
    if state.get("is_composite"):
        return current
    frame = build_intent_frame(state)
    needed, _ = rewrite_needed(frame)
    if ifc.gate_mode() == "enforce" and not needed:
        return current
    return render_canonical_block(
        frame.slot_dict(), intent=frame.intent, raw_query=raw_query_of(state) or current,
    )
