"""plans/199 W0 픽스처 생성 2단 — 조사 서비스 poll 응답을 본체 생산 코드로 화면 뷰 봉투로 만든다.

    .venv/bin/python tests/fixtures/plan199/make_fixtures.py

1단(`_gen_sre.py` · sre_agent 자체 venv)이 dispatcher를 가짜 콜러블·가짜 LLM·가짜 HolmesGPT로 돌려
poll 응답을 만들고, 이 스크립트가 그 응답을 본체 경로 그대로 통과시킨다 — 트리거 접수 행 기록 →
`investigation_poll.poll_investigation`(가짜 조사 서비스 클라이언트) → 종결이면 저장소 기록
(`clip_briefing`·`render_briefing_lines` 포함) → `investigation_view` + `_attach_live_progress`
(`/api/v1/alarm/investigations/*` 응답 조립과 같은 순서 · 허브 실행 기록·KB 부록은 제외).
망·LLM 호출 0. pytest 수집 대상 아님(파일명). 결과 JSON의 `updated_at`만 고정값으로 바꾼다.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from noise_gate.domain.notification_policy import NotificationDecision  # noqa: E402
from noise_gate.infrastructure import sre_agent_client  # noqa: E402
from noise_gate.infrastructure.decision_store import DecisionStore  # noqa: E402
from noise_gate.infrastructure.investigation_store import (  # noqa: E402
    InvestigationStore,
    investigation_view,
)
from src.api import investigation_poll  # noqa: E402
from src.api.routes.alarm import (  # noqa: E402
    _attach_live_progress,
    _investigation_alarm_brief,
    _investigation_mask_fn,
)
from src.config.security import SecurityConfig  # noqa: E402

HERE = Path(__file__).resolve().parent
SRE_PY = ROOT / "sre_agent" / ".venv" / "bin" / "python"
FIXED_TS = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
FIXED_UPDATED_AT = "2026-10-19T01:10:00+00:00"
MASK_CONFIG = SimpleNamespace(
    security=SimpleNamespace(mask_pattern=SecurityConfig.model_fields["mask_pattern"].default)
)

#: 케이스(= 파일 이름) → `_note` 머리 한 줄(케이스 뜻).
CASES: dict[str, str] = {
    "confirmed_same_layer": (
        "gc 픽스처 · parallel · 가짜 HolmesGPT가 apm_runtime_health 인용 → "
        "엔진 same_layer(gc) · 1순위 가설 지지(H-GC-LEAK) — 「확정」 표본"
    ),
    "confirmed_cpu_ke": (
        "case3_cpu_loop · parallel · 가짜 HolmesGPT 증상 도구만 인용(not_comparable) · "
        "validated 알려진 오류 KE-DEMO-012 일치(1순위 고정·우회 있음) · "
        "원인 영역 cpu 「확정」 + 꼭 확인 ③ 표본"
    ),
    "evidence_missing": (
        "case3_cpu_loop · S6 프로세스 조회 오류 주입 → S6 missing · "
        "1순위 가설 지지인데 evidence_steps에 S6 — 「추정」 + 꼭 확인 ② 표본"
    ),
    "deep_skipped": (
        "latency_external · 심층 모드 off → deep_dive.status skipped · 1순위 가설 지지"
    ),
    "deep_failed": (
        "latency_external · parallel · 가짜 HolmesGPT 예외 → deep_dive.status failed · "
        "1순위 가설 지지"
    ),
    "missing_many": (
        "polestar_unresolved · 폴스타 서버 미확정 → S4·S6 missing · S3 skipped · "
        "가설 없음 · 원인 영역 unknown — 「미확정」 표본"
    ),
    "different_layer": (
        "latency_external · parallel · 가짜 HolmesGPT apm_resource_pool 인용 → "
        "플레이북 external ↔ HolmesGPT db · different_layer · 「엔진 간 불일치」"
    ),
    "non_playbook": (
        "sre_playbook_enabled off(종전 HolmesGPT 경로) — playbook 키 없음 · "
        "briefing_lines는 저장소 render_briefing_lines 결과"
    ),
    "markdown_narrative": (
        "latency_sql · parallel · 가짜 HolmesGPT 마크다운 답 11,000자대 → "
        "_deep_section이 8,000자로 자르고 DEEP_NARRATIVE_CLIPPED 표지. "
        "`poll_view`는 조사 서비스 응답 그대로, `view`는 저장소(32KB 상한) 통과본"
    ),
    "episode_scope": (
        "latency_sql · 사건 종합 조사(kind episode · ep-demo-1) · cross_source on · "
        "멤버 폴스타 2 + 제니퍼 1 · 유효 링크 1 · "
        "`alarm`은 with_alarm 조회의 판정 기록 요약 · `user_view` 동봉"
    ),
    "user_view_secret": (
        "latency_external · 소견·종합 서술·심층 답·프로세스 표 셀에 비밀값 꼴 문자열 "
        "(password=… · Bearer JWT) — `view`(운영자 원문)·`user_view`(마스킹) 쌍"
    ),
    "running_collect": (
        "latency_sql · 수집 중(S5 차례 · 4/10) — 진행 중 progress·partial_briefing"
    ),
    "running_deep": (
        "latency_sql · parallel · 플레이북 종합 완료 · 심층 진행 중 — "
        "partial_briefing에 6요소·engine_agreement(not_comparable)"
    ),
}
#: 사용자 뷰를 함께 싣는 케이스.
WITH_USER_VIEW = frozenset({"episode_scope", "user_view_secret"})


class _FakeClient:
    """가짜 조사 서비스 클라이언트 — poll은 1단이 만든 응답을 그대로 돌려준다."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def poll(self, investigation_id: str, **_: Any) -> dict:
        return self._responses[investigation_id]


def _sre_stage(out: Path) -> dict[str, dict]:
    subprocess.run([str(SRE_PY), str(HERE / "_gen_sre.py"), str(out)], check=True, cwd=ROOT)
    return json.loads(out.read_text(encoding="utf-8"))


def _decisions(path: Path, case: dict) -> None:
    """episode 케이스의 판정 기록(알람 요약 `alarm`의 출처) — 대표·멤버 알람 3건."""
    store = DecisionStore(str(path))
    names = {"p-demo-1": ("CPU 사용률", "2026-10-19 09:58:00"),
             "p-demo-2": ("메모리 사용률", "2026-10-19 09:59:00")}
    for alarm_id in case["alarm_ids"]:
        name, at = names.get(alarm_id, ("TX_BAD_RESPONSE", "2026-10-19 10:00:00"))
        store.record(
            NotificationDecision(tier="page", reason="사건 종합 조사 대상", priority=3, signals={}),
            alarm_id=alarm_id, alarm_name=name, server_name="wasdemo01", hostname="wasdemo01",
            alarm_time=at, ts=FIXED_TS,
        )


def _view(store: InvestigationStore, iid: str, case: dict,
          mask_fn: Callable[[str], str] | None, decisions: Path | None) -> dict:
    rec = store.get_by_investigation(iid)
    assert rec is not None
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LIVE.clear()
    ng = SimpleNamespace(investigation_mcp_call_timeout_seconds=10.0)
    rec, reason = asyncio.run(investigation_poll.poll_investigation(store, rec, iid, ng))
    assert reason is None, reason
    view = investigation_view(rec, mask_fn=mask_fn, operator_view=mask_fn is None)
    _attach_live_progress(view, rec, mask_fn)
    if decisions is not None:
        config = SimpleNamespace(noise_gate=SimpleNamespace(
            decision_store_path=str(decisions), decision_store_enabled=True,
            decision_store_max_lines=20000,
        ))
        alarm = _investigation_alarm_brief(config, rec, mask_fn, mask_fn is None, related=True)
        if alarm:
            view["alarm"] = alarm
    if view.get("updated_at"):
        view["updated_at"] = FIXED_UPDATED_AT
    return view


def _poll_view(raw: dict, iid: str, case: dict) -> dict:
    """저장소를 거치지 않은 조사 서비스 응답 브리핑의 운영자 뷰(32KB 상한 미적용 비교용)."""
    rec = {"investigation_id": iid, "kind": case["kind"], "status": "done",
           "briefing": raw["briefing"], "briefing_lines": [], "ts": FIXED_UPDATED_AT}
    view = investigation_view(rec, operator_view=True)
    view["progress"] = raw.get("progress")
    return view


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw_all = _sre_stage(Path(tmp) / "raw.json")
        responses = {f"inv-{name}": case["summary"] for name, case in raw_all.items()}
        fake = _FakeClient(responses)
        sre_agent_client.build_sre_agent_client = lambda _cfg: fake  # type: ignore[assignment]
        for name, note in CASES.items():
            case = raw_all[name]
            iid = f"inv-{name}"
            out: dict[str, Any] = {"_note": (
                f"{note}. 생성: make_fixtures.py(1단 _gen_sre.py — 가짜 콜러블·가짜 LLM·"
                "가짜 HolmesGPT) · 생산 코드 산출(손 편집 없음 · updated_at만 고정)"
            )}
            user_mask = _investigation_mask_fn(MASK_CONFIG)
            for label, mask_fn in (("view", None), ("user_view", user_mask)):
                if label == "user_view" and name not in WITH_USER_VIEW:
                    continue
                store = InvestigationStore(str(Path(tmp) / f"{name}-{label}.jsonl"))
                store.record(investigation_id=iid, alarm_ids=case["alarm_ids"], status="accepted",
                             source="trigger", kind=case["kind"], episode_id=case["episode_id"],
                             ts=FIXED_TS)
                decisions = None
                if name == "episode_scope":
                    decisions = Path(tmp) / f"{name}-{label}-decisions.jsonl"
                    _decisions(decisions, case)
                out[label] = _view(store, iid, case, mask_fn, decisions)
            if name == "markdown_narrative":
                out["poll_view"] = _poll_view(case["summary"], iid, case)
            (HERE / f"{name}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            print(f"wrote {name}.json")


if __name__ == "__main__":
    main()
