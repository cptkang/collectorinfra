"""plans/201 W4 — 영역별 판정 매핑 사본 계약 (D-354 · D-139 양방향 import 0).

sre_agent는 본체 어휘를 import하지 않는다. 대신 `sre_agent/config/duty_area_layers.yaml`이 본체 정본의
일부를 사본으로 든다 — 이 테스트가 세 YAML을 **파일로** 읽어(코드 import 없음) 사본이 정본과 같은지 고정한다.
- 영역 코드 집합·선언 순서·라벨 = `config/duty_areas.yaml` areas
- 외부 연계 근거 문구 = `config/duty_areas.yaml` cause_hints의 외부 연계 라벨
- 상위 프로세스 kind → 영역 = `config/duty_areas.yaml` cause_hints.process_kinds
- DB 엔진 시그니처 사본 = `config/middleware_signatures.yaml`의 같은 kind `match_regex`(글자까지)
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_BODY = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
_SIGS = yaml.safe_load(
    (_ROOT / "config" / "middleware_signatures.yaml").read_text(encoding="utf-8")
)
_COPY = yaml.safe_load(
    (_ROOT / "sre_agent" / "config" / "duty_area_layers.yaml").read_text(encoding="utf-8")
)


def _signature_entries() -> list[dict]:
    raw = _SIGS.get("signatures") if isinstance(_SIGS, dict) else _SIGS
    assert isinstance(raw, list) and raw
    return [e for e in raw if isinstance(e, dict)]


def test_area_codes_order_and_labels_match_body() -> None:
    body = [(a["code"], a["label"]) for a in _BODY["areas"]]
    copy = [(a["code"], a["label"]) for a in _COPY["areas"]]
    assert copy == body


def test_every_area_has_no_step_reason() -> None:
    assert all(str(a.get("no_step_reason") or "").strip() for a in _COPY["areas"])


def test_external_label_matches_cause_hints() -> None:
    """외부 연계 단서(유형 → was_mw)의 근거 문구가 사본의 external.label 하나와 같다."""
    hints = _BODY["cause_hints"]["apm_event_types"]
    labels = {v["label"] for v in hints.values() if "외부 연계" in str(v.get("label") or "")}
    assert labels == {_COPY["external"]["label"]}
    assert all(v["areas"] == ["was_mw"] for v in hints.values() if v.get("label") in labels)
    assert _COPY["layers"]["external"] == "was_mw"


def test_process_kind_areas_match_cause_hints() -> None:
    body = {area: list(v["kinds"]) for area, v in _BODY["cause_hints"]["process_kinds"].items()}
    assert _COPY["process"]["kind_areas"] == body


def test_db_engine_signature_copies_match() -> None:
    by_kind = {e["kind"]: e for e in _signature_entries()}
    db_kinds = _COPY["process"]["kind_areas"]["db"]
    copies = {e["kind"]: e["match_regex"] for e in _COPY["process_signatures"]}
    assert sorted(copies) == sorted(db_kinds)  # DB 엔진 6종만 · WAS·웹서버 사본 없음
    for kind, regex in copies.items():
        entry = by_kind[kind]
        assert regex == entry["match_regex"], kind
        # 이름만으로 판정하는 사본이 완전하려면 정본도 match_regex 하나로만 판정해야 한다
        assert not entry.get("match_any") and not entry.get("exclude_any"), kind


def test_area_codes_in_mapping_are_known() -> None:
    codes = {a["code"] for a in _COPY["areas"]}
    assert set(_COPY["layers"].values()) <= codes
    assert {c for v in _COPY["reading_areas"].values() for c in v} <= codes
    assert set(_COPY["connection_hypotheses"].values()) <= codes
    assert set(_COPY["connection_inconclusive"]["areas"]) <= codes
    assert set(_COPY["hypothesis_areas"].values()) <= codes | {"process"}
    assert _COPY["process"]["default_area"] in codes
    assert _COPY["polestar_unlinked"]["area"] in codes
