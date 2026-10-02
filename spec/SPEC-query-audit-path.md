# Spec: 질의 감사 경로 일원화 (Plan 84 트랙 B)

> 요구·실측 근거의 정본은 **`plans/84` §1.2·§2.2**이다. 배경을 복사하지 않는다.
> 모듈 id: **`query-audit-path`** (`CAPABILITY-MAP-84.md`) · 착수 결정: **D-183**(예약 등재 완료).
> 의존: 없음. `query-history-ui`와 병렬.

## Objective

관리자 대시보드의 "감사 로그" 탭(`dashboard.html:60` · `admin.js:1450`)은 DB `audit_logs`를
읽는데, **거기에 질의 이벤트가 한 건도 없다**. 원인은 단순하다 —
`AuditService.log_user_request`(`audit_service.py:126`)가 **정의만 있고 호출부가 0건**이다.

질의 전문은 파일 감사에는 남고 있다(`input_parser.py:157` → `logs/audit-*.jsonl`).
**없는 것은 저장이 아니라 조회 경로다.**

**이 스펙이 만드는 것**: 질의 1건이 API·CLI 어느 쪽으로 들어와도 **감사 파일 1건 + (API면) DB
1행**으로 남는, 단일한 기록 경로.

## 핵심 설계 결정 — 이중 기록을 만들지 않는다

`AuditService.log`는 **JSONL과 DB에 둘 다 쓴다**(`audit_service.py:43`·`:51`, 기본값
`jsonl_enabled=True`·`db_enabled=True` — `config.py:544-545`, `.env`에 `AUDIT_*` 없음).
그래서 라우트에서 그냥 호출하면 `input_parser`의 기록과 겹쳐 **파일에 같은 질의가 두 번** 남는다.

| 안 | 내용 | 판정 |
|---|---|---|
| (a) | 라우트 호출 + 노드 호출 유지 | **기각** — 파일 이중 기록. 감사 파일 건수가 실제 질의 수와 어긋나면 그 파일로 하는 모든 집계(`plans/82` §5.1의 918건 비용 실측 등)가 오염된다 |
| (b) | 라우트에서 `audit_repo.log_event` 직접 호출 | 차선 — 중복은 없지만 `AuditService`를 만든 이유(형식 통일·`client_ip`·`request_id`)를 우회 |
| **(c)** | **라우트로 일원화** + 노드 호출 제거 + **CLI에 파일 기록 이설** | **채택** |

(c)의 근거:

1. **감사의 주체는 "요청 수신"이지 "파싱 노드"가 아니다.** 노드는 `app.state`에 닿지 못해
   `client_ip`·`request_id`·`session_id`를 영원히 채울 수 없다 — `log_user_request`가 호출부 없이
   남아 있던 것 자체가 이 계층 문제의 결과로 보인다.
2. **노드에 감사 I/O가 있으면 기록이 그래프 실행 구조에 묶인다.** 지금은 첫 노드라 1회지만
   재실행·분기가 생기면 조용히 중복된다.
3. 파일과 DB가 한 지점에서 나오므로 **두 저장소의 건수가 일치**한다.

**CLI 보존이 수용 기준이다.** `src/main.py:71`이 `graph.ainvoke`를 직접 부르는 CLI 모드가 있어,
노드에서 호출을 빼면 그 경로의 감사가 사라진다. 파일 감사 호출을 CLI 진입부로 **옮겨 심는다**.

## Tech Stack

기존 스택 — FastAPI 라우트 · `AuditService`(JSONL + asyncpg) · structlog. **신규 의존성 0건.**

## Commands

```bash
.venv/bin/python -m pytest tests/test_api/test_query_audit_path.py -q
.venv/bin/python -m pytest tests/test_api tests/test_nodes -q   # 회귀
.venv/bin/python scripts/arch_check.py --ci
```

## Project Structure

```
src/api/routes/query.py        → 4개 진입점에 대칭 주입
src/nodes/input_parser.py      → 감사 호출 제거 (노드는 감사하지 않는다)
src/main.py                    → CLI 경로에 파일 감사 이설
src/static/js/admin.js         → 감사 탭에 "인증 off = 전원 anonymous" 표기
tests/test_api/test_query_audit_path.py  → 신규
```

## Code Style

라우트에서의 감사는 **요청 처리를 막지 않는다**. 기존 `user_auth.py:69-80`의 관용을 따른다.

```python
    # 감사: 질의 요청 기록 (D-183 — 노드가 아니라 라우트가 주체다.
    # client_ip·request_id는 여기서만 알 수 있고, 노드는 app.state에 닿지 못한다)
    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service:
        try:
            await audit_service.log_user_request(
                user_id=current_user.get("sub"),
                user_query=body.query,
                output_format=body.output_format or "text",
                has_file=False,
                client_ip=request.client.host if request.client else None,
                session_id=body.thread_id,
            )
        except Exception as e:  # 감사 실패가 질의를 막지 않는다
            logger.warning("감사 기록 실패: %s", e)
```

## Testing Strategy

| # | 대상 | 단언 |
|---|---|---|
| 1 | `query.py` | **4개 진입점 전부**(`/query`·`/query/stream`·`/query/file`·`/query/file/stream`)가 `log_user_request`를 호출한다 — 비대칭 주입은 이 저장소의 반복 실수 유형이다 |
| 2 | `input_parser.py` | 감사 호출이 **없다**(이중 기록 차단) |
| 3 | `main.py` | CLI 경로에 파일 감사 호출이 **있다**(감사 상실 차단) |
| 4 | `AuditService` | `log_user_request` 호출 시 `audit_repo.log_event`가 `event_type="user_request"`로 **1회** 불린다(가짜 repo) |
| 5 | `AuditService` | 같은 호출로 JSONL에도 1건 — 파일과 DB의 건수가 같다 |
| 6 | 라우트 | `audit_service`가 `None`이어도 질의가 정상 처리된다(미초기화 환경) |
| 7 | 라우트 | 감사 기록이 예외를 던져도 질의 응답이 성공한다 |
| 8 | `admin.js` | 감사 탭에 인증 상태 안내 문구가 있다 |

## Boundaries

- **Always**: 감사 실패를 삼키되 `logger.warning`으로 가시화(침묵적 폴백 금지 — CLAUDE.md) ·
  4개 진입점 대칭 · `getattr(app.state, ..., None)` 방어
- **Ask first**: `AUTH_ENABLED` 전환 · `audit_logs` 스키마 변경 · 보존 기간(`AUDIT_RETENTION_DAYS`) 변경 ·
  감사 이벤트 종류 신설
- **Never**: 감사 실패로 질의를 실패시키기 · 파일과 DB에 **중복** 기록 · 질의문을 마스킹 없이
  외부로 전송 · 노드 계층에서 `app.state` 접근

## Success Criteria

1. 질의 1건 → `logs/audit-*.jsonl`에 `user_request` **정확히 1건**.
2. 질의 1건 → DB `audit_logs`에 **1행**(`event_type='user_request'`).
3. CLI(`python -m src.main --query "..."`) 질의도 파일에 1건 남는다.
4. 관리자 "감사 로그" 탭에서 `user_request` 이벤트가 조회된다.
5. `audit_service`가 없거나 감사가 실패해도 질의 응답은 성공한다.
6. `arch_check --ci` exit 0 · 클린 기준선 대비 신규 실패 0.

## Open Questions

- 인증이 켜지기 전까지 DB 이력의 `user_id`는 전부 `anonymous`다. **버그가 아니라 설정 상태**이므로
  관리자 화면에 표기한다(D-179가 설정 UI에 경계를 표시한 것과 같은 방식). `AUTH_ENABLED` 전환은
  이 스펙의 범위 밖(`plans/84` §2.3).
