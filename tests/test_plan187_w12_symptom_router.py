"""plans/187 W12 — 채팅 증상 질의 라우터(`src.domain.symptom_router`) 검증.

- 라우팅 표: 대상 0·1·2 × 증상 유형 0·1·2 — 대상 1 × 유형 1만 플레이북.
- 지시어만 있는 질의 = HolmesGPT(호출부는 이번 턴 명시 대상만 넘긴다 — 노드 테스트가 따로 본다).
- 경로 표기 문구 결정적.
- 사전 사본 동등성: sre_agent `classify_alarm_kind` 어휘(`_KIND_KEYWORDS`) · 플레이북 YAML
  `match.surface_terms`·`match.alarm_kind` · 가상 알람 표가 비fallback 플레이북 전부를 덮는지 —
  sre_agent를 import하지 않고 파일을 읽어 대조한다(D-118).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from src.domain import symptom_router as sr
from src.domain.symptom_router import (
    REASON_MATCHED,
    REASON_TARGET_MULTI,
    REASON_TARGET_NONE,
    REASON_TYPE_MULTI,
    REASON_TYPE_NONE,
    REASON_TYPE_UNRESOLVED,
    ROUTE_HOLMESGPT,
    ROUTE_PLAYBOOK,
    route_symptom,
    symptom_candidates,
)

_ROOT = Path(__file__).resolve().parent.parent
_SRE_GUIDANCE = _ROOT / "sre_agent" / "sre_agent" / "application" / "investigation_guidance.py"
_SRE_PLAYBOOKS = _ROOT / "sre_agent" / "config" / "playbooks"

_T1 = [{"hostname": "web01", "db_id": "polestar"}]
_T2 = [{"hostname": "web01"}, {"hostname": "web02"}]
_Q_TYPE0 = "{h} 서버 상태 원인 분석해줘"
_Q_TYPE1 = "{h} CPU 사용률 원인 분석해줘"
_Q_TYPE2 = "{h} CPU와 메모리 원인 분석해줘"


# ── 라우팅 표 (대상 0·1·2 × 유형 0·1·2) ──────────────────────────────────────


@pytest.mark.parametrize(
    ("targets", "query", "route", "reason"),
    [
        ([], _Q_TYPE0, ROUTE_HOLMESGPT, REASON_TARGET_NONE),
        ([], _Q_TYPE1, ROUTE_HOLMESGPT, REASON_TARGET_NONE),
        ([], _Q_TYPE2, ROUTE_HOLMESGPT, REASON_TARGET_NONE),
        (_T1, _Q_TYPE0, ROUTE_HOLMESGPT, REASON_TYPE_NONE),
        (_T1, _Q_TYPE1, ROUTE_PLAYBOOK, REASON_MATCHED),
        (_T1, _Q_TYPE2, ROUTE_HOLMESGPT, REASON_TYPE_MULTI),
        (_T2, _Q_TYPE0, ROUTE_HOLMESGPT, REASON_TARGET_MULTI),
        (_T2, _Q_TYPE1, ROUTE_HOLMESGPT, REASON_TARGET_MULTI),
        (_T2, _Q_TYPE2, ROUTE_HOLMESGPT, REASON_TARGET_MULTI),
    ],
)
def test_routing_table(targets, query, route, reason):
    result = route_symptom(query.format(h="web01"), targets)
    assert (result.route, result.reason) == (route, reason)
    if route == ROUTE_PLAYBOOK:
        assert result.playbook_hint == "host.cpu"
        assert result.alarm_kind == "cpu"
        assert result.target == {"hostname": "web01", "db_id": "polestar"}
    else:
        assert result.playbook_hint is None
    # 대상은 정확히 1건일 때만 싣는다.
    assert (result.target is not None) == (len(targets) == 1)


def test_demonstrative_only_goes_to_holmesgpt():
    """지시어는 식별자가 아니다 — 호출부가 명시 대상 0건을 넘기면 HolmesGPT · 대상 미확정."""
    result = route_symptom("그 서버 CPU 사용률 원인 분석해줘", [])
    assert result.route == ROUTE_HOLMESGPT
    assert result.reason == REASON_TARGET_NONE
    assert result.label == "HolmesGPT — 대상 미확정"


@pytest.mark.parametrize(
    ("query", "label"),
    [
        ("web01 응답 지연 원인 봐줘", "플레이북 was.latency — 대상·증상 일치"),
        ("web01 응답지연 원인 봐줘", "플레이북 was.latency — 대상·증상 일치"),
        ("web01 디스크 용량 부족 원인", "플레이북 host.fs — 대상·증상 일치"),
        ("web01 디스크 I/O 대기 원인", "플레이북 host.disk_io — 대상·증상 일치"),
        ("web01 메모리 부족 원인", "플레이북 host.memory — 대상·증상 일치"),
        ("web01 서버 상태 원인 분석", "HolmesGPT — 증상 유형 미확정"),
        ("web01 CPU와 메모리 원인", "HolmesGPT — 증상 유형 복수"),
        # 디스크 kind는 플레이북이 둘(용량·I/O) — 표면어가 없으면 유형 미확정.
        ("web01 디스크 이상 원인", "HolmesGPT — 증상 유형 미확정"),
        # 로그 kind는 전용 플레이북이 없다.
        ("web01 로그 에러 원인", "HolmesGPT — 증상 유형 미확정"),
    ],
)
def test_route_label_is_deterministic(query, label):
    first = route_symptom(query, _T1)
    assert first.label == label
    assert route_symptom(query, _T1) == first  # 같은 입력 = 같은 결과


def test_unresolved_kind_keeps_alarm_kind():
    result = route_symptom("web01 디스크 이상 원인", _T1)
    assert result.reason == REASON_TYPE_UNRESOLVED
    assert result.alarm_kind == "disk"


def test_identifier_is_not_read_as_symptom():
    """서버명 속 키워드(logsvr01의 log)는 증상으로 잡히지 않는다."""
    result = route_symptom("logsvr01 CPU 원인 분석", [{"server_name": "logsvr01"}])
    assert result.route == ROUTE_PLAYBOOK
    assert result.playbook_hint == "host.cpu"


def test_candidates_union_of_terms_and_keywords():
    assert symptom_candidates("CPU 사용률") == ["host.cpu"]
    assert symptom_candidates("응답 지연과 메모리") == ["host.memory", "was.latency"]
    assert symptom_candidates("상태 확인") == []


def test_empty_target_values_are_ignored():
    result = route_symptom("CPU 원인", [{"hostname": " ", "server_name": None}])
    assert result.reason == REASON_TARGET_NONE


# ── 사전 사본 동등성 (sre_agent 파일을 읽어 대조 · import 없음) ─────────────────


def _module_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name}에 {name}이 없다")


#: 증상 질의로 고르지 않는 플레이북 — 호스트 공통 점검 · APM 공통 점검(plans/196 D2 ②).
_NOT_BY_SYMPTOM = frozenset({"fallback", "was.fallback"})


def _playbooks() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(_SRE_PLAYBOOKS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        out[str(doc["id"])] = doc
    return out


def test_kind_keywords_match_sre_agent_classifier():
    assert _module_literal(_SRE_GUIDANCE, "_KIND_KEYWORDS") == sr.KIND_KEYWORDS


def test_surface_terms_match_playbook_yaml():
    books = _playbooks()
    expected = {
        pid: tuple((doc.get("match") or {}).get("surface_terms") or ())
        for pid, doc in books.items()
        if pid not in _NOT_BY_SYMPTOM
    }
    assert expected == sr.PLAYBOOK_SURFACE_TERMS


def test_alarm_kinds_match_playbook_yaml():
    books = _playbooks()
    expected = {
        pid: str(doc["match"]["alarm_kind"])
        for pid, doc in books.items()
        if (doc.get("match") or {}).get("alarm_kind")
    }
    assert expected == sr.PLAYBOOK_ALARM_KINDS


def test_virtual_events_cover_every_playbook():
    """가상 알람 표가 비fallback 플레이북 전부를 덮는다(선택 결과 대조는 sre_agent 테스트)."""
    assert set(sr.VIRTUAL_EVENTS) == {pid for pid in _playbooks() if pid not in _NOT_BY_SYMPTOM}
    # sre_agent 테스트가 ast로 읽는다 — 리터럴이어야 한다.
    literal = _module_literal(_ROOT / "src" / "domain" / "symptom_router.py", "VIRTUAL_EVENTS")
    assert literal == sr.VIRTUAL_EVENTS


def test_ref_kind_matches_thread_history():
    from src.api.thread_history import INVESTIGATION_REF_KIND

    assert sr.INVESTIGATION_REF_KIND == INVESTIGATION_REF_KIND
