# 알려진 오류(KEDB) 레코드 — 런타임 디렉토리 (plans/187 W8 §5.7.3 · D-341 ⑨)

기동 시 `sre_agent.infrastructure.known_error_loader`가 이 디렉토리의 `*.yaml`만 파일명 순으로 읽는다
(이 README는 적재 대상이 아니다). 비어 있으면 알려진 오류 판정이 없다 — 정상이다.

## 추가 방법

- **저장소 YAML + 리뷰로만** 추가·변경한다. 런타임은 이 디렉토리에 쓰지 않는다(읽기 전용).
- 본체 KB 초안(wip)은 여기로 자동 승격되지 않는다 — 사람이 검토해 파일로 옮긴다.
- 스키마를 통과하지 못한 파일은 그 파일만 비활성되고, 브리핑 notes에 「설정 파일 거부」로 드러난다.

## 스키마 요지 (`sre_agent/domain/known_error.py` `KnownError`)

| 키 | 필수 | 내용 |
|---|---|---|
| `id` | 예 | `KE-...` 형식 · 디렉토리 안 유일 |
| `title` · `issue` · `cause` | 예 | 사람 서술(종합 소견 입력이 아니다) |
| `state` | 예 | `wip` · `not_validated` · `validated` · `archived` — 가설 1순위 고정은 `validated` 완전 일치만 |
| `governance` | | `experience`(기본) · `compliance` |
| `environment` | 예 | `playbook`(플레이북 ID) · `targets`(solution·instance_glob 등) · `time_window` |
| `match` · `cause_test` | 예 | 플레이북 조건식(`all`/`any`) — 스텝 결과(`S2.pool`)·판독(`db_split`) 경로 |
| `workaround[]` | | `action`·`risk`·`verification`·`rollback` — 명령 리터럴 금지 |
| `permanent_fix` | | `status`·`problem_ref` |
| `owner` · `last_reviewed` | validated면 예 | 검토 책임자 · 최종 검토일 |
| `review_every_days` | | 검토 주기(기본값은 스키마) |

## 예시

형식 예시(가짜 리터럴)는 런타임 경로가 아니라 테스트 픽스처에 둔다:
`sre_agent/tests/fixtures/plan187/known_errors/KE-EXAMPLE-001.yaml`.
