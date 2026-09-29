"""설정 — `.env.example` 커버리지 · 기본값 · 폴링 하한 · `.env` 비덮어쓰기 (plans/87 §7.0 · Known
Mistakes).
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

from apm_gateway import config as config_mod
from apm_gateway.config import load_config, load_dotenv

GATEWAY = Path(__file__).resolve().parents[1]
_DOCUMENTED = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", re.MULTILINE)
_READ = re.compile(r'get\("([A-Z][A-Z0-9_]{2,})"\)')


def test_env_example_covers_every_key_read():
    """`load_config`가 읽는 키를 소스에서 뽑아 `.env.example`과 대조한다(목록 복제 금지 —
    mcp_server 전례)."""
    read = set(_READ.findall(inspect.getsource(config_mod.load_config)))
    documented = set(_DOCUMENTED.findall((GATEWAY / ".env.example").read_text(encoding="utf-8")))
    assert read and read - documented == set()
    assert documented - read == set()


def test_env_example_has_no_inline_comments():
    for line in (GATEWAY / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            assert "#" not in line.split("=", 1)[1], line


def test_defaults():
    cfg = load_config({})
    assert cfg.jennifer.url == "" and cfg.server.port == 9096 and cfg.server.host == "127.0.0.1"
    assert cfg.poller.enabled is False and cfg.poller.stream_key == "alarm:raw"
    assert cfg.policies.level_severity["fatal"] == 3 and cfg.policies.unknown_level_severity == 2
    assert cfg.policies.instance_map["match_rules"][0]["kind"] == "host_name"


def test_poll_interval_floor_and_domain_ids():
    cfg = load_config(
        {"APM_EVENT_POLL_INTERVAL_SECONDS": "3", "JENNIFER_DOMAIN_IDS": "[1000, 2000]"}
    )
    assert cfg.poller.interval_seconds == 10
    assert cfg.jennifer.domain_ids == (1000, 2000)


def test_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APM_TEST_KEY_X=from_file\nAPM_TEST_KEY_Y=file_y\n", encoding="utf-8")
    monkeypatch.setenv("APM_TEST_KEY_X", "from_env")
    monkeypatch.delenv("APM_TEST_KEY_Y", raising=False)
    load_dotenv(env)
    assert os.environ["APM_TEST_KEY_X"] == "from_env" and os.environ["APM_TEST_KEY_Y"] == "file_y"
    monkeypatch.delenv("APM_TEST_KEY_Y")
