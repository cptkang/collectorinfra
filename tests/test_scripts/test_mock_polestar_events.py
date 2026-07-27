"""scripts/mock_polestar_events.py 단위 테스트 (Plan 65 §6 #7).

검증 범위(서버·Redis·LLM 불필요 — 순수 단위):
    - 이벤트 빌더: 페이로드가 §2.2 스키마·§2.4 매핑 규칙 준수(키 집합·alarmTime·alarmId 형식).
    - 카탈로그: 번호-시나리오 매핑 고정(1~12·이름·stub).
    - 메뉴 디스패치: parse_menu_input 순수 함수.
    - 전제 점검·판정 로직: check_preconditions / evaluate_step / format_audit.

전송·판정 e2e(--send 경유·서버 필요)는 RUN_E2E=1 옵트인 게이트.
"""

from __future__ import annotations

import os
import re
import socket
from types import SimpleNamespace

import pytest

from scripts import mock_polestar_events as mpe


# ─── 이벤트 빌더 (§2.2 스키마 · §2.4 매핑) ─────────────────────────────────────

def test_make_payload_keys_match_required_schema():
    """make_payload 출력 키가 §2.2 확정 스키마와 정확히 일치한다."""
    payload = mpe.make_payload(
        db_id="polestar_pg", server_name="srv1", severity=2, alarm_name="가용성",
        alarm_id="MOCK-abcd1234-1",
    )
    assert set(payload.keys()) == mpe.REQUIRED_PAYLOAD_KEYS


def test_make_payload_alarm_time_format():
    """alarmTime은 %Y%m%d%H%M%S(14자리 숫자) 형식으로 채워진다."""
    payload = mpe.make_payload(
        db_id="d", server_name="s", severity=2, alarm_name="a", alarm_id="MOCK-x-1",
    )
    assert re.fullmatch(r"\d{14}", payload["alarmTime"])


def test_make_payload_hostname_defaults_to_server_name():
    """hostname 미지정 시 serverName과 동일하게 채운다."""
    payload = mpe.make_payload(
        db_id="d", server_name="hostA", severity=2, alarm_name="a", alarm_id="MOCK-x-1",
    )
    assert payload["hostname"] == "hostA"


def test_mock_alarm_id_format():
    """alarmId는 MOCK-<run_id>-<seq> 형식이다(판정 추적 키, §2.2)."""
    assert mpe.mock_alarm_id("a1b2c3d4", 2) == "MOCK-a1b2c3d4-2"


def test_severity_mapping_constants():
    """§2.4 severity 매핑 가정(DOWN=2, UP=0, 임계=1, 설비 단락=3)."""
    assert mpe.SEV_CLEAR == 0
    assert mpe.SEV_THRESHOLD == 1
    assert mpe.SEV_AVAIL_DOWN == 2
    assert mpe.SEV_FACILITY_PAGE == 3


@pytest.mark.parametrize("scen", mpe.SCENARIOS, ids=lambda s: s.name)
def test_every_scenario_step_payload_conforms(scen):
    """모든 시나리오의 각 스텝 페이로드가 §2.2 스키마·MOCK alarmId 규칙을 준수한다."""
    steps = scen.build("run5678", "polestar_pg")
    assert len(steps) >= 1
    for step in steps:
        assert set(step.payload.keys()) == mpe.REQUIRED_PAYLOAD_KEYS
        assert step.payload["alarmId"].startswith("MOCK-run5678-")
        assert isinstance(step.payload["severity"], int)
        assert re.fullmatch(r"\d{14}", step.payload["alarmTime"])


def test_scenario_severity_assignments():
    """대표 시나리오가 §2.4 매핑에 맞는 severity를 부여한다."""
    sev3 = mpe.SCENARIOS_BY_NAME["sev3-page"].build("r", "d")
    assert sev3[0].payload["severity"] == mpe.SEV_FACILITY_PAGE
    maint = mpe.SCENARIOS_BY_NAME["maint-suppress"].build("r", "d")
    assert maint[0].payload["severity"] == mpe.SEV_AVAIL_DOWN
    low = mpe.SCENARIOS_BY_NAME["low-suppress"].build("r", "d")
    assert low[0].payload["severity"] == mpe.SEV_THRESHOLD
    clear = mpe.SCENARIOS_BY_NAME["clear"].build("r", "d")
    assert clear[-1].payload["severity"] == mpe.SEV_CLEAR   # 2건째 UP = 클리어


def test_condition_log_bracket_format():
    """conditionLog는 §2.4 대괄호 조건 원문 형식을 따른다."""
    maint = mpe.SCENARIOS_BY_NAME["maint-suppress"].build("r", "d")
    assert maint[0].payload["conditionLog"].startswith("[")
    assert "DOWN" in maint[0].payload["conditionLog"]


def test_semantic_dup_distinct_fingerprint_fields():
    """semantic-dup 쌍은 서버명이 달라 지문이 분리된다(동일 지문이면 결정적 dedup으로 주석 미도달)."""
    steps = mpe.SCENARIOS_BY_NAME["semantic-dup"].build("r", "d")
    assert steps[0].payload["serverName"] != steps[1].payload["serverName"]
    # 텍스트(alarm_name)는 유사 유지 — 근접중복 후보 성립 조건.
    assert steps[0].payload["alarmName"] == steps[1].payload["alarmName"]


# ─── 카탈로그 (번호-시나리오 매핑 고정) ────────────────────────────────────────

def test_catalog_numbers_are_contiguous_1_to_14():
    """시나리오 번호는 1~14 연속이며 중복이 없다(고정 계약 — E7 커버리지 [13]·[14] 확장)."""
    numbers = [s.number for s in mpe.SCENARIOS]
    assert numbers == list(range(1, 15))


def test_catalog_name_by_number_fixed():
    """번호 → 시나리오 이름 매핑이 고정되어 있다(§4 카탈로그 + E7 커버리지 [13]·[14])."""
    expected = {
        1: "sev3-page", 2: "low-suppress", 3: "maint-suppress", 4: "dup-suppress",
        5: "clear", 6: "distinct-pair", 7: "recur-audit", 8: "cross-host",
        9: "cascade", 10: "change-corr", 11: "semantic-dup", 12: "invest-trigger",
        13: "non-alarm", 14: "net-site-cascade",
    }
    assert {n: s.name for n, s in mpe.SCENARIOS_BY_NUMBER.items()} == expected


def test_catalog_categories():
    """카테고리 배정(기본 6 / Plan 60 5 / SRE 1 / Plan 60 E7 2)."""
    from collections import Counter

    counts = Counter(s.category for s in mpe.SCENARIOS)
    assert counts == {"basic": 6, "plan60": 5, "sre": 1, "plan60e7": 2}


def test_invest_trigger_is_stub_with_reason():
    """invest-trigger는 R8 미구현 스텁이며 보류 사유를 보유한다(submit 코드 없음)."""
    scen = mpe.SCENARIOS_BY_NAME["invest-trigger"]
    assert scen.stub is True
    assert "R8" in scen.stub_reason


def test_plan60_scenarios_declare_required_flags():
    """Plan 60 플래그 시나리오는 필요 플래그를 선언한다(recur-audit 제외 — 상시 on)."""
    assert mpe.SCENARIOS_BY_NAME["cross-host"].required_flags
    assert mpe.SCENARIOS_BY_NAME["cascade"].required_flags
    assert mpe.SCENARIOS_BY_NAME["change-corr"].required_flags
    assert mpe.SCENARIOS_BY_NAME["semantic-dup"].requires_model is True
    # recur-audit(E1)은 게이트 상시 감사라 별도 플래그 불요.
    assert mpe.SCENARIOS_BY_NAME["recur-audit"].required_flags == ()


# ─── E7 커버리지 신규 시나리오 ([13] non-alarm · [14] net-site-cascade) ─────────

def test_non_alarm_scenario_flag_and_text():
    """[13] non-alarm은 E7-b 비알람 필터 플래그를 선언하고 비알람 마커 텍스트를 담는다."""
    scen = mpe.SCENARIOS_BY_NAME["non-alarm"]
    assert scen.number == 13
    assert scen.category == "plan60e7"
    # 필요 플래그: non_alarm_filter_enabled(E7-b).
    assert scen.required_flags == (
        ("non_alarm_filter_enabled", "NOISE_NON_ALARM_FILTER_ENABLED"),
    )
    steps = scen.build("r", "d")
    assert len(steps) == 1
    # 비알람 마커(승인/요청/바랍니다) 존재 + 알람 마커 부재(가명화 담당자 ○○○).
    text = steps[0].payload["alarmName"] + steps[0].payload["conditionLog"]
    assert "승인" in text
    assert "○○○" in text  # 가명화 유지
    assert steps[0].expect_tiers == frozenset({mpe.TIER_SUPPRESS})


def test_net_site_cascade_scenario_flags_and_marker():
    """[14] net-site-cascade은 E7-c/d 플래그를 선언하고 serverName에 ||(장애) 마커를 싣는다."""
    scen = mpe.SCENARIOS_BY_NAME["net-site-cascade"]
    assert scen.number == 14
    assert scen.category == "plan60e7"
    # 필요 플래그: format_tolerant_parsing_enabled(E7-c) + correlation_site_dimension_enabled(E7-d).
    attrs = {attr for attr, _env in scen.required_flags}
    assert attrs == {"format_tolerant_parsing_enabled", "correlation_site_dimension_enabled"}
    steps = scen.build("r", "d")
    assert len(steps) >= 2  # 동일 사이트 네트워크 장비 2대+
    for st in steps:
        # 사이트 마커는 serverName에 실린다(extract_site_token 파싱 대상).
        assert "||(장애) 세종대" in st.payload["serverName"]
        assert st.payload["resourceType"] == "network.Switch"
        assert st.payload["severity"] == mpe.SEV_AVAIL_DOWN


# ─── 메뉴 디스패치 (parse_menu_input 순수 함수) ────────────────────────────────

def test_parse_menu_input_scenario_number():
    """번호 입력은 해당 시나리오로 해석된다(공백 허용)."""
    action, scen = mpe.parse_menu_input(" 4 ")
    assert action == "scenario"
    assert scen.name == "dup-suppress"


def test_parse_menu_input_aux_keys():
    """보조 키 l/v/q(대소문자 무관)를 액션으로 해석한다."""
    assert mpe.parse_menu_input("q")[0] == "quit"
    assert mpe.parse_menu_input("Q")[0] == "quit"
    assert mpe.parse_menu_input("l")[0] == "list"
    assert mpe.parse_menu_input("v")[0] == "toggle"


def test_parse_menu_input_empty_and_invalid():
    """빈 입력·범위 밖 번호·비숫자는 empty/invalid로 구분된다."""
    assert mpe.parse_menu_input("")[0] == "empty"
    assert mpe.parse_menu_input("   ")[0] == "empty"
    assert mpe.parse_menu_input("99")[0] == "invalid"
    assert mpe.parse_menu_input("abc")[0] == "invalid"


# ─── 전제 점검 (check_preconditions) ───────────────────────────────────────────

def _fake_config(**overrides):
    base = dict(
        enable_noise_gate=True,
        cross_host_correlation_enabled=False,
        multi_hop_cascade_enabled=False,
        dependency_suppression=False,
        change_correlation_enabled=False,
        semantic_dedup_annotation_enabled=False,
        embedding_model_path="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_precondition_basic_ok_when_gate_on():
    """기본 시나리오는 게이트 on이면 전제를 충족한다."""
    ok, reasons = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["sev3-page"], _fake_config()
    )
    assert ok is True
    assert reasons == []


def test_precondition_fails_when_gate_off():
    """게이트 off면 미충족 사유(ENABLE_NOISE_GATE)를 반환한다."""
    ok, reasons = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["sev3-page"], _fake_config(enable_noise_gate=False)
    )
    assert ok is False
    assert any("ENABLE_NOISE_GATE" in r for r in reasons)


def test_precondition_cross_host_requires_flag():
    """cross-host는 상관 플래그 off면 미충족, on이면 충족."""
    off_ok, off_reasons = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["cross-host"], _fake_config()
    )
    assert off_ok is False
    assert any("CROSS_HOST_CORRELATION" in r for r in off_reasons)

    on_ok, _ = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["cross-host"],
        _fake_config(cross_host_correlation_enabled=True),
    )
    assert on_ok is True


def test_precondition_semantic_dup_requires_model_dir():
    """semantic-dup은 플래그 on이어도 모델 디렉토리가 없으면 미충족."""
    ok, reasons = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["semantic-dup"],
        _fake_config(semantic_dedup_annotation_enabled=True,
                     embedding_model_path="/no/such/model/dir"),
    )
    assert ok is False
    assert any("EMBEDDING_MODEL_PATH" in r for r in reasons)


def test_precondition_stub_always_deferred():
    """invest-trigger(stub)는 항상 미충족으로 보류 사유를 반환한다."""
    ok, reasons = mpe.check_preconditions(
        mpe.SCENARIOS_BY_NAME["invest-trigger"], _fake_config()
    )
    assert ok is False
    assert reasons == [mpe.SCENARIOS_BY_NAME["invest-trigger"].stub_reason]


def test_precondition_none_config():
    """config 로드 실패(None)면 미충족 사유를 반환한다."""
    ok, reasons = mpe.check_preconditions(mpe.SCENARIOS_BY_NAME["sev3-page"], None)
    assert ok is False
    assert reasons


# ─── 판정 로직 (evaluate_step / format_audit) ──────────────────────────────────

def test_evaluate_step_none_record():
    """레코드 미검출은 None(미확정) 상태로 판정한다(침묵 실패 아님)."""
    step = mpe.Step(payload={}, label="x")
    state, detail = mpe.evaluate_step(step, None)
    assert state is None
    assert "없음" in detail


def test_evaluate_step_expect_recurrence():
    """dedup 기대 스텝은 type=recurrence 레코드에 ✔, 결정 레코드엔 ✘."""
    step = mpe.Step(payload={}, label="x", expect_recurrence=True)
    ok, _ = mpe.evaluate_step(step, {"type": "recurrence", "count": 2})
    assert ok is True
    bad, _ = mpe.evaluate_step(step, {"tier": "page"})
    assert bad is False


def test_evaluate_step_expect_no_dedup():
    """독립 판정 기대(음성 대조군)는 결정 레코드에 ✔, 재발 레코드엔 ✘."""
    step = mpe.Step(payload={}, label="x", expect_no_dedup=True)
    ok, _ = mpe.evaluate_step(step, {"tier": "ticket"})
    assert ok is True
    bad, _ = mpe.evaluate_step(step, {"type": "recurrence", "count": 2})
    assert bad is False


def test_evaluate_step_tier_match():
    """tier 기대 집합과 일치/불일치를 판정한다."""
    step = mpe.Step(payload={}, label="x", expect_tiers=frozenset({"suppress"}))
    ok, _ = mpe.evaluate_step(step, {"tier": "suppress"})
    assert ok is True
    bad, _ = mpe.evaluate_step(step, {"tier": "page"})
    assert bad is False


def test_evaluate_step_any_decision():
    """expect_tiers=None이면 결정 레코드 존재만으로 ✔(1건째 통과 판정)."""
    step = mpe.Step(payload={}, label="x", expect_tiers=None)
    ok, _ = mpe.evaluate_step(step, {"tier": "ticket"})
    assert ok is True


def test_format_audit_fields():
    """감사 필드(최상위 correlation_meta·signals.cascaded)를 요약 출력한다."""
    rec = {
        "tier": "suppress",
        "correlation_meta": {"member_seq": 2},
        "signals": {"cascaded": True, "correlated": True, "root_resource": "r1"},
    }
    out = mpe.format_audit(rec)
    assert "correlation_meta" in out
    assert "cascaded" in out


def test_format_audit_empty():
    """감사 필드가 없으면 안내 문구를 반환한다."""
    out = mpe.format_audit({"tier": "page", "signals": {"cascaded": None}})
    assert "없음" in out


# ─── 크래시 방지: 서버 부재 시 graceful 실패 (verification #4) ──────────────────

def _closed_port() -> int:
    """확실히 닫힌 포트 번호를 얻는다(바인딩 후 즉시 해제)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_dispatch_graceful_when_server_down(tmp_path, capsys):
    """alarm_server 미기동(닫힌 포트) 시 dispatch가 크래시 없이 사유를 출력한다."""
    sender = mpe.TcpSender("127.0.0.1", _closed_port(), timeout=1.0)
    judge = mpe.Judge(str(tmp_path / "decisions.jsonl"))
    ctx = mpe.RunContext(
        sender=sender, judge=judge, noise_config=_fake_config(),
        db_id="polestar_pg", timeout=1.0, wait_enabled=True,
    )
    # 예외 없이 반환해야 한다.
    mpe.dispatch_scenario(mpe.SCENARIOS_BY_NAME["dup-suppress"], ctx)
    out = capsys.readouterr().out
    assert "전송 실패" in out


# ─── e2e 옵트인 (서버 필요 — RUN_E2E=1) ────────────────────────────────────────

@pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="RUN_E2E=1 옵트인(서버 필요)")
def test_send_dup_suppress_e2e():
    """--send dup-suppress가 실제 서버 경로로 주입·판정까지 완주한다(옵트인)."""
    rc = mpe.main(["--send", "dup-suppress"])
    assert rc == 0
