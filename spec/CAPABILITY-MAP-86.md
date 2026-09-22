# Capability Map: 알람 → 질의 프롬프트 인계 (`plans/86` · D-192)

> **작성일**: 2026-08-31 · **근거**: `plans/86-alarm-to-query-prompt-handoff.md`
> **사용자 확정(2026-08-31)**: G-1=**서버명 + 하단 추천 칩**(둘 다) · G-2=**확인 후 전송**(안 C) ·
> G-3=**검증된 형태만 추천**(평균·최대 + 파일시스템 급증형) · G-4=**Phase 1 + 2**(LLM 추천 포함)

## 모듈

| Module id | 책임 | 소비자 | Depends on |
|---|---|---|---|
| `alarm-prompt-builder` | 알람 payload → 추천 프롬프트 목록. `resource_type` 축 매핑 · `{서버}` 치환 · 패턴 반영. **순수 함수, DOM/네트워크 무관** | `alarm-prompt-ui` | — |
| `alarm-prompt-ui` | 카드의 진입 2종(헤더 서버명 트리거 · 하단 추천 칩) + 질의응답 인계(**확인 후 전송**) | 운영자 | `alarm-prompt-builder` |
| `alarm-prompt-llm-suggest` | 결정적 매핑이 비는 축에서 `recommended_action`·`pattern_analysis` 근거로 질의 1건 제안. **서버 라우트 · 기본 off** | `alarm-prompt-ui` | `alarm-prompt-builder`(폴백 계약) |

**Build order**: `alarm-prompt-builder` → `alarm-prompt-ui` → `alarm-prompt-llm-suggest`

## 경계가 이렇게 그어진 이유

- **builder를 UI에서 떼는 이유**: 추천 문구는 *"파이프라인이 답할 수 있는가"* 로 검증되는 값이다
  (`plans/86` §4). DOM에 묶여 있으면 그 검증을 테스트로 고정할 수 없다. 순수 함수라야 축별 문구를
  단언할 수 있다.
- **llm-suggest를 뒤에 두는 이유**: 결정적 추천이 **폴백 계약의 기준선**이다. LLM 경로가 실패하거나
  꺼져 있으면 builder 결과로 되돌아간다 — 기준선이 먼저 서 있어야 폴백이 정의된다.
- **순환 없음**: `llm-suggest`는 builder의 *출력 형식*(제안 객체)만 따르고 builder를 호출하지 않는다.
  폴백 선택은 `alarm-prompt-ui`가 한다.

## 인터페이스 (경계 계약)

`alarm-prompt-builder`가 제공하고 나머지 둘이 소비하는 단일 형식:

```js
// AlarmPromptSuggestion
{
  label: string,   // 칩에 보이는 짧은 요지 (예: "1개월 메모리 사용률")
  text:  string,   // 입력창에 들어갈 질의 전문
  axis:  string,   // 판정에 쓰인 resource_type 축 (관측·테스트용)
  source: "deterministic" | "llm"   // 폴백 판정과 표기의 근거
}
```

`llm-suggest` 라우트는 같은 형식의 객체 **1건**을 `{"suggestion": {...} | null}`로 돌려준다.

## 산출물

| 모듈 | 스펙 |
|---|---|
| `alarm-prompt-builder` | `SPEC-alarm-prompt-builder.md` |
| `alarm-prompt-ui` | `SPEC-alarm-prompt-ui.md` |
| `alarm-prompt-llm-suggest` | `SPEC-alarm-prompt-llm-suggest.md` |

계획·태스크: `tasks/plan-86.md` · `tasks/todo-86.md`
