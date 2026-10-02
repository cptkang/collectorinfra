"""호스트 조사 경로 — `mcp_server` 고수준 도구 배선 (Plan 78 W3-1·W3-2·W3-3 · WU-18).

**중간 비용대의 공백을 메운다.** 지금까지 "서버 상태를 본다"는 요구가 갈 곳은 둘뿐이었다 —
`data_query`(DB SQL)이거나 `fault_diagnosis`(`sre_agent` 위임 · 비쌈). 그 사이의
**OS 구성·자원 현황·메트릭 추세 단건 조회**가 통째로 비어 있었다(78 §2.2 G1).

이 모듈이 `DBHubClient.inspect_host`(WU-13)의 **프로덕션 호출부**다. 그전까지 호출부가
`tests/`에만 있어, W3-1 수용 기준의 *"본체에서 호출되고 소비된다"* 가 e2e로 충족되지 않았다
(`docs/18_known_mistakes.md` 2026-08-27).

설계는 `SPEC-host-inspect-routing.md`. 요지 셋:

1. **도구는 하나만 는다**(W3-4) — 프로파일(4종 + plans/92 O3 `metrics_live`)을 subagent 1개가
   `profile` 인자로 흡수한다.
2. **게이트는 handler에**(W3-3 · P14) — 도구 목록은 **항상 고정**(빼면 KV 캐시가 무효화된다).
   비활성이면 handler가 **구조화 거부**를 돌려주고, 모델은 그 사유를 보고 대체 경로를 고른다.
3. **판정은 결정적**(D-035) — 프로파일도 대상도 코드가 정한다. LLM은 값을 만들지 않는다.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig
from src.db import get_db_client
from src.orchestration.db_access import access_denied_result, authorize_targets
from src.utils.prior_targets import TargetRef

logger = logging.getLogger(__name__)

#: SUBAGENT_REGISTRY 키 — `_TOOL_NAMES` 동사+목적어 관례(W3-4).
HOST_INSPECT_AGENT = "host_inspect"

#: 거부·실패 사유 키. 침묵 폴백 금지 — 호출자가 이 키를 보고 다음 행동을 고른다.
DEGRADED_KEY = "degraded_reason"

# ── 프로파일 판정 (W3-2) ────────────────────────────────────────────────
#
# **의도적으로 좁다.** `data_query`는 본체의 주력 경로라 여기서 욕심을 내면 정상 조회를
# 잠식하는데, WU-06(분포 실측)이 G-BILL로 막혀 있어 **정확도를 측정할 수단이 없다**
# (SPEC §0.1). 측정 없이 넓히지 않는다 — 넓히는 것은 WU-06 이후의 판단이다.
# Known Mistakes: "금지·교정 규칙은 범위를 좁게 못 박는다."
#
# 선언 순서 = 충돌 시 우선순위. `processes`는 여기 없다 — 실시간 프로세스 조회는
# `process_query`가 이미 1급 경로다(D-041 · D-046/047 결정적 교정).
# `metrics_live`(plans/92 O3 · exporter 현재값)는 **맨 뒤 = 최저 우선순위**다 — 기존 세
# 프로파일의 판정을 하나도 뺏지 않는다("실시간 메트릭 추세"는 여전히 `metric_trend`).
_PROFILE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("os_config", ("os 정보", "os정보", "운영체제", "커널", "os 버전", "os 구성", "os구성")),
    ("resource_status", ("자원 현황", "자원현황", "리소스 현황", "리소스현황")),
    ("metric_trend", ("메트릭 추세", "지표 추세", "사용률 추세")),
    ("metrics_live", (
        "실시간 메트릭", "실시간 지표", "현재 메트릭", "exporter 메트릭", "익스포터 메트릭",
    )),
)

#: `inspect_host`가 프로파일별로 요구하는 식별자 (D-046 — 폴스타는 server_name ≠ hostname).
#: `metrics_live`의 도구 인자명은 `hostname`이지만 값은 server_name이다
#: (D-119 ③ — `DBHubClient` 주석).
_PROFILE_IDENTIFIER: dict[str, str] = {
    "os_config": "hostname",
    "resource_status": "server_name",
    "metric_trend": "server_name",
    "metrics_live": "server_name",
}

#: `metrics_live` 필터 후보 토큰 — 식별자 문자 연속열(한글·공백·기호에서 끊는다). 하이픈·점을
#: 포함해 끊어야 `svr-web_01`·FQDN이 통째로 한 토큰이 되어 아래 bare 이름 판정에서 탈락한다.
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_:.\-]+")
#: bare 메트릭 이름(Prometheus 규칙) — `DBHubClient._METRIC_NAME_RE`와 같은 패턴.
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")

#: 메트릭 필터를 뽑지 못했을 때의 구조화 거부(침묵 금지 — 호출하지 않는다).
_METRIC_UNSPECIFIED_ERROR = (
    "실시간 메트릭 조회에는 메트릭 이름(예: node_load1) 또는 접두(예: node_)가 필요합니다."
)

#: `metric_trend`의 지표 종류(서버 `kind`) — 입력 파서의 `query_targets`(LLM 닫힌 열거 · D-004)에서
#: 결정적으로 고른다(plans/121 TP-1.9 · N-9). 「디스크」는 파일시스템 사용률과 디스크 IO 둘 다일 수
#: 있어 모호하다 — 추측하지 않고 거부한다.
_METRIC_KIND_BY_TARGET: dict[str, str] = {
    "CPU": "cpu", "메모리": "memory", "파일시스템": "filesystem",
}
_AMBIGUOUS_METRIC_TARGETS: frozenset[str] = frozenset({"디스크"})
_METRIC_KIND_LABELS: dict[str, str] = {
    "cpu": "CPU", "memory": "메모리", "filesystem": "파일시스템", "disk_io": "디스크 IO",
}
_METRIC_KIND_UNRESOLVED_ERROR = (
    "메트릭 추세 조회에는 지표 종류(CPU·메모리·파일시스템 사용률 중 하나 이상)가 필요합니다."
)
#: 기존 DB 프로파일의 응답용 이름 — 행 요약(결정적 문구)에 쓴다.
_PROFILE_LABELS: dict[str, str] = {
    "os_config": "OS 구성", "resource_status": "자원 현황", "metric_trend": "메트릭 추세",
}


def _refusal(message: str, reason: str) -> dict[str, Any]:
    """거부·실패 결과 — **텍스트 결과**로 돌려준다(plans/121 TP-1.5 · N-4).

    종전에는 `organized_data: ""`를 실어 응답 조립기가 행 결과로 읽다가 `TypeError` 문구로 끝났다
    (1단은 조사 플래그 off에서도 도구가 노출돼 운영 응답에 섞였다). 사유는 `error`·`degraded_reason`
    으로 구조화해 그대로 두고, 사용자 문구는 `final_response`로 싣는다.
    """
    return {"error": message, DEGRADED_KEY: reason, "final_response": message}


def _metric_kinds(isolated: dict[str, Any]) -> list[str] | None:
    """`metric_trend`에 넘길 `kind` 목록 — 모호하거나 하나도 없으면 None(호출하지 않는다)."""
    targets = (isolated.get("parsed_requirements") or {}).get("query_targets") or []
    if not isinstance(targets, list):
        return None
    names = [str(t).strip() for t in targets]
    if any(n in _AMBIGUOUS_METRIC_TARGETS for n in names):
        return None
    kinds = list(dict.fromkeys(
        _METRIC_KIND_BY_TARGET[n] for n in names if n in _METRIC_KIND_BY_TARGET
    ))
    return kinds or None


def _organize_db_rows(result: dict[str, Any], profile: str) -> dict[str, Any]:
    """DB 프로파일(`os_config`·`resource_status`·`metric_trend`)의 행을 응답 조립기가 읽는 모양으로.

    `_organize_live_metrics`(plans/92 O3)와 같은 규약이다 — 서버 계약 키는 건드리지 않고
    `organized_data`·`query_results` 두 키만 더한다(D-122). 종전에는 이 키가 없어 성공한 조사도
    "처리 결과가 없습니다."로 끝났다(plans/121 TP-1.5 · N-4). 요약은 결정적 문구다(LLM 0).
    """
    rows = [r for r in (result.get("rows") or []) if isinstance(r, dict)]
    parts = [
        f"{_PROFILE_LABELS.get(profile, profile)} 조회 결과 {len(rows)}행입니다"
        f"(조회 시각 {result.get('queried_at') or '미상'})."
    ]
    if profile == "metric_trend":
        kinds = result.get("kinds") or ([result["kind"]] if result.get("kind") else [])
        labels = ", ".join(_METRIC_KIND_LABELS.get(k, str(k)) for k in kinds)
        parts.append(f"지표 {labels} · 기간은 최근 24시간(시간 단위)입니다.")
        for kind, reason in (result.get("kind_errors") or {}).items():
            label = _METRIC_KIND_LABELS.get(kind, kind)
            parts.append(f"{label} 추세는 조회하지 못했습니다({reason}).")
    return {
        "organized_data": {
            "summary": " ".join(parts),
            "rows": rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        },
        "query_results": rows,
    }


def detect_profile(sub_query: str) -> Optional[str]:
    """질의 문자열에서 조사 프로파일을 **결정적으로** 판정한다 (W3-2).

    Args:
        sub_query: task의 sub_query (또는 격리 입력의 user_query)

    Returns:
        `os_config` | `resource_status` | `metric_trend` | `metrics_live`(exporter 현재값 ·
        최저 우선순위), 해당 없으면 None. None은 "이 경로가 아니다"라는 뜻이지 실패가 아니다.
    """
    text = (sub_query or "").lower()
    if not text:
        return None
    for profile, keywords in _PROFILE_KEYWORDS:
        if any(k in text for k in keywords):
            return profile
    return None


def has_target_signal(state: dict, app_config: AppConfig) -> bool:
    """이번 턴에 **조사 대상 호스트 신호가 있는지** 판정한다 (W3-2 발화 조건 ③).

    `intent_planner`가 경로를 교정하기 전에 부른다. **이 함수가 `src.utils.prior_targets`
    접촉을 이 모듈 안에 가둔다** — `intent_planner`는 W1(대상 해소)의 소유 경계 밖이고,
    그 경계는 테스트로 고정돼 있다(`test_r13_boundary_untouched` · 80 §6 소유권 계약).

    planner 시점에는 선행 task 결과가 아직 없으므로 `prior_targets`는 보지 않는다 —
    **이번 턴 지목(`filter_conditions`)과 승계 엔티티(`previous_entities`)만** 본다.

    Args:
        state: 현재 상태
        app_config: 앱 설정(상한)

    Returns:
        대상이 하나라도 해소되면 True.
    """
    from src.utils.prior_targets import resolve_targets

    parsed = state.get("parsed_requirements") or {}
    ctx = state.get("conversation_context") or {}
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        previous_entities=ctx.get("previous_entities"),
        max_targets=app_config.composite.max_targets,
    )
    return bool(resolution.targets)


def _identifier_for(profile: str, target: TargetRef) -> tuple[Optional[str], Optional[str]]:
    """프로파일이 요구하는 식별자를 대상에서 뽑는다.

    한 필드로 뭉개면 절반이 0건이 된다(TargetRef 주석 · D-046). 요구 필드가 비어 있으면
    **다른 필드로 대체하지 않는다** — 폴스타에서 server_name과 hostname은 다른 값이므로
    대체는 엉뚱한 호스트를 조사하는 길이다.

    Returns:
        `(hostname, server_name)` — 요구하지 않는 쪽은 None.
    """
    if _PROFILE_IDENTIFIER[profile] == "hostname":
        return (target.hostname or None), None
    return None, (target.server_name or None)


def _metric_filter(
    sub_query: str, *, exclude: tuple[str | None, ...] = ()
) -> dict[str, str] | None:
    """`metrics_live`의 필터(`metric` 또는 `prefix`)를 질의에서 **결정적으로** 뽑는다(plans/92 O3).

    ASCII 토큰 중 `_`를 포함하고 bare 메트릭 이름 형식에 맞는 **첫** 토큰을 쓴다 —
    `_`로 끝나면 접두(`node_` → prefix), 아니면 이름(`node_load1` → metric).
    LLM은 값을 만들지 않는다(D-035).

    Args:
        sub_query: task의 sub_query
        exclude: 필터로 쓰지 않을 토큰(대상 식별자 — `web_01` 같은 서버명이 메트릭으로
            오인되지 않게). 대소문자 무시.

    Returns:
        `{"metric": …}` 또는 `{"prefix": …}`, 후보가 없으면 None.
    """
    skip = {str(v).casefold() for v in exclude if v}
    for token in _ASCII_TOKEN_RE.findall(sub_query or ""):
        if "_" not in token or not _METRIC_NAME_RE.match(token) or token.casefold() in skip:
            continue
        return {"prefix": token} if token.endswith("_") else {"metric": token}
    return None


def _organize_live_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """`om_metric_instant`의 instant vector를 응답 조립기가 읽는 행으로 펼친다(plans/92 O3).

    응답 조립(`result_aggregator._finalize_task`)은 `organized_data`(행+요약)나 `final_response`만
    소비한다. 서버 계약(`data.result`)만 실으면 사용자 응답이 "처리 결과가 없습니다."로 끝난다
    (실측 2026-09-22). 서버 계약 키는 **건드리지 않고** 이 두 키만 더한다(D-122).

    행: `server_name`(= 주입 라벨 `nodename` · D-119 ③) · `metric`(= `__name__`) · 나머지 라벨 ·
    `value`. `server_name`을 앞에 두어 복합 질의의 서버 키 병합(`_find_identity_col`)이 메트릭
    이름을 키로 오인하지 않게 한다. 요약은 결정적 문구다 — 현재값·counter 누적값(R-12 · R-5) ·
    절단(침묵 절단 금지) · 신원 불일치.
    """
    series = (result.get("data") or {}).get("result") or []
    rows: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        labels = dict(item.get("metric") or {})
        value = item.get("value")
        rows.append({
            "server_name": labels.pop("nodename", None),
            "metric": labels.pop("__name__", None),
            **labels,
            "value": value[1] if isinstance(value, (list, tuple)) and len(value) > 1 else None,
        })
    observed = result.get("observed_at") or result.get("queried_at")
    notes = [
        f"OpenMetrics 스크레이프로 읽은 현재값입니다(조회 시각 {observed}). "
        "이력·rate는 없고 counter 타입은 누적값입니다."
    ]
    if result.get("truncated"):
        notes.append(f"시리즈 {result.get('series_total')}개 중 {len(rows)}개만 담았습니다(절단).")
    if result.get("target_identity") == "mismatch":
        notes.append(
            "⚠ 스크레이프 대상의 OS 호스트명이 등록 정보와 다릅니다(target_identity=mismatch) — "
            "허용목록 오등록 가능성이 있어 이 값을 이 서버의 값으로 단정할 수 없습니다."
        )
    return {
        "organized_data": {
            "summary": " ".join(notes),
            "rows": rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        },
        "query_results": rows,
    }


async def _metric_trend_by_kind(
    client: Any, kinds: list[str], server_name: str | None,
) -> dict[str, Any]:
    """지표 종류마다 한 번씩 부른다(최대 4종 · plans/121 TP-1.9).

    한 종류면 서버 계약을 그대로 돌려준다. 여러 종류면 행마다 `kind`를 붙여 잇고, 실패한 종류는
    `kind_errors`로 남긴다(침묵 누락 금지). 전부 실패면 `{error}`다.
    """
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for kind in kinds:
        res = await client.inspect_host(profile="metric_trend", server_name=server_name, kind=kind)
        if not isinstance(res, dict) or res.get("error"):
            errors[kind] = str((res or {}).get("error") if isinstance(res, dict) else res)
        else:
            results[kind] = res
    if not results:
        return {"error": " / ".join(
            f"{_METRIC_KIND_LABELS.get(k, k)}: {e}" for k, e in errors.items()
        )}
    if len(kinds) == 1:
        return results[kinds[0]]
    first = next(iter(results.values()))
    rows = [
        {"kind": kind, **row}
        for kind, res in results.items()
        for row in (res.get("rows") or []) if isinstance(row, dict)
    ]
    merged: dict[str, Any] = {**first, "rows": rows, "row_count": len(rows), "kinds": list(results)}
    merged.pop("kind", None)
    if errors:
        merged["kind_errors"] = errors
    return merged


async def run_host_inspect(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any]:
    """`mcp_server` 고수준 도구로 호스트를 단건 조사한다 (W3-1 호출부 · W3-3 게이트).

    SUBAGENT_REGISTRY handler 규약(task, isolated, *, llm, app_config)을 따른다.
    `llm`은 쓰지 않는다 — 프로파일·대상·인자가 전부 결정적이다(D-035).

    **게이트는 여기 하나뿐이다**(W3-3 · P14): 도구 목록에서 빼는 방식으로 라우팅하지 않는다 —
    도구 정의는 컨텍스트 접두부라 목록이 흔들리면 이후 전 턴의 KV 캐시가 무효화된다.
    목록은 고정하고 **가용성만** 여기서 제어하며, 거부는 **조용히 하지 않고** 사유를 구조화해
    돌려준다 — 모델이 대체 경로를 고를 수 있어야 한다(W3-4).

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(parsed_requirements·conversation_context·prior_* 포함)
        llm: 미사용 (시그니처 호환)
        app_config: 앱 설정

    Returns:
        성공 시 `inspect_host`의 서버 반환 계약을 **변형 없이** 실은 dict
        (`{rows, row_count, queried_at, source_kind, source, engine}` · D-122)에 응답 조립용
        `organized_data`·`query_results`를 **더한다**(DB 프로파일 `_organize_db_rows` ·
        `metrics_live` `_organize_live_metrics`). 실패·거부는 텍스트 결과
        `{error, degraded_reason, final_response}`(plans/121 TP-1.5). `metric_trend`가 여러
        지표 종류면 종류별 호출을 잇는다(TP-1.9).
    """
    # ── 게이트 (W3-3 · fail-closed) ─────────────────────────────────
    if not app_config.composite.investigation_enabled:
        logger.info("host_inspect 거부: composite.investigation_enabled=False")
        return _refusal(
            "호스트 조사 경로가 비활성입니다(COMPOSITE_INVESTIGATION_ENABLED).",
            "composite_investigation_disabled",
        )

    sub_query = task.get("sub_query") or isolated.get("user_query") or ""
    profile = detect_profile(sub_query)
    if not profile:
        logger.info("host_inspect 거부: 프로파일 미판정 sub_query=%r", sub_query[:120])
        return _refusal(
            "조사 프로파일을 판정하지 못했습니다"
            "(OS 구성·자원 현황·메트릭 추세 중 하나여야 합니다).",
            "profile_undetected",
        )

    # ── 대상 해소 — 공통 모듈 경유 (W1-4 · G5) ──────────────────────
    # 사본을 만들지 않는다(D-053): 세 진입 경로가 쓰는 그 함수를 그대로 쓴다.
    from src.orchestration.process_query import (  # 지연 임포트 — 순환 방지
        _resolve_db_id,
        resolve_investigation_targets,
    )

    db_id = _resolve_db_id(task, isolated, sub_query, app_config)
    # 사용자별 DB 인가(D-232 · plans/116 §10.3 결함 ②) — 존이 확정되지 않으면 아래
    # `get_db_client`가 기본 소스(DBHUB_SOURCE_NAME)로 붙으므로 그 소스로 판정한다
    # (대상 미확정을 인가 통과로 읽지 않는다).
    inspect_db_id = db_id or getattr(getattr(app_config, "dbhub", None), "source_name", None)
    if authorize_targets([{"db_id": inspect_db_id or "default"}], isolated) is None:
        logger.info("host_inspect 인가 거부: db_id=%s", inspect_db_id)
        return access_denied_result()
    resolution = resolve_investigation_targets(isolated, db_id=db_id)
    if not resolution.targets:
        logger.info("host_inspect 0건: 대상 미식별 db_id=%s source=%s", db_id, resolution.source)
        return _refusal(
            "조사 대상 서버를 식별하지 못했습니다. 서버명을 지정해 주세요.", "target_unresolved",
        )

    # **단건 조회 경로다**(78 W3-2 경로표 "단건 조회"). N개 대상은 W2 fan-out 소관이므로
    # 여기서 조용히 첫 건만 쓰지 않고 **절단 사실을 결과에 싣는다**(침묵 절단 금지).
    target = resolution.targets[0]
    truncated = len(resolution.targets) - 1

    hostname, server_name = _identifier_for(profile, target)
    if not (hostname or server_name):
        need = _PROFILE_IDENTIFIER[profile]
        logger.info("host_inspect 0건: %s 프로파일이 요구하는 %s 부재", profile, need)
        return _refusal(
            f"{profile} 조사에 필요한 {need}을(를) 대상에서 찾지 못했습니다.", "identifier_missing",
        )

    # `metrics_live`는 필터가 필수다(서버 계약 — metric 또는 prefix). 다른 프로파일은 옵션
    # 없이 종전 인자 그대로 부른다(`**{}` — 호출 인자 비트 동일).
    options: dict[str, str] = {}
    if profile == "metrics_live":
        metric_filter = _metric_filter(
            sub_query, exclude=(target.server_name, target.hostname)
        )
        if metric_filter is None:
            logger.info(
                "host_inspect 거부: metrics_live 메트릭 미지정 sub_query=%r", sub_query[:120]
            )
            return _refusal(_METRIC_UNSPECIFIED_ERROR, "metric_unspecified")
        options = metric_filter

    # `metric_trend`는 지표 종류(`kind`)가 서버 필수 인자다(plans/121 TP-1.9 · N-9 — 종전에는
    # 넘기지 않아 상시 거부됐다). 입력 파서의 조회 대상에서 결정적으로 고르고, 없거나 모호하면
    # 부르지 않는다.
    kinds: list[str] = []
    if profile == "metric_trend":
        resolved_kinds = _metric_kinds(isolated)
        if resolved_kinds is None:
            logger.info(
                "host_inspect 거부: metric_trend 지표 종류 미확정 query_targets=%r",
                (isolated.get("parsed_requirements") or {}).get("query_targets"),
            )
            return _refusal(_METRIC_KIND_UNRESOLVED_ERROR, "metric_kind_unresolved")
        kinds = resolved_kinds

    logger.info(
        "host_inspect 진입: profile=%s db_id=%s hostname=%s server_name=%s targets=%d",
        profile, db_id, hostname, server_name, len(resolution.targets),
    )

    async with get_db_client(app_config, db_id=db_id) as client:
        if kinds:
            result = await _metric_trend_by_kind(client, kinds, server_name)
        else:
            result = await client.inspect_host(
                profile=profile,
                hostname=hostname,
                server_name=server_name,
                **options,
            )

    # 반환 계약은 **서버가 정본**이다(D-122) — 본체는 변형하지 않고 그대로 싣는다.
    if isinstance(result, dict) and result.get("error"):
        return {**result, DEGRADED_KEY: "inspect_failed", "final_response": str(result["error"])}

    payload: dict[str, Any] = {**result, "profile": profile}
    # 서버 계약에 `target`이 있으면(`om_metric_instant` — 스크레이프 허용목록 타깃 이름)
    # 덮어쓰지 않고 조사 대상은 `inspect_target`에 싣는다(D-122). 기존 프로파일 계약에는
    # `target`이 없어 종전과 같다.
    payload["inspect_target" if "target" in result else "target"] = target.model_dump()
    if profile == "metrics_live":
        # vector는 `rows`가 아니라 `data.result`라 응답 조립기가 읽지 못한다 —
        # 행으로 펼친 두 키를 더한다.
        payload.update(_organize_live_metrics(result))
    else:
        # DB 프로파일도 응답 조립기가 읽는 두 키를 더한다(plans/121 TP-1.5 — 서버 계약 키 불변).
        payload.update(_organize_db_rows(result, profile))
    if truncated > 0:
        payload["truncated_targets"] = truncated
        payload["truncation_note"] = (
            f"대상 {len(resolution.targets)}건 중 1건만 조사했습니다"
            f"(단건 조회 경로 — 나머지 {truncated}건은 조사하지 않았습니다)."
        )
    return payload
