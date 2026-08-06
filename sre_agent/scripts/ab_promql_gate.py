"""D-119 품질 게이트 — PromQL 접근 경로 A/B 비교 (Plan 06 §8 수용 기준 7).

**무엇을 재는가**: 같은 Prometheus 픽스처·같은 질문·같은 LLM에서, PromQL에 닿는 경로만
바꿔 조사 품질이 열화되는지 본다.

    A안(폴백 후보) : holmesgpt 내장 `prometheus/metrics` toolset 직결
    B안(현행 D-119): `mcp_server`가 노출하는 PromQL 도구 경유(서버측 nodename 조립)

B가 A보다 나쁘지 않으면 D-119 유지, 열화면 A안 복귀(Plan 06 §3 폴백 절차).

**판정은 결정적으로 한다**(D-035 — LLM 서술을 심판으로 쓰지 않는다). Docker 픽스처의
mock_exporter가 내는 값은 고정이므로, 조사 서술이 그 값을 집어냈는지를 문자열로 대조한다:

    cpu user 97.5 / cpu system 1.5 / memory 8589934592(=8GiB) / oom_kills 3

**과금 주의(D-127)**: 실 Gemini를 호출한다. `RUN_E2E=1` + 사용자 승인 없이는 실행되지 않는다.

전제(운영자 사전 기동):
  1) Docker 픽스처 — Prometheus 9190(target-vm·mock_exporter).
  2) `mcp_server` 조사 프로파일(고수준 도구·9099) — B안 팔에서만 쓴다.
  3) Gemini 키 — `.encenv`의 `LLM_GEMINI_API_KEY`(AgentSettings alias·CWD=repo root).

사용법:
    RUN_E2E=1 python sre_agent/scripts/ab_promql_gate.py [--trials 2]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field

# 픽스처 결정값 — mock_exporter가 내는 고정 메트릭(Plan 06 §8.1 실측).
# 조사 서술이 이 사실을 집어냈는지로 "품질"을 잰다(LLM 심판 배제).
#
# 각 항목은 (요구조건, 후보) — "any"는 후보 중 하나만 있으면 인정, "all"은 전부 필요.
# 값이 아니라 지표 이름만 언급하고 넘어가는 서술을 사실 포착으로 세지 않으려면 **수치**가
# 들어가야 한다. OOM은 숫자 3 단독이 흔해 오탐이므로 'oom'과 '3'을 동시에 요구한다.
EXPECTED_FACTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "cpu_user_97.5": ("any", ("97.5", "97.50")),
    "cpu_system_1.5": ("any", ("1.5", "1.50")),
    "memory_8gib": ("any", ("8589934592", "8.59", "8 GiB", "8GiB", "8.0 GiB", "8 GB")),
    "oom_kills_3": ("all", ("3",)),  # + 아래에서 'oom' 동반 확인
}

PROMETHEUS_URL = os.environ.get("AB_PROMETHEUS_URL", "http://localhost:9190")

# 무료 티어는 모델별 분당 요청 수가 매우 낮다(gemini-3.5-flash 실측 5 RPM). ReAct 루프는
# 짧은 시간에 수십 회를 연속 호출하므로 재시도 없이는 첫 조사에서 전건 429로 죽는다
# (첫 실행 실측 — A2/B1/B2가 0.5~0.7초 만에 실패). litellm 재시도·백오프로 흡수한다.
LLM_NUM_RETRIES = int(os.environ.get("AB_LLM_RETRIES", "8"))

# 픽스처가 결정값을 갖는 메트릭을 **이름으로 명시**한다. 이름을 주지 않으면 LLM이
# node_exporter 계열(node_cpu_seconds_total 등)을 조회하는데, 그 값은 컨테이너 실측이라
# 고정이 아니어서 결정적 채점이 불가능하다(1차 실행에서 양 팔 0/4로 드러남).
# 두 팔에 **같은 질문**이 가므로 비교 공정성은 유지된다.
QUESTION = (
    "서버 svr-web-01(nodename 라벨)의 다음 Prometheus 메트릭 현재값을 조회하라: "
    "mock_cpu_usage_percent{mode=\"user\"}, mock_cpu_usage_percent{mode=\"system\"}, "
    "mock_memory_used_bytes, mock_oom_kills_total. "
    "조회한 수치를 **그대로 인용**해 CPU·메모리·OOM 상태를 한국어로 간단히 진단하라. "
    "네 값을 모두 얻으면 즉시 결론을 내라."
)


@dataclass
class ArmResult:
    """한 팔의 1회 조사 결과(결정적 지표만)."""

    arm: str
    trial: int
    completed: bool
    elapsed_s: float
    tool_calls: list[str] = field(default_factory=list)
    facts_found: list[str] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    answer_chars: int = 0
    answer_excerpt: str = ""   # 판정 근거 감사용 발췌(전문은 남기지 않는다)
    error: str | None = None

    @property
    def fact_score(self) -> int:
        return len(self.facts_found)

    @property
    def infra_failure(self) -> bool:
        """품질과 무관한 인프라 사유(무료 티어 쿼터 등)로 죽었는지.

        429는 접근 경로의 품질이 아니라 LLM 제공자 쿼터 문제다. 이런 시행을 품질 평균에
        섞으면 "B 열화"로 오판된다(실측: B2가 429로 0점 → 판정이 열화로 뒤집힘).
        품질 비교에서는 제외하고 별도로 보고한다(숨기지 않는다).
        """
        return bool(self.error) and "RateLimitError" in (self.error or "")


def _settings_base():
    from sre_agent.settings import AgentSettings

    return AgentSettings()


def _configure_rate_limit_retry() -> None:
    """litellm 전역 재시도를 켜 429(무료 티어 RPM)를 백오프로 흡수한다.

    양 팔에 **동일하게** 적용된다 — 재시도는 지연에만 영향을 주고 조사 품질(사실 포착)에는
    개입하지 않으므로 A/B 비교의 공정성을 해치지 않는다. 대신 `elapsed_s`는 대기 시간을
    포함하므로 지연 비교는 이 실행에서 유효하지 않다(요약에 명시).
    """
    import litellm

    litellm.num_retries = LLM_NUM_RETRIES
    litellm.retry_after = 60  # RetryInfo가 58s를 지시(실측) — 분당 창이 열릴 때까지 대기


def _make_settings(base, max_steps: int, model: str | None = None):
    """조사용 설정을 만든다(.env 재로딩 차단 — 테스트 config 누수 방지)."""
    from sre_agent.settings import AgentSettings

    return AgentSettings(
        _env_file=None,
        model=model or base.investigation_llm_model,
        api_key=base.gemini_api_key,
        max_steps=max_steps,
        polestar_mcp_url=os.environ.get("AB_MCP_URL") or base.polestar_mcp_url,
        polestar_mcp_token=base.polestar_mcp_token,
    )


def _run_arm_a(base, max_steps: int, model: str | None = None):
    """A안 — holmesgpt 내장 prometheus/metrics toolset 직결(mcp_server 미등록).

    **prerequisite 캐시를 반드시 끈다**(Plan 06 §3 폴백 참고·`docs/18_known_mistakes.md`).
    캐시가 켜져 있으면 이전 실행의 실패 상태가 히트해 toolset이 조용히 DISABLED로 남고,
    LLM에 Prometheus 도구가 하나도 안 붙는다 — 실측으로 확인했다(A안 1차 실행에서 실제
    도구 호출 0건·내부 task 도구만 3건). 그 상태의 비교는 A/B가 아니라 "A 부재 vs B"다.
    """
    from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag

    from sre_agent.diagnosis import DiagnosisAgent

    toolsets = {
        "kubernetes/logs": {"enabled": False},
        "bash": {"enabled": True, "config": {"allow": [], "builtin_allowlist": "core"}},
        # A안의 본질 — 내장 toolset이 Prometheus에 직접 붙는다.
        "prometheus/metrics": {
            "enabled": True,
            "config": {"prometheus_url": PROMETHEUS_URL},
        },
    }
    agent = DiagnosisAgent(settings=_make_settings(base, max_steps, model), toolsets=toolsets)
    # DiagnosisAgent.llm은 캐시 기본값(ENABLED)으로 만든다 — A안만 명시적으로 다시 만든다.
    agent._llm = agent._config.create_toolcalling_llm(
        toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
    )
    _assert_prometheus_tools_present(agent)
    return agent.ask(QUESTION)


def _assert_prometheus_tools_present(agent) -> None:
    """A안이 실제로 Prometheus 도구를 들고 있는지 확인한다(없으면 비교 자체가 무의미).

    도구가 붙지 않은 채로 조사를 돌리면 "A가 아무것도 못 찾았다"는 결과가 나오는데, 그건
    접근 경로 비교가 아니라 설정 실패다. 여기서 끊어 측정 불가로 드러낸다(침묵 금지).
    """
    names = list(getattr(agent.llm.tool_executor, "tools_by_name", {}) or {})
    prom = [n for n in names if "prometheus" in n.lower() or "metric" in n.lower()]
    if not prom:
        raise RuntimeError(
            "A안 구성 실패 — 내장 prometheus 도구가 LLM에 붙지 않았다"
            f"(노출 도구: {names}). 비교 불가."
        )


def _run_arm_b(base, max_steps: int, model: str | None = None):
    """B안(현행) — remote_vm_profile + mcp_server PromQL 도구 경유."""
    from sre_agent.diagnosis import DiagnosisAgent
    from sre_agent.interface.mcp_service import _build_mcp_servers
    from sre_agent.toolset_profiles import remote_vm_profile

    s = _make_settings(base, max_steps, model)
    agent = DiagnosisAgent(
        settings=s, toolsets=remote_vm_profile(), mcp_servers=_build_mcp_servers(s)
    )
    return agent.ask(QUESTION)


def _normalize(text: str) -> str:
    """수치 대조 전에 표면형을 정규화한다.

    LLM은 같은 값을 `8589934592`·`8,589,934,592`처럼 다르게 쓴다. 정규화 없이 문자열로
    대조하면 **정답을 오답으로 세고**, 그게 곧 "B 열화" 오판이 된다(실측: B2가 실제로는
    8,589,934,592를 보고했는데 천 단위 구분자 때문에 미포착으로 집계됨).
    천 단위 구분자(숫자 사이 쉼표)만 제거하고 공백을 접는다 — 값 자체는 바꾸지 않는다.
    """
    import re

    return re.sub(r"\s+", " ", re.sub(r"(?<=\d),(?=\d{3}\b)", "", text))


def _score(answer: str) -> list[str]:
    """서술이 픽스처 결정값을 집어냈는지 결정적으로 대조한다."""
    norm = _normalize(answer)
    lowered = norm.lower()
    found = []
    for fact, (mode, needles) in EXPECTED_FACTS.items():
        hit = all(n in norm for n in needles) if mode == "all" else any(
            n in norm for n in needles
        )
        if fact == "oom_kills_3":
            hit = hit and "oom" in lowered  # 숫자 3 단독 오탐 차단
        if hit:
            found.append(fact)
    return found


def run_trial(arm: str, trial: int, base, max_steps: int, model: str | None = None) -> ArmResult:
    runner = _run_arm_a if arm == "A" else _run_arm_b
    started = time.monotonic()
    try:
        r = runner(base, max_steps, model)
    except Exception as exc:  # noqa: BLE001 — 한 팔의 실패가 비교 전체를 중단시키지 않는다
        return ArmResult(
            arm=arm, trial=trial, completed=False,
            elapsed_s=round(time.monotonic() - started, 1), error=f"{type(exc).__name__}: {exc}",
        )
    elapsed = round(time.monotonic() - started, 1)
    answer = r.answer or ""
    return ArmResult(
        arm=arm,
        trial=trial,
        completed=not r.incomplete,
        elapsed_s=elapsed,
        tool_calls=list(r.tool_calls or []),
        facts_found=_score(answer),
        total_tokens=getattr(r, "total_tokens", 0) or 0,
        total_cost=float(getattr(r, "total_cost", 0.0) or 0.0),
        answer_chars=len(answer.strip()),
        answer_excerpt=answer.strip()[:600],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="D-119 PromQL 접근 경로 A/B 품질 게이트")
    ap.add_argument("--trials", type=int, default=2, help="팔당 반복 횟수(기본 2)")
    ap.add_argument("--max-steps", type=int, default=40, help="조사 step 상한(기본 40)")
    ap.add_argument("--out", default="eval_results/d119_ab_gate.json", help="결과 JSON 경로")
    ap.add_argument("--pace-seconds", type=int, default=35,
                    help="시행 사이 대기(초) — 무료 티어 RPM 창 회복용(기본 35)")
    ap.add_argument(
        "--model", default=None,
        help="모델 오버라이드(예: gemini/gemini-3.5-flash-lite). 미지정 시 AgentSettings 기본값",
    )
    args = ap.parse_args()

    # ── 과금 게이트(D-127) — 승인 없이는 실행하지 않는다 ──
    if os.environ.get("RUN_E2E") != "1":
        print("보류: 실 Gemini 호출(과금)이므로 RUN_E2E=1 + 사용자 승인 후에만 실행합니다 (D-127).")
        return 0

    base = _settings_base()
    if base.gemini_api_key is None or not base.gemini_api_key.get_secret_value():
        print("보류: Gemini 키 미설정(.encenv LLM_GEMINI_API_KEY) — 실행하지 않습니다.")
        return 0

    _configure_rate_limit_retry()
    model = args.model or base.investigation_llm_model
    # B안이 붙을 mcp_server — AB_MCP_URL로 조사 프로파일 인스턴스를 지정할 수 있다
    # (기본 인스턴스에 PROMETHEUS_URL이 없으면 B가 값을 못 얻어 "열화"로 오판된다 — 실측).
    mcp_url = os.environ.get("AB_MCP_URL") or base.polestar_mcp_url
    print(f"D-119 A/B 게이트 — 모델={model} prometheus={PROMETHEUS_URL} "
          f"mcp={mcp_url} trials={args.trials} retries={LLM_NUM_RETRIES}")

    results: list[ArmResult] = []
    first = True
    for trial in range(1, args.trials + 1):
        for arm in ("A", "B"):
            # 무료 티어 분당 창(5 RPM)이 다시 열리도록 시행 사이를 띄운다. 안 띄우면
            # 앞 시행이 창을 소진해 다음 시행이 429로 죽고, 그게 "열화"로 오판된다(실측).
            if not first and args.pace_seconds > 0:
                print(f"  … 쿼터 페이싱 {args.pace_seconds}s 대기", flush=True)
                time.sleep(args.pace_seconds)
            first = False
            print(f"  [{arm}] trial {trial} 실행 중 ...", flush=True)
            res = run_trial(arm, trial, base, args.max_steps, model)
            results.append(res)
            print(f"    완주={res.completed} 사실={res.fact_score}/{len(EXPECTED_FACTS)} "
                  f"도구={len(res.tool_calls)} {res.elapsed_s}s tokens={res.total_tokens}"
                  + (f" ERROR={res.error}" if res.error else ""), flush=True)

    summary = {}
    for arm in ("A", "B"):
        rs = [r for r in results if r.arm == arm]
        # 품질 비교는 **인프라 사유로 죽지 않은 시행**만으로 한다(쿼터 실패 ≠ 품질 열화).
        qs = [r for r in rs if not r.infra_failure]
        n = len(qs) or 1
        summary[arm] = {
            "trials": len(rs),
            "scored_trials": len(qs),
            "infra_failed": sum(1 for r in rs if r.infra_failure),
            "completed": sum(1 for r in qs if r.completed),
            "fact_score_avg": round(sum(r.fact_score for r in qs) / n, 2),
            "fact_score_max": max((r.fact_score for r in qs), default=0),
            "tool_calls_avg": round(sum(len(r.tool_calls) for r in qs) / n, 1),
            "elapsed_avg_s": round(sum(r.elapsed_s for r in qs) / n, 1),
            "tokens_avg": int(sum(r.total_tokens for r in qs) / n),
            "errors": [r.error for r in rs if r.error],
        }

    a, b = summary["A"], summary["B"]
    # ── 측정 성립 여부 먼저 판정한다 ──
    # 두 팔이 모두 0점이면 "B가 A만큼 좋다"가 아니라 **아무것도 측정되지 않은 것**이다.
    # 이 구분을 두지 않으면 쿼터 초과·서비스 다운 같은 전면 실패가 그대로 "열화 없음"
    # 통과로 읽힌다(실제로 첫 실행에서 429 전건 실패가 통과로 출력됐다 — D-127 재발 방지).
    # 양 팔 모두 채점 가능한 완주 시행이 최소 1회씩 있어야 비교가 성립한다.
    measurable = a["completed"] > 0 and b["completed"] > 0
    if not measurable:
        reasons = sorted({(e or "").split(":")[0] for e in a["errors"] + b["errors"] if e})
        verdict = (
            "측정 불가 — 한쪽 이상에 채점 가능한 완주 시행이 없음"
            f" (A 완주 {a['completed']}/{a['scored_trials']}, B 완주 {b['completed']}/{b['scored_trials']}"
            + (f", 원인: {', '.join(reasons)}" if reasons else "")
            + "). 통과/열화 어느 쪽으로도 해석 금지."
        )
        degraded = None
    else:
        # 열화 판정 — B가 A보다 사실 포착이 낮거나 완주율이 낮으면 열화.
        degraded = (b["fact_score_avg"] < a["fact_score_avg"]) or (b["completed"] < a["completed"])
        verdict = "열화 — A안 복귀 검토" if degraded else "열화 없음 — D-119(B안) 유지"

    print("\n=== 요약 ===")
    for arm in ("A", "B"):
        s = summary[arm]
        print(f"  {arm}: 완주 {s['completed']}/{s['scored_trials']} · 사실 {s['fact_score_avg']}"
              f"/{len(EXPECTED_FACTS)} · 도구 {s['tool_calls_avg']} · {s['elapsed_avg_s']}s"
              f" · {s['tokens_avg']} tokens"
              + (f" · 인프라실패 {s['infra_failed']}(채점 제외)" if s['infra_failed'] else ""))
    print(f"  판정: {verdict}")

    out = {
        "model": model,
        "prometheus_url": PROMETHEUS_URL,
        "mcp_url": mcp_url,
        "question": QUESTION,
        "expected_facts": list(EXPECTED_FACTS),
        "summary": summary,
        "verdict": verdict,
        "degraded": degraded,       # None이면 측정 불가(통과/열화 어느 쪽도 아님)
        "measurable": measurable,
        "results": [asdict(r) for r in results],
    }
    path = args.out
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"  결과 저장: {path}")
    # 측정 불가는 성공 종료로 만들지 않는다 — 호출부(CI·운영자)가 통과로 오인하지 못하게.
    return 0 if measurable else 2


if __name__ == "__main__":
    sys.exit(main())
