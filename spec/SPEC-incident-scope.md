# Spec: 사건 기준시각·구간의 파싱과 위임 계약 전달 (`plans/50` A′-5)

> 근거 정본: `plans/50` §0.3 G1(계약 지점) · §0.6 A′-5. 모듈 id **`incident-scope`** · 패키지 `src` + `sre_agent`.
> 의존: `incident-window-tools` · 이 모듈에 의존: `evidence-correlation`.

## Objective

위임 계약(`sre_diagnose`)이 기준시각을 나르지 못하고, push는 `event.alarmTime`을 보유하고도 질문에 싣지 않는다.
pull은 질의에서 시각을 **결정적으로** 파싱하고, push는 알람 시각을 쓰며, 잡이 `reference_time`·`lookback_minutes`를
보유해 `investigation-guidance`가 조사 LLM에 전달한다.

## 계약 결정

- `sre_diagnose(question, server_name, hostname, db_id, target_state, reference_time=None, lookback_minutes=None)`.
  `reference_time`은 ISO 8601. 형식 오류는 **거부**(`rejected` · reason 명시 — 침묵 폴백 금지).
- 잡 필드 `reference_time`·`lookback_minutes`(둘 다 `summary()`에 노출). push는 `event.alarmTime`(yyyyMMddHHmmss)
  → ISO 변환, 구간 기본 **120분**(`plans/50` §3.4 `default_lookback_minutes`). 변환 불가면 None(시각 없는 조사 — 종전과 동일).
- pull 파싱은 `src/domain/incident_time.py::parse_incident_time(query, now)` 순수 함수(LLM 비의존 · `now` 주입).
  해상도별 구간: 도구 창은 `[reference − lookback, reference]`(상한 = 기준시각)이므로 **"쯤"의 뒤쪽을 덮도록 여유를 더한다**:

  | 표현 | anchor | reference_time | lookback |
  |---|---|---|---|
  | `어제 14시(쯤)` | 어제 14:00 | anchor + 60분 | 120 + 60 |
  | `어제 14시 20분` · `14:20` | 14:20 | anchor + 30분 | 120 + 30 |
  | `어제`(시각 없음) | 어제 00:00 | 어제 23:59:59 | 1440 |
  | `2시간 전` · `30분 전` | now − N | anchor + 60/30 | 120 + 60/30 |
  | `최근 2시간` | — | now | 120 |
  | 시각 표현 없음 | — | **None**(앵커 없음 — 종전 동작) | — |

  시각만 있고 날짜가 없으면 오늘, anchor가 미래면 어제. `오전/오후/새벽/밤/저녁` 반영.
- 챗 노드는 파싱 결과가 있을 때만 `client.diagnose(..., reference_time=…, lookback_minutes=…)`를 넘긴다
  (없으면 인자 자체를 보내지 않는다 — 구버전 서비스·기존 fake 호환).
- `_job_to_question`: push 질문에 `발생 시각`을 싣는다. pull 질문은 원문 유지(구간 지시는 guidance가 나른다).

## Commands
`pytest tests/test_incident_time.py -q` · `cd sre_agent && .venv/bin/python -m pytest tests/test_incident_scope.py tests/test_mcp_service.py tests/test_investigation_jobs.py -q`

## Boundaries
Always: 파서는 `now` 주입(벽시계 미사용) · 잡 필드는 기본 None(종전 동작) · Never: LLM 파싱 의존 · 실 조사 실행.

## Success Criteria
1. 표의 사례가 단위 테스트로 고정. 2. push 잡이 `alarmTime`에서 `reference_time`을 얻는다. 3. 형식 오류 거부.
4. 시각 없는 질의는 위임 인자가 종전과 동일. 5. `tests/test_semantic_routing/test_fault_diagnosis.py` 등 기존 fake 무회귀.
