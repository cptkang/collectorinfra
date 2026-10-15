"""폴스타↔APM 상관 진단 `page_audit` S11~S14 · 행 `xs` 테스트 (plans/162 §11.8 W0-X).

실 Redis·LLM·DB·게이트웨이를 쓰지 않는다 — 가짜 Redis(읽기 3종)와 tmp 경로만 쓴다. 픽스처는
`noise_gate/testdata/page_audit/xsource/`(줄마다 `scenario` 키 A~J — 부분 집합으로 판독표 행을
만든다).
설정은 `AppConfig()`를 만들지 않고 검증 대상 필드를 명시해 주입한다(`.env` 누수 차단).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig, NoiseGateConfig

from noise_gate.scripts import page_audit as pa
from noise_gate.scripts import page_audit_xsource as xsource
from noise_gate.scripts.page_audit_sanitize import (
    _PSEUDONYM_RE,
    APM_SOURCE_DEFAULT,
    GATEWAY_ERROR_CODES,
    IDENT_ALARM_ID,
    IDENT_APM_DOMAIN,
    IDENT_APM_SOURCE,
    IDENT_HOST,
    IDENT_INSTANCE,
    IDENT_RESOURCE,
    IDENT_URL,
    OTHER,
    WAS_KINDS,
    IdentifierSet,
    Pseudonymizer,
    apm_source_label,
    app_impact_code,
    db_id_label,
    leak_check,
    normalize_text,
    sanitize_xs,
    vocabulary_words,
    xs_vocabulary,
)
from noise_gate.scripts.replay_noise import build_tracker, replay, summarize_cross_source

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "page_audit" / "xsource"
BASE_FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "page_audit"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 14, 23, 0, 0, tzinfo=KST)
BASE_NOW = datetime(2026, 10, 13, 23, 0, 0, tzinfo=KST)
OUTPUT_FILES = ("summary.json", "summary.md", "policy_snapshot.json", "leak_check.json",
                "alarms.jsonl")
KEY = b"x" * 32

# 픽스처 원문 — 출력 5개 파일 어디에도 없어야 한다(경계 변형 포함 · 비교는 NFKC·casefold 뒤).
RAW_SECRETS = (
    "FAKE-WEB01.example.test", "fake-web01", "fake-web01-reg", "fake-web01_tomcat1",
    "fake-was02", "fake-was02_jeus", "fake-app03", "fake-api04", "fake-db05", "fake_db05",
    "fake-mq06", "fake-mq06-svc", "fake-batch07", "fake-batch07_jvm1", "fake-orphan",
    "fake-amb-inst01", "fake-cache08", "fake-clr09", "fake-ipsrv10", "fake-nohost11",
    "fake-sev12", "fake-domain-a", "fake-dom-192.0.2.77", "fake-domain-dev",
    "fake-domain-single", "fake-app.example.test", "http://", "fake-gw.example.test",
    "example.test", "192.0.2.", "198.51.100.", "198_51_100_7", "::ffff:", "１９２．０．２．９",
    "ConnectError", "refused", "인스턴스 없음", "weird_code", "ep-fixture",
    "FAKE-XS-", "fakexs", "jvm1", "tomcat1",
    # 미등록 소스 id — `xs.apm_source`·행 `db_id` 모두 `S-` 가명(`jennifer_<S-…>`)이다.
    "cm_dev",
)


# ── 가짜 Redis ────────────────────────────────────────────────────────────────
def _parse_id(stream_id: str, *, upper: bool = False) -> tuple[int, int]:
    if stream_id in ("-", "+"):
        return (0, 0) if stream_id == "-" else (2**63, 2**63)
    ms, sep, seq = stream_id.partition("-")
    if not sep:
        return int(ms), (2**63 if upper else 0)
    return int(ms), int(seq)


class FakeRedis:
    """읽기 3종만 구현하는 가짜 Redis(그 밖의 호출은 기록만)."""

    def __init__(self, streams: dict[str, list[tuple[bytes, dict[bytes, bytes]]]],
                 groups: dict[str, list[dict[bytes, Any]]]) -> None:
        self.streams = streams
        self.groups = groups
        self.calls: list[str] = []

    def xrange(self, name: str, min: str = "-", max: str = "+",
               count: int | None = None) -> list[tuple[bytes, dict[bytes, bytes]]]:
        self.calls.append("xrange")
        lo, hi = _parse_id(min), _parse_id(max, upper=True)
        items = [(i, f) for i, f in self.streams.get(name, []) if lo <= _parse_id(i.decode()) <= hi]
        return items[:count] if count else items

    def xinfo_groups(self, name: str) -> list[dict[bytes, Any]]:
        self.calls.append("xinfo_groups")
        return self.groups.get(name, [])

    def xpending(self, name: str, groupname: str) -> dict[bytes, Any]:
        self.calls.append("xpending")
        return {b"pending": 0, b"min": None, b"max": None, b"consumers": []}

    def __getattr__(self, name: str) -> Any:
        def _write(*args: Any, **kwargs: Any) -> None:
            self.calls.append(name)
        return _write


def _lines(path: Path, scenarios: Iterable[str] | None) -> list[dict[str, Any]]:
    keep = set(scenarios) if scenarios is not None else None
    out = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    return [r for r in out if keep is None or r.get("scenario") in keep]


def _fake_redis(scenarios: Iterable[str] | None = None, fixture: Path = FIXTURE,
                raw_edit: Any = None) -> FakeRedis:
    """픽스처 원문(시나리오 부분 집합)으로 가짜 Redis를 만든다 — dead 파일이 없으면 빈 스트림."""
    lines = _lines(fixture / "raw_stream.jsonl", scenarios)
    if raw_edit is not None:
        lines = [raw_edit(r) for r in lines]
    raw = [(r["id"].encode(), {b"data": json.dumps(r["data"], ensure_ascii=False).encode()})
           for r in lines]
    dead: list[tuple[bytes, dict[bytes, bytes]]] = []
    if (fixture / "dead_stream.jsonl").exists():
        dead = [(r["id"].encode(), {k.encode(): str(v).encode() for k, v in r["fields"].items()})
                for r in _lines(fixture / "dead_stream.jsonl", None)]
    groups_json = json.loads((fixture / "stream_groups.json").read_text("utf-8"))
    groups = {k: [{gk.encode(): (gv.encode() if isinstance(gv, str) else gv)
                   for gk, gv in g.items()} for g in v] for k, v in groups_json.items()}
    return FakeRedis({"alarm:raw": raw, "alarm:dead": dead}, groups)


def _decisions(tmp_path: Path, scenarios: Iterable[str] | None,
               edit: Any = None) -> Path:
    recs = _lines(FIXTURE / "decisions.jsonl", scenarios)
    if edit is not None:
        recs = [edit(r) for r in recs]
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    return path


def _config(**noise: Any) -> SimpleNamespace:
    """검증 대상 필드를 명시한 설정 묶음(사건 상관 shadow · 기본 규칙 표)."""
    fields: dict[str, Any] = {
        "enable_noise_gate": True,
        "repeat_interval_seconds": 14400,
        "sev3_repeat_interval_seconds": 14400,
        "importance_value_map_csv": "1=낮음,2=보통,3=높음",
        "cross_source_mode": "shadow",
        "app_impact_enabled": False,
        "app_impact_window_minutes": 10,
        "app_impact_late_promotion_enabled": False,
        "episode_idle_seconds": 900,
        "cross_source_rules_path": "config/cross_source_rules.yaml",
    }
    ng = NoiseGateConfig(**{**fields, **noise})
    alarm = AlarmConfig(min_severity=2, redis_stream_key="alarm:raw",
                        redis_consumer_group="alarm-workers", dead_letter_stream_key="alarm:dead")
    return SimpleNamespace(noise_gate=ng, alarm=alarm)


def _run(tmp_path: Path, scenarios: Iterable[str] | None = None, *, stream: bool = True,
         config: Any = None, edit: Any = None, key: bytes = KEY, raw_edit: Any = None,
         sanitizer_cls: Any = pa.Sanitizer) -> pa.AuditResult:
    scen = list(scenarios) if scenarios is not None else None
    return pa.run_audit(
        config=config or _config(),
        decisions_path=_decisions(tmp_path, scen, edit),
        out_root=tmp_path / "out",
        hours=24, days=7,
        streams=pa.ReadOnlyStreams(_fake_redis(scen, raw_edit=raw_edit)) if stream else None,
        now=NOW, key=key, sanitizer_cls=sanitizer_cls,
    )


def _summary(result: pa.AuditResult) -> dict[str, Any]:
    assert result.exit_code == 0, result.leak
    return json.loads((result.out_dir / "summary.json").read_text("utf-8"))


def _rows(result: pa.AuditResult) -> list[dict[str, Any]]:
    text = (result.out_dir / "alarms.jsonl").read_text("utf-8")
    return [json.loads(x) for x in text.splitlines()]


def _row_of(rows: list[dict[str, Any]], scenario_rows: list[dict[str, Any]], aid: str
            ) -> dict[str, Any]:
    """원문 alarmId로 행을 찾는다(행 순서 = 수신 순서 = 픽스처 원문 순서)."""
    idx = [i for i, r in enumerate(scenario_rows) if r["data"]["alarmId"] == aid]
    assert len(idx) == 1, aid
    return rows[idx[0]]


def _scenario_raws(scenarios: Iterable[str] | None) -> list[dict[str, Any]]:
    return _lines(FIXTURE / "raw_stream.jsonl", scenarios)


def _xs_of(result: pa.AuditResult, scenarios: Iterable[str] | None, aid: str,
           nth: int = 0) -> dict[str, Any]:
    """원문 alarmId의 `nth`번째 수신 행의 `xs`(행 순서 = 수신 순서 = 픽스처 원문 순서)."""
    raws = _scenario_raws(scenarios)
    idx = [i for i, r in enumerate(raws) if r["data"]["alarmId"] == aid]
    xs = _rows(result)[idx[nth]]["xs"]
    assert isinstance(xs, dict)
    return xs


def _folded_outputs(out_dir: Path) -> str:
    from noise_gate.scripts.page_audit_sanitize import fold

    return "\n".join(fold((out_dir / name).read_text("utf-8")) for name in OUTPUT_FILES)


# ── 1 누출 0 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stream", [True, False])
def test_outputs_contain_no_raw_identifiers(tmp_path: Path, stream: bool) -> None:
    """완료 기준 1 — 인스턴스명·도메인명·URL·예외 문구·정규화 전후 호스트 키·경계 변형(끝 점 IP·
    5묶음·`::ffff:` 사상·`_` 구분·전각·ZWSP)이 출력 5개 파일 어디에도 없다."""
    from noise_gate.scripts.page_audit_sanitize import fold

    result = _run(tmp_path, stream=stream)
    assert result.exit_code == 0
    assert result.leak["passed"] is True
    text = _folded_outputs(result.out_dir)
    for secret in RAW_SECRETS:
        assert fold(secret) not in text, secret
    # 픽스처 이름은 전부 `fake-*` — ZWSP·전각 변형까지 접은 뒤에도 한 글자도 남지 않는다.
    assert "fake" not in text


def test_collect_identifiers_adds_new_kinds() -> None:
    """인스턴스명·제니퍼 도메인명·URL·정규화 전후 호스트 키(3자 이상)·기록된 키가 일치 대상이다."""
    idents = IdentifierSet()
    payload = {"hostname": "FAKE-ZZ01.example.test", "serverName": "fq1",
               "apm": {"instance_name": "fake-inst-qq", "domain_name": "fake-dom-qq",
                       "application": "http://fake-app.example.test/ctx"}}
    rec = {"stage_evidence": {"cross_source": {"host_key": "fake-rec-key",
                                               "cause_alarm_id": "FAKE-CAUSE-9"}}}
    xsource.collect_identifiers(idents, [payload, None], [rec])
    hits = idents.count_hits(
        "fake-inst-qq fake-dom-qq http://fake-app.example.test/ctx fake-zz01 fake-rec-key "
        "FAKE-CAUSE-9 fq1")
    assert hits[IDENT_INSTANCE] == 1 and hits[IDENT_APM_DOMAIN] == 1 and hits[IDENT_URL] == 1
    # 3자 키(fq1)도 더한다 — `IDENT_HOST` 최소 길이(3)와 같다(교정 5).
    assert hits[IDENT_HOST] == 3 and hits[IDENT_ALARM_ID] == 1


def test_leak_check_covers_xs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`xs`에 원문 인스턴스명이 새면 누출 검사가 닫힌다(종료 코드 2 · 분석 파일 미기록)."""
    real = xsource.sanitize_xs

    def leaky(raw: Any, pseudo: Any, **kw: Any) -> Any:
        out = real(raw, pseudo, **kw)
        apm = raw.get("apm") if isinstance(raw, dict) else None
        if out is not None and apm and apm.get("instance_name"):
            out["inst"] = apm["instance_name"]
        return out

    monkeypatch.setattr(xsource, "sanitize_xs", leaky)
    result = _run(tmp_path, ["A"])
    assert result.exit_code == 2
    # APM 자원명 = 인스턴스명이라 먼저 등록된 종류(resource)로 센다 — 어느 쪽이든 식별자 일치다.
    hits = [h for h in result.leak["hits"] if h["file"] == "alarms.jsonl"]
    assert hits and all(h["kind"].startswith("identifier:") for h in hits)
    assert not (result.out_dir / "alarms.jsonl").exists()


def test_stream_reads_only(tmp_path: Path) -> None:
    """Redis는 읽기 3종만 쓴다(상관 진단이 더한 호출 없음)."""
    fake = _fake_redis()
    pa.run_audit(config=_config(), decisions_path=FIXTURE / "decisions.jsonl",
                 out_root=tmp_path / "out", hours=24, days=7,
                 streams=pa.ReadOnlyStreams(fake), now=NOW, key=KEY)
    assert set(fake.calls) <= {"xrange", "xinfo_groups", "xpending"}


# ── 2 소스 간 같은 가명 ───────────────────────────────────────────────────────
def test_same_host_key_pseudonym_across_sources(tmp_path: Path) -> None:
    """완료 기준 2 — 폴스타 `FAKE-WEB01.example.test`와 APM `fake-web01`이 같은 `K-`."""
    from noise_gate.domain.alarm import normalize_host_key

    result = _run(tmp_path, ["A"])
    pol = _xs_of(result, ["A"], "FAKE-XS-A1")
    apm = _xs_of(result, ["A"], "jennifer:fakexsa2000000000")
    assert pol["hk"] == apm["hk"]
    assert pol["hk"] == Pseudonymizer(KEY)("K", normalize_host_key("FAKE-WEB01.example.test"))
    assert pol["peer_7d"] is True and apm["peer_7d"] is True
    # 기존 `host`(H-)는 등록 서버명 기준이라 소스 간에 다르다 — 그래서 K-를 따로 둔다.
    rows = _rows(result)
    assert rows[0]["host"] != rows[1]["host"]


# ── 3 도메인과 차등 0 ─────────────────────────────────────────────────────────
def _replay_summary(scenarios: Iterable[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """같은 픽스처의 판정된 원문 페이로드를 판정 순으로 replay_noise(shadow)에 넣는다."""
    decided: dict[str, list[str]] = {}
    for d in _lines(FIXTURE / "decisions.jsonl", scenarios):
        if d.get("type") is None:
            decided.setdefault(d["alarm_id"], []).append(d["ts"])
    used: dict[str, int] = {}
    pairs: list[tuple[str, dict[str, Any]]] = []
    for r in _scenario_raws(scenarios):
        aid = r["data"]["alarmId"]
        k = used.get(aid, 0)
        if k < len(decided.get(aid, [])):          # dedup이 ACK한 반복 원문은 판정이 없다
            pairs.append((decided[aid][k], r["data"]))
            used[aid] = k + 1
    pairs.sort(key=lambda p: p[0])
    rows, _ = replay([p for _, p in pairs], tracker=build_tracker("shadow"))
    return summarize_cross_source(rows), rows


COMPARED = ("episodes", "members", "would_demote", "link", "missing", "by_rule",
            "with_related_effects")


def test_v0_equals_replay_noise_when_tiers_agree(tmp_path: Path) -> None:
    """완료 기준 3 — 원인 티어가 기록·재판정에서 같은 시나리오 전부에서 V0 = replay_noise 요약."""
    scen = [s for s in "ACDEFGHIJ"]
    v0 = _summary(_run(tmp_path, scen))["S13"]["sim"]["variants"]["V0"]
    expected, _ = _replay_summary(scen)
    assert {k: v0[k] for k in COMPARED} == {k: expected[k] for k in COMPARED}
    assert v0["episodes"] > 0 and v0["would_demote"] > 0 and v0["link"] > 0


def test_v0_vs_replay_noise_full_fixture_differs_only_by_recorded_tier(tmp_path: Path) -> None:
    """완료 기준 3(전체 픽스처) — 사건·멤버·결합 수는 같고, 시나리오 B 1건만 link↔demote로 갈린다.

    원인: 시계 정의가 아니라 **원인 티어 출처** 차이다. V0은 판정 레코드의 기록 티어(B 원인 =
    DASHBOARD)로 `record_tier`해 `missing=cause_tier` link가 되고, 리플레이는 폴스타 원문 페이로드에
    기록된 noise_ctx가 없어 수집 실패로 티어를 다시 판정(통보 티어)하므로 같은 결합이 demote가 된다.
    시계(판정 시각 대 발생 시각)는 이 픽스처에서 사건 종료 경계를 넘나들지 않아 사건 수가 같다.
    """
    v0 = _summary(_run(tmp_path))["S13"]["sim"]["variants"]["V0"]
    expected, rows = _replay_summary(None)
    for k in ("episodes", "members", "by_rule", "with_related_effects"):
        assert v0[k] == expected[k], k
    assert v0["link"] + v0["would_demote"] == expected["link"] + expected["would_demote"]
    assert (v0["link"] - expected["link"], expected["would_demote"] - v0["would_demote"]) == (1, 1)
    assert v0["missing"].get("cause_tier") == 1 and "cause_tier" not in expected["missing"]
    rest = {k: v for k, v in v0["missing"].items() if k != "cause_tier"}
    assert rest == expected["missing"]
    b_cause = next(r for r in rows if r["alarm_id"] == "FAKE-XS-B1")
    b_effect = next(r for r in rows if r["alarm_id"] == "jennifer:fakexsb2000000000")
    assert b_cause["tier"] != "dashboard"            # 리플레이가 다시 판정한 원인 티어
    assert b_effect["cross_source"]["action"] == "demote"
    assert _xs_of(_run(tmp_path / "b", ["B"]), ["B"],
                  "jennifer:fakexsb2000000000")["sim"]["V0"] == "link:cause_tier"


# ── 4 변형 효과 ──────────────────────────────────────────────────────────────
def test_variant_effects(tmp_path: Path) -> None:
    """완료 기준 4 — dedup이 사건을 닫는 시퀀스(C)는 V0 link 0 → V1 link 1, 30분 지연 시퀀스(D)는
    V0 `missing=window` → V2 해소. demote 후보 중 sev3 증상 0."""
    result = _run(tmp_path)
    c = _xs_of(result, None, "jennifer:fakexsc2000000000")["sim"]
    assert (c["V0"], c["V1"]) == ("member", "link:window")
    d = _xs_of(result, None, "jennifer:fakexsd4000000000")["sim"]
    assert d["V0"] == "link:window" and "window" not in d["V2"] and d["V2"] == "demote"
    assert d["V3"] == "demote"
    j = _xs_of(result, None, "jennifer:fakexsj2000000000")["sim"]
    assert j["V0"] == "link:severity"                # sev3 증상은 강등 후보가 되지 않는다
    sim = _summary(result)["S13"]["sim"]["variants"]
    assert all(v["demote_sev3"] == 0 for v in sim.values())

    only_c = _summary(_run(tmp_path / "c", ["C"]))["S13"]["sim"]["variants"]
    assert (only_c["V0"]["link"], only_c["V1"]["link"]) == (0, 1)
    only_d = _summary(_run(tmp_path / "d", ["D"]))["S13"]["sim"]["variants"]
    assert only_d["V0"]["missing"] == {"window": 1}
    assert "window" not in only_d["V2"]["missing"]
    assert only_d["V2"]["would_demote"] == 1


def test_dedup_recurrence_rows_have_no_simulation(tmp_path: Path) -> None:
    """dedup이 ACK한 반복 원문 행은 사건 재생 대상이 아니다(`sim` null · 키는 있다)."""
    result = _run(tmp_path, ["C"])
    rec = _xs_of(result, ["C"], "FAKE-XS-C1", nth=1)
    assert rec["hk"] and rec["sim"] is None


# ── 5 판독표 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("scenarios", "cause"), [
    (["A"], "C4"), (["B"], "C3"), (["C"], "C1"), (["D"], "C2"), (["E"], "B2"), (["F"], "B1"),
    (["G"], "A2"),
])
def test_readout_per_scenario(tmp_path: Path, scenarios: list[str], cause: str) -> None:
    """완료 기준 5 — §11.7 판독표 각 행을 만드는 픽스처가 기대 원인 하나만 낸다."""
    ro = _summary(_run(tmp_path, scenarios))["xs_readout"]
    assert ro["causes"] == [cause]
    assert ro["rows"][0]["action"]


def test_readout_a1_mode_off_or_rules_load_failure(tmp_path: Path) -> None:
    """A1 — 모드 off면 A1(시뮬레이션은 그대로 돈다) · 규칙 표 적재 실패면 A1 + 시뮬레이션 모름."""
    off = _summary(_run(tmp_path / "off", ["A"], config=_config(cross_source_mode="off")))
    assert "A1" in off["xs_readout"]["causes"]
    assert off["S13"]["sim"]["known"] is True
    bad = _summary(_run(tmp_path / "bad", ["A"], config=_config(
        cross_source_rules_path=str(tmp_path / "missing_rules.yaml"))))
    assert bad["S11"]["rules_loaded"] is False and bad["S11"]["rules_sha256"] is None
    assert bad["S13"]["sim"] == {**bad["S13"]["sim"], "known": False,
                                 "reason": "rules_load_failed"}
    assert bad["xs_readout"]["causes"] == ["A1"]


# ── 6 --no-stream ─────────────────────────────────────────────────────────────
def test_no_stream_marks_unknown(tmp_path: Path) -> None:
    """완료 기준 6 — `--no-stream`이면 S12 「모름」, S13 시뮬레이션 모름(실제 기록만), S14는 판정
    시각 기준 · 예외 없이 끝난다."""
    result = _run(tmp_path, stream=False)
    s = _summary(result)
    assert s["S12"]["known"] is False and "모름" in s["S12"]["note"]
    assert s["S13"]["sim"]["known"] is False and s["S13"]["sim"]["reason"] == "no_stream"
    assert s["S13"]["actual"]["known"] is True
    assert s["S13"]["actual"]["two_source_episodes"] == 2
    assert s["S14"]["basis"] == "decision_ts" and s["S14"]["pairs"] >= 1
    md = (result.out_dir / "summary.md").read_text("utf-8")
    assert "## S12 이름 정합" in md and "모름" in md
    rows = _rows(result)
    assert all("sim" not in (r["xs"] or {}) or r["xs"]["sim"] is None for r in rows)


# ── 7 기존 출력 불변 ──────────────────────────────────────────────────────────
def _base_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None, stream: bool
              ) -> pa.AuditResult:
    from noise_gate.tests.test_page_audit import _config as base_config
    from noise_gate.tests.test_page_audit import _fake_redis as base_redis

    if monkeypatch is not None:
        monkeypatch.setattr(pa.xsource, "collect_identifiers", lambda *a, **k: None)
        monkeypatch.setattr(pa.xsource, "analyze", lambda items, **k: xsource.XsResult(
            rows=[None] * len(items), sections={}, snapshot={}))
    return pa.run_audit(
        config=base_config(), decisions_path=BASE_FIXTURE / "decisions.jsonl",
        out_root=tmp_path / "out", hours=24, days=7,
        streams=pa.ReadOnlyStreams(base_redis()) if stream else None, now=BASE_NOW, key=KEY,
    )


@pytest.mark.parametrize("stream", [True, False])
def test_existing_outputs_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    stream: bool) -> None:
    """완료 기준 7 — 기존 픽스처에서 S0~S10·기존 행 필드는 상관 진단이 없을 때와 비트 동일하고,
    `xs`(행 끝)와 S11~S14·판독 결과만 더해진다(기준 코드 9ccc44f 대조는 보고서 참조)."""
    cur = _base_run(tmp_path / "cur", None, stream)
    with monkeypatch.context() as mp:
        ref = _base_run(tmp_path / "ref", mp, stream)
    sc = json.loads((cur.out_dir / "summary.json").read_text("utf-8"))
    sr = json.loads((ref.out_dir / "summary.json").read_text("utf-8"))
    for k in (f"S{i}" for i in range(11)):
        assert sc[k] == sr[k], k
    assert sorted(set(sc) - set(sr)) == ["S11", "S12", "S13", "S14", "xs_readout"]
    rc, rr = _rows(cur), _rows(ref)
    assert len(rc) == len(rr) > 0
    for a, b in zip(rc, rr, strict=True):
        assert list(a)[-1] == "xs"
        assert {k: v for k, v in a.items() if k != "xs"} == {
            k: v for k, v in b.items() if k != "xs"}
    md_cur = (cur.out_dir / "summary.md").read_text("utf-8")
    md_ref = (ref.out_dir / "summary.md").read_text("utf-8")
    assert md_cur.startswith(md_ref.rstrip("\n"))
    assert "경고: 상관 시뮬레이션" not in md_cur      # 불일치가 없으면 S10 경고도 없다
    assert "xs_v0_actual_mismatch" not in sc["S10"]
    assert sc["S11"]["apm_received_zero"] is True and "APM 수신 0건" in md_cur
    snap = json.loads((cur.out_dir / "policy_snapshot.json").read_text("utf-8"))
    ref_snap = json.loads((ref.out_dir / "policy_snapshot.json").read_text("utf-8"))
    assert {k: v for k, v in snap.items() if k != "cross_source"} == \
        {k: v for k, v in ref_snap.items() if k != "cross_source"}


def test_s10_warns_only_when_v0_differs_from_actual(tmp_path: Path) -> None:
    """S10 「V0 ≠ 실제 기록」 경고는 불일치일 때만 — 실제 기록을 바꾸면 1건 경고."""
    clean = _summary(_run(tmp_path / "clean", ["A", "B"]))
    assert "xs_v0_actual_mismatch" not in clean["S10"]
    assert clean["S13"]["sim"]["v0_actual_mismatch"] == 0

    def edit(rec: dict[str, Any]) -> dict[str, Any]:
        cs = (rec.get("stage_evidence") or {}).get("cross_source") or {}
        if cs.get("action") == "demote":
            cs["action"], cs["missing"] = "link", ["window"]
        return rec

    result = _run(tmp_path / "bad", ["A", "B"], edit=edit)
    s = _summary(result)
    assert s["S10"]["xs_v0_actual_mismatch"] == 1
    assert "경고: 상관 시뮬레이션 V0 ≠ 실제 기록 1건" in (result.out_dir / "summary.md").read_text(
        "utf-8")


# ── S11·S12 판독 재료 ─────────────────────────────────────────────────────────
def test_s11_policy_rules_and_sources(tmp_path: Path) -> None:
    """S11 — 정책값 · 규칙 표(비활성 행 포함 · 알람명 정규화) · APM 소스 × 등록 · 앱 영향 분포."""
    result = _run(tmp_path)
    s11 = _summary(result)["S11"]
    assert s11["cross_source_mode"] == "shadow" and s11["episode_idle_seconds"] == 900
    ids = [r["id"] for r in s11["rules"]]
    assert "host_down_to_was_all" in ids and s11["rules_loaded"] is True
    assert all(not r["enforce"] for r in s11["rules"])
    sources = {(r["source"], r["registered"]): r["alarms"] for r in s11["apm_sources"]}
    assert sources[("cm_prd", True)] >= 1 and sources[(APM_SOURCE_DEFAULT, False)] == 1
    unreg = [s for (s, reg) in sources if not reg and s != APM_SOURCE_DEFAULT]
    assert len(unreg) == 1 and re.fullmatch(r"S-[0-9a-f]{16}", unreg[0])
    assert s11["zone_empty_apm_alarms"] == 2
    assert s11["app_impact"]["error:gateway_error"] == 1
    assert s11["app_impact"][f"error:{OTHER}"] == 1
    assert s11["app_impact"]["fatal"] == 1
    snap = json.loads((result.out_dir / "policy_snapshot.json").read_text("utf-8"))["cross_source"]
    assert snap["cross_source_mode"] == "shadow" and snap["rules"]["loaded"] is True
    assert {s["id"] for s in snap["apm_sources"]} >= {"cm_prd"}
    assert "path" not in json.dumps(snap)            # 규칙 표 경로 값은 싣지 않는다(적재 결과만)


def test_s12_name_matching(tmp_path: Path) -> None:
    """S12 — 정합 사유 · 미정합 인스턴스 · 짝 비율 · 근접 불일치 · 토큰 후보 · 역방향 미정합."""
    s12 = _summary(_run(tmp_path))["S12"]
    assert s12["known"] is True
    assert s12["match_reason_alarms"][OTHER] == 1 and s12["match_reason_alarms"]["regex"] == 1
    assert s12["unresolved_instances"] == 4 and s12["token_candidate_instances"] == 1
    assert (s12["near_mismatch_loose"], s12["near_mismatch_contains"]) == (1, 1)
    assert s12["instance_unresolved_polestar_keys"] == 1
    assert 0 < s12["apm_keys_with_polestar_ratio"] < 1


def test_xs_row_shapes(tmp_path: Path) -> None:
    """행 `xs` — 폴스타 행은 APM 필드 없이, 키 빈 행은 `hk: null`과 사유만, 값은 허용 어휘만."""
    result = _run(tmp_path)
    pol = _xs_of(result, None, "FAKE-XS-A1")
    assert not {"match_reason", "inst", "apm_source", "was_kinds", "nearest_cause"} & set(pol)
    nohost = _xs_of(result, None, "FAKE-XS-I3")
    assert nohost == {"hk": None, "hk_reason": "no_hostname", "app_impact": None}
    weird = _xs_of(result, None, "jennifer:fakexsf5000000000")
    assert weird["hk"] is None and weird["hk_reason"] == "unresolved"
    assert (weird["match_reason"], weird["match_conf"]) == (OTHER, OTHER)
    assert weird["was_kinds"] == [OTHER]
    apm = _xs_of(result, None, "jennifer:fakexsh5000000000")
    assert apm["nearest_cause"] == {"lag_s": 240, "kind": "cpu", "tier": "page", "open": False}
    ip = _xs_of(result, None, "jennifer:fakexsi2000000000")
    assert ip["match_reason"] == "override" and ip["hk_strength"] == "strong"
    loose = _xs_of(result, None, "jennifer:fakexse2000000000")
    assert (loose["peer_7d"], loose["loose_peer"], loose["contains_peer"]) == (False, True, False)
    contains = _xs_of(result, None, "jennifer:fakexse4000000000")
    assert (contains["peer_7d"], contains["loose_peer"], contains["contains_peer"]) == (
        False, False, True)


# ── 단위 — 비식별 규칙 ─────────────────────────────────────────────────────────
def test_app_impact_code_keeps_code_only() -> None:
    """`app_impact_error`는 `" — "` 앞 코드만 · 그 밖은 `<other>` · 뒤 문구는 버린다."""
    assert app_impact_code("gateway_error — ConnectError: http://x 192.0.2.1") == "gateway_error"
    assert app_impact_code("instance_unresolved — fake") == "instance_unresolved"
    assert app_impact_code("apm_api_error — 500") == "apm_api_error"
    assert app_impact_code("weird_code — 198_51_100_7") == OTHER
    assert app_impact_code("fake-host.example.test 연결 실패") == OTHER
    assert app_impact_code(None) is None and app_impact_code("") is None


def test_apm_source_label() -> None:
    """등록 id는 그대로 · 단일 설정은 default · 미등록은 S- 가명(registered false)."""
    p = Pseudonymizer(KEY)
    reg = frozenset({"b0", "cm_prd"})
    assert apm_source_label("cm_prd", "jennifer_cm_prd", reg, p) == ("cm_prd", True)
    assert apm_source_label("", "jennifer", reg, p) == (APM_SOURCE_DEFAULT, False)
    assert apm_source_label("default", "jennifer", reg, p) == (APM_SOURCE_DEFAULT, False)
    label, flag = apm_source_label("cm_dev", "jennifer_cm_dev", reg, p)
    assert flag is False and label == p("S", "cm_dev")


def test_sanitize_xs_value_allow_list() -> None:
    """값 단위 허용 목록 — 열거 밖은 `<other>` · 수치 아니면 None · 규칙 id는 표의 id만."""
    p = Pseudonymizer(KEY)
    raw = {
        "host_key": "fake-x01", "hk_strength": "weird", "zone": "gongjon zone 192.0.2.1",
        "apm": {"source_id": "cm_prd", "db_id": "jennifer_cm_prd", "match_reason": "host_name",
                "match_conf": "high", "instance_name": "fake-inst", "was_kinds": ["was_gc_stall",
                                                                                 "fake-kind"]},
        "peer_7d": "yes", "nearest_cause": {"lag_s": "1830", "kind": "fake", "tier": "page",
                                            "open": True},
        "actual": {"episode_id": "ep-1", "rule_id": "fake-rule", "action": "fake",
                   "missing": ["window", "fake"], "applied": False, "lag_seconds": 1.5},
        "app_impact": {"error": "gateway_error — fake", "fatal_events": "3", "source": "fake"},
        "sim": {"V0": "link:window,cause_tier", "V1": "demote:fake", "V9": "member"},
    }
    out = sanitize_xs(raw, p, registered=frozenset({"cm_prd"}),
                      rule_ids=frozenset({"host_resource_to_was_latency"}))
    assert out is not None
    assert out["hk"] == p("K", "fake-x01") and out["hk_strength"] == OTHER
    assert out["zone"] == OTHER and out["zone_ok"] is True
    assert out["inst"] == p("I", "fake-inst") and out["was_kinds"] == ["was_gc_stall", OTHER]
    assert out["peer_7d"] is None
    assert out["nearest_cause"] == {"lag_s": None, "kind": OTHER, "tier": "page", "open": True}
    assert out["actual"]["episode"] == p("E", "ep-1") and out["actual"]["rule_id"] == OTHER
    assert out["actual"]["action"] == OTHER and out["actual"]["missing"] == ["window", OTHER]
    assert out["app_impact"] == {"code": "gateway_error", "fatal_events": None, "source": OTHER}
    assert out["sim"] == {"V0": "link:window,cause_tier", "V1": OTHER}
    assert "fake" not in json.dumps(out)


def test_new_pseudonym_prefixes_are_scrubbed_before_leak_check() -> None:
    """누출 검사 전 가명 제거 정규식이 새 접두(K·I·E·S)를 덮고, 낸 가명은 `emitted`에 남는다."""
    p = Pseudonymizer(KEY)
    for prefix in ("K", "I", "E", "S", "H"):
        out = str(p(prefix, "fake-x01"))
        assert _PSEUDONYM_RE.fullmatch(out.lower()) and out in p.emitted


# ── 교정 라운드 회귀 사례 (verifier·security-auditor 지적) ─────────────────────────
def test_db_id_label_registry_only() -> None:
    """교정 2 — 행 db_id는 등록 값만 원문 · 미등록 `jennifer_<id>`는 `S-` 가명 · 그 밖은
    `<other>`."""
    p = Pseudonymizer(KEY)
    reg = frozenset({"polestar_cm_gp"})
    src = frozenset({"cm_prd"})
    label = lambda v: db_id_label(v, db_ids=reg, apm_sources=src, pseudo=p)  # noqa: E731
    assert label("polestar_cm_gp") == "polestar_cm_gp"
    assert label("jennifer") == "jennifer" and label("jennifer_cm_prd") == "jennifer_cm_prd"
    assert label("jennifer_cm_dev") == "jennifer_" + str(p("S", "cm_dev"))
    assert label("jennifer_cm_dev")[len("jennifer_"):] == apm_source_label(
        "cm_dev", "jennifer_cm_dev", src, p)[0]
    # 적대적 접미 — IP·호스트형 원문도 가명 뒤로 숨는다.
    assert label("jennifer_198_51_100_7") == "jennifer_" + str(p("S", "198_51_100_7"))
    assert label("fake-secret-db") == OTHER and label("polestar_cm_gp2") == OTHER
    assert label(None) is None and label("") is None
    assert db_id_label("fake_db_x", db_ids=None, apm_sources=src, pseudo=p) == "fake_db_x"


def _adv_source(raw: dict[str, Any]) -> dict[str, Any]:
    if raw["data"]["dbId"] == "jennifer_cm_prd":
        raw["data"]["dbId"] = "jennifer_secsrc77"
        raw["data"]["apm"]["source_id"] = "secsrc77"
    return raw


def _adv_source_dec(rec: dict[str, Any]) -> dict[str, Any]:
    if rec.get("db_id") == "jennifer_cm_prd":
        rec["db_id"] = "jennifer_secsrc77"
    return rec


@pytest.mark.parametrize("stream", [True, False])
def test_unregistered_db_id_suffix_is_pseudonymized_in_rows_and_summary(
    tmp_path: Path, stream: bool
) -> None:
    """교정 2 — 미등록 소스 dbId 접미가 행 db_id·S1 by_db_id·md 어디에도 원문으로 없다."""
    from noise_gate.scripts.page_audit_sanitize import fold

    result = _run(tmp_path, ["A", "G"], stream=stream, raw_edit=_adv_source,
                  edit=_adv_source_dec)
    assert result.exit_code == 0, result.leak
    text = _folded_outputs(result.out_dir)
    for secret in ("secsrc77", "cm_dev", "cm-dev"):
        assert fold(secret) not in text, secret
    rows = _rows(result)
    apm = [r for r in rows if str(r["db_id"]).startswith("jennifer_")]
    assert apm and all(re.fullmatch(r"jennifer_S-[0-9a-f]{16}", r["db_id"]) for r in apm)
    if stream:
        assert all(r["db_id"] == "jennifer_" + r["xs"]["apm_source"] for r in apm)
    by_db = _summary(result)["S1"]["by_db_id"]
    assert "polestar_cm_gp" in by_db and all("secsrc" not in k for k in by_db)


def test_unregistered_db_id_suffix_leak_closes_run(tmp_path: Path) -> None:
    """교정 2 — 미등록 dbId 접미 원문은 누출 검사 식별자다(새면 종료 코드 2)."""

    class _RawDb(pa.Sanitizer):
        def row(self, ctx: Any) -> dict[str, Any]:
            out = super().row(ctx)
            out["raw_db"] = ctx.db_id
            return out

    result = _run(tmp_path, ["A"], raw_edit=_adv_source, edit=_adv_source_dec,
                  sanitizer_cls=_RawDb)
    assert result.exit_code == 2
    assert any(h["kind"] == f"identifier:{IDENT_APM_SOURCE}" for h in result.leak["hits"])


def test_mode_and_tier_hostile_values_are_allow_listed(tmp_path: Path) -> None:
    """교정 1 — S13 실제 모드·S14 원인 티어는 열거만(정상 값은 그대로 · 적대값은 `<other>`)."""

    def edit(rec: dict[str, Any]) -> dict[str, Any]:
        cs = (rec.get("stage_evidence") or {}).get("cross_source")
        if rec.get("alarm_id") == "FAKE-XS-A1":
            rec["tier"] = "fake-tier-198_51_100_7"
            if cs is not None:
                cs["mode"] = "fake-mode-x1"
        elif cs is not None:
            cs["mode"] = "enforce"
        return rec

    s = _summary(_run(tmp_path, ["A", "B"], edit=edit))
    assert set(s["S13"]["actual"]["modes"]) <= {"enforce", OTHER}
    assert s["S13"]["actual"]["modes"].get(OTHER) == 1
    assert set(s["S14"]["cause_tiers"]) <= {"page", "ticket", "dashboard", "suppress", OTHER}
    assert s["S14"]["cause_tiers"].get(OTHER) == 1


def test_vocabulary_exclusion_is_exact_word_and_spares_pinned_kinds() -> None:
    """교정 3 — 고정 어휘 제외는 단어 경계 완전 일치만 · 인스턴스·도메인·URL·호스트 키는 제외
    없음."""
    vocab = " ".join([*vocabulary_words(), *xs_vocabulary()])
    idents = IdentifierSet()
    idents.add(IDENT_RESOURCE, "Memory")                 # 어휘와 같은 이름 — 종전대로 제외
    idents.add(IDENT_RESOURCE, "ticke")                  # 어휘 `ticket`의 부분 문자열 — 제외 안 함
    idents.add(IDENT_INSTANCE, "memory")                 # 같은 이름의 인스턴스 — 고정 종류
    idents.add(IDENT_HOST, "default", pin=True)          # 같은 이름의 정규화 호스트 키
    idents.add(IDENT_HOST, "keep", pin=False)            # URL 조각 — 완전 일치면 제외를 받는다
    idents.exclude_vocabulary(vocab)
    assert leak_check({"x": "ticke-01"}, idents)["passed"] is False
    assert leak_check({"x": "memory"}, idents)["passed"] is False           # 인스턴스로 걸린다
    assert leak_check({"x": "source default"}, idents)["passed"] is False
    report = leak_check({"x": "whatif keep"}, idents)
    assert report["passed"] is True and report["vocabulary_identifiers_excluded"] == 2


def test_url_host_labels_and_path_pieces_are_identifiers() -> None:
    """교정 4 — URL 원문 · 호스트(`IDENT_HOST`) · 호스트 라벨 · 경로 조각(4자 이상)이 일치
    대상이다."""
    idents = IdentifierSet()
    xsource.collect_identifiers(idents, [{"apm": {
        "application": "http://fake-gw9.example.test:8080/ctx/fake-path-seg/x?q=1"}}], [])
    assert idents.count_hits("fake-gw9.example.test")[IDENT_HOST] == 1
    assert idents.count_hits("호스트 fake-gw9 끝")[IDENT_HOST] == 1
    assert idents.count_hits("경로 fake-path-seg 끝")[IDENT_URL] == 1
    assert not idents.count_hits("ctx x q")                   # 4자 미만 조각은 더하지 않는다


@pytest.mark.parametrize("variant", ["secinst--tomcat-9", "secinst/tomcat/9", "secinst_-_tomcat 9",
                                     "SECINST..TOMCAT//9"])
def test_repeated_separators_are_folded(variant: str) -> None:
    """교정 5 — 연속 구분자(`-_. /`)는 하나로 접혀 누출 검사·정규화 모두 잡는다."""
    idents = IdentifierSet()
    idents.add(IDENT_INSTANCE, "secinst-tomcat-9")
    assert leak_check({"x": f"인스턴스 {variant} 지연"}, idents)["passed"] is False
    # 정규화도 원문을 남기지 않는다(`/` 포함 토큰은 경로 자리표시자가 먼저 지운다).
    assert normalize_text(f"인스턴스 {variant} 지연", identifiers=idents) in (
        "인스턴스 <HOST> 지연", "인스턴스 <PATH> 지연")


def test_only_emitted_pseudonyms_are_scrubbed() -> None:
    """교정 6 — 가명 모양의 원문 식별자(`web-i-<16hex>`)는 지우지 않고, 낸 가명만 지운다."""
    p = Pseudonymizer(KEY)
    real = str(p("I", "fake-inst"))
    hexpart = real[2:]
    idents = IdentifierSet()
    idents.add(IDENT_HOST, "web-i-0123456789abcdef")
    idents.add(IDENT_RESOURCE, hexpart[3:9])             # 가명 hex와 우연히 겹치는 짧은 원문
    assert leak_check({"x": "web-i-0123456789abcdef"}, idents, p.emitted)["passed"] is False
    assert leak_check({"x": f"inst {real}"}, idents, p.emitted)["passed"] is True
    # 낸 적 없는 가명 모양은 지우지 않는다 → 겹친 원문이 그대로 걸린다.
    assert leak_check({"x": f"inst {real}"}, idents)["passed"] is False


def test_late_lag_threshold_comes_from_rules(tmp_path: Path) -> None:
    """교정 7 — S14·C2 「늦은 폴스타 선행」 문턱은 적재된 규칙 cause_before_seconds 최댓값이다."""
    from noise_gate.infrastructure.cross_source_rules import load_rules

    rules = load_rules("config/cross_source_rules.yaml")
    assert rules
    s14 = _summary(_run(tmp_path, ["D"]))["S14"]
    assert s14["late_lag_s"] == max(r.cause_before_seconds for r in rules)
    assert s14["polestar_first_beyond_window_ratio"] == 1.0
    assert "C2_LATE_LAG_S" not in vars(xsource)


def test_no_stream_record_only_rows_have_unknown_strength(tmp_path: Path) -> None:
    """교정 8 — 원문 없이 기록된 키로 만든 행의 `hk_strength`는 null(가짜 이벤트 값을 싣지
    않는다)."""
    rows = _rows(_run(tmp_path, stream=False))
    keyed = [r["xs"] for r in rows if r["xs"] and r["xs"]["hk"]]
    assert keyed and all(x["hk_strength"] is None for x in keyed)
    assert any(x.get("apm_source") for x in keyed)


def test_s12_unresolved_instance_alarms_and_md_extras(tmp_path: Path) -> None:
    """교정 9·11·12 — S12 미정합 인스턴스 알람 수 · md 레지스트리 오류 행 · 거짓 양성·희석 주석."""
    result = _run(tmp_path)
    s = _summary(result)
    raws = [r["data"] for r in _scenario_raws(None) if r["data"]["dbId"].startswith("jennifer")]
    expected = sum(1 for d in raws if not d.get("hostname") and d["apm"].get("instance_name"))
    assert s["S12"]["unresolved_inst_alarms"] == expected >= s["S12"]["unresolved_instances"]
    md = (result.out_dir / "summary.md").read_text("utf-8")
    assert "| 레지스트리 오류 |" in md and "unresolved_inst_alarms" in md
    assert any("거짓 양성" in n for n in s["S13"]["notes"])
    assert any("희석" in n for n in s["xs_readout"]["notes"])


# ── 사본 동기 — 게이트웨이 고정 어휘 ────────────────────────────────────────────
def _module_ast(path: Path) -> Any:
    import ast

    return ast.parse(path.read_text("utf-8"))


def test_was_kinds_copy_matches_gateway() -> None:
    """`WAS_KINDS` 사본 = 게이트웨이 `signals.LABELS` 키(import 없이 소스 텍스트로 대조)."""
    import ast

    tree = _module_ast(REPO / "apm_gateway" / "apm_gateway" / "domain" / "signals.py")
    keys: set[str] = set()
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == "LABELS":
            assert isinstance(node.value, ast.Dict)
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert keys and keys == set(WAS_KINDS)


def test_gateway_error_codes_copy_matches_gateway() -> None:
    """`GATEWAY_ERROR_CODES` 사본 = 게이트웨이 `errors.ERROR_CODES` 값(소스 텍스트로 대조)."""
    import ast

    tree = _module_ast(REPO / "apm_gateway" / "apm_gateway" / "domain" / "errors.py")
    consts: dict[str, str] = {}
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and \
                isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            consts[node.targets[0].id] = node.value.value
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "ERROR_CODES" and node.value is not None:
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)} - {"frozenset"}
    assert names and {consts[n] for n in names} == set(GATEWAY_ERROR_CODES)
