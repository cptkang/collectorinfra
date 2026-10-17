"""첫 홉(부하 순위·인스턴스 목록) 조각 — 조회 인스턴스 · 호스트별 소스 · 대상 문구.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from typing import Any

from src.domain import disclosure as disc
from src.orchestration.apm.calls import _Call
from src.routing.registry import ViewSpec
from src.utils.prior_dependency import NOTE_TRACE


def _queried_instances(calls: list[_Call]) -> int:
    """대상별 호출(hostname · 인스턴스)이 실제로 돌려준 인스턴스 수 — (소스 · 인스턴스 id) 기준."""
    seen: set[tuple[Any, Any]] = set()
    for call in calls:
        if call.error is not None or not (call.hostname or call.instance):
            continue
        for row in (call.envelope or {}).get("rows") or []:
            if isinstance(row, dict) and row.get("instance_id") is not None:
                seen.add((row.get("source_id"), row.get("instance_id")))
    return len(seen)


def _host_sources(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """순위 행 → 호스트별 소스 목록(행 순서 · 중복 없음) — 소스가 없는 행의 호스트는 싣지 않는다."""
    out: dict[str, list[str]] = {}
    for row in rows:
        host = str(row.get("hostname") or "").strip()
        sid = row.get("source_id")
        if host and isinstance(sid, str) and sid and sid not in out.setdefault(host, []):
            out[host].append(sid)
    return {h: sids for h, sids in out.items() if sids}


def _target_rows(rows: list[dict[str, Any]], *, host_ok: Any
                 ) -> tuple[list[str], list[dict[str, Any]], int]:
    """첫 홉 행 → (hostname 대상, 호스트 없는 인스턴스 대상, 부를 수 없는 행 수).

    호스트가 정합된 행(`host_ok`)은 hostname 대상(서버당 하나 — 그 서버의 인스턴스를 게이트웨이가
    정합한다), 호스트가 없는 행은 (이름 · 소스 · id) 대상이다.
    """
    hosts: list[str] = []
    hostless: list[dict[str, Any]] = []
    unaddressed = 0
    for row in rows:
        host = str(row.get("hostname") or "").strip()
        if host and host_ok(row):
            if host not in hosts:
                hosts.append(host)
        elif row.get("source_id") and row.get("instance_id") is not None \
                and row.get("instance_name"):
            hostless.append({k: row.get(k)
                             for k in ("instance_name", "source_id", "domain_id", "instance_id")})
        else:
            unaddressed += 1
    return hosts, hostless, unaddressed


def _step_call(step: dict[str, Any], view: ViewSpec, args: dict[str, Any]) -> None:
    """첫 홉 단계에 게이트웨이 호출(도구 · 인자 — 스레드·owner 제외)을 남긴다 — 본체 감사
    `commands`가 대상 조회 호출과 같은 모양으로 싣는다(V6-8 ①)."""
    step["tool"] = view.tool
    used = {k: v for k, v in args.items() if k not in ("thread_id", "owner") and v is not None}
    if used:
        step["args"] = used


#: 대상 미지정 상위 N 고지의 안내 꼬리(plans/134 W6 ④).
_NAME_TARGET_TAIL = "특정 서버(또는 업무)를 지정하면 그 대상만 정확히 조회합니다"


def _first_hop_kind(step: dict[str, Any]) -> str:
    """첫 홉 범위 고지의 kind — 실패가 섞이면 `apm_partial_sources`, 건강한 순위로 상위 N개만
    고른 것은 의도된 범위 제한이라 `apm_untargeted_scope`(둘 다 의무), 전부 조회면 비의무 `trace`.

    실패 = 결과 파일 읽기 실패 · 잠정 순위 · 순위를 받지 못해 목록 앞 N개로 대신한 경우(④).
    """
    if step.get("read_error"):
        return disc.APM_PARTIAL_SOURCES
    if step.get("mode") == "ranking":
        if "provisional" in step:
            return disc.APM_PARTIAL_SOURCES
        total, shown = step.get("fleet_total") or 0, step.get("instances") or 0
        return disc.APM_UNTARGETED_SCOPE if total > shown else NOTE_TRACE
    if step.get("mode") == "list" and (step.get("listed") or 0) > (step.get("instances") or 0):
        return disc.APM_PARTIAL_SOURCES
    return NOTE_TRACE


def _first_hop_text(step: dict[str, Any]) -> str:
    """첫 홉 범위 고지 — 「대상 서버 미지정 — 전체 인스턴스 N개(호스트 H대) 조회」.

    - 부하 순위(④): 전체 M개 중 상위 N개만이면 그 사실과 대상 지정 안내 · M ≤ N이면 「전체 M개」 ·
      순위 밖(값 없음)·잠정(조회 실패 도메인 제외)을 덧붙인다.
    - 순위 폴백(④): 「부하 순위를 받지 못해 목록 순서 앞 N개 — 부하 순이 아님」(전체·순위라고
      부르지 않는다).
    - 결과 파일을 끝까지 읽지 못했으면 「전체」라고 쓰지 않고 읽은 범위와 사유를 쓴다.
    고지 kind는 `_first_hop_kind`가 정한다(상위 N = `apm_untargeted_scope` · 실패 =
    `apm_partial_sources`).
    """
    hosts = step.get("hosts", 0)
    if step.get("mode") == "ranking":
        total, shown = step.get("fleet_total", 0), step.get("instances", 0)
        if total > shown:
            actual = (f" · 실제 조회 인스턴스 {step['queried']}개"
                      if step.get("queried") else "")
            text = (f"대상 서버 미지정 — 전체 인스턴스 {total}개 중 현재 부하(TPS) 상위 {shown}개"
                    f"(호스트 {hosts}대{actual})만 조회했습니다")
            if step.get("unranked") and shown < step.get("top_n", shown):
                text += f" · TPS 값이 없는 인스턴스 {step['unranked']}개는 순위에 없어 빠졌습니다"
            text += f" · {_NAME_TARGET_TAIL}"
        else:
            text = f"대상 서버 미지정 — 전체 인스턴스 {total}개(호스트 {hosts}대) 조회"
        if "provisional" in step:
            failed = f" {step['provisional']}곳" if step["provisional"] else ""
            text += f" · 잠정 — 조회 실패 도메인{failed}의 인스턴스는 부하 순위에 없습니다"
        if step.get("read_error"):
            text += f" · 순위 결과 파일을 끝까지 읽지 못했습니다({step['read_error']})"
    elif step.get("read_error"):
        text = (f"대상 서버 미지정 — 인스턴스 {step.get('total')}개 중 {step.get('instances', 0)}개"
                f"(호스트 {step.get('hosts', 0)}대)만 조회 — 나머지는 목록 결과 파일을 끝까지"
                f" 읽지 못했습니다({step['read_error']})")
        if step.get("mode") == "list":
            text += f" · 부하 순위를 받지 못해({step.get('rank_error')}) 목록으로 골랐습니다"
    elif step.get("mode") == "list" and (step.get("listed") or 0) > step.get("instances", 0):
        text = (f"대상 서버 미지정 — 부하 순위를 받지 못해({step.get('rank_error')}) 인스턴스 목록"
                f" 순서 앞 {step.get('instances', 0)}개(호스트 {hosts}대)만 조회했습니다 — 부하"
                f" 순이 아닙니다 · 목록 인스턴스 {step['listed']}개 · {_NAME_TARGET_TAIL}")
    else:
        text = (f"대상 서버 미지정 — 전체 인스턴스 {step.get('instances', 0)}개"
                f"(호스트 {hosts}대) 조회")
        if step.get("mode") == "full":
            text += "(전부 조회 요청)"
        elif step.get("mode") == "list":
            text += f" — 부하 순위를 받지 못해({step.get('rank_error')}) 목록으로 골랐습니다"
    if step.get("unaddressed"):
        text += f" · 이름·소스가 없어 부를 수 없는 인스턴스 {step['unaddressed']}개는 빠졌습니다"
    return text
