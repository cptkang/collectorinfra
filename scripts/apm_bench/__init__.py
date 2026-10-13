"""제니퍼 APM 조회 벤치마크 (plans/152 · D-323).

APM 사용자가 칠 법한 **사용자 프롬프트**(`testdata/apm_bench/scenarios.yaml`)로 2단 경로의
보기·인자·대상·창 선택을 잰다. 시나리오는 `spec/CAPABILITY-MAP-134.md` 표 A(허용 GET 경로)·
표 B(실제 전달하는 선택 인자)와 레지스트리 `solutions[apm].views`를 전수로 덮는다.

단일 진입점은 `python -m scripts.apm_bench` 하나다.
  --dry-run       시나리오 린트·레지스트리 정합·분할 검사 (LLM·네트워크 0)
  --coverage      표 A·표 B·보기·보기 인자 커버리지 → coverage.md (LLM·네트워크 0)
  --run           드라이버 → 판정 → (P층) → 치환 → 근거 → 관문 → 산출물 6종(`__main__` 참조)
  --fewshot-check 예문 과적합 · APM 절 크기 상한
  --compare A B   두 run 산출물 대조
  --replay DIR    실패 턴 분해·재선택 재현(실 MLX · 방향 확인용)

모듈 경계 (plans/152 §3):
  scenarios  시나리오 로더(dataclass) · 자리표 채우기 · 린트 · 정합 · 분할 · 커버리지

**벤치 코드는 `apm_gateway`를 import하지 않는다(D-274)** — 계약은 레지스트리와 대응표뿐이다.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_PATH = REPO_ROOT / "testdata" / "apm_bench" / "scenarios.yaml"
#: 커버리지 원천 — 표 A(§2)·표 B(§3).
CAPABILITY_MAP_PATH = REPO_ROOT / "spec" / "CAPABILITY-MAP-134.md"
RESULTS_ROOT = REPO_ROOT / "results" / "apm_bench"
COVERAGE_DIR = RESULTS_ROOT / "coverage"
#: 레지스트리 솔루션 코드(보기·소스의 정본 — D-053).
SOLUTION = "apm"

__all__ = [
    "REPO_ROOT",
    "SCENARIOS_PATH",
    "CAPABILITY_MAP_PATH",
    "RESULTS_ROOT",
    "COVERAGE_DIR",
    "SOLUTION",
]
