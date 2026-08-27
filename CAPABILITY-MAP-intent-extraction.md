# Capability Map: 의도 추출 출력 계약 (Plan 80 실행 · 차수 0 + 차수 2)

> 상위 계획: `plans/80` §5 실행 단위(WU) 표 — **착수 판정의 단일 출처**
> 설계 근거: `plans/79` 트랙 E · `plans/78` W0 · `docs/instructor_intent_extraction_review.md`
> 사용자 승인(2026-08-27): **차수 0 + G-WHEEL 해제**(instructor 반입 진행) · todo-80 재조정 선행

## 모듈

| Module id | 책임 | WU | Depends on |
|---|---|---|---|
| `router-output-contract` | 라우터 응답 **처리** 계약 — intent 허용 집합 대조(F1) · `relevance_score` 항목 단위 격리(F2) · 임계 잠정값 문서 고정 | WU-01·02·03 | — |
| `composite-gap-tests` | 78 갭(G2·G3) 재현 테스트 — **xfail(strict)** 로 고정 | WU-04 | — |
| `structured-output-backend` | instructor 어댑터 + **한국어 MD_JSON 핸들러** + 모드 자동 선택 + 플래그·graceful 강등 | WU-07 | — |
| `intent-extraction-typing` | 구조화 출력 **적용** — DAG 분해(E-3a) · 라우터(E-3b) · 요구사항 추출(E-3c) | WU-08·09·10 | `structured-output-backend` · (E-3b만) `router-output-contract` |

**Build order**

```
router-output-contract  ─┐
composite-gap-tests     ─┼─▶ (병렬 · 상호 의존 0)
structured-output-backend┘
                          └─▶ intent-extraction-typing
```

- 앞의 셋은 **파일이 겹치지 않아** 순서 제약이 없다.
- 권장 착수 순서는 `router-output-contract` → `structured-output-backend` → `composite-gap-tests`.
  **E-2가 WU-05(S-1 골든셋 회귀)가 감시할 불변식(멀티 DB 축소)을 코드 쪽에서 먼저 막기 때문**이다 —
  승인 대기 중에 승인 대상 리스크를 줄이는 순서다.
- `intent-extraction-typing`은 백엔드 없이는 성립하지 않으므로 **유일한 진짜 의존**이다.

## 경계 — 이 맵이 다루지 않는 것

| 항목 | 이유 |
|---|---|
| WU-05·06 (S-1·S-2) | **G-BILL** — D-127 건별 승인 미취득 |
| WU-11~19 (78 본체) | WU-05 선행 |
| WU-15 · WU-21 | **G-DEC** — R-9 · `unknown` 의미 매핑 미확정 |
| 이월 축(트랙 C·B · W7-2) | vLLM 전환 / APM 도입 |
| 78·79 **문서 정정** | WU가 아니다 — `plans/80` §7 · `tasks/todo-80.md` Phase A·B·D 소유 |

## 공통 사항 (모듈 스펙은 이 절을 참조하고 반복하지 않는다)

**Tech Stack** — Python ≥3.11 · LangGraph 1.2.5 · langchain-core 1.4.7 · pydantic 2.12.5 ·
**instructor 1.15.4**(신규 · optional extra `structured`) · pytest 8 + pytest-asyncio

**Commands**
```bash
.venv/bin/python -m pytest tests/<대상> -q          # 대상 스위트
.venv/bin/python -m pytest -q                       # 전체 회귀
python scripts/arch_check.py --ci                   # 계층 검사 (exit 0 필수)
```

**Project Structure** (`arch_check` 계층 매핑 기준)
```
src/clients/        infrastructure  — LLM 클라이언트 · 구조화 출력 어댑터
src/routing/        infrastructure  — 시멘틱 라우팅
src/prompts/        prompts         — 프롬프트 템플릿·상수
src/nodes/          application     — 그래프 노드
src/orchestration/  orchestration   — DAG 분해·서브에이전트
tests/              (검사 제외)
```

**Code Style** — 기존 코드 관례를 따른다. 한국어 docstring·주석, 근거는 계획서/결정 번호로 인용.
```python
def _coerce_relevance(raw: Any) -> float | None:
    """LLM이 준 relevance_score를 float로 강제한다. 실패는 None(판정 불가).

    임의 기본값을 부여하지 않는다 — 형식 오류는 "관련도 0.5"가 아니라 판정 불가이고,
    기본값을 주면 게이트(MIN_RELEVANCE_SCORE)를 그냥 통과해 버린다(plans/79 트랙 E-2).
    """
```

**Boundaries** (전 모듈 공통)

- **Always**: 플래그 신설 시 기본값 = 현행 동작(비트동일) · 기동 시 1회 해석(78 P14) ·
  강등·탈락·폴백은 **사유를 구조화해 남긴다**(침묵 폴백 금지) · 단일/멀티 경로 **대칭 실측** ·
  변경 후 `pytest -q` + `arch_check --ci`
- **Ask first**: 결정적 상수 **값** 변경(`MIN_RELEVANCE_SCORE` 등) · 신규 의존 추가 ·
  `.env`/설정 스키마 변경 · **프롬프트 텍스트 변경**(S-1 미실행 — 측정 기준이 흔들린다)
- **Never**: 실 LLM 호출(**D-127 건별 승인** · `RUN_E2E=1` 무단 설정 금지) ·
  시크릿 커밋 · 실패 테스트 무단 삭제 · 병렬 세션 소유 파일 무단 수정

## Open Questions

| # | 항목 | 영향 |
|---|---|---|
| Q1 | 폐쇄망 **운영** 반입은 별개다 — 이번 설치는 개발 venv 한정 | 운영 배포 전 보안 절차 필요 |
| Q2 | `pyproject`에 `structured` extra를 **필수 의존으로 승격**할지 | 현재는 optional + graceful 강등 |
