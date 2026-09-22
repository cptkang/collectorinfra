# Spec: `alarm-prompt-builder` — 알람에서 추천 질의 조립

> Module id: `alarm-prompt-builder` (`CAPABILITY-MAP-86.md`) · `plans/86` §3.1 · D-192

## Objective

알람 카드 payload를 받아 **파이프라인이 답할 수 있다고 확인된** 질의 문장 목록을 만든다.
사용자는 운영자이고, 성공은 *"칩을 눌러 나간 질의가 실제 데이터를 돌려준다"* 이다.

**핵심 제약**: 추천 문구는 창작 대상이 아니라 **검증된 형태의 재사용**이다. 골드셋
(`testdata/text2sql_gold/gp.yaml`)에 동형이 없는 문구는 넣지 않는다(`plans/86` §4).

### 가정 (진행 전 확인)

1. `resource_type`이 축 판정의 1차 키다 — `alarm_name`은 자유 한국어라 단독 키로 못 쓴다
   (실측: `"내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다"` 같은 값이 섞인다)
2. 서버 표기는 카드 헤더와 **같은 우선순위**여야 한다 — `server_identity.name` → `server_name` → `hostname`
3. `history_stats`·`process_snapshot`에 **의존하지 않는다** — SSE 티어 payload에 없다(경로별 비대칭)
4. 이 모듈은 순수 함수다 — DOM·네트워크·전역 상태를 만지지 않는다

## Tech Stack

바닐라 JS(ES5 호환 — 기존 `app.js` 스타일). 빌드 도구·프레임워크 없음.

## Commands

```bash
node --check src/static/js/app.js                      # 문법
python3 -m pytest tests/test_api/test_ui_alarm_view.py  # 정적 회귀
node /private/tmp/.../scratchpad/builder_probe.js       # 원문 추출 실행 검증(§Testing)
```

## Project Structure

```
src/static/js/app.js   → buildAlarmPrompts(data) 및 헬퍼 (기존 파일에 추가)
tests/test_api/test_ui_alarm_view.py → 정적 회귀
```

**서버측 파일은 만들지 않는다.** 축 매핑에 `server.Memory` 등 폴스타 리터럴이 들어가는데,
`overfit_check`는 `rglob("*.py")`만 훑으므로 JS는 대상이 아니다. 서버로 옮길 일이 생기면
`src/db_adapters/polestar/`(EXCLUDE) 또는 `config/` YAML이어야 한다(D-089).

## Code Style

기존 `app.js` 관례를 따른다 — `var`, 함수 선언, 인라인 `onclick` 금지, 한국어 주석에 **왜**를 남긴다.

```js
    // 축 매핑표 — 각 문구는 골드셋에 동형이 있는 것만 넣는다(plans/86 §4).
    // 창작한 문구를 넣으면 파이프라인이 답하지 못해도 아무도 그것을 검증하지 않는다.
    var ALARM_PROMPT_AXES = {
        "server.Cpus": [
            { label: "1개월 CPU 사용률", suffix: "서버의 지난 1개월 평균·최대 CPU 사용률을 보여줘" }
        ],
        "server.Memory": [
            { label: "1개월 메모리 사용률", suffix: "서버의 지난 1개월 평균·최대 메모리 사용률을 보여줘" }
        ]
        // …
    };
```

## Testing Strategy

- **정적 회귀**(`pytest`): 축별 문구 고정 · 미매핑 축은 빈 배열 · 서버명 우선순위가 카드 헤더와 동일 ·
  상한 3개 · `history_stats` 미의존
- **실행 검증**: 브라우저 확장·playwright MCP가 **모두 미연결**이므로, D-190에서 확립한 방식을 쓴다 —
  `app.js`에서 함수 원문을 잘라내 최소 스텁 위에서 실행(복제가 아니라 배포될 코드를 돌린다)
- **실 LLM 호출 없음** — 이 모듈은 결정적이다

## Boundaries

- **Always**: 골드셋 동형이 있는 문구만 추가 · 서버 표기 우선순위를 헤더와 일치 · 상한 3개 유지
- **Ask first**: 축 매핑표에 새 `resource_type` 추가(답변 가능성 검증이 선행돼야 한다) ·
  급증형 문구의 임계값 변경
- **Never**: `history_stats`·`process_snapshot` 의존 · LLM 호출 · DOM 접근 · 미검증 문구 추가

## Success Criteria

1. `buildAlarmPrompts(data)`가 `AlarmPromptSuggestion[]`을 돌려준다(형식은 capability map)
2. `resource_type`별 추천이 아래 표대로 나온다

   | 축 | 추천 |
   |---|---|
   | `server.Cpus` | 1개월 평균·최대 CPU 사용률 |
   | `server.Memory` | 1개월 평균·최대 메모리 사용률 |
   | `server.Disks` · `server.FileSystems` | 파일시스템별 사용률 + **급증형**(지난달 대비 10%p 상승 & 80% 이상) |
   | `server.Network` | 1개월 네트워크 트래픽 통계 — **T7 검증 통과 시에만 포함** |
   | `server.LogMonitor` | 최근 3개월 알람 발생 이력 |
   | `server.Server` | 가용성 상태와 IP |
   | 그 외(`management.*`·`platform.*`·미매핑) | **빈 배열** |

3. 공통 추천 1건(`사양과 OS 정보`)이 **마지막**에 붙는다 — 단 **매핑된 축에만**.
   *(초안은 "축과 무관하게"였으나 기준 2와 모순된다: 미매핑 축에 공통 1건이 붙으면
   "결정적 추천 0건"이 성립하지 않아 LLM 보강 경로(`alarm-prompt-llm-suggest`)가 영영
   열리지 않는다. 구현 중 발견해 정정했다.)*
4. `pattern_type === "급증"`이면 급증형이 **1순위**로 정렬된다
5. 최대 **3개**. 초과분은 잘린다
6. `{서버}`는 `server_identity.name` → `server_name` → `hostname` 순으로 치환된다
7. 대상명이 하나도 없으면 **빈 배열**을 돌려준다(주어 없는 질의를 만들지 않는다)
8. `is_routine === true`여도 **목록은 만든다** — 접는 것은 UI의 판단이다(관심사 분리)

## Open Questions

- `server.Network` 축은 골드셋에 동형이 없다 → `tasks/todo-86.md` T7에서 실 파이프라인 1회 검증
  후 포함 여부 결정. **검증 전에는 매핑표에서 제외**한다.
