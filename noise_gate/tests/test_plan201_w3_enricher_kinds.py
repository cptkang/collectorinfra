"""plans/201 W3 교정 2 — 상위 프로세스 종류(kind)를 마스킹·절단 **전** 원 항목으로 판정한다.

- 스냅샷 `ProcessInfo.args`는 `mask_args`로 120자 절단돼 Java WAS 단서가 사라진다. enricher는 같은 정렬로 고른
  원 항목을 주입 판정기에 넘기고, 결과 kind 이름만 상태 키 `top_process_kinds`에 싣는다.
- 판정기 미주입(= 담당 강조 off)이면 반환 키 집합·스냅샷·webhook payload가 종전과 같다.
- 원문 cmdline은 상태·SSE·로그 어디에도 남지 않는다. 판정기 예외는 값 생략(빈 목록)으로 흡수한다.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_context_enricher import (
    alarm_context_enricher_node,
    enrich_processes,
)
from noise_gate.application.nodes.alarm_notifier import _process_payload, alarm_notifier_node
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.notification_policy import TIER_DASHBOARD, NotificationDecision
from noise_gate.domain.process_rank import (
    classify_top_process_kinds,
    mask_args,
    rank_raw_processes,
    select_top_processes,
)
from noise_gate.tests.test_alarm_enricher import FakeRepo, make_alarm_cfg
from noise_gate.tests.test_alarm_pattern import make_event
from noise_gate.tests.test_alarm_process_enrich import FakeProcessClient, _proc_raw, _result

# 앞 120자 안에는 WAS 단서가 없다(JVM 옵션이 길다) — 폐쇄망 실 cmdline 형태.
_SECRET_MARK = "s3cr3t-cmdline-marker"
_TOMCAT = (
    "/usr/lib/jvm/jre/bin/java "
    "-Djava.util.logging.config.file=/opt/tomcat/conf/logging.properties "
    "-Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager -Xms2g -Xmx2g "
    f"-Dmarker={_SECRET_MARK} -Dcatalina.base=/opt/tomcat -Dcatalina.home=/opt/tomcat "
    "-classpath /opt/tomcat/bin/bootstrap.jar org.apache.catalina.startup.Bootstrap start"
)


def _procs() -> list[dict]:
    return [
        _proc_raw("java", 11, p100cpu=90.0, args=_TOMCAT),
        _proc_raw("db2sysc", 12, p100cpu=50.0, args="db2sysc 0"),
        _proc_raw("sshd", 13, p100cpu=1.0, args="/usr/sbin/sshd -D"),
    ]


def _real_classifier():
    from src.domain.middleware import classify_process_kind

    return classify_process_kind


def _cfg(**kw):
    return SimpleNamespace(alarm=make_alarm_cfg(**kw))


def _config(client, classifier=None, cfg=None) -> dict:
    configurable = {"app_config": cfg or _cfg(), "history_repo": FakeRepo(entries=[]),
                    "history_redis": None, "process_client": client}
    if classifier is not None:
        configurable["process_kind_classifier"] = classifier
    return {"configurable": configurable}


def _cpu_event():
    return make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")


# ─── 실 shape — 절단 스냅샷은 kind를 잃어도 새 경로는 tomcat을 잡는다 ──────────────


def test_masked_snapshot_loses_kind_but_raw_path_keeps_it() -> None:
    clf = _real_classifier()
    top, _ = select_top_processes(_procs(), "cpu", 5)
    assert top[0].args == mask_args(_TOMCAT) and len(top[0].args) <= 123
    assert clf(top[0].name, top[0].args) != "tomcat"                 # 종전 경로(마스킹 값) 미탐
    raw_top, total = rank_raw_processes(_procs(), "cpu", 5)
    assert total == 3 and [it["pid"] for it in raw_top] == [p.pid for p in top]   # 같은 정렬·선별
    assert classify_top_process_kinds(raw_top, clf) == ["tomcat", "db2"]


def test_rank_raw_processes_same_order_as_snapshot_memory_and_top_n() -> None:
    procs = [_proc_raw(f"p{i}", i, pmem=float(i % 4), p100cpu=float(10 - i)) for i in range(8)]
    for kind in ("cpu", "memory"):
        top, total = select_top_processes(procs, kind, 3)
        raw_top, raw_total = rank_raw_processes(procs, kind, 3)
        assert [p.pid for p in top] == [it["pid"] for it in raw_top] and total == raw_total
    assert rank_raw_processes(None, "cpu", 3) == ([], 0)                    # type: ignore[arg-type]
    assert rank_raw_processes(procs, "cpu", 0) == ([], 8)


def test_classify_top_process_kinds_unique_capped() -> None:
    items = [{"name": f"n{i}", "args": ""} for i in range(9)]
    assert classify_top_process_kinds(items, lambda n, a: f"k-{n}") == ["k-n0", "k-n1", "k-n2", "k-n3", "k-n4"]
    assert classify_top_process_kinds(items, lambda n, a: "same") == ["same"]
    assert classify_top_process_kinds(items, lambda n, a: None) == []


async def test_node_on_puts_kinds_state_key_from_raw_items() -> None:
    out = await alarm_context_enricher_node(
        {"alarm_event": _cpu_event()}, _config(FakeProcessClient(result=_result(procs=_procs())),
                                               _real_classifier()))
    assert out["top_process_kinds"] == ["tomcat", "db2"]
    snap = out["process_snapshot"]
    assert snap is not None and snap.top[0].args == mask_args(_TOMCAT)        # 스냅샷은 종전 그대로(마스킹)


# ─── off = 비트 동일 ───────────────────────────────────────────────────────────


async def test_node_off_keys_and_snapshot_identical() -> None:
    def run_cfg():
        return _config(FakeProcessClient(result=_result(procs=_procs())))

    off = await alarm_context_enricher_node({"alarm_event": _cpu_event()}, run_cfg())
    assert list(off) == ["history_stats", "process_snapshot"]
    base = await enrich_processes(_cpu_event(), make_alarm_cfg(), FakeProcessClient(result=_result(procs=_procs())))
    on = await alarm_context_enricher_node(
        {"alarm_event": _cpu_event()},
        _config(FakeProcessClient(result=_result(procs=_procs())), _real_classifier()))
    # 스냅샷은 on/off 모두 같다(필드 추가 없음 · repr·eq 동일) — webhook 프로세스 payload도 같다
    assert off["process_snapshot"] == base == on["process_snapshot"]
    assert repr(off["process_snapshot"]) == repr(on["process_snapshot"])
    assert _process_payload(off["process_snapshot"]) == _process_payload(on["process_snapshot"])
    assert set(on) - set(off) == {"top_process_kinds"}


async def test_node_on_non_target_or_failure_gives_empty_list_not_stale() -> None:
    clf = _real_classifier()
    # 비대상(디스크) 알람 — 스냅샷 None · 키는 빈 목록(직전 값이 남지 않게 명시)
    disk = make_event(resource_type="server.FileSystem", alarm_name="디스크")
    out = await alarm_context_enricher_node({"alarm_event": disk},
                                            _config(FakeProcessClient(result=_result(procs=_procs())), clf))
    assert out["process_snapshot"] is None and out["top_process_kinds"] == []
    # 조회 실패
    out = await alarm_context_enricher_node({"alarm_event": _cpu_event()},
                                            _config(FakeProcessClient(error=RuntimeError("down")), clf))
    assert out["process_snapshot"] is None and out["top_process_kinds"] == []
    # 타임아웃
    out = await alarm_context_enricher_node(
        {"alarm_event": _cpu_event()},
        _config(FakeProcessClient(result=_result(procs=_procs()), delay=1.6), clf,
                cfg=_cfg(enrich_timeout_seconds=1)))
    assert out["process_snapshot"] is None and out["top_process_kinds"] == []


async def test_classifier_exception_drops_value_keeps_snapshot(caplog) -> None:
    def boom(name: str, args: str) -> str | None:
        raise RuntimeError(args)        # 예외 메시지에 원문이 실려도 로그로 새지 않아야 한다

    with caplog.at_level(logging.DEBUG):
        out = await alarm_context_enricher_node(
            {"alarm_event": _cpu_event()}, _config(FakeProcessClient(result=_result(procs=_procs())), boom))
    assert out["process_snapshot"] is not None and out["top_process_kinds"] == []
    assert any("담당 강조 프로세스 종류 판정 실패" in r.getMessage() for r in caplog.records)
    assert all(_SECRET_MARK not in r.getMessage() and not r.exc_info for r in caplog.records)


# ─── 원문 미노출 — 상태·SSE·로그 ─────────────────────────────────────────────


async def test_raw_cmdline_never_in_state_sse_or_logs(caplog, monkeypatch) -> None:
    async def fake(*_a, **_k):
        return None

    monkeypatch.setattr(notifier_mod, "_send_workb", fake)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake)
    with caplog.at_level(logging.DEBUG):
        out = await alarm_context_enricher_node(
            {"alarm_event": _cpu_event()},
            _config(FakeProcessClient(result=_result(procs=_procs())), _real_classifier()))
        assert _SECRET_MARK not in repr(out) and "catalina.home" not in repr(out)

        published: list[dict] = []

        class _Bus:
            async def publish(self, payload: dict) -> None:
                published.append(payload)

        duty = SimpleNamespace(enabled=True, alarm_focus_enabled=True, alarm_focus_effective=True)
        app_cfg = SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace(), duty_area=duty)
        result = AlarmAnalysisResult(alarm_event=_cpu_event(), severity_label="경고", summary="s",
                                     probable_cause="c", recommended_action="a", notification_channels=[])
        state = {"analysis_result": result, "process_snapshot": out["process_snapshot"],
                 "top_process_kinds": out["top_process_kinds"],
                 "notification_decision": NotificationDecision(tier=TIER_DASHBOARD, reason="r", priority=1,
                                                               signals={}, fingerprint="fp")}
        await alarm_notifier_node(state, {"configurable": {"app_config": app_cfg, "alarm_bus": _Bus()}})
    assert published and published[0]["top_process_kinds"] == ["tomcat", "db2"]
    blob = json.dumps(published[0], ensure_ascii=False, default=str)
    assert _SECRET_MARK not in blob and "catalina.home" not in blob
    assert all(_SECRET_MARK not in r.getMessage() for r in caplog.records)


def test_alarm_state_declares_kinds_key() -> None:
    """LangGraph는 TypedDict에 없는 키를 노드에 넘기지 않는다 — 선언 필수."""
    from noise_gate.orchestration.alarm_graph import AlarmState

    assert "top_process_kinds" in AlarmState.__annotations__
