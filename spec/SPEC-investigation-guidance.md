# Spec: 조사 지침 주입 배선 (`plans/50` G5)

> 근거 정본: `plans/50` §0.3 G5 · §0.6 A′-4. 모듈 id **`investigation-guidance`** · 패키지 `sre_agent`.
> 의존 없음 · 이 모듈에 의존: `evidence-correlation` · (외부) `plans/85` §9 티어 1-①(Plan 51 §6 플레이북).

## Objective

`_default_diagnose_fn`이 `agent.ask(question)`만 호출해 `system_prompt_additions`를 넘기는 **프로덕션 호출부가
0건**이다. 그래서 원격 프로파일이 요구하는 `REMOTE_VM_SHELL_NOTE`("로컬 셸은 대상 VM 아님")조차 주입되지
않고, 사건 구간(`incident-scope`)을 조사 LLM에 알릴 통로도 없다. 지침을 **한 곳에서 조립**해 `ask()`에 넘긴다.

## 계약 결정

- 조립 함수 `sre_agent/application/investigation_guidance.py::build_guidance(settings, job, *, remote=True) -> str | None`.
  순서 고정: ① `REMOTE_VM_SHELL_NOTE`(remote일 때) ② 사건 구간 지침(잡에 `reference_time`이 있을 때 —
  앵커 인자를 도구 호출에 쓰라는 지시) ③ `settings.investigation_guidance_extra`(운영자 자유 지침, 기본 None).
  전부 비면 `None` — `ask()`가 붙이는 `LOAD_GUARD_NOTE`는 종전대로 항상 주입된다(중복 금지는 `_with_load_guard_note`가 보장).
- `investigation_guidance_extra`는 **Plan 51 §6 플레이북의 편입점**이다. 이 스펙은 플레이북을 만들지 않는다.
- 기본값(설정 None · 잡에 시각 없음)에서 주입되는 것은 `REMOTE_VM_SHELL_NOTE`뿐이다 — 이는 `remote_vm_profile()`
  독스트링이 애초에 요구한 동작이라 회귀가 아니라 **결함 수정**이다.

## Commands
`cd sre_agent && .venv/bin/python -m pytest tests/test_investigation_guidance.py tests/test_mcp_service.py -q`

## Testing Strategy
`build_guidance` 순서·생략 · `_with_load_guard_note` 중복 0 · **`_default_diagnose_fn`이 `ask()`에 additions를 전달**(fake agent 캡처 — 현재 0건인 것을 테스트로 고정).

## Boundaries
Always: 지침은 문자열 조립만(LLM 호출 없음) · Ask first: 프로파일 allowlist 확장 · Never: 실 LLM 호출(D-127).

## Success Criteria
1. 프로덕션 경로에서 `ask(system_prompt_additions=…)`가 non-None으로 호출된다. 2. `REMOTE_VM_SHELL_NOTE` 도달. 3. 사건 구간이 있으면 앵커 인자 지시가 포함된다. 4. `sre_agent/tests` 무회귀.
