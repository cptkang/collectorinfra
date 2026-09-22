"""OpenMetrics 설정 파싱 테스트 (plans/92 §4.7 · §4.3 (a) · D-210).

``[openmetrics]`` TOML 파싱, 허용목록 ``_parse_openmetrics_targets``의 무효 항목 건너뛰기,
env 오버라이드(불리언·정수), ``fallback_policy`` 정규화를 고정한다. 설정 모듈은 ``mcp``를
임포트하지 않으므로 skip 없이 돈다.
"""

import logging

import pytest
from mcp_server.config import (
    AppServerConfig,
    OpenMetricsConfig,
    OpenMetricsTarget,
    _apply_env_overrides,
    _load_toml,
    _parse_openmetrics_targets,
)

_OM_ENV_KEYS = (
    "EXPOSE_OPENMETRICS_TOOLS",
    "EXPOSE_POLESTAR_EXPORTER",
    "OPENMETRICS_SCRAPE_TIMEOUT",
    "OPENMETRICS_MAX_BODY_BYTES",
    "OPENMETRICS_MAX_SERIES",
    "OPENMETRICS_FALLBACK_POLICY",
    "OPENMETRICS_COVERAGE_TTL_SECONDS",
    "OPENMETRICS_CROSS_CHECK_TOLERANCE",
    "OPENMETRICS_SCRAPE_INTERVAL_HINT",
    "OPENMETRICS_BRIDGE_CACHE_SECONDS",
)


@pytest.fixture
def clean_env(monkeypatch):
    """OpenMetrics env 키를 비워 외부 환경 누수를 막는다."""
    for key in _OM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_are_off():
    om = OpenMetricsConfig()
    assert om.expose_openmetrics_tools is False
    assert om.expose_polestar_exporter is False
    assert om.targets == []
    assert (om.scrape_timeout, om.max_body_bytes, om.max_series) == (10, 4 * 1024 * 1024, 200)
    assert om.fallback_policy == "off"
    assert isinstance(AppServerConfig().openmetrics, OpenMetricsConfig)


def test_parse_targets_keeps_valid_and_strips():
    targets = _parse_openmetrics_targets([
        {"hostname": " web-01 ", "url": " http://exporter.test:9100/metrics ",
         "os_hostname": " host-a01 "},
        {"hostname": "prom-view", "url": "https://prom.test", "kind": "federate"},
    ])
    assert targets == [
        OpenMetricsTarget(hostname="web-01", url="http://exporter.test:9100/metrics",
                          kind="exporter", os_hostname="host-a01"),
        OpenMetricsTarget(hostname="prom-view", url="https://prom.test", kind="federate"),
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"url": "http://exporter.test/metrics"},                       # hostname 없음
        {"hostname": "web-01"},                                         # url 없음
        {"hostname": " ", "url": "http://exporter.test/metrics"},       # 공백 hostname
        {"hostname": "web-01", "url": "file:///etc/passwd"},            # http(s) 아님
        {"hostname": "web-01", "url": "ftp://exporter.test/metrics"},
        {"hostname": "web-01", "url": "http://exporter.test/metrics", "kind": "push"},
    ],
)
def test_parse_targets_skips_invalid_entry_with_warning(entry, caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.config"):
        assert _parse_openmetrics_targets([entry]) == []
    assert "OpenMetrics 타깃 무시" in caplog.text


def test_parse_targets_duplicate_hostname_first_wins(caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.config"):
        targets = _parse_openmetrics_targets([
            {"hostname": "web-01", "url": "http://first.test/metrics"},
            {"hostname": "web-01", "url": "http://second.test/metrics"},
        ])
    assert [t.url for t in targets] == ["http://first.test/metrics"]
    assert "중복 hostname" in caplog.text


def test_toml_openmetrics_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[openmetrics]\n'
        'expose_openmetrics_tools = true\n'
        'scrape_timeout = 5\n'
        'max_body_bytes = 1024\n'
        'max_series = 50\n'
        '[[openmetrics.targets]]\n'
        'hostname = "web-01"\n'
        'url = "http://exporter.test:9100/metrics"\n'
        '[[openmetrics.targets]]\n'
        'hostname = "bad"\n'
        'url = "gopher://x"\n',
        encoding="utf-8",
    )
    om = _load_toml(path).openmetrics
    assert om.expose_openmetrics_tools is True
    assert (om.scrape_timeout, om.max_body_bytes, om.max_series) == (5, 1024, 50)
    assert [t.hostname for t in om.targets] == ["web-01"]


def test_toml_without_section_keeps_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\nname = "x"\n', encoding="utf-8")
    om = _load_toml(path).openmetrics
    assert om.expose_openmetrics_tools is False
    assert om.targets == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("YES", True), (" on ", True),
     ("false", False), ("0", False), ("off", False)],
)
def test_env_boolean_flags(clean_env, raw, expected):
    clean_env.setenv("EXPOSE_OPENMETRICS_TOOLS", raw)
    clean_env.setenv("EXPOSE_POLESTAR_EXPORTER", raw)
    config = AppServerConfig()
    _apply_env_overrides(config)
    assert config.openmetrics.expose_openmetrics_tools is expected
    assert config.openmetrics.expose_polestar_exporter is expected


def test_env_boolean_off_overrides_toml_on(clean_env):
    clean_env.setenv("EXPOSE_OPENMETRICS_TOOLS", "false")
    config = AppServerConfig(openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True))
    _apply_env_overrides(config)
    assert config.openmetrics.expose_openmetrics_tools is False


def test_env_numeric_overrides(clean_env):
    clean_env.setenv("OPENMETRICS_SCRAPE_TIMEOUT", "3")
    clean_env.setenv("OPENMETRICS_MAX_BODY_BYTES", "2048")
    clean_env.setenv("OPENMETRICS_MAX_SERIES", "20")
    clean_env.setenv("OPENMETRICS_CROSS_CHECK_TOLERANCE", "0.1")
    config = AppServerConfig()
    _apply_env_overrides(config)
    om = config.openmetrics
    assert (om.scrape_timeout, om.max_body_bytes, om.max_series) == (3, 2048, 20)
    assert om.cross_check_tolerance == pytest.approx(0.1)


def test_env_absent_keeps_values(clean_env):
    config = AppServerConfig(openmetrics=OpenMetricsConfig(max_series=7))
    _apply_env_overrides(config)
    assert config.openmetrics.max_series == 7
    assert config.openmetrics.expose_openmetrics_tools is False


@pytest.mark.parametrize(("raw", "expected"), [("ON_EMPTY", "on_empty"),
                                               (" on_unavailable ", "on_unavailable"),
                                               ("off", "off")])
def test_fallback_policy_normalized(clean_env, raw, expected):
    clean_env.setenv("OPENMETRICS_FALLBACK_POLICY", raw)
    config = AppServerConfig()
    _apply_env_overrides(config)
    assert config.openmetrics.fallback_policy == expected


def test_fallback_policy_invalid_falls_back_to_off_with_warning(clean_env, caplog):
    clean_env.setenv("OPENMETRICS_FALLBACK_POLICY", "always")
    config = AppServerConfig()
    with caplog.at_level(logging.WARNING, logger="mcp_server.config"):
        _apply_env_overrides(config)
    assert config.openmetrics.fallback_policy == "off"
    assert "OPENMETRICS_FALLBACK_POLICY" in caplog.text
