"""plans/215 W1 — 설정 로더: 잘못된 값은 그 신호만 끄고 경고(침묵 금지 · 수용 32)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from noise_gate.application.biz_service_config import (
    DEFAULT_CONFIG_PATH,
    BizServiceSettings,
    load_biz_service_config,
    load_generic_terms,
)
from noise_gate.domain import biz_service_model as m

LOGGER = "noise_gate.application.biz_service_config"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _load(tmp_path: Path, body: str) -> BizServiceSettings:
    """저장소 yaml에 값 하나만 덮어 쓴 설정 — 그 값의 효과만 본다."""
    base = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    p = tmp_path / "biz_service.yaml"
    p.write_text(yaml.safe_dump(_merge(base, yaml.safe_load(body)), allow_unicode=True),
                 encoding="utf-8")
    return load_biz_service_config(p)


def test_repo_yaml_loads_clean(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = load_biz_service_config()
    assert s.warnings == () and not caplog.records
    cfg = s.config
    assert cfg.host_prefix.enabled and cfg.host_prefix.length == 3
    assert cfg.tree_group.enabled and cfg.disabled_reasons == {}
    assert not cfg.aux.itam_contract.enabled and not cfg.aux.alarm_cooccurrence.enabled
    assert cfg.sms_thresholds == {} and cfg.layer_tokens == {}  # W0 전엔 비움
    assert "운영" in s.generic_terms


def test_strip_pattern_without_caret_disables_s6_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """수용 32 — 패턴이 `^` 없이 시작 → S6 비활성 + 경고 · 다른 신호는 그대로."""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = _load(tmp_path, "host_prefix_code: {strip_prefix_patterns: ['co(b0|cm)-']}\n"
                            "tree_group: {enabled: true}\n")
    cfg = s.config
    assert not cfg.host_prefix.enabled and m.SIG_HOST_PREFIX in cfg.disabled_reasons
    assert cfg.tree_group.enabled
    assert any(m.SIG_HOST_PREFIX in r.getMessage() for r in caplog.records)


def test_valid_strip_pattern_kept(tmp_path: Path) -> None:
    s = _load(tmp_path, "host_prefix_code: {strip_prefix_patterns: ['^co(b0|cm)-']}\n")
    assert s.config.host_prefix.strip_prefix_patterns == ("^co(b0|cm)-",)


def test_bad_tree_share_disables_s7_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = _load(tmp_path, "tree_group: {enabled: true, min_share: 1.7}\n")
    cfg = s.config
    assert not cfg.tree_group.enabled and set(cfg.disabled_reasons) == {m.SIG_TREE_GROUP}
    assert cfg.host_prefix.enabled and caplog.records


def test_bad_aux_sub_block_disables_that_signal(tmp_path: Path) -> None:
    s = _load(tmp_path, "aux_signals: {ip_subnet: {enabled: true, prefix_len: 99}}\n")
    cfg = s.config
    assert not cfg.aux.ip_subnet.enabled and set(cfg.disabled_reasons) == {m.SIG_IP_SUBNET}
    assert cfg.aux.registered_batch.enabled


def test_bad_aux_common_disables_all_aux(tmp_path: Path) -> None:
    s = _load(tmp_path, "aux_signals: {min_purity: -1}\n")
    cfg = s.config
    assert all(not cfg.aux.enabled(sig) for sig in m.AUX_SIGNALS)
    assert set(cfg.disabled_reasons) == set(m.AUX_SIGNALS)
    assert cfg.host_prefix.enabled and cfg.tree_group.enabled


def test_invalid_sms_threshold_entry_dropped(tmp_path: Path) -> None:
    s = _load(tmp_path, "sms_thresholds:\n  cpu: {warn: 80, crit: 90}\n"
                        "  memory: {warn: 95, crit: 90}\n  disk_io: {warn: 1}\n")
    assert s.config.sms_thresholds == {"cpu": m.SmsThreshold(80.0, 90.0)}
    assert len(s.warnings) == 2


def test_invalid_layer_tokens_off(tmp_path: Path) -> None:
    s = _load(tmp_path, "layer_tokens: {web: [web], cache: [rds]}\n")
    assert s.config.layer_tokens == {} and s.warnings
    ok = _load(tmp_path, "layer_tokens: {web: [WEB, ap]}\n")
    assert ok.config.layer_tokens == {"web": ("web", "ap")}


def test_missing_file_turns_learning_signals_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        s = load_biz_service_config(tmp_path / "none.yaml")
    cfg = s.config
    assert not cfg.host_prefix.enabled and not cfg.tree_group.enabled
    assert all(not cfg.aux.enabled(sig) for sig in m.AUX_SIGNALS)
    assert s.warnings and caplog.records


def test_generic_terms_failure_reported(tmp_path: Path) -> None:
    terms, failed = load_generic_terms(tmp_path / "none.yaml")
    assert terms == frozenset() and failed
