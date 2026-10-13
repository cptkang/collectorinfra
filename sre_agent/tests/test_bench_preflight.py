"""plans/151 W3 — 사전 점검(§6.4 · §6.7 · §9.2 preflight 행).

실 LLM 0 · 외부 네트워크 0. LLM 엔드포인트는 `httpx.MockTransport`로 흉내 내고, 포트 검사는
127.0.0.1 임의 포트만 쓴다.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from investigation_bench import preflight, runner
from investigation_bench.preflight import PreflightError

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "investigation_bench"
_SPECS = Path(__file__).resolve().parents[1] / "testdata" / "investigation_bench" / "specs"


def _plane(model: str = "openai/served", api_base: str | None = "http://127.0.0.1:8080/v1", *,
           backend: str = "mlx", spec_site: str = "local", cli_site: str = "local",
           test: bool = False, allow_billed: str | None = None) -> dict[str, Any]:
    return preflight.check_llm_plane(model=model, api_base=api_base, backend=backend,
                                     spec_site=spec_site, cli_site=cli_site, test=test,
                                     allow_billed=allow_billed)


def _fake_spec(tmp_path: Path) -> Path:
    """`fake_e2e.yaml` 사본 — 포트는 0(빈 포트)으로 바꿔 병렬 실행에 안전하게."""
    path = tmp_path / "fake_e2e.yaml"
    text = (_SPECS / "fake_e2e.yaml").read_text(encoding="utf-8")
    path.write_text(text.replace(
        "ports: {fake_llm: 19180, world: 19190, world_apm: 19191, arm_base: 19200}",
        "ports: {fake_llm: 0, world: 0, world_apm: 0, arm_base: 0}"), encoding="utf-8")
    return path


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "run": {"site": "local", "k": 3},
        "llm": {"backend": "mlx", "model": "openai/served", "api_base": "http://127.0.0.1:8080/v1"},
        "base_env": {"MAX_STEPS": 40},
        "arms": {"control": {}, "control_b": {}},
    }
    for key, value in over.items():
        spec[key] = {**spec.get(key, {}), **value}
    return spec


# ── 과금 평면 ──


@pytest.mark.parametrize("api_base", ["http://127.0.0.1:8080/v1", "http://localhost:8080/v1"])
def test_openai_loopback_allowed_and_local_records_mlx(api_base: str) -> None:
    out = _plane(api_base=api_base)
    assert out["backend_recorded"] == "mlx" and out["host_kind"] == "loopback"
    assert out["allow_billed"] is None


@pytest.mark.parametrize("backend", ["vllm", "fabrix_proxy"])
def test_closed_loopback_records_spec_backend(backend: str) -> None:
    """`--site closed`의 127.0.0.1은 MLX가 아니라 명세 backend로 기록된다."""
    out = _plane(api_base="http://127.0.0.1:8000/v1", backend=backend,
                 spec_site="closed", cli_site="closed")
    assert out["backend_recorded"] == backend


@pytest.mark.parametrize("api_base", ["http://10.1.2.3:8000/v1", "http://192.168.0.5:8000/v1",
                                      "http://gpu-server:8000/v1"])
def test_private_or_internal_host_only_when_both_closed(api_base: str) -> None:
    for spec_site, cli_site in (("local", "local"), ("closed", "local"), ("local", "closed")):
        with pytest.raises(PreflightError) as exc:
            _plane(api_base=api_base, backend="vllm", spec_site=spec_site, cli_site=cli_site)
        assert exc.value.exit_code == 2
    out = _plane(api_base=api_base, backend="vllm", spec_site="closed", cli_site="closed")
    assert out["backend_recorded"] == "vllm"


@pytest.mark.parametrize("model, api_base", [
    ("gemini/gemini-3.5-flash", "http://127.0.0.1:8080/v1"),
    ("anthropic/claude-sonnet-5", "http://127.0.0.1:8080/v1"),
    ("gemini/gemini-3.5-flash", None),
    ("openai/served", None),
    ("openai/gpt-x", "https://api.openai.com/v1"),
    ("openai/served", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("openai/served", "https://api.anthropic.com/v1"),
    ("openai/served", "http://8.8.8.8:8000/v1"),
])
def test_billed_planes_rejected_exit_2(model: str, api_base: str | None) -> None:
    for site in ("local", "closed"):
        with pytest.raises(PreflightError) as exc:
            _plane(model, api_base, backend="vllm", spec_site=site, cli_site=site)
        assert exc.value.exit_code == 2


def test_allow_billed_records_approval_phrase() -> None:
    """`--allow-billed "<승인 문구>"`는 차단을 풀고 문구를 그대로 남긴다(D-127 건별 승인)."""
    phrase = "2026-10-13 사용자 승인: Gemini 1회"
    out = _plane("gemini/gemini-3.5-flash", None, allow_billed=phrase)
    assert out["allow_billed"] == phrase and out["billed_override"] is True


def test_allow_billed_phrase_lands_in_manifest(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """승인 문구가 manifest에 남는다 — 실행 단계는 막아 프로세스를 띄우지 않는다."""
    spec_path = _fake_spec(tmp_path)

    def _stop(*_: Any, **__: Any) -> Any:
        raise runner.RunnerError("테스트: 기동 전 중단")

    monkeypatch.setattr(runner, "materialize_ports", _stop)
    phrase = "테스트 승인 문구"
    rc = runner.run(runner.RunOptions(spec_path=spec_path, test=True, allow_billed=phrase,
                                      out_root=tmp_path / "out"))
    assert rc == 1
    (manifest_path,) = (tmp_path / "out").glob("*/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["allow_billed"] == phrase
    assert manifest["preflight"]["static"]["llm_plane"]["allow_billed"] == phrase


# ── 명세 ──


def test_backend_required_and_known() -> None:
    for llm in ({"backend": None}, {"backend": "gemini"}):
        spec = _spec(llm=llm)
        if llm["backend"] is None:
            del spec["llm"]["backend"]
        with pytest.raises(PreflightError) as exc:
            preflight.check_spec(spec, cli_site="local", test=False)
        assert exc.value.exit_code == 2


def test_fake_backend_requires_test_flag() -> None:
    spec = _spec(llm={"backend": "fake"})
    with pytest.raises(PreflightError) as exc:
        preflight.check_spec(spec, cli_site="local", test=False)
    assert exc.value.exit_code == 2
    preflight.check_spec(spec, cli_site="local", test=True)
    # 가짜 LLM 포트를 가리키는 api_base도 --test 없이는 거부 — 승인 문구로도 안 풀린다
    with pytest.raises(PreflightError):
        _plane(api_base=f"http://127.0.0.1:{preflight.FAKE_LLM_PORT}/v1",
               allow_billed="승인")
    assert _plane(api_base=f"http://127.0.0.1:{preflight.FAKE_LLM_PORT}/v1", backend="fake",
                  test=True)["backend_recorded"] == "fake"


def test_site_must_match_spec() -> None:
    with pytest.raises(PreflightError) as exc:
        preflight.check_spec(_spec(run={"site": "local"}), cli_site="closed", test=False)
    assert exc.value.exit_code == 2


def test_fabrix_expected_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """fabrix_proxy면 예상 호출 수(세계 × k × 실험군 × MAX_STEPS)를 출력한다."""
    out = preflight.static_checks(
        _spec(llm={"backend": "fabrix_proxy", "api_base": "http://127.0.0.1:9095/v1"},
              run={"site": "closed"}),
        cli_site="closed", test=False, allow_billed=None, api_base="http://127.0.0.1:9095/v1",
        ports=[0], n_worlds=24)
    assert out["expected_llm_calls"] == 24 * 3 * 2 * 40
    # --dry-run 경로에서 실제로 출력되는지(프로세스 0)
    spec_path = tmp_path / "fabrix.yaml"
    text = (_SPECS / "closed_round1_fabrix.yaml").read_text(encoding="utf-8")
    text = text.replace("worlds_dir: testdata/investigation_bench/worlds",
                        "worlds_dir: tests/fixtures/investigation_bench/worlds\n"
                        "  snapshot: tests/fixtures/investigation_bench/tool_schemas.json")
    text = text.replace("splits: [dev, holdout]", "splits: [dev]")
    text = text.replace("per_run_timeout_s: 420\n",
                        "per_run_timeout_s: 420\n  ports: {world: 0, world_apm: 0, arm_base: 0}\n")
    spec_path.write_text(text, encoding="utf-8")
    rc = runner.run(runner.RunOptions(spec_path=spec_path, cli_site="closed", dry_run=True))
    assert rc == 0
    assert f"예상 FabriX 호출 상한: {2 * 3 * 2 * 40}" in capsys.readouterr().out


# ── 포트 ──


def test_busy_bench_port_aborts() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        with pytest.raises(PreflightError, match="사용 중"):
            preflight.check_ports([0, busy])
    assert preflight.check_ports([0])["planned"] == []


# ── LLM 엔드포인트 ──


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def test_models_serving_info_collected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "qwen", "object": "model", "max_model_len": 32768}]})

    out = preflight.fetch_models("http://127.0.0.1:8000/v1", "dummy", _client(handler))
    assert out["models"] == ["qwen"] and out["max_model_len"] == 32768


def test_tool_call_roundtrip_pass_and_fail() -> None:
    seen: list[dict[str, Any]] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion({"role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "bench_ping",
                                         "arguments": "{\"value\":\"ok\"}"}}]}))

    out = preflight.tool_call_roundtrip("http://127.0.0.1:8000/v1", "openai/qwen", "dummy",
                                        _client(ok))
    assert out["ok"] is True and out["tool"] == "bench_ping"
    assert seen[0]["model"] == "qwen" and seen[0]["tools"][0]["function"]["name"] == "bench_ping"

    def text_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion({"role": "assistant", "content": "ok"}))

    with pytest.raises(PreflightError, match="tool_calls"):
        preflight.tool_call_roundtrip("http://127.0.0.1:8000/v1", "openai/qwen", "dummy",
                                      _client(text_only))

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(PreflightError):
        preflight.tool_call_roundtrip("http://127.0.0.1:8000/v1", "openai/qwen", "dummy",
                                      _client(down))


def test_roundtrip_failure_stops_before_run(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """tool-calling 왕복 실패 = 세계 서버·sre_agent를 띄우기 전에 중단."""
    spec_path = _fake_spec(tmp_path)

    def fail(**_: Any) -> Any:
        raise PreflightError("tool-calling 왕복 실패 — 테스트")

    monkeypatch.setattr(preflight, "live_checks", fail)
    rc = runner.run(runner.RunOptions(spec_path=spec_path, test=True, out_root=tmp_path / "out"))
    assert rc == 1
    (run_dir,) = (tmp_path / "out").iterdir()
    names = [p["name"] for p in json.loads((run_dir / "pids.json").read_text())["processes"]]
    assert names == ["fake_llm"]  # 세계 서버·실험군은 뜨지 않았다
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "preflight_failed"
    assert not (run_dir / "raw.jsonl").exists()


# ── 호스트 · 버전 ──


def test_gpu_not_collected_without_nvidia_smi() -> None:
    def no_run(*_: Any, **__: Any) -> Any:
        raise AssertionError("nvidia-smi가 없으면 실행하지 않는다")

    info = preflight.gpu_info(which=lambda _: None, run=no_run)
    assert info == {"status": preflight.GPU_NOT_COLLECTED}
    assert preflight.host_info(which=lambda _: None, run=no_run)["gpu"]["status"] == "미수집"


def test_gpu_info_with_nvidia_smi() -> None:
    class _Out:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(cmd: list[str], **_: Any) -> _Out:
        if "--query-gpu=index,name,driver_version,memory.total" in cmd:
            return _Out("0, NVIDIA H100, 550.54, 81559\n")
        return _Out("1234, python, 70000\n")

    info = preflight.gpu_info(which=lambda _: "/usr/bin/nvidia-smi", run=fake_run)
    assert info["status"] == "ok"
    assert info["gpus"] == [{"index": "0", "name": "NVIDIA H100", "driver": "550.54",
                             "memory_total_mib": "81559"}]
    assert info["processes"][0]["pid"] == "1234"


def test_versions_reported() -> None:
    out = preflight.versions()
    assert {"python", "venv", "holmesgpt", "litellm", "mcp"} <= set(out)


def test_no_process_env_reads_in_preflight() -> None:
    """판정은 명세·설정 필드 이름으로만.

    소스에 `os.getenv`·`os.environ`이 없다(2026-08-28 실수 이력).
    """
    text = (_SCRIPTS / "preflight.py").read_text(encoding="utf-8")
    assert not re.search(r"os\.getenv|os\.environ|getenv\(", text)


def test_port_in_use_ignores_time_wait() -> None:
    """리스너가 있으면 사용 중 · 서버 쪽이 먼저 끊어 TIME_WAIT만 남으면 빈 포트(회차 연속 실행)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    assert preflight.port_in_use(port)
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = listener.accept()
    conn.close()  # 서버 쪽 먼저 종료 → 서버 포트에 TIME_WAIT
    client.close()
    listener.close()
    assert not preflight.port_in_use(port)


# ── 호스트 판별 보강(verify 2026-10-13) ──


def _closed(api_base: str, resolve: Any) -> dict[str, Any]:
    return preflight.check_llm_plane(model="openai/m", api_base=api_base, backend="vllm",
                                     spec_site="closed", cli_site="closed", resolve=resolve)


def _no_dns(host: str) -> list[str]:
    raise AssertionError(f"DNS를 부르면 안 된다: {host}")


@pytest.mark.parametrize("api_base", ["https://generativelanguage.googleapis.com./v1",
                                      "https://API.OPENAI.COM./v1"])
def test_trailing_dot_public_domain_rejected(api_base: str) -> None:
    with pytest.raises(PreflightError) as exc:
        _closed(api_base, _no_dns)
    assert exc.value.exit_code == 2


def test_closed_fqdn_allowed_only_when_resolving_private() -> None:
    out = _closed("http://vllm.corp.internal:8000/v1", lambda h: ["10.20.30.40"])
    assert out["host_kind"] == "internal" and out["backend_recorded"] == "vllm"
    for addrs in (["8.8.8.8"], ["10.0.0.1", "8.8.4.4"], []):  # 공인 섞임 · 해석 실패
        with pytest.raises(PreflightError) as exc:
            _closed("http://llm.example.net:8000/v1", lambda h, a=addrs: a)
        assert exc.value.exit_code == 2


def test_single_label_host_needs_no_dns_and_local_never_resolves() -> None:
    assert _closed("http://gpu-server:8000/v1", _no_dns)["host_kind"] == "internal"
    with pytest.raises(PreflightError):  # local site는 해석 없이 거부
        preflight.check_llm_plane(model="openai/m", api_base="http://vllm.corp.internal/v1",
                                  backend="vllm", spec_site="local", cli_site="local",
                                  resolve=_no_dns)
