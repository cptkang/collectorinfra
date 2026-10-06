"""ITAM 자산 질의 벤치마크 (plans/135 · D-301).

자산관리 포탈 사용자가 칠 법한 **사용자 프롬프트**를 로그인한 사용자와 같은
경로(`/api/v1/query/stream`)로 보내고, 턴마다 시스템이 만든 SQL · 사용자가 받은 결과(가림) · LLM에
제시된 스키마 맥락(구조화)을 남겨 ITAM에 실제로 있는 값(읽기 전용 오라클)과 대조한다. 실패 분류가
고칠 ITAM 프롬프트 자산을 가리킨다.

단일 진입점은 `python -m scripts.itam_bench` 하나다.
  --dry-run       시나리오·프롬프트 린트·실행 계획만 (LLM·DB 0)
  --check-oracle  정답 SQL만 읽기 전용으로 실행 (LLM 0)
  --run           벤치 서버 기동 → 로그인 → 시나리오 → 산출물 (비과금 평면에서만)
  --compare A B   두 run 의 시나리오별 전이 (LLM·DB 0)
  --sync RUN      반출 run 의 카탈로그 ↔ 로컬 전사본·컬럼 정책 차이 (LLM·DB 0)

모듈 경계 (plans/135 §3.1):
  catalog   시나리오·컬럼 정책 로드·검증 · 프롬프트 린트 · 스키마 카탈로그
  redact    값 기록 정책 · SQL 리터럴·오류 문구 가림 · 계정·실행자·경로 가림 · 누출 관문
  judge     오라클 대조 어댑터 · SQL 분석 · 실패 분류
  report    report.md · --compare
  _serve    벤치 서버 진입(측정 수신기 설치)

**로그 위생은 기본 거부다** — 검토된 일반 컬럼만 값을 남기고 사람 정보는 건수만 남긴다(D-301 ②).
시나리오 하네스 러너(`scripts/scenario/runner.py`)는 쓰지 않는다 — 응답 원문을 `raw.jsonl`에 남긴다.
운영 경로에 배선되지 않는다.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_PATH = REPO_ROOT / "testdata" / "itam_bench" / "scenarios.yaml"
POLICY_PATH = REPO_ROOT / "testdata" / "itam_bench" / "column_policy.yaml"
#: 폐쇄망(운영) 기본값 — `--env closed` 이면 이 둘을 쓴다(plans/135 W8 · 1회차 = 관찰).
CLOSED_SCENARIOS_PATH = REPO_ROOT / "testdata" / "itam_bench" / "scenarios.closed.yaml"
CLOSED_POLICY_PATH = REPO_ROOT / "testdata" / "itam_bench" / "column_policy.closed.yaml"
#: 반출 후 싱크 대조의 로컬 기준 — 샌드박스 전사본.
TRANSCRIPT_PATH = REPO_ROOT / "testdata" / "itam" / "schema.yaml"
RESULTS_ROOT = REPO_ROOT / "results" / "itam_bench"
DB_ID = "itam"

__all__ = [
    "REPO_ROOT",
    "SCENARIOS_PATH",
    "POLICY_PATH",
    "CLOSED_SCENARIOS_PATH",
    "CLOSED_POLICY_PATH",
    "TRANSCRIPT_PATH",
    "RESULTS_ROOT",
    "DB_ID",
]
