"""plans/151 W0~W5 독립 검증 — 과금 차단 · 실험군 env 격리 · 반출 가림 (verifier).

통과 테스트는 우회 시도에 대한 현행 방어를 고정한다. `xfail(strict=True)`는 검증에서 찾은 결함을
재현한다 — 고치면 strict라 XPASS 실패로 드러나므로 그때 마커를 지운다. 실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from investigation_bench import export, preflight, runner
from investigation_bench.export import ExportLeakError, export_bundle
from sre_agent.application.investigation_dispatcher import INSTRUMENTATION_KEYS

_TOP = Path(__file__).resolve().parents[1]
_SPECS = _TOP / "testdata" / "investigation_bench" / "specs"


def _plane(model: str, api_base: str | None, *, site: str = "local",
           backend: str = "mlx") -> dict[str, Any]:
    return preflight.check_llm_plane(model=model, api_base=api_base, backend=backend,
                                     spec_site=site, cli_site=site)


# ─────────────────────────────── 과금 평면(§6.4 · D-324 ⑤) ───────────────────────────────


@pytest.mark.parametrize("model, api_base", [
    ("OpenAI/x", "http://127.0.0.1:8080/v1"),               # 프로바이더 대소문자 위장
    ("openai/gemini/gemini-2", None),                        # openai/ 위장 + api_base 없음
    ("openai/x", ""),                                        # 빈 api_base
    ("openai/x", "http://127.0.0.1@evil.example.com/v1"),   # 사용자 정보로 루프백 위장
    ("openai/x", "http://localhost.evil.example.com/v1"),   # localhost 접두 도메인
    ("openai/x", "http://0.0.0.0:8080/v1"),                  # 와일드카드 주소
    ("openai/x", "http://2130706433/v1"),                    # 정수 표기 IP
    ("openai/x", "http://10.1.2.3:8000/v1"),                 # 사설(local site)
    ("openai/x", "http://[fd00::1]:8000/v1"),                # IPv6 ULA(local site)
])
def test_local_site_rejects_disguised_planes(model: str, api_base: str | None) -> None:
    with pytest.raises(preflight.PreflightError) as e:
        _plane(model, api_base)
    assert e.value.exit_code == preflight.EXIT_REJECT


@pytest.mark.parametrize("api_base", [
    "http://[::1]:8080/v1", "http://127.0.0.2:8080/v1", "http://LOCALHOST:8080/v1",
])
def test_local_site_loopback_variants_allowed_as_mlx(api_base: str) -> None:
    out = _plane("openai/m", api_base)
    assert out["host_kind"] == "loopback" and out["backend_recorded"] == "mlx"
    assert out["billed_override"] is False


@pytest.mark.parametrize("api_base", [
    "https://generativelanguage.googleapis.com/v1",
    "https://API.ANTHROPIC.COM/v1",
    "http://[::ffff:8.8.8.8]/v1",                            # IPv4 매핑 공인 IP
    "http://8.8.8.8/v1",
])
def test_closed_site_still_rejects_public_planes(api_base: str) -> None:
    with pytest.raises(preflight.PreflightError):
        _plane("openai/m", api_base, site="closed", backend="vllm")


def test_closed_site_rejects_trailing_dot_public_domain() -> None:
    with pytest.raises(preflight.PreflightError):
        _plane("openai/m", "https://generativelanguage.googleapis.com./v1",
               site="closed", backend="vllm")


def test_closed_requires_both_spec_and_cli() -> None:
    with pytest.raises(preflight.PreflightError):
        preflight.check_llm_plane(model="openai/m", api_base="http://10.0.0.5:8000/v1",
                                  backend="vllm", spec_site="closed", cli_site="local")
    with pytest.raises(preflight.PreflightError):
        preflight.check_llm_plane(model="openai/m", api_base="http://10.0.0.5:8000/v1",
                                  backend="vllm", spec_site="local", cli_site="closed")


# ─────────────────────────────── 실험군 env 격리(§4.7) ───────────────────────────────


def _arm_env(tmp_path: Path, parent: dict[str, str] | None = None) -> dict[str, str]:
    spec, _ = runner.load_spec(_SPECS / "fake_e2e.yaml")
    arm = next(iter(spec["arms"]))
    return runner.build_arm_env(spec, arm, run_dir=tmp_path, polestar_url="http://127.0.0.1:1/sse",
                                apm_url=None, api_base="http://127.0.0.1:2/v1",
                                parent_env=parent or {})


def test_arm_env_drops_parent_llm_keys(tmp_path: Path) -> None:
    parent = {"PATH": "/usr/bin", "HOME": "/h", "GEMINI_API_KEY": "g", "LLM_GEMINI_API_KEY": "g",
              "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "OPENAI_API_BASE": "https://x",
              "MODEL": "gemini/gemini-3.5-flash", "API_BASE": "https://x", "HTTPS_PROXY": "http://p"}
    env = _arm_env(tmp_path, parent)
    assert env["GEMINI_API_KEY"] == "" and env["LLM_GEMINI_API_KEY"] == ""
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_API_BASE", "HTTPS_PROXY"):
        assert k not in env
    assert env["MODEL"] == "openai/fake-bench" and env["API_BASE"] == "http://127.0.0.1:2/v1"
    assert not any(m in k for k in runner.INHERITED_ENV_KEYS for m in runner.SECRET_MARKERS)


def test_arm_env_disables_litellm_dotenv(tmp_path: Path) -> None:
    assert _arm_env(tmp_path).get("LITELLM_MODE", "DEV") != "DEV"


def _ancestor_dotenv() -> Path | None:
    """litellm 설치 위치에서 위로 올라가며 처음 만나는 `.env`(litellm `find_dotenv`와 같은 탐색)."""
    import litellm

    d = Path(litellm.__file__).resolve().parent
    for p in (d, *d.parents):
        if (p / ".env").is_file():
            return p / ".env"
    return None


@pytest.mark.skipif(_ancestor_dotenv() is None, reason="litellm 상위에 .env가 없으면 재현 불가")
def test_arm_process_environment_is_only_runner_env(tmp_path: Path) -> None:
    """러너와 같은 `-m` 실행 · 빈 cwd · 러너 env로 sre_agent 조립 import 뒤 os.environ 증분."""
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "verify_env_probe.py").write_text(
        "import json, os\n"
        "before = set(os.environ)\n"
        "import sre_agent.interface.mcp_service  # noqa: F401 — _serve.py와 같은 import 사슬\n"
        "print(json.dumps(sorted(set(os.environ) - before)))\n", encoding="utf-8")
    cwd = tmp_path / "arm-control"
    cwd.mkdir()
    env = _arm_env(tmp_path, {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ})
    env["PYTHONPATH"] = f"{mods}{os.pathsep}{env['PYTHONPATH']}"
    out = subprocess.run([sys.executable, "-m", "verify_env_probe"], cwd=cwd, env=env,
                         capture_output=True, text=True, timeout=120, check=True)
    injected = set(json.loads(out.stdout.strip().splitlines()[-1]))
    dotenv_keys = {line.split("=", 1)[0].strip() for line in
                   _ancestor_dotenv().read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
                   if "=" in line and not line.lstrip().startswith("#")}
    assert not (injected & dotenv_keys), sorted(injected & dotenv_keys)


def test_runner_instrumentation_keys_match_dispatcher() -> None:
    """러너는 sre_agent를 import하지 않아 키 목록을 복사해 둔다 — 단일 출처와 어긋나지 않는가."""
    assert tuple(runner.INSTRUMENTATION_KEYS) == tuple(INSTRUMENTATION_KEYS)


# ─────────────────────────────── 반출 가림(§6.5 · D-324 ⑥) ───────────────────────────────


def _real_shape_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, api_key: str = "dummy",
                    venv: str | None = None, stderr: str = "ok\n") -> Path:
    """러너가 실제로 쓰는 manifest 모양(`spec`·`spec_text`·`versions`)의 최소 run."""
    monkeypatch.setattr(export.getpass, "getuser", lambda: "benchsvc")
    monkeypatch.setattr(export.socket, "gethostname", lambda: "gpu-node7.corp.internal")
    monkeypatch.setenv("HOME", "/home/benchsvc")
    run = tmp_path / "20261013-120000-closed"
    (run / "arm-control").mkdir(parents=True)
    spec_text = ("run: {site: closed, k: 1}\n"
                 "llm:\n  backend: vllm\n  model: openai/q\n"
                 "  api_base: http://127.0.0.1:8000/v1\n"
                 f"  api_key: {api_key}\n"
                 "arms: {control: {}}\n")
    manifest = {
        "run_id": run.name, "site": "closed", "backend": "vllm", "model": "openai/q",
        "api_base": "http://127.0.0.1:8000/v1",
        "spec": {"run": {"site": "closed", "k": 1},
                 "llm": {"backend": "vllm", "model": "openai/q",
                         "api_base": "http://127.0.0.1:8000/v1", "api_key": api_key},
                 "arms": {"control": {}}},
        "spec_text": spec_text,
        "versions": {"python": "3.13.1",
                     "venv": venv or str(export._REPO_ROOT / "sre_agent/.venv")},
    }
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (run / "raw.jsonl").write_text(json.dumps({"briefing": {"summary": "CPU 98%"}}) + "\n",
                                   encoding="utf-8")
    (run / "arm-control" / "stderr.log").write_text(stderr, encoding="utf-8")
    return run


def _bundle_text(bundle: Path) -> str:
    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_real_shape_run_exports_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = export_bundle(_real_shape_run(tmp_path, monkeypatch))
    text = _bundle_text(bundle)
    assert "CPU 98%" in text and "benchsvc" not in text


def test_uppercase_hostname_residual_masked(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    run = _real_shape_run(tmp_path, monkeypatch, stderr="peer GPU-NODE7.CORP.INTERNAL up\n")
    text = _bundle_text(export_bundle(run)).upper().replace("<REDACTED:HOST>", "")
    assert "GPU-NODE7" not in text


def test_spec_api_key_not_exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-vllm-Zq8w3Lr0bench"
    run = _real_shape_run(tmp_path, monkeypatch, api_key=secret)
    try:
        bundle = export_bundle(run)
    except ExportLeakError:
        return  # 묶음 거부도 허용(누출 관문)
    assert secret not in _bundle_text(bundle)


def test_internal_path_outside_home_and_repo_not_exported(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _real_shape_run(tmp_path, monkeypatch, venv="/opt/kbsre/bench/.venv")
    try:
        bundle = export_bundle(run)
    except ExportLeakError:
        return
    assert "/opt/kbsre" not in _bundle_text(bundle)


def test_private_ipv6_not_exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _real_shape_run(tmp_path, monkeypatch, stderr="connect fd12:3456:789a::15 refused\n")
    try:
        bundle = export_bundle(run)
    except ExportLeakError:
        return
    assert "fd12:3456:789a::15" not in _bundle_text(bundle)


def test_failed_export_leaves_no_temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _real_shape_run(tmp_path, monkeypatch)
    monkeypatch.setattr(export.Masker, "leaks", lambda self, text: ["강제"])
    with pytest.raises(ExportLeakError):
        export_bundle(run)
    assert not run.with_name(run.name + export.BUNDLE_SUFFIX).exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")], "임시 디렉터리 잔존"
    shutil.rmtree(run)
