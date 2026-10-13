"""plans/151 W3 — 결정적 채점기(§4.4)·실패 유형(§4.5)·보고서(§4.9) 규칙 고정.

실 LLM 0 · 네트워크 0. 브리핑은 손으로 쓴 픽스처이고 세계는 소형 채점 전용 세계
(`tests/fixtures/investigation_bench/judge/worlds/`)다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from investigation_bench import judge, report, schema
from investigation_bench.judge import (
    JUDGE_VERSION,
    claim_supported,
    collect_numbers,
    extract_claims,
    rejudge,
    score_run,
)
from investigation_bench.worlds import load_worlds

_FIX = Path(__file__).resolve().parent / "fixtures" / "investigation_bench"
_WORLDS_DIR = _FIX / "judge" / "worlds"
_WORLDS = load_worlds([_WORLDS_DIR])
_APP = "bw-j01-app01"

_FS_REPLY = {"rows": [{"mount": "/var/log", "value": 98.4, "used_mb": 2048}], "row_count": 1,
             "unit": "%"}
_ALARM_REPLY = {"rows": [{"msg": "logrotate 실패 3회"}], "row_count": 1}
_CPU_NOTE = "매시 정각 60%대 — 지난 14일 동일 패턴"
_CPU_REPLY = {"rows": [{"value": 61.2}], "row_count": 1, "note": _CPU_NOTE}


# ─────────────────────────────── 픽스처 도우미 ───────────────────────────────


class Run:
    """raw 1행 + 그 실행의 world_calls를 함께 만든다."""

    def __init__(self, world_id: str = "J01", *, arm: str = "control", trial: int = 1) -> None:
        self.world_id, self.arm, self.trial = world_id, arm, trial
        self.trace: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def call(self, tool: str, args: dict[str, Any], reply: Any, *, arg_error: str | None = None,
             phase: str = "investigate") -> Run:
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.trace.append({"seq": len(self.trace) + 1, "phase": phase, "tool": tool, "args": args,
                           "matcher": None if arg_error else f"{tool}#0", "arg_error": arg_error,
                           "reply_hash": digest})
        self.calls.append({"world_id": self.world_id, "trial": self.trial, "arm": self.arm,
                           "tool": tool, "args": args, "reply": reply, "reply_hash": digest})
        return self

    def fs(self, **extra: Any) -> Run:
        return self.call("polestar_metric_trend",
                         {"source": "polestar", "server_name": _APP, "kind": "filesystem", **extra},
                         _FS_REPLY)

    def alarms(self) -> Run:
        return self.call("polestar_incident_alarms", {"source": "polestar", "server_name": _APP},
                         _ALARM_REPLY)

    def raw(self, briefing: Any, **extra: Any) -> dict[str, Any]:
        row = {"arm": self.arm, "world_id": self.world_id, "split": _WORLDS[self.world_id].split,
               "trial": self.trial,
               "investigation_id": f"inv-{self.arm}-{self.world_id}-{self.trial}",
               "status": "done", "incomplete": False, "elapsed_s": 10.0, "steps": 4,
               "tokens": 1000, "tool_calls_n": len(self.trace), "briefing": briefing,
               "tool_trace": list(self.trace), "audit": [], "infra_error": None}
        row.update(extra)
        return row


def _hyp(rank: int, cause: str, confidence: str = "medium", reasoning: str = "") -> dict[str, Any]:
    return {"rank": rank, "cause": cause, "confidence": confidence, "evidence": [],
            "reasoning": reasoning}


def _briefing(*hyps: dict[str, Any], summary: str = "", insufficient: bool = False,
              cause: str | None = None) -> dict[str, Any]:
    return {
        "severity": {"level": "high", "confidence": "high", "escalate": True,
                     "evidence_insufficient": insufficient},
        "summary": summary or (hyps[0]["cause"] if hyps else ""),
        "timeline": [],
        "bottleneck": "미확정(시그니처 매칭 없음)",
        "cause": cause if cause is not None else (hyps[0]["cause"] if hyps else ""),
        "root_cause_hypotheses": list(hyps),
        "recommendation": {"items": [], "note": ""},
        "limitations": ["프로세스·자원 스냅샷은 조사 시점 단면일 수 있음"],
        "citations_verified": True,
        "hypotheses": [],
    }


_GOOD = "bw-j01-app01 /var/log 파일시스템 98.4% — logrotate 실패로 디스크 포화"


def _score(run: Run, briefing: Any, **extra: Any) -> dict[str, Any]:
    return score_run([run.raw(briefing, **extra)], run.calls, _WORLDS)[0]


# ─────────────────────────────── 픽스처 세계 ───────────────────────────────


def test_fixture_worlds_pass_schema_lint() -> None:
    snap = schema.load_snapshot(_FIX / "tool_schemas.json")
    for world in _WORLDS.values():
        assert schema.check_world(world.data, snap) == [], world.id


# ─────────────────────────────── RC ───────────────────────────────


def test_rc1_requires_entity_and_kind() -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, _GOOD)))
    assert (row["rc1"], row["rc3"], row["entity_hit"], row["kind_hit"], row["component_hit"]) == (
        True, True, True, True, True)
    assert row["failure_code"] == "PASS"
    assert row["judge_version"] == JUDGE_VERSION


def test_rc1_partial_kind_only_records_partial() -> None:
    # 원인 kind는 맞지만 다른 호스트를 지목 — 적중 아님 · kind만 부분 점수
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, "bw-j01-db01 디스크 포화")))
    assert (row["rc1"], row["kind_hit"], row["entity_hit"]) == (False, True, False)
    assert row["failure_code"] == "F-READ"


def test_rc1_partial_entity_only_records_partial() -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, "bw-j01-app01 /var/log 메모리 누수")))
    assert (row["rc1"], row["kind_hit"], row["entity_hit"], row["component_hit"]) == (
        False, False, True, True)


def test_rc_host_unmentioned_means_alarm_host() -> None:
    # J01: 원인 호스트 = 알람 호스트 → 호스트 미언급이면 적중
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, "/var/log 98% logrotate 실패")))
    assert row["entity_hit"] is True and row["rc1"] is True
    # J03: 원인 호스트(DB) ≠ 알람 호스트(앱) → 미언급이면 알람 호스트를 지목한 것이라 불일치
    args = {"source": "polestar", "server_name": "bw-j03-db01", "kind": "tablespace"}
    reply = {"rows": [{"value": 99.1}], "row_count": 1}
    run = Run("J03").call("polestar_metric_trend", args, reply)
    miss = _score(run, _briefing(_hyp(1, "테이블스페이스 포화 99%")))
    hit = _score(run, _briefing(_hyp(1, "bw-j03-db01 테이블스페이스 포화 99%")))
    assert (miss["kind_hit"], miss["entity_hit"], miss["rc1"]) == (True, False, False)
    assert hit["rc1"] is True and hit["component_hit"] is None  # 라벨에 구성요소 없음


@pytest.mark.parametrize("cause", [
    "BW-J01-APP01 FileSystem   Full",
    "bw-j01-app01 Disk_Full",
    "bw-j01-app01 디스크포화",          # 별칭 「디스크 포화」 — 공백 차이
    "bw-j01-app01 LogRotate 실패",      # 한영 혼용 · 대소문자
    "bw-j01-app01 ＤＩＳＫ ＦＵＬＬ",    # 전각
])
def test_kind_normalization(cause: str) -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, cause)))
    assert row["kind_hit"] is True and row["rc1"] is True


def test_rc3_rank_boundary_and_rank_order() -> None:
    wrong = [_hyp(1, "메모리 누수"), _hyp(2, "네트워크 단절")]
    at3 = _score(Run().fs().alarms(), _briefing(*wrong, _hyp(3, _GOOD)))
    at4 = _score(Run().fs().alarms(), _briefing(*wrong, _hyp(3, "커널 패닉"), _hyp(4, _GOOD)))
    assert (at3["rc1"], at3["rc3"]) == (False, True)
    assert (at4["rc1"], at4["rc3"]) == (False, False)
    # 목록 순서가 아니라 rank 값으로 정렬한다
    shuffled = _score(Run().fs().alarms(), _briefing(_hyp(2, "메모리 누수"), _hyp(1, _GOOD)))
    assert shuffled["rc1"] is True


def test_cause_is_rank1_when_no_hypotheses() -> None:
    row = _score(Run().fs().alarms(), _briefing(cause=_GOOD))
    assert row["rc1"] is True and row["top1"]["cause"] == _GOOD


# ─────────────────────────────── ECR ───────────────────────────────


def test_ecr_counts_only_evidence_world_actually_served() -> None:
    # cite 토큰(98·/var/log·logrotate)은 브리핑에 다 있지만 알람 도구는 부르지 않았다
    row = _score(Run().fs(), _briefing(_hyp(1, _GOOD)))
    assert row["ecr"] == {"num": 1, "den": 2, "value": 0.5}
    # 아무 도구도 안 불렀으면 브리핑에 토큰이 있어도 0
    none = _score(Run(), _briefing(_hyp(1, _GOOD)))
    assert none["ecr"]["num"] == 0


def test_ecr_match_subset_null_args_and_errors() -> None:
    # 매처 키(kind=filesystem)만 맞으면 다른 인자·null 인자가 있어도 인정
    ok = _score(Run().fs(granularity="h", reference_time=None).alarms(), _briefing(_hyp(1, _GOOD)))
    assert ok["ecr"]["num"] == 2
    # 다른 매처(kind=cpu) 응답은 불인정
    cpu_args = {"source": "polestar", "server_name": _APP, "kind": "cpu"}
    cpu = Run().call("polestar_metric_trend", cpu_args, _CPU_REPLY).alarms()
    assert _score(cpu, _briefing(_hyp(1, _GOOD)))["ecr"]["num"] == 1
    # 인자 오류 호출 · 오류 응답은 불인정
    bad = Run().call("polestar_metric_trend",
                     {"source": "polestar", "server_name": "bw-x", "kind": "filesystem"},
                     {"rows": [], "row_count": 0, "message": "대상 없음"}, arg_error="host")
    bad.call("polestar_incident_alarms", {"source": "polestar", "server_name": _APP},
             {"error": "DB 연결 시간 초과"})
    assert _score(bad, _briefing(_hyp(1, _GOOD)))["ecr"]["num"] == 0


def test_ecr_requires_cite_tokens_in_briefing() -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, "bw-j01-app01 디스크 포화")))
    assert row["ecr"]["num"] == 0
    assert row["failure_code"] == "F-CITE"


# ─────────────────────────────── HALLU ───────────────────────────────


def _claims(text: str, hosts: tuple[str, ...] = ()) -> list[str]:
    return [c.text for c in extract_claims([text], hosts)]


def test_claim_extraction_units_and_exclusions() -> None:
    text = ("bw-j01-app01 /var/log 98.4% · 2GB · 오류 1,520건 · 응답 320ms · 3.2초 · "
            "2026-10-12 03:15:00 · 10월 12일 3시 15분 · 20261012031500 · v1.2.3 · 2.4.1 · "
            "T-15m · 최근 60분 · 12시간 · app01 · CPU 4코어")
    assert _claims(text, ("bw-j01-app01",)) == ["98.4%", "2GB", "1,520건", "320ms", "3.2초"]


def test_hostname_digits_are_not_claims() -> None:
    assert _claims("gpu7 node-12% 확인", ()) == []          # 식별자에 붙은 숫자
    assert _claims("bw-j01-app01 50% 호스트", ("bw-j01-app01",)) == ["50%"]


def _supported(claim: str, *values: Any) -> bool:
    nums: list[Any] = []
    collect_numbers(list(values), nums)
    (c,) = extract_claims([claim])
    return claim_supported(c, nums)


def test_rounding_both_ways() -> None:
    assert _supported("98%", 98.4)
    assert _supported("98.4%", 98)
    assert _supported("98.43%", 98.4)
    assert not _supported("98.5%", 98.4)
    assert not _supported("99%", 98.4)
    assert not _supported("99.7%", 98.4)


def test_unit_conversion() -> None:
    assert _supported("2GB", 2048)            # MB → GB (1024)
    assert _supported("1.5GB", 1500)          # 1000 기수
    assert _supported("2048MB", 2)            # GB → MB
    assert _supported("3.2초", 3200)          # ms → 초
    assert _supported("320ms", 0.32)
    assert not _supported("3GB", 2048)


def test_numbers_inside_strings_count() -> None:
    assert _supported("3회", "logrotate 실패 3회")


def test_hallu_scoring_with_alarm_value_and_served_replies() -> None:
    # 98.4%·2GB = 응답 · 90% = 알람 이벤트 문구 · 99.7% = 어디에도 없음
    text = _GOOD + " · 사용 2GB · 임계 90% · 최고 99.7%"
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, text)))
    assert row["hallu"] == {"num": 1, "den": 4, "claims": ["99.7%"]}
    assert row["failure_code"] == "F-HALLU"


def test_hallu_ignores_unserved_world_rules() -> None:
    # 세계 YAML에는 61.2(cpu)가 있지만 이 실행에서 내준 적이 없다 → 환각
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, _GOOD + " · CPU 61.2%")))
    assert row["hallu"]["claims"] == ["61.2%"]


def test_hallu_skips_fixed_briefing_fields() -> None:
    b = _briefing(_hyp(1, _GOOD))
    b["limitations"] = ["임계 77.7% 이상만 상향"]
    b["bottleneck"] = "CPU 95% 시그니처"
    assert _score(Run().fs().alarms(), b)["hallu"]["den"] == 1


# ─────────────────────────────── DIST · ABST ───────────────────────────────


def test_dist_top1_is_distractor() -> None:
    run = Run().fs().alarms()
    row = _score(run, _briefing(_hyp(1, "CPU 과부하가 원인 — 61% 지속")))
    assert row["dist"] is True and row["failure_code"] == "F-DIST"
    # 정답 표면형도 함께 말하면 유인으로 세지 않는다(부정 서술 「CPU 과부하가 아니라 …」)
    both = _score(run, _briefing(_hyp(1, "CPU 과부하가 아니라 /var/log 디스크 포화")))
    assert both["dist"] is False and both["rc1"] is True


def test_abst_no_fault_world_both_ways() -> None:
    run = Run("J02").call("polestar_metric_trend",
                          {"source": "polestar", "server_name": "bw-j02-app01", "kind": "cpu"},
                          {"rows": [{"value": 62.0}], "row_count": 1, "note": _CPU_NOTE})
    asserted = _score(run, _briefing(_hyp(1, "메모리 누수로 응답 저하", "high")))
    assert (asserted["abst_ok"], asserted["abstained"]) == (False, False)
    assert asserted["failure_code"] == "F-ABST"
    assert asserted["rc1"] is None  # 무장애 세계는 RC를 매기지 않는다
    lured = _score(run, _briefing(_hyp(1, "CPU 과부하", "high")))
    assert lured["failure_code"] == "F-DIST"
    held_b = _briefing(_hyp(1, "지난 14일 동일 패턴 — 장애 근거 없음", "low"), insufficient=True)
    held = _score(run, held_b)
    assert (held["abst_ok"], held["failure_code"]) == (True, "PASS")


def test_abst_sufficient_world_marked_insufficient() -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, _GOOD), insufficient=True))
    assert (row["rc1"], row["abst_ok"], row["failure_code"]) == (True, False, "F-ABST")
    low = _score(Run().fs().alarms(), _briefing(_hyp(1, _GOOD, "low")))
    assert low["abstained"] is True and low["failure_code"] == "F-ABST"


def test_abstain_threshold_constant() -> None:
    assert judge.CONFIDENCE_RANK["low"] == judge.ABSTAIN_MAX_CONFIDENCE_RANK
    assert judge.CONFIDENCE_RANK["medium"] > judge.ABSTAIN_MAX_CONFIDENCE_RANK


# ─────────────────────────────── 실패 유형 우선순위 ───────────────────────────────


@pytest.mark.parametrize("status,extra", [
    ("rejected", {}), ("stub", {}), ("failed", {}), ("running", {}),
    ("done", {"infra_error": "poll_timeout"}),
])
def test_infra_invalid(status: str, extra: dict[str, Any]) -> None:
    row = _score(Run().fs().alarms(), _briefing(_hyp(1, _GOOD)), status=status, **extra)
    assert (row["valid"], row["failure_code"], row["rc1"]) == (False, "F-INFRA", None)


def test_done_with_stub_briefing_is_invalid() -> None:
    row = _score(Run(), {"stub": True, "message": "x", "elements": None})
    assert row["valid"] is False and row["failure_code"] == "F-INFRA"


def test_priority_tmo_then_inc() -> None:
    good = _briefing(_hyp(1, _GOOD))
    tmo = _score(Run().fs().alarms(), {"stub": True}, status="timeout", incomplete=True)
    assert (tmo["valid"], tmo["failure_code"], tmo["complete"]) == (True, "F-TMO", False)
    inc = _score(Run().fs().alarms(), good, incomplete=True)
    assert (inc["rc1"], inc["failure_code"], inc["complete"]) == (True, "F-INC", False)


def test_priority_arg_loop_miss_read() -> None:
    wrong = _briefing(_hyp(1, "메모리 누수"))
    arg = Run().call("polestar_metric_trend", {"source": "polestar", "server_name": "bw-x",
                                               "kind": "filesystem"},
                     {"rows": [], "row_count": 0}, arg_error="host")
    arg.call("polestar_incident_alarms", {"source": "polestar"}, "Error executing tool",
             arg_error="required")
    # 인자 오류가 반복·정체보다 앞선다
    stalled = [{"event": "done", "unresolved": ["x x3"]}]
    assert _score(arg, wrong, audit=stalled)["failure_code"] == "F-ARG"

    loop = Run()
    for _ in range(3):
        loop.call("polestar_host_status", {"source": "polestar", "server_name": _APP}, {"rows": []})
    assert _score(loop, wrong)["failure_code"] == "F-LOOP"
    unresolved = _score(Run(), wrong, audit=[{"event": "done", "unresolved": ["a x3"]}])
    assert unresolved["failure_code"] == "F-LOOP"

    assert _score(Run(), wrong)["failure_code"] == "F-MISS"
    assert _score(Run().fs().alarms(), wrong)["failure_code"] == "F-READ"
    # 핵심 증거를 받았고 1위가 유인이면 F-READ가 아니라 F-DIST
    assert _score(Run().fs().alarms(), _briefing(_hyp(1, "CPU 과부하")))["failure_code"] == "F-DIST"


def test_priority_hallu_before_cite_before_abst() -> None:
    # 원인 적중 + 환각 + ECR 0 + 불충분 표기 → F-HALLU
    row = _score(Run(), _briefing(_hyp(1, "bw-j01-app01 디스크 포화 77%"), insufficient=True))
    assert row["rc1"] is True and row["failure_code"] == "F-HALLU"
    # 환각 없음 → F-CITE
    row = _score(Run(), _briefing(_hyp(1, "bw-j01-app01 디스크 포화"), insufficient=True))
    assert row["failure_code"] == "F-CITE"


def test_failure_codes_order() -> None:
    assert judge.FAILURE_CODES[0] == "F-INFRA" and judge.FAILURE_CODES[-1] == "PASS"
    assert judge.FAILURE_CODES.index("F-DIST") < judge.FAILURE_CODES.index("F-READ")


def test_unknown_world_raises() -> None:
    with pytest.raises(KeyError):
        score_run([{"world_id": "NOPE", "status": "done"}], [], _WORLDS)


# ─────────────────────────────── 보고서 지표 ───────────────────────────────


def test_invalid_excluded_from_denominator() -> None:
    good = Run(trial=1).fs().alarms()
    bad = Run(trial=2)
    rows = score_run([good.raw(_briefing(_hyp(1, _GOOD))), bad.raw(None, status="rejected")],
                     good.calls, _WORLDS)
    m = report.metrics(rows)
    assert (m["n"], m["valid"], m["rc1"], m["complete"]) == (2, 1, 1.0, 1.0)


def test_maj_at_k() -> None:
    raws, calls = [], []
    for trial, cause in enumerate([_GOOD, "/var/log 디스크 포화", "CPU 과부하"], start=1):
        r = Run(trial=trial).fs().alarms()
        raws.append(r.raw(_briefing(_hyp(1, cause))))
        calls += r.calls
    rows = score_run(raws, calls, _WORLDS)
    assert report.maj_at_k(rows) == pytest.approx(2 / 3)
    assert report.maj_at_k(rows[:1]) is None  # 반복 1회는 Maj@k 없음


# ─────────────────────────────── 재채점 · 보고서 파일 ───────────────────────────────


def _write_run(run_dir: Path, raws: list[dict[str, Any]], calls: list[dict[str, Any]],
               manifest: dict[str, Any] | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw.jsonl").write_text(judge.dumps_jsonl(raws), encoding="utf-8")
    (run_dir / "world_calls.jsonl").write_text(judge.dumps_jsonl(calls), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(manifest or {
        "run_id": "20261013-120000-closed", "backend": "vllm", "model": "openai/qwen",
        "site": "closed", "commit": "abc1234", "started_at": "2026-10-13T12:00:00"},
        ensure_ascii=False), encoding="utf-8")


def _sample_run(run_dir: Path, *, invalid: int = 0, arms: tuple[str, ...] = ("control",)) -> None:
    raws, calls = [], []
    for arm in arms:
        for trial in (1, 2):
            r = Run(arm=arm, trial=trial).fs().alarms()
            raws.append(r.raw(_briefing(_hyp(1, _GOOD if arm != "control" or trial == 1
                                            else "메모리 누수"))))
            calls += r.calls
    for i in range(invalid):
        raws.append(Run(arm=arms[0], trial=10 + i).raw(None, status="rejected"))
    _write_run(run_dir, raws, calls)


def test_rejudge_idempotent_and_preserves_previous(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _sample_run(run_dir)
    old = '{"judge_version": "j0", "valid": true}\n'
    (run_dir / "scores.jsonl").write_text(old, encoding="utf-8")

    path = rejudge(run_dir, _WORLDS_DIR)
    first = path.read_bytes()
    assert (run_dir / "scores.j0.jsonl").read_text(encoding="utf-8") == old
    assert (run_dir / "report.md").exists()

    rejudge(run_dir, _WORLDS_DIR)
    assert path.read_bytes() == first
    assert (run_dir / f"scores.{JUDGE_VERSION}.jsonl").read_bytes() == first
    # 같은 판의 다른 내용은 덮지 않고 번호를 붙인다
    edited = json.dumps({"judge_version": JUDGE_VERSION, "edited": 1}) + "\n"
    path.write_text(edited, encoding="utf-8")
    rejudge(run_dir, _WORLDS_DIR)
    assert (run_dir / f"scores.{JUDGE_VERSION}.2.jsonl").exists()
    assert path.read_bytes() == first


def test_rejudge_on_bundle_dir(tmp_path: Path) -> None:
    bundle = tmp_path / "20261013-120000-closed.bundle"
    _sample_run(bundle)
    rejudge(bundle, _WORLDS_DIR)
    assert "20261013-120000-closed" in (bundle / "report.md").read_text(encoding="utf-8")


def test_report_deterministic_and_header(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _sample_run(run_dir, arms=("control", "control_b", "exp_a"))
    rejudge(run_dir, _WORLDS_DIR)
    first = (run_dir / "report.md").read_bytes()
    report.write_report(run_dir)
    assert (run_dir / "report.md").read_bytes() == first
    text = first.decode("utf-8")
    assert "## LLM 백엔드: VLLM" in text.splitlines()[2]
    assert "| 무효표 | 0 / 6 (0.0%) |" in text
    assert f"| judge_version | {JUDGE_VERSION} |" in text
    assert "### 홀드아웃 (`holdout`)" in text
    assert "## A/A 잡음폭" in text and "| rc1 | 50.0% |" in text
    # 세계 1개라 1세계분 = 100%p — ΔRC@1 +50%p는 기준 안
    assert "| exp_a | 판정 불가 | 차이가 기준 안 — ΔRC@1 +50.0%p" in text
    # 오답 검토표: 정답 라벨 · 1위 가설 원문 · 걸린 규칙
    assert "bw-j01-app01 · /var/log · disk_full | 메모리 누수 | 1위 대조 — kind 불일치" in text


def test_report_invalid_over_20pct_is_undecidable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _sample_run(run_dir, invalid=2, arms=("control", "exp_a"))
    rejudge(run_dir, _WORLDS_DIR)
    text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "> **판정 불가 — 무효표 2/6 (33.3%) > 20.0%" in text
    assert "| exp_a | 판정 불가 | 무효표 33.3% > 20.0% |" in text


def test_report_gpu_samples(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _sample_run(run_dir)
    (run_dir / "gpu_samples.jsonl").write_text(
        '{"ts": "t1", "gpus": [{"index": 0, "util": 40}, {"index": 1, "util": 80}]}\n'
        '{"ts": "t2", "gpus": [{"index": 0, "util": 60}]}\n', encoding="utf-8")
    rejudge(run_dir, _WORLDS_DIR)
    text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "샘플 2건 · 평균 60.0% · 최대 80.0%" in text


def _drows(arm: str, hits: int, *, worlds: int = 8, complete: bool = True, hallu: int = 0,
           elapsed: float = 10.0) -> list[dict[str, Any]]:
    return [{"arm": arm, "world_id": f"W{i}", "trial": 1, "split": "holdout", "valid": True,
             "rc1": i < hits, "complete": complete, "dist": False,
             "hallu": {"num": hallu, "den": 1, "claims": []}, "cost": {"elapsed_s": elapsed}}
            for i in range(worlds)]


@pytest.mark.parametrize("exp,expected", [
    (_drows("exp", 7), "채택 권고"),                    # +3/8 > 1세계분(1/8)
    (_drows("exp", 5), "판정 불가"),                    # +1/8 = 1세계분 — 기준 안
    (_drows("exp", 4), "기각"),                         # 대조군과 같다
    (_drows("exp", 7, hallu=1), "기각"),                # 가드(HALLU) 악화
    (_drows("exp", 7, elapsed=16.0), "기각"),           # 지연 p90 +50% 초과
    (_drows("exp", 7, complete=False), "판정 불가"),    # 실험군 완주 0건
])
def test_disposition_rules(exp: list[dict[str, Any]], expected: str) -> None:
    scores = _drows("control", 4) + exp
    verdict, _why = report.disposition(scores, "exp", invalid_rate=0.0, noise=None)
    assert verdict == expected
    assert verdict in report.DISPOSITIONS


def test_disposition_uses_aa_noise_when_larger() -> None:
    scores = _drows("control", 4) + _drows("exp", 7)
    verdict, why = report.disposition(scores, "exp", invalid_rate=0.0, noise={"rc1": 0.5})
    assert verdict == "판정 불가" and "잡음폭 50.0%" in why


def test_loop_note_not_duplicated() -> None:
    run = Run()
    for _ in range(4):
        run.call("polestar_alarm_history", {"source": "polestar", "server_name": _APP},
                 {"rows": []})
    row = _score(run, _briefing(_hyp(1, "메모리 누수")),
                 audit=[{"event": "done", "unresolved": ["polestar_alarm_history x4"]}])
    loop_notes = [n for n in row["notes"] if n.startswith("반복·정체")]
    assert loop_notes == ["반복·정체: polestar_alarm_history x4"]
    assert row["failure_code"] == "F-LOOP"


def test_converted_claims_not_backed_by_small_reply_values() -> None:
    # j2: 환산 비교는 응답을 주장 단위로 바꿔 주장 자릿수 폭으로 — 0·1(row_count 등)은 근거가 아니다
    for claim in ("812ms", "700MB", "900KB", "3초"):
        assert not _supported(claim, 0, 1)
    assert _supported("2458MB", 2.4)     # 2.4GB × 1024 = 2457.6
    assert _supported("1536MB", 1.5)


def test_reply_time_and_identifier_digits_not_evidence() -> None:
    nums: list[Any] = []
    collect_numbers({"t": "2026-10-12T09:52:00", "h": "bw-d07-db01", "v": "v1.2.3",
                     "m": "lock wait 312건 · cpu=98.4"}, nums)
    assert sorted(float(n.value) for n in nums) == [98.4, 312.0]


def test_loop_note_groups_same_tool_with_different_args() -> None:
    run = Run()
    for server in (_APP, "bw-j01-db01"):
        for _ in range(3):
            run.call("polestar_alarm_history", {"source": "polestar", "server_name": server},
                     {"rows": []})
    row = _score(run, _briefing(_hyp(1, "메모리 누수")))
    loop_notes = [n for n in row["notes"] if n.startswith("반복·정체")]
    assert loop_notes == ["반복·정체: polestar_alarm_history x3 (인자 2종)"]
    assert row["failure_code"] == "F-LOOP"
