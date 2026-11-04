"""plans/211 W3 — 게이트웨이 호스트 정규형 비교 (§3.1·§3.2·§5 · D-363).

- 사본(`domain.host_canonical`)이 §5 벡터 표를 그대로 만족한다(정본 `src/domain/host_names.py`와의
  동등성은 각 패키지가 같은 표를 단언하는 것으로 확인한다).
- 정합기: 플래그 off = 종전 비교(비트 동일) · on = 정규형 비교
  (`override`·`host_name`·`regex`·`exact`).
- 플래그 `APM_HOST_CANONICAL_MATCH_ENABLED` 기본 off · 기동 시 정합기에 전달.
외부 접속 0.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from apm_gateway.application.resolver import HIGH, MEDIUM, NONE, InstanceResolver
from apm_gateway.application.sources import build_source_set
from apm_gateway.domain.host_canonical import (
    canonical_host_key,
    host_keys_match,
    split_business_suffix,
)

from apm_gateway.config import load_config

# ── §5 벡터 표(정본) ─────────────────────────────────────

MATCH_VECTORS = [
    ("WEBAP01", "webap01", True),
    ("web-ap01", "web_ap01", True),
    ("webap01.corp.local", "webap01", True),
    ("webap01(인터넷뱅킹)", "webap01", True),
    ("webap01 (인터넷뱅킹)", "WEB-AP01.corp.local", True),
    ("webap01.a.local", "webap01.b.local", False),
    ("webap01", "webap010", False),
    ("10.1.2.3", "10.1.2.3", True),
]


@pytest.mark.parametrize(("a", "b", "expected"), MATCH_VECTORS)
def test_vector_match(a: str, b: str, expected: bool) -> None:
    assert host_keys_match(a, b) is expected
    assert host_keys_match(b, a) is expected


def test_vector_business_suffix() -> None:
    assert split_business_suffix("webap01(인터넷뱅킹)") == ("webap01", "인터넷뱅킹")
    assert split_business_suffix("webap01 (인터넷뱅킹)") == ("webap01", "인터넷뱅킹")
    assert split_business_suffix("webap01") == ("webap01", "")


def test_vector_business_only_is_not_a_key() -> None:
    assert canonical_host_key("(인터넷뱅킹)") == ""
    assert host_keys_match("(인터넷뱅킹)", "아무거나") is False
    assert host_keys_match("(인터넷뱅킹)", "webap01") is False


def test_vector_wildcard_is_not_a_key() -> None:
    assert canonical_host_key("web%01") == ""
    assert host_keys_match("web%01", "web%01") is False


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("WEBAP01.", "webap01"),
        ("web..ap01", ""),
        ("-", ""),
        ("", ""),
        (None, ""),
        ("WEB-AP01.Corp.Local", "webap01.corp.local"),
        ("10.1.2.3", "10.1.2.3"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
    ],
)
def test_vector_canonical_key(value: str | None, key: str) -> None:
    assert canonical_host_key(value) == key


def test_ip_never_matches_by_first_label() -> None:
    assert host_keys_match("10.1.2.3", "10.1.2.4") is False
    assert host_keys_match("10.1.2.3", "10") is False


# ── 정합기 on/off ───────────────────────────────────────


def _inst(name: str, host: str, iid: int = 1) -> dict[str, Any]:
    return {
        "domain_id": 1000,
        "instance_id": iid,
        "instance_name": name,
        "host_name": host,
        "source_id": "default",
    }


def _resolver(instance_map: dict[str, Any], *, on: bool) -> InstanceResolver:
    return InstanceResolver(None, instance_map, canonical_match=on)  # type: ignore[arg-type]


HOST_NAME_RULES = {"match_rules": [{"kind": "host_name"}]}
EXACT_RULES = {"match_rules": [{"kind": "exact"}]}


def test_default_is_off() -> None:
    res = InstanceResolver(None, HOST_NAME_RULES)  # type: ignore[arg-type]
    found, _, _ = res.match("webap01.b.local", [_inst("was1", "webap01.a.local")])
    assert found  # 종전 비교: 짧은 이름이 같으면 같다


def test_host_name_fqdn_other_domain_off_matches_on_does_not() -> None:
    instances = [_inst("was1", "webap01.a.local")]
    found, conf, reason = _resolver(HOST_NAME_RULES, on=False).match("webap01.b.local", instances)
    assert [i["instance_id"] for i in found] == [1] and (conf, reason) == (HIGH, "host_name")
    found, conf, reason = _resolver(HOST_NAME_RULES, on=True).match("webap01.b.local", instances)
    assert (found, conf, reason) == ([], NONE, "no_match")


def test_host_name_hyphen_underscore() -> None:
    instances = [_inst("was1", "web_ap01")]
    assert _resolver(HOST_NAME_RULES, on=False).match("web-ap01", instances)[0] == []
    found, conf, reason = _resolver(HOST_NAME_RULES, on=True).match("web-ap01", instances)
    assert len(found) == 1 and (conf, reason) == (HIGH, "host_name")


def test_host_name_business_suffix() -> None:
    instances = [_inst("was1", "webap01")]
    assert _resolver(HOST_NAME_RULES, on=False).match("webap01(인터넷뱅킹)", instances)[0] == []
    found, _, _ = _resolver(HOST_NAME_RULES, on=True).match("webap01(인터넷뱅킹)", instances)
    assert len(found) == 1


def test_host_name_short_vs_fqdn_kept_on() -> None:
    instances = [_inst("was1", "WEB-AP01.corp.local")]
    found, _, _ = _resolver(HOST_NAME_RULES, on=True).match("webap01 (인터넷뱅킹)", instances)
    assert len(found) == 1


def test_exact_rule_on_off() -> None:
    instances = [_inst("web_ap01", "")]
    assert _resolver(EXACT_RULES, on=False).match("web-ap01", instances)[0] == []
    found, conf, reason = _resolver(EXACT_RULES, on=True).match("web-ap01", instances)
    assert len(found) == 1 and (conf, reason) == (HIGH, "exact")
    # 둘 다 FQDN이고 도메인이 다르면 on에서도 불일치
    fqdn = [_inst("webap01.a.local", "")]
    assert _resolver(EXACT_RULES, on=True).match("webap01.b.local", fqdn)[0] == []


def test_exact_rule_known_risk_hyphen_collapse() -> None:
    """알려진 위험(현상 고정): `-` 제거로 인스턴스명 `webap01-1`과 호스트 `webap011`이 같아진다."""
    instances = [_inst("webap01-1", "")]
    assert _resolver(EXACT_RULES, on=False).match("webap011", instances)[0] == []
    found, conf, _ = _resolver(EXACT_RULES, on=True).match("webap011", instances)
    assert len(found) == 1 and conf == HIGH


def test_override_on_off() -> None:
    instance_map = {
        "overrides": [{"hostname": "webap01.a.local", "instance_name": "was1"}],
        "match_rules": [],
    }
    instances = [_inst("was1", "")]
    found, conf, reason = _resolver(instance_map, on=False).match("webap01.b.local", instances)
    assert len(found) == 1 and (conf, reason) == (HIGH, "override")
    assert _resolver(instance_map, on=True).match("webap01.b.local", instances)[0] == []
    found, _, reason = _resolver(instance_map, on=True).match("WEBAP01.a.local.", instances)
    assert len(found) == 1 and reason == "override"
    found, _, _ = _resolver(instance_map, on=True).match("web-ap01(인터넷뱅킹)", instances)
    assert len(found) == 1  # 단일 레이블 입력은 첫 레이블 비교


def test_regex_rule_host_compare_on() -> None:
    instance_map = {"match_rules": [{"kind": "regex", "pattern": r"^(?P<hostname>[^:]+):\d+$"}]}
    instances = [_inst("web_ap01:8080", "")]
    assert _resolver(instance_map, on=False).match("web-ap01", instances)[0] == []
    found, conf, reason = _resolver(instance_map, on=True).match("web-ap01", instances)
    assert len(found) == 1 and (conf, reason) == (MEDIUM, "regex")


def test_prefix_rule_unchanged() -> None:
    instance_map = {"match_rules": [{"kind": "prefix"}]}
    instances = [_inst("webap01_was", "")]
    for on in (False, True):
        found, conf, reason = _resolver(instance_map, on=on).match("WEBAP01", instances)
        assert len(found) == 1 and (conf, reason) == (MEDIUM, "prefix")
        assert _resolver(instance_map, on=on).match("web-ap01", instances)[0] == []


# ── 플래그 ──────────────────────────────────────────────


def _env(**extra: str) -> dict[str, str]:
    return {"JENNIFER_API_URL": "http://jennifer.invalid", "JENNIFER_API_TOKEN": "t", **extra}


def _only_resolver(cfg: Any) -> InstanceResolver:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    (source,) = list(build_source_set(cfg, transport=transport))
    return source.resolver


def test_flag_default_off() -> None:
    cfg = load_config(_env())
    assert cfg.runtime.host_canonical_match is False
    resolver = _only_resolver(cfg)
    assert resolver._canonical_match is False


def test_flag_on_wired_to_resolver() -> None:
    cfg = load_config(_env(APM_HOST_CANONICAL_MATCH_ENABLED="true"))
    assert cfg.runtime.host_canonical_match is True
    resolver = _only_resolver(cfg)
    assert resolver._canonical_match is True
