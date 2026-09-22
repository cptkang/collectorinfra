# Spec: 상관 결과의 브리핑 반영 (`plans/50` §7.2·§9.1)

> 근거 정본: `plans/50` §7.2(가설 스키마) · §9.1(상대시각 타임라인) · §0.6 A′-6. 모듈 id **`diagnosis-briefing`** · 패키지 `sre_agent`(+ `noise_gate/domain` 렌더러 1키).
> 의존: `evidence-correlation` · `briefing-contract`.

## Objective
`build_briefing`이 `CorrelationResult`(dict)를 **정본 입력**으로 받아 ① 타임라인을 `T-15m 메트릭 …` 상대시각 항목으로 ② 복수
원인 가설을 `rank·confidence·evidence`로 ③ 데이터 한계(`notes`)를 `limitations`로 조립한다. **수치는 주입값만 쓴다(환각 0)** —
LLM 서술은 인용 근거로만 뒤에 붙는다.

## 계약 결정
- 시그니처: `build_briefing(..., correlation: dict | None = None)`. dispatcher는 `job.correlation`이 있을 때만 넘긴다.
- **새 키 `root_cause_hypotheses`**: `[{rank, cause, confidence, evidence[], reasoning}]`(§7.2). 상관이 없으면 `[]`
  (렌더러가 빈 값을 생략하므로 **사용자 출력은 종전과 동일** — 기본 off 비트 동일 원칙). `cause`(문자열)는 rank 1의 `cause`.
- 가설 순서(결정적): ① 선행 신호(lead_lag<0)의 지표 — sustained면 `high`, spike면 `medium` ② 그 외 이상 지표 — 알람 선행이면 `medium`, 아니면 `low`
  ③ LLM 인용 원인 — `citations_verified`면 `medium`, 아니면 `low`(가설 강등). `reasoning`에 **상관 ≠ 인과**를 명시.
- 타임라인 = 상관 타임라인(`T±Nm 메트릭|알람 detail`) 뒤에 LLM 인용 라인(`← 도구명`). 상관이 없으면 종전 그대로.
- `limitations` += `correlation.notes`(결정적 한계) + "상관 ≠ 인과 — 가설 신뢰도는 선행성·지속성에서 도출".
- 렌더러(M1): `_ORDERED`에 `("root_cause_hypotheses", "원인 가설")`을 `cause` 다음에 추가. dict 항목은 `1) (high) cause — 근거: …` 로 편다(repr 0).
  양쪽 계약 리터럴(`BRIEFING_CONTRACT_KEYS` · `CONTRACT_KEYS` · `CONSUMER_CONTRACT_KEYS`)을 함께 갱신한다.

## Commands
`cd sre_agent && .venv/bin/python -m pytest tests/test_briefing_builder.py -q` · `pytest tests/test_briefing_contract.py -q`

## Success Criteria
1. 골든(디스크IO 선행)에서 rank 1 = disk_io · `high` · evidence에 타임라인 항목 인용. 2. 타임라인 첫 항목이 `T-15m`. 3. `notes`가 `[한계]`로 사용자에게 도달(M1 경로).
4. 상관 None이면 `root_cause_hypotheses == []`이고 나머지 키 값은 종전과 동일. 5. 두 소비자 출력에 repr 0.
