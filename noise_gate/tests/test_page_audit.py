"""분류 정책 분석 로그 `page_audit` 테스트 (plans/162 W0 §10.9).

실 Redis·LLM·DB를 쓰지 않는다 — 가짜 Redis(호출 기록)와 tmp 경로만 쓴다. 설정은 `AppConfig()`를
만들지 않고 그룹 객체를 검증 대상 필드를 명시해 주입한다(`.env` 누수 차단).
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig, NoiseGateConfig

from noise_gate.scripts import page_audit as pa
from noise_gate.scripts.page_audit_sanitize import (
    IDENT_ALARM_ID,
    IDENT_HOST,
    IDENT_RESOURCE,
    OTHER,
    UNKNOWN_KEY,
    IdentifierSet,
    Pseudonymizer,
    Sanitizer,
    leak_check,
    normalize_text,
    policy_snapshot,
    record_type_label,
    sanitize_evidence,
    sanitize_recurrence,
    sanitize_signals,
    snapshot_group,
    vocabulary_words,
)

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "page_audit"
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 13, 23, 0, 0, tzinfo=KST)
OUTPUT_FILES = ("summary.json", "summary.md", "policy_snapshot.json", "leak_check.json",
                "alarms.jsonl")
READ_COMMANDS = {"xrange", "xinfo_groups", "xpending"}

# 픽스처 원문 식별자·로그 — 출력 어디에도 없어야 한다.
RAW_SECRETS = (
    "fake-web-01", "fake-was-02", "fake-db-03", "fake-batch-04", "fake-app-05", "fake-app-06",
    "fake-api-07", "example.test", "192.0.2.11", "198.51.100.22", "192.0.2.33", "192.0.2.44",
    "198.51.100.55", "198.51.100.66", "198.51.100.77", "fake-res-disk-01", "fake-res-cpu-02",
    "fake-res-mem-03", "fake-res-log-04", "fake-res-proc-05", "fake-res-proc-06",
    "fake-res-api-07", "fake-res-net-01", "fake-root-res-01", "FAKE-ALM-10", "ops@example.test",
    "사용률 97%", "batch job failed", "eth0 err", "디스크가", "FakeRoot", "보수적 PAGE",
)


def _config(**noise: Any) -> SimpleNamespace:
    """검증 대상 필드를 명시한 설정 묶음."""
    ng = NoiseGateConfig(
        enable_noise_gate=True,
        repeat_interval_seconds=14400,
        sev3_repeat_interval_seconds=14400,
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


def _parse_id(stream_id: str, *, upper: bool = False) -> tuple[int, int]:
    """가짜 XRANGE 경계 해석 — `ms`만 오면 시작은 seq 0, 끝은 seq 최대."""
    if stream_id in ("-", "+"):
        return (0, 0) if stream_id == "-" else (2**63, 2**63)
    ms, sep, seq = stream_id.partition("-")
    if not sep:
        return int(ms), (2**63 if upper else 0)
    return int(ms), int(seq)


class FakeRedis:
    """호출을 모두 기록하는 가짜 Redis — 읽기 3종만 구현하고 그 밖의 호출은 기록만 한다."""

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


def _fake_redis() -> FakeRedis:
    """정적 픽스처(원문·dead·그룹)로 가짜 Redis를 만든다."""
    raw: list[tuple[bytes, dict[bytes, bytes]]] = []
    for line in (FIXTURE / "raw_stream.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        data = rec.get("data_text") or json.dumps(rec["data"], ensure_ascii=False)
        raw.append((rec["id"].encode(), {b"data": data.encode("utf-8")}))
    dead: list[tuple[bytes, dict[bytes, bytes]]] = []
    for line in (FIXTURE / "dead_stream.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        dead.append((rec["id"].encode(),
                     {k.encode(): str(v).encode("utf-8") for k, v in rec["fields"].items()}))
    groups_json = json.loads((FIXTURE / "stream_groups.json").read_text(encoding="utf-8"))
    groups = {k: [{gk.encode(): (gv.encode() if isinstance(gv, str) else gv)
                   for gk, gv in g.items()} for g in v] for k, v in groups_json.items()}
    return FakeRedis({"alarm:raw": raw, "alarm:dead": dead}, groups)


def _run(tmp_path: Path, *, streams: Any = None, decisions: Path | None = None,
         **kwargs: Any) -> pa.AuditResult:
    """픽스처로 1회 실행."""
    kwargs.setdefault("config", _config())
    return pa.run_audit(
        decisions_path=decisions or FIXTURE / "decisions.jsonl",
        out_root=tmp_path / "out",
        streams=streams,
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def _rows(out_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (out_dir / "alarms.jsonl").read_text("utf-8").splitlines()]


def _all_text(out_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(out_dir.iterdir()))


# ── 1 누출 0 ──────────────────────────────────────────────────────────────────
def test_outputs_contain_no_raw_identifiers(tmp_path: Path) -> None:
    fake = _fake_redis()
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(fake))
    assert result.exit_code == 0
    assert result.out_dir is not None
    assert sorted(p.name for p in result.out_dir.iterdir()) == sorted(OUTPUT_FILES)
    text = _all_text(result.out_dir)
    for secret in RAW_SECRETS:
        assert secret.lower() not in text.lower(), secret
    for pattern in leak_check({"x": text}, IdentifierSet())["hits"]:
        pytest.fail(f"주소형 문자열 잔존: {pattern}")
    leak = json.loads((result.out_dir / "leak_check.json").read_text("utf-8"))
    assert leak["passed"] is True and leak["identifiers_checked"] > 0


def test_alarm_name_with_ip_and_server_is_normalized(tmp_path: Path) -> None:
    key = b"k" * 32
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis()), key=key)
    assert result.out_dir is not None
    pseudo = Pseudonymizer(key)
    rows = [r for r in _rows(result.out_dir)
            if r["alarm_def"] == pseudo("N", "fake-web-01(192.0.2.11) 파일시스템 사용률 임계 초과")]
    assert rows and rows[0]["alarm_name_norm"] == "<HOST>(<IP>) 파일시스템 사용률 임계 초과"
    assert rows[0]["conditions_norm"] == "사용률 >= 90 (<RES>)"
    # 다른 알람의 서버명이 이름에 섞여도 지운다(이번 실행에서 본 식별자 전수).
    api = [r for r in _rows(result.out_dir) if r["disposition"] == "decision_without_raw"]
    assert api[0]["alarm_name_norm"] == "API 응답 지연 <HOST> <IP>"


def test_normalize_text_placeholders() -> None:
    text = ("svc-a1b2c fe80::1:2:3 aa:bb:cc:dd:ee:ff a@b.example http://x.example/y "
            "192.0.2.9 값 12345 임계 90")
    assert normalize_text(text, max_len=200) == "<ID> <IP> <MAC> <EMAIL> <URL> <IP> 값 <N> 임계 90"
    assert len(normalize_text("가" * 100)) == 60


# ── 2 누출 검사가 닫힌다 ────────────────────────────────────────────────────────
class _LeakySanitizer(Sanitizer):
    """허용 목록을 우회해 원문 서버명을 싣는 가짜 변환기."""

    def row(self, ctx: Any) -> dict[str, Any]:
        out = super().row(ctx)
        out["leak"] = ctx.server_name
        return out


def test_leak_check_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis()),
                  sanitizer_cls=_LeakySanitizer)
    assert result.exit_code == 2
    assert result.out_dir is not None
    assert [p.name for p in result.out_dir.iterdir()] == ["leak_check.json"]
    raw = (result.out_dir / "leak_check.json").read_text("utf-8")
    leak = json.loads(raw)
    assert leak["passed"] is False
    assert {h["file"] for h in leak["hits"]} == {"alarms.jsonl"}
    assert all(set(h) == {"file", "kind", "count"} for h in leak["hits"])
    for secret in RAW_SECRETS:
        assert secret.lower() not in raw.lower()


def test_leak_check_reports_counts_only() -> None:
    idents = IdentifierSet()
    idents.add(IDENT_HOST, "fake-host-77")
    report = leak_check({"a.json": "x 192.0.2.1 FAKE-HOST-77 y"}, idents)
    kinds = {h["kind"]: h["count"] for h in report["hits"]}
    assert kinds == {"ipv4": 1, "identifier:host": 1}
    assert "192.0.2.1" not in json.dumps(report) and "fake-host-77" not in json.dumps(report)


def test_time_strings_are_not_ipv6() -> None:
    report = leak_check({"a": "2026-10-13T09:12:03+09:00 12:30"}, IdentifierSet())
    assert report["passed"] is True


# ── 3 가명 ────────────────────────────────────────────────────────────────────
def test_pseudonyms_stable_within_run_differ_across_runs(tmp_path: Path) -> None:
    def hosts_of(result: pa.AuditResult) -> list[str]:
        assert result.out_dir is not None
        rows = _rows(result.out_dir)
        return [r["host"] for r in rows if r["stage"] in ("severity3", "collection_failed")]

    first = hosts_of(_run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis())))
    second = hosts_of(_run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis())))
    assert len(first) == 2 and first[0] == first[1]  # 같은 서버(fake-web-01) = 같은 가명
    assert first[0].startswith("H-") and len(first[0]) == 2 + 16
    assert first[0] != second[0]


def test_run_key_is_written_nowhere(tmp_path: Path) -> None:
    key = bytes(range(32))
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis()), key=key)
    assert result.out_dir is not None
    for path in result.out_dir.iterdir():
        blob = path.read_bytes()
        assert key not in blob and key.hex().encode() not in blob
    assert key.hex() not in repr(Pseudonymizer(key))


# ── 4 처분 7종 ────────────────────────────────────────────────────────────────
def test_seven_dispositions(tmp_path: Path) -> None:
    key = b"d" * 32
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis()), key=key)
    assert result.out_dir is not None
    pseudo = Pseudonymizer(key)
    by_host: dict[str | None, list[str]] = {}
    for r in _rows(result.out_dir):
        by_host.setdefault(r["host"], []).append(r["disposition"])
    assert by_host[pseudo("H", "fake-web-01")] == ["decided", "decided", "decided"]
    assert by_host[pseudo("H", "fake-was-02")] == ["dedup_fingerprint"]
    assert by_host[pseudo("H", "fake-db-03")] == ["below_min_severity"]
    assert by_host[pseudo("H", "fake-batch-04")] == ["dead_letter"]
    assert by_host[pseudo("H", "fake-app-05")] == ["pending"]
    assert by_host[pseudo("H", "fake-app-06")] == ["unaccounted"]
    assert by_host[pseudo("H", "fake-api-07")] == ["decision_without_raw"]
    assert by_host[None] == ["unaccounted"]  # 깨진 원문
    summary = json.loads((result.out_dir / "summary.json").read_text("utf-8"))
    assert summary["S1"]["dispositions"] == {
        "decided": 3, "dedup_fingerprint": 1, "below_min_severity": 1, "dead_letter": 1,
        "pending": 1, "unaccounted": 2, "decision_without_raw": 1,
    }
    assert summary["S1"]["decision_without_raw"] == 1
    assert summary["S10"]["bad_audit_lines"] == 1
    assert summary["S10"]["dropped_evidence_keys"] == {"inhibitor": 1}


def test_no_stream_uses_audit_only(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.exit_code == 0 and result.out_dir is not None
    summary = json.loads((result.out_dir / "summary.json").read_text("utf-8"))
    assert summary["S0"]["stream_read"] is False
    assert summary["S1"]["dispositions"]["decided"] == 4
    assert summary["S1"]["dispositions"]["dedup_fingerprint"] == 1
    assert "stream" not in json.loads((result.out_dir / "policy_snapshot.json").read_text("utf-8"))


def test_summary_only_skips_alarm_rows(tmp_path: Path) -> None:
    result = _run(tmp_path, summary_only=True)
    assert result.out_dir is not None
    assert not (result.out_dir / "alarms.jsonl").exists()
    assert (result.out_dir / "summary.md").read_text("utf-8").startswith("# 분류 정책 분석 로그")


# ── 5 파생·집계 — 손으로 만든 시퀀스 ───────────────────────────────────────────
_SEQ_BASE = datetime(2026, 10, 13, 1, 0, 0, tzinfo=KST)


def _decision(sec: float, server: str, name: str, *, stage: str = "matrix", sev: int = 2,
              eff: int | None = None, fp: str | None = None, tier: str = "page",
              log: str = "") -> dict[str, Any]:
    eff = sev if eff is None else eff
    rec: dict[str, Any] = {
        "ts": (_SEQ_BASE + timedelta(seconds=sec)).astimezone(UTC).isoformat(),
        "alarm_id": f"SEQ-{server}-{sec}", "tier": tier, "reason": "x", "priority": 1,
        "fingerprint": fp or hashlib.sha1(f"{server}|{name}".encode()).hexdigest(),
        "signals": {"severity": sev, "ai_severity": None, "effective_severity": eff,
                    "importance": "보통"},
        "stage": stage, "alarm_name": name, "server_name": server, "db_id": "polestar_cm_gp",
    }
    if log:
        rec["condition_log"] = log
    return rec


def _sequence_file(tmp_path: Path) -> Path:
    """§10.9-5 시퀀스: 같은 지문 4h 간격 3회 · 같은 호스트 5분 안 3건 · 10분 안 7호스트 ·
    재기동 폭주(1시간 공백 뒤 같은 분 6건) · 수집 실패(R4) · LLM 상향(R5) · 시그니처 상향."""
    recs = [
        # A 같은 지문 4h 간격 3회(sev3 단락 · 원 sev3)
        *(_decision(h * 3600, "seq-host-a", "디스크 가득", stage="severity3", sev=3)
          for h in (0, 4, 8)),
        # B 같은 호스트 5분 안 3건(서로 다른 알람)
        *(_decision(1800 + m * 120, "seq-host-b", f"서비스 지연 유형{m}") for m in range(3)),
        # C 10분 안 7호스트(같은 알람명)
        *(_decision(7200 + i * 60, f"seq-fan-{i}", "링크 다운") for i in range(7)),
        # D 수집 실패 PAGE(R4) · LLM 상향 sev3(R5) · 시그니처 상향 sev3(R5 대상 아님)
        _decision(20000, "seq-host-d", "수집 실패 알람", stage="collection_failed"),
        _decision(20100, "seq-host-e", "앱 로그 경고", stage="severity3", sev=2, eff=3,
                  log="user session closed normally"),
        _decision(20200, "seq-host-f", "커널 로그", stage="severity3", sev=2, eff=3,
                  log="kernel: Out of memory: Killed process"),
        # E 재기동 폭주 — 12:06:00부터 같은 분에 6건(직전 판정 A 09:00 뒤 3.1시간 공백)
        *(_decision(39960 + i * 5, f"seq-burst-{i}", f"재기동 알람 {i}") for i in range(6)),
    ]
    path = tmp_path / "seq_decisions.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    return path


def test_sequence_aggregates(tmp_path: Path) -> None:
    result = _run(tmp_path, decisions=_sequence_file(tmp_path))
    assert result.exit_code == 0 and result.out_dir is not None
    s = json.loads((result.out_dir / "summary.json").read_text("utf-8"))

    # S3 — 같은 지문 3회 · 재PAGE 간격 4h 2건 · dedup 창(4h) 안 재PAGE는 없음(간격 = 창)
    assert s["S3"]["fp_page_count_dist"] == {"3": 1, "1": 19}
    assert s["S3"]["repage_interval_dist"] == {"4~24시간": 2}
    assert s["S3"]["within_dedup_window_repage"] == 0
    assert s["S3"]["top_fingerprints"][0]["pages"] == 3

    # S4 — 30분 안 PAGE ≥2 호스트 1대(seq-host-b · 3건) · 10분 안 7호스트 군집 1개
    assert s["S4"]["hosts_multi_page_30m"] == 1
    assert s["S4"]["pages_on_those_hosts"] == 3
    assert s["S4"]["fanout_clusters_10m"] == 1
    assert s["S4"]["fanout_cluster_size_dist"] == {"6-10": 1}
    assert s["S4"]["pages_in_fanout_clusters"] == 7

    # S5 — 폭주 1분(6건) · 직전 판정(A 세 번째 · 28800초) 뒤 공백 11160초
    assert s["S5"]["bursts"] == [
        {"minute": "2026-10-13T12:06", "pages": 6, "gap_before_s": 11160.0}
    ]

    # S2 — sev3 상향 출처
    assert s["S2"]["sev3_origin"] == {
        "original_sev3": 3, "llm_or_anomaly": 1, "signature:OOM 강제종료": 1,
    }

    # S8 — 단독: R1 2 · R2 2 · R3 6 · R4 1 · R5 1 · R6 0(7일 이력 부족 → 판정 불가)
    #      누적 12 · 고아 0
    rules = s["S8"]["rules"]
    assert s["S8"]["pages_before"] == 22
    assert {k: v["demoted"] for k, v in rules.items()} == {
        "R4": 1, "R5": 1, "R1": 2, "R2": 2, "R3": 6, "R6": 0, "cumulative": 12,
    }
    assert rules["R6"]["undecidable"] == 22
    assert rules["cumulative"]["pages_after"] == 10
    assert rules["R3"]["demoted_origin_stage"] == {"matrix": 6}
    assert s["S8"]["orphans_total"] == 0

    rows = _rows(result.out_dir)
    fan = [r for r in rows if r["alarm_name_norm"] == "링크 다운"]
    assert [r["fanout_hosts_10m"] for r in fan] == [1, 2, 3, 4, 5, 6, 7]
    assert [r["whatif"]["R3"] for r in fan] == ["keep"] + ["demote"] * 6
    assert [r["whatif"]["cumulative"] for r in fan] == ["keep"] + ["demote:R3"] * 6
    rep_a = [r for r in rows if r["alarm_name_norm"] == "디스크 가득"]
    assert [r["fp_page_seq"] for r in rep_a] == [1, 2, 3]
    assert [r["whatif"]["R1"] for r in rep_a] == ["keep", "demote", "demote"]


def test_r1_reopens_after_clear_and_reminder(tmp_path: Path) -> None:
    fp = "f" * 40
    recs = [
        _decision(0, "seq-r1", "디스크 가득", fp=fp),
        _decision(600, "seq-r1", "디스크 가득", fp=fp, tier="suppress", sev=0, stage="resolved"),
        _decision(900, "seq-r1", "디스크 가득", fp=fp),             # 해소 뒤 5분 → 재발(강등·재개)
        _decision(900 + 3600, "seq-r1", "디스크 가득", fp=fp),      # 재개된 사건의 반복(W1) → 강등
        _decision(900 + 3600 + 25 * 3600, "seq-r1", "디스크 가득", fp=fp),  # 24h 리마인더
        _decision(900 + 3600 + 25 * 3600 + 60, "seq-r1", "디스크 가득", fp=fp, eff=3, sev=3,
                  stage="severity3"),                                      # 심각도 상승 보존
    ]
    path = tmp_path / "r1.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(days=2), days=3)
    assert result.out_dir is not None
    pages = [r for r in _rows(result.out_dir) if r["tier"] == "page"]
    assert [r["whatif"]["R1"] for r in pages] == ["keep", "demote", "demote", "keep", "keep"]
    assert [r["clear_after_s"] for r in pages][0] == 600.0


def test_r6_chronic_with_seven_day_history(tmp_path: Path) -> None:
    fp = "c" * 40
    recs = [_decision(d * 86400, "seq-chronic", "만성 알람", fp=fp) for d in range(10)]
    path = tmp_path / "r6.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
    now = _SEQ_BASE + timedelta(days=9, hours=1)
    result = _run(tmp_path, decisions=path, now=now, hours=24, days=2)
    assert result.out_dir is not None
    rows = _rows(result.out_dir)
    assert [r["fp_page_days_7d"] for r in rows] == [7, 7]
    # 7일 이력이 있는 행만 판정 — 첫 PAGE(파일 시작) 뒤 6일째부터
    assert [r["whatif"]["R6"] for r in rows] == ["demote", "demote"]
    s = json.loads((result.out_dir / "summary.json").read_text("utf-8"))
    assert s["S8"]["rules"]["R6"]["demoted"] == 1  # 짧은 창(24h)에는 1건
    assert s["S8"]["daily_avg_long_before"] == 1.0


def test_app_impact_preserves_page(tmp_path: Path) -> None:
    recs = [
        _decision(0, "seq-app", "응답 지연 A"),
        _decision(60, "seq-app", "응답 지연 B"),
    ]
    recs[1]["stage_evidence"] = {"app_impact_fatal_events": 2}
    path = tmp_path / "app.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=1))
    assert result.out_dir is not None
    assert [r["whatif"]["R2"] for r in _rows(result.out_dir)] == ["keep", "keep"]


# ── 6 정책 스냅샷 ─────────────────────────────────────────────────────────────
def test_policy_snapshot_allowlist() -> None:
    ng = NoiseGateConfig(
        apm_mcp_url="http://apm.example.test/mcp",
        investigation_service_token="tok-secret-123",
        l3_ssh_user="fakeuser",
        decision_store_path="/fake/audit/path.jsonl",
        importance_value_map_csv="1=낮음,2=보통,3=높음",
        enrichment_profile_map_csv="a=b,c=d",
        cross_source_mode="shadow",
        enrichment_min_tier="PAGE",
        enable_noise_gate=True,
        l3_host_access_mode="allowlist_exec",
    )
    alarm = AlarmConfig(webhook_url="http://hook.example.test/x", min_severity=2)
    snap = policy_snapshot(SimpleNamespace(noise_gate=ng, alarm=alarm))
    dumped = json.dumps(snap, ensure_ascii=False)
    for value in ("apm.example.test", "tok-secret-123", "fakeuser", "/fake/audit", "hook.example"):
        assert value not in dumped
    for name in ("noise_gate.apm_mcp_url", "noise_gate.investigation_service_token",
                 "noise_gate.l3_ssh_user", "noise_gate.decision_store_path",
                 "alarm.webhook_url", "alarm.redis_stream_key"):
        assert name in snap["excluded"]
    groups = snap["groups"]
    importance = groups["noise_gate"]["importance_value_map_csv"]
    assert importance == {"1": "낮음", "2": "보통", "3": "높음"}
    assert groups["noise_gate"]["enrichment_profile_map_csv"] == "설정됨: 2항목"
    assert groups["noise_gate"]["cross_source_mode"] == "shadow"
    assert groups["noise_gate"]["enable_noise_gate"] is True
    assert groups["noise_gate"]["enrichment_min_tier"] == "PAGE"
    assert groups["alarm"]["min_severity"] == 2
    # 민감 토큰(host)이 든 문자열 필드는 값 없이 제외 · 그 밖의 비허용 형은 생략
    assert "noise_gate.l3_host_access_mode" in snap["excluded"]
    assert "alarm.redis_consumer_group" in snap["omitted"]


# ── 7 읽기 전용 ───────────────────────────────────────────────────────────────
def test_readonly_wrapper_exposes_only_read_commands() -> None:
    public = {n for n in dir(pa.ReadOnlyStreams) if not n.startswith("_")}
    assert public == READ_COMMANDS
    reader = pa.ReadOnlyStreams(object())
    for write in ("xadd", "xack", "xreadgroup", "xgroup_create", "xdel", "xtrim", "set", "delete"):
        assert not hasattr(reader, write)
    with pytest.raises(AttributeError):
        reader.extra = 1  # type: ignore[attr-defined]


def test_run_issues_no_write_commands(tmp_path: Path) -> None:
    fake = _fake_redis()
    _run(tmp_path, streams=pa.ReadOnlyStreams(fake))
    assert fake.calls and set(fake.calls) <= READ_COMMANDS


def test_stream_paging_reads_every_entry() -> None:
    entries = [(f"{1791849600000 + i}-0".encode(), {b"data": b"{}"}) for i in range(2500)]
    fake = FakeRedis({"s": entries}, {})
    start = datetime.fromtimestamp(1791849600, tz=UTC)
    got = pa.read_stream(pa.ReadOnlyStreams(fake), "s", start, start + timedelta(hours=1))
    assert len(got) == 2500 and fake.calls == ["xrange"] * 3


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    fake = _fake_redis()
    info = pa.dry_run(config=_config(), decisions_path=FIXTURE / "decisions.jsonl", hours=24,
                      days=None, streams=pa.ReadOnlyStreams(fake), now=NOW)
    assert info["decisions"] == 4 and info["recurrences"] == 1
    assert info["stream"]["raw_entries"] == 9 and info["stream"]["dead_entries"] == 1
    assert set(fake.calls) <= READ_COMMANDS
    assert list(tmp_path.iterdir()) == []


# ── 교정 — 보안(누출 경계·값 허용 목록·쓰기) ─────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "응답없음 192.0.2.10.", "ip=0192.0.2.1", "addr 192.0.2.1.5", "::ffff:192.0.2.7 끊김",
    "192-0-2-9 down", "192．0．2．9 down", "１９２.0.2.3 down", "198.51.100.4:8080 timeout",
    "net 198.51.100.0/24 drop",
])
def test_ipv4_boundary_variants_are_masked(raw: str) -> None:
    out = normalize_text(raw, max_len=200)
    assert "<IP>" in out
    assert not any(part in out for part in ("192", "198", "１９２"))
    assert leak_check({"x": out}, IdentifierSet())["passed"] is True


@pytest.mark.parametrize("raw", [
    "응답없음 192.0.2.10.", "addr 192.0.2.1.5", "::ffff:192.0.2.7", "192-0-2-9 down",
    "192．0．2．9 down", "１９２.0.2.3", "mac aabb.ccdd.eeff", "mac aa:bb:cc:dd:ee:ff",
])
def test_leak_patterns_catch_raw_variants(raw: str) -> None:
    assert leak_check({"x": raw}, IdentifierSet())["passed"] is False


@pytest.mark.parametrize(("raw", "secret", "placeholder"), [
    ("fake-db.intranet 접속 실패", "fake-db", "<HOST>"),
    ("fake-app.corp 응답", "fake-app", "<HOST>"),
    ("/var/log/fake.log 가득", "fake.log", "<PATH>"),
    (r"C:\fake\dir 없음", "fake", "<PATH>"),
    ("user=fakeuser 로그인 실패", "fakeuser", "<USER>"),
    ("계정:fakeacct 잠김", "fakeacct", "<USER>"),
    ("id=fake01 차단", "fake01", "<USER>"),
    ("mac aabb.ccdd.eeff 변경", "aabb", "<MAC>"),
])
def test_fqdn_path_account_mac_are_masked(raw: str, secret: str, placeholder: str) -> None:
    out = normalize_text(raw, max_len=200)
    assert secret not in out and placeholder in out


def test_word_pairs_and_ratios_survive_path_masking() -> None:
    assert normalize_text("I/O 지연 90%") == "I/O 지연 90%"
    assert normalize_text("net <IP>/24") == "net <IP>/24"


def test_free_text_values_under_allowed_keys_become_other() -> None:
    signals = sanitize_signals(
        {"pattern": "fake-pattern 원문", "noti_policy": "fake-team", "severity": "3",
         "importance": "보통"}, Pseudonymizer(b"k" * 32))
    assert signals == {"severity": None, "importance": "보통", "pattern": OTHER,
                       "noti_policy": OTHER}
    dropped: Counter[str] = Counter()
    pseudo = Pseudonymizer(b"k" * 32)
    evidence = sanitize_evidence(
        {"markers": ["fake-host-88 점검"], "promote": ["fake reason"],
         "apm_policy": {"event_type_norm": "fake svc err", "fake sub": 1},
         "cross_source": {"rule_id": 7},
         "fake host key": 1, "inhibitor": 2}, dropped, pseudo)
    assert evidence == {"markers": [OTHER], "promote": [OTHER],
                        "apm_policy": {"event_type_norm": pseudo("N", "fake svc err")},
                        "cross_source": {"rule_id": None}}
    assert dropped == {UNKNOWN_KEY: 2, "inhibitor": 1}
    assert sanitize_recurrence({"count": "fake-5", "first_seen": 1, "last_seen": "x"}) == {
        "count": None, "span_s": None}
    assert record_type_label("fake weird type") == UNKNOWN_KEY
    assert record_type_label("investigation") == "investigation"


def test_free_text_values_in_run_do_not_leak(tmp_path: Path) -> None:
    rec = _decision(0, "seq-val-host", "디스크 가득")
    rec["signals"].update({"pattern": "seq-val-host spike", "noti_policy": "fake-vip-team"})
    rec["stage_evidence"] = {"markers": ["seq-val-host 점검"], "mode": "fake core mode",
                             "fake-key-seq-val-host": 1}
    path = tmp_path / "val.jsonl"
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n" +
                    json.dumps({"ts": rec["ts"], "type": "fake weird type"}) + "\n", "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=1))
    assert result.exit_code == 0 and result.out_dir is not None
    text = _all_text(result.out_dir)
    for secret in ("seq-val-host", "fake-vip-team", "fake core mode", "fake weird type"):
        assert secret not in text
    s10 = json.loads((result.out_dir / "summary.json").read_text("utf-8"))["S10"]
    assert s10["dropped_evidence_keys"] == {UNKNOWN_KEY: 1}


def test_leak_check_folds_case_width_and_matches_short_ids_exactly() -> None:
    idents = IdentifierSet()
    idents.add(IDENT_HOST, "fake-web-01")
    idents.add(IDENT_HOST, "db")
    idents.add(IDENT_ALARM_ID, "123456")
    assert leak_check({"x": "ＦＡＫＥ-WEB-01"}, idents)["passed"] is False
    assert leak_check({"x": "db 경보"}, idents)["passed"] is False
    report = leak_check({"x": "dbms 9123456 id 123456"}, idents)
    assert report["passed"] is True
    assert report["short_identifiers_exact_match"] == 1
    assert report["numeric_identifiers_excluded"] == 1
    idents.add(IDENT_RESOURCE, "Memory")
    idents.exclude_vocabulary(" ".join(vocabulary_words()))
    report = leak_check({"x": "memory"}, idents)
    assert report["passed"] is True and report["vocabulary_identifiers_excluded"] == 1


def test_outputs_are_private_and_tmp_dir_is_gone(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.exit_code == 0 and result.out_dir is not None
    assert stat.S_IMODE(result.out_dir.stat().st_mode) == 0o700
    for path in result.out_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in (tmp_path / "out").iterdir()] == [result.out_dir.name]


def test_write_failure_leaves_no_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = pa._write_private
    calls: list[str] = []

    def _flaky(path: Path, text: str) -> None:
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(pa, "_write_private", _flaky)
    with pytest.raises(OSError):
        _run(tmp_path)
    assert list((tmp_path / "out").iterdir()) == []


def test_leak_check_file_is_written_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = pa._write_private
    names: list[str] = []

    def _record(path: Path, text: str) -> None:
        names.append(path.name)
        real(path, text)

    monkeypatch.setattr(pa, "_write_private", _record)
    _run(tmp_path)
    assert names[-1] == "leak_check.json" and len(names) == len(OUTPUT_FILES)


def test_readonly_wrapper_has_no_client_attribute() -> None:
    reader = pa.ReadOnlyStreams(FakeRedis({}, {}))
    assert not hasattr(reader, "_client")
    assert pa.ReadOnlyStreams.__slots__ == ("_read",)


def test_stream_read_stops_at_max_entries() -> None:
    entries = [(f"{1791849600000 + i}-0".encode(), {b"data": b"{}"}) for i in range(2500)]
    fake = FakeRedis({"s": entries}, {})
    start = datetime.fromtimestamp(1791849600, tz=UTC)
    with pytest.raises(pa.StreamLimitError):
        pa.read_stream(pa.ReadOnlyStreams(fake), "s", start, start + timedelta(hours=1),
                       max_entries=1500)


def test_cli_error_prints_exception_type_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "AppConfig", lambda: _config())
    bad = tmp_path / "fake-secret-dir"
    bad.mkdir()
    argv = ["--no-stream", "--decisions", str(bad), "--out", str(tmp_path / "out")]
    assert pa.main(argv) == 1
    err = capsys.readouterr().err
    assert "실행 실패: IsADirectoryError" in err and "fake-secret-dir" not in err
    with pytest.raises(IsADirectoryError):
        pa.main([*argv, "-v"])


def test_snapshot_excludes_sensitive_name_tokens() -> None:
    group = SimpleNamespace(
        db_password="x", smtp_port=25, ops_email="x", dsn_text="x", cred_file="x",
        remote_addr="x", importance_weight=3, bypass_enabled=True, host_flag_enabled=True,
    )
    values, excluded, _ = snapshot_group(group)
    assert sorted(excluded) == ["cred_file", "db_password", "dsn_text", "ops_email",
                                "remote_addr", "smtp_port"]
    assert values == {"bypass_enabled": True, "host_flag_enabled": True, "importance_weight": 3}


# ── 교정 — 집계(no-stream · 원문 없는 행 · 창 · 고아) ───────────────────────────
def test_no_stream_marks_stream_dispositions_unknown(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.out_dir is not None
    s1 = json.loads((result.out_dir / "summary.json").read_text("utf-8"))["S1"]
    assert s1["received"] is None and s1["decision_without_raw"] is None
    assert {k for k, v in s1["dispositions"].items() if v is None} == pa.STREAM_DISPOSITIONS
    md = (result.out_dir / "summary.md").read_text("utf-8")
    s1_md = md.split("## S1", 1)[1].split("##", 1)[0]
    assert "판정 건수 4건" in s1_md and not re.search(r"수신 \d+건", s1_md)
    assert all(r["is_message"] is None for r in _rows(result.out_dir))


def test_row_without_raw_has_unknown_is_message(tmp_path: Path) -> None:
    result = _run(tmp_path, streams=pa.ReadOnlyStreams(_fake_redis()))
    assert result.out_dir is not None
    rows = [r for r in _rows(result.out_dir) if r["disposition"] == "decision_without_raw"]
    assert rows and all(r["is_message"] is None for r in rows)


def test_sev3_dedup_window_uses_original_severity(tmp_path: Path) -> None:
    ng = NoiseGateConfig(enable_noise_gate=True, repeat_interval_seconds=14400,
                         sev3_repeat_interval_seconds=600, cross_source_mode="off",
                         importance_value_map_csv="1=낮음,2=보통,3=높음")
    cfg = SimpleNamespace(noise_gate=ng, alarm=AlarmConfig(min_severity=2))
    recs = [_decision(0, "seq-d3", "디스크 가득", sev=3, stage="severity3"),
            _decision(1800, "seq-d3", "디스크 가득", sev=3, stage="severity3"),
            _decision(0, "seq-d2", "링크 지연"), _decision(1800, "seq-d2", "링크 지연")]
    path = tmp_path / "dedup.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, config=cfg, now=_SEQ_BASE + timedelta(hours=1))
    assert result.out_dir is not None
    by_name: dict[str, list[Any]] = {}
    for r in _rows(result.out_dir):
        by_name.setdefault(r["alarm_name_norm"], []).append(r["within_dedup_window_repage"])
    assert by_name == {"디스크 가득": [False, False], "링크 지연": [False, True]}


def test_s4_counts_only_pages_inside_30m_cluster(tmp_path: Path) -> None:
    recs = [_decision(0, "seq-s4", "지연 A"), _decision(600, "seq-s4", "지연 B"),
            _decision(5400, "seq-s4", "지연 C")]
    path = tmp_path / "s4.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=3))
    assert result.out_dir is not None
    s4 = json.loads((result.out_dir / "summary.json").read_text("utf-8"))["S4"]
    assert s4["hosts_multi_page_30m"] == 1 and s4["pages_on_those_hosts"] == 2


def test_s5_burst_window_slides_across_minute_boundary(tmp_path: Path) -> None:
    # 01:00:40~01:01:20 — 분 경계를 넘는 5건
    recs = [_decision(40 + i * 10, f"seq-s5-{i}", f"폭주 {i}") for i in range(5)]
    path = tmp_path / "s5.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=1))
    assert result.out_dir is not None
    s5 = json.loads((result.out_dir / "summary.json").read_text("utf-8"))["S5"]
    assert s5["bursts"] == [{"minute": "2026-10-13T01:00", "pages": 5, "gap_before_s": None}]


def _page_event(idx: int, ts: float, alarm_id: str, fp: str = "f") -> pa.PageEvent:
    return pa.PageEvent(idx=idx, ts=ts, fp=fp, host=("polestar_cm_gp", "h"), defn=("d", "x"),
                        eff_sev=2, severity=2, importance="보통", stage="matrix", app_fatal=0,
                        boost_origin=None, page_days_7d=1, r6_decidable=False, alarm_id=alarm_id)


def test_orphan_check_is_row_level() -> None:
    events = [_page_event(0, 0.0, "SEQ-A"), _page_event(1, 60.0, "SEQ-B")]
    bound = pa.RunResult(demoted_by={1: "R1"}, links={1: ("R1", "SEQ-A")})
    assert pa.verify_orphans(events, bound) == 0
    missing = pa.RunResult(demoted_by={1: "R1"}, links={1: ("R1", "SEQ-X")})
    assert pa.verify_orphans(events, missing) == 1
    rep_demoted = pa.RunResult(demoted_by={0: "R4", 1: "R1"}, links={1: ("R1", "SEQ-A")})
    assert pa.verify_orphans(events, rep_demoted) == 1


def test_s8_and_s10_carry_caveat_notes(tmp_path: Path) -> None:
    result = _run(tmp_path, decisions=_sequence_file(tmp_path))
    assert result.out_dir is not None
    s = json.loads((result.out_dir / "summary.json").read_text("utf-8"))
    assert s["S8"]["notes"] == list(pa.S8_NOTES) and s["S10"]["notes"] == list(pa.S10_NOTES)
    assert any("R6" in note and "보존 1" in note for note in pa.S8_NOTES)
    md = (result.out_dir / "summary.md").read_text("utf-8")
    assert pa.S8_NOTES[2] in md and pa.S10_NOTES[0] in md


# ── 교정 2차 — 코드형 값 가명 · 서식 문자 · 구분자 변형 ──────────────────────────
def test_code_like_names_are_pseudonymized_in_run(tmp_path: Path) -> None:
    rec = _decision(0, "seq-code-host", "디스크 가득")
    rec["stage_evidence"] = {"apm_policy": {"event_type_norm": "fakepay-svc-error"},
                             "cross_source": {"rule_id": "r-fakeacme", "applied": True}}
    path = tmp_path / "code.jsonl"
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", "utf-8")
    key = b"c" * 32
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=1), key=key)
    assert result.exit_code == 0 and result.out_dir is not None
    text = _all_text(result.out_dir)
    assert "fakepay" not in text and "fakeacme" not in text
    (row,) = _rows(result.out_dir)
    pseudo = Pseudonymizer(key)
    assert row["evidence"]["apm_policy"]["event_type_norm"] == pseudo("N", "fakepay-svc-error")
    assert row["evidence"]["cross_source"] == {"rule_id": pseudo("N", "r-fakeacme"),
                                               "applied": True}


@pytest.mark.parametrize("raw", ["fake\u200b-web-01 다운", "fake-\u2060web-01 다운",
                                 "\ufefffake-web-01 다운", "fake-web\u200d-01 다운"])
def test_format_chars_cannot_split_identifiers(raw: str) -> None:
    idents = IdentifierSet()
    idents.add(IDENT_HOST, "fake-web-01")
    out = normalize_text(raw, identifiers=idents)
    assert out == "<HOST> 다운"
    assert leak_check({"x": raw}, idents)["passed"] is False


@pytest.mark.parametrize("raw", [
    "링크 198_51_100_7 다운", "링크 198 51 100 7 다운", "링크 198,51,100,7 다운",
    "링크 198·51·100·7 다운", "링크 198‧51‧100‧7 다운", "링크 10_1_2_3 다운",
])
def test_wide_separator_ipv4_is_masked_and_detected(raw: str) -> None:
    out = normalize_text(raw)
    assert out == "링크 <IP> 다운"
    assert leak_check({"x": raw}, IdentifierSet())["passed"] is False


@pytest.mark.parametrize("variant", ["FAKE_WEB_01 다운", "fake.web.01 다운", "fake web 01 다운",
                                     "fake_web-01 다운"])
def test_separator_variants_of_identifiers_are_caught(variant: str) -> None:
    idents = IdentifierSet()
    idents.add(IDENT_HOST, "fake-web-01")
    assert normalize_text(variant, identifiers=idents) == "<HOST> 다운"
    assert leak_check({"x": variant}, idents)["passed"] is False


@pytest.mark.parametrize("text", [
    "2026-10-13T09:12:03+09:00", "2026-10-13 09:12:03", "20261013-091203", "v1.2", "임계 90 80",
    "1, 2, 3, 4", "p50 120ms · p95 980ms",
])
def test_wide_separator_patterns_do_not_flag_times_or_versions(text: str) -> None:
    assert leak_check({"x": text}, IdentifierSet())["passed"] is True


def test_version_and_time_in_alarm_name_do_not_close_run(tmp_path: Path) -> None:
    recs = [_decision(0, "seq-ver-host", "agent v5.2.1 재시작 2026-10-13 09:12"),
            _decision(60, "seq-ver-host", "임계 90 80 70 초과")]
    path = tmp_path / "ver.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    result = _run(tmp_path, decisions=path, now=_SEQ_BASE + timedelta(hours=1))
    assert result.exit_code == 0, result.leak
    names = [r["alarm_name_norm"] for r in _rows(result.out_dir)]  # type: ignore[arg-type]
    assert names == ["agent v<N> 재시작 <N> 09:12", "임계 90 80 70 초과"]
