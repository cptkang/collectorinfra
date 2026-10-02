# Spec: 사건 구간 증거 사전수집 + 결정적 상관 계산 (`plans/50` G4·G4-b)

> 근거 정본: `plans/50` §6(알고리즘) · §0.4-a(소재지 = `sre_agent`) · §0.6 A′-3·B′. 모듈 id **`evidence-correlation`**.
> 의존: `incident-window-tools` · `incident-scope` · `investigation-guidance`. 이 모듈에 의존: `diagnosis-briefing`.

## Objective

*"무엇이 먼저 일어났는가"* 를 서술이 아니라 **계산**으로 답한다. 조사 LLM이 도구를 부르기 전에 코드가 사건 구간의
알람·지표를 결정적으로 수집하고(z-score 이상탐지 · 선행성 · 타임라인 병합) 그 결과를 ① 조사 지침에 실어 LLM이 수치를
인용하게 하고 ② 브리핑(`diagnosis-briefing`)의 정본 입력으로 넘긴다.

## 계약 결정

- **호출 수단 = `mcp` 클라이언트 직접 호출**(계획 B′ 선행 채택). 실측(2026-09-02): holmes 0.36.0 `Tool.invoke(params, ToolInvokeContext)`의
  `ToolInvokeContext`는 `llm: holmes.core.llm.LLM`·`max_token_count`·`tool_call_id`가 **필수**라 결정적 사전수집이 LLM 객체에 결합된다.
  `mcp` 1.25.0은 `sre_agent/.venv`에 이미 있고(holmesgpt 전이) `pyproject.toml`에 `mcp<2`를 **명시 선언**한다(D-181 상한 동일).
  되돌리는 비용: `infrastructure/mcp_tool_client.py` 1파일 교체 — 응용 계층은 `ToolCaller` 콜러블만 안다.
- **계층**: `domain/correlation.py`(순수 함수 · **벤더 중립** — 시각·수치·라벨만) · `application/evidence_prefetch.py`(도구 인자·행 해석 —
  폴스타 어휘는 여기까지) · `infrastructure/mcp_tool_client.py`(SSE 세션·배치 호출). 규율 테스트: `correlation.py` 원문에 `polestar`·`cmm_` 0건.
- **플래그 기본 off**: `EVIDENCE_CORRELATION_ENABLED=false`면 dispatcher는 사전수집을 부르지 않는다(회귀 0). 켜져도 잡에
  `reference_time`이 없으면 건너뛴다(구간 없는 상관은 최신 데이터에 상관을 매기는 셈 — §6 선행 조건).
- **부분 실패 보장**: 도구 5회(전 알람 1 + 지표 4)는 개별 try — 실패한 축은 `notes`에 결손으로 기록되고 나머지로 계산한다.
  세션 자체가 실패하면 상관 없음(None) + 감사 `prefetch_failed`. 어느 경우도 조사를 막지 않는다.
- **알고리즘(§6 그대로)**: baseline μ·σ(사건 직전 N기간 `avg_val`) → `z=(window_max−μ)/σ ≥ 3.0` 급등 · 절대 임계(사용률 90) 보수적 결합 ·
  연속 2구간↑ sustained/1구간 spike · onset 상대분 · 첫 알람 대비 `lead_lag_min`(음수=지표 선행) · `leading_signal`=최초 onset.
  `notes`: 지표 정밀도(granularity 분) · baseline 부족(n<3) · 축 결손 · 구간 내 알람 0건.

## Project Structure
```
sre_agent/sre_agent/domain/correlation.py             신규 — 순수 함수 · CorrelationResult
sre_agent/sre_agent/application/evidence_prefetch.py  신규 — scope_from_job · prefetch_and_correlate
sre_agent/sre_agent/infrastructure/mcp_tool_client.py 신규 — run_tool_batch(SSE 1세션) · make_tool_caller
sre_agent/sre_agent/application/investigation_dispatcher.py  수정 — prefetch_fn 주입 · job.correlation
sre_agent/sre_agent/application/investigation_guidance.py    수정 — 상관 결과 지침 단락
sre_agent/sre_agent/interface/mcp_service.py           수정 — _default_prefetch_fn 배선(플래그)
sre_agent/tests/test_correlation.py · test_evidence_prefetch.py · test_mcp_tool_client.py
```

## Commands
`cd sre_agent && .venv/bin/python -m pytest tests -q && .venv/bin/python scripts/arch_check.py --ci`

## Boundaries
Always: LLM 호출 0 · now() 미사용(기준시각 좌표계) · 부분 실패 개별 try. Ask first: 임계값 상수 변경 · 지표 종류 추가. Never: 실 MCP 서버 호출 테스트 · `sre_agent → src/noise_gate` import.

## Success Criteria
1. 골든 케이스(디스크IO T-15m → CPU T-12m → 알람 T-10m)에서 `leading_signal=disk_io` · `lead_lag_min<0`. 2. 축 결손·baseline 부족이 `notes`에 결정적으로 남는다.
3. 플래그 off에서 dispatcher 경로 비트 동일. 4. `correlation.py`에 벤더 어휘 0건. 5. `sre_agent/tests`·`arch_check` 무회귀.
