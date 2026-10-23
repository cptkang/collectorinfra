"""plans/191 독립 검증(verifier) — 설정 관리 API의 계약·경계 사례.

xfail(strict=True)는 검증에서 찾은 결함의 재현이다. 고쳐지면 XPASS가 실패로 드러나니 마커를 지운다.
실 `.env`·`.encenv` 누수를 막기 위해 모든 테스트는 tmp_path를 CWD·패키지 루트로 쓰고
필드를 명시한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sre_agent.interface.config_admin import (
    ConfigAdminService,
    build_admin_app,
    parse_env_lines,
    render_env,
)
from sre_agent.interface.config_admin_adapter import build_adapter
from sre_agent.settings import AgentSettings
from starlette.testclient import TestClient

ADMIN = "admin-tok"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
_SRC = Path(__file__).resolve().parents[1]


def _settings() -> AgentSettings:
    return AgentSettings(_env_file=None, model="m", max_steps=40, service_bearer_token=None)


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("MAX_STEPS", "BASH_WRITE_GUARD_ENABLED", "MODEL"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text("MAX_STEPS=40\n", encoding="utf-8")
    return tmp_path


def _adapter(root: Path):
    return build_adapter(_settings(), cwd=root, package_root=root, audit_path=root / "a.jsonl")


# ── 공용 모듈 경계 사례(통과해야 하는 현행 동작) ─────────────────────────


def test_parse_handles_crlf_export_and_quotes():
    lines = "A=1\r\nexport B=two\r\nC=\"q v\"\r\n# D=4\r\n  E = 5 \r\n".splitlines()
    parsed = {k: v[-1][1] for k, v in parse_env_lines(lines).items()}
    assert parsed == {"A": "1", "B": "two", "C": "q v", "E": "5"}


def test_render_reset_only_line_gives_empty_file_and_appends_new_key():
    assert render_env(["A=1"], {}, {"A"}) == ""
    assert render_env([], {"B": "2"}, set()) == "B=2\n"
    assert render_env(["# A=0", "X=1"], {"A": "3"}, set()) == "# A=0\nX=1\nA=3\n"


def test_missing_write_target_is_created_with_0600(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAX_STEPS", raising=False)
    client = TestClient(build_admin_app(_adapter(tmp_path), ADMIN))
    resp = client.put("/admin/config", json={"settings": {"MAX_STEPS": "41"}}, headers=AUTH)
    assert resp.status_code == 200, resp.text
    target = tmp_path / ".env"
    assert target.read_text(encoding="utf-8") == "MAX_STEPS=41\n"
    assert oct(target.stat().st_mode & 0o777) == oct(0o600)
    assert not (tmp_path / ".env.bak").exists()


# ── 결함 재현 ───────────────────────────────────────────────────────


def test_schema_default_and_effective_are_env_strings(pkg):
    body = ConfigAdminService(_adapter(pkg)).schema()
    bad = [
        (i["env_key"], i["default"], i["effective_value"])
        for g in body["groups"]
        for i in g["settings"]
        if not isinstance(i["default"], (str, type(None)))
        or not isinstance(i["effective_value"], (str, type(None)))
    ]
    assert bad == []


def test_put_errors_carry_message_field_for_ui(pkg):
    client = TestClient(build_admin_app(_adapter(pkg), ADMIN))
    resp = client.put("/admin/config", json={"settings": {"MAX_STEPS": "abc"}}, headers=AUTH)
    assert resp.status_code == 400
    assert resp.json()["errors"][0].get("message")


_PROBE = textwrap.dedent(
    """
    import os
    from pathlib import Path
    before = "MAX_STEPS" in os.environ
    import sre_agent.interface.mcp_service  # run_service와 같은 import 경로(holmes → litellm)
    from sre_agent.interface.config_admin_adapter import build_adapter
    from sre_agent.settings import AgentSettings
    root = Path.cwd()
    adapter = build_adapter(AgentSettings(), cwd=root, package_root=root,
                            audit_path=root / "a.jsonl")
    print(before, adapter.override_for("MAX_STEPS"))
    """
)


def test_dotenv_keys_are_not_flagged_override_os_in_real_layout(tmp_path):
    """표준 배치 재현: 패키지 루트 = CWD, `.venv`가 패키지 루트 안, 비대화형 스크립트 기동."""
    (tmp_path / ".env").write_text("MAX_STEPS=41\n", encoding="utf-8")
    (tmp_path / ".venv").symlink_to(sys.prefix)
    script = tmp_path / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MAX_STEPS"}
    env["PYTHONPATH"] = str(_SRC)
    out = subprocess.run(
        [str(tmp_path / ".venv" / "bin" / "python"), str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    before, override = out.stdout.split()[-2:]
    assert before == "False"
    assert override == "None"
