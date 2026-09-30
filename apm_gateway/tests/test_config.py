"""설정 — `.env.example` 커버리지 · 기본값 · 폴링 하한 · `.env` 비덮어쓰기 (plans/87 §7.0 · Known
Mistakes).
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

from apm_gateway import config as config_mod
from apm_gateway.config import describe, load_config, load_dotenv

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


# ── 다중 소스 설정 (plans/87 J8 · D-287 ③) ─────────────────────


def _multi(**extra: str) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["bank", "legacy"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": "tok-bank",
        "JENNIFER_LEGACY_API_URL": "http://legacy.test",
        "JENNIFER_LEGACY_API_TOKEN": "tok-legacy",
        **extra,
    }


def test_env_example_documents_source_key_suffixes():
    text = (GATEWAY / ".env.example").read_text(encoding="utf-8")
    for suffix in config_mod.SOURCE_KEY_SUFFIXES:
        assert f"JENNIFER_<ID>_{suffix}" in text, suffix


def test_single_setting_is_source_default_and_unset_is_zero_sources():
    cfg = load_config({"JENNIFER_API_URL": "http://one.test", "JENNIFER_DOMAIN_IDS": "[1000]"})
    assert [(s.source_id, s.url, s.domain_ids) for s in cfg.sources] == [
        ("default", "http://one.test", (1000,))
    ]
    assert load_config({"JENNIFER_API_TOKEN": "t"}).sources == ()


def test_multi_sources_in_declared_order_with_global_fallback():
    cfg = load_config(
        _multi(
            JENNIFER_API_TIMEOUT_SECONDS="4",
            JENNIFER_LEGACY_API_TIMEOUT_SECONDS="2",
            JENNIFER_LEGACY_DOMAIN_IDS="[3000]",
        )
    )
    assert [s.source_id for s in cfg.sources] == ["bank", "legacy"]
    bank, legacy = cfg.sources
    assert (bank.url, bank.token, bank.timeout_seconds, bank.domain_ids) == (
        "http://bank.test",
        "tok-bank",
        4.0,
        (),
    )
    assert (legacy.timeout_seconds, legacy.domain_ids, legacy.rate_limit_per_sec) == (
        2.0,
        (3000,),
        5.0,
    )


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        (_multi(JENNIFER_API_URL="http://one.test"), "함께 쓸 수 없다"),
        (_multi(JENNIFER_API_TOKEN="t"), "함께 쓸 수 없다"),
        (_multi(JENNIFER_LEGACY_API_URL=""), "JENNIFER_LEGACY_API_URL"),
        (_multi(JENNIFER_BANK_API_TOKEN=""), "JENNIFER_BANK_API_TOKEN"),
        (_multi(JENNIFER_SOURCES='["bank", "bank"]'), "중복"),
        (_multi(JENNIFER_SOURCES='["Bank"]'), "소문자 슬러그"),
        (_multi(JENNIFER_SOURCES='["default"]'), "예약어"),
        (_multi(JENNIFER_SOURCES='["api"]'), "예약어"),
        (_multi(JENNIFER_SOURCES='"bank"'), "JSON 배열"),
    ],
)
def test_invalid_source_settings_fail_startup(env, needle):
    with pytest.raises(ValueError) as exc:
        load_config(env)
    assert needle in str(exc.value)


def test_describe_has_source_ids_but_no_secret_values():
    text = str(describe(load_config(_multi())))
    assert "'id': 'bank'" in text and "'id': 'legacy'" in text
    for secret in ("tok-bank", "tok-legacy", "bank.test", "legacy.test"):
        assert secret not in text


def test_unknown_policy_source_warns(tmp_path, caplog):
    (tmp_path / "instance_map.yaml").write_text(
        "overrides: [{source_id: nope, instance_name: x, hostname: h}]\n"
        "per_source: {ghost: {match_rules: []}}\n",
        encoding="utf-8",
    )
    load_config(_multi(), policy_dir=tmp_path)
    assert "['ghost', 'nope']" in caplog.text
