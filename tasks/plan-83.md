# 구현 계획 — Plan 83 (피드백 루프 · 표시 레벨 · 설정 UI 커버리지)

> SPEC: `SPEC-alarm-feedback-loop.md` · `SPEC-alarm-view-level.md` · `SPEC-settings-ui-coverage.md`
> 맵: `CAPABILITY-MAP-83.md` · 요구 근거: `plans/83` · 결정: D-177~179(예약)
> 게이트 확정(2026-08-28): G-1 **localStorage** · G-2 기본 **dashboard** · G-3 note **신설** ·
> G-4 비관리자 suppress **조용한 강등** · G-5 철회 **tombstone append**

## 의존 그래프

```
[settings-ui-coverage]
T1 섹션 매핑 + 누락 감지 테스트                    ← 의존 없음
        │
[alarm-feedback-loop]                              (T2·T3·T4는 서로 독립 — 병렬 가능)
T2 피드백 존 RBAC ★보안                            ← 의존 없음
T3 ack 존 RBAC (+ IncidentStore.get_db_id)         ← 의존 없음
T4 feedback_store 확장(labeled_by·retract·tail·회전) ← 의존 없음
        │
        ├─→ T5 capabilities 엔드포인트              ← 의존 없음 (T2와 같은 파일 → 순차 편집)
        ├─→ T6 결정적 pattern 배선 + blocking 회피  ← T4
        └─→ T7 카드 UI(필드 보강·note·취소·게이팅)   ← T4·T5·T6
                │
[alarm-view-level]
T8 플래그 + .env + 구획 등재                        ← T1(누락 감지 테스트가 먼저 있어야 의미)
        └─→ T9 SUPPRESS 발행 분기                   ← T8
                └─→ T10 스트림 관리자 판정          ← T9
                        └─→ T11 레벨 셀렉트·필터 UI ← T10·T5
[마감]
T12 경계 안내 + 시크릿 배지                         ← T1
T13 피드백 summary API + 관리자 탭                  ← T4
T14 문서 갱신(docs/28) + 결정 등재(D-177~179)       ← 전체
```

## 실행 순서와 근거

| 차수 | 태스크 | 근거 |
|---|---|---|
| 1 | **T2 → T3** | 유일한 보안 결함(다른 존 알람에 라벨·ack 가능). 가장 먼저 닫는다 |
| 2 | T1 | T8이 추가하는 신규 키가 걸릴 **그물을 먼저** 친다(모듈 간 의존의 실체) |
| 3 | T4 → T5 → T6 | 저장소 계약 확정 후 소비처 배선. T5는 T2와 같은 파일이라 순차 |
| 4 | T7 | 프런트는 서버 계약이 굳은 뒤 |
| 5 | T8 → T9 → T10 → T11 | 표시 레벨. 플래그 → 발행 → 권한 판정 → UI 순 |
| 6 | T12 → T13 → T14 | 마감 |

## 위험과 완화

| 위험 | 완화 |
|---|---|
| **병렬 작업 충돌** — `plans/81`·`82`가 `src/api/routes/alarm.py`·`src/config.py`를 동시 편집 중(작업 트리에 미커밋 변경 존재) | 편집 직전 해당 파일 `git diff` 재확인 · 삽입 위치를 파일 말미/전용 섹션으로 잡아 충돌면 최소화 · 같은 파일 태스크는 순차 실행 |
| **tail 전환이 조회 결과를 바꿈**(T4) | 상한 = 읽기 창으로 두어 회전 전까지 **결과 동일**. 20k 픽스처로 전체 스캔과 등가성 단언 |
| **기본 레벨 변경이 회귀를 부름**(T11) | G-2 확정대로 기본 `dashboard` — 현행과 동일 집합. 테스트로 고정 |
| **억제 알람이 비관리자에 도달**(T10) | 서버 판정으로 차단. 클라이언트 필터에 의존하지 않음(SPEC-B §권한과 선호의 분리) |
| **db_id 없는 기존 피드백 요청 거부**(T2) | 하위호환 — db_id 미동반은 존 무판정 통과. 강제 전환은 별도 스위치 |
| **arch_check 위반**(feedback_store에 외부 import) | 표준 라이브러리만 유지. 각 태스크 verify에 `arch_check --ci` 포함 |

## 검증 체크포인트

- **차수 1 종료**: 존 격리 테스트 통과 + 전건 회귀 0 → 보안 결함 종결
- **차수 3 종료**: `find_similar` 등가성·회전·철회 테스트 통과
- **차수 5 종료**: 플래그 off 비트 동일 + 4조합(플래그×role) 도달 매트릭스 일치
- **최종**: `pytest tests/ noise_gate/ -q` 전건 · `arch_check --ci` 0위반 · `docs/28` 갱신 반영

## 병렬 가능성

- T2·T3·T4는 서로 독립(다른 파일) — 단 T2·T3·T5는 `alarm.py` 공유라 **순차 편집**한다.
- T1은 어느 시점에나 독립 실행 가능(다른 모듈과 파일 무교차).
