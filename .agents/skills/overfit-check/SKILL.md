---
name: overfit-check
description: 공용 계층에 특정 DB 스키마와 운영 주소가 누수되는지 검사한다. 공용 코드 변경, 새 DB·어댑터 편입 또는 과적합 리뷰 시 기준선 대비 신규 유입을 확인한다.
---

# 공용 계층 과적합 검사

저장소 루트와 프로젝트 Python 환경에서 실행한다. 외부 LLM·DB 접속은 필요 없다.

```bash
python scripts/overfit_check.py --verbose
python scripts/overfit_check.py --ci
```

집계만 필요하면 옵션 없이, 기계 판독 결과는 `--json`으로 확인한다.

## 검사 범위와 카테고리

정확한 스캔 범위·제외 경로·탐지 패턴은 `scripts/overfit_check.py`를 읽어 확인한다.
공용 src 계층뿐 아니라 noise_gate·sre_agent 도메인, 범용 MCP·APM 경로도 포함된다.
벤더 어댑터·전용 도구의 제외 여부를 경로 이름만으로 추정하지 않는다.

- schema-literal: 특정 DB 테이블·컬럼·리소스타입. CI 게이트 대상.
- ops-literal: 고객사·운영 인스턴스 도메인·사설 IP. CI 게이트 대상.
- routing-vocab: DB 위치·별칭 어휘. 별도 집계하며 CI 게이트에서 제외한다.
  이 어휘의 정본은 `config/db_registry.yaml`이다.

## 기준선과 수정

`scripts/overfit_baseline.json`은 카테고리별 `(파일, 토큰)` 기준선이다.
라인 이동보다 새 파일·토큰의 유입을 판정하며 독스트링도 검사 대상이다.

1. 신규 탐지와 기존 잔존을 구분하고 해당 코드·문맥을 직접 확인한다.
2. DB 특화 지식은 어댑터·프로필·레지스트리에, 운영 주소는 환경 설정에 둔다.
3. 검사 요청이면 근거와 제안만 보고한다. 수정 요청이면 관련 범위만 고친 뒤
   관련 테스트와 `--ci`를 다시 실행한다.
4. 기준선의 전면 재생성은 금지한다. `--update-baseline`으로 실패를 통과시키지 않는다.
   소거한 자기 델타만 줄이며, 의도된 예외 추가는 근거·변경량을 리뷰받는다.
5. 실행 명령·종료 코드, 카테고리별 신규 유입, 파일·토큰, 잔여 문제를 보고한다.
   실행할 수 없었다면 통과로 보고하지 않는다.

관련 근거: `AGENTS.md` 품질 게이트, `docs/02_decision.md` D-088·D-179,
`docs/polestar_bias_review.md`. 코드 변경 시 `$arch-check`와 함께 실행한다.
