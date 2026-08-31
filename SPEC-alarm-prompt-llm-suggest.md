# Spec: `alarm-prompt-llm-suggest` — LLM 추천 보강 (기본 off)

> Module id: `alarm-prompt-llm-suggest` (`CAPABILITY-MAP-86.md`) · `plans/86` §3.4 · D-192
> **사용자 확정**: G-4=**Phase 1 + 2 포함**

## Objective

결정적 매핑이 비는 축(`management.*` · `platform.*` · 미매핑 `resource_type`)에서,
`recommended_action`·`pattern_analysis`를 근거로 **질의 1건**을 제안한다. 사용자가 요청한
*"권고 조치나 패턴 분석에서 해당 이벤트를 분석할 수 있는 프롬프트를 추천"* 의 직접 구현이다.

### 가정 (진행 전 확인)

1. **기본 off**다 — `alarm_prompt_llm_suggest_enabled: bool = False`. 신규 플래그 기본 off =
   현행 동작과 비트 동일(`plans/80` §5.4-③)
2. **결정적 추천이 있으면 부르지 않는다** — 보강이지 대체가 아니다. 호출은 builder 결과가
   빈 배열일 때로 한정한다(과금 경로를 좁힌다)
3. LLM 산출도 **확인 후 전송**이다(G-2) — 자동 전송 경로는 애초에 없다
4. 실 LLM 호출은 **테스트에서 하지 않는다**(D-127) — mock으로 계약만 고정한다

## Tech Stack

FastAPI 라우트 + `src.llm.create_llm` + pydantic 모델. 기존 알람 라우트와 같은 스택.

## Commands

```bash
python3 -m pytest tests/test_api/test_alarm_prompt_suggest.py -q
python3 scripts/arch_check.py --ci
python3 scripts/overfit_check.py --ci
ruff check src/ && mypy src/api/routes/alarm.py
```

## Project Structure

```
src/api/routes/alarm.py        → POST /api/v1/alarm/suggest-prompt
src/config.py                  → NoiseGateConfig.alarm_prompt_llm_suggest_enabled (기본 False)
noise_gate/prompts/alarm_prompt_suggest.py → 시스템 프롬프트 + 사용자 템플릿
tests/test_api/test_alarm_prompt_suggest.py
```

프롬프트를 `noise_gate/prompts/`에 두는 이유: 입력이 알람 분석 결과이고, 기존
`alarm_analyzer.py` 프롬프트와 같은 도메인이다. `noise_gate/prompts/`는 `overfit_check`의
`PUBLIC_LAYER_DIRS`에 **없다**(스캔 대상은 `noise_gate/domain`) — 다만 스키마 리터럴은 넣지 않는다.

## Code Style

기존 알람 라우트 관례를 따른다 — pydantic 요청/응답 모델, `require_user` 의존성,
`_assert_zone_access`로 존 RBAC, 비활성은 503.

```python
@router.post("/alarm/suggest-prompt", response_model=AlarmPromptSuggestResponse)
async def suggest_alarm_prompt(
    request: Request,
    body: AlarmPromptSuggestRequest,
    current_user: dict = Depends(require_user),
) -> AlarmPromptSuggestResponse:
    """권고 조치·패턴 분석을 근거로 조회 질의 1건을 제안한다(기본 off → 503)."""
    ng = request.app.state.config.noise_gate
    if not getattr(ng, "alarm_prompt_llm_suggest_enabled", False):
        raise HTTPException(status_code=503, detail="프롬프트 추천 비활성")
    _assert_zone_access(request, current_user, body.db_id)   # 쓰기 경로와 같은 규약
```

## Testing Strategy

- **계약 테스트**(mock LLM): 200 정상 · **비활성 시 503** · **존 권한 없으면 403** ·
  LLM 실패 시 `suggestion=null`(500 아님 — graceful) · 응답 형식이 capability map의
  `AlarmPromptSuggestion`과 일치
- **실 LLM 호출 금지** — `RUN_E2E=1` 옵트인 뒤에도 이 계획에서는 실행하지 않는다.
  실행이 필요하면 **건별 사용자 승인**(D-127)
- **프론트**: 라우트 503/403/실패에서 **칩이 사라지지 않고** 결정적 추천만 남는지

## Boundaries

- **Always**: 기본 off · 결정적 추천이 빈 경우에만 호출 · 존 RBAC · 실패는 graceful(null)
- **Ask first**: 플래그를 기본 on으로 전환 · 호출 조건 확대(결정적 추천이 있어도 호출) ·
  실 LLM 호출 실행
- **Never**: LLM 산출을 확인 없이 전송 · 알람 원문을 그대로 로그에 남김(PII) ·
  실패를 5xx로 올려 카드 렌더를 깨뜨림

## Success Criteria

1. `POST /api/v1/alarm/suggest-prompt`가 `{"suggestion": {label, text, axis, source:"llm"} | null}`을 돌려준다
2. 플래그 off(**기본값**)면 **503**, 본문은 `"프롬프트 추천 비활성"`
3. 다른 존 알람이면 **403**(`_assert_zone_access` — 피드백·ack와 같은 규약)
4. LLM 예외·타임아웃·파싱 실패는 **200 + `suggestion: null`** (카드는 결정적 추천으로 계속 동작)
5. 프론트는 결정적 추천이 **0건일 때만** 호출한다 — 있으면 네트워크 요청 자체가 없다
6. 플래그 off 상태에서 **기존 동작과 바이트 동일** — 신규 테스트 외 회귀 0
7. `arch_check --ci` exit 0 · `overfit_check --ci` 신규 유입 0

## Open Questions

- LLM 산출 문구가 파이프라인이 답할 수 있는 형태인지는 **보장되지 않는다**(결정적 경로와 달리
  골드셋 대조가 없다). 그래서 ①기본 off ②결정적이 빌 때만 ③확인 후 전송 — 3중으로 막는다.
  운영 투입 전 실측이 필요하면 별건으로 승인받아 진행한다.
