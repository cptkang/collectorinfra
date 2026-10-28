"""plans/198 W1 (I-4 · 154 H-4) — 증거 원장 · 값 대조 인용 검증 · 공용 환각 수치 가드.

실 LLM 0 — 도구 출력·서술은 픽스처다.
"""

from __future__ import annotations

import json

import pytest
from sre_agent.application import playbook_narrator
from sre_agent.application.briefing_builder import (
    HYPOTHESIS_PREFIX,
    build_briefing,
)
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, tools_unavailable_result
from sre_agent.domain import value_guard
from sre_agent.domain.evidence_ledger import (
    SUMMARY_MAX_ARGS_BYTES,
    SUMMARY_MAX_ROWS,
    build_ledger,
    check_claim,
    host_tokens,
    ledger_summary,
    normalize_host,
)
from sre_agent.domain.severity_signatures import judge
from sre_agent.settings import AgentSettings

OUT_CPU = json.dumps({
    "hostname": "web-01.corp.local",
    "metric": "cpu_usage",
    "series": [{"time": "2026-10-19T07:20:00", "value": 97.34}],
    "peak": 97.34,
    "avg_ratio": 0.7342,
})
OUT_APM = "p95_ms 6100 · error_ratio 0.042 · rows 250"


def _rec(tool: str, output: str, params: dict | None = None, status: str = "success"):
    return ToolCallRecord(tool_name=tool, description=f"{tool} 호출", status=status,
                          output=output, params=params)


RECORDS = [
    _rec("prom_metric_range", OUT_CPU, {"hostname": "web-01", "minutes": 60}),
    _rec("apm_service_latency", OUT_APM, {"service": "order-api"}),
]
TOOLS = [r.tool_name for r in RECORDS]


def _ledger():
    return build_ledger(RECORDS)


def _verdict():
    return judge(gate_severity=2, tool_outputs=[OUT_CPU])


# ── 추출 결정성 ──────────────────────────────────────────────────────


def test_ledger_extraction_is_deterministic_and_rows_follow_calls():
    a, b = build_ledger(RECORDS, times=[1.0]), build_ledger(RECORDS, times=[1.0])
    assert a == b
    assert [(r.ev_id, r.tool) for r in a.rows] == [
        ("ev1", "prom_metric_range"), ("ev2", "apm_service_latency")]
    assert a.rows[0].args == {"hostname": "web-01", "minutes": 60}
    assert (a.rows[0].at, a.rows[1].at) == (1.0, None)
    assert {"web-01.corp.local", "web-01"} <= a.rows[0].identifiers
    assert 97.34 in a.rows[0].numbers and "2026-10-19" in a.rows[0].numbers
    # params가 없으면 holmes 호출 요약(description)이 인자 자리에 온다.
    assert build_ledger([_rec("bash", "ok")]).rows[0].args == "bash 호출"


def test_extra_rows_follow_tool_rows():
    led = build_ledger(RECORDS, extra=[("alarm.payload", {"event": {"value": 91.5}})])
    assert [r.ev_id for r in led.rows] == ["ev1", "ev2", "ev3"]
    assert led.rows[2].tool == "alarm.payload" and 91.5 in led.rows[2].numbers


@pytest.mark.parametrize("text, expected", [
    ("web-01 서버와 db02.corp 확인", ["web-01", "db02.corp"]),
    ("p95 S3 ev12 v1.2.3 T-15m 은 호스트가 아니다", []),
    ("prom_metric_range · node_load1 은 밑줄 식별자", []),
    ("CPU 사용률 · 메모리 고갈", []),
])
def test_host_tokens(text, expected):
    assert host_tokens(text) == expected


def test_normalize_host_minimal_copy():
    assert normalize_host("WEB-01.corp.local.") == "web-01"
    assert normalize_host("10.0.0.15") == "10.0.0.15"


# ── 값 대조: 정확 일치 · 반올림 · 단위 · 별칭 ─────────────────────────


@pytest.mark.parametrize("line", [
    "web-01 CPU 사용률이 07:20에 97.34%까지 올랐다",          # 정확 일치 · 시각
    "web-01 CPU 최고 97.3%",                                  # 1자리 반올림
    "평균 사용률은 73.4%",                                    # 비율 → % 단위 환산 + 반올림
    "응답 p95가 6.1초로 지연됐다",                             # ms → 초
    "오류율 4.2%",                                            # 비율 → %
    "WEB-01.corp.local 에서 확인",                            # FQDN·대소문자 별칭
    "2026-10-19 07시 20분 이후 3건의 알람",                    # 날짜·한국어 시각·개수 접미
    "1. web-01 메모리 고갈 확인",                              # 목록 번호는 수치가 아니다
])
def test_matching_claims(line):
    chk = check_claim(line, _ledger())
    assert chk.unmatched == () and chk.checked >= 1 and chk.verified


@pytest.mark.parametrize("line, bad", [
    ("web-01 CPU가 99.9%였다", ("99.9",)),
    ("web-07 서버도 영향을 받았다", ("web-07",)),
    ("08:45에 급증했다", ("08", "45")),
    ("web-01 응답 12.5초", ("12.5",)),
])
def test_unmatched_claims(line, bad):
    assert check_claim(line, _ledger()).unmatched == bad


def test_host_alias_short_name_found_in_ledger_text():
    led = build_ledger([_rec("bash", "node=app-server-03.prod.internal up")])
    assert check_claim("app-server-03 정상 응답", led).verified


def test_claim_without_values_is_not_checked():
    chk = check_claim("디스크 IO가 원인으로 보인다", _ledger())
    assert chk.checked == 0 and not chk.verified


# ── 브리핑: 강등 · 한계 · 가산 키 ──────────────────────────────────────

HALLUCINATED = (
    "web-01 CPU 사용률이 07:20에 97.3%까지 올랐다 ← prom_metric_range\n"
    "web-01 CPU가 99.9%로 포화돼 장애가 났다 ← prom_metric_range"
)


def test_unmatched_claim_is_demoted_with_limitation():
    b = build_briefing(answer=HALLUCINATED, verdict=_verdict(), tool_names=TOOLS,
                       evidence_ledger=_ledger())
    assert b["citations_verified"] is True
    assert "web-01 CPU가 99.9%로 포화돼 장애가 났다 ← prom_metric_range" in b["hypotheses"]
    assert b["summary"].startswith("web-01 CPU 사용률이 07:20")
    # 마커가 있어도 원장에 없는 값이 있으면 원인으로 쓰지 않는다.
    assert b["cause"] == "web-01 CPU 사용률이 07:20에 97.3%까지 올랐다 ← prom_metric_range"
    assert "원장에 없는 값: 99.9" in b["limitations"]
    assert b["citation_check"] == {"checked": 5, "unmatched": ["99.9"], "demoted": 1,
                                   "ledger_size": 2}
    assert [r["ev_id"] for r in b["evidence_ledger"]] == ["ev1", "ev2"]
    assert set(b["evidence_ledger"][0]) == {"ev_id", "tool", "args"}


def test_all_unmatched_falls_back_to_hypothesis_and_unverified():
    b = build_briefing(answer="web-09 CPU 88.8% 포화 ← prom_metric_range", verdict=_verdict(),
                       tool_names=TOOLS, evidence_ledger=_ledger())
    assert b["citations_verified"] is False
    assert b["summary"].startswith(HYPOTHESIS_PREFIX)
    assert b["cause"].startswith(HYPOTHESIS_PREFIX)
    assert "원장에 없는 값: web-09, 88.8" in b["limitations"]


def test_marker_only_prose_is_not_value_verified():
    b = build_briefing(answer="디스크 IO가 원인으로 보인다 ← prom_metric_range",
                       verdict=_verdict(), tool_names=TOOLS, evidence_ledger=_ledger())
    # 값 없는 단정은 마커 판정으로 인용에 남지만 검증 여부는 대조 결과로만 정한다.
    assert b["cause"] == "디스크 IO가 원인으로 보인다 ← prom_metric_range"
    assert b["citations_verified"] is False
    assert b["citation_check"]["checked"] == 0


def test_unmatched_limitation_is_capped():
    answer = " ".join(f"{i}.{i}%" for i in range(11, 26))  # 15개 모두 원장에 없다
    b = build_briefing(answer="web-01 " + answer, verdict=_verdict(), tool_names=TOOLS,
                       evidence_ledger=_ledger())
    line = next(x for x in b["limitations"] if x.startswith("원장에 없는 값: "))
    assert line.endswith(" 외 5개") and line.count(",") == 9
    assert len(b["citation_check"]["unmatched"]) == 15


def test_hypothesis_ranking_unchanged_llm_item_last():
    corr = {
        "metric_findings": {"cpu": {"is_anomalous": True, "kind": "sustained",
                                    "onset_offset_min": -20, "lead_lag_min": -10,
                                    "peak_value": 97.34, "z_score": 4.1}},
        "leading_signal": "cpu",
        "alarm_summary": {"first_offset_min": -10},
        "timeline": [{"t_offset_min": -20, "kind": "metric", "detail": "cpu 급등"}],
    }
    off = build_briefing(answer=HALLUCINATED, verdict=_verdict(), tool_names=TOOLS,
                         correlation=corr)
    on = build_briefing(answer=HALLUCINATED, verdict=_verdict(), tool_names=TOOLS,
                        correlation=corr, evidence_ledger=_ledger())
    assert [h["rank"] for h in on["root_cause_hypotheses"]] == [1, 2]
    assert on["root_cause_hypotheses"][0] == off["root_cause_hypotheses"][0]
    assert on["cause"] == off["cause"]


def test_no_tool_outputs_nothing_cited_even_with_ledger():
    b = build_briefing(answer="web-01 CPU 97.34%", verdict=_verdict(), tool_names=[],
                       evidence_ledger=build_ledger([], extra=[("alarm.payload", OUT_CPU)]))
    assert b["citations_verified"] is False and b["summary"].startswith(HYPOTHESIS_PREFIX)


# ── 오탐률: 원장 값만 쓴 정상 서술은 강등되지 않는다 ──────────────────

NORMAL_NARRATIVES = [
    "web-01 CPU 사용률이 07:20에 97.3%까지 올랐다 ← prom_metric_range\n"
    "평균 사용률은 73.4%로 높았다 ← prom_metric_range",
    "## 조사 결과\n- web-01.corp.local 최고 97.34% (2026-10-19 07:20)\n- 근거: prom_metric_range",
    "order-api 응답 p95가 6.1초, 오류율 4.2%였다 ← apm_service_latency\n"
    "조회 250건 중 오류 비중이 높았다",
    "1. WEB-01 CPU 97.3% ← prom_metric_range\n2. 07시 20분 이후 지속\n3. 3건 연관 알람 없음",
    "web-01에서 60분 구간을 조회했다 ← prom_metric_range\nCPU가 97.3%로 포화 상태다",
]


@pytest.mark.parametrize("answer", NORMAL_NARRATIVES)
def test_normal_narrative_not_demoted(answer):
    b = build_briefing(answer=answer, verdict=_verdict(), tool_names=TOOLS,
                       evidence_ledger=_ledger())
    assert b["citation_check"]["unmatched"] == [] and b["citation_check"]["demoted"] == 0
    assert b["citations_verified"] is True
    assert not any(x.startswith("원장에 없는 값") for x in b["limitations"])
    # 값 대조가 마커 판정보다 더 강등하지 않는다(값 없는 머리말 줄은 종전과 같이 판정).
    off = build_briefing(answer=answer, verdict=_verdict(), tool_names=TOOLS)
    assert b["summary"] == off["summary"]
    assert set(b["hypotheses"]) <= set(off["hypotheses"])


# ── 크기 상한 ────────────────────────────────────────────────────────


def test_ledger_summary_caps_rows_and_args_bytes():
    recs = [_rec(f"t{i}", "1", {"q": "가" * 500, "i": i}) for i in range(100)]
    summ = ledger_summary(build_ledger(recs), masker=lambda s: s)
    assert len(summ) == SUMMARY_MAX_ROWS
    assert all(len(r["args"].encode()) <= SUMMARY_MAX_ARGS_BYTES for r in summ)
    b = build_briefing(answer="web-01 99.9%", verdict=_verdict(), tool_names=["t0"],
                       evidence_ledger=build_ledger(recs))
    added = {k: b[k] for k in ("citation_check", "evidence_ledger")}
    assert len(json.dumps(added, ensure_ascii=False).encode()) < 8 * 1024


# ── 공용 가드: 소견기 동작 비트 동일 ──────────────────────────────────


def test_narrator_reexports_shared_guard():
    for name in ("source_numbers", "unknown_numbers", "number_allowed", "SourceNumber"):
        assert getattr(playbook_narrator, name) is getattr(value_guard, name)


@pytest.mark.parametrize("sentence", [
    "p95 1234.5ms, 오류율 42%", "풀 사용률 73%다.", "SQL 6.1초다.", "08:20 변경이 있었다.",
    "20건 실패했다.", "3건 실패", "2026-10-19 07시 20분", "12,345건 · -0.42",
])
def test_claim_number_tokens_agree_with_unknown_numbers(sentence):
    allowed = value_guard.source_numbers(
        '{"columns":["name","p95_ms","ratio"],"rows":[["a",6100,0.73]]} '
        "p95_ms 1234.5 · error_ratio 0.42 · 2026-10-19T07:20:00 count 12345"
    )
    toks = value_guard.claim_number_tokens(sentence, allowed)
    assert [t for t, ok in toks if not ok] == value_guard.unknown_numbers(sentence, allowed)


def test_guard_text_unchanged_on_hallucinated_number():
    op = playbook_narrator.guard_text(
        "CPU 97.3%로 높다. 응답 99.9초로 지연됐다.", verdict="degraded",
        allowed=value_guard.source_numbers("cpu 97.34"), max_sentences=4, max_chars=420,
    )
    assert op.text == "CPU 97.3%로 높다." and op.guard == ["환각 수치 문장 제거(99.9)"]


# ── 디스패처 배선 · 플래그 off 비트 동일 ───────────────────────────────


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "openai/test-model")
    kw.setdefault("api_key", "x")
    kw.setdefault("gemini_api_key", "k")
    kw.setdefault("service_bearer_token", None)
    kw.setdefault("evidence_correlation_enabled", False)
    kw.setdefault("apm_mcp_url", "")
    kw.setdefault("apm_guidance_enabled", False)
    kw.setdefault("polestar_mcp_token", None)
    kw.setdefault("investigation_dedup_ttl_seconds", None)
    kw.setdefault("citation_value_check_enabled", False)
    return AgentSettings(**kw)


def _job(name: str) -> InvestigationJob:
    return InvestigationJob(
        investigation_id=f"id-{name}", kind="alarm", status="running", created_at=0.0,
        updated_at=0.0, fingerprint=name,
        payload={"event": {"serverName": "web-01", "severity": 4, "value": 91.5},
                 "decision": {"tier": "PAGE"}},
    )


def _run(tmp_path, diagnose_fn, *, spy: list | None = None, **kw) -> InvestigationJob:
    def briefing_fn(**kwargs):
        if spy is not None:
            spy.append(kwargs)
        return build_briefing(**kwargs)

    disp = InvestigationDispatcher(_settings(**kw), diagnose_fn=diagnose_fn,
                                   briefing_fn=briefing_fn, audit_path=tmp_path / "a.jsonl")
    job = _job(tmp_path.name)
    disp(job)
    disp.wait_workers()
    return job


def _result() -> DiagnosisResult:
    return DiagnosisResult(
        answer=HALLUCINATED + "\n알람 값 91.5를 확인했다",
        tool_outputs=[*RECORDS, _rec("prom_metric_range", "boom 55.5", status="error")],
    )


def test_flag_off_passes_no_ledger_and_briefing_identical(tmp_path):
    spy: list[dict] = []
    job = _run(tmp_path, lambda job: _result(), spy=spy)
    assert job.status == "done"
    (kwargs,) = spy
    assert "evidence_ledger" not in kwargs and "ledger_masker" not in kwargs
    assert job.briefing == build_briefing(**kwargs)
    assert "citation_check" not in job.briefing and "evidence_ledger" not in job.briefing


def test_flag_on_builds_ledger_from_citable_calls_and_payload(tmp_path):
    job = _run(tmp_path, lambda job: _result(), citation_value_check_enabled=True)
    b = job.briefing
    # 실패 호출(error)은 원장 행이 아니다 — 그 출력의 55.5는 근거가 되지 않는다.
    assert [r["tool"] for r in b["evidence_ledger"]] == [
        "prom_metric_range", "apm_service_latency", "alarm.payload"]
    assert b["citation_check"]["ledger_size"] == 3
    # 알람 페이로드 값(91.5)은 원장에 있다 · 99.9는 없다.
    assert b["citation_check"]["unmatched"] == ["99.9"]
    assert "알람 값 91.5를 확인했다" not in b["hypotheses"]
    assert "원장에 없는 값: 99.9" in b["limitations"]


def test_flag_on_skipped_investigation_unchanged(tmp_path):
    job = _run(tmp_path, lambda job: tools_unavailable_result({"polestar": "connect failed"}),
               citation_value_check_enabled=True)
    assert job.status == "done"
    assert "citation_check" not in job.briefing and job.briefing["hypotheses"] == []
