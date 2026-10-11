"""plans/147 W4 — 배포 전 대조 CLI `scripts/apm_source_check.py`(D-322 ⑥).

레지스트리 `solutions[apm].sources[]` ↔ 게이트웨이 `.env` `JENNIFER_SOURCES`를 파일로 대조한다.
종료 코드 0 일치 · 1 불일치 · 2 파일·형식 오류 · 토큰·URL 값은 출력하지 않는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import apm_source_check as cli

REPO = Path(__file__).resolve().parents[2]
TOKEN = "tok-cli-secret-p147"
URL = "https://jennifer-secret.example.internal:7900"

_REGISTRY = """
zones:
  - {code: bankjon, label: 은행존}
  - {code: gongjon, label: 공동존}
solutions:
  - code: apm
    label: 제니퍼
    backend: mcp
    family: jennifer
    sources:
      - {id: bank, label: 은행존 제니퍼, zone: bankjon, terms: ["은행존", "은행"]}
      - {id: common, label: 공동존 제니퍼, zone: gongjon, terms: ["공동존", "김포"]}
"""


def _env_text(ids: list[str], *, skip: tuple[str, ...] = ()) -> str:
    lines = ["# 주석", f"JENNIFER_SOURCES={json.dumps(ids)}", "JENNIFER_API_URL=",
             "JENNIFER_API_TOKEN="]
    for sid in ids:
        for suffix, value in (("API_URL", URL), ("API_TOKEN", TOKEN)):
            key = f"JENNIFER_{sid.upper()}_{suffix}"
            lines.append(f"{key}=" if key in skip else f"{key}={value}")
    return "\n".join(lines) + "\n"


@pytest.fixture
def registry(tmp_path) -> Path:
    path = tmp_path / "db_registry.yaml"
    path.write_text(_REGISTRY, encoding="utf-8")
    return path


def _run(tmp_path: Path, registry: Path, env_text: str, capsys) -> tuple[int, str]:
    env = tmp_path / "gw.env"
    env.write_text(env_text, encoding="utf-8")
    code = cli.main(["--gateway-env", str(env), "--registry", str(registry)])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_consistent_exit_0(tmp_path, registry, capsys) -> None:
    code, out = _run(tmp_path, registry, _env_text(["bank", "common"]), capsys)
    assert code == 0, out
    assert "결과: 일치" in out and "은행존 제니퍼" in out
    assert TOKEN not in out and URL not in out


def test_registry_only_exit_1(tmp_path, registry, capsys) -> None:
    code, out = _run(tmp_path, registry, _env_text(["bank"]), capsys)
    assert code == 1
    assert "레지스트리만" in out and "common" in out


def test_gateway_only_exit_1(tmp_path, registry, capsys) -> None:
    code, out = _run(tmp_path, registry, _env_text(["bank", "common", "drsite"]), capsys)
    assert code == 1
    assert "게이트웨이만" in out and "drsite" in out


def test_missing_required_key_exit_1_without_values(tmp_path, registry, capsys) -> None:
    code, out = _run(tmp_path, registry,
                     _env_text(["bank", "common"], skip=("JENNIFER_COMMON_API_TOKEN",)), capsys)
    assert code == 1
    assert "JENNIFER_COMMON_API_TOKEN" in out and "필수 키 누락" in out
    assert TOKEN not in out and URL not in out


def test_both_config_styles_is_problem(tmp_path, registry, capsys) -> None:
    text = _env_text(["bank", "common"]).replace("JENNIFER_API_URL=\n",
                                                 f"JENNIFER_API_URL={URL}\n")
    code, out = _run(tmp_path, registry, text, capsys)
    assert code == 1 and "함께 씀" in out and URL not in out


def test_single_style_is_default_source(tmp_path, registry, capsys) -> None:
    text = f"JENNIFER_API_URL={URL}\nJENNIFER_API_TOKEN={TOKEN}\n"
    code, out = _run(tmp_path, registry, text, capsys)
    assert code == 1
    assert "default" in out and "게이트웨이만" in out and "레지스트리만" in out


def test_bad_json_and_missing_file_exit_2(tmp_path, registry, capsys) -> None:
    code, out = _run(tmp_path, registry, "JENNIFER_SOURCES=[bank\n", capsys)
    assert code == 2 and "JSON" in out
    code = cli.main(["--gateway-env", str(tmp_path / "nope.env"), "--registry", str(registry)])
    assert code == 2


def test_token_values_are_not_kept(tmp_path) -> None:
    """`.env` 해석 결과에는 `JENNIFER_SOURCES` 원문 외의 값이 남지 않는다."""
    p = tmp_path / ".env"
    p.write_text(_env_text(["bank"]), encoding="utf-8")
    parsed = cli.read_gateway_env(p)
    assert TOKEN not in repr(parsed) and URL not in repr(parsed)
    assert parsed.has("JENNIFER_BANK_API_TOKEN") and not parsed.has("JENNIFER_API_URL")


def test_cli_process_exit_code(tmp_path, registry) -> None:
    env = tmp_path / "gw.env"
    env.write_text(_env_text(["bank"]), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "apm_source_check.py"),
                           "--gateway-env", str(env), "--registry", str(registry)],
                          capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert proc.returncode == 1, proc.stderr
    assert "common" in proc.stdout and TOKEN not in proc.stdout + proc.stderr
