"""MVP 테스트 실행 기록기 — 레벨 A(게이트→조사 배관) 측 (Plan 66 · docs/23).

**테스트 코드가 스스로 기록한다.** 외부 실행 스크립트가 감사 파일을 뒤져 판정을 재도출하면
판정 로직이 두 벌이 되고, pytest를 직접 돌렸을 때는 기록이 남지 않는다. 그래서 기록은
테스트 자신이 관측한 값으로 남긴다 — 어떤 방식으로 실행하든(pytest·IDE·CI) 기록이 생긴다.

산출:
    logs/mvp_test/runs.jsonl              런별 1줄(기계 판독 · `logs/`는 gitignore)
    docs/24_plan66_mvp_test_log.md        실행 대장(커밋 대상 · 세션을 넘는 참조 경로)

사용:
    @pytest.mark.mvp
    def test_something(mvp_record):
        mvp_record["observed"]["investigation_id"] = ...

**경계 주의(D-118/D-139)**: `sre_agent/tests/mvp_record.py`에 같은 계약의 사본이 있다.
별도 venv·양방향 import 0 경계라 모듈을 공유할 수 없어, **공유하는 것은 모듈이 아니라
출력 파일 계약**이다(`mcp_server`가 자체 미니 SQL 로거로 같은 `logs/sql/`에 append하는
D-140 전례와 동일한 판단).
"""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNS_JSONL = REPO / "logs" / "mvp_test" / "runs.jsonl"
LEDGER = REPO / "docs" / "24_plan66_mvp_test_log.md"

LEVEL = "A(stub·배관)"

LEDGER_HEADER = """# 24. Plan 66 MVP 테스트 실행 기록 (대장)

> MVP 테스트가 실행될 때마다 **테스트 코드 자신이** 한 행을 덧붙인다
> (`noise_gate/tests/mvp_record.py` · `sre_agent/tests/mvp_record.py`).
> 절차·판정 기준은 `docs/23_plan66_mvp_test_guide.md`, 잔여 항목은
> `plans/66-sre-agent-integrated-implementation-plan.md` §1.5.
>
> 원시 기록은 `logs/mvp_test/runs.jsonl`에 남으며 **`logs/`는 gitignore라 로컬 한정**이다 —
> 이 대장이 세션·작업자를 넘어 참조 가능한 유일한 기록이므로, 판정 근거가 되는 값은 여기에 적는다.
> 해석 방법은 `docs/23` §12(실행 기록을 Plan 66 진행에 쓰는 법).

| 실행 시각(UTC) | 레벨 | 테스트 | 결과 | 소요 | 커밋 | 관측 요약 |
|---|---|---|---|---|---|---|
"""


def _sh(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 — 지문 수집 실패가 테스트를 깨뜨리면 안 된다
        return ""


def env_fingerprint() -> dict:
    """'왜 그렇게 동작했는가'를 나중에 해석하려면 결과만큼 환경이 중요하다."""
    fp: dict = {
        "git_commit": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "git_branch": _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(_sh(["git", "status", "--porcelain"])),
        "run_e2e": os.environ.get("RUN_E2E", ""),
    }
    try:
        from src.config import load_config

        n = load_config().noise_gate
        fp["flags"] = {
            "investigation_trigger_enabled": n.investigation_trigger_enabled,
            "investigation_service_url": n.investigation_service_url,
            "investigation_total_timeout_seconds": n.investigation_total_timeout_seconds,
            "investigation_followup_enabled": n.investigation_followup_enabled,
            "fault_escalation_enabled": n.fault_escalation_enabled,
            # 레벨 A에서 전부 off여야 LLM 호출 0이 보장된다(D-127)
            "enable_agentic_enricher": n.enable_agentic_enricher,
            "enable_ai_severity_boost": n.enable_ai_severity_boost,
            "enable_llm_actionability": n.enable_llm_actionability,
        }
    except Exception as exc:  # noqa: BLE001
        fp["flags"] = {"(로드 실패)": f"{type(exc).__name__}: {exc}"}
    return fp


def write_record(record: dict) -> None:
    """runs.jsonl 1줄 + 대장 1행. 기록 실패는 테스트를 깨뜨리지 않되 침묵하지 않는다."""
    try:
        RUNS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with RUNS_JSONL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        if not LEDGER.exists():
            LEDGER.write_text(LEDGER_HEADER, encoding="utf-8")
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


# ─── 판정 보조 (테스트가 감사 레코드로 직접 판정하도록) ──────────────────────

def load_gate_config():
    """게이트 설정을 반환한다(로드 실패 시 None — 전제 확인용이라 예외를 던지지 않는다)."""
    try:
        from src.config import load_config

        return load_config().noise_gate
    except Exception:  # noqa: BLE001
        return None


class _Slice:
    """주입 구간에 추가된 감사 레코드만 읽는다(과거 레코드 혼입 방지)."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = path.stat().st_size if path.exists() else 0

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._offset)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


@contextmanager
def decision_slice(path: Path):
    """with 블록 진입 시점 이후 적재분만 보는 슬라이스를 만든다."""
    yield _Slice(Path(path))


# ─── pytest 통합 ─────────────────────────────────────────────────────────────

def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "mvp: Plan 66 MVP 테스트 — 실행 결과를 대장(docs/24)에 기록한다")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """테스트가 스스로 판정한 결과(pass/fail/skip)를 기록에 싣는다."""
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
