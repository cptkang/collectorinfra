# Spec: 알람 피드백·ack 루프 신뢰성 (Plan 83 트랙 A)

> 요구·실측 근거의 정본은 **`plans/83` §1**, 문제 정의 출처는 **`docs/28`**이다. 배경을 복사하지 않는다.
> 모듈 id: **`alarm-feedback-loop`** (`CAPABILITY-MAP-83.md`) · 착수 결정: **D-177**(예약 등재 완료).

## Objective

운영자가 알람 카드에서 누르는 `유효`/`노이즈`(피드백)와 `확인`(ack)은 지금 **운영에 올리면
신뢰할 수 없는 상태**다. `docs/28` 작성 중 전수 실측으로 드러난 결함 9건 중 이 모듈이 다루는 것:

- **보안**: 두 엔드포인트에 **존(zone) RBAC이 없다** — SSE 스트림은 `alarm_zones_for_user`로
  거르는데(`src/api/routes/alarm.py:938`), 피드백(`:1122`)·ack(`:1064`)는 `require_user`만 요구한다.
  알람명만 알면 **다른 존 알람에 라벨을 남기고 사건을 확인 처리**할 수 있다.
- **정확성**: 저장하는 `pattern`은 LLM 산출값(`pattern_type`), 조회에 쓰는 `pattern`은 결정적
  사전분류(`pre_classification`, `alarm_analyzer.py:244`)다. 어긋나면 few-shot 가점(+1)이 누락된다.
- **감사**: 피드백에 **작성자가 없다**(ack는 `acked_by`를 남긴다). 오라벨의 출처를 추적할 수 없다.
- **운영**: 라벨 **철회 수단이 없다** — 오클릭이 영구 잔존해 few-shot을 오염시킨다.
- **성능**: `find_similar`가 매 알람마다 **파일 전체를 동기 읽기**하고(`feedback_store.py:107`),
  파일 **회전·상한이 없다**. async 노드 안의 blocking I/O다.
- **UX**: 액션가능성이 off인데 버튼은 항상 렌더되고, 누르면 503이 뜬다(`app.js:2508`).

**이 스펙이 만드는 것**: 두 엔드포인트의 존 경계 강제, 피드백 레코드의 완결성(자원·작성자·결정적
패턴), 철회 경로, 조회 성능의 상한, 게이트 상태 조회 API.

**하지 않는 것**: 라벨의 다수결·가중 합의 알고리즘(판정 로직 변경 = D-035 경계 재검토 선행) ·
ML 학습(D-048.11이 few-shot 한정으로 확정) · 억제 임계 튜닝 · 카드 시각 디자인.

## 확정된 게이트 (2026-08-28 사용자)

| # | 사항 | 확정 |
|---|---|---|
| G-3 | note 입력란 | **신설** — 접이식 한 줄(200자). 입력란에 민감정보 금지 안내 상시 노출 |
| G-5 | 철회 방식 | **tombstone append** — 파일 재작성 금지(append-only 감사 원칙 보존) |

## Tech Stack

기존 스택만 사용한다. Python 3.11 · FastAPI · pydantic v2 · 표준 라이브러리(json/pathlib/asyncio).
프런트는 바닐라 JS(빌드 없음). **신규 의존성 0**.

## Commands

```bash
# 단위·통합 테스트 (본체 + noise_gate 자동 수집)
.venv/bin/python -m pytest tests/test_api/test_alarm_feedback_rbac.py -q
.venv/bin/python -m pytest noise_gate/tests/test_feedback_store.py -q
.venv/bin/python -m pytest tests/ noise_gate/ -q          # 전건 회귀

# 아키텍처 계층 검사 (합격 기준)
.venv/bin/python scripts/arch_check.py --ci
```

과금 외부 API는 호출하지 않는다(D-127) — 이 모듈의 검증에 LLM 실호출 경로는 없다.

## Project Structure

```
noise_gate/infrastructure/feedback_store.py   → 적재·조회·회전·철회 (표준 라이브러리만)
noise_gate/domain/incident_store.py           → IncidentStore 포트(ack 존 판정용 db_id 조회 추가)
noise_gate/infrastructure/incident_repository.py → PG 구현
noise_gate/application/nodes/alarm_analyzer.py → few-shot 조회 호출부(blocking 회피)
noise_gate/application/nodes/alarm_notifier.py → SSE payload에 pre_classification 추가
src/api/routes/alarm.py                        → RBAC·capabilities·retract·summary 엔드포인트
src/static/js/app.js                           → 요청 필드 보강·note 입력·취소 링크·버튼 게이팅
noise_gate/tests/                              → feedback_store 단위 테스트
tests/test_api/                                → 라우트 RBAC·계약 테스트
```

계층 규칙(`domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry`)을
어기지 않는다. `feedback_store.py`는 **표준 라이브러리만** 쓰는 현행 제약을 유지한다.

## Code Style

기존 파일의 밀도를 그대로 따른다 — 한국어 docstring, 결정 근거를 주석에 남기고, graceful 실패는
`logger.warning` 후 무시한다(발송·응답 차단 금지).

```python
def record_feedback(
    self,
    *,
    label: str,
    alarm_name: str,
    resource_name: str = "",
    pattern: str = "",
    server_name: str = "",
    db_id: str = "",
    severity: Optional[int] = None,
    note: str = "",
    labeled_by: str = "",          # (Plan 83 A4) 감사 전용 — few-shot 프롬프트에는 싣지 않는다
    ts: Optional[datetime] = None,
) -> None:
    """운영자 피드백을 JSONL 한 줄로 append 한다.

    label은 "noise"|"valid"만 허용하며, 그 외 값은 기록하지 않고 warning 후 무시한다.
    기록 실패(OSError) 시 logger.warning 후 무시한다(응답 차단 금지). enabled=False면 no-op.
    """
```

라우트는 기존 패턴을 따른다 — `summary`/`description`(HTML `<br/>` 포함)·`response_model`·
`Depends(require_user)`·503은 기능 비활성, 403은 권한 부족.

## Testing Strategy

- **프레임워크**: pytest. 신규 테스트는 `noise_gate/tests/`(저장소 단위)와 `tests/test_api/`(라우트)에 둔다.
- **레벨**: 저장소 로직은 tmp_path 단위 테스트, 라우트는 FastAPI TestClient, 노드 결합은 기존
  `noise_gate/tests/test_llm_actionability.py` 패턴 재사용.
- **회귀 고정**: ① 기존 `find_similar` 결과가 tail 전환 후에도 **동일**함을 단언 ② `labeled_by`가
  few-shot 렌더 문자열에 **포함되지 않음**을 단언 ③ 게이트 off 경로는 현행과 **비트 동일**.
- **커버리지**: 신규·변경 함수는 정상 1건 + 실패/경계 1건 이상.

## Boundaries

- **Always**: 변경 후 `pytest` 전건 + `arch_check --ci` 통과 · 실패는 graceful(발송·응답 무차단) ·
  `.env` 신규 키는 `.env.example`에도 추가하고 **인라인 주석 금지**(별도 줄)
- **Ask first**: 판정 로직(`notification_policy.py`) 변경 · few-shot 프롬프트 구조 변경 ·
  DB 스키마 변경 · 신규 의존성
- **Never**: 시크릿·개인정보를 로그/프롬프트에 노출 · 피드백 파일 **재작성**(append-only 위반) ·
  실패한 테스트 삭제 · `feedback_store.py`에 외부 패키지 import

## 계약 — `GET /api/v1/alarm/capabilities` (이 모듈이 소유)

`alarm-view-level`이 소비한다. **불리언·정수만** 노출하고 경로·시크릿·엔드포인트 주소는 싣지 않는다.

```json
{"feedback_enabled": false, "incident_tracking": false,
 "sse_bridge": true, "suppress_stream": false, "suppress_max_severity": 2}
```

- 인증 필요(`require_user`). 게이트 조합은 서버가 계산해 내린다 — 클라이언트가 `.env`를 추론하지 않는다.
- `feedback_enabled` = `enable_noise_gate AND enable_llm_actionability` (피드백 라우트의 503 조건과 동일 식).

## Success Criteria

- [ ] 존 A 사용자 토큰으로 존 B `db_id`의 피드백·ack 요청 → **403**. 같은 존 요청은 **200**(회귀 0)
- [ ] `db_id` 미동반 요청은 **거부하지 않는다**(하위호환) — 존 무판정 통과
- [ ] 피드백 1건 후 JSONL 레코드에 `db_id`·`server_name`·`labeled_by`·**결정적** `pattern` 존재
- [ ] `labeled_by`가 `_render_feedback_section()` 출력 문자열에 **미포함**
- [ ] 라벨 → 철회 → `find_similar` 결과에서 해당 레코드 **제외**
- [ ] 20,000줄 픽스처에서 tail 읽기 결과가 전체 스캔과 **동일**. 상한 초과 시 `.1` 회전 파일 생성
- [ ] `find_similar` 호출이 이벤트 루프를 블로킹하지 않음(`asyncio.to_thread` 경유)
- [ ] `enable_llm_actionability=false`에서 피드백 버튼 **미렌더**, true에서 렌더
- [ ] `GET /alarm/capabilities`가 5개 불리언/정수 필드를 반환하고 경로·시크릿을 싣지 않음
- [ ] `pytest` 전건 통과 · `arch_check --ci` 0위반

## Open Questions

없음 — 게이트 5건은 2026-08-28 사용자 확정으로 종결(G-1·G-2·G-4는 `SPEC-alarm-view-level.md` 소관).
