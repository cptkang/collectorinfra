"""plans/210 독립 검증 — 파드 연계 미확정 문장이 진단 화면의 D-348 선택 UI를 열지 않는가 (verifier).

문장은 sre_agent 정본 상수(`playbook_steps.POLESTAR_UNRESOLVED` · `playbook_runner` 연계 표)를
소스에서 읽어 조립한다(sre_agent는 별도 venv — import하지 않는다 · plan196 계약 테스트 관례).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_api.test_ui_plan187_w15_diagnosis import _node
from tests.test_api.test_ui_plan210_w6 import _PROBE, _final, _script

_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _ROOT / "sre_agent" / "sre_agent" / "application" / "playbook_runner.py"
_STEPS = _ROOT / "sre_agent" / "sre_agent" / "application" / "playbook_steps.py"


def _const(src: str, pattern: str) -> str:
    m = re.search(pattern, src)
    assert m is not None, pattern
    return m.group(1)


def _pod_sentence() -> str:
    runner = _RUNNER.read_text(encoding="utf-8")
    steps = _STEPS.read_text(encoding="utf-8")
    head = _const(steps, r'^POLESTAR_UNRESOLVED = "(.*?)"$'.replace("^", "(?m)^"))
    template = _const(runner, r'POLESTAR_APM_MAP_POD_UNRESOLVED = f"\{POLESTAR_UNRESOLVED\}(.*?)"')
    why = _const(runner, r'"apm_map_pending": "(.*?)"')
    status = _const(runner, r'"missing": "(제니퍼 노드 주소 조회 비활성.*?)"')
    body = template.replace("{{why}}", why)
    return f"{head}{body} · {status}"


def _skipped_card(reason: str, *, operator: bool) -> dict:
    sections = [{"step": "S4", "title": "호스트 자원", "status": "skipped", "reason": reason}]
    final = _final({"playbook": {"id": "fallback", "sections": sections,
                                 "scope": {"alarm_instance": "pod-x"}}})
    return _node(_script(final, _PROBE, operator=operator))


def test_pod_sentence_is_built_from_sre_agent_constants() -> None:
    text = _pod_sentence()
    assert text.startswith("폴스타 서버 미확정 — K8s 파드 · APM↔SMS 연계 미확정(")
    assert text.endswith("제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)")


def test_pod_apm_map_unresolved_does_not_open_selection_ui() -> None:
    """D-355 ④ — 파드 연계 미확정은 「서버 선택 대기」가 아니다(후보 조회·안내 0)."""
    text = _pod_sentence()
    state = _node(f"""
out = DV.hostLinkState([{{ step: "S4", skipped: true,
    because: {json.dumps(text, ensure_ascii=False)}, skipRaw: "" }}], null);
""")
    assert state is None
    for operator in (False, True):
        out = _skipped_card(text, operator=operator)
        assert out["called"] == 0 and out["intro"] == 0, operator
    # 관리자는 연계 상자(노드 주소 조회 상태 · 수정 링크)를 본다
    admin = _skipped_card(text, operator=True)
    assert admin["apm"] == 1
    assert admin["notes"] == [["제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)"]]
