"""보기 대상 종류(`ViewSpec.target`)별 호출 구성 표(`_CALL_BUILDERS` — plans/172).

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from typing import Any

from src.orchestration.apm.calls import _Call, _call_args, _host_calls
from src.orchestration.apm.context import _RunCtx
from src.orchestration.apm.targets import (
    _answer_call,
    _instance_calls,
    _service_names,
    _service_scoped,
    _targeted,
)
from src.orchestration.apm.window import WindowPlan
from src.routing.registry import ViewSpec


def _build_calls(ctx: _RunCtx) -> list[_Call]:
    """보기별 게이트웨이 호출 — 보기 `target` 종류로 구성 함수를 고른다(`_CALL_BUILDERS`)."""
    calls: list[_Call] = []
    for vid in ctx.views:
        if vid in ctx.blocked:  # 필수 조건·참조 미해결 — 되묻기(고지는 위에서 실었다)
            continue
        view = ctx.by_id[vid]
        build = _CALL_BUILDERS.get(view.target, _calls_host)
        calls += build(ctx, view, ctx.windows[vid], ctx.view_args.get(vid))
    return calls


def _calls_reference(ctx: _RunCtx, view: ViewSpec, plan: WindowPlan,
                     args: dict[str, Any] | None) -> list[_Call]:
    """앞 결과 행 참조 — 참조 칸·창은 고른 행에서(창 인자는 `_trace_window`).

    GUID 추적만 이번 턴 사용자가 말한 서버로 좁힌다(없으면 허용된 전 소스·도메인).
    """
    pick = ctx.picks[view.id]
    ref_hosts: list[str | None] = (
        list(ctx.explicit_hosts) if view.reference == "guid" else [pick.hostname])
    return [_Call(view, h, _call_args(view, h, WindowPlan("current"), ctx.scope, pick.args),
                  reference=pick.reference)
            for h in ref_hosts or [None]]


def _calls_named(ctx: _RunCtx, view: ViewSpec, plan: WindowPlan,
                 args: dict[str, Any] | None) -> list[_Call]:
    """이름 보기(plans/134 W3·W4) — 말한 이름 목록을 한 번에(해석은 게이트웨이).

    없으면 전체 1회 · 말한 이름이 모두 부를 수 없으면(길이 초과) 전체로 넓히지 않는다.
    """
    if ctx.named_given and not ctx.named_texts:
        return []
    named_args = _call_args(view, None, plan, ctx.scope, args)
    if ctx.named_texts:
        named_args[view.target_arg] = list(ctx.named_texts)
    return [_Call(view, None, named_args, named=list(ctx.named_texts) or None)]


def _calls_none(ctx: _RunCtx, view: ViewSpec, plan: WindowPlan,
                args: dict[str, Any] | None) -> list[_Call]:
    """전 대상 보기(V34-4) — `service` 조건이 있으면 말한 서비스·업무 이름으로 좁힌다.

    말한 서비스·업무 이름(인스턴스 이름 제외)은 `service`로 좁힌다(조건 `service`와 합친 목록 ·
    named 보기와 같은 규칙) · 말한 이름이 모두 부를 수 없으면(길이 초과) 전체로 넓히지 않는다.
    `service` 조건이 없으면 기본 구성(`_calls_host`)과 같다.
    """
    if not _service_scoped(view):
        return _calls_host(ctx, view, plan, args)
    if ctx.named_given and not ctx.named_texts:
        return []
    fleet_args = _call_args(view, None, plan, ctx.scope, args)
    names = _service_names((args or {}).get("service"), ctx.named_texts)
    if ctx.named_texts:
        fleet_args["service"] = names
    return [_Call(view, None, fleet_args, named=names or None)]


def _calls_optional(ctx: _RunCtx, view: ViewSpec, plan: WindowPlan,
                    args: dict[str, Any] | None) -> list[_Call]:
    """선택 대상 — 대상이 있으면 대상별 · 없으면 hostname 없이 1회(첫 홉 삽입 없음).

    소스 범위 kind(예 색상 경계)는 대상과 무관하게 1회(`targeted_choices`).
    """
    opt_hosts: list[str | None] = (
        list(ctx.scoped_hosts) if _targeted(view, args) else [])
    if ctx.texts and _targeted(view, args):
        # 대상 텍스트 — 대상별 + 해석 인스턴스별(대상 없이 1회로 넓히지 않는다)
        return ([_Call(view, h, _call_args(view, h, plan, ctx.scope, args)) for h in opt_hosts]
                + _instance_calls(view, plan, ctx.scope, args, ctx.instances))
    return [_Call(view, h, _call_args(view, h, plan, ctx.scope, args))
            for h in opt_hosts or [None]]


def _calls_host(ctx: _RunCtx, view: ViewSpec, plan: WindowPlan,
                args: dict[str, Any] | None) -> list[_Call]:
    """대상별 호출: 대상이 필요한 보기 · 대상으로 좁힐 수 있는 첫 홉 목록(인스턴스).

    대상이 없는 전체 보기(예 지표 목록)는 대상과 무관하게 한 번만 부른다(plans/134 W2).
    목록 질문의 해석 행 답은 `ctx.answers`에 싣는다.
    """
    calls: list[_Call] = []
    if view.required_input or (view.first_hop and ctx.hostnames):
        calls += _host_calls(view, ctx.hostnames, plan, ctx.scope, args, ctx.host_sources)
    elif not (ctx.texts and view.first_hop):
        calls.append(_Call(view, None, _call_args(view, None, plan, ctx.scope, args)))
    if ctx.texts and view.required_input:  # 해석 인스턴스마다 1호출(상한 없음 · D-296 ④)
        calls += _instance_calls(view, plan, ctx.scope, args, ctx.instances)
    elif ctx.texts and view.first_hop and ctx.instances:  # 목록 질문 — 해석 행이 답이다
        ctx.answers.append(_answer_call(
            view, ctx.instances, [r.text for r in ctx.resolved if r.instances]))
    if view.required_input and ctx.hostless:  # 첫 홉의 호스트 없는 인스턴스(M-5)
        calls += _instance_calls(view, plan, ctx.scope, args, ctx.hostless)
    return calls


#: 보기 `target`(`src/routing/registry.py` `VIEW_TARGETS`) → 호출 구성 함수. 알 수 없는 값은
#: 기본(`_calls_host`)으로 간다(종전 분기 사슬의 끝 분기). 함수 객체를 import 시점에 묶으므로
#: 테스트에서 구성 함수를 바꿔 끼우려면 모듈 속성이 아니라 이 표의 항목을 바꾼다
_CALL_BUILDERS = {
    "reference": _calls_reference,
    "named": _calls_named,
    "none": _calls_none,
    "optional": _calls_optional,
    "": _calls_host,
}
