"""plans/198 W7 I-8 — 사건 기억: 결정적 지문 · Jaccard 유사 조사 · KEDB 부트스트랩 문턱 (D-350 ⑤).

- 지문 결정성(같은 입력 → 같은 지문 · 키 순서 무관) · 5요소 토큰 · `n/a` 제외 · 호스트군
- Jaccard 상위 3(점수 · 동점 최신 · 진행 중·자기 자신 제외 · 하한)
- 라벨 없는 결론 비노출 · 판단 불가는 결론 없음 · 목록에 없음은 자유 서술
- KEDB 문턱: N-1회 미생성 · N회 후보 1건 · 다른 원인·다른 지문은 세지 않음
- domain 순수성(표준 라이브러리 + noise_gate.domain만)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from noise_gate.domain import incident_fingerprint as fp

_MODULE = Path(fp.__file__)


def _rec(
    iid: str,
    *,
    kind: str = "alarm",
    playbook: str = "was.latency",
    readings: dict[str, Any] | None = None,
    supported: str = "H-DB-POOL-CHAIN",
    host: str = "web01",
    instance: str = "shop01",
    ts: str = "2026-10-20T01:00:00+00:00",
    status: str = "done",
) -> dict[str, Any]:
    return {
        "investigation_id": iid,
        "kind": kind,
        "status": status,
        "ts": ts,
        "briefing": {
            "summary": "WAS 응답 지연",
            "root_cause_hypotheses": [
                {"rank": 1, "cause": "DB 지연 → 풀 고갈", "status": "supported",
                 "reasoning": supported},
                {"rank": 2, "cause": "GC 과다", "status": "inconclusive", "reasoning": "H-GC"},
            ],
            "playbook": {
                "id": playbook,
                "readings": readings if readings is not None
                else {"dominant": "sql", "S2.pool": "exhausted", "gc": "n/a"},
                # sre_agent RunScope.to_dict 모양 — 서비스군은 대상 인스턴스에서 온다
                "scope": {"alarm_instance": instance, "host": host, "polestar_source": None,
                          "targets": [{"instance": instance, "source_id": "s1"}]},
            },
        },
    }


def _label(verdict: str, rank: int | None = None, note: str = "") -> dict[str, Any]:
    return {"verdict": verdict, "rank": rank, "note": note}


# ── 지문 ─────────────────────────────────────────────────────────────────
def test_fingerprint_deterministic_and_tokens() -> None:
    a = _rec("i1")
    b = _rec("i2", readings={"S2.pool": "exhausted", "gc": "n/a", "dominant": "sql"}, host="web02")
    assert fp.fingerprint(a) == fp.fingerprint(_rec("i1"))
    assert fp.fingerprint(a) == fp.fingerprint(b)  # 키 순서·호스트 번호 무관
    assert fp.fingerprint(a) == frozenset({
        "kind:alarm", "pb:was.latency", "rd:dominant=sql", "rd:S2.pool=exhausted",
        "hyp:H-DB-POOL-CHAIN", "hg:web", "svc:shop",
    })
    assert fp.fingerprint_id(fp.fingerprint(a)) == fp.fingerprint_id(fp.fingerprint(b))
    assert len(fp.fingerprint_id(fp.fingerprint(a))) == 16


def test_fingerprint_skips_unsupported_and_holmes_shape() -> None:
    holmes = {"investigation_id": "h", "kind": "manual", "status": "done",
              "briefing": {"root_cause_hypotheses": [{"rank": 1, "cause": "x"}]}}
    assert fp.fingerprint(holmes) == frozenset({"kind:manual"})
    assert fp.fingerprint({}) == frozenset({"kind:alarm"})
    assert fp.host_group("WEB-01") == "web" and fp.host_group("1234") == "1234"


def test_fingerprint_service_from_alarm_instance_when_no_targets() -> None:
    rec = _rec("j")
    rec["briefing"]["playbook"]["scope"] = {"alarm_instance": "pay-was02", "host": None}
    tokens = fp.fingerprint(rec)
    assert "svc:pay-was" in tokens and not any(t.startswith("hg:") for t in tokens)


def test_similar_skips_kind_only_fingerprints() -> None:
    """(plans/198 교정) 종류 토큰뿐인 지문은 비교하지 않고, 종류 외 공통 토큰이 있어야 후보다."""
    holmes = {"investigation_id": "h", "kind": "alarm", "status": "done",
              "briefing": {"root_cause_hypotheses": [{"rank": 1, "cause": "x"}]}}
    other = {**holmes, "investigation_id": "h2"}
    assert fp.similar_investigations(holmes, [holmes, other, _rec("p")], {}) == []
    assert fp.similar_investigations(_rec("t"), [holmes, other], {}) == []


def test_jaccard() -> None:
    assert fp.jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == 1 / 3
    assert fp.jaccard(frozenset(), frozenset()) == 0.0


# ── 유사 조사 상위 3 ─────────────────────────────────────────────────────
def test_similar_top3_order_and_filters() -> None:
    target = _rec("t")
    records = [
        target,
        _rec("same-old", ts="2026-10-01T00:00:00+00:00"),
        _rec("same-new", ts="2026-10-10T00:00:00+00:00"),
        _rec("diff-host", host="db01"),
        _rec("diff-host-svc", host="db01", instance="pay01"),
        _rec("running", status="running"),
        _rec("unrelated", kind="manual", playbook="os.cpu", readings={}, supported="H-X",
             host="x", instance="y"),
    ]
    out = fp.similar_investigations(target, records, {})
    assert [i["investigation_id"] for i in out] == ["same-new", "same-old", "diff-host"]
    assert out[0]["score"] == 1.0 and out[2]["score"] < 1.0
    allx = fp.similar_investigations(target, records, {}, limit=None)
    ids = [i["investigation_id"] for i in allx]
    assert "t" not in ids and "running" not in ids and "unrelated" not in ids
    assert ids[-1] == "diff-host-svc"


# ── 라벨 없는 결론 비노출 ────────────────────────────────────────────────
def test_conclusion_only_when_labeled() -> None:
    target = _rec("t")
    records = [_rec("a", ts="2026-10-04T00:00:00+00:00"), _rec("b", ts="2026-10-03T00:00:00+00:00"),
               _rec("c", ts="2026-10-02T00:00:00+00:00"), _rec("d", ts="2026-10-01T00:00:00+00:00")]
    labels = {
        "a": _label("top1", 1),
        "b": _label("undeterminable"),
        "d": _label("not_listed", note="디스크 풀"),
    }
    by = {i["investigation_id"]: i for i in
          fp.similar_investigations(target, records, labels, limit=None)}
    assert by["a"]["labeled"] is True and by["a"]["conclusion"] == "DB 지연 → 풀 고갈"
    assert by["a"]["verdict"] == "top1" and by["a"]["rank"] == 1
    assert by["b"]["labeled"] is True and by["b"]["conclusion"] is None
    assert by["c"] == {"investigation_id": "c", "kind": "alarm", "ts": "2026-10-02T00:00:00+00:00",
                       "score": 1.0, "labeled": False, "verdict": None, "rank": None,
                       "conclusion": None}
    assert by["d"]["conclusion"] == "디스크 풀"
    assert fp.confirmed_cause(records[0], _label("ranked", 2)) == "GC 과다"
    assert fp.confirmed_cause(records[0], _label("ranked", 5)) is None
    assert fp.confirmed_cause(records[0], _label("not_listed")) is None


# ── KEDB 부트스트랩 문턱 ─────────────────────────────────────────────────
def test_kedb_threshold() -> None:
    recs = [_rec(f"i{n}") for n in range(3)]
    labels = {"i0": _label("top1", 1), "i1": _label("top1", 1)}
    assert fp.kedb_candidate(recs[1], recs, labels, min_count=3) is None  # N-1회
    labels["i2"] = _label("top1", 1)
    cand = fp.kedb_candidate(recs[2], recs, labels, min_count=3)
    assert cand is not None
    assert cand["count"] == 3 and cand["investigation_ids"] == ["i0", "i1", "i2"]
    assert cand["cause"] == "DB 지연 → 풀 고갈" and cand["cause_key"] == "hyp:H-DB-POOL-CHAIN"
    # 같은 묶음이면 어느 조사에서 봐도 같은 키(호출부 중복 방지 키)
    again = fp.kedb_candidate(recs[0], recs, labels, min_count=3)
    assert again is not None and again["key"] == cand["key"]


def test_kedb_counts_same_fingerprint_and_cause_only() -> None:
    recs = [_rec("i0"), _rec("i1"), _rec("other-fp", instance="pay01"), _rec("i3")]
    labels = {
        "i0": _label("top1", 1),
        "i1": _label("ranked", 2),          # 다른 확정 원인
        "other-fp": _label("top1", 1),      # 다른 지문
        "i3": _label("top1", 1),
    }
    assert fp.kedb_candidate(recs[0], recs, labels, min_count=3) is None
    assert fp.kedb_candidate(recs[0], recs, labels, min_count=2)["count"] == 2  # type: ignore[index]
    assert fp.kedb_candidate(recs[0], recs, {"i0": _label("undeterminable")}, min_count=1) is None
    assert fp.kedb_candidate(recs[0], recs, {}, min_count=1) is None
    notes = {f"i{n}": _label("not_listed", note=" 디스크  풀 ") for n in (0, 1, 3)}
    cand = fp.kedb_candidate(recs[0], recs, notes, min_count=3)
    assert cand is not None and cand["cause_key"] == "note:디스크 풀"


# ── domain 순수성 ────────────────────────────────────────────────────────
def test_domain_imports_stdlib_only() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    allowed_stdlib = {"__future__", "hashlib", "re", "collections.abc", "typing"}
    assert {m for m in mods if not m.startswith("noise_gate.domain")} <= allowed_stdlib
