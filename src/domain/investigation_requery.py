"""조사 브리핑 → 「질의응답에서 확인할 질의」 (plans/195 W1) — 결정적 템플릿 · 순수 함수.

가린 사용자 뷰의 `playbook`(scope·sections)을 받아, 단계 `action`마다 코드 카탈로그
(`REQUERY_CATALOG`)의 템플릿에 대상 이름·사건창 절대 시각을 채운다. LLM을 부르지 않는다
(CLAUDE.md 「LLM 비결정성」 · plans/195 §3.1 (a)). 같은 입력이면 늘 같은 출력이다.

규칙(plans/195 §1.4 · §3.1):
- 템플릿마다 검증 출처(`source_ref` — 텍스트투SQL 골드셋·채점 라우팅 골드·제니퍼 벤치 항목 id)가
  있다. 출처가 없는 action은 카탈로그에 넣지 않는다 → 질의 0건(D-192 「검증된 형태만」).
- 대상 이름을 박는다(지시어 금지). 대상이 비거나 가림 표시(`mask_marker` — 호출부가 마스킹 패턴을
  넘긴다 · 부분 가림 표지가 따로 있으면 여러 개)가 섞이면 그 질의를 만들지 않는다.
- 시간은 사건창 `scope.incident_window{start, end}`의 절대 시각을 질의응답 시간 해석기
  (`time_expr` · D-333)가 분 정밀로 되읽는 꼴로 적는다 — 「2026년 10월 26일 오후 7시 10분부터
  오후 7시 40분까지」. 오전·오후 표지를 붙이고(표지 없는 1~12시는 되묻기 · D-333 ③ⓒ) 0시는
  표지 없이 쓴다. naive 값은 KST 벽시계로, tz 값은 KST로 옮겨 읽는다(`time_spec` 관례).
- 제니퍼 축은 `scope.targets[].instance`(없으면 `scope.host`) · 다른 축(`axis`가 제니퍼가 아닌
  카탈로그 항목)은 `scope.polestar_server`만 쓰고, 비었거나 `scope.polestar_link`가
  `absent`·`unresolved`면 만들지 않는다(plans/196 계약).
- 섹션 상태(혼합 단계는 `axis_status[축]`)가 `skipped`·`missing`이면 만들지 않는다.
  `reason`·`reason_detail`·도구 인자 원문은 읽지 않는다. 재사용 섹션(`reused_from`)도 같은 규칙이다.
- 현재값 전용 보기(커넥션 풀·실행 중 서비스)는 `realtime=True`로 표지한다(화면 문구는 W3).

domain 계층 — 표준 라이브러리만 의존한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

Axis = Literal["polestar", "apm"]
#: 템플릿 대상 종류 — 인스턴스(`targets[].instance`) · 호스트(`scope.host`) · 폴스타 등록 서버명.
TargetKind = Literal["inst", "host", "server"]
#: 시간 칸 — 사건창 · 변경 대조 창(사건창 끝 기준 24시간) · 시간 없음.
WindowKind = Literal["incident", "change", "none"]

#: 시간 해석 기준 시간대(`src/domain/time_spec.KST`와 같은 값).
_KST = timezone(timedelta(hours=9))
#: 변경 대조 창(시간) — sre_agent `change.recent` 스텝 기본 `lookback_hours`와 같은 값.
CHANGE_LOOKBACK_HOURS = 24
#: 섹션 하나에서 만드는 인스턴스 질의 상한 — 대상이 많아도 화면을 덮지 않게 앞에서부터 자른다.
MAX_INSTANCE_TARGETS = 3

#: 질의를 만들지 않는 섹션·축 상태(plans/196 §5.2 — 조회하지 않은 단계).
SKIP_STATUSES = frozenset({"skipped", "missing"})
#: 폴스타 축을 만들지 않는 연결 상태(plans/196 §5.2).
POLESTAR_LINK_SKIP = frozenset({"absent", "unresolved"})


@dataclass(frozen=True)
class RequeryTemplate:
    """카탈로그 항목 1건 — 단계 action × 대상 종류의 질의 꼴과 검증 출처."""

    action: str
    axis: Axis
    target: TargetKind
    window: WindowKind
    path: str  # 질의응답 처리기 예상(사다리 2단 — plans/195 §3.2)
    view: str  # 제니퍼 보기 id(벤치 `expect.views` 대조용) — SQL 경로는 빈 값
    source_ref: str  # 검증 출처 항목 id(골드·벤치)
    realtime: bool
    template: str  # `{target}` · `{window}` 자리표


def _apm(action: str, target: TargetKind, window: WindowKind, view: str, ref: str,
         template: str, *, realtime: bool = False) -> RequeryTemplate:
    return RequeryTemplate(
        action, "apm", target, window, "apm_query", view, ref, realtime, template
    )


#: 템플릿 카탈로그 — 출처 id는 `testdata/apm_bench/scenarios.yaml` 항목(대상 종류·보기 일치를
#: 테스트가 로드해 단언한다). 벤치의 상대 기간(「지난 30분」)은 사건창 절대 시각으로 바꿔 끼운다.
#: 출처가 없어 넣지 않은 action(부재를 테스트로 고정): 호스트 자원 추이 · 프로세스 상위 · 같은 알람
#: 재발 · 전후 연관 알람 · 폴스타 변경 이력 · 자원 가용 상태 · 토폴로지 · 실행 중 SQL · DPM ·
#: 대상 확정(scope.resolve_was_targets) · 종합(synthesize) — 근거는 plans/195 W0 대조표.
REQUERY_CATALOG: tuple[RequeryTemplate, ...] = (
    _apm("scope.host_to_was", "host", "none", "apm.instances", "APM-INST-host",
         "{target} 서버에 올라가 있는 WAS 인스턴스가 뭐야?"),
    _apm("apm.golden_signals", "inst", "incident", "apm.app_health", "APM-HEALTH-colloquial",
         "{target} {window} 에러율이랑 p95 응답시간 알려줘"),
    _apm("apm.golden_signals", "host", "incident", "apm.app_health", "APM-HEALTH-host-30m",
         "{target} WAS {window} 에러율이랑 p95 응답시간 알려줘"),
    _apm("apm.runtime_and_pool", "inst", "incident", "apm.runtime", "APM-RUNTIME-heap",
         "{target} {window} 힙 메모리 추세 보여줘"),
    _apm("apm.runtime_and_pool", "inst", "none", "apm.pool", "APM-POOL-inst",
         "{target} DB 커넥션 풀 지금 얼마나 쓰고 있어?", realtime=True),
    _apm("apm.runtime_and_pool", "host", "incident", "apm.runtime", "APM-RUNTIME-metrics-interval",
         "{target} {window} GC 시간이랑 스레드 수 추세 보여줘"),
    _apm("apm.runtime_and_pool", "host", "none", "apm.pool", "APM-POOL-mode",
         "{target} 커넥션 풀이랑 실행 모드별 서비스 개수 보여줘", realtime=True),
    _apm("apm.slow_tx_breakdown", "inst", "incident", "apm.slow_tx", "APM-SLOWTX-10m",
         "{target} {window} 느린 거래 보여줘"),
    _apm("apm.slow_tx_breakdown", "host", "incident", "apm.slow_tx", "APM-MT-profile-sql",
         "{target} {window} 느린 거래 보여줘"),
    _apm("apm.events_errors", "inst", "incident", "apm.events", "APM-ERROR-record",
         "{target} {window} 오류 기록 보여줘"),
    _apm("apm.events_errors", "host", "incident", "apm.events", "APM-ERROR-colloquial",
         "{target} {window} 오류 기록 보여줘"),
    _apm("apm.active_services", "inst", "none", "apm.active", "APM-ACTIVE-inst",
         "{target}에서 지금 돌고 있는 서비스 보여줘", realtime=True),
    _apm("apm.active_services", "host", "none", "apm.active", "APM-ACTIVE-n",
         "{target} 실행 중인 서비스 오래 걸린 순으로 보여줘", realtime=True),
    _apm("change.recent", "inst", "change", "apm.changes", "APM-CHANGES-default",
         "{target} {window} 소스 변경 감지 이력 보여줘"),
    _apm("change.recent", "host", "change", "apm.changes", "APM-CHANGES-3d",
         "{target} {window} 소스 변경 감지 이력 보여줘"),
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


#: 가림 표지 — 문자열 1개 또는 여러 개(사용자 뷰 마스킹은 값 전체 가림과 부분 가림의 표지가 다를 수
#: 있다 · plans/195 W2 교정). 값은 호출부가 넘긴다(domain은 표지 상수를 모른다).
MaskMarkers = str | Sequence[str] | None


def _markers(mask_marker: MaskMarkers) -> tuple[str, ...]:
    """가림 표지 → 빈 값을 뺀 튜플."""
    if mask_marker is None:
        return ()
    if isinstance(mask_marker, str):
        return (mask_marker,) if mask_marker else ()
    return tuple(m for m in mask_marker if isinstance(m, str) and m)


def _usable(name: str, markers: tuple[str, ...]) -> bool:
    """대상 이름으로 쓸 수 있는가 — 비었거나 가림 표지 중 하나라도 섞이면 False."""
    return bool(name) and not any(m in name for m in markers)


def _parse_time(value: Any) -> datetime | None:
    """ISO 시각 → KST 벽시계(naive). 형식이 아니면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_KST).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def _clock(dt: datetime) -> str:
    """「오후 7시 10분」 꼴 — 0시는 표지 없이, 1~11시 오전, 12~23시 오후(12시간제)."""
    h = dt.hour
    if h == 0:
        hour = "0시"
    elif h < 12:
        hour = f"오전 {h}시"
    else:
        hour = f"오후 {h - 12 if h > 12 else 12}시"
    return f"{hour} {dt.minute}분" if dt.minute else hour


def _date(dt: datetime) -> str:
    return f"{dt.year}년 {dt.month}월 {dt.day}일"


def window_phrase(start: datetime, end: datetime) -> str:
    """절대 시각 구간 문구 — 같은 날이면 날짜를 한 번만 쓴다."""
    head = f"{_date(start)} {_clock(start)}부터"
    if start.date() == end.date():
        return f"{head} {_clock(end)}까지"
    return f"{head} {_date(end)} {_clock(end)}까지"


def _windows(scope: Mapping[str, Any]) -> dict[WindowKind, str]:
    """시간 칸 문구 — 사건창이 없거나 깨지면 시간 칸이 있는 템플릿은 만들지 않는다."""
    out: dict[WindowKind, str] = {"none": ""}
    window = scope.get("incident_window")
    if not isinstance(window, Mapping):
        return out
    start, end = _parse_time(window.get("start")), _parse_time(window.get("end"))
    if start is None or end is None or end <= start:
        return out
    out["incident"] = window_phrase(start, end)
    out["change"] = window_phrase(end - timedelta(hours=CHANGE_LOOKBACK_HOURS), end)
    return out


def _instances(scope: Mapping[str, Any]) -> list[str]:
    targets = scope.get("targets")
    if not isinstance(targets, list):
        return []
    names = [_text(t.get("instance")) for t in targets if isinstance(t, Mapping)]
    return list(dict.fromkeys(n for n in names if n))


def _axis_status(section: Mapping[str, Any], axis: Axis) -> str:
    """섹션의 축 상태 — 혼합 단계(`axis_status`에 축 키가 있음)는 축별 상태, 아니면 섹션 상태."""
    per_axis = section.get("axis_status")
    if isinstance(per_axis, Mapping) and axis in per_axis:
        return _text(per_axis.get(axis))
    return _text(section.get("status"))


def _targets(
    tpl: RequeryTemplate, scope: Mapping[str, Any], markers: tuple[str, ...]
) -> list[str]:
    """템플릿 대상 이름 목록 — 쓸 수 없는 이름(빈 값·가림 표시)은 뺀다."""
    if tpl.target == "server":
        if _text(scope.get("polestar_link")) in POLESTAR_LINK_SKIP:
            return []
        names = [_text(scope.get("polestar_server"))]
    elif tpl.target == "inst":
        names = _instances(scope)[:MAX_INSTANCE_TARGETS]
    else:
        # 호스트 템플릿은 인스턴스 대상이 있으면 쓰지 않는다(같은 보기 중복 방지) — 역방향
        # 단계(scope.host_to_was)는 인스턴스 템플릿이 없어 늘 호스트로 간다.
        has_inst = any(t.action == tpl.action and t.target == "inst" for t in REQUERY_CATALOG)
        if has_inst and _instances(scope):
            return []
        names = [_text(scope.get("host"))]
    return [n for n in names if _usable(n, markers)]


def build_requery(
    playbook: Mapping[str, Any] | None, *, mask_marker: MaskMarkers
) -> list[dict[str, Any]]:
    """가린 사용자 뷰의 `playbook`으로 질의응답 질의 목록을 만든다(섹션 순서 · 카탈로그 순서).

    항목 키: `step`·`title`(섹션 제목)·`prompt`·`path`(처리기 예상)·`source_ref`·`realtime`·
    `axis` · 폴스타 축이면 `polestar_source`(scope 값 그대로). 플레이북이 없으면(HolmesGPT 단독
    조사 포함) 빈 목록. `mask_marker`는 표지 1개 또는 여러 개(하나라도 섞인 대상은 생략).
    """
    if not isinstance(playbook, Mapping):
        return []
    scope = playbook.get("scope")
    sections = playbook.get("sections")
    if not isinstance(scope, Mapping) or not isinstance(sections, list):
        return []
    windows = _windows(scope)
    markers = _markers(mask_marker)
    out: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        action = _text(section.get("action"))
        for tpl in REQUERY_CATALOG:
            if tpl.action != action or _axis_status(section, tpl.axis) in SKIP_STATUSES:
                continue
            phrase = windows.get(tpl.window)
            if phrase is None:
                continue
            for name in _targets(tpl, scope, markers):
                item: dict[str, Any] = {
                    "step": _text(section.get("step")),
                    "title": _text(section.get("title")),
                    "prompt": tpl.template.format(target=name, window=phrase),
                    "path": tpl.path,
                    "source_ref": tpl.source_ref,
                    "realtime": tpl.realtime,
                    "axis": tpl.axis,
                }
                if tpl.axis == "polestar":
                    item["polestar_source"] = scope.get("polestar_source")
                out.append(item)
    return out
