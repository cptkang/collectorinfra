"""MVP 테스트 실행 기록기 — 레벨 B(실 LLM 조사 완주) 측 (Plan 66 · docs/23).

`noise_gate/tests/mvp_record.py`와 **같은 출력 계약**(runs.jsonl + 대장 1행)을 쓰지만
모듈은 공유하지 않는다 — `sre_agent`는 별도 venv·양방향 import 0 경계(D-118/D-139)라
collectorinfra 모듈을 import할 수 없다. **공유하는 것은 모듈이 아니라 파일 계약**이며,
이는 `mcp_server`가 자체 미니 SQL 로거로 같은 `logs/sql/`에 append하는 D-140 전례와
같은 판단이다.

레벨 A와 달리 여기는 **실 LLM 호출이 일어나는 쪽**이라, 백엔드·모델·엔드포인트를 지문에
반드시 남긴다 — 나중에 "그때 어떤 모델로 완주했는가"가 판정의 핵심이 된다.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

TOP = Path(__file__).resolve().parents[1]        # collectorinfra/sre_agent/
REPO = TOP.parent                                 # collectorinfra/
RUNS_JSONL = REPO / "logs" / "mvp_test" / "runs.jsonl"
LEDGER = REPO / "logs" / "mvp_test" / "mvp_test_log.md"

LEVEL = "B(real·조사완주)"


def _sh(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 — 지문 수집 실패가 테스트를 깨뜨리면 안 된다
        return ""


def env_fingerprint() -> dict:
    """조사 LLM 지문 — 키 값은 절대 남기지 않고 '설정 여부'만 남긴다."""
    fp: dict = {
        "git_commit": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "git_branch": _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(_sh(["git", "status", "--porcelain"])),
        "run_e2e": os.environ.get("RUN_E2E", ""),
    }
    try:
        from sre_agent.settings import AgentSettings

        s = AgentSettings()
        fp["llm"] = {
            "investigation_llm_model": s.investigation_llm_model,
            "api_base": s.api_base or "(미설정 — 프로바이더 기본)",
            "backend": "vllm/사내" if s.api_base else "gemini/외부",
            "api_key_set": s.gemini_api_key is not None,   # 값은 남기지 않는다
            "max_steps": s.max_steps,
            "polestar_mcp_url": s.polestar_mcp_url,
            "severity_judge_enabled": s.severity_judge_enabled,
            "remediation_recommender_enabled": s.remediation_recommender_enabled,
        }
    except Exception as exc:  # noqa: BLE001
        fp["llm"] = {"(로드 실패)": f"{type(exc).__name__}: {exc}"}
    return fp


def write_record(record: dict) -> None:
    """runs.jsonl 1줄 + 대장 1행. 기록 실패는 테스트를 깨뜨리지 않되 침묵하지 않는다."""
    try:
        RUNS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with RUNS_JSONL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        if not LEDGER.exists():   # 레벨 A 기록기가 먼저 만들지만 단독 실행도 가능해야 한다
            LEDGER.write_text(
                "# Plan 66 MVP 테스트 실행 기록 (대장)\n\n"
                "| 실행 시각(UTC) | 레벨 | 테스트 | 결과 | 소요 | 커밋 | 관측 요약 |\n"
                "|---|---|---|---|---|---|---|\n", encoding="utf-8")
        obs = record.get("observed") or {}
        obs_txt = " · ".join(f"{k}={v}" for k, v in obs.items()) or "-"
        row = (f"| {record['ts']} | {record['level']} | `{record['test']}` | "
               f"**{record['outcome'].upper()}** | {record['duration_s']:.1f}s | "
               f"`{record['env']['git_commit']}`"
               f"{'+dirty' if record['env']['git_dirty'] else ''} | {obs_txt} |")
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(row + "\n")
    except OSError as exc:
        print(f"[mvp_record] 기록 실패(테스트 결과에는 영향 없음): {exc}")


# ─── pytest 통합 ─────────────────────────────────────────────────────────────

def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "mvp: Plan 66 MVP 테스트 — 실행 결과를 대장(docs/24)에 기록한다")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.skipped):
        item.stash_mvp = getattr(item, "stash_mvp", {})
        item.stash_mvp["outcome"] = report.outcome
        item.stash_mvp["duration"] = report.duration
        if report.skipped:
            item.stash_mvp["skip_reason"] = str(report.longrepr)


@pytest.fixture
def mvp_record(request):
    """MVP 테스트가 관측값을 담는 그릇. teardown 시 기록으로 남는다."""
    rec: dict = {"observed": {}, "notes": []}
    yield rec

    st = getattr(request.node, "stash_mvp", {})
    write_record({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": LEVEL,
        "test": request.node.nodeid,
        "outcome": st.get("outcome", "unknown"),
        "duration_s": float(st.get("duration", 0.0)),
        "skip_reason": st.get("skip_reason", ""),
        "env": env_fingerprint(),
        "observed": rec["observed"],
        "notes": rec["notes"],
    })
