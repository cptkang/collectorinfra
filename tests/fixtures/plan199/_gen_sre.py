"""plans/199 W0 픽스처 생성 1단 — sre_agent dispatcher를 가짜 콜러블로 돌려 poll 응답을 모은다.

poll 응답 = `job.summary()`(`sre_get_investigation` 반환 스키마).

`sre_agent/.venv/bin/python tests/fixtures/plan199/_gen_sre.py <출력 JSON>`으로만 돈다
(`make_fixtures.py`가 부른다). 플레이북 도구는 plans/187 W0 녹화(`FakeCaller`), 소견기는 가짜 LLM,
HolmesGPT는 가짜 조사 함수다 — 실 LLM·MCP·DB 호출 0. pytest 수집 대상 아님(파일명).
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

_SRE_TOP = Path(__file__).resolve().parents[3] / "sre_agent"
for _p in (_SRE_TOP, _SRE_TOP / "scripts", _SRE_TOP / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sre_agent.application.investigation_dispatcher import PlaybookWiring  # noqa: E402
from sre_agent.diagnosis import ToolCallRecord  # noqa: E402
from sre_agent.domain.known_error import parse_known_error  # noqa: E402
from test_plan187_w2_runner import FakeCaller, _load  # noqa: E402
from test_plan187_w4_dispatch import (  # noqa: E402
    FakeDeep,
    FakeLLM,
    alarm_of,
    build,
    make_job,
    make_settings,
    run_job,
)

#: 판독 계층 도구가 아닌 증상 도구만 인용하는 심층 답(엔진 비교 = HolmesGPT 근거 계층 없음).
SYMPTOM_ONLY = dict(
    answer="응답 지연과 CPU 상승이 함께 보인다 — apm_app_health 확인",
    outputs=[ToolCallRecord("apm_app_health", "골든 시그널", "success", '{"ok": true}')],
)

#: CPU 사례(case3)와 일치하는 validated 알려진 오류 — 리터럴은 전부 가짜다.
KE_CPU = parse_known_error({
    "id": "KE-DEMO-012",
    "title": "배치 겹침 CPU 포화(예시)",
    "state": "validated",
    "issue": "온라인 시간대 WAS CPU 급등",
    "environment": {"playbook": "was.latency", "targets": {"instance_glob": ["A*"]}},
    "match": {"all": ["cpu_owner == 'was_logic'"]},
    "cause": "정산 배치와 온라인 거래 겹침",
    "cause_test": {"all": ["S1.verdict == 'degraded'"]},
    "workaround": [
        {"action": "담당 업무팀에 배치 시작 시각 조정 요청", "risk": "low",
         "verification": "CPU 기준선 복귀", "rollback": "해당 없음(요청만)"},
    ],
    "owner": "WAS운영팀(예시)",
    "last_reviewed": "2026-10-01",
})

#: 사용자 뷰 마스킹 쌍에 넣는 비밀값 꼴 문자열(가짜).
SECRET_PASSWORD = "password=abc123"
SECRET_BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZW1vIn0.c2lnbmF0dXJl"


def _markdown_answer() -> str:
    """HolmesGPT풍 마크다운 답변 — 제목·목록·번호 목록·굵게·인라인 코드·파이프 표 · 8,000자 초과."""
    head = [
        "### 장애 분석 결과 보고서",
        "",
        "**대상**: `wasdemo01` 인스턴스 `A1` · 업무 계좌조회 · 알람 `TX_BAD_RESPONSE`",
        "",
        "### 1. 요약",
        "",
        "- 원인: 업무 DB 응답 지연으로 SQL 실행 시간이 응답시간의 대부분을 차지한다"
        " (`apm_resource_pool` 근거)",
        "- 영향: 같은 업무의 다른 호스트(`wasdemo02`)까지 p95 상승 — 공통 의존 의심",
        "- 확신도: **중간** — DB 쪽 지표(DPM)는 미연동이라 직접 확인하지 못했다",
        "",
        "### 2. 근거",
        "",
        "| 시각 | 지표 | 값 | 기준선 | 도구 |",
        "|---|---|---|---|---|",
        "| 09:40 | p95(ms) | 6800 | 900 | apm_app_health |",
        "| 09:45 | sql_ms 비중 | 90% | 35% | apm_slow_transactions |",
        "| 09:50 | 풀 대기 | 12 | 0 | apm_resource_pool |",
        "",
        "### 3. 권고 조치",
        "",
        "1. **커넥션 풀 점유 트랜잭션 확인** — `apm_resource_pool` 결과의 대기 스레드를 본다",
        "2. **업무 DB 담당 확인 요청** — `/acct/inquiry` 의 실행계획 변경 여부",
        "3. 재발 시 `apm_transaction_profile` 로 획득·실행 시간을 나눠 본다",
        "",
        "### 4. 한계",
        "",
        "- DPM 미연동 — DB 세션·락 직접 확인 불가",
        "- 프로세스 스냅샷은 조사 시점 단면일 수 있음",
        "",
        "### 5. 상세 관측",
        "",
        "| 분 | 서비스 | 응답(ms) | SQL(ms) | 외부(ms) | 비고 |",
        "|---|---|---|---|---|---|",
    ]
    rows = [
        f"| {9 + (30 + m) // 60:02d}:{(30 + m) % 60:02d} | /acct/inquiry | {5200 + m * 7} | "
        f"{4700 + m * 6} | {20 + m % 5} | 풀 대기 {m % 13}건 · 같은 SQL 반복 · `ORDER_ITEM` 조회 |"
        for m in range(0, 120)
    ]
    return "\n".join(head + rows)


def _done(settings: Any, fixture: str, *, deep: Any = None, caller: Any = None,
          known_errors: tuple = (), llm: Any = None, alarm: dict | None = None,
          wiring: bool = True) -> dict:
    caller = caller or FakeCaller(_load(fixture))
    llm = llm or FakeLLM()
    d = build(settings, caller, deep=deep, llm=llm, wiring=wiring)
    if known_errors:
        d._playbook = PlaybookWiring(playbooks=d._playbook.playbooks, polestar=caller,
                                     apm=caller, llm=llm, known_errors=known_errors)
    job = run_job(d, alarm if alarm is not None else alarm_of(fixture))
    return job.summary()


def _running_collect() -> dict:
    """수집 중 스냅숏 — 도구 배치 호출 직전마다 summary를 찍어 4단계까지 끝난 것을 고른다."""
    base = FakeCaller(_load("latency_sql"))
    holder: dict = {}
    snaps: list[dict] = []

    def caller(calls):  # noqa: ANN001, ANN202
        job = holder.get("job")
        if job is not None:
            snaps.append(copy.deepcopy(job.summary()))
        return base(calls)

    d = build(make_settings(), caller, llm=FakeLLM())
    job = make_job(alarm_of("latency_sql"), "inv-running-collect")
    holder["job"] = job
    d(job)
    d.wait_workers(30)
    mid = [s for s in snaps
           if s.get("partial_briefing") and (s.get("progress") or {}).get("done") == 4]
    return mid[0]


def _running_deep() -> dict:
    """플레이북 완료 · 심층 진행 중 스냅숏(parallel · 가짜 HolmesGPT를 게이트로 붙잡는다)."""
    settings = make_settings(sre_deep_dive_mode="parallel")
    gate = threading.Event()
    deep = FakeDeep(settings, gate=gate)
    d = build(settings, FakeCaller(_load("latency_sql")), deep=deep, llm=FakeLLM())
    job = make_job(alarm_of("latency_sql"), "inv-running-deep")
    d(job)
    assert deep.started.wait(10)
    deadline = time.time() + 10
    snap: dict = {}
    while time.time() < deadline:
        snap = copy.deepcopy(job.summary())
        if "summary" in (snap.get("partial_briefing") or {}):
            break
        time.sleep(0.01)
    gate.set()
    d.wait_workers(30)
    return snap


def _episode_alarm() -> dict:
    """사건 종합 조사 페이로드 — 대표 알람(제니퍼) + 폴스타 멤버 2건 · 유효 링크 1건."""
    alarm = alarm_of("latency_sql")
    alarm_id = alarm["event"]["alarmId"]
    alarm["meta"]["investigation_kind"] = "episode"
    alarm["meta"]["episode"] = {
        "episode_id": "ep-demo-1",
        "host_key": "wasdemo01",
        "infra_db_ids": ["polestar"],
        "members": [
            {"alarm_id": "p-demo-1", "source": "polestar", "db_id": "polestar", "kind": "cpu",
             "alarm_name": "CPU 사용률", "occurred_at": "2026-10-19T09:58:00"},
            {"alarm_id": "p-demo-2", "source": "polestar", "db_id": "polestar", "kind": "memory",
             "alarm_name": "메모리 사용률", "occurred_at": "2026-10-19T09:59:00"},
            {"alarm_id": alarm_id, "source": "jennifer", "kind": "apm",
             "was_kinds": ["was_slow_sql"], "alarm_name": "TX_BAD_RESPONSE",
             "occurred_at": "2026-10-19T10:00:00"},
        ],
        "links": [
            {"rule_id": "R-DEMO-CPU-APM", "cause_alarm_id": "p-demo-1",
             "effect_alarm_id": alarm_id, "lag_seconds": 120.0, "missing": []},
        ],
    }
    return alarm


def _secret_case() -> dict:
    """비밀값 꼴 문자열이 소견·심층 답·프로세스 표 셀에 섞인 조사(사용자 뷰 마스킹 쌍의 원문)."""
    settings = make_settings(sre_deep_dive_mode="parallel")
    fx = _load("latency_external")
    caller = FakeCaller(fx)
    snapshot = copy.deepcopy(fx["tool_responses"]["polestar_process_snapshot"][0]["response"])
    snapshot["rows"][0]["args"] = f"/opt/java/bin/java -Ddb.{SECRET_PASSWORD} -jar app.jar"
    caller.overrides["polestar_process_snapshot"] = snapshot
    deep = FakeDeep(settings, answer=(
        "### 장애 분석\n- 원인: 외부 연계 지연 ← apm_resource_pool\n"
        f"- 로그 발췌: 연계 호출 헤더 Authorization: {SECRET_BEARER}\n"
        f"- 설정 발췌: {SECRET_PASSWORD}"
    ))
    llm = FakeLLM(f"수집된 표의 값을 확인했다. 접속 문자열 {SECRET_PASSWORD} 가 로그에 보인다.")
    return _done(settings, "latency_external", deep=deep, caller=caller, llm=llm)


def build_all() -> dict[str, dict]:
    """케이스 이름 → `{summary, alarm_ids, kind, episode_id}`."""
    par = make_settings(sre_deep_dive_mode="parallel")
    legacy = make_settings(sre_playbook_enabled=False)
    s6_missing = FakeCaller(_load("case3_cpu_loop"))
    s6_missing.overrides["polestar_process_snapshot"] = {"error": "폴스타 프로세스 API 미설정"}
    cases: dict[str, dict] = {
        "confirmed_same_layer": _done(par, "gc", deep=FakeDeep(
            par, answer="힙 바닥 상승 · GC 비중 증가 — apm_runtime_health 확인",
            outputs=[ToolCallRecord("apm_runtime_health", "런타임", "success", '{"ok": true}')])),
        "confirmed_cpu_ke": _done(par, "case3_cpu_loop", deep=FakeDeep(par, **SYMPTOM_ONLY),
                                  known_errors=(KE_CPU,)),
        "evidence_missing": _done(par, "case3_cpu_loop", deep=FakeDeep(par, **SYMPTOM_ONLY),
                                  caller=s6_missing),
        "deep_skipped": _done(make_settings(sre_deep_dive_mode="off"), "latency_external"),
        "deep_failed": _done(par, "latency_external", deep=FakeDeep(
            par, error=RuntimeError("holmes 연결 실패"))),
        "missing_many": _done(par, "polestar_unresolved"),
        "different_layer": _done(par, "latency_external"),
        "non_playbook": _done(legacy, "latency_sql", deep=FakeDeep(
            legacy,
            answer="원인: 업무 DB 커넥션 풀 고갈 ← apm_resource_pool\n"
                   "타임라인: 09:40 p95 상승 → 09:50 풀 대기 12건 (apm_resource_pool)\n"
                   "권고: 풀 점유 트랜잭션 확인"), wiring=False),
        "markdown_narrative": _done(par, "latency_sql",
                                    deep=FakeDeep(par, answer=_markdown_answer())),
        "episode_scope": _done(make_settings(sre_deep_dive_mode="parallel",
                                             cross_source_investigation_enabled=True),
                               "latency_sql", alarm=_episode_alarm()),
        "user_view_secret": _secret_case(),
        "running_collect": _running_collect(),
        "running_deep": _running_deep(),
    }
    out: dict[str, dict] = {}
    for name, summary in cases.items():
        alarm_ids = ["a-demo-1"]
        kind, episode_id = "alarm", None
        if name == "episode_scope":
            members = _episode_alarm()["meta"]["episode"]["members"]
            rep = members[-1]["alarm_id"]
            alarm_ids = [rep] + [m["alarm_id"] for m in members if m["alarm_id"] != rep]
            kind, episode_id = "episode", "ep-demo-1"
        out[name] = {"summary": summary, "alarm_ids": alarm_ids, "kind": kind,
                     "episode_id": episode_id}
    return out


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(
        json.dumps(build_all(), ensure_ascii=False, default=str), encoding="utf-8"
    )
