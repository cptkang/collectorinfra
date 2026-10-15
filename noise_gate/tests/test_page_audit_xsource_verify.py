"""plans/162 §11 W0-X 독립 검증 테스트 (verifier — 구현 테스트 `test_page_audit_xsource`와 별도).

- ③ V0 ↔ replay_noise 이탈 원인이
「원인 티어 출처」 하나뿐인지 · 시계 정의(판정 시각 대 발생 시각)가
  사건 경계를 바꾸는 경우의 특성화.
- V1(반복 유지)·V2(창 확대) 의미 — idle로 닫힌 사건을 되살리지 않음 · 창은 넓히기만(max).
- 누출 — IPv4 경계 변형(끝 점·5묶음·`::ffff:`·`_` 구분·전각·ZWSP)이 인스턴스명·도메인명·URL·
  hostname·앱 영향 오류 문구 안에 있을 때 출력에 남지 않는다(stream · no-stream).
- 값 단위 허용 목록 구멍 — 결함 재현은 `xfail(strict=True)`로 둔다(고치면 XPASS → 표지를 뗀다).

실 Redis·LLM·DB·게이트웨이를 쓰지 않는다.
설정은 구현 테스트의 `_config`(검증 대상 필드 명시)를 쓴다.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure.cross_source_rules import load_rules
from noise_gate.scripts import page_audit as pa
from noise_gate.scripts import page_audit_xsource as xsource
from noise_gate.scripts.page_audit_sanitize import Pseudonymizer, fold
from noise_gate.scripts.replay_noise import event_from_payload
from noise_gate.tests import test_page_audit_xsource as xt

KST = timezone(timedelta(hours=9))
RULES = "config/cross_source_rules.yaml"


# ── 공통 ──────────────────────────────────────────────────────────────────────
def _scenario(name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (copy.deepcopy(xt._lines(xt.FIXTURE / "raw_stream.jsonl", [name])),
            copy.deepcopy(xt._lines(xt.FIXTURE / "decisions.jsonl", [name])))


def _run_custom(tmp_path: Path, raws: list[dict[str, Any]], decs: list[dict[str, Any]],
                *, stream: bool = True) -> pa.AuditResult:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in decs), "utf-8")
    raw = [(r["id"].encode(), {b"data": json.dumps(r["data"], ensure_ascii=False).encode()})
           for r in raws]
    groups = {"alarm:raw": [{b"name": b"alarm-workers", b"consumers": 1, b"pending": 0,
                             b"last-delivered-id": b"1791964980000-0", b"lag": 0}]}
    fake = xt.FakeRedis({"alarm:raw": raw, "alarm:dead": []}, groups)
    return pa.run_audit(
        config=xt._config(), decisions_path=path, out_root=tmp_path / "out", hours=24, days=7,
        streams=pa.ReadOnlyStreams(fake) if stream else None, now=xt.NOW, key=xt.KEY,
    )


def _pair_items(*, cause_decided: datetime, effect_decided: datetime,
                cause_time: str = "20261014090000", effect_time: str = "20261014090500"
                ) -> list[xsource.XsItem]:
    """시나리오 A의 폴스타 CPU 원인 + APM 큐잉 증상 — 발생 시각·판정 시각을 따로 준다."""
    raws, decs = _scenario("A")
    raws[0]["data"]["alarmTime"] = cause_time
    raws[1]["data"]["alarmTime"] = effect_time
    return [
        xsource.XsItem(event=event_from_payload(raws[0]["data"]), rec=decs[0], ts=cause_decided,
                       decided=True),
        xsource.XsItem(event=event_from_payload(raws[1]["data"]), rec=decs[1], ts=effect_decided,
                       decided=True),
    ]


def _analyze(items: list[xsource.XsItem], **kw: Any) -> xsource.XsResult:
    args: dict[str, Any] = {"config": xt._config().noise_gate, "pseudo": Pseudonymizer(xt.KEY),
                            "stream_read": True, "dedup_window_seconds": 14400.0}
    return xsource.analyze(items, **{**args, **kw})


# ── ③ 도메인과 차등 ───────────────────────────────────────────────────────────
def test_v0_equals_replay_on_full_fixture_once_b_cause_tier_aligned(tmp_path: Path) -> None:
    """전체 픽스처의 V0 ≠ replay 이탈은 B 원인 티어 하나뿐이다 — 판정 레코드의 B1 티어를 리플레이가
    다시 판정한 티어로 바꾸면 V0 요약이 replay_noise 요약과 완전히 같다(사건·멤버·결합·missing)."""
    expected, rows = xt._replay_summary(None)
    replay_tier = next(r["tier"] for r in rows if r["alarm_id"] == "FAKE-XS-B1")
    assert replay_tier != "dashboard"

    def edit(rec: dict[str, Any]) -> dict[str, Any]:
        if rec.get("alarm_id") == "FAKE-XS-B1" and rec.get("type") is None:
            rec["tier"] = replay_tier
        return rec

    v0 = xt._summary(xt._run(tmp_path, edit=edit))["S13"]["sim"]["variants"]["V0"]
    assert {k: v0[k] for k in xt.COMPARED} == {k: expected[k] for k in xt.COMPARED}


def test_v0_clock_is_decision_time_so_decision_lag_beyond_idle_splits_episode() -> None:
    """특성화 — 발생 간격 300초(< idle 900)라도 판정 간격이 idle을 넘으면 V0은 사건을 닫는다.

    같은 입력을 발생 시각 = 판정 시각으로 주면(리플레이 시계) 한 사건으로 묶여 결합이 생긴다. 즉
    V0과 replay_noise의 사건 수는 「판정 지연 > idle」인 운영 데이터에서 갈릴 수 있다(계획 ③의
    「차등 0」은 판정 지연이 작은 픽스처에서만 성립)."""
    t0 = datetime(2026, 10, 14, 9, 0, 5, tzinfo=KST)
    late = _analyze(_pair_items(cause_decided=t0, effect_decided=t0 + timedelta(seconds=1205)))
    v0 = late.sections["S13"]["sim"]["variants"]["V0"]
    assert (v0["episodes"], v0["judged"]) == (2, 0)
    assert late.rows[1] is not None and late.rows[1]["sim"]["V0"] == "member"

    prompt = _analyze(_pair_items(cause_decided=t0, effect_decided=t0 + timedelta(seconds=300)))
    v0p = prompt.sections["S13"]["sim"]["variants"]["V0"]
    assert (v0p["episodes"], v0p["judged"]) == (1, 1)


def test_v0_decision_order_inversion_loses_link() -> None:
    """특성화 — 원인(폴스타)의 판정이 증상(APM)보다 늦으면 V0은 결합을 만들지 못한다
    (증상 관측 시점에
    원인 멤버가 없다). 발생 순으로 재생하는 replay_noise는 결합한다."""
    t0 = datetime(2026, 10, 14, 9, 6, 0, tzinfo=KST)
    inv = _analyze(_pair_items(cause_decided=t0 + timedelta(seconds=30), effect_decided=t0))
    v0 = inv.sections["S13"]["sim"]["variants"]["V0"]
    assert v0["episodes"] == 1 and v0["judged"] == 0


def test_episode_input_does_not_mutate_event() -> None:
    """`_Capture` 어댑터 조립은 원 이벤트를 바꾸지 않고(`server_identity` 그대로),
    리플레이 조립과 같은
    사건 알람을 낸다(존·키·지문·kind)."""
    from noise_gate.scripts.replay_noise import _cross_source_signal, build_tracker

    raws, _ = _scenario("A")
    ev = event_from_payload(raws[0]["data"])
    alarm, clear = xsource.episode_input(ev)
    assert ev.server_identity is None and clear is False
    tracker = build_tracker("shadow")
    _cross_source_signal(tracker, event_from_payload(raws[0]["data"]))
    member = tracker.episodes()[0].members[0].alarm
    assert alarm == member


# ── V1 · V2 의미 ─────────────────────────────────────────────────────────────
def _nodes(items: list[xsource.XsItem]) -> list[Any]:
    zones: dict[str, str] = {}
    return [xsource._node(i, it, zones) for i, it in enumerate(items)]


def test_v1_recurrence_keeps_open_episode_but_does_not_revive_closed_one() -> None:
    """V1 — 반복 시각에 sweep 뒤 `episode_for`로 얻은 열린 사건만 갱신한다.

    원인 판정 t0 · 증상 판정 t0+1500. 반복 t0+800(idle 안)은 사건을 살려
    증상이 결합(window 누락)되고,
    반복 t0+1000(idle 900 밖)은 이미 닫힌 사건을 되살리지 않는다."""
    rules = load_rules(RULES)
    assert rules is not None
    t0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=KST)
    items = _pair_items(cause_decided=t0, effect_decided=t0 + timedelta(seconds=1500),
                        effect_time="20261014092500")
    nodes = _nodes(items)
    zone, hk = nodes[0].zone, nodes[0].host_key
    assert zone and hk
    base, _, _ = xsource.simulate(nodes, rules, idle_seconds=900)
    assert base[1] == "member"
    kept, _, _ = xsource.simulate(nodes, rules, idle_seconds=900,
                                  recurrences=[(t0.timestamp() + 800, zone, hk)])
    assert kept[1].startswith("link") and "window" in kept[1]
    dead, _, _ = xsource.simulate(nodes, rules, idle_seconds=900,
                                  recurrences=[(t0.timestamp() + 1000, zone, hk)])
    assert dead[1] == "member"
    other, _, _ = xsource.simulate(nodes, rules, idle_seconds=900,
                                   recurrences=[(t0.timestamp() + 800, zone, "other-key")])
    assert other[1] == "member"


def test_v2_only_widens_cause_window(tmp_path: Path) -> None:
    """V2 — `cause_before_seconds = max(기존, dedup)` · dedup 창이 규칙 창보다 짧으면 V2 = V0."""
    items = _pair_items(cause_decided=datetime(2026, 10, 14, 9, 0, 5, tzinfo=KST),
                        effect_decided=datetime(2026, 10, 14, 9, 15, 5, tzinfo=KST),
                        effect_time="20261014091500")
    narrow = _analyze(items, dedup_window_seconds=60.0).sections["S13"]["sim"]["variants"]
    assert narrow["V0"]["missing"] == {"window": 1}
    assert narrow["V2"] == narrow["V0"]
    wide = _analyze(items, dedup_window_seconds=14400.0).sections["S13"]["sim"]["variants"]
    assert "window" not in wide["V2"]["missing"] and wide["V2"]["would_demote"] == 1


# ── ① 누출 — 적대적 경계 변형 ───────────────────────────────────────────────────
INST = "fake-adv01_198_51_100_7_jvm"
DOM = "dom-192.0.2.9."
URL = "http://198.51.100.9:8080/ctx/::ffff:192.0.2.10"
UNRESOLVED_INST = "inst-１９２．０．２．１１-x"
ZW_INST = "fa​ke-zw-192.0.2.12.5"
ZW_HOST = "fake-zw​02"
APP_ERR = "gateway_error — ConnectError: http://fake-gw.example.test:8080 198.51.100.10 refused"
ADV_SECRETS = (
    INST, DOM.rstrip("."), "192.0.2.9", "198_51_100_7", "198.51.100", "192.0.2", "::ffff",
    UNRESOLVED_INST, "fake-zw-192", "zw02", "fake-adv01", "adv01", "fake-gw", "connecterror",
    "refused", "fake",
)


def _adversarial() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raws, decs = _scenario("A")
    pol, apm = raws
    pol["data"].update(hostname="FAKE-ADV01.example.test", serverName="fake-adv01-reg")
    apm["data"].update(hostname="fake-adv01", serverName="fake-adv01", resourceName=INST,
                       resourceAncestry=f"제니퍼 > cm_prd > {DOM} > {INST}")
    apm["data"]["apm"].update(instance_name=INST, domain_name=DOM, application=URL)
    unresolved = copy.deepcopy(apm)
    unresolved["id"] = "1791936300000-0"
    unresolved["data"].update(
        alarmId="jennifer:fakexsadvu00000000", hostname="", serverName=UNRESOLVED_INST,
        resourceName=UNRESOLVED_INST, alarmTime="20261014090400",
        resourceAncestry=f"제니퍼 > cm_prd > {DOM} > {UNRESOLVED_INST}")
    unresolved["data"]["apm"].update(instance_name=UNRESOLVED_INST, match_reason="unresolved",
                                     match_confidence="none")
    zw = copy.deepcopy(apm)
    zw["id"] = "1791936360000-0"
    zw["data"].update(alarmId="jennifer:fakexsadvz00000000", hostname=ZW_HOST, serverName=ZW_INST,
                      resourceName=ZW_INST, alarmTime="20261014090500",
                      resourceAncestry=f"제니퍼 > cm_prd > {DOM} > {ZW_INST}")
    zw["data"]["apm"].update(instance_name=ZW_INST)
    dp, da = decs
    dp["server_name"] = "fake-adv01-reg"
    dp["stage_evidence"]["cross_source"]["host_key"] = "fake-adv01"
    dp["stage_evidence"]["app_impact_error"] = APP_ERR
    da.update(server_name="fake-adv01", resource_name=INST)
    da["stage_evidence"]["cross_source"]["host_key"] = "fake-adv01"
    du = copy.deepcopy(da)
    du.update(alarm_id="jennifer:fakexsadvu00000000", server_name=UNRESOLVED_INST,
              resource_name=UNRESOLVED_INST, ts="2026-10-14T00:05:02+00:00", stage_evidence={})
    dz = copy.deepcopy(da)
    dz.update(alarm_id="jennifer:fakexsadvz00000000", server_name=ZW_INST, resource_name=ZW_INST,
              ts="2026-10-14T00:06:02+00:00", stage_evidence={})
    return [pol, apm, unresolved, zw], [dp, da, du, dz]


@pytest.mark.parametrize("stream", [True, False])
def test_adversarial_ip_variants_inside_names_do_not_leak(tmp_path: Path, stream: bool) -> None:
    """IPv4 경계 변형이 인스턴스명·도메인명·URL·hostname(ZWSP)·앱 영향 문구 안에 있어도 출력 5개
    파일에 남지 않고 누출 검사가 통과한다(가명·고정 어휘·수치만)."""
    raws, decs = _adversarial()
    result = _run_custom(tmp_path, raws, decs, stream=stream)
    assert result.exit_code == 0, result.leak
    text = xt._folded_outputs(result.out_dir)
    for secret in ADV_SECRETS:
        assert fold(secret) not in text, secret
    rows = xt._rows(result)
    assert rows[0]["xs"]["app_impact"]["code"] == "gateway_error"
    if stream:
        assert rows[0]["xs"]["hk"] == rows[1]["xs"]["hk"]
        assert rows[2]["xs"]["hk"] is None and rows[2]["xs"]["inst"].startswith("I-")


# ── 값 단위 허용 목록 구멍(결함 재현) ──────────────────────────────────────────
def _poison(tier: str | None = None, mode: str | None = None) -> Any:
    def edit(rec: dict[str, Any]) -> dict[str, Any]:
        if rec.get("alarm_id") == "FAKE-XS-A1":
            if tier is not None:
                rec["tier"] = tier
            if mode is not None:
                rec["stage_evidence"]["cross_source"]["mode"] = mode
        return rec
    return edit


def test_s14_cause_tiers_value_allow_listed(tmp_path: Path) -> None:
    """S14 원인 최종 티어 분포 키는 TIERS 또는 `<other>`여야 한다."""
    result = xt._run(tmp_path, ["A"], edit=_poison(tier="secretTierValue42"))
    text = (result.out_dir / "summary.json").read_text("utf-8")
    assert "secretTierValue42" not in text


def test_s13_actual_modes_value_allow_listed(tmp_path: Path) -> None:
    """S13 실제 기록 모드 분포 키는 모드 열거(off·shadow·annotate·enforce) 또는
    `<other>`여야 한다."""
    result = xt._run(tmp_path, ["A"], edit=_poison(mode="secretModeValue77"))
    text = (result.out_dir / "summary.json").read_text("utf-8")
    assert "secretModeValue77" not in text


# ── 기존 출력 불변 보강 ─────────────────────────────────────────────────────────
def test_existing_fixture_has_no_apm_and_xs_adds_no_leak_identifiers(tmp_path: Path) -> None:
    """기존 W0 픽스처 — 상관 진단이 누출 검사 식별자 수·어휘 제외 수를 바꾸지 않는다(기준 9ccc44f와
    같은 32·0 — 독립 대조값)."""
    result = xt._base_run(tmp_path, None, True)
    assert result.exit_code == 0
    leak = json.loads((result.out_dir / "leak_check.json").read_text("utf-8"))
    assert (leak["identifiers_checked"], leak["vocabulary_identifiers_excluded"]) == (32, 0)
