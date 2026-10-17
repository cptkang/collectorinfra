"""plans/176 verifier — build_briefing 반환 dict의 리스트 별칭·입력 비변형 계약.

판정/렌더 분리로 `timeline`이 인용 목록의 사본이 됐다. 반환 dict 안 리스트·dict가
서로(또는 호출자 입력과) 같은 객체를 공유하지 않아야 소비자(직렬화·후속 편집)가
한쪽을 고쳐도 다른 쪽이 바뀌지 않는다.
"""

from __future__ import annotations

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.domain.cross_source_episode import EpisodeContext, EpisodeLink, EpisodeMember
from sre_agent.domain.severity_signatures import ImportanceVerdict


def _containers(obj, acc):
    if isinstance(obj, (list, dict)):
        acc.append(obj)
        for v in obj.values() if isinstance(obj, dict) else obj:
            _containers(v, acc)
    return acc


def _ctx() -> EpisodeContext:
    return EpisodeContext(
        episode_id="ep-1", host_key="web-01", reference_time="2026-09-01T14:00:00",
        members=(
            EpisodeMember(alarm_id="p1", source="polestar", alarm_name="CPU", kind="cpu",
                          occurred_at="2026-09-01T13:55:00"),
            EpisodeMember(alarm_id="j1", source="jennifer", alarm_name="Q", was_kinds=("queuing",),
                          occurred_at="2026-09-01T14:00:00"),
        ),
        links=(EpisodeLink(rule_id="R1", cause_alarm_id="p1", effect_alarm_id="j1",
                           lag_seconds=300.0),),
    )


def _cases():
    verdict = ImportanceVerdict(level="경고", confidence="none", escalate=False)
    answer = "메모리 고갈 ← polestar_metric_trend\napm_service_stats 지연 확인\n추정"
    for labels in (False, True):
        for corr in (None, {"timeline": [{"t_offset_min": -3, "kind": "metric", "detail": "cpu"}],
                            "notes": ["n"]}):
            for ctx in (None, _ctx()):
                kw = dict(answer=answer, verdict=verdict,
                          tool_names=["polestar_metric_trend", "apm_service_stats"],
                          remediation=["재기동"], limitations=["한계"], correlation=corr,
                          source_labels=labels, cross_source=ctx)
                yield kw


def test_no_shared_containers_inside_briefing_or_with_inputs():
    for kw in _cases():
        before = repr(kw)
        b = build_briefing(**kw)
        found = _containers(b, [])
        ids = [id(c) for c in found]
        assert len(ids) == len(set(ids)), "반환 dict 안에서 같은 리스트/dict를 두 곳이 공유한다"
        inputs = {id(kw[k]) for k in ("tool_names", "remediation", "limitations", "correlation")
                  if kw[k] is not None}
        assert not inputs & set(ids), "호출자 입력 객체가 반환 dict에 그대로 실렸다"
        assert repr(kw) == before, "입력이 변형됐다"


def test_timeline_edit_does_not_leak_into_hypotheses_or_rules():
    kw = next(k for k in _cases() if k["cross_source"] is not None and not k["source_labels"])
    b = build_briefing(**kw)
    snapshot = repr(b["root_cause_hypotheses"]), repr(b["hypotheses"])
    b["timeline"].append("X")
    assert (repr(b["root_cause_hypotheses"]), repr(b["hypotheses"])) == snapshot
