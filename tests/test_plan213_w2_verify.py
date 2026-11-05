"""plans/213 W2(D-366 ②) 독립 검증 — 실 저장 경로(poll → persist_terminal → clip) 왕복과
mode off 반복 접수 시 D-347 상한 동작을 고정한다(verifier).

- 저장 레코드는 테스트가 직접 쓴 dict가 아니라 poll 응답을 본체가 저장한 결과다.
- `partial_briefing`(deep_dive.status="running")은 저장 레코드 `briefing`에 섞이지 않는다.
실 LLM·실 서비스 0(가짜 클라이언트).
"""

from __future__ import annotations

from typing import Any

from src.api.routes import alarm as alarm_routes
from tests.test_api.test_plan187_w11_manual import (  # noqa: F401 — autouse·fixture 등록
    ADMIN,
    _client,
    _config,
    _FakeClient,
    _inject,
    _post,
    _reset_poll_state,
    _seed_alarm,
    _store,
    audits,
)
from noise_gate.infrastructure.investigation_store import _dict_candidates
from tests.test_plan213_w2_join_exception import _briefing, _deep, _deep_mode_off


def _big(deep: dict[str, Any]) -> dict[str, Any]:
    """저장 상한(32KB)을 넘겨 `clip_briefing` 축소가 일어나는 플레이북 브리핑."""
    b = _briefing(deep)
    b["playbook"]["sections"] = [
        {"step": f"S{i}", "status": "ok", "verdict": "degraded", "tools": ["apm_x"],
         "table": {"rows": [["행" * 40, "값" * 40] for _ in range(40)]}}
        for i in range(10)
    ]
    return b


def _done_poll(deep: dict[str, Any], *, big: bool = False) -> dict[str, Any]:
    return {
        "status": "done",
        "verdict": "high",
        "briefing": _big(deep) if big else _briefing(deep),
        # 진척 키 — 종결 응답에 남아 있어도 저장 briefing에 섞이면 안 된다.
        "partial_briefing": {"partial": True, **_briefing({"status": "running", "mode": "parallel"})},
    }


def _seed_submitted(tmp_path, inv: str = "man-old") -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=["a1"], kind="manual", status="submitted",
        source="manual", origin="manual", parent_alarm_id="a1",
    )


# ── 실 저장 경로 왕복 ─────────────────────────────────────────────────────
def test_store_roundtrip_clipped_keeps_skipped_status(tmp_path):  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="r1", alarm_ids=["a1"], kind="manual", status="done",
        source="api_poll", briefing=_big(_deep("skipped", narrative="서" * 9000)),
    )
    rec = _store(tmp_path).get_by_investigation("r1")
    assert rec is not None and isinstance(rec["briefing"], dict)
    assert rec["briefing"]["deep_dive"]["status"] == "skipped"
    assert alarm_routes._deep_dive_retryable(rec) is True


def test_poll_persist_then_judge_new_submit(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    """진행 중 레코드 → 이번 poll에서 done(심층 skipped) 저장 → 같은 요청에서 합류하지 않고 새 접수."""
    _seed_alarm(tmp_path)
    _seed_submitted(tmp_path)
    fake = _FakeClient(poll=_done_poll(_deep("skipped"), big=True))
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert fake.poll_calls == ["man-old"]
    assert body["joined"] is False and body["investigation_id"] == "man-1"
    stored = _store(tmp_path).get_by_investigation("man-old")
    assert stored is not None and stored["status"] == "done"
    assert stored["briefing"]["deep_dive"]["status"] == "skipped"
    assert "partial" not in stored["briefing"] and "partial_briefing" not in stored


def test_poll_persist_deep_done_joins(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    _seed_alarm(tmp_path)
    _seed_submitted(tmp_path)
    fake = _FakeClient(poll=_done_poll(_deep("done")))
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body == {"investigation_id": "man-old", "status": "done", "joined": True}
    assert fake.submit_calls == []


def test_running_with_partial_deep_running_joins(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    """진행 중(partial_briefing deep_dive running)은 종전대로 합류 — 판정이 진행 중 합류를 막지 않는다."""
    _seed_alarm(tmp_path)
    _seed_submitted(tmp_path)
    fake = _FakeClient(poll={"status": "running", "partial_briefing": {
        "partial": True, **_briefing({"status": "running", "mode": "parallel"})}})
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["joined"] is True and body["investigation_id"] == "man-old"
    assert fake.submit_calls == []


# ── mode off 반복(plans/213 W4 · D-366 부기 1) — 다시 돌려도 같은 생략은 합류 ─────
def test_mode_off_repeat_joins_after_done(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    """심층 mode off(늘 skipped · retryable 없음)면 끝난 조사에 합류 — 반복 클릭이 새 접수를 만들지 않는다."""
    _seed_alarm(tmp_path)
    fake = _FakeClient(poll=_done_poll(_deep_mode_off()))
    _inject(monkeypatch, fake)
    client = _client(_config(tmp_path), ADMIN)
    ids = [_post(client).json().get("investigation_id") for _ in range(4)]
    assert ids == ["man-1"] * 4
    assert len(fake.submit_calls) == 1


def test_mode_off_other_users_join_done(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    """다른 사용자도 끝난 mode off 조사에 합류한다(알람 단위 1건)."""
    _seed_alarm(tmp_path)
    fake = _FakeClient(poll=_done_poll(_deep_mode_off()))
    _inject(monkeypatch, fake)
    cfg = _config(tmp_path)
    _post(_client(cfg, ADMIN))
    body = _post(_client(cfg, {**ADMIN, "sub": "admin2"})).json()
    assert body["joined"] is True and body["investigation_id"] == "man-1"
    assert len(fake.submit_calls) == 1


def test_retryable_survives_deepest_clip_level():
    """저장 상한 축소의 마지막 후보(상태만)에도 `retryable`이 남는다."""
    deep = _deep("skipped", narrative="서" * 9000, summary="요약", cause="원인")
    last = _dict_candidates("deep_dive", deep)[-1]
    assert last["status"] == "skipped" and last["retryable"] is True
    assert alarm_routes._deep_dive_retryable({"briefing": {"deep_dive": last}}) is True


def test_mode_off_inflight_still_joins_across_users(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    """진행 중에는 다른 사용자도 합류(사용자별 진행 중 1건과 무관하게 같은 알람 1건)."""
    _seed_alarm(tmp_path)
    fake = _FakeClient(poll={"status": "running"})
    _inject(monkeypatch, fake)
    cfg = _config(tmp_path)
    first = _post(_client(cfg, ADMIN)).json()
    second = _post(_client(cfg, {**ADMIN, "sub": "admin2"})).json()
    assert first["joined"] is False
    assert second["joined"] is True and second["investigation_id"] == first["investigation_id"]
    assert len(fake.submit_calls) == 1
