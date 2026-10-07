---
name: arch-check
description: Clean Architecture 계층 간 의존성 규칙 위반을 자동 탐지하고 수정 방안을 제시한다
user_invocable: true
---

# Clean Architecture 의존성 규칙 검사 스킬

## 개요

이 프로젝트의 `src/` 디렉토리에 정의된 Clean Architecture 계층 구조를 분석하여 의존성 방향 위반을 탐지한다.

## 계층 구조 (안쪽 → 바깥쪽)

```
domain (src/state.py)              — 핵심 상태, 값 객체. 의존 없음
  ↑
config (src/config.py)             — 설정. 의존 없음
utils (src/utils/)                 — 공유 유틸. 의존 없음
prompts (src/prompts/)             — LLM 프롬프트. utils만 참조 가능
  ↑
infrastructure                     — DB, LLM, 캐시, 보안, 문서, 라우팅
  (src/db/, src/dbhub/, src/llm.py, src/clients/,
   src/security/, src/schema_cache/, src/document/, src/routing/)
  → domain, config, utils, prompts, infrastructure(같은 레벨) 참조 가능
  ↑
application (src/nodes/)           — LangGraph 노드 (유스케이스)
  → domain, config, utils, prompts, infrastructure 참조 가능
  ↑
orchestration (src/graph.py)       — 그래프 빌드 (노드 조합)
  → domain, config, utils, application, infrastructure 참조 가능
  ↑
interface (src/api/)               — FastAPI 어댑터
  → domain, config, utils, orchestration, infrastructure, application 참조 가능
  ↑
entry (src/main.py)                — 진입점
  → 모든 계층 참조 가능 (config, orchestration, interface, infrastructure)
```

## 핵심 금지 규칙

1. **infrastructure → application**: 인프라가 유스케이스를 참조하면 안 됨
2. **infrastructure → orchestration**: 인프라가 그래프를 참조하면 안 됨
3. **infrastructure → interface**: 인프라가 API를 참조하면 안 됨
4. **application → orchestration**: 노드가 그래프를 참조하면 안 됨
5. **application → interface**: 노드가 API를 참조하면 안 됨
6. **application → application** (노드 간 직접 의존): 노드가 다른 노드를 직접 import하면 안 됨
7. **domain → 모든 외부**: 도메인은 어디에도 의존하면 안 됨

## 실행 방법

### 자동화 스크립트
```bash
python scripts/arch_check.py              # 기본 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --json       # JSON 출력 (CI 연동)
python scripts/arch_check.py --ci         # 위반 시 exit 1
```

### 이 스킬 호출 시 수행 절차

1. `python scripts/arch_check.py --verbose` 실행하여 위반 목록 수집
2. 각 위반에 대해 해당 파일의 import 라인을 직접 확인
3. 위반 유형별 수정 방안 제시:
   - **함수 위치 이동**: 하위 계층 함수가 상위 계층에 있으면 이동
   - **인터페이스 추출**: Protocol/ABC로 의존성 역전
   - **파라미터 주입**: 상위 계층 로직을 콜백/팩토리로 주입
4. 수정 적용 후 재검사하여 위반 해소 확인

## 일반적 수정 패턴

### 패턴 A: 함수를 올바른 계층으로 이동
위반: `infrastructure`가 `application`의 유틸 함수를 import
수정: 해당 함수를 `utils/` 또는 같은 `infrastructure` 모듈로 이동

### 패턴 B: 의존성 역전 (DIP)
위반: `infrastructure`가 `application`의 구체 클래스를 import
수정: `infrastructure`에 Protocol을 정의하고, `application`에서 구현

### 패턴 C: 콜백 주입
위반: 하위 계층이 상위 계층 로직에 의존
수정: 상위 계층에서 함수/콜백을 파라미터로 전달
