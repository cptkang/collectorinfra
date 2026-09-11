# plan-94 — 기능·성능 시나리오 자동 실행 하네스 구현 계획

정본 스펙: `plans/94-WIP-feature-perf-scenario-suite.md`
실행 가이드 정본: 같은 문서 맨 앞 「실행 가이드」 절

## 0. 이번 착수의 범위 경계

| Wave | 범위 | 과금 | 이번 착수 |
|---|---|---|---|
| S0 | 커버리지 매트릭스 `docs/30_scenario_coverage.md` | 0 | **포함** |
| S1 | 문서 145건 → YAML 이관(초안) + 단언 작성 | 0 | **포함(초안 전건 + 단언 부분)** |
| S1b | R군 75건 신규 작성 | 0 | **포함(골격·대조군 쌍·대표 케이스)** |
| S2 | 러너 골격 | 0 | **포함** |
| S3 | 리포트 생성기 | 0 | **포함** |
| S4 | 분석기 | 0 | **포함** |
| S5 | 개발망 소규모 실행 | 소 | **제외 — D-127 승인 대상** |
| S6 | 폐쇄망 전 스위트 | 대 | **제외 — D-127 승인 대상** |
| S7 | 반영(docs/29 전환·FI 승격·D-212 등재) | 0 | **제외 — S6 산출 필요** |

**S0~S4는 전부 무과금이다.** 실 LLM 호출은 한 건도 하지 않는다.

## 1. 게이트 미확정 상태에서의 진행 규칙

G-1~G-12는 미확정이다. 코드는 **계획서의 권고안을 기본값으로 삼되 설정으로 뒤집을 수 있게** 만든다.

| 게이트 | 코드에 박는 기본값 | 뒤집는 방법 |
|---|---|---|
| G-1 정본 환경 | `--env` 선언 필수 · 리포트에 환경 명기 | 인자 |
| G-3 인증 | 토큰 미보유 시 에코 검증을 `unverified`로 남김(침묵 통과 금지) | `--token` |
| G-4 승인 단위 | 스위트 1회 = 승인 1건. `--estimate` 출력이 승인 근거 | — |
| G-5 서술 품질 | LLM-as-judge 미구현. L3는 `manual` | 별건 |
| G-8 HTML | Markdown+JSON 기본, HTML은 `--html` | 인자 |
| G-9 FI 등재 | 분석기는 제안 문서만. `docs/17` 미수정 | — |
| **G-10 대응 등급** | **`policy_confirmed: false` → R군은 `manual`**. 금지 3종만 즉시 불합격 | 카탈로그 군 헤더의 `policy_confirmed: true` |
| G-11 1차 범위 | `--group`으로 선택 | 인자 |
| G-12 Windows | **1급** — W1~W9 전건 구현 | — |

## 2. 계획서 실측 정정 (구현 중 확인)

- **§4.2-2 에코 엔드포인트 정정**: `GET /api/v1/admin/settings`는 *".env에 실존하는 키만"* 돌려주는
  DEPRECATED 평면 목록이다(`admin.py:535`). 주입은 OS env로 하는데 `.env`에 없는 키는 **에코에 안 나온다** —
  주입 무시를 잡아야 할 장치가 주입 자체를 못 본다. 정본은
  **`GET /api/v1/admin/settings/schema`**(`admin.py:566`)다. `build_catalog`가
  `groups[].settings[].{env_key, effective_value, override}`를 주고 `override="os"`로
  **OS env 주입이 실제로 먹었는지**까지 알려준다(`settings_catalog.py:1029`).
- 두 엔드포인트 모두 `require_admin_user`. `AUTH_ENABLED=false`면 무인증 통과(`dependencies.py:203`).
  켜져 있으면 토큰 필요 → 미보유 시 `echo=unverified`로 남기고 INVALID로 단정하지 않는다.
- SSE `done`에는 `status` 키가 없다. 역질문은 `clarification` 키 존재로 판정한다(`query.py:1311`).

## 3. 구현 순서와 검증점

```
1. profiles.yaml + _schema.yaml        -> verify: 카탈로그 로더 테스트가 이 파일을 읽는다
2. catalog.py                          -> verify: V1·V2·V16 테스트
3. assertions.py                       -> verify: V15·V17·V18 테스트
4. client.py (HTTP/SSE)                -> verify: SSE 파서 단위 테스트(네트워크 0)
5. server.py (플랫폼 분기 W1~W9)        -> verify: 포트 선정·종료 분기 단위 테스트
6. mockserver.py                       -> verify: --mock 전 경로(V12)
7. runner.py (JSONL·resume)            -> verify: V11 재개 테스트
8. report.py (11절 고정)                -> verify: V8·V9 테스트
9. analyze.py (6종 산출)                -> verify: V10·V18 테스트
10. __main__.py (D-127 하드 게이트)      -> verify: RUN_E2E 미설정 시 --run 즉시 종료
11. import_docs.py + 초안 생성          -> verify: 145건 파싱 건수 대조
12. R군 카탈로그(대조군 쌍)              -> verify: V16 로더 거부 확인
13. docs/30 커버리지 매트릭스            -> verify: V3 역집계 대조
14. 품질 게이트                          -> verify: arch_check·overfit_check·ruff·pytest
```

## 4. 지켜야 할 제약 (위반 시 되돌린다)

- `src/` 수정 0. 러너는 `scripts/scenario/` 안에만 산다.
- 실 LLM 호출 0. `--run`은 `RUN_E2E=1` 하드 게이트 뒤(`eval_routing.py:46` 패턴).
- 모든 파일 쓰기 `encoding="utf-8"` **와** `newline="\n"` 동시 명시(W4).
- 콘솔 출력은 ASCII 구두점만(W5 · cp949).
- 경로는 전부 `pathlib`(W8). 기준 URL은 `127.0.0.1` 고정(W2).
- 신규 `enable_*` 플래그 0(D-162). 프로파일은 기존 env 키 조합일 뿐이다.
- DB 쓰기 경로 0(D-003).
