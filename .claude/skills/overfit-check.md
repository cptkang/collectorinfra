---
name: overfit-check
description: 공용(DB-agnostic) 계층에 특정 DB(폴스타) 스키마 리터럴·운영 도메인이 누수됐는지 탐지하고 기준선 대비 신규 유입을 차단한다
user_invocable: true
---

# 공용 계층 과적합(DB 스키마·운영 리터럴) 검사 스킬

## 개요

이 프로젝트의 공용 계층에 특정 DB(폴스타)의 테이블/컬럼/리소스타입 리터럴이 누수되면,
등록된 비폴스타 DB를 활성화하는 순간 오지시 주입·기능 무력화로 드러난다(Plan 63 §1, D-088).
이 스킬은 그런 리터럴을 스캔해 **기준선(화이트리스트) 대비 신규 유입을 CI 실패**로 막는다.

스캔 대상(Plan 67 R2에서 사각지대 4곳 편입 — `docs/polestar_bias_review.md` §5):
`src/utils`·`src/nodes`·`src/orchestration`·`src/prompts`·`src/document`·`src/schema_cache`·
`src/alarm/domain`·`mcp_server/mcp_server`.
제외: `src/db_adapters/`(어댑터 격리 계층), `mcp_server/mcp_server/polestar_tools.py`(전용 도구).

## 카테고리

- **schema-literal** — "무엇이 어디에 있는가"(테이블/컬럼/리소스타입: `cmm_*`, `server.*`,
  `stat_date`, `core_config_prop`, `stringvalue*`, `resource_conf_id`, `platform_resource_id`,
  `configuration_id`, `polestar.*`). **CI 게이트 대상.**
- **ops-literal** — 특정 고객사/운영 인스턴스의 도메인·엔드포인트(`kbonecloud`·`sotori`,
  사설망 IP). 운영 주소는 코드가 아니라 `.env`에 둔다. **CI 게이트 대상**(기준선 0으로 시작).
- **routing-vocab** — "어느 DB로 보낼 것인가"(위치·별칭 어휘: 김포/여의도/은행/공동존 등).
  Plan 63 §1.3 스코프 아웃 — 분리 집계·가시화만, 게이트 제외. 이 어휘의 정본은
  `config/db_registry.yaml`이다(Plan 67 R2).

## 실행

```bash
python scripts/overfit_check.py                   # 스캔 리포트(카테고리·파일별 집계)
python scripts/overfit_check.py --verbose         # 파일·라인별 상세
python scripts/overfit_check.py --json            # JSON 출력
python scripts/overfit_check.py --ci              # CI: 게이트 대상 신규 유입 시 exit 1
python scripts/overfit_check.py --update-baseline # 잔존분으로 기준선 재생성(리뷰 대상)
```

## 기준선 운용

- 기준선: `scripts/overfit_baseline.json` — 카테고리별(`schema_literal`/`ops_literal`)
  `(파일, 토큰)` 단위 화이트리스트(라인 이동에 둔감).
- **신규 유입**(새 파일·새 토큰)이면 `--ci`가 실패한다. 공용 계층에 DB 특화 리터럴을 두지 말고
  **어댑터/프로필/레지스트리로 이동**하고, 운영 도메인·엔드포인트는 `.env`로 옮긴다.
  의도된 잔존이면 `--update-baseline`으로 갱신(리뷰 필수).
- P2(어댑터 이동)·P3(선언 전환)가 리터럴을 소거하며 기준선을 재생성해 **감소**시킨다
  (감소량이 트랙 완료 지표 — Plan 63 §9 화이트리스트 형해화 방지).

## 언제 실행하나

- 위 스캔 대상 디렉터리의 코드 변경 후.
- 새 DB 편입/어댑터 작업 시 리터럴이 공용으로 새지 않았는지 확인.
- `arch-check`와 함께 코드 변경 승인 전 품질 게이트로 실행.
