# Spec: 변경 이벤트 오버레이 (`plans/91` 1-2 · `plans/50` C′-2 · §4.5)

> 근거 정본: `plans/50` §0.8.3 C′-2 · §4.5 · G1 앵커 패턴(`SPEC-incident-window-tools`). 모듈 id **`change-event-overlay`**.
> 의존: `correlation-e2e-assertions`. 이 모듈에 의존: `related-host-consumption`.

## Objective

"알람 직전에 무엇이 바뀌었는가"를 조사 LLM의 서술이 아니라 **사전수집·결정적 가설**로 답한다. 도구는 있으나(`polestar_change_history`) now 앵커라
사건 구간을 못 보고, 사전수집 배치는 알람 1 + 지표 4만 부른다(실측 `evidence_prefetch.py:71-80`).

## 계약 결정

- **mcp_server** `polestar_change_history(source, server_name, hours=24, reference_time: str|None=None, lookback_minutes: int|None=None, ctx)`:
  `reference_time` 미지정이면 **종전 SQL 문자열 동일**(now 앵커 · `hours`). 지정 시 `[ref − (lookback_minutes or hours·60), ref]`를 **epoch 정수**로
  보간 — `build_change_history_sql(server_name, cutoff_epoch, limit, until_epoch=None)`에 `h.event_time <= until` 1줄 추가(None이면 문자열 동일).
  epoch 변환: `parse_reference_time` 후 `datetime.timestamp()`(오프셋 있으면 그대로, naive면 서버 로컬 — 종전 now 앵커 `time.time()`과 같은 좌표).
  응답에 `window={reference_time, incident_from}` 동봉. DB2는 종전대로 `unsupported=True + note`.
- **sre_agent** 플래그 `evidence_change_overlay_enabled: bool = False`(env `EVIDENCE_CHANGE_OVERLAY_ENABLED` · **만료 2027-03-10**): off면 배치·결과·`to_dict`
  키 집합 **비트 동일**. on이면 `EvidenceScope.change_overlay=True` → `build_calls`가 **말미**에 `("polestar_change_history", {source, server_name,
  reference_time, lookback_minutes})` 1건 추가(알람·지표 인덱스 규약 불변) → `_changes()`가 `rows[].event_time`(epoch)·`description`·`lifecycle_type`을
  `ChangePoint(time, description, lifecycle)`로 → `correlate(..., changes=)`.
- **domain** `TimelineItem.kind="change"`(정렬 tie-break: metric 0 · change 1 · alarm 2 — 기존 metric<alarm 순서 불변) · `CorrelationResult.change_finding:
  dict|None`(키 `count`·`last_change_offset_min`·`before_first_alarm`·`descriptions[:3]`) — `to_dict`에는 **None이 아닐 때만** 실린다(키 집합 불변).
  DB2/미지원 응답(`unsupported`)이면 `notes`에 *"변경 이력은 PostgreSQL 소스만 지원 — 이 소스({source})는 변경 오버레이 없음"* (결정적 한계 · limitations로 자동 렌더).
  도구 실패는 `notes` 결손 1줄 + 나머지 상관 보존(부분 반환).
- **briefing** `_KIND_LABEL["change"]="변경"` · `root_cause_hypotheses`: `change_finding.before_first_alarm`이면 **rank 1**에 *"변경 직후 — {desc} 이후 {N}분 뒤
  첫 알람"* 가설을 **confidence `medium`**(상한 유지 — 상관≠인과)으로 삽입, evidence는 변경 타임라인 라인. 변경이 첫 알람 뒤면 가설 없음(타임라인에만).

## 성공 기준(골든)

1. `build_change_history_sql("web01", 1700000000, 200)` 문자열 **불변**; `until_epoch=1700003600` 지정 시 `h.event_time <= 1700003600` 1줄만 추가.
2. 도구: `reference_time` 미지정 호출은 종전 SQL(스냅샷) · 지정 시 epoch 창 + `window` 응답.
3. 골든: 변경 `T-20m` · 첫 알람 `T-10m` ⇒ 가설 1위 "변경 직후" · confidence medium · 타임라인 `T-20m 변경 …`.
4. 플래그 off: `build_calls` 5건 · `to_dict` 키 집합 종전과 동일 · 기존 테스트 전부 통과.
5. DB2 소스: notes에 PG 전용 한계 문장 · 나머지 상관 보존. 도구 실패: 결손 1줄 · 나머지 보존.
