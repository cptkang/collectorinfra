# Spec: composite-task-progress — 복합 질의 단계 이벤트

> 모듈 id `composite-task-progress` (`CAPABILITY-MAP-89.md`) · `plans/89` §3.4 · `plans/88` §4.1 연계.
> **가정**: 88 W1(`DependencyVerdict`)은 미착수다. 이 모듈은 **이벤트 자리와 필드명**만 만들고 판정 값은 싣지
> 않는다(88이 랜딩하면 `verdict=`로 채운다).

## Objective

순차 복합 질의가 도는 동안 단계(task)마다 시작·종료 이벤트를 내보내, UI가 "k/N 단계 · 무엇을 · 몇 건"을
실행 중에 그릴 수 있게 한다. 2단(`agent_orchestrator` 레벨 루프)과 1단(`deepagents_tools._run_subagent_tool`)이
**같은 헬퍼**를 부른다.

## Tech Stack

`langchain_core.callbacks.manager.adispatch_custom_event(name, data)` — 부모 run이 없으면 `RuntimeError`이므로
헬퍼가 삼키고 로그만 남긴다(단위 테스트·CLI 경로 안전).

## Commands

```
pytest tests/test_orchestration/test_task_progress.py -q
pytest tests/test_orchestration tests/test_composite -q
python scripts/arch_check.py --ci
```

## Project Structure

```
src/orchestration/task_progress.py     emit_task_progress(task, phase, *, result=None, verdict=None, total=None)
src/orchestration/agent_orchestrator.py  레벨 루프 start/end 호출 2줄
src/orchestration/deepagents_tools.py    _run_subagent_tool start/end 호출 2줄
src/orchestration/deep_agent.py          emit_step("agent.resume"/"agent.aggregate") 마일스톤
src/utils/progress_events.py             dispatch_progress_event · emit_step — 발행 공통부(utils 계층, T4)
src/nodes/schema_analyzer.py             _collect_live_samples: schema.sample start(k/n)·end (T4)
src/orchestration/subagents.py           pipeline.schema/generate/validate/execute/multi_db/organize start/end (T4)
tests/test_orchestration/test_task_progress.py
tests/test_orchestration/test_handler_milestones.py
```

## 계약 (`on_custom_event` name=`"task"` data)

```
{task_id, order, total|null, agent, sub_query, input_from: [task_id…], phase: "start"|"end",
 status: "in_progress"|"completed"|"failed"|"skipped", row_count?, error?,
 scope_col?, scope_size?, truncated?, truncated_count?, reason?, notes?}
```

- `status`는 task dict의 값을 그대로 낸다(88이 `skipped`를 넣으면 그대로 흐른다).
- `row_count`는 결과의 `rows`/`query_results`/`organized_data.rows` 중 첫 리스트 길이(없으면 생략).
- `verdict`가 주어지면 `scope_col/scope_size/truncated/truncated_count/reason`을 같은 이름으로 복사한다.
- name=`"step"` data: `{name, phase, label?}` — deep_agent 재개/합성 마일스톤. **T4 추가**: `schema.sample`(테이블마다 start `샘플 수집 k/n` · end 1회 `샘플 수집 완료 done/n`) · `pipeline.<stage>`(`schema`·`generate`(`SQL 재생성 k회차`)·`validate`·`execute`·`multi_db`(`멀티 DB 조회 N곳`)·`organize` — 실행된 단계만, start/end 쌍). end에는 label이 없을 수 있다(UI는 `stepLabels` 사전으로 보완).

## Code Style

```python
async def emit_task_progress(task: dict, phase: str, *, result: Any = None,
                             verdict: Any = None, total: int | None = None) -> None:
    """task 단계 진행을 custom event로 낸다. 부모 run이 없으면 조용히 건너뛴다(로그 debug)."""
```

## Testing Strategy

pytest · LLM 0. 헬퍼는 `adispatch_custom_event`를 monkeypatch해 페이로드를 단언한다. 대칭은 소스 grep으로
고정한다(`agent_orchestrator.py`·`deepagents_tools.py`가 `emit_task_progress`를 import·호출).

## Boundaries

- Always: 호출부는 2줄(판정 로직 없음) · 예외 삼킴(진행 표시 실패가 질의를 죽이지 않는다)
- Ask first: task dict에 새 키를 쓰는 것(88 소관)
- Never: 이벤트 실패를 오류로 승격 · LLM 호출 추가

## Success Criteria

1. 2단 mock 실행에서 task마다 start 1건·end 1건, 레벨 순서 보존, `status`가 end에서 `completed/failed`.
2. 1단 `_run_subagent_tool`에서 같은 헬퍼로 start/end(`total=None`).
3. 부모 run 없는 컨텍스트에서 호출해도 예외 없음.
4. 소스 대칭 테스트: 두 호출부 모두 존재.
5. `arch_check --ci` 0.

## Open Questions

- 88 W1 착수 시 `verdict=` 인자에 `DependencyVerdict`를 넘긴다 — 필드명은 이미 동일하게 예약했다.
