# Spec: 벤치마크 전수 설정 검증 (bench-catalog · bench-probe · bench-validate · bench-report · bench-cli)

> **계획서** `plans/93` · **맵** `CAPABILITY-MAP-93.md` · **결정 예약** D-211 · 작성 2026-09-11

## Objective

**335개 설정 필드 전건**이 멀쩡한지 기계로 확인하고, 그 결과를 사람이 읽는 보고서로 낸다.
성능 측정(L5)은 실 LLM이 필요하므로 이번 범위 밖이고, **LLM을 한 번도 부르지 않는 L1~L4**가 대상이다.

사용자는 *"환경변수가 너무 많아 설정하기 너무 어렵다"* 고 했다. 그 어려움의 상당 부분은 성능이 아니라
**"이 키가 먹기는 하나 · 이 값을 넣으면 뜨나 · 바꿨는데 왜 안 바뀌나 · 파일에만 있는 키 아닌가"** 다.
이 스펙은 그 네 질문에 전수로 답한다.

## Tech Stack

Python ≥3.11 · 표준 라이브러리 + `pydantic-settings`(기존) · 신규 의존 **0**.
재사용: `src.api.settings_catalog`(D-129 SSOT) · `subprocess`(격리 실행).

## Commands

```bash
python -m scripts.bench --preflight          # 환경 사전점검
python -m scripts.bench --quick              # L1·L3 (초 단위)
python -m scripts.bench --quick --ci         # 위와 같되 문제 시 exit 1
python -m scripts.bench                      # L1~L4 전수 (기본)
python -m scripts.bench --show-env           # 현재 실효 프로바이더·설정 출처

pytest tests/test_scripts/test_bench_*.py -q # 테스트
python scripts/arch_check.py --ci            # 계층 게이트
python scripts/overfit_check.py --ci         # 리터럴 누수 게이트
```

## Project Structure

```
scripts/bench/__init__.py     공개 API 재노출
scripts/bench/catalog.py      bench-catalog — 카탈로그 어댑터·F1 필터·L1 정합
scripts/bench/probe.py        bench-probe   — 자식 프로세스 실효값 에코
scripts/bench/validate.py     bench-validate— L1~L4 실행·결과 모델
scripts/bench/axes.py         bench-axes    — 축 자동 선별·arm 전개
scripts/bench/report.py       bench-report  — 건강 보고서·커버리지 장부·판정
scripts/bench/__main__.py     bench-cli     — 단일 진입·preflight
tests/test_scripts/test_bench_*.py           단위 테스트
results/bench/<run_id>/                      산출물(생성물 · git 미추적)
```

## Code Style

기존 `scripts/` 관행을 따른다 — 모듈 docstring에 목적·사용법, 한국어 주석, `from __future__ import annotations`,
`@dataclass(frozen=True)` 결과 모델, 순수 함수 우선.

```python
@dataclass(frozen=True)
class ShadowedKey:
    """`.env` 값이 상위 소스에 가려져 무효인 키 (L3)."""

    env_key: str
    file_value: str | None
    effective_value: str | None
    source: str  # "os_env" | "encenv"
```

## Testing Strategy

`pytest` · `tests/test_scripts/` · **실 프로세스·실 네트워크 없이** 돈다.
- `bench-probe`는 자식 프로세스를 띄우므로 **자기 자신을 대상으로 한 경량 에코**만 실제로 실행하고,
  나머지는 주입 가능한 러너로 대체한다.
- 파일 입출력은 `tmp_path`를 쓴다. **저장소의 `.env`를 절대 쓰지 않는다.**
- 커버리지 목표는 두지 않되, **각 층(L1~L4)의 판정 분기마다 테스트 1건 이상**을 둔다.

## Boundaries

- **Always**: 읽기 전용 · 산출물은 `results/bench/` 아래만 · 결과에 시크릿 값 미기록(키 이름만) ·
  미측정 항목에 사유 필수
- **Ask first**: `src/` 수정 · 신규 의존 추가 · 실 LLM 호출
- **Never**: `.env`·`.encenv`·`config.py` 쓰기 · 신규 config 필드/플래그 추가 · 시크릿 값 출력

## Success Criteria

| # | 조건 | 검증 |
|---|---|---|
| S1 | `field_index()` 전건(335)이 커버리지 장부에 한 줄씩 들어간다 | 장부 행 수 == 카탈로그 필드 수 |
| S2 | 미측정(⚪)에 사유가 비면 리포트 생성이 **실패**한다 | 사유 제거 입력 → 예외 |
| S3 | `.env`에만 있고 코드에 없는 키를 **고아 키**로 잡는다 | 합성 `.env`에 미지 키 → 검출 |
| S4 | OS env가 `.env`를 덮는 키를 **가림**으로 잡는다 | `monkeypatch.setenv` → 검출 |
| S5 | 기동을 깨뜨리는 값을 L2가 잡고 **오류 메시지를 보존**한다 | 잘못된 타입 주입 → `ValidationError` 원문 기록 |
| S6 | 값을 바꿔도 동작이 불변인 키를 L4가 **노브 환상 후보**로 분류하고 `UNCONSUMED_KEYS`와 대조한다 | 대조표 3분류 산출 |
| S7 | 어떤 경로로도 `.env`·`config.py`가 수정되지 않는다 | mtime 불변 단언 |
| S8 | 내부망(`fabrix`)이면 승인 프롬프트가 없고, 외부 프로바이더면 뜬다 | 프로바이더별 판정 테스트 |
| S9 | 기존 게이트 무회귀 | `arch_check --ci` 0 · `overfit_check --ci` 0 · 기준선 대비 신규 실패 0 |

## Open Questions

없음 — 계획서 §11 게이트는 권고안으로 확정하고 진행한다. 이견이 생기면 계획서를 먼저 고친다.
