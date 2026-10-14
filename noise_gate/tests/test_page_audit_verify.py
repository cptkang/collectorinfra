"""`page_audit` 독립 검증 (plans/162 W0 §10.9 — verifier).

구현자 테스트(`test_page_audit.py`)와 독립된 시퀀스로 수용 기준을 다시 확인한다. 실 Redis·LLM·DB 0 —
가짜 Redis(호출 기록)와 tmp 경로만 쓴다. 설정은 그룹 객체에 검증 대상 필드를 명시해 주입한다.

결함을 드러내는 테스트는 실패한 그대로 둔다(이름에 `defect_` 접두).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig, NoiseGateConfig

from noise_gate.scripts import page_audit as pa
from noise_gate.scripts.page_audit_sanitize import Pseudonymizer

KST = timezone(timedelta(hours=9))
BASE = datetime(2026, 10, 12, 1, 0, 0, tzinfo=KST)
KEY = b"v" * 32
READ_COMMANDS = {"xrange", "xinfo_groups", "xpending"}


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


def _ms(sec: float) -> int:
    return int((BASE + timedelta(seconds=sec)).timestamp() * 1000)


class FakeRedis:
    """읽기 3종만 구현 · 그 밖의 속성 접근은 호출로 기록한다."""

    def __init__(self, streams: dict[str, list[tuple[bytes, dict[bytes, bytes]]]],
                 groups: dict[str, list[dict[bytes, Any]]] | None = None) -> None:
        self.streams = streams
        self.groups = groups or {}
        self.calls: list[str] = []

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
        items = [(i, f) for i, f in self.streams.get(name, [])
                 if lo <= self._id(i.decode(), False) <= hi]
        return items[:count] if count else items

    def xinfo_groups(self, name: str) -> list[dict[bytes, Any]]:
        self.calls.append("xinfo_groups")
        return self.groups.get(name, [])

    def xpending(self, name: str, groupname: str) -> dict[bytes, Any]:
        self.calls.append("xpending")
        return {b"pending": 0, b"consumers": []}

    def __getattr__(self, name: str) -> Any:
        def _call(*a: Any, **k: Any) -> None:
            self.calls.append(name)
        return _call


def _payload(alarm_id: str, server: str, name: str, sev: int, *, res: str = "res-0001",
             log: str = "", rtype: str = "server.Cpus") -> dict[str, Any]:
    return {"dbId": "polestar_cm_gp", "serverName": server, "hostname": server,
            "ipAddress": "", "resourceAncestry": f"/Servers/{server}/x", "alarmId": alarm_id,
            "severity": sev, "alarmStatus": "", "resourceType": rtype, "resourceName": res,
            "alarmName": name, "alarmTime": "20261012010000", "conditions": "> 90",
            "conditionLog": log}


def _raw(sec: float, payload: dict[str, Any], seq: int = 0) -> tuple[bytes, dict[bytes, bytes]]:
    return (f"{_ms(sec)}-{seq}".encode(),
            {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")})


def _group(last_sec: float, seq: int = 0) -> dict[str, list[dict[bytes, Any]]]:
    return {"alarm:raw": [{b"name": b"alarm-workers", b"consumers": 1, b"pending": 0,
                           b"last-delivered-id": f"{_ms(last_sec)}-{seq}".encode(), b"lag": 0}]}


def _dec(sec: float, server: str, name: str, *, stage: str | None = "matrix", sev: int = 2,
         eff: int | None = None, fp: str | None = None, tier: str = "page", aid: str | None = None,
         res: str = "res-0001", log: str = "", evidence: dict[str, Any] | None = None,
         **extra: Any) -> dict[str, Any]:
    eff = sev if eff is None else eff
    rec: dict[str, Any] = {
        "ts": (BASE + timedelta(seconds=sec)).astimezone(UTC).isoformat(),
        "alarm_id": aid or f"VER-{server}-{int(sec)}", "tier": tier, "reason": "r", "priority": 1,
        "fingerprint": fp or hashlib.sha1(f"{server}|{name}|{res}".encode()).hexdigest(),
        "signals": {"severity": sev, "effective_severity": eff, "importance": "보통"},
        "alarm_name": name, "server_name": server, "db_id": "polestar_cm_gp",
        "resource_name": res,
    }
    if stage is not None:
        rec["stage"] = stage
    if log:
        rec["condition_log"] = log
    if evidence:
        rec["stage_evidence"] = evidence
    rec.update(extra)
    return rec


def _clear(sec: float, server: str, name: str, *, fp: str | None = None) -> dict[str, Any]:
    return _dec(sec, server, name, fp=fp, tier="suppress", sev=0, stage="resolved")


def _write(tmp_path: Path, recs: list[Any], name: str = "d.jsonl") -> Path:
    path = tmp_path / name
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in recs]
    path.write_text("".join(x + "\n" for x in lines), "utf-8")
    return path


def _run(tmp_path: Path, recs: list[Any], *, now_sec: float, streams: Any = None,
         **kwargs: Any) -> pa.AuditResult:
    kwargs.setdefault("config", _config())
    kwargs.setdefault("key", KEY)
    return pa.run_audit(decisions_path=_write(tmp_path, recs), out_root=tmp_path / "out",
                        streams=streams, now=BASE + timedelta(seconds=now_sec), **kwargs)


def _rows(result: pa.AuditResult) -> list[dict[str, Any]]:
    assert result.out_dir is not None
    text = (result.out_dir / "alarms.jsonl").read_text("utf-8")
    return [json.loads(x) for x in text.splitlines()]


def _summary(result: pa.AuditResult) -> dict[str, Any]:
    assert result.out_dir is not None
    return json.loads((result.out_dir / "summary.json").read_text("utf-8"))


def _pages(result: pa.AuditResult) -> list[dict[str, Any]]:
    return [r for r in _rows(result) if r["tier"] == "page" and not r["is_clear"]]


H = 3600


# ── 1 누출 0 · 정규화 ─────────────────────────────────────────────────────────
def test_alarm_name_with_ipv6_mac_email_and_foreign_host_is_normalized(tmp_path: Path) -> None:
    recs = [
        _dec(0, "verhost-alpha", "다운 2001:db8::1 aa:bb:cc:dd:ee:01 me@ex.test VERHOST-BETA"),
        _dec(60, "verhost-beta", "기타", res="verres-9001"),
    ]
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code == 0
    first = _rows(result)[0]
    assert first["alarm_name_norm"] == "다운 <IP> <MAC> <EMAIL> <HOST>"
    blob = "\n".join(p.read_text("utf-8") for p in result.out_dir.iterdir()).lower()
    for secret in ("verhost-alpha", "verhost-beta", "2001:db8::1", "me@ex.test", "verres-9001"):
        assert secret not in blob


def test_evidence_value_carrying_hostname_is_neutralized(tmp_path: Path) -> None:
    """허용 키의 문자열 값(markers)에 원문 호스트가 실려도 허용 값 목록이 `<other>`로 바꿔
    새지 않는다 — 출력 전 파일에 원문이 없고 정상 종료(누출 0으로 닫힘)."""
    recs = [_dec(0, "verhost-gamma", "x", evidence={"markers": ["verhost-gamma 점검"]})]
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code == 0, result.leak
    assert result.out_dir is not None
    files = list(result.out_dir.iterdir())
    assert "alarms.jsonl" in [p.name for p in files]
    for path in files:
        assert "verhost-gamma" not in path.read_text("utf-8").casefold()
    leak = json.loads((result.out_dir / "leak_check.json").read_text("utf-8"))
    assert leak["hits"] == []


def test_defect_numeric_alarm_id_false_positive_closes_run(tmp_path: Path) -> None:
    """폴스타 alarmId는 숫자형(문서 예 "1234567"). 출력의 수치(lag_ms 등)와 같은 값이면 원문 누출이
    없어도 닫힌다(종료 2 · 분석 파일 0) — 운영 7일 실행에서 재기동 적체 lag_ms와 겹칠 수 있다."""
    payload = _payload("1234567", "verhost-num", "CPU 사용률 임계 초과", 3)
    fake = FakeRedis({"alarm:raw": [_raw(0, payload)]}, _group(0))
    # 수신 0초 · 판정 1234.567초 뒤 → lag_ms 1234567
    recs = [_dec(1234.567, "verhost-num", "CPU 사용률 임계 초과", sev=3, aid="1234567")]
    result = _run(tmp_path, recs, now_sec=2 * H, streams=pa.ReadOnlyStreams(fake))
    assert result.exit_code == 0, result.leak


def test_defect_numeric_alarm_id_equal_to_rule_param_closes_run(tmp_path: Path) -> None:
    """S8 params(1800·86400)는 매 실행 summary에 실린다 —
    그 값의 alarm_id가 하나라도 있으면 닫힌다."""
    recs = [_dec(0, "verhost-p", "x", aid="86400")]
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code == 0, result.leak


# ── 3 가명 ────────────────────────────────────────────────────────────────────
def test_pseudonym_shared_across_24h_and_7d_windows_in_one_run(tmp_path: Path) -> None:
    recs = [_dec(d * 86400, "VerHost-Daily", "만성", fp="d" * 40) for d in range(7)]
    result = _run(tmp_path, recs, now_sec=6 * 86400 + H, hours=24, days=7)
    rows = _rows(result)
    assert len(rows) == 7
    assert [r["in_hours_window"] for r in rows] == [False] * 6 + [True]
    hosts = {r["host"] for r in rows}
    assert hosts == {Pseudonymizer(KEY)("H", "verhost-daily")}  # 대소문자 정규화 후 1개
    s = _summary(result)
    assert s["S8"]["pages_before"] == 1
    assert s["S8"]["daily_avg_long_before"] == 1.0
    assert s["S1"]["dispositions"]["decided"] == 1  # S1은 짧은 창만


def test_pseudonyms_differ_without_injected_key(tmp_path: Path) -> None:
    recs = [_dec(0, "verhost-k", "x")]
    path = _write(tmp_path, recs)
    hosts = []
    for i in range(2):
        res = pa.run_audit(config=_config(), decisions_path=path, out_root=tmp_path / f"o{i}",
                           now=BASE + timedelta(hours=1))
        hosts.append(_rows(res)[0]["host"])
    assert hosts[0] != hosts[1]


# ── 4 처분 ────────────────────────────────────────────────────────────────────
def test_dispositions_boundaries(tmp_path: Path) -> None:
    raws = [
        _raw(0, _payload("VA-0001", "verhost-a", "a", 3)),           # decided (판정 59초 전)
        _raw(10, _payload("VA-0002", "verhost-b", "b", 3)),          # 판정 61초 전 → 맞대기 밖
        _raw(20, _payload("VA-0003", "verhost-c", "c", 1)),          # below_min_severity
        _raw(30, _payload("VA-0004", "verhost-d", "d", 0)),          # 해소 sev0 → unaccounted
        _raw(40, _payload("VA-0005", "verhost-e", "e", 3)),          # = last-delivered
        _raw(50, _payload("VA-0006", "verhost-f", "f", 3)),          # 뒤 → pending
        _raw(55, _payload("VA-0007", "verhost-g", "g", 3)),          # 판정 3599초 뒤 → decided
    ]
    fake = FakeRedis({"alarm:raw": raws}, _group(40))
    recs = [
        _dec(-59, "verhost-a", "a", aid="VA-0001", sev=3),
        _dec(-51, "verhost-b", "b", aid="VA-0002", sev=3),
        _dec(55 + 3599, "verhost-g", "g", aid="VA-0007", sev=3),
    ]
    result = _run(tmp_path, recs, now_sec=2 * H, streams=pa.ReadOnlyStreams(fake))
    assert result.exit_code == 0
    p = Pseudonymizer(KEY)
    disp = {r["host"]: r["disposition"] for r in _rows(result)}
    assert disp[p("H", "verhost-a")] == "decided"
    assert disp[p("H", "verhost-c")] == "below_min_severity"
    assert disp[p("H", "verhost-d")] == "unaccounted"
    assert disp[p("H", "verhost-e")] == "unaccounted"
    assert disp[p("H", "verhost-f")] == "pending"
    assert disp[p("H", "verhost-g")] == "decided"
    b_rows = sorted(r["disposition"] for r in _rows(result) if r["host"] == p("H", "verhost-b"))
    assert b_rows == ["decision_without_raw", "unaccounted"]


def test_no_group_means_no_pending(tmp_path: Path) -> None:
    fake = FakeRedis({"alarm:raw": [_raw(0, _payload("VA-0101", "verhost-z", "z", 3))]}, {})
    result = _run(tmp_path, [], now_sec=H, streams=pa.ReadOnlyStreams(fake))
    assert [r["disposition"] for r in _rows(result)] == ["unaccounted"]


def test_dead_letter_and_dedup_by_alarm_id(tmp_path: Path) -> None:
    raws = [_raw(0, _payload("VA-0201", "verhost-dl", "x", 3)),
            _raw(5, _payload("VA-0202", "verhost-dd", "y", 3))]
    dead = [(f"{_ms(1)}-0".encode(), {b"data": b"not json", b"source_msg_id":
                                      raws[0][0], b"error": b"ValueError"})]
    fake = FakeRedis({"alarm:raw": raws, "alarm:dead": dead}, _group(100))
    rec = {"type": "recurrence", "fingerprint": "e" * 40, "count": 2, "first_seen_ts": 0,
           "alarm_id": "VA-0202",
           "ts": (BASE + timedelta(seconds=6)).astimezone(UTC).isoformat()}
    result = _run(tmp_path, [rec], now_sec=H, streams=pa.ReadOnlyStreams(fake))
    assert sorted(r["disposition"] for r in _rows(result)) == ["dead_letter", "dedup_fingerprint"]
    assert _summary(result)["S1"]["recurrence_without_raw"] == 0


# ── 5 파생·집계 · what-if ─────────────────────────────────────────────────────
def test_sequence_s3_s4_s5_s8(tmp_path: Path) -> None:
    recs = [
        # 같은 지문 4h 간격 3회
        *(_dec(h * 4 * H, "verhost-rep", "디스크", sev=3, stage="severity3") for h in range(3)),
        # 같은 호스트 5분 안 3건(서로 다른 알람 · 같은 심각도)
        *(_dec(13 * H + m * 150, "verhost-host", f"서비스 {m}") for m in range(3)),
        # 10분 안 7호스트(정확히 600초 간격 끝 포함) + 601초 뒤 8번째
        *(_dec(15 * H + i * 100, f"verhost-fan{i}", "링크 다운") for i in range(7)),
        _dec(15 * H + 601, "verhost-fan7", "링크 다운"),
        # 재기동 폭주 — 같은 분 5건(임계 = 5)
        *(_dec(18 * H + i, f"verhost-burst{i}", f"폭주 {i}") for i in range(5)),
    ]
    result = _run(tmp_path, recs, now_sec=20 * H)
    s = _summary(result)
    assert s["S3"]["fp_page_count_dist"]["3"] == 1
    assert s["S3"]["repage_interval_dist"] == {"4~24시간": 2}
    assert s["S3"]["within_dedup_window_repage"] == 0
    assert s["S4"]["hosts_multi_page_30m"] == 1 and s["S4"]["pages_on_those_hosts"] == 3
    assert s["S4"]["fanout_clusters_10m"] == 1
    assert s["S4"]["fanout_cluster_size_dist"] == {"6-10": 1}
    assert s["S5"]["bursts"] == [
        {"minute": (BASE + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M"), "pages": 5,
         "gap_before_s": float(18 * H - (15 * H + 601))}
    ]
    rows = _rows(result)
    fan = [r for r in rows if r["alarm_name_norm"] == "링크 다운"]
    assert [r["whatif"]["R3"] for r in fan] == ["keep"] + ["demote"] * 6 + ["keep"]
    assert fan[-1]["fanout_hosts_10m"] == 7
    host = [r for r in rows if (r["alarm_name_norm"] or "").startswith("서비스")]
    assert [r["whatif"]["R2"] for r in host] == ["keep", "demote", "demote"]
    rep = [r for r in rows if r["alarm_name_norm"] == "디스크"]
    assert [r["whatif"]["R1"] for r in rep] == ["keep", "demote", "demote"]
    rules = s["S8"]["rules"]
    assert s["S8"]["pages_before"] == 3 + 3 + 8 + 5
    assert rules["R1"]["demoted"] == 2 and rules["R2"]["demoted"] == 2
    assert rules["R3"]["demoted"] == 6
    assert rules["cumulative"]["demoted"] == 10
    assert s["S8"]["orphans_total"] == 0


def test_within_dedup_window_repage_detects_restart_repage(tmp_path: Path) -> None:
    recs = [_dec(h * 4 * H, "verhost-dd", "디스크", sev=3, stage="severity3") for h in range(3)]
    result = _run(tmp_path, recs, now_sec=10 * H,
                  config=_config(sev3_repeat_interval_seconds=6 * H))
    assert _summary(result)["S3"]["within_dedup_window_repage"] == 2


def test_independent_orphan_check_from_rows(tmp_path: Path) -> None:
    """누적 R1~R3 강등 행마다 같은 키의 남은 PAGE가 그 앞에 있어야 한다(행에서 독립 재검증)."""
    recs = []
    for i in range(30):
        recs.append(_dec(i * 170, f"verhost-o{i % 4}", f"알람 {i % 3}", sev=2 + (i % 5 == 0)))
        if i % 7 == 6:
            recs.append(_clear(i * 170 + 30, f"verhost-o{i % 4}", f"알람 {i % 3}"))
    result = _run(tmp_path, recs, now_sec=3 * H)
    kept: list[dict[str, Any]] = []
    for r in _pages(result):
        cum = r["whatif"]["cumulative"]
        if cum == "keep":
            kept.append(r)
            continue
        rule = cum.split(":")[1]
        if rule == "R1":
            assert any(k["fp"] == r["fp"] for k in kept)
        elif rule == "R2":
            assert any(k["host"] == r["host"] for k in kept)
        elif rule == "R3":
            assert any(k["alarm_def"] == r["alarm_def"] and k["host"] != r["host"] for k in kept)
    assert _summary(result)["S8"]["orphans_total"] == 0


def test_r1_reminder_boundary_exactly_24h(tmp_path: Path) -> None:
    fp = "1" * 40
    recs = [
        _dec(0, "verhost-r1", "x", fp=fp),
        _dec(23 * H, "verhost-r1", "x", fp=fp),            # 강등 — 대표가 되지 않는다
        _dec(24 * H - 1, "verhost-r1", "x", fp=fp),        # 대표(0초)로부터 24h 미만 → 강등
        _dec(24 * H, "verhost-r1", "x", fp=fp),            # 정확히 24h → 리마인더 보존
        _dec(24 * H + 60, "verhost-r1", "x", fp=fp),       # 새 대표(24h)로부터 → 강등
    ]
    result = _run(tmp_path, recs, now_sec=25 * H, hours=48)
    assert [r["whatif"]["R1"] for r in _pages(result)] == [
        "keep", "demote", "demote", "keep", "demote"]


def test_r1_reopen_quiet_boundary(tmp_path: Path) -> None:
    fp1, fp2 = "2" * 40, "3" * 40
    recs = [
        _dec(0, "verhost-q1", "x", fp=fp1),
        _clear(600, "verhost-q1", "x", fp=fp1),
        _dec(600 + 1800, "verhost-q1", "x", fp=fp1),       # 해소 뒤 정확히 30분 → 강등
        _dec(10 * H, "verhost-q2", "y", fp=fp2),
        _clear(10 * H + 600, "verhost-q2", "y", fp=fp2),
        _dec(10 * H + 600 + 1801, "verhost-q2", "y", fp=fp2),  # 30분+1초 → 새 사건
    ]
    result = _run(tmp_path, recs, now_sec=12 * H)
    assert [r["whatif"]["R1"] for r in _pages(result)] == ["keep", "demote", "keep", "keep"]


def test_defect_r1_reopened_incident_stays_open(tmp_path: Path) -> None:
    """해소 뒤 유예 안 재발(강등)로 사건이 다시 열렸으면, 그 뒤 반복도 같은 사건이다
    (§4.2 R1 「열린 사건」 · W1 `PageRegistry._count_repeat`가 resolved_ts를 지운다).
    W0은 마지막 해소 시각만 보고 유지한다."""
    fp = "4" * 40
    recs = [
        _dec(0, "verhost-ro", "x", fp=fp),
        _clear(600, "verhost-ro", "x", fp=fp),
        _dec(1200, "verhost-ro", "x", fp=fp),               # 유예 안 재발 → 강등(재개)
        _dec(1200 + H, "verhost-ro", "x", fp=fp),           # 재개된 사건의 반복 — 해소 없음
    ]
    result = _run(tmp_path, recs, now_sec=3 * H)
    assert [r["whatif"]["R1"] for r in _pages(result)] == ["keep", "demote", "demote"]


def test_r2_window_severity_and_cleared_rep(tmp_path: Path) -> None:
    recs = [
        _dec(0, "verhost-r2", "a", sev=2),
        _dec(1800, "verhost-r2", "b", sev=2),        # 정확히 30분 → 강등
        _dec(1801, "verhost-r2", "c", sev=3),        # 심각도 상승 → 보존(새 대표)
        _dec(1900, "verhost-r2", "d", sev=3),        # 실효 ≤ 대표(3) → 강등
        _clear(2000, "verhost-r2", "c"),             # 대표 지문 해소
        _dec(2100, "verhost-r2", "e", sev=2),        # 대표 해소 → R2 없음
        _dec(5000, "verhost-r2x", "f", sev=2),
        _dec(5000 + 1801, "verhost-r2x", "g", sev=2),  # 30분+1초 → 창 밖
    ]
    result = _run(tmp_path, recs, now_sec=3 * H)
    assert [r["whatif"]["R2"] for r in _pages(result)] == [
        "keep", "demote", "keep", "demote", "keep", "keep", "keep"]


def test_r3_severity_rise_and_demoted_not_representative(tmp_path: Path) -> None:
    recs = [
        _dec(0, "verhost-f1", "링크", sev=2),       # 대표
        _dec(300, "verhost-f2", "링크", sev=2),     # 대표 외 → 강등(대표 아님)
        _dec(400, "verhost-f3", "링크", sev=3),     # 심각도 상승 → 보존(새 대표)
        _dec(700, "verhost-f4", "링크", sev=2),     # f1은 창 밖 · f3 대표(300초 전) → 강등
        _dec(1050, "verhost-f5", "링크", sev=2),    # f3 650초 · f2·f4는 강등이라 대표 아님 → 보존
    ]
    result = _run(tmp_path, recs, now_sec=H)
    assert [r["whatif"]["R3"] for r in _pages(result)] == [
        "keep", "demote", "keep", "demote", "keep"]


def test_cumulative_order_generation_first_and_not_representative(tmp_path: Path) -> None:
    fp = "5" * 40
    recs = [
        # R4 강등 건은 대표가 되지 않는다 → 뒤 같은 지문 매트릭스 PAGE는 누적에서 새 사건(유지)
        _dec(0, "verhost-c4", "수집", fp=fp, stage="collection_failed", sev=2),
        _dec(60, "verhost-c4", "수집", fp=fp, stage="matrix", sev=2,
             evidence={"promote": ["x"]}),
        # R5(LLM 상향)가 R1보다 먼저 — 누적 라벨은 demote:R5
        _dec(H, "verhost-c5", "앱 로그", fp="6" * 40, stage="severity3", sev=3),
        _dec(H + 60, "verhost-c5", "앱 로그", fp="6" * 40, stage="severity3", sev=2, eff=3,
             log="user session closed normally"),
        # 앱 영향 fatal>0 은 R4도 보존
        _dec(2 * H, "verhost-c6", "수집2", stage="collection_failed", sev=2,
             evidence={"app_impact_fatal_events": 1}),
    ]
    result = _run(tmp_path, recs, now_sec=3 * H)
    w = [r["whatif"] for r in _pages(result)]
    assert w[0]["R4"] == "demote" and w[0]["cumulative"] == "demote:R4"
    assert w[1]["R1"] == "demote"           # 단독 R1은 원 PAGE를 대표로 본다
    assert w[1]["cumulative"] == "keep"     # 누적에서는 R4 강등 건이 대표가 아니다
    assert w[3]["R5"] == "demote" and w[3]["R1"] == "demote"
    assert w[3]["cumulative"] == "demote:R5"
    assert w[4]["R4"] == "keep" and w[4]["cumulative"] == "keep"


def test_r6_new_incident_first_page_demoted(tmp_path: Path) -> None:
    """R6은 보존 1(새 사건 첫 PAGE)을 적용하지 않는다 — 구현자 밝힌 이탈의 실측(기록용 · 통과)."""
    fp = "7" * 40
    recs = []
    for d in range(8):
        recs.append(_dec(d * 86400, "verhost-ch", "만성", fp=fp))
        recs.append(_clear(d * 86400 + 600, "verhost-ch", "만성", fp=fp))
    result = _run(tmp_path, recs, now_sec=7 * 86400 + H, hours=24)
    last = _pages(result)[-1]
    assert last["fp_page_days_7d"] == 7
    assert last["whatif"]["R1"] == "keep"        # 해소 뒤 하루 → 새 사건
    assert last["whatif"]["R6"] == "demote"      # 그래도 R6은 강등


def test_window_boundaries_inclusive(tmp_path: Path) -> None:
    recs = [
        _dec(0, "verhost-w0", "x"),                  # 긴 창 시작 1초 전 → 행 없음(이력만)
        _dec(1, "verhost-w1", "x"),                  # 긴 창 시작 정확히 → 행
        _dec(1 + 24 * H, "verhost-w2", "x"),         # 짧은 창 시작 정확히 → in_hours_window
        _dec(24 * H, "verhost-w3", "x"),             # 짧은 창 시작 1초 전
    ]
    # now = 1 + 48h → 긴 창(2일) 시작 = 1초 · 짧은 창(24h) 시작 = 1 + 24h
    result = _run(tmp_path, recs, now_sec=1 + 48 * H, hours=24, days=2)
    p = Pseudonymizer(KEY)
    rows = {r["host"]: r["in_hours_window"] for r in _rows(result)}
    assert p("H", "verhost-w0") not in rows
    assert rows[p("H", "verhost-w1")] is False
    assert rows[p("H", "verhost-w3")] is False
    assert rows[p("H", "verhost-w2")] is True


# ── S10 데이터 품질 ───────────────────────────────────────────────────────────
def test_s10_damaged_lines_old_records_and_typed_records(tmp_path: Path) -> None:
    ts = (BASE + timedelta(seconds=10)).astimezone(UTC).isoformat()
    recs: list[Any] = [
        "{not json",
        "[1, 2]",
        json.dumps({"alarm_id": "x", "tier": "page"}),                   # ts 없음
        _dec(0, "verhost-old", "옛 알람", stage=None),                    # stage 없는 옛 PAGE
        {"type": "investigation", "ts": ts, "fingerprint": "z"},
        {"type": "late_promotion", "ts": ts, "fingerprint": "z"},
        {"type": "resolution", "ts": ts, "fingerprint": "z", "duration_seconds": 5},
    ]
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code == 0
    s = _summary(result)
    assert s["S10"]["bad_audit_lines"] == 3
    assert s["S10"]["decisions_without_stage"] == 1
    assert s["S10"]["other_record_types"] == {"investigation": 1}
    assert s["S2"]["late_promotions"] == 1
    assert s["S2"]["tier_by_stage"]["unknown"]["page"] == 1
    assert s["S8"]["pages_before"] == 1
    assert len(_rows(result)) == 1


# ── CLI 모드 · 읽기 전용 ──────────────────────────────────────────────────────
def test_cli_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "AppConfig", lambda: _config())
    path = _write(tmp_path, [_dec(0, "verhost-cli", "x")])
    out = tmp_path / "out"
    code = pa.main(["--dry-run", "--no-stream", "--decisions", str(path), "--out", str(out)])
    assert code == 0 and not out.exists()
    info = json.loads(capsys.readouterr().out)
    assert info["stream"] is None and info["decisions_exists"] is True


def test_cli_no_stream_summary_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "AppConfig", lambda: _config())
    monkeypatch.setattr(pa, "open_streams", lambda *_: pytest.fail("Redis를 열면 안 된다"))
    path = _write(tmp_path, [_dec(-60, "verhost-cli", "x")])
    out = tmp_path / "out"
    code = pa.main(["--no-stream", "--summary-only", "--decisions", str(path), "--out", str(out),
                    "--hours", "1000000"])
    assert code == 0
    (run_dir,) = list(out.iterdir())
    names = sorted(p.name for p in run_dir.iterdir())
    assert names == ["leak_check.json", "policy_snapshot.json", "summary.json", "summary.md"]
    leak = json.loads((run_dir / "leak_check.json").read_text("utf-8"))
    assert "alarms.jsonl" not in leak["files_checked"]


def test_stream_run_uses_read_commands_only(tmp_path: Path) -> None:
    raws = [_raw(i, _payload(f"VA-1{i:03d}", f"verhost-s{i}", "x", 3)) for i in range(3)]
    fake = FakeRedis({"alarm:raw": raws}, _group(1))
    _run(tmp_path, [], now_sec=H, streams=pa.ReadOnlyStreams(fake))
    pa.dry_run(config=_config(), decisions_path=tmp_path / "none.jsonl", hours=1, days=None,
               streams=pa.ReadOnlyStreams(fake), now=BASE + timedelta(hours=1))
    assert fake.calls and set(fake.calls) <= READ_COMMANDS


# ── 교정 라운드 재검증 (2026-10-14) ───────────────────────────────────────────
def _all_output(result: pa.AuditResult) -> str:
    assert result.out_dir is not None
    return "\n".join(p.read_text("utf-8") for p in result.out_dir.iterdir()).casefold()


@pytest.mark.parametrize(
    ("evidence", "secret"),
    [
        ({"apm_policy": {"event_type_norm": "VERHOST-ETA"}}, "verhost-eta"),
        ({"cross_source": {"rule_id": "192.0.2.77"}}, "192.0.2.77"),
        ({"promote": [{"h": "verhost-eta"}, "verhost-eta"]}, "verhost-eta"),
        ({"mode": "verhost-eta"}, "verhost-eta"),
        ({"heal_seconds": "verhost-eta"}, "verhost-eta"),
    ],
)
def test_allowed_key_values_never_reach_outputs(
    tmp_path: Path, evidence: dict[str, Any], secret: str
) -> None:
    """허용 키 값(코드형 `_CODE` 포함)에 실린 원문 — 값 거름(`<other>`/None) 또는
    누출 검사(종료 2)로 닫힌다."""
    recs = [_dec(0, "verhost-eta", "x", evidence=evidence)]
    if secret.startswith("192."):
        recs.append(_dec(30, "verhost-eta", "y", ipAddress=secret))
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code in (0, 2)
    assert secret not in _all_output(result)
    if result.exit_code == 2:
        assert [p.name for p in result.out_dir.iterdir()] == ["leak_check.json"]


def test_unknown_evidence_key_and_record_type_names_are_hidden(tmp_path: Path) -> None:
    ts = (BASE + timedelta(seconds=10)).astimezone(UTC).isoformat()
    recs: list[Any] = [
        _dec(0, "verhost-theta", "x", evidence={"verhost-kappa": 1, "inhibitor": "verhost-kappa"}),
        {"type": "verhost-kappa-type", "ts": ts},
    ]
    result = _run(tmp_path, recs, now_sec=H)
    assert result.exit_code == 0, result.leak
    blob = _all_output(result)
    assert "verhost-kappa" not in blob
    s = _summary(result)
    assert "<unknown_key>" in json.dumps(s, ensure_ascii=False)


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("링크 다운 192.0.2.15.", "192.0.2.15"),
        ("링크 다운 192.0.2.15.5", "192.0.2.15"),
        ("링크 다운 ::ffff:192.0.2.15", "192.0.2.15"),
        ("링크 다운 0192.000.002.015", "192.000.002.015"),
        ("링크 다운 [198.51.100.7]:22", "198.51.100.7"),
        ("링크 다운 １９８．５１．１００．７", "198.51.100.7"),
        ("링크 다운 198。51。100。7", "198.51.100.7"),
        ("링크 다운 198-51-100-7", "198-51-100-7"),
    ],
)
def test_ipv4_boundary_variants_end_to_end(tmp_path: Path, raw: str, secret: str) -> None:
    """직전 HIGH(IPv4 경계) 회귀 — 실행 단위로 출력 어디에도 주소가 없고 정상 종료."""
    result = _run(tmp_path, [_dec(0, "verhost-iota", raw)], now_sec=H)
    assert result.exit_code == 0, result.leak
    blob = _all_output(result)
    assert secret not in blob
    assert "198.51.100.7" not in blob and "192.0.2.15" not in blob


def test_defect_zero_width_split_hostname_in_alarm_name(tmp_path: Path) -> None:
    recs = [_dec(0, "verhost-lambda", "다운 verhost​-lambda 응답 없음")]
    result = _run(tmp_path, recs, now_sec=H)
    blob = _all_output(result).replace("​", "")
    assert "verhost-lambda" not in blob


def test_defect_underscore_separated_ipv4_in_alarm_name(tmp_path: Path) -> None:
    result = _run(tmp_path, [_dec(0, "verhost-mu", "링크 198_51_100_7 다운")], now_sec=H)
    assert "51_<n>_7" not in _all_output(result)


def test_cli_error_hides_message_with_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "AppConfig", lambda: _config())

    def _boom(**_: Any) -> None:
        raise ValueError("verhost-nu 192.0.2.99")

    monkeypatch.setattr(pa, "run_audit", _boom)
    code = pa.main(["--no-stream", "--decisions", str(tmp_path / "d.jsonl"),
                    "--out", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == 1
    assert "ValueError" in err and "verhost-nu" not in err and "192.0.2.99" not in err


def test_leak_failure_writes_only_private_leak_file(tmp_path: Path) -> None:
    class _Raw(pa.Sanitizer):
        def row(self, ctx: Any) -> dict[str, Any]:
            out = super().row(ctx)
            out["raw_host"] = ctx.server_name
            return out

    result = _run(tmp_path, [_dec(0, "verhost-xi", "x")], now_sec=H, sanitizer_cls=_Raw)
    assert result.exit_code == 2
    assert [p.name for p in result.out_dir.iterdir()] == ["leak_check.json"]
    assert (result.out_dir.stat().st_mode & 0o777) == 0o700
    assert ((result.out_dir / "leak_check.json").stat().st_mode & 0o777) == 0o600
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".tmp-")]
    assert "verhost-xi" not in _all_output(result)


# ── what-if 방식 · 순서 ───────────────────────────────────────────────────────
def test_single_rule_masks_only_first_hit_rule(tmp_path: Path) -> None:
    """단독 수치 = 끈 규칙이 첫 적중이면 그 규칙 재료만 비우고 다시 판정.

    A(지문 a) 0초 sev2 · B(지문 b · 같은 호스트) 60초 sev3 · A 반복 120초 sev2 · A 반복 180초 sev3.
    """
    fa, fb = "a" * 40, "b" * 40
    recs = [
        _dec(0, "verhost-wm", "A", fp=fa, sev=2),
        _dec(60, "verhost-wm", "B", fp=fb, sev=3),
        _dec(120, "verhost-wm", "A", fp=fa, sev=2),
        _dec(180, "verhost-wm", "A", fp=fa, sev=3),
    ]
    result = _run(tmp_path, recs, now_sec=H)
    w = [r["whatif"] for r in _pages(result)]
    # B: R2 대표 A(sev2) < B(sev3) → 심각도 상승 보존
    assert w[1]["R2"] == "keep" and w[1]["cumulative"] == "keep"
    # A 반복(sev2): R1 첫 적중 · R2 단독은 R1 재료를 비워 B(sev3)로 강등 · R3 단독은 keep
    assert (w[2]["R1"], w[2]["R2"], w[2]["R3"]) == ("demote", "demote", "keep")
    assert w[2]["cumulative"] == "demote:R1"
    # A 반복(sev3): R1 적중이지만 심각도 상승 보존 → 누적 keep. R2 단독은 R1 재료를 비우므로
    # B(sev3) 대비 실효 3 ≤ 3 → 강등(단독은 「R1이 없는 세계」 수치 — 누적보다 클 수 있다).
    assert w[3]["R1"] == "keep" and w[3]["cumulative"] == "keep"
    assert w[3]["R2"] == "demote"


def test_cumulative_r6_applies_after_domain_rules(tmp_path: Path) -> None:
    """누적 순서 R4·R5 → R1~R3 → R6 — 대표가 남아 있는 열린 사건 반복은 R6이 아니라 R1로 라벨된다.

    6일째 PAGE(22시간 뒤 · 해소 없음)는 7일 이력이 모자라 R6 판정 불가 → 대표로 남는다.
    7일째 PAGE는 그 대표의 2시간 뒤 반복(R1) 이면서 7일 중 7일 PAGE(R6).
    """
    fp = "c" * 40
    recs = []
    for d in range(6):
        recs.append(_dec(d * 86400, "verhost-or", "만성", fp=fp))
        recs.append(_clear(d * 86400 + 600, "verhost-or", "만성", fp=fp))
    recs.append(_dec(6 * 86400 + 22 * H, "verhost-or", "만성", fp=fp))
    recs.append(_dec(7 * 86400 + 60, "verhost-or", "만성", fp=fp))
    result = _run(tmp_path, recs, now_sec=7 * 86400 + H, hours=24)
    rep, last = (r["whatif"] for r in _pages(result)[-2:])
    assert rep["R6"] == "undecidable" and rep["cumulative"] == "keep"
    assert last["R6"] == "demote" and last["R1"] == "demote"
    assert last["cumulative"] == "demote:R1"


def test_cumulative_r6_demoted_page_is_not_a_representative(tmp_path: Path) -> None:
    """R6으로 강등된 PAGE는 대표가 되지 않는다 — 그 뒤 같은 지문 반복은 누적에서 R1이 아니다."""
    fp = "d" * 40
    recs = []
    for d in range(8):
        recs.append(_dec(d * 86400, "verhost-or2", "만성", fp=fp))
        recs.append(_clear(d * 86400 + 600, "verhost-or2", "만성", fp=fp))
    recs.append(_dec(7 * 86400 + 900, "verhost-or2", "만성", fp=fp))
    result = _run(tmp_path, recs, now_sec=7 * 86400 + 2 * H, hours=24)
    last = _pages(result)[-1]["whatif"]
    assert last["R1"] == "demote"                 # 단독 R1: 원 PAGE(7일째 첫 PAGE)가 대표
    assert last["cumulative"] == "demote:R6"      # 누적: 그 PAGE가 R6 강등이라 대표 없음
    assert _summary(result)["S8"]["orphans_total"] == 0


def test_w0_cumulative_review_matches_w1_registry_differential() -> None:
    """R1~R3 묶음 시뮬레이션 == W1 `PageRegistry`+`review_page`(enforce)를 워커 순서로 돈 결과."""
    import random

    from noise_gate.domain.notification_policy import NotificationDecision
    from noise_gate.domain.page_review import (
        PageRegistry,
        fanout_key_of,
        host_key_of,
        review_page,
    )

    cfg = SimpleNamespace(
        page_review_mode="enforce", page_review_reminder_hours=24,
        page_review_reopen_quiet_minutes=30, page_review_host_window_minutes=30,
        page_review_fanout_window_minutes=10, page_review_fanout_page_max=1,
        page_review_always_page_globs=[])
    base = datetime(2026, 10, 13, tzinfo=UTC)
    pages = disagree = 0
    for trial in range(120):
        rnd = random.Random(trial)
        t, decs = 0, []
        for i in range(rnd.randint(5, 25)):
            t += rnd.choice([30, 60, 120, 300, 600, 900, 1500, 2400, 4000])
            host, name = rnd.choice(["vh1", "vh2", "vh3"]), rnd.choice(["a", "b"])
            clear, sev = rnd.random() < 0.2, rnd.choice([2, 2, 3])
            s = 0 if clear else sev
            rec = {"ts": (base + timedelta(seconds=t)).isoformat(), "alarm_id": f"A{i}",
                   "tier": "suppress" if clear else "page",
                   "fingerprint": hashlib.sha1(f"{host}|{name}".encode()).hexdigest(),
                   "signals": {"severity": s, "effective_severity": s, "importance": "보통"},
                   "stage": "resolved" if clear else "matrix", "alarm_name": name,
                   "server_name": host, "db_id": "x"}
            decs.append(pa.Decision(i, base + timedelta(seconds=t), rec))
        clears = pa.build_resolutions(decs, [])
        _, events = pa.derive_features(decs, clears, window_start=base, coverage_start=base,
                                       repeat_interval_s=14400, sev3_repeat_interval_s=14400)
        w0 = pa.simulate(events, clears, ("R1", "R2", "R3"))
        assert w0.orphans == 0
        reg, w1 = PageRegistry(), {}
        for d in decs:
            now = d.ts.timestamp()
            hk = host_key_of("x", d.rec["server_name"])
            fk = fanout_key_of("x", d.rec["alarm_name"])
            nd = NotificationDecision(tier=d.rec["tier"], reason="", priority=1,
                                      signals=d.rec["signals"], fingerprint=d.fingerprint,
                                      stage=d.rec["stage"], evidence={})
            if nd.tier == "page":
                nd = review_page(nd, reg.signals_for(d.fingerprint, hk, fk, now), cfg)
                if nd.tier != "page":
                    w1[d.idx] = nd.evidence["page_review"]["rule"]
            reg.observe(nd, fingerprint=d.fingerprint, host_key=hk, fanout_key=fk,
                        alarm_id=d.alarm_id, now=now)
        for ev in events:
            pages += 1
            disagree += w0.demoted_by.get(ev.idx) != w1.get(ev.idx)
    assert pages > 1000
    assert disagree == 0
