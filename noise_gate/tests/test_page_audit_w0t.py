"""`page_audit` 보정 (plans/162 W0-T · §12.5 T1~T6 · F4).

실 Redis·LLM·DB 0 — 가짜 Redis(호출 기록)와 tmp 경로만 쓴다. 입력은 `testdata/page_audit/w0t/`
(제니퍼 sev3 단락 PAGE 4종 · 같은 지문 반복 · 무소식 닫힘 · 판정 공백) 픽스처다.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig, NoiseGateConfig

from noise_gate.scripts import page_audit as pa
from noise_gate.scripts.page_audit_sanitize import OTHER

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "page_audit" / "w0t"
XS_FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "page_audit" / "xsource"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 15, 23, 0, 0, tzinfo=KST)
XS_NOW = datetime(2026, 10, 14, 23, 0, 0, tzinfo=KST)
KEY = b"w" * 32
READ_COMMANDS = {"xrange", "xinfo_groups", "xpending", "xinfo_consumers"}
OUTPUT_FILES = ("summary.json", "summary.md", "policy_snapshot.json", "leak_check.json",
                "alarms.jsonl")
# 픽스처 원문 — 출력 어디에도 없어야 한다(비교는 NFKC·casefold 뒤).
RAW_SECRETS = (
    "fake-w0t-web01", "fake-w0t-was01", "fake-w0t-was01_inst1", "fake-w0t-res01",
    "fake-w0t-domain", "fake-w0t-host01-worker", "192.0.2.", "ep-w0t-alpha",
    "fake load failure", "fakew0t", "FAKE-W0T-",
)


def _config(**noise: Any) -> SimpleNamespace:
    noise.setdefault("repeat_interval_seconds", 14400)
    noise.setdefault("sev3_repeat_interval_seconds", 14400)
    ng = NoiseGateConfig(
        enable_noise_gate=True,
        importance_value_map_csv="1=낮음,2=보통,3=높음",
        cross_source_mode="off",
        **noise,
    )
    alarm = AlarmConfig(
        min_severity=2,
        redis_stream_key="alarm:raw",
        redis_consumer_group="alarm-workers",
        dead_letter_stream_key="alarm:dead",
    )
    return SimpleNamespace(noise_gate=ng, alarm=alarm)


class FakeRedis:
    """읽기 4종만 구현 · 그 밖의 속성 접근은 호출로 기록한다."""

    def __init__(self, fixture: Path = FIXTURE) -> None:
        self.calls: list[str] = []
        lines = (fixture / "raw_stream.jsonl").read_text("utf-8").splitlines()
        self.raw = [
            (r["id"].encode(), {b"data": json.dumps(r["data"], ensure_ascii=False).encode()})
            for r in map(json.loads, lines)
        ]
        groups = json.loads((fixture / "stream_groups.json").read_text("utf-8"))
        self.groups = {k: [{gk.encode(): (gv.encode() if isinstance(gv, str) else gv)
                            for gk, gv in g.items()} for g in v] for k, v in groups.items()}
        pending_path = fixture / "stream_pending.json"
        meta = json.loads(pending_path.read_text("utf-8")) if pending_path.exists() else {}
        self.pending = meta.get("xpending", {"pending": 0, "consumers": []})
        self.consumers = meta.get("xinfo_consumers", [])

    @staticmethod
    def _id(text: str, upper: bool) -> tuple[int, int]:
        if text in ("-", "+"):
            return (0, 0) if text == "-" else (2**63, 2**63)
        ms, sep, seq = text.partition("-")
        return (int(ms), int(seq)) if sep else (int(ms), 2**63 if upper else 0)

    def xrange(self, name: str, min: str = "-", max: str = "+",
               count: int | None = None) -> list[tuple[bytes, dict[bytes, bytes]]]:
        self.calls.append("xrange")
        lo, hi = self._id(min, False), self._id(max, True)
        items = [(i, f) for i, f in (self.raw if name == "alarm:raw" else [])
                 if lo <= self._id(i.decode(), False) <= hi]
        return items[:count] if count else items

    def xinfo_groups(self, name: str) -> list[dict[bytes, Any]]:
        self.calls.append("xinfo_groups")
        return self.groups.get(name, [])

    def xpending(self, name: str, groupname: str) -> dict[bytes, Any]:
        self.calls.append("xpending")
        return {k.encode(): (v.encode() if isinstance(v, str) else v)
                for k, v in self.pending.items()}

    def xinfo_consumers(self, name: str, groupname: str) -> list[dict[bytes, Any]]:
        self.calls.append("xinfo_consumers")
        return [{k.encode(): (v.encode() if isinstance(v, str) else v) for k, v in c.items()}
                for c in self.consumers]

    def __getattr__(self, name: str) -> Any:
        def _call(*a: Any, **k: Any) -> None:
            self.calls.append(name)
        return _call


def _write(tmp_path: Path, recs: list[dict[str, Any]], name: str = "d.jsonl") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    return path


def _fixture_decisions(tmp_path: Path, extra: list[dict[str, Any]] | None = None) -> Path:
    recs = [json.loads(x) for x in (FIXTURE / "decisions.jsonl").read_text("utf-8").splitlines()]
    return _write(tmp_path, recs + (extra or []))


def _run(tmp_path: Path, *, stream: str | None = "consumers", decisions: Path | None = None,
         fake: FakeRedis | None = None, **kwargs: Any) -> pa.AuditResult:
    fake = fake or FakeRedis()
    streams: pa.ReadOnlyStreams | None = None
    if stream == "consumers":
        streams = pa.ConsumerInfoStreams(fake)
    elif stream == "plain":
        streams = pa.ReadOnlyStreams(fake)
    kwargs.setdefault("config", _config())
    return pa.run_audit(
        decisions_path=decisions or _fixture_decisions(tmp_path), out_root=tmp_path / "out",
        hours=24, days=7, streams=streams, now=NOW, key=KEY, **kwargs,
    )


def _summary(result: pa.AuditResult) -> dict[str, Any]:
    assert result.exit_code == 0, result.leak
    assert result.out_dir is not None
    return json.loads((result.out_dir / "summary.json").read_text("utf-8"))


def _snapshot(result: pa.AuditResult) -> dict[str, Any]:
    assert result.out_dir is not None
    return json.loads((result.out_dir / "policy_snapshot.json").read_text("utf-8"))


def _rows(result: pa.AuditResult) -> list[dict[str, Any]]:
    assert result.out_dir is not None
    text = (result.out_dir / "alarms.jsonl").read_text("utf-8")
    return [json.loads(x) for x in text.splitlines()]


def _by_name(result: pa.AuditResult, name: str) -> list[dict[str, Any]]:
    return [r for r in _rows(result) if r["alarm_name_norm"] == name and r["tier"] is not None]


def _blob(result: pa.AuditResult) -> str:
    assert result.out_dir is not None
    text = "\n".join((result.out_dir / f).read_text("utf-8") for f in OUTPUT_FILES)
    return unicodedata.normalize("NFKC", text).casefold()


def _dec(sec: float, server: str, name: str, *, evidence: dict[str, Any] | None = None,
         db: str = "polestar_cm_gp", stage: str = "matrix", sev: int = 2,
         tier: str = "page") -> dict[str, Any]:
    rec: dict[str, Any] = {
        "ts": (NOW - timedelta(hours=2) + timedelta(seconds=sec)).astimezone(UTC).isoformat(),
        "alarm_id": f"W0T-{server}-{int(sec)}", "tier": tier, "reason": "r", "priority": 1,
        "fingerprint": hashlib.sha1(f"{server}|{name}".encode()).hexdigest(),
        "signals": {"severity": sev, "effective_severity": sev, "importance": "보통"},
        "alarm_name": name, "server_name": server, "db_id": db, "resource_name": "res-0001",
        "stage": stage,
    }
    if evidence is not None:
        rec["stage_evidence"] = evidence
    return rec


# ── 누출 0 · 읽기 명령만 ──────────────────────────────────────────────────────
def test_fixture_run_passes_leak_check_and_hides_raw_identifiers(tmp_path: Path) -> None:
    result = _run(tmp_path, idle_close_minutes=[30, 60])
    assert result.exit_code == 0, result.leak
    assert result.leak["passed"] is True
    blob = _blob(result)
    for secret in RAW_SECRETS:
        assert unicodedata.normalize("NFKC", secret).casefold() not in blob, secret


def test_consumer_info_streams_exposes_read_commands_only(tmp_path: Path) -> None:
    public = {n for n, _ in inspect.getmembers(pa.ConsumerInfoStreams) if not n.startswith("_")}
    assert public == READ_COMMANDS
    assert {n for n, _ in inspect.getmembers(pa.ReadOnlyStreams)
            if not n.startswith("_")} == READ_COMMANDS - {"xinfo_consumers"}
    fake = FakeRedis()
    _run(tmp_path, fake=fake)
    assert "xinfo_consumers" in fake.calls
    assert fake.calls and set(fake.calls) <= READ_COMMANDS


# ── T1 정규화 유형 · 정책 표 일치 ─────────────────────────────────────────────
def test_t1_event_type_norm_kept_when_gateway_form_else_pseudonym(tmp_path: Path) -> None:
    result = _run(tmp_path)
    fake_type = _by_name(result, "FAKE_EVENT_TYPE")[0]["evidence"]["apm_policy"]
    assert fake_type == {"event_type_norm": "FAKE_EVENT_TYPE", "in_policy_table": False,
                         "persistence": None}
    queuing = _by_name(result, "SERVICE_QUEUING")[0]["evidence"]["apm_policy"]
    assert queuing["event_type_norm"] == "SERVICE_QUEUING" and queuing["in_policy_table"] is True
    db_fail = _by_name(result, "DB_CONNECTION_FAIL")[0]["evidence"]["apm_policy"]
    assert db_fail["event_type_norm"] == "DB_CONNECTION_FAIL" and db_fail["in_policy_table"]
    lower = _by_name(result, "fake lower type")[0]["evidence"]["apm_policy"]
    assert lower["event_type_norm"].startswith("N-") and lower["in_policy_table"] is False
    assert _snapshot(result)["apm_policy_table"]["loaded"] is True


@pytest.mark.parametrize("value", ["fake lower type", "Fake_Type", "1234", "A", "X" * 41,
                                   "FAKE-TYPE", "FAKE TYPE"])
def test_t1_non_gateway_form_values_become_pseudonyms(tmp_path: Path, value: str) -> None:
    recs = [_dec(0, "fake-w0t-t1", "x", evidence={"apm_policy": {"event_type_norm": value}})]
    result = _run(tmp_path, stream=None, decisions=_write(tmp_path, recs))
    (row,) = _rows(result)
    got = row["evidence"]["apm_policy"]["event_type_norm"]
    assert got.startswith("N-") and got != value


def test_t1_event_type_carrying_run_identifier_is_pseudonymized(tmp_path: Path) -> None:
    """정규화 형식이어도 이번 실행 식별자(서버명)를 담으면 가명이다."""
    recs = [_dec(0, "fakew0tid9", "x",
                 evidence={"apm_policy": {"event_type_norm": "FAKEW0TID9"}})]
    result = _run(tmp_path, stream=None, decisions=_write(tmp_path, recs))
    (row,) = _rows(result)
    assert row["evidence"]["apm_policy"]["event_type_norm"].startswith("N-")
    assert "fakew0tid9" not in _blob(result)


def test_t1_vocabulary_overlap_keeps_type_and_leak_check_passes(tmp_path: Path) -> None:
    """(교정) 정적 어휘(`memory`)와만 겹치는 자원명은 유형 판정에서 빠진다 — 실제 식별자를 담은
    유형은 계속 `N-`이고 누출 검사는 통과한다."""
    pol = _dec(0, "fake-w0t-p1", "메모리 사용률")
    pol["resource_name"] = "Memory"
    keep = _dec(60, "fake-w0t-j1", "OUT_OF_MEMORY", db="jennifer_cm_prd",
                evidence={"apm_policy": {"event_type_norm": "OUT_OF_MEMORY"}})
    hide = _dec(120, "fakew0tvoc", "y", db="jennifer_cm_prd",
                evidence={"apm_policy": {"event_type_norm": "MEMORY_FAKEW0TVOC"}})
    result = _run(tmp_path, stream=None, decisions=_write(tmp_path, [pol, keep, hide]))
    assert result.exit_code == 0 and result.leak["passed"] is True
    got = [r["evidence"]["apm_policy"]["event_type_norm"] for r in _rows(result)
           if (r["evidence"] or {}).get("apm_policy")]
    assert got[0] == "OUT_OF_MEMORY"
    assert got[1].startswith("N-")
    assert "fakew0tvoc" not in _blob(result)


def test_sanitize_evidence_without_identifiers_fails_closed() -> None:
    from collections import Counter

    from noise_gate.scripts.page_audit_sanitize import Pseudonymizer, sanitize_evidence

    out = sanitize_evidence(
        {"apm_policy": {"event_type_norm": "SERVICE_QUEUING"},
         "apm_sev3_policy": {"notify": "page", "policy_row": "SERVICE_QUEUING"}},
        Counter(), Pseudonymizer(KEY), policy_keys=frozenset({"SERVICE_QUEUING"}),
    )
    assert out["apm_policy"]["event_type_norm"].startswith("N-")
    assert out["apm_policy"]["in_policy_table"] is True
    assert out["apm_sev3_policy"]["policy_row"].startswith("N-")


def test_t1_policy_table_unloaded_omits_match(tmp_path: Path) -> None:
    result = _run(tmp_path, policy_path=tmp_path / "missing.yaml")
    queuing = _by_name(result, "SERVICE_QUEUING")[0]["evidence"]["apm_policy"]
    assert queuing["event_type_norm"] == "SERVICE_QUEUING"
    assert "in_policy_table" not in queuing
    assert _snapshot(result)["apm_policy_table"] == {"loaded": False, "type_keys": 0}


def test_apm_sev3_policy_evidence_allow_list(tmp_path: Path) -> None:
    recs = [
        _dec(0, "fake-w0t-s1", "x", evidence={
            "apm_sev3_policy": {"notify": "page", "policy_row": "SERVICE_QUEUING"}}),
        _dec(60, "fake-w0t-s2", "y", evidence={
            "apm_sev3_policy": {"notify": "ticket", "policy_row": "default"}}),
        _dec(120, "fake-w0t-s3", "z", evidence={
            "apm_sev3_policy": {"notify": "fake-w0t-notify", "policy_row": "fake-w0t-row",
                                "extra": 1}}),
        _dec(180, "fake-w0t-s4", "w", evidence={
            "apm_sev3_policy": {"notify": ["page"], "policy_row": {"k": "v"}}}),
    ]
    result = _run(tmp_path, stream=None, decisions=_write(tmp_path, recs))
    got = [r["evidence"]["apm_sev3_policy"] for r in _rows(result)]
    assert got[0] == {"notify": "page", "policy_row": "SERVICE_QUEUING"}
    assert got[1] == {"notify": "ticket", "policy_row": "default"}
    assert got[2]["notify"] == OTHER and got[2]["policy_row"].startswith("N-")
    assert "extra" not in got[2]
    assert got[3]["policy_row"] is None
    blob = _blob(result)
    assert "fake-w0t-notify" not in blob and "fake-w0t-row" not in blob
    # 허용 목록 밖 하위 키는 이름도 숨긴다.
    assert _summary(result)["S10"]["dropped_evidence_keys"] == {"<unknown_key>": 1}


# ── T2 S6 출처별 ──────────────────────────────────────────────────────────────
def test_t2_s6_by_source_and_sources_without_clear_events(tmp_path: Path) -> None:
    s6 = _summary(_run(tmp_path))["S6"]
    assert set(s6["by_source"]) == {"polestar", "jennifer"}
    assert s6["by_source"]["polestar"]["unresolved"] == 0
    assert s6["by_source"]["jennifer"]["pages"] == s6["by_source"]["jennifer"]["unresolved"]
    assert s6["clear_events_by_source_long"] == {"jennifer": 0, "polestar": 1}
    assert s6["sources_without_clear_events"] == ["jennifer"]


# ── T3 S9 출처별 · 제니퍼 지문 원천 ───────────────────────────────────────────
def test_t3_s9_by_source_and_jennifer_fp_source(tmp_path: Path) -> None:
    s9 = _summary(_run(tmp_path))["S9"]
    assert set(s9["by_source"]) == {"polestar", "jennifer"}
    for key in ("host_name_groups_multi_fp", "fps_in_those_groups"):
        assert s9[key] == sum(v[key] for v in s9["by_source"].values())
    jfp = s9["jennifer_fp_source"]
    assert jfp["rows_without_instance"] == 0
    # 유형 7종(같은 인스턴스) — (유형, 인스턴스) 원천마다 지문 하나.
    assert jfp["fp_source_groups"] == 7 and jfp["fp_source_groups_multi_fp"] == 0


# ── T4 S1 소비 지표 · XINFO CONSUMERS ─────────────────────────────────────────
def test_t4_s1_delivered_unacked_hourly_match_and_gaps(tmp_path: Path) -> None:
    s1 = _summary(_run(tmp_path))["S1"]
    assert s1["delivered_unacked"] == {"xpending_total": 2, "unaccounted_in_pending_range": 2}
    assert s1["hourly_match"]["2026-10-15T15"] == {"received": 2, "decided_or_dedup": 0,
                                                    "ratio": 0.0}
    assert s1["hourly_match"]["2026-10-15T10"]["ratio"] == 1.0
    assert s1["decision_gaps"] == [{"start": "2026-10-15T15:00", "end": "2026-10-15T16:00",
                                    "hours": 1, "received": 2}]


def test_t4_snapshot_consumer_details_enumerates_names(tmp_path: Path) -> None:
    stream = _snapshot(_run(tmp_path))["stream"]
    assert stream["xpending_min_id"] and stream["xpending_max_id"]
    assert stream["consumer_details"] == [
        {"name": "worker-1", "pending": 0, "idle_ms": 1200, "inactive_ms": 1500},
        {"name": OTHER, "pending": 2, "idle_ms": 3600000, "inactive_ms": 3700000},
    ]


def test_t4_plain_streams_and_no_stream(tmp_path: Path) -> None:
    plain = _run(tmp_path / "a", stream="plain")
    assert "consumer_details" not in _snapshot(plain)["stream"]
    none = _summary(_run(tmp_path / "b", stream=None))["S1"]
    assert none["delivered_unacked"] is None and none["hourly_match"] is None
    assert none["decision_gaps"] is None


# ── T5 사건 id 이름 계수 ──────────────────────────────────────────────────────
def test_t5_episode_id_named_and_shares_xs_pseudonym(tmp_path: Path) -> None:
    result = pa.run_audit(
        config=_config(), decisions_path=XS_FIXTURE / "decisions.jsonl",
        out_root=tmp_path / "out", hours=24, days=7, streams=None, now=XS_NOW, key=KEY,
    )
    s10 = _summary(result)["S10"]
    assert "<unknown_key>" not in s10["dropped_evidence_keys"]
    assert s10["named_evidence_keys"]["episode_id"] == 4
    rows = [r for r in _rows(result) if "episode_id" in (r["evidence"] or {})]
    assert len(rows) == 4
    for row in rows:
        assert row["evidence"]["episode_id"].startswith("E-")
        assert row["evidence"]["episode_id"] == row["xs"]["actual"]["episode"]


def test_t5_episode_id_in_w0t_fixture(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert _summary(result)["S10"]["named_evidence_keys"] == {"episode_id": 1}
    (row,) = _by_name(result, "SERVICE_QUEUING")
    assert row["evidence"]["episode_id"].startswith("E-")


# ── T6 S8 추가 시나리오 · 제니퍼 간격 분포 ─────────────────────────────────────
def test_t6_apm_sev3_policy_scenario(tmp_path: Path) -> None:
    s8 = _summary(_run(tmp_path))["S8"]
    sc = s8["scenarios"]
    assert set(sc) == {"APM_SEV3_POLICY", "R1~R3", "APM_SEV3_POLICY+R1~R3"}
    apm = sc["APM_SEV3_POLICY"]
    # 원 PAGE 8건 = 폴스타 1 + 제니퍼 sev3 5 + 제니퍼 sev2 2.
    assert s8["pages_before"] == 8
    # 표 밖(FAKE_EVENT_TYPE) · 표 밖(가명 유형) → TICKET · 근거 없음 → 재현 불가(PAGE 유지) ·
    # 정책 적재 실패 → 단락 PAGE · notify 행 → PAGE.
    assert apm["demoted"] == 2 and apm["pages_after"] == 6
    assert apm["unreproducible"] == 1
    assert apm["demoted_origin_stage"] == {"severity3": 2}
    # R1~R3 단독: 같은 호스트 30분 창(R2)이 01:10·01:20·01:30 sev3를, R1이 04:30 반복을 내린다.
    assert sc["R1~R3"]["demoted"] == 4
    # 누적: 정책이 먼저 2건을 내리고(대표가 되지 않음) R2가 01:30 1건 · R1이 04:30 1건.
    assert sc["APM_SEV3_POLICY+R1~R3"]["demoted"] == 4
    assert sc["APM_SEV3_POLICY+R1~R3"]["unreproducible"] == 1
    assert sc["R1~R3"]["unreproducible"] == 0
    # 기존 rules에는 섞이지 않는다.
    assert "APM_SEV3_POLICY" not in s8["rules"]
    assert set(s8["rules"]) == {"R4", "R5", "R1", "R2", "R3", "R6", "cumulative"}
    assert s8["orphans_total"] == sum(v["orphans"] for v in s8["rules"].values())


def test_t6_label_reuses_domain_decision() -> None:
    def _decision(evidence: dict[str, Any] | None, noti: str | None) -> pa.Decision:
        rec = _dec(0, "fake-w0t-l", "SERVICE_QUEUING", db="jennifer_cm_prd", stage="severity3",
                   sev=3, evidence=evidence)
        rec["signals"]["noti_policy"] = noti
        return pa.Decision(idx=0, ts=NOW, rec=rec)

    row = {"apm_policy": {"event_type_norm": "SERVICE_QUEUING", "policy_row": "SERVICE_QUEUING"}}
    assert pa.apm_sev3_label(_decision(row, "notify"), jennifer=True) == "page"
    assert pa.apm_sev3_label(_decision(row, None), jennifer=True) == "ticket"
    assert pa.apm_sev3_label(_decision(None, "notify"), jennifer=True) == "unreproducible"
    failed = {"apm_policy": {"event_type_norm": "X_Y", "policy_row": "", "policy_error": "e"}}
    assert pa.apm_sev3_label(_decision(failed, None), jennifer=True) == "page"
    assert pa.apm_sev3_label(_decision(row, "notify"), jennifer=False) is None


def test_t6_jennifer_event_intervals_include_recurrence(tmp_path: Path) -> None:
    gaps = _summary(_run(tmp_path))["S8"]["jennifer_event_intervals"]
    assert gaps["recurrences_included"] == 1
    # HTTP_IO_EXCEPTION 3건 + 재발 1건 → 간격 3 · PLC_REJECTED 3건 → 간격 2.
    assert gaps["gaps"] == 5
    assert sum(gaps["histogram"].values()) == gaps["gaps"]
    assert gaps["histogram"]["1~5분"] == 1 and gaps["histogram"]["5~15분"] == 2
    assert gaps["histogram"]["30분~1시간"] == 2
    assert gaps["max_s"] == 3000


# ── F4 무소식 닫힘 ────────────────────────────────────────────────────────────
def test_f4_idle_close_scenarios_per_value(tmp_path: Path) -> None:
    s8 = _summary(_run(tmp_path, idle_close_minutes=[60, 30, 0]))["S8"]
    sc = s8["scenarios"]
    assert s8["scenario_params"]["idle_close_minutes"] == [0, 30, 60]
    # 03:00 PAGE → 03:50 비-PAGE(소식) → 04:30 PAGE: 닫힘 없으면 R1 반복 강등(R2 3건 별도).
    assert sc["R1~R3"]["demoted"] == 4
    # 30분: 03:50 소식 뒤 40분 무소식 → 닫힘 → 04:30은 새 사건(유지).
    assert sc["R1~R3@idle=30m"]["demoted"] == 3
    # 60분: 03:50 소식(touch)이 사건을 열어 둔다 → 반복 강등.
    assert sc["R1~R3@idle=60m"]["demoted"] == 4
    assert "APM_SEV3_POLICY+R1~R3@idle=30m" in sc and "APM_SEV3_POLICY@idle=30m" not in sc


def test_f4_idle_zero_is_bit_identical(tmp_path: Path) -> None:
    base = _summary(_run(tmp_path / "a"))
    zero = _summary(_run(tmp_path / "b", idle_close_minutes=[0]))
    assert base == zero


def test_f4_cli_passes_idle_close_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config as cfg_mod

    seen: dict[str, Any] = {}

    def _fake_run(**kwargs: Any) -> pa.AuditResult:
        seen.update(kwargs)
        return pa.AuditResult(exit_code=0, out_dir=None, leak={"passed": True})

    monkeypatch.setattr(cfg_mod, "AppConfig", lambda: _config())
    monkeypatch.setattr(pa, "open_streams", lambda *_: pytest.fail("Redis를 열면 안 된다"))
    monkeypatch.setattr(pa, "run_audit", _fake_run)
    code = pa.main(["--no-stream", "--decisions", str(_fixture_decisions(tmp_path)),
                    "--out", str(tmp_path / "out"), "--idle-close-minutes", "30", "60"])
    assert code == 0 and list(seen["idle_close_minutes"]) == [30, 60]
