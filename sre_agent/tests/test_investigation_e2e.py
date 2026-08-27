"""실 HolmesGPT 조사 완주 e2e (RUN_E2E 옵트인 · Gemini 실 호출 · D-120).

전제(운영자 사전 기동):
  1) Docker 픽스처: PG(5434·polestar.cmm_resource)·Prometheus(9190·target-vm/mock).
  2) mcp_server를 **조사 프로파일(고수준 도구만)**로 기동 — execute_sql·raw_promql 비노출
     (D-122: raw 노출 시 LLM이 방언 오류로 step 소진). 픽스처 연결:
       POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb
       PROMETHEUS_URL=http://localhost:9190  EXPOSE_EXECUTE_SQL=false EXPOSE_RAW_PROMQL=false
       PYTHONPATH=<repo>/mcp_server  python -m mcp_server   (포트 9099)
  3) Gemini 키: .encenv의 LLM_GEMINI_API_KEY (AgentSettings alias·CWD=repo root).

데이터 통제(D-120): 외부(Gemini) 송신 입력은 목업·Docker 픽스처 데이터만 — 실 운영 미연결.
값이 아니라 **구조**를 단언한다(LLM 비결정성): 완주 시 도구 인용·answer, 또는 graceful
미완주(incomplete=True)·하드 실패 없음.
"""

import os
import urllib.request

import pytest

from sre_agent.settings import AgentSettings

RUN_E2E = os.environ.get("RUN_E2E") == "1"


def _gemini_ready() -> bool:
    k = AgentSettings().gemini_api_key
    return k is not None and bool(k.get_secret_value())


def _mcp_reachable(url: str) -> bool:
    base = url.replace("/sse", "")
    try:
        with urllib.request.urlopen(base + "/sse", timeout=3) as _:
            return True
    except Exception as exc:  # noqa: BLE001 — SSE는 스트리밍이라 read 타임아웃도 "도달"로 간주
        return "timed out" in str(exc).lower() or "http error" in str(exc).lower()


_settings = AgentSettings()
pytestmark = pytest.mark.skipif(
    not (RUN_E2E and _gemini_ready() and _mcp_reachable(_settings.polestar_mcp_url)),
    reason=(
        "RUN_E2E=1 + Gemini 키(.encenv) + mcp_server(고수준 프로파일·9099) 기동 시에만 실행. "
        "미충족 시 skip(사유 명시 — 침묵 skip 아님)."
    ),
)


def _investigate(question: str, max_steps: int | None = None):
    """프로덕션 배선과 동형: remote_vm_profile + mcp_servers(폴스타/PromQL) + Gemini."""
    from sre_agent.diagnosis import DiagnosisAgent
    from sre_agent.interface.mcp_service import _build_mcp_servers
    from sre_agent.toolset_profiles import remote_vm_profile

    base = AgentSettings()
    s = AgentSettings(
        _env_file=None,
        model=base.investigation_llm_model,
        api_key=base.gemini_api_key,
        max_steps=max_steps if max_steps is not None else base.max_steps,
        polestar_mcp_url=base.polestar_mcp_url,
        polestar_mcp_token=base.polestar_mcp_token,
    )
    agent = DiagnosisAgent(
        settings=s, toolsets=remote_vm_profile(), mcp_servers=_build_mcp_servers(s)
    )
    return agent.ask(question)


@pytest.mark.mvp
def test_mvp_investigation_completes_or_graceful(mvp_record):
    """**레벨 B MVP 판정** — 실 LLM 조사가 완주(도구 인용·answer)하거나 graceful 미완주.

    결과는 mvp_record를 통해 실행 대장(docs/24)에 남는다. 백엔드(vLLM/Gemini)·모델은
    지문에 자동 기록되므로, 나중에 "어떤 모델로 완주했는가"를 대장에서 대조할 수 있다
    (docs/23 §7-V.5 완주 판정의 근거).
    """
    r = _investigate(
        "소스 polestar의 서버 svr-web-01에 대해 Prometheus 메트릭으로 CPU 사용률과 메모리 "
        "상태를 조회하고, 근거(도구 출력)와 함께 한국어로 간단히 진단하라. 도구는 "
        "hostname=svr-web-01로 호출하고 충분한 근거가 모이면 즉시 결론을 내라."
    )
    assert r is not None
    mvp_record["observed"] |= {
        "완주": "no(incomplete)" if r.incomplete else "yes",
        "도구호출": len(r.tool_calls or []),
        "토큰": getattr(r, "total_tokens", 0),
    }
    if r.incomplete:
        # 미완주도 유효 결과 — 사유가 구조화돼 있어야 한다(침묵 실패 금지).
        assert "미완주" in r.answer
    else:
        # 완주 — 실 도구를 사용했고(고수준 도구), 인용 있는 진단을 산출했다.
        assert r.answer.strip()
        assert r.tool_calls, "완주 시 최소 1건 도구 호출이 있어야 한다"
        hl = [tc for tc in r.tool_calls if "prom_metric" in tc or "polestar_" in tc]
        assert hl, f"고수준 도구(prom_metric·polestar_)를 사용해야 한다: {r.tool_calls}"
        # 토큰·비용 감사 기록(예산 가드의 근거)
        assert r.total_tokens > 0


def test_step_limit_forces_graceful_incomplete():
    """max_steps=2로 도구 사용 조사를 강제 미완주시켜 graceful incomplete를 실측한다."""
    r = _investigate(
        "svr-web-01의 CPU·메모리·디스크·네트워크·로드·프로세스를 모두 상세 조사하고 "
        "트리아지하라(각 지표를 개별 도구로 확인).",
        max_steps=2,
    )
    assert r is not None
    # 2 step으로는 다지표 조사 미완주 가능성이 높다 — 미완주면 graceful(사유), 완주여도 무크래시.
    if r.incomplete:
        assert "미완주" in r.answer and "step 상한" in r.answer
