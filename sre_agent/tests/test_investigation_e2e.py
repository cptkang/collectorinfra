"""실 HolmesGPT 조사 완주 e2e (RUN_E2E 옵트인 · Gemini 또는 사내·자체 호스팅 LLM 실 호출 · D-120 · D-127).

전제(운영자 사전 기동):
  1) Docker 픽스처: PG(5434·polestar.cmm_resource)·Prometheus(9190·target-vm/mock).
  2) mcp_server를 **조사 프로파일(고수준 도구만)**로 기동 — execute_sql·raw_promql 비노출
     (D-122: raw 노출 시 LLM이 방언 오류로 step 소진). 픽스처 연결:
       POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb
       PROMETHEUS_URL=http://localhost:9190  EXPOSE_EXECUTE_SQL=false EXPOSE_RAW_PROMQL=false
       PYTHONPATH=<repo>/mcp_server  python -m mcp_server   (포트 9099)
  3) Gemini 키: .encenv의 LLM_GEMINI_API_KEY. **실행 cwd는 sre_agent**(루트 cwd에서 돌리면 conftest의
     `tests.mvp_record`가 루트 `tests/` 패키지와 충돌해 수집 단계에서 죽는다 — 2026-09-10 실측). 루트 .encenv는
     sre_agent cwd에서 읽히지 않으므로 키를 환경변수로 내보낸다:
       cd sre_agent && LLM_GEMINI_API_KEY=$(grep ^LLM_GEMINI_API_KEY= ../.encenv | cut -d= -f2-) \
         RUN_E2E=1 POLESTAR_MCP_URL=http://localhost:9097/sse .venv/bin/python -m pytest tests/test_investigation_e2e.py
  4) 쿼터: 조사 1건이 최대 max_steps(40)회 LLM 호출이라 Gemini **무료 등급(분당 5회)**으로는 429로 완주 불가(2026-09-10 실측).
  5) **사내·자체 호스팅 LLM(vLLM 등 OpenAI 호환)**: `API_BASE`가 설정돼 있으면 Gemini가 아니라 운영 배선
     `MODEL`·`API_BASE`·`API_KEY`로 조사한다(`_llm_wiring`). 이때 Gemini 키는 필요 없다 — 조사 LLM 게이트는
     `investigation_llm_enabled=True`로 연다(D-230 · 종전의 더미 키 우회 대체). 종전에는 `API_BASE`를 줘도
     `investigation_llm_model`(Gemini)로 조립돼 사내 LLM을 검증하지 못했다(2026-09-17 실측).
       cd sre_agent && RUN_E2E=1 MODEL=openai/<served-name> API_BASE=http://<host>:8000/v1 API_KEY=dummy \
         GEMINI_API_KEY= LLM_GEMINI_API_KEY= POLESTAR_MCP_URL=http://localhost:9097/sse \
         .venv/bin/python -m pytest tests/test_investigation_e2e.py
     토큰 예산은 `OVERRIDE_MAX_CONTENT_SIZE`·`OVERRIDE_MAX_OUTPUT_TOKEN`(AgentSettings 필드)로 함께 넘어간다.

데이터 통제(D-120): 외부(Gemini) 송신 입력은 목업·Docker 픽스처 데이터만 — 실 운영 미연결.
값이 아니라 **구조**를 단언한다(LLM 비결정성): 완주 시 도구 인용·answer, 또는 graceful
미완주(incomplete=True)·하드 실패 없음.
"""

import os
import urllib.request

import pytest

from sre_agent.settings import AgentSettings

RUN_E2E = os.environ.get("RUN_E2E") == "1"


def _llm_wiring(base: AgentSettings) -> dict:
    """조사 LLM 배선을 고른다 — `API_BASE`가 있으면 운영 배선, 없으면 Gemini(D-120).

    운영 배선은 `DiagnosisAgent`가 실제로 읽는 필드(`model`·`api_key`·`api_base`)와 토큰 예산을 그대로
    넘긴다. `API_BASE`를 기준으로 삼는 이유: 자체 호스팅 엔드포인트를 명시했다는 뜻이고, 없을 때
    프로바이더 기본(외부 SaaS) 경로로 조용히 새지 않게 하기 위해서다. Gemini 경로는 종전과 비트 동일.
    """
    if base.api_base:
        return {
            "model": base.model,
            "api_key": base.api_key,
            "api_base": base.api_base,
            "override_max_content_size": base.override_max_content_size,
            "override_max_output_token": base.override_max_output_token,
            # 조사 LLM 게이트를 키 없이 연다(D-230). Gemini 키는 명시적으로 비운다 — `_env_file=None`이어도
            # 프로세스 env(GEMINI_API_KEY·LLM_GEMINI_API_KEY)는 읽히므로 생략하면 그 값이 실린다.
            "investigation_llm_enabled": True,
            "gemini_api_key": None,
        }
    return {"model": base.investigation_llm_model, "api_key": base.gemini_api_key,
            "gemini_api_key": base.gemini_api_key}


def _llm_ready(base: AgentSettings) -> bool:
    if base.api_base:
        return True
    k = base.gemini_api_key
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
    not (RUN_E2E and _llm_ready(_settings) and _mcp_reachable(_settings.polestar_mcp_url)),
    reason=(
        "RUN_E2E=1 + 조사 LLM(API_BASE 운영 배선 또는 Gemini 키) + mcp_server(고수준 프로파일) 기동 시에만 실행. "
        "미충족 시 skip(사유 명시 — 침묵 skip 아님)."
    ),
)


def _investigate(question: str, max_steps: int | None = None):
    """프로덕션 배선과 동형: remote_vm_profile + mcp_servers(폴스타/PromQL) + 조사 LLM(`_llm_wiring`)."""
    from sre_agent.diagnosis import DiagnosisAgent
    from sre_agent.interface.mcp_service import _build_mcp_servers
    from sre_agent.toolset_profiles import remote_vm_profile

    base = AgentSettings()
    s = AgentSettings(
        _env_file=None,
        **_llm_wiring(base),
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


# ── 상관 on 실 경로 (plans/91 1-1 · C′-0 · SPEC-correlation-e2e-assertions) ─────────────────
# JobStore 경유 · evidence_correlation_enabled=True · 페이로드 alarmTime → reference_time. 값이 아니라 **구조**만
# 단언한다(실 데이터·LLM 비결정성). 실행은 D-127 건별 승인(RUN_E2E=1) 뒤 — 파일 전역 pytestmark가 게이트한다.


def test_correlation_on_job_briefing_has_hypotheses_timeline_limitations(tmp_path):
    from sre_agent.application.briefing_builder import CORRELATION_NOT_CAUSATION_NOTE
    from sre_agent.application.investigation_jobs import JobStore
    from sre_agent.interface.mcp_service import _build_dispatcher

    base = AgentSettings()
    s = AgentSettings(
        _env_file=None,
        **_llm_wiring(base),
        max_steps=base.max_steps,
        polestar_mcp_url=base.polestar_mcp_url,
        polestar_mcp_token=base.polestar_mcp_token,
        evidence_correlation_enabled=True,
        investigation_timeout_seconds=600,
    )
    # dispatcher도 같은 tmp 감사 파일을 쓴다 — 미지정이면 공용 기본 경로(sre_agent/.data)에 종결 기록이 쌓인다(D-213 ⑥).
    disp = _build_dispatcher(s, audit_path=tmp_path / "audit.jsonl")
    store = JobStore(s, executor=disp, audit_path=tmp_path / "audit.jsonl")
    payload = {
        "contract_version": "1",
        # 폴스타 원 이벤트 시각 계약 yyyyMMddHHmmss(`alarm_time_to_iso`). 종전 "2026-09-01 14:00:00"은 None으로
        # 떨어져 reference_time·사전수집·상관이 전부 생략돼 이 테스트가 구조적으로 통과할 수 없었다(2026-09-17 실측).
        "event": {"dbId": "polestar", "serverName": "web-01", "hostname": "web-01", "severity": 2,
                  "alarmTime": "20260901140000"},
        "decision": {"fingerprint": "e2e-corr", "tier": "PAGE"},
    }
    res = store.submit(payload)
    assert res["status"] == "accepted"
    disp.wait_workers(600)
    got = store.get(res["investigation_id"])
    assert got["status"] == "done", got
    b = got["briefing"]
    hyps = b["root_cause_hypotheses"]
    assert [h["rank"] for h in hyps] == list(range(1, len(hyps) + 1))
    assert all(h["confidence"] in {"high", "medium", "low"} for h in hyps)
    assert any(ln.startswith("T-") or ln.startswith("T+") for ln in b["timeline"]) or "타임라인 근거 없음" in b["timeline"][0]
    assert CORRELATION_NOT_CAUSATION_NOTE in b["limitations"]
