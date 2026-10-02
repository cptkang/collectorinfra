---
name: arch-check
description: 이 저장소의 계층 의존성 위반을 검사하고 수정 방안을 제시한다. 아키텍처 리뷰 요청 또는 코드 변경 후 품질 게이트에 사용한다.
---

# Clean Architecture 의존성 검사

저장소 루트에서 실행한다. Python은 프로젝트 가상환경을 사용한다.

```bash
python scripts/arch_check.py --verbose
python scripts/arch_check.py --ci
```

`--verbose`로 의존성 매트릭스와 위반 목록을 확인하고 `--ci` 종료 코드로 판정한다.
JSON이 필요하면 `--json`을 사용한다. 이 검사는 외부 LLM이나 DB 접속이 필요 없다.

## 판정 기준

실제 규칙의 정본은 `scripts/arch_check.py`의 `MODULE_LAYER_MAP`, `ALLOWED_DEPS`와
위반 판정 코드다. `src/`와 `noise_gate/`를 검사하며 테스트·스크립트 제외도 이 코드에 따른다.
`apm_gateway/`의 패키지 경계는 해당 패키지의 `tests/test_boundary.py`로 별도 확인한다.

계층 순서는 안쪽부터 domain, config/utils, prompts, infrastructure, application,
orchestration, interface, entry다. import는 바깥 계층에서 허용된 안쪽 계층으로 향한다.

- infrastructure에서 application/orchestration/interface로 역참조하지 않는다.
- application에서 orchestration/interface로 역참조하지 않는다.
- domain은 다른 프로젝트 계층에 의존하지 않는다.
- application 간 직접 참조는 스크립트가 warning으로 보고한다. error와 구분한다.
- `db_adapters/`, `tools/`, `semantic/`은 application이다.

## 수행 절차

1. 각 위반의 파일과 import를 직접 확인하고 기존 위반인지 이번 변경인지 구분한다.
2. 수정이 요청된 범위에서는 함수 이동, Protocol/ABC 추출, 콜백·팩토리 주입 중
   실제 계층 책임에 맞는 방법을 적용한다. 검사만 요청받으면 근거와 제안만 보고한다.
3. 하위 계층이 상위 유틸을 참조하면 공유 유틸 또는 해당 인프라로 옮기는 방안을 검토한다.
   구체 구현에 묶이면 안쪽 계층에 계약을 정의하고 바깥 구현을 주입한다.
4. 수정 후 관련 테스트와 `--ci`를 재실행한다. 규칙을 완화해 실패를 숨기지 않는다.
5. 실행 명령, 종료 코드, error/warning 수, 파일·라인, 잔여 문제를 보고한다.
   환경 문제로 검사를 못 했으면 미검증으로 표시한다.

코드 변경 품질 게이트에는 `$overfit-check`도 함께 적용한다.
