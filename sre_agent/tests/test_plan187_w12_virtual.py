"""plans/187 W12 — 채팅 증상 질의의 가상 알람 맥락이 의도한 플레이북을 고르는지(본체 사본 대조).

본체 라우터(`src/domain/symptom_router.py`)는 플레이북별 가상 알람 (resourceType, alarmName)을
`VIRTUAL_EVENTS` 리터럴로 둔다. 이 테스트는 그 파일을 **import 없이** ast로 읽어(D-118 · 양방향
import 0), 기존 선택 경로(`alarm_facts_from_payload` → `select_playbook`)가 같은 id를 고르는지
대조한다. sre_agent 쪽 변경은 없다 — 가상 맥락은 기존 계약 "1" 페이로드 그대로이고
`meta.virtual`·`meta.playbook_hint`는 검증기가 허용하는 가산 키다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sre_agent.application.investigation_jobs import validate_payload
from sre_agent.application.playbook_runner import alarm_facts_from_payload
from sre_agent.domain.playbook import APM_FALLBACK_ID, select_playbook
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTER = REPO_ROOT / "src" / "domain" / "symptom_router.py"


def _virtual_events() -> dict[str, tuple[str, str]]:
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "VIRTUAL_EVENTS":
            return ast.literal_eval(node.value)
    raise AssertionError("symptom_router.py에 VIRTUAL_EVENTS 리터럴이 없다")


def _payload(resource_type: str, alarm_name: str, playbook_id: str) -> dict:
    """본체 `build_trigger_payload` 산출과 같은 키 모양의 가상 알람 페이로드."""
    return {
        "contract_version": "1",
        "event": {
            "dbId": "polestar",
            "serverName": "web01",
            "hostname": "web01",
            "alarmId": "",
            "severity": 0,
            "alarmName": alarm_name,
            "resourceType": resource_type,
            "resourceName": "web01",
            "alarmTime": "20261019140500",
            "conditions": "",
            "conditionLog": "",
        },
        "decision": {
            "tier": "", "reason": "chat_symptom", "fingerprint": "chat:abc", "signals": {},
        },
        "meta": {
            "recurrence": None,
            "cluster": None,
            "root_resource": None,
            "source": "collectorinfra",
            "investigation_kind": "manual",
            "virtual": True,
            "playbook_hint": playbook_id,
        },
    }


PLAYBOOKS = load_playbooks(DEFAULT_PLAYBOOK_DIR).playbooks


def test_virtual_events_cover_every_playbook():
    # APM 공통 점검(was.fallback)은 fallback처럼 증상 질의로 고르지 않는다(plans/196 D2 ②)
    ids = {pb.id for pb in PLAYBOOKS if not pb.is_fallback and pb.id != APM_FALLBACK_ID}
    assert set(_virtual_events()) == ids


@pytest.mark.parametrize(("playbook_id", "fields"), sorted(_virtual_events().items()))
def test_virtual_event_selects_intended_playbook(playbook_id, fields):
    payload = _payload(*fields, playbook_id)
    ok, reason = validate_payload(payload)
    assert ok, reason
    chosen, matched_by = select_playbook(alarm_facts_from_payload(payload), PLAYBOOKS)
    assert chosen is not None and chosen.id == playbook_id, matched_by
    assert matched_by.startswith("match:")
