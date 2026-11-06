"""plans/215 W9 — D1 판독 순수 함수(§4.7 · §14 D1 벡터 · 수용 14 · 결정성 · 「정상」 어휘 0)."""

from __future__ import annotations

import random
from typing import Any

from noise_gate.domain import biz_diagnosis as d
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, STATE_ACTIVE, match_key_for
from noise_gate.domain.biz_diagnosis import DiagAlarm, DiagLink, DiagMember, DiagMetrics
from noise_gate.domain.biz_service_keys import (
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import SmsThreshold
from noise_gate.domain.biz_service_state import ApmSignalFact, SmsMetricFact

NOW = 1_800_000_000.0
WINDOW = 15
TH = {"cpu": SmsThreshold(80.0, 90.0)}


def _inst(name: str, layer: str = "was") -> DiagMember:
    key = apm_instance_member_key("s1", "7", SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, name))
    assert key is not None
    return DiagMember(KIND_APM_INSTANCE, key, name, layer)


def _srv(name: str, rid: int, db_id: str = "polestar_cm_gp", layer: str = "was") -> DiagMember:
    key = sms_member_key(db_id, rid)
    assert key is not None
    return DiagMember(KIND_SMS_SERVER, key, name, layer, db_id, str(rid))


INST = _inst("ibk-was-01")
SRV = _srv("ibkwas01", 101)
STAT = NOW - 600  # 정각 통계가 창과 겹친다


def _run(members: list[DiagMember], **kw: Any) -> dict[str, Any]:
    kw.setdefault("thresholds", TH)
    return d.diagnose_service(members=members, now=NOW, window_minutes=WINDOW, **kw)


def _burst(*members: DiagMember) -> tuple[ApmSignalFact, ...]:
    return tuple(ApmSignalFact(m.member_key, "was_error_burst", "CRITICAL") for m in members)


def _link(*targets: DiagMember) -> dict[str, DiagLink]:
    return {INST.member_key: DiagLink(STATE_ACTIVE, tuple(t.member_key for t in targets))}


def _alarm(m: DiagMember, at: float, alarm_id: str = "a1", sev: int = 2) -> DiagAlarm:
    return DiagAlarm(m.member_key, alarm_id, sev, at)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


# ── §14 D1 벡터 ─────────────────────────────────────────────────────────
def test_r3_apm_and_linked_server_alarm_same_window() -> None:
    out = _run([INST, SRV], alarms=[_alarm(SRV, NOW - 120)], links=_link(SRV),
               metrics=DiagMetrics(apm_signals=_burst(INST), apm_observed_at=NOW - 30))
    assert out["verdict"] == "R3"
    assert out["representative"]["member_key"] == SRV.member_key
    assert out["representative"]["rule"] == "first_sms"
    inst_row = next(r for r in out["anomalous_members"] if r["kind"] == KIND_APM_INSTANCE)
    assert inst_row["linked_servers"] == [SRV.member_key]
    # 최초 이상 순 — 서버 알람(120초 전)이 APM 관측(30초 전)보다 먼저
    assert [t["member_key"] for t in out["timeline"]] == [SRV.member_key, INST.member_key]
    assert out["hypothesis_label"] == "가설 · 참고용"
    assert "상관≠인과" in out["correlation_note"]


def test_r4_linked_server_no_signal_representative_is_linked() -> None:
    out = _run([INST, SRV], links=_link(SRV), metrics=DiagMetrics(
        apm_signals=_burst(INST), apm_observed_at=NOW - 30,
        sms_metrics=(SmsMetricFact(SRV.member_key, "cpu", 40.0),), sms_stat_at=STAT,
        sms_stat_label="12시 통계"))
    assert out["verdict"] == "R4"
    assert out["representative"]["member_key"] == SRV.member_key
    assert out["representative"]["rule"] == "apm_linked"
    assert [r["member_key"] for r in out["anomalous_members"]] == [INST.member_key]


def test_r5_no_link_gap_and_no_representative() -> None:
    out = _run([INST, SRV], metrics=DiagMetrics(apm_signals=_burst(INST)))
    assert out["verdict"] == "R5"
    assert "연계 없음" in out["verdict_text"]
    gap = next(g for g in out["gaps"] if g["code"] == "no_link")
    assert gap["label"] == "연계 없음" and gap["member_keys"] == [INST.member_key]
    assert out["representative"] is None
    assert "연계 없음" in out["representative_reason"]


def test_r2_three_of_four_servers_representative_first() -> None:
    srvs = [_srv(f"web{i}", 200 + i) for i in range(4)]
    alarms = [_alarm(srvs[2], NOW - 300, "x2"), _alarm(srvs[0], NOW - 200, "x0"),
              _alarm(srvs[1], NOW - 100, "x1")]
    out = _run(srvs, alarms=alarms)
    assert out["verdict"] == "R2"
    assert out["representative"]["member_key"] == srvs[2].member_key


def test_tie_same_time_smaller_db_and_resource_id() -> None:
    a, b, c = _srv("b", 10), _srv("a", 9), _srv("z", 1, db_id="polestar_cm_yd")
    out = _run([a, b, c, _srv("q", 50), _srv("r", 51), _srv("s", 52)],
               alarms=[_alarm(a, NOW - 60, "1"), _alarm(b, NOW - 60, "2"),
                       _alarm(c, NOW - 60, "3")])
    # db_id 순(gp < yd) → resource_id 수치 순(9 < 10)
    assert out["representative"]["member_key"] == b.member_key


# ── 그 밖의 판독 규칙 ───────────────────────────────────────────────────
def test_r1_nothing_anomalous() -> None:
    out = _run([INST, SRV], metrics=DiagMetrics(
        sms_metrics=(SmsMetricFact(SRV.member_key, "cpu", 10.0),), sms_stat_at=STAT))
    assert out["verdict"] == "R1" and out["verdict_text"] == "이상 구성원 없음"
    assert out["anomalous_members"] == [] and out["representative"] is None


def test_r6_server_only() -> None:
    others = [_srv("o1", 301), _srv("o2", 302)]
    out = _run([INST, SRV, *others], alarms=[_alarm(SRV, NOW - 60)])
    assert out["verdict"] == "R6"


def test_r2_two_specified_layers_and_unspecified_skipped() -> None:
    web = _srv("web1", 401, layer="web")
    srvs = [web, _srv("d1", 402, layer="db"), _srv("d2", 403, layer="db"), _srv("d3", 404)]
    inst = _inst("ibk-was-01", layer="was")
    out = _run([inst, *srvs], alarms=[_alarm(web, NOW - 60)],
               metrics=DiagMetrics(apm_signals=_burst(inst)))
    assert out["verdict"] == "R2"
    # 계층 미지정만 있으면 계층 조건은 건너뛴다 → APM 연계 없음 R5
    bare_inst = _inst("ibk-was-01", layer="")
    bare_srv = _srv("x", 405, layer="")
    out = _run([bare_inst, bare_srv, _srv("y", 406, layer=""), _srv("w", 407, layer="")],
               alarms=[_alarm(bare_srv, NOW - 60)],
               metrics=DiagMetrics(apm_signals=_burst(bare_inst)))
    assert out["verdict"] == "R5"


def test_r5_when_thresholds_unset_or_stat_out_of_window() -> None:
    sms = (SmsMetricFact(SRV.member_key, "cpu", 40.0),)
    # W0-9 전(임계 미설정) — 이상 신호 없음으로 단정하지 않는다
    out = _run([INST, SRV], links=_link(SRV), thresholds={}, metrics=DiagMetrics(
        apm_signals=_burst(INST), sms_metrics=sms, sms_stat_at=STAT))
    assert out["verdict"] == "R5" and "지표 없음" in out["verdict_text"]
    gap = next(g for g in out["gaps"] if g["code"] == "no_metric")
    assert "임계 미설정" in gap["label"] and gap["member_keys"] == [SRV.member_key]
    # F12 — 창 15분과 겹치는 정각 통계가 없으면 R5
    out = _run([INST, SRV], links=_link(SRV), metrics=DiagMetrics(
        apm_signals=_burst(INST), sms_metrics=sms, sms_stat_at=NOW - 3 * 3600))
    assert out["verdict"] == "R5"
    assert any("창 안 정각 통계 없음" in g["label"] for g in out["gaps"])


def test_member_stat_time_overrides_service_level() -> None:
    other = _srv("ydwas01", 7, db_id="polestar_cm_yd")
    sms = (SmsMetricFact(SRV.member_key, "cpu", 95.0), SmsMetricFact(other.member_key, "cpu", 95.0))
    out = _run([SRV, other], metrics=DiagMetrics(
        sms_metrics=sms, sms_stat_at=STAT,
        sms_stats={SRV.member_key: (STAT, "12시 통계"),
                   other.member_key: (NOW - 3 * 3600, "09시 통계")}))
    assert [r["member_key"] for r in out["anomalous_members"]] == [SRV.member_key]
    gap = next(g for g in out["gaps"] if g["code"] == "no_metric")
    assert gap["member_keys"] == [other.member_key] and "창 안 정각 통계 없음" in gap["label"]
    assert "12시 통계" in out["anomalous_members"][0]["signals"][0]["label"]


def test_sms_threshold_anomaly_counts_as_os() -> None:
    out = _run([INST, SRV], links=_link(SRV), metrics=DiagMetrics(
        apm_signals=_burst(INST), sms_metrics=(SmsMetricFact(SRV.member_key, "cpu", 95.0),),
        sms_stat_at=STAT, sms_stat_label="12시 통계"))
    assert out["verdict"] == "R3"
    srv_row = next(r for r in out["anomalous_members"] if r["kind"] == KIND_SMS_SERVER)
    assert "12시 통계" in srv_row["signals"][0]["label"]


def test_two_linked_servers_no_pick() -> None:
    other = _srv("ibkwas02", 102)
    out = _run([INST, SRV, other], links=_link(SRV, other),
               metrics=DiagMetrics(apm_signals=_burst(INST)))
    assert out["representative"] is None
    assert "2대" in out["representative_reason"] and "D-224" in out["representative_reason"]


def test_old_alarm_outside_window_ignored_open_incident_kept() -> None:
    old = _alarm(SRV, NOW - 3600)
    out = _run([SRV], alarms=[old])
    assert out["verdict"] == "R1"
    out = _run([SRV], alarms=[DiagAlarm(SRV.member_key, "i1", 2, NOW - 3600, True)])
    assert out["verdict"] == "R6"
    assert out["anomalous_members"][0]["signals"][0]["basis"] == "incident"


def test_linked_server_outside_scope_is_gap() -> None:
    out = _run([INST], links=_link(SRV), metrics=DiagMetrics(apm_signals=_burst(INST)),
               hidden_members=1)
    assert out["verdict"] == "R5"
    codes = [g["code"] for g in out["gaps"]]
    assert codes == ["link_outside", "out_of_scope"]
    hidden = out["gaps"][-1]
    assert hidden["count"] == 1 and hidden["member_keys"] == []
    assert SRV.name not in " ".join(_strings(out))


# ── 결정성 · 어휘 ───────────────────────────────────────────────────────
def test_deterministic_under_shuffle() -> None:
    srvs = [_srv(f"s{i}", 500 + i, layer=("web", "was", "")[i % 3]) for i in range(6)]
    insts = [_inst(f"ibk-was-0{i}") for i in range(3)]
    alarms = [_alarm(s, NOW - 60 * (i % 2), f"a{i}") for i, s in enumerate(srvs[:3])]
    sigs = _burst(*insts)
    sms = tuple(SmsMetricFact(s.member_key, "cpu", 50.0 + i) for i, s in enumerate(srvs))
    links = {i.member_key: DiagLink(STATE_ACTIVE, (srvs[0].member_key,)) for i in insts}
    base = _run([*srvs, *insts], alarms=alarms, links=links,
                metrics=DiagMetrics(apm_signals=sigs, sms_metrics=sms, sms_stat_at=STAT))
    rnd = random.Random(7)
    for _ in range(5):
        m, a, s, x = [*srvs, *insts], list(alarms), list(sigs), list(sms)
        for seq in (m, a, s, x):
            rnd.shuffle(seq)
        out = _run(m, alarms=a, links=dict(reversed(list(links.items()))),
                   metrics=DiagMetrics(apm_signals=tuple(s), sms_metrics=tuple(x),
                                       sms_stat_at=STAT))
        assert out == base


def test_no_forbidden_word_in_any_output() -> None:
    texts = [*d.VERDICT_TEXTS.values(), *d.VERDICT_GUIDANCE.values(), d.HYPOTHESIS_LABEL,
             d.CORRELATION_NOTE]
    cases = [
        _run([INST, SRV]),
        _run([INST, SRV], alarms=[_alarm(SRV, NOW - 60)], links=_link(SRV),
             metrics=DiagMetrics(apm_signals=_burst(INST))),
        _run([INST, SRV], links=_link(SRV), metrics=DiagMetrics(
            apm_signals=_burst(INST), sms_metrics=(SmsMetricFact(SRV.member_key, "cpu", 1.0),),
            sms_stat_at=STAT)),
        _run([INST, SRV], metrics=DiagMetrics(apm_signals=_burst(INST))),
        _run([SRV, _srv("b", 2)], alarms=[_alarm(SRV, NOW), _alarm(_srv("b", 2), NOW, "b")]),
        _run([INST, SRV, _srv("c", 3)], alarms=[_alarm(SRV, NOW)], hidden_members=2),
    ]
    assert {c["verdict"] for c in cases} == {"R1", "R2", "R3", "R4", "R5", "R6"}
    for c in cases:
        texts += _strings(c)
    assert not [t for t in texts if "정상" in t]
