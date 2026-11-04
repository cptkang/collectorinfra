"""용도별 LLM 평면의 과금 판정·MLX 점검 (plans/212 W3 · D-365 · D-127 우회 차단).

역할 provider(`LLM_ROUTER_PROVIDER` 등)가 과금 평면이면 워커·오케스트레이터가 둘 다 mlx 여도
하네스가 승인을 요구해야 한다. 판정 정의는 `scripts.scenario.preflight.external_planes` 1곳이다.

설정은 `_env_file=None` 으로 명시 생성해 `.env` 누수를 막는다. `LLM_PROVIDER` 환경변수는 덮지 않는다
(OS env 가 `_env_file` 을 이긴다 - 대신 init 인자로 넘긴다).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scripts.scenario import preflight as pf

W_URL = "http://127.0.0.1:8080/v1"
R_URL = "http://127.0.0.1:8081/v1"


def _app_config(**llm: Any) -> Any:
    """워커·오케스트레이터 mlx(8080) + 지정한 역할 항목. `.env` 를 읽지 않는다."""
    from src.config import AppConfig, LLMConfig, OrchestratorConfig

    llm_kwargs: dict[str, Any] = {"provider": "mlx", "mlx_base_url": W_URL, "mlx_model": ""}
    llm_kwargs.update(llm)
    return AppConfig(
        llm=LLMConfig(_env_file=None, **llm_kwargs),
        orchestrator=OrchestratorConfig(_env_file=None, provider="mlx", base_url=W_URL,
                                        model="default_model"),
    )


# --- 판정 정의 ----------------------------------------------------------------

@pytest.mark.parametrize("roles,external", [
    (None, []),
    ({}, []),
    ({"router": "mlx", "planner": "vllm", "answer": "fabrix"}, []),
    ({"router": "ollama"}, []),
    ({"router": "gemini"}, ["LLM_ROUTER_PROVIDER=gemini"]),
    ({"planner": "gemini", "answer": "gemini"},
     ["LLM_PLANNER_PROVIDER=gemini", "LLM_ANSWER_PROVIDER=gemini"]),
    ({"router": ""}, []),  # 빈 값 = 적지 않음(워커 승계) - 워커 판정이 덮는다
    ({pf.ROLES_UNREADABLE: "unknown(ImportError)"}, ["LLM_ROLES_PROVIDER=unknown(ImportError)"]),
])
def test_role_planes_use_worker_internal_set(roles, external) -> None:
    assert pf.external_planes("mlx", "mlx", roles=roles) == external


def test_role_planes_follow_worker_and_orchestrator() -> None:
    assert pf.external_planes("gemini", "gemini", roles={"router": "gemini"}) == [
        "LLM_PROVIDER=gemini", "ORCHESTRATOR_PROVIDER=gemini", "LLM_ROUTER_PROVIDER=gemini"]


def test_role_providers_returns_explicit_roles_only() -> None:
    cfg = _app_config(router_provider="gemini", planner_base_url=R_URL)
    assert pf.role_providers(cfg) == {"router": "gemini"}
    assert pf.role_providers(_app_config()) == {}


def test_config_without_role_fields_has_no_role_planes() -> None:
    """역할 도입 전 형태 · 테스트 대역(SimpleNamespace) - 기존 판정과 비트 동일."""
    cfg = SimpleNamespace(llm=SimpleNamespace(provider="mlx"),
                          orchestrator=SimpleNamespace(provider="mlx"))
    assert pf.role_providers(cfg) == {}
    for worker, orch in (("mlx", "mlx"), ("mlx", "gemini"), ("gemini", "vllm")):
        assert (pf.external_planes(worker, orch, roles=pf.role_providers(cfg))
                == pf.external_planes(worker, orch))


def test_unreadable_role_settings_are_external() -> None:
    def boom(_role: str) -> object:
        raise RuntimeError("설정 없음")

    roles = pf.role_providers_from(boom)
    assert roles == {pf.ROLES_UNREADABLE: "unknown(RuntimeError)"}
    assert pf.external_planes("mlx", "mlx", roles=roles)


def test_role_names_come_from_factory() -> None:
    from src.llm import LLM_ROLES

    assert pf.llm_roles() == tuple(LLM_ROLES)


# --- --preflight 행 -----------------------------------------------------------

def test_unset_role_rows_are_worker_inherit_ok() -> None:
    report = pf.Report()
    pf._check_provider(_app_config(), report)
    rows = {c.key: c for c in report.checks}
    assert [c.key for c in report.checks] == [
        "LLM_PROVIDER", "ORCHESTRATOR_PROVIDER",
        "LLM_ROUTER_PROVIDER", "LLM_PLANNER_PROVIDER", "LLM_ANSWER_PROVIDER"]
    for role in ("ROUTER", "PLANNER", "ANSWER"):
        row = rows[f"LLM_{role}_PROVIDER"]
        assert row.verdict == pf.VERDICT_OK and row.observed == "워커 승계"
    assert not report.stops


def test_billed_role_plane_stops_with_approval_action() -> None:
    report = pf.Report()
    pf._check_provider(_app_config(router_provider="gemini", planner_provider="vllm"), report)
    rows = {c.key: c for c in report.checks}
    assert rows["LLM_PROVIDER"].verdict == pf.VERDICT_OK
    assert rows["ORCHESTRATOR_PROVIDER"].verdict == pf.VERDICT_OK
    router = rows["LLM_ROUTER_PROVIDER"]
    assert router.verdict == pf.VERDICT_STOP and router.observed == "gemini"
    assert "D-127" in router.action and "RUN_E2E=1" in router.action
    assert rows["LLM_PLANNER_PROVIDER"].verdict == pf.VERDICT_OK
    assert rows["LLM_ANSWER_PROVIDER"].observed == "워커 승계"
    assert [c.key for c in report.stops] == ["LLM_ROUTER_PROVIDER"]
    text = pf.format_report(report)
    assert "판정: 중단" in text
    for ch in ("—", "–", "‘", "’", "“", "”"):
        assert ch not in text


def test_role_provider_from_shell_names_source(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ROUTER_PROVIDER", "gemini")
    report = pf.Report()
    pf._check_provider(_app_config(router_provider="gemini"), report)
    router = {c.key: c for c in report.checks}["LLM_ROUTER_PROVIDER"]
    assert router.source == "os" and "unset LLM_ROUTER_PROVIDER" in router.action


# --- 소비처: 승인 요구 경로가 실제로 막힌다 -----------------------------------

def test_scenario_run_refuses_billed_role_without_optin(monkeypatch) -> None:
    import src.config
    from scripts.scenario import __main__ as cli

    cfg = _app_config(router_provider="gemini")
    monkeypatch.setattr(src.config, "load_config", lambda: cfg)
    monkeypatch.delenv("RUN_E2E", raising=False)
    monkeypatch.setattr(cli, "execute", lambda *_: pytest.fail("승인 없이 실행했다"))
    assert cli.llm_providers() == ("mlx", "mlx")
    assert cli.llm_role_providers() == {"router": "gemini"}
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run"])
    assert exc.value.code == 2


def test_scenario_unreadable_roles_are_external(monkeypatch) -> None:
    import src.config
    from scripts.scenario import __main__ as cli

    def boom():
        raise RuntimeError("설정 없음")

    monkeypatch.setattr(src.config, "load_config", boom)
    roles = cli.llm_role_providers()
    assert pf.external_planes("mlx", "mlx", roles=roles)


def _echo(**extra: str) -> Any:
    from scripts.bench import probe

    return probe.EchoResult(ok=True, config={"llm.provider": "mlx", "orchestrator.provider": "mlx",
                                             **extra})


def test_bench_approval_policy_requires_approval_for_billed_role() -> None:
    from scripts.bench import __main__ as cli

    roles = cli._role_providers_of(_echo(**{"llm.router_provider": "gemini",
                                            "llm.planner_provider": ""}))
    assert roles == {"router": "gemini"}
    need, reason = cli.approval_policy("mlx", "mlx", roles=roles)
    assert need is True and "LLM_ROUTER_PROVIDER=gemini" in reason
    need, reason = cli.approval_policy("mlx", "mlx", roles={"router": "vllm"})
    assert need is False and "역할 router vllm" in reason
    # 역할 미설정 - 종전 사유 문구 그대로
    assert cli.approval_policy("mlx", "mlx", roles={}) == cli.approval_policy("mlx", "mlx")


def test_bench_sweep_run_stops_for_billed_role(monkeypatch, capsys) -> None:
    from scripts.bench import __main__ as cli

    monkeypatch.setattr(cli.probe, "echo_config",
                        lambda *a, **k: _echo(**{"llm.router_provider": "gemini"}))
    monkeypatch.setattr(cli, "mlx_run_blockers", lambda: pytest.fail("승인 전에 MLX 점검까지 갔다"))
    monkeypatch.setattr(cli.sweep_mod, "run_arms",
                        lambda *a, **k: pytest.fail("승인 없이 arm 을 돌렸다"))
    rc = cli.main(["--sweep", "--mode", "run", "--scale", "smoke"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "LLM_ROUTER_PROVIDER=gemini" in out and "--yes" in out


def test_bench_show_env_prints_role_rows(monkeypatch, capsys) -> None:
    from scripts.bench import __main__ as cli

    monkeypatch.setattr(cli.probe, "echo_config",
                        lambda *a, **k: _echo(**{"llm.planner_provider": "vllm"}))
    assert cli.cmd_show_env(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "역할 router  : 워커 승계" in out
    assert "역할 planner : vllm" in out
    assert "역할 answer  : 워커 승계" in out


def test_eval_routing_local_mlx_mode_requires_loopback_mlx_roles() -> None:
    from scripts import eval_routing

    ok, reason = eval_routing.local_mlx_mode(_app_config(router_provider="gemini"))
    assert not ok and "과금" in reason
    vllm = _app_config(router_provider="vllm", router_model="m",
                       router_base_url="http://10.0.0.5:8000/v1")
    ok, _ = eval_routing.local_mlx_mode(vllm)
    assert not ok
    remote = _app_config(router_provider="mlx", router_base_url="http://10.0.0.5:8081/v1")
    ok, reason = eval_routing.local_mlx_mode(remote)
    assert not ok and "루프백" in reason
    local = _app_config(router_provider="mlx", router_base_url=R_URL)
    ok, reason = eval_routing.local_mlx_mode(local)
    assert ok, reason


def test_itam_bench_preflight_blocks_billed_role() -> None:
    from scripts.itam_bench import __main__ as itam
    from scripts.itam_bench.build_assets import p2_billing_refusal

    cfg = _app_config(router_provider="gemini")
    reasons = itam.preflight(cfg, check_mlx=False)
    assert any("LLM_ROUTER_PROVIDER=gemini" in r for r in reasons)
    assert "LLM_ROUTER_PROVIDER=gemini" in (p2_billing_refusal(cfg) or "")
    assert p2_billing_refusal(_app_config()) is None


def test_apm_bench_replay_llm_refuses_billed_role() -> None:
    from scripts.apm_bench import loop

    with pytest.raises(loop.ReplayBlockedError, match="LLM_ROUTER_PROVIDER=gemini"):
        loop.make_mlx_llm(_app_config(router_provider="gemini"))


def test_apm_bench_e1_starts_nothing_for_billed_body_role(monkeypatch) -> None:
    from pathlib import Path

    from scripts.apm_bench import driver as dv

    placeholders = dv.load_placeholders(
        Path(__file__).resolve().parents[1] / "testdata/apm_bench/placeholders.e1.yaml")
    monkeypatch.setattr(dv, "_body_roles", lambda: {"router": "gemini"})
    with pytest.raises(dv.LLMBlockedError, match="LLM_ROUTER_PROVIDER=gemini"):
        dv.run([], env="e1", placeholders=placeholders, say=lambda _t: None)


def test_apm_bench_unreadable_body_roles_are_external(monkeypatch) -> None:
    import src.config
    from scripts.apm_bench import driver as dv

    def boom():
        raise RuntimeError("설정 없음")

    monkeypatch.setattr(src.config, "load_config", boom)
    assert pf.external_planes("mlx", "mlx", roles=dv._body_roles())


# --- MLX 점검: 서로 다른 base_url 마다 1회 ------------------------------------

def _count_mlx_calls(monkeypatch, models: tuple[str, ...] = ()) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"fetch": [], "probe": []}

    def fake_fetch(base_url: str, timeout: float = 3.0):
        calls["fetch"].append(base_url)
        return list(models), ""

    def fake_probe(base_url: str, timeout: float = 30.0):
        calls["probe"].append(base_url)
        return True, ""

    monkeypatch.setattr(pf, "_fetch_mlx_models", fake_fetch)
    monkeypatch.setattr(pf, "_probe_mlx_generation", fake_probe)
    return calls


def test_role_mlx_on_other_port_checks_each_server_once(monkeypatch) -> None:
    calls = _count_mlx_calls(monkeypatch)
    report = pf.Report()
    pf._check_mlx(_app_config(router_provider="mlx", router_base_url=R_URL), report)
    assert calls["fetch"] == [W_URL, R_URL]
    assert calls["probe"] == [W_URL, R_URL]
    keys = [c.key for c in report.checks]
    assert "MLX 서버(역할 router)" in keys and "MLX 생성(역할 router)" in keys
    assert not report.stops


def test_role_with_endpoint_only_inherits_worker_mlx(monkeypatch) -> None:
    """provider 를 비우면 워커 provider(mlx)를 승계한다 - 엔드포인트만 바꾼 역할도 점검 대상이다."""
    calls = _count_mlx_calls(monkeypatch)
    pf._check_mlx(_app_config(planner_base_url=R_URL), pf.Report())
    assert calls["fetch"] == [W_URL, R_URL] and calls["probe"] == [W_URL, R_URL]


def test_non_mlx_role_is_not_checked(monkeypatch) -> None:
    calls = _count_mlx_calls(monkeypatch)
    pf._check_mlx(_app_config(router_provider="vllm", router_base_url="http://10.0.0.5:8000/v1",
                              router_model="m"), pf.Report())
    assert calls["fetch"] == [W_URL] and calls["probe"] == [W_URL]


def test_unset_roles_keep_mlx_checks_unchanged(monkeypatch) -> None:
    calls = _count_mlx_calls(monkeypatch)
    report = pf.Report()
    pf._check_mlx(_app_config(), report)
    assert calls["fetch"] == [W_URL] and calls["probe"] == [W_URL]
    assert [c.key for c in report.checks] == [
        "MLX 서버(워커)", "MLX 모델(워커)", "MLX 생성(워커)",
        "MLX 서버(오케스트레이터)", "MLX 모델(오케스트레이터)", "MLX 생성(오케스트레이터)"]


def test_run_blockers_report_unreachable_role_server(monkeypatch) -> None:
    def fake_fetch(base_url: str, timeout: float = 3.0):
        return (None, "ConnectionError") if base_url == R_URL else ([], "")

    monkeypatch.setattr(pf, "_fetch_mlx_models", fake_fetch)
    monkeypatch.setattr(pf, "_probe_mlx_generation", lambda base_url, timeout=30.0: (True, ""))
    blockers = pf.mlx_run_blockers(_app_config(router_provider="mlx", router_base_url=R_URL))
    assert [c.key for c in blockers] == ["MLX 서버(역할 router)"]


def test_role_with_other_model_on_same_server_warns_reload(monkeypatch) -> None:
    _count_mlx_calls(monkeypatch, models=("a/model-1", "b/model-2"))
    report = pf.Report()
    pf._check_mlx(_app_config(mlx_model="a/model-1", router_model="b/model-2"), report)
    reload_checks = [c for c in report.checks if c.key == "MLX 재적재"]
    assert len(reload_checks) == 1
    check = reload_checks[0]
    assert check.verdict == pf.VERDICT_WARN and check.observed == "a/model-1 / b/model-2"
    assert "역할 router" in check.action and "LLM_<역할>_BASE_URL" in check.action


def test_same_model_or_other_server_has_no_reload_warning(monkeypatch) -> None:
    _count_mlx_calls(monkeypatch, models=("a/model-1", "b/model-2"))
    report = pf.Report()
    pf._check_mlx(_app_config(mlx_model="a/model-1", router_model="a/model-1"), report)
    assert "MLX 재적재" not in {c.key for c in report.checks}
    report = pf.Report()
    pf._check_mlx(_app_config(mlx_model="a/model-1", router_provider="mlx",
                              router_base_url=R_URL, router_model="b/model-2"), report)
    assert "MLX 재적재" not in {c.key for c in report.checks}


# --- 워커 교체(WORKER_PROVIDER_OVERRIDE · 테스트 전용) ---------------------------

def _override_config(override: str | None, **llm: Any) -> Any:
    cfg = _app_config(**llm)
    cfg.worker_provider_override = override
    return cfg


@pytest.mark.parametrize("override,external", [
    ("gemini", ["WORKER_PROVIDER_OVERRIDE=gemini"]),
    ("fabrix", []),
    ("ollama", []),
    (None, []),
])
def test_worker_override_is_judged_with_worker_internal_set(override, external) -> None:
    roles = pf.role_providers(_override_config(override))
    assert pf.external_planes("mlx", "mlx", roles=roles) == external


def test_worker_override_preflight_row_only_when_set() -> None:
    report = pf.Report()
    pf._check_provider(_override_config(None), report)
    assert "WORKER_PROVIDER_OVERRIDE" not in {c.key for c in report.checks}

    report = pf.Report()
    pf._check_provider(_override_config("gemini"), report)
    row = {c.key: c for c in report.checks}["WORKER_PROVIDER_OVERRIDE"]
    assert row.verdict == pf.VERDICT_STOP and "D-127" in row.action
    assert [c.key for c in report.stops] == ["WORKER_PROVIDER_OVERRIDE"]
    assert [c.key for c in report.checks if c.key.startswith("LLM_")] == [
        "LLM_PROVIDER", "LLM_ROUTER_PROVIDER", "LLM_PLANNER_PROVIDER", "LLM_ANSWER_PROVIDER"]

    report = pf.Report()
    pf._check_provider(_override_config("fabrix"), report)
    assert {c.key: c for c in report.checks}["WORKER_PROVIDER_OVERRIDE"].verdict == pf.VERDICT_OK


def test_worker_override_reaches_scenario_and_bench_gates(monkeypatch) -> None:
    import src.config
    from scripts.bench import __main__ as bench
    from scripts.scenario import __main__ as scenario

    cfg = _override_config("gemini")
    monkeypatch.setattr(src.config, "load_config", lambda: cfg)
    monkeypatch.delenv("RUN_E2E", raising=False)
    monkeypatch.setattr(scenario, "execute", lambda *_: pytest.fail("승인 없이 실행했다"))
    with pytest.raises(SystemExit) as exc:
        scenario.main(["--run"])
    assert exc.value.code == 2

    roles = bench._role_providers_of(_echo(worker_provider_override="gemini"))
    need, reason = bench.approval_policy("mlx", "mlx", roles=roles)
    assert need is True and "WORKER_PROVIDER_OVERRIDE=gemini" in reason
    need, reason = bench.approval_policy("mlx", "mlx",
                                         roles=bench._role_providers_of(
                                             _echo(worker_provider_override="fabrix")))
    assert need is False and "워커 교체 fabrix" in reason
