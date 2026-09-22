# Spec: middleware-os-identification

> Module id: `middleware-os-identification` · WU-20 (`plans/80` §5.2 **자유 축 · 게이트 없음**)
> 설계: `plans/78` **W7-1단계**(§4.7.1-A) · D-168 · 관련 D-035(결정적=판단) · D-118(`sre_agent` 경계)
> **Phase 0 판정**: 단일 capability — 분해하지 않는다(프로파일 명세와 식별 규칙은 분리 불가).

## Objective

**대상 호스트에서 돌고 있는 미들웨어가 무엇인지 OS 레벨 신호만으로 결정적으로 식별한다.**

*왜 지금*: 미들웨어 장애의 정본 소스는 APM(W7-2)이지만 **도입 시점·벤더가 미정**(R-11)이다.
OS 근사는 **선행조건이 0**이라 APM 일정과 무관하게 착수할 수 있고, APM 도입 **후에도 존치**한다
(§4.7.1) — ①APM 에이전트가 붙지 않은 레거시·경량 미들웨어가 남고 ②**APM 자체가 죽었을 때**
볼 수단이 필요하다(관측 도구의 장애를 관측 도구로만 진단할 수 없다).

*사용자*: `sre_agent`의 장애 조사 경로와, 그 결과를 소비하는 78의 브리핑.
*성공*: 프로세스 목록만 주면 **미들웨어 종류·인스턴스·기동 인자 핵심값**이 나오고, 못 찾으면
**왜 못 찾았는지가 남는다**.

## 범위

| 포함 | 제외 |
|---|---|
| 미들웨어 **식별 규칙 선언 파일** | ~~`profile="middleware"` 구현~~ → **범위 편입**(v2 · 사용자 지시 2026-08-27 — `sre_agent`가 이 저장소 안에 있다) |
| 선언 파일을 읽어 판정하는 **결정적 매처** | 조사 결과 **소비·표시** → W4(WU-19) 종속 · 이번 범위 밖 |
| `profile="middleware"` **요구 명세 문서** | APM 연계 일체 → W7-2(R-11 대기) |
| **`sre_agent`에 `middleware_profile()` 구현**(v2 편입) | 원격(중앙 실행) 모드 확장 → `remote_vm_profile` 규약 유지 |
| 선행 실측 2항 문서화(수용 기준) | `VM_DIAG_ALLOW` 확장 — **불필요함이 실측 확인됨** |

## Tech Stack

Python ≥3.11 · pydantic 2.12.5 · PyYAML(기보유) · pytest 8. **신규 의존 0.**

## Commands

```bash
.venv/bin/python -m pytest tests/test_middleware/ -q     # 대상 스위트
.venv/bin/python -m pytest -q --ignore=tests/e2e         # 전체 회귀
python scripts/arch_check.py --ci                        # 계층 검사 (exit 0 필수)
```

## Project Structure

```
config/middleware_signatures.yaml   선언적 식별 규칙 (정책은 코드가 아닌 파일에 — 하네스 표 29 G)
src/domain/middleware.py            결정적 매처 + 결과 모델        (domain 계층)
docs/24_middleware_profile_spec.md  sre_agent profile="middleware" 요구 명세
tests/test_middleware/              단위 테스트
```

> **계층 근거**: 매처는 외부 I/O가 없는 **순수 판정 로직**이므로 `domain`이다
> (`arch_check` 매핑 확인 필요 — 없으면 `src.domain` 항목 사용). 선언 파일 로딩만 수행하며
> 프로세스 수집은 하지 않는다 — 수집은 `sre_agent` 소관이다.

## Code Style

```python
def identify(processes: Sequence[ProcessInfo]) -> MiddlewareScan:
    """프로세스 목록에서 미들웨어를 식별한다. 판정은 100% 결정적이다(D-035).

    LLM을 쓰지 않는다 — 같은 입력에 항상 같은 출력이어야 조사 결과를 신뢰할 수 있고,
    미식별이 "모델이 못 맞혔다"가 아니라 "규칙에 없다"로 귀결되어야 규칙을 고칠 수 있다.
    """
```

한국어 docstring·주석, 근거는 계획서·결정 번호로 인용. 기존 코드 관례를 따른다.

## Testing Strategy

`tests/test_middleware/test_identification.py`. **실 호스트·실 명령 없이** 검증한다 —
`ps` 출력 텍스트를 픽스처로 넣고 판정 결과를 단언한다. 실 LLM 0건(D-127 무관).

## Boundaries

- **Always**: 식별은 결정적 · 미식별 시 **사유를 남긴다**(빈 결과 금지) · 규칙은 **선언 파일에만**
  (코드에 하드코딩 금지) · 변경 후 `pytest -q` + `arch_check --ci`
- **Ask first**: `VM_DIAG_ALLOW` 확장(이번엔 **불필요**) · `sre_agent` 코드 수정 · 신규 의존
- **Never**: 본체가 `sre_agent`를 **import**(D-118 · 양방향 import 0 — **편집은 허용, import는 금지**) · 실 호스트 명령 실행 ·
  LLM으로 미들웨어 종류 추정(D-035)

## Success Criteria

| # | 조건 |
|---|---|
| **S1** | 프로세스 목록을 주면 **미들웨어 종류·인스턴스 식별자·기동 인자 핵심값**(힙 설정 등)이 나온다 |
| **S2** | 같은 입력에 **항상 같은 출력**(결정성 단언) · LLM 호출 **0회** |
| **S3** | 규칙이 **선언 파일에만** 있다 — 코드에 미들웨어명 하드코딩 0(테스트로 단언) |
| **S4** | 미식별 시 **사유가 구조화되어 남는다**(`unmatched: [{pid, cmdline, reason}]`) — 빈 결과 금지 |
| **S5** | 선언 파일에 **규칙을 추가하는 것만으로** 새 미들웨어가 식별된다(코드 변경 0 · 테스트로 실증) |
| **S6** | `profile="middleware"` 요구 명세가 문서로 존재하고 **`VM_DIAG_ALLOW` 확장 0**임을 근거와 함께 명시 |
| **S7** | 선행 실측 2항(OS 레벨 식별 가능성 · 대상 미들웨어 종류)이 **문서로 남는다**(W7-1 수용 기준) |
| **S8** | `sre_agent`를 import하지 않는다(D-118) · `arch_check --ci` exit 0 |
| **S9** | 전체 회귀 기준선 동일 |
| **S10** | (v2) `sre_agent.middleware_profile()`이 존재하고 **allowlist가 `vm_profile`과 동일**하다 — 확장 0 단언 |
| **S11** | (v2) 미들웨어 조사 초점 지침이 **수집 순서·부하 가드·판정 위임**을 담는다 |
| **S12** | (v2) `sre_agent` 자체 스위트가 통과한다(`cd sre_agent && pytest`) |

## Open Questions

| # | 항목 | 처리 |
|---|---|---|
| **Q1** | **조직의 실제 미들웨어 종류**를 알 수 없다 | 국내 엔터프라이즈 관행 기준 **기본 규칙 세트**를 싣고 확장 방법을 문서화한다. 실제 목록 확정은 **사용자 확인 대상**이며, S5(선언적 확장)가 성립하면 확인 지연이 착수를 막지 않는다 |
| **Q2** | 한 호스트에 **같은 미들웨어 인스턴스가 여럿**일 때 구분 키 | 포트·인스턴스명·`-D` 기동 인자로 구분한다(§4.7.3 체크리스트 2항과 같은 문제). APM 편입 시 재사용 |
| ~~Q3~~ | 매처 계층 | **해소** — `arch_check.py:43` `"src.domain": "domain"`이 이미 매핑돼 있다. 등재 불요 |
