"""plans/198 재검토(sre_agent) 교정 — 이웃 배치 격리·선행 기준점·원장 색인·미연결 소스·
판별 기록·부분 브리핑.

원장 가속(Minor4)은 **판정 불변**을 종전 구현(정규식 탐색·제곱 치환)과 같은 입력으로
맞대어 확인한다.
"""

from __future__ import annotations

import json
import random
import re
import threading
from types import SimpleNamespace

import pytest
from sre_agent.application import playbook_runner
from sre_agent.application.briefing_builder import build_partial_briefing
from sre_agent.application.evidence_prefetch import (
    TOOL_TOPOLOGY,
    EvidenceScope,
    prefetch_and_correlate,
)
from sre_agent.domain import value_guard
from sre_agent.domain.correlation import AlarmPoint, correlate, leads
from sre_agent.domain.evidence_ledger import (
    boundary_names,
    build_ledger,
    check_claim,
    host_tokens,
    normalize_host,
)
from sre_agent.domain.failure_guards import ExpectedSources, unchecked_sources
from test_cross_source_investigation import _ctx, _verdict
from test_plan198_w3_discriminator import S7, _go, _lib, _playbook

REF = "2026-10-19T07:20:00"


def _scope(**kw) -> EvidenceScope:
    base = dict(source="polestar_cm_gp", server_name="web01", reference_time=REF,
                lookback_minutes=60, baseline_periods=24, neighbors_max=5)
    base.update(kw)
    return EvidenceScope(**base)


def _first_batch(calls):
    out = []
    for tool, args in calls:
        if tool == TOOL_TOPOLOGY:
            out.append(json.dumps({"rows": [{"resource_name": "db01", "hop": 1}]}))
        elif tool == "polestar_incident_alarms" and args["server_name"] == "web01":
            out.append(json.dumps({"rows": [
                {"alarm_time": "2026-10-19 07:20:00", "severity": 3, "alarm_name": "cpu"}]}))
        else:
            out.append(json.dumps({"rows": []}))
    return out


# ── Major1 — 이웃 배치 실패·지연이 대표 상관을 잃게 하지 않는다 ─────────────


@pytest.mark.parametrize("exc", [TimeoutError("hung"), RuntimeError("session dead")])
def test_neighbor_batch_exception_keeps_representative(exc):
    state = {"n": 0}

    def _call(calls):
        state["n"] += 1
        if state["n"] == 2:
            raise exc
        return _first_batch(calls)

    r = prefetch_and_correlate(_scope(), _call)
    assert r.alarm_summary["count"] == 1   # 대표 알람 보존
    host = r.neighbors["hosts"][0]
    assert host["name"] == "db01" and host["leads"] is None and host["alarm_count"] is None
    assert any("이웃 db01 알람 결손" in n and type(exc).__name__ in n for n in r.notes)


def test_neighbor_batch_bounded_by_budget():
    release = threading.Event()
    state = {"n": 0}

    def _call(calls):
        state["n"] += 1
        if state["n"] == 2:
            release.wait(5)   # 매달린 이웃 배치
            return [json.dumps({"rows": []})] * len(calls)
        return _first_batch(calls)

    try:
        r = prefetch_and_correlate(_scope(), _call, neighbor_budget_s=0.2)
    finally:
        release.set()
    assert r.alarm_summary["count"] == 1
    assert r.neighbors["hosts"][0]["error"].startswith("TimeoutError")


def test_neighbor_batch_skipped_when_budget_spent():
    state = {"n": 0}

    def _call(calls):
        state["n"] += 1
        return _first_batch(calls)

    r = prefetch_and_correlate(_scope(), _call, neighbor_budget_s=0)
    assert state["n"] == 1   # 이웃 배치를 부르지 않았다
    nb = r.neighbors
    assert nb["tool_calls"] == 1 and nb["hosts"][0]["leads"] is None


# ── Major2 — 대표 0건일 때 선행 기준점 일치 ─────────────────────────────────


def test_leads_anchor_default_keeps_legacy_rule():
    assert leads(10, None) is True              # 종전(연관 서버 · 이웃 off)
    assert leads(10, None, 0) is False          # 이웃 블록과 같은 기준점
    assert leads(-5, None, 0) is True
    assert leads(10, 20, 0) is True             # 대표 알람이 있으면 anchor 무시


def test_correlate_without_anchor_is_legacy():
    pts = {"db01": [AlarmPoint("2026-10-19T07:30:00", 3, "cpu")]}
    legacy = correlate(REF, [], [], related_alarms=pts)
    assert any("db01" in n and "대표보다 선행" in n for n in legacy.notes)
    anchored = correlate(REF, [], [], related_alarms=pts, lead_anchor=0)
    assert not any("db01" in n and "대표보다 선행" in n for n in anchored.notes)


def test_leading_neighbor_note_and_block_agree_when_target_has_no_alarm():
    def _call(calls):
        out = []
        for tool, args in calls:
            if tool == TOOL_TOPOLOGY:
                out.append(json.dumps({"rows": [{"resource_name": "db01", "hop": 1}]}))
            elif args.get("server_name") == "db01":
                out.append(json.dumps({"rows": [
                    {"alarm_time": "2026-10-19 07:15:00", "severity": 3, "alarm_name": "x"}]}))
            else:
                out.append(json.dumps({"rows": []}))
        return out

    r = prefetch_and_correlate(_scope(), _call)
    assert r.neighbors["hosts"][0]["leads"] is True
    assert any("db01" in n and "대표보다 선행" in n for n in r.notes)


# ── Minor3 — 마크다운 제목·굵게 번호 ───────────────────────────────────────


def _ledger():
    rec = SimpleNamespace(tool_name="polestar_metric_trend", output='{"cpu": 95.2}',
                          params={"server_name": "web01"}, description="")
    return build_ledger([rec])


@pytest.mark.parametrize("line", ["### 4. web01 CPU 95.2%", "**3)** web01 CPU 95.2%",
                                  "## 2) web01 CPU 95.2%"])
def test_heading_numbers_stripped(line):
    c = check_claim(line, _ledger())
    assert c.unmatched == () and c.checked == 2


@pytest.mark.parametrize("line", ["95.2% CPU", "7.5 web01", "### 7.5 이상"])
def test_leading_numbers_are_not_bullets(line):
    assert check_claim(line, _ledger()).checked >= 1


# ── Minor4 — 원장 색인은 종전 판정과 같다 ─────────────────────────────────


def _regex_known(name: str, text: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text))


_ALPHABET = "ab9-.x_ :/한0"


def _random_text(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(n))


def test_boundary_names_matches_regex_search():
    rng = random.Random(198)
    for _ in range(300):
        text = _random_text(rng, rng.randint(0, 40))
        idx = boundary_names(text)
        names = {normalize_host(t) for t in host_tokens(text + " ab9-x ab9 x9a.b9")}
        names |= {t.lower() for t in host_tokens(text)}
        names |= {"ab9", "ab9-x", "ab9.x", "x9", "ab9-", "b9"}
        for name in names:
            if name and name[0].isalpha():
                assert (name in idx) == _regex_known(name, text), (name, text)


def _source_numbers_legacy(text: str) -> list:
    """종전 구현(일치마다 문자열 재구성 · lookbehind 없는 표지 정규식) — 결과 대조용."""
    labeled = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\"?\s*[:=]?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)(?![\d.])"
    )
    raw = text or ""
    out: list = []
    blanked = raw
    for m in value_guard._DATE.finditer(raw):
        out.append(m.group())
        blanked = blanked[: m.start()] + " " * len(m.group()) + blanked[m.end():]
    for m in value_guard._TIME.finditer(blanked):
        out.append(value_guard._norm_time(m.group(1), m.group(2)))
        blanked = blanked[: m.start()] + " " * len(m.group()) + blanked[m.end():]
    out.extend(float(m.group().replace(",", "")) for m in value_guard._NUMBER.finditer(blanked))
    for m in labeled.finditer(blanked):
        factors = value_guard._unit_factors([m.group(1)])
        if factors:
            out.append(value_guard.SourceNumber(float(m.group(2).replace(",", "")), factors))
    decoder = json.JSONDecoder()
    pos = raw.find("{")
    while pos != -1:
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            pos = raw.find("{", pos + 1)
            continue
        value_guard._walk_json(obj, (), out)
        pos = raw.find("{", end)
    return out


def test_source_numbers_same_as_legacy():
    rng = random.Random(7)
    pieces = ["latency_ms", "a1b_s", "share", " ", ":", "=", "\"", "12", "3.5", "1,234", "-",
              "2026-10-19", "07:20", "7:05:09", "x", "_", "9", "ratio", "{\"p_pct\": 40}", "."]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        assert value_guard.source_numbers(text) == _source_numbers_legacy(text), text


def _check_legacy(line: str, ledger) -> tuple:
    """종전 `check_claim`(원문 정규식 탐색 · 전체 허용 집합) — 결과 대조용."""
    from sre_agent.domain import evidence_ledger as el

    texts = [r._raw for r in ledger.rows]
    allowed = [n for r in ledger.rows for n in r.numbers]

    def known(token):
        names = {token.lower(), normalize_host(token)}
        for row, raw in zip(ledger.rows, texts):
            if names & row.identifiers:
                return True
            if any(n and _regex_known(n, raw) for n in names):
                return True
        return False

    text = el._BULLET.sub("", line)
    unmatched, checked, masked = [], 0, text
    stems = ledger.host_stems()
    for token in host_tokens(text):
        if known(token):
            checked += 1
        elif el.host_stem(token) in stems:
            checked += 1
            unmatched.append(token)
        masked = re.sub(rf"(?<![A-Za-z0-9_.\-]){re.escape(token)}(?![A-Za-z0-9_\-])",
                        " " * len(token), masked)
    for token, ok in value_guard.claim_number_tokens(masked, allowed):
        checked += 1
        if not ok:
            unmatched.append(token)
    return checked, tuple(dict.fromkeys(unmatched))


def test_check_claim_same_as_legacy():
    rng = random.Random(42)
    recs = []
    for k in range(6):
        rows = [{"server_name": f"web{(i + k) % 9:02d}.corp",
                 "avg_val": round(rng.random() * 100, 2),
                 "latency_ms": rng.randint(1, 5000), "t": f"2026-10-19 07:{i % 60:02d}:00",
                 "share": round(rng.random(), 3), "node": f"db-node-{i * 7 + k}"}
                for i in range(40)]
        recs.append(SimpleNamespace(tool_name=f"t{k}", output=json.dumps({"rows": rows}),
                                    params={"server_name": f"web{k:02d}"}, description=""))
    ledger = build_ledger(recs, extra=[("alarm.payload", {"event": {"serverName": "was07"}})])
    for row, rec in zip(ledger.rows, [*recs, None]):
        body = (rec.output if rec else json.dumps({"event": {"serverName": "was07"}},
                                                   ensure_ascii=False))
        args = row.args
        object.__setattr__(row, "_raw", (body + "\n" + (
            json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
            if isinstance(args, dict) else str(args or ""))).lower())
    for k in range(150):
        nums = [f"{rng.random() * 100:.{rng.randint(0, 3)}f}" for _ in range(3)]
        line = (f"{rng.choice(['- ', '1. ', '### 2. ', '**3)** ', ''])}web{k % 12:02d} "
                f"CPU {nums[0]}% · {rng.randint(1, 5000)}ms · {nums[1]}초 · 07:{k % 60:02d} · "
                f"db-node-{k} · was07 · ratio {nums[2]} · G1GC eth0 · 2026-10-19 · {k}건")
        got = check_claim(line, ledger)
        assert (got.checked, got.unmatched) == _check_legacy(line, ledger), line


# ── Minor5 — 미연결 소스는 「확인하지 않은 소스」가 아니다 ─────────────────────


_SPEC = ExpectedSources(sources={"polestar": ("polestar_",), "prometheus": ("prom_",)},
                        kinds={"host": ("polestar", "prometheus")})


def test_unchecked_excludes_unconnected_sources():
    assert unchecked_sources("host", [], _SPEC) == ["polestar", "prometheus"]   # 종전
    loaded = ["polestar_incident_alarms", "todo_write"]
    assert unchecked_sources("host", [], _SPEC, loaded=loaded) == ["polestar"]
    assert unchecked_sources("host", ["polestar_metric_trend"], _SPEC, loaded=loaded) == []


# ── Minor6 — 판별 스텝 실행 기록 ────────────────────────────────────────────


def test_discriminator_context_carries_step_id(monkeypatch):
    seen: list = []
    orig = playbook_runner._exec_step

    def spy(spec, ctx, library, clock):
        seen.append((spec.id, ctx.step_id))
        return orig(spec, ctx, library, clock)

    monkeypatch.setattr(playbook_runner, "_exec_step", spy)
    from sre_agent.application.playbook_steps import StepResult

    _go(_playbook(S7), _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded")),
        discriminate_top_n=2)
    assert ("S1", "S1") in seen   # 판별 스텝 S1 — 래퍼가 이 ID로 기록한다


def test_discriminator_budget_skip_notified():
    from sre_agent.application.playbook_steps import StepResult

    t = {"now": 0.0}

    def s7(ctx):
        t["now"] += 500.0
        return StepResult(status="ok", data={"candidates": [
            {"source": "deploy", "minutes_before_alarm": 10}]})

    skipped: list = []
    _go(_playbook(S7), _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded"), s7=s7),
        discriminate_top_n=2, clock=lambda: t["now"], budget_s=240.0,
        on_skip=lambda res, because: skipped.append((res.step, res.status)))
    assert ("S1", "skipped") in skipped


# ── Minor7 — 부분 브리핑의 크로스소스 블록 ──────────────────────────────────


def test_partial_briefing_default_unchanged_and_cross_source_added():
    ledger = build_ledger([SimpleNamespace(tool_name="apm_slow_tx", output="{}", params={},
                                           description="")])
    base = build_partial_briefing(reason="stalled", verdict=_verdict(), ledger=ledger)
    assert "cross_source" not in base
    b = build_partial_briefing(reason="stalled", verdict=_verdict(), ledger=ledger,
                               cross_source=_ctx(), apm_tools_connected=True, source_labels=True)
    assert b["cross_source"]["apm_tools_called"] == 1
    assert b["timeline"][-1].startswith("[애플리케이션")
    assert len(b["limitations"]) > len(base["limitations"])
