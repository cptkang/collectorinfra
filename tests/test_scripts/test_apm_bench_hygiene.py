"""APM 조회 벤치 — 반출 위생(토큰 단위 치환 · 민감 모양만 · 근거 묶음 · 누출 관문).

plans/152 W4 · D-323.

순수 로직 — 네트워크·LLM 0. 디스크 쓰기는 `tmp_path`에만 한다. 이름은 전부 합성 값이다.
"""

from __future__ import annotations

import copy
import json
import random
import string
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import hygiene as hy
from scripts.itam_bench import substitute

INVENTORY = ["ord-was01", "ORD_api02", "pay-was01", "주문WAS", "only-inventory77"]
GUID = "3f2a9c1e-1b2c-4d5e-8f90-123456789abc"
TXID = "-8823412391234"
CANARY = "CANARY-Q7Z"
VERSIONS: dict[str, Any] = {
    "code_sha": "ee13b47",
    "fingerprints": {"F1": "9c2eb9cd7418", "F1_render": "aa", "F2": "99ee2e9f9bf9",
                     "F3": "f5646d317265", "F4": "71ff3e68f138", "registry": "0123456789ab"},
    "apm_section": {"fingerprint": "abcdef012345", "chars": 12344,
                    "view_rows": ["apm.sql_stats", "apm.trace"]},
}


def _record(**over: Any) -> dict[str, Any]:
    """실패 턴 레코드 하나(드라이버 출력 모양 · 판정 붙음)."""
    rec: dict[str, Any] = {
        "v": 1,
        "scenario_id": "APM-T1",
        "turn": 0,
        "split": "dev",
        "covers": ["COV-SQL"],
        "heavy": False,
        "was_touch": False,
        "sensitive": False,
        "prompt_template": "{{inst_1}} SQL 통계",
        "prompt": "ord-was01 SQL 통계",
        "placeholders": {"inst_1": "ord-was01", "guid_1": GUID, "user_1": "op01",
                         "pid_1": "4242"},
        "expect": {"agents": ["apm_query"], "views": ["apm.sql_stats"], "target": "inst:1",
                   "window": "default", "args": {}, "apm_sources": [], "clarify": None,
                   "refusal": None},
        "thread_id": "a1b2c3d4-1b2c-4d5e-8f90-123456789abc",
        "sent_at": "2026-10-12T14:37:00+09:00",
        "observed": {
            "agents": ["apm_query"],
            "plan_raw": [{"task_id": "t1", "agent": "apm_query", "views": ["apm.trace"],
                          "view_args": {}, "targets": ["ord-was01"], "sources": []}],
            "plan_final": [{"task_id": "t1", "agent": "apm_query", "views": ["apm.trace"]}],
            "sanitize_changes": [],
            "reselect": None,
            "targets": [{"text": "ord-was01", "kind": "instance", "resolved": True,
                         "candidates": ["ord-was01", "ORD_api02"], "selected": ["ord-was01"]}],
            "untargeted": False,
            "time_resolution": {"start": "2026-10-12T14:07:00+09:00"},
            "calls": [{"tool": "apm_transaction_trace", "view": "apm.trace",
                       "hostname": "ord-was01",
                       "args": {"instance_name": "ord-was01", "guid": GUID, "txid": TXID,
                                "user_id": "op01"},
                       "window": {"minutes": 30}, "rows": 0, "error": None}],
            "answer_lines": ["ord-was01 p95 120ms"],
            "disclosures": [{"kind": "apm_unresolved_condition",
                             "text": "ORD_api02 는 찾지 못했다", "source": "common"}],
            "clarify_kind": None,
            "clarify_text": None,
            "refused": False,
            "response": f"ord-was01 의 GUID {GUID} 거래 · 10.1.2.3 · 계정 op01",
            "response_len": 40,
            "apm_called": True,
            "error": None,
        },
        "verdict": {"axes": {"source": "pass", "views": "fail", "target": "pass",
                             "window": "pass", "args": "pass"},
                    "turn_pass": False, "categories": ["view_wrong"], "fewshot_fixable": True,
                    "locus": ["F1:apm.trace:example#2"],
                    "fewshot_copy": {"view": "apm.trace", "example_index": 2,
                                     "args": {"n": 5},
                                     "text": "예문 원문 — 반출 금지 문장"}},
        "probe": [],
        "evidence": {},
    }
    rec.update(over)
    return rec


def _run(records: list[dict[str, Any]], *, policy: hy.TermPolicy | None = None,
         inventory: list[str] | None = None, run_meta: dict[str, Any] | None = None,
         ) -> tuple[hy.RunSanitizer, hy.SanitizedRun]:
    san = hy.RunSanitizer(term_policy=policy, canaries=[CANARY])
    out = san.sanitize(records, inventory_names=inventory or INVENTORY, run_meta=run_meta)
    return san, out


def _gate(san: hy.RunSanitizer, records: list[dict[str, Any]],
          inventory: list[str] | None = None) -> hy.ApmLeakGate:
    return hy.make_gate(inventory_names=inventory or INVENTORY,
                        placeholders=records[0]["placeholders"],
                        user_values={"login_user": "benchop"}, canaries=[CANARY], sanitizer=san)


def _staged(out: hy.SanitizedRun) -> dict[str, str]:
    for rec in out.records:
        rec["evidence"] = hy.build_evidence(rec, versions=VERSIONS)
    return hy.stage_outputs(out.records, out.run_meta, out.probe_rows, "# coverage\n",
                            "# report\n" + "\n".join(hy.term_risk_lines(hy.TermPolicy())) + "\n")


# --- 토큰화 · 허용 목록 ------------------------------------------------------------------


def test_split_name_rules() -> None:
    def tokens(name: str) -> list[str]:
        return [p for p, is_token in hy.split_name(name) if is_token]

    assert tokens("ord-was01_a") == ["ord", "was", "01", "a"]
    assert tokens("ordWas") == ["ord", "Was"]
    assert tokens("HTTPServer.x") == ["HTTP", "Server", "x"]
    assert tokens("주문WAS 결제") == ["주문", "WAS", "결제"]
    # 구분자는 원문 그대로 재조립된다
    assert "".join(p for p, _ in hy.split_name("a.b/c:d@e f")) == "a.b/c:d@e f"


def test_repo_term_policy_is_default_deny() -> None:
    policy = hy.load_term_policy()
    assert policy.terms == frozenset()
    assert policy.changes("ord-was01")


@pytest.mark.parametrize("doc", [{}, {"terms": "WAS"}, {"terms": [""]}, {"terms": ["ORD-WAS"]},
                                 {"terms": ["01"]}, {"terms": [3]}])
def test_term_policy_rejects_bad_entries(doc: Any) -> None:
    with pytest.raises(ValueError):
        hy.parse_term_policy(doc)


def test_placeholder_kind() -> None:
    assert hy.placeholder_kind("inst_1") == "inst"
    assert hy.placeholder_kind("inst_w") == "inst"
    assert hy.placeholder_kind("domain_id_1") == "domain_id"
    assert hy.placeholder_kind("guid_1") == "guid"


# --- 치환 --------------------------------------------------------------------------------


def _token_map(original: str, fake: str) -> dict[str, str]:
    pieces = hy.split_name(original)
    fakes = hy.split_name(fake)
    assert [p for p, t in pieces if not t] == [p for p, t in fakes if not t]  # 구분자 유지
    return {p.lower(): f.lower() for (p, t), (f, _) in zip(pieces, fakes, strict=True) if t}


def test_shared_token_is_consistent_everywhere() -> None:
    rec = _record()
    rec["observed"]["targets"][0]["candidates"] = ["ord-was01", "ORD_api02", "pay-was01"]
    _san, out = _run([rec])
    obs = out.records[0]["observed"]
    cands = obs["targets"][0]["candidates"]
    a, b, c = (_token_map(o, f) for o, f in zip(["ord-was01", "ORD_api02", "pay-was01"], cands,
                                                  strict=True))
    assert a["ord"] == b["ord"] != "ord"  # 두 이름의 공유 토큰 ORD
    assert a["was"] == c["was"] and a["01"] == c["01"]  # was·01 공유
    assert cands[1][:3].isupper() and cands[0][:3].islower()  # 원 대소문자 재입힘
    fake_name = cands[0]
    # 같은 이름은 어디서든 같은 가짜 이름(호출 인자·provenance·자유 문장·자리표)
    assert obs["calls"][0]["hostname"] == fake_name
    assert obs["calls"][0]["args"]["instance_name"] == fake_name
    assert out.records[0]["placeholders"]["inst_1"] == fake_name
    assert out.records[0]["prompt"].startswith(fake_name)
    assert obs["answer_lines"][0].startswith(fake_name)
    assert cands[1] in obs["disclosures"][0]["text"]
    # 자유 문장의 홀로 선 토큰도 같은 가짜 토큰
    assert out.records[0]["observed"]["plan_raw"][0]["targets"] == [fake_name]


def test_free_text_bare_token_uses_same_fake() -> None:
    rec = _record()
    rec["observed"]["response"] = "ORD 업무 지연"
    _san, out = _run([rec])
    fake = _token_map("ord-was01", out.records[0]["observed"]["targets"][0]["text"])["ord"]
    assert out.records[0]["observed"]["response"] == f"{fake.upper()} 업무 지연"


def test_allowed_term_kept_and_fully_allowed_name_not_gated() -> None:
    policy = hy.parse_term_policy({"terms": ["was", "주문"]})
    rec = _record()
    rec["observed"]["targets"][0]["candidates"] = ["ord-was01", "주문WAS"]
    san, out = _run([rec], policy=policy)
    cands = out.records[0]["observed"]["targets"][0]["candidates"]
    assert cands[0].split("-")[1].startswith("was") and not cands[0].startswith("ord")
    assert cands[1] == "주문WAS"  # 전부 허용 토큰 — 원문 유지
    assert "주문WAS" not in san.names
    gate = _gate(san, [rec])
    assert gate.check(_staged(out)) == []


def test_ids_accounts_ip_and_secrets() -> None:
    rec = _record()
    rec["observed"]["response"] += (
        " Bearer abcdefghijkl12345 · eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.sig")
    _san, out = _run([rec])
    obs = out.records[0]["observed"]
    fake_guid = obs["calls"][0]["args"]["guid"]
    assert fake_guid != GUID and len(fake_guid) == len(GUID)
    assert [len(x) for x in fake_guid.split("-")] == [8, 4, 4, 4, 12]
    assert all(ch in "0123456789abcdef-" for ch in fake_guid)
    assert fake_guid in obs["response"] and GUID not in obs["response"]
    assert out.records[0]["placeholders"]["guid_1"] == fake_guid
    fake_tx = obs["calls"][0]["args"]["txid"]
    assert fake_tx != TXID and fake_tx[0] == "-" and fake_tx[1:].isdigit()
    assert len(fake_tx) == len(TXID)
    assert obs["calls"][0]["args"]["user_id"] == "o***"  # D-300 ④
    assert out.records[0]["placeholders"]["user_1"] == "o***"
    assert out.records[0]["placeholders"]["pid_1"] == "4242"
    assert "10.1.2.3" not in obs["response"]
    assert "abcdefghijkl12345" not in obs["response"] and "Bearer <가림>" in obs["response"]
    assert "eyJhbGciOiJIUzI1" not in obs["response"]
    assert out.records[0]["thread_id"] != rec["thread_id"]


def test_sql_body_masked() -> None:
    rec = _record()
    rec["observed"]["answer_lines"] = ["SELECT name FROM t_cust WHERE id = 3"]
    _san, out = _run([rec])
    assert out.records[0]["observed"]["answer_lines"] == ["SELECT " + hy.SQL_MASK]


def test_input_untouched_and_single_use() -> None:
    rec = _record()
    before = copy.deepcopy(rec)
    san, _out = _run([rec])
    assert rec == before
    with pytest.raises(RuntimeError):
        san.sanitize([rec], inventory_names=INVENTORY)


def test_sensitive_scenario_keeps_shape_only() -> None:
    rec = _record(sensitive=True)
    rec["observed"]["response"] = "JAVA_HOME=/opt/secret/jdk\nPATH=/usr/bin"
    rec["observed"]["response_len"] = 38
    rec["observed"]["answer_lines"] = ["JAVA_HOME = /opt/secret/jdk", "com.example.OrderSvc",
                                       "PATH: /usr/bin"]
    _san, out = _run([rec])
    obs = out.records[0]["observed"]
    assert obs["response"] == hy.SENSITIVE_RESPONSE
    assert obs["response_len"] == 38
    assert obs["answer_lines"] == ["JAVA_HOME=<가림>", "<가림>", "PATH=<가림>"]
    dumped = json.dumps(out.records[0], ensure_ascii=False)
    assert "/opt/secret/jdk" not in dumped and "OrderSvc" not in dumped


def test_run_meta_user_fields_are_whole_fakes() -> None:
    rec = _record()
    san, out = _run([rec], run_meta={"run_id": "r1", "host": "bench-host01", "sha": "ee13b47"})
    assert out.run_meta["host"] != "bench-host01"
    assert san.fakes.is_fake(out.run_meta["host"])
    assert out.run_meta["sha"] == "ee13b47"


# --- 2단계 등록 ---------------------------------------------------------------------------


def test_two_phase_later_original_never_taken_as_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """앞 턴 토큰의 첫 후보를 뒤 턴 원값으로 강제해도 그 후보는 거절되고 뒤 턴 원값은 치환된다."""
    real = substitute._draw
    forced = {"n": 0}

    def draw(segment: str) -> str:
        if segment == "abc" and forced["n"] == 0:
            forced["n"] += 1
            return "xyz"
        return real(segment)

    monkeypatch.setattr(substitute, "_draw", draw)
    r0 = _record(placeholders={"inst_1": "abc"}, prompt="abc 응답시간")
    r0["observed"]["targets"] = [{"text": "abc", "resolved": True, "candidates": ["abc"],
                                  "selected": ["abc"]}]
    r1 = _record(turn=1, placeholders={"inst_1": "xyz"}, prompt="xyz 응답시간")
    r1["observed"]["targets"] = [{"text": "xyz", "resolved": True, "candidates": ["xyz"],
                                  "selected": ["xyz"]}]
    san, out = _run([r0, r1], inventory=["abc", "xyz"])
    assert forced["n"] == 1
    fake_abc = out.records[0]["observed"]["targets"][0]["text"]
    fake_xyz = out.records[1]["observed"]["targets"][0]["text"]
    assert fake_abc not in ("abc", "xyz")
    assert fake_xyz not in ("abc", "xyz")  # 뒤 턴 원값이 그대로 통과하지 않는다
    assert san.fakes.collisions() == 0
    assert _gate(san, [r0, r1], ["abc", "xyz"]).check(_staged(out)) == []


def test_two_phase_monte_carlo_short_values() -> None:
    """빈 정책 · 짧은 값(2자 토큰 다수) — 충돌로 run 이 막히거나 원값이 남지 않는다."""
    rng = random.Random(152)
    letters = string.ascii_lowercase
    for _ in range(3):
        names = sorted({f"{rng.choice(letters)}{rng.choice(letters)}-"
                        f"{rng.choice(letters)}{rng.choice(letters)}" for _ in range(60)})
        records = []
        for turn, name in enumerate(names):
            rec = _record(turn=turn, placeholders={"inst_1": name}, prompt=f"{name} 응답시간")
            rec["observed"]["targets"] = [{"text": name, "resolved": True,
                                           "candidates": [name], "selected": [name]}]
            records.append(rec)
        san, out = _run(records, inventory=names)
        originals = {t for n in names for t in n.split("-")}
        for rec in out.records:
            fake = rec["observed"]["targets"][0]["text"]
            assert not ({t for t in fake.split("-")} & originals)
        assert san.fakes.collisions() == 0
        violations = _gate(san, records, names).check(_staged(out))
        assert [v for v in violations if v["rule"] in ("substitution", "pii_value")] == []


# --- 관문 ---------------------------------------------------------------------------------


def _inject(staged: dict[str, str], text: str) -> dict[str, str]:
    lines = staged["trace.jsonl"].splitlines()
    rec = json.loads(lines[0])
    rec["observed"]["response"] += " " + text
    lines[0] = json.dumps(rec, ensure_ascii=False)
    return {**staged, "trace.jsonl": "\n".join(lines) + "\n"}


def test_clean_run_passes_and_records_leak_check(tmp_path: Path) -> None:
    rec = _record()
    san, out = _run([rec])
    staged = _staged(out)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert ok and violations == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [*hy.OUTPUT_FILES, "leak_check.json"])
    assert json.loads((tmp_path / "leak_check.json").read_text())["passed"] is True


@pytest.mark.parametrize("injected, rule", [
    (CANARY, "canary"),
    ("only-inventory77", "pii_value"),  # 레코드에 없고 인벤토리에만 있는 원이름
    ("ONLY-INVENTORY77", "pii_value"),  # 대소문자 무시
    ("ord-was01", "pii_value"),
    ("guid " + GUID, "pii_value"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig", "user_info"),
    ("Bearer abcdefghijklmnop123", "user_info"),
    ("op01", "user_info"),
    ("benchop", "user_info"),
    ("192.168.10.20", "pii_regex"),
    ("0f9e8d7c-6b5a-4f3e-9d2c-1b0a9f8e7d6c", "pii_regex"),  # 이 run 가짜 값이 아닌 GUID 모양
])
def test_gate_blocks_injection(tmp_path: Path, injected: str, rule: str) -> None:
    rec = _record()
    san, out = _run([rec])
    staged = _inject(_staged(out), injected)
    ok, violations = hy.write_outputs(tmp_path, staged, _gate(san, [rec]))
    assert not ok
    assert rule in {v["rule"] for v in violations}
    assert [p.name for p in tmp_path.iterdir()] == ["leak_check.json"]  # 산출물 미기록
    report = json.loads((tmp_path / "leak_check.json").read_text())
    assert report["passed"] is False
    assert injected not in json.dumps(report, ensure_ascii=False)  # 위치만 — 값 없음


def test_tokens_replaced_in_structured_values_and_sentence_fields() -> None:
    """토큰 사전은 구조 칸 문자열 값에도(문장 칸은 `redact_text`까지) — 코드 어휘·시나리오
    칸은 그대로."""
    rec = _record()
    rec["observed"]["apm_source_clarification"] = {"original_query": "ord 업무 지연",
                                                   "question": "pay 업무를 어디서 볼까요?"}
    rec["observed"]["calls"][0]["args"]["scope"] = "ord"
    rec["extra"] = {"degraded_reason": "ord 계열 부분 응답"}
    rec["expect"]["args"] = {"sort_by": "ord"}
    san, out = _run([rec])
    got = out.records[0]
    dumped = json.dumps({k: v for k, v in got.items() if k != "expect"}, ensure_ascii=False)
    assert '"ord' not in dumped.lower() and " ord" not in dumped.lower()
    assert "pay 업무" not in dumped
    assert got["expect"]["args"] == {"sort_by": "ord"}  # 시나리오 어휘 — 토큰 사전 제외
    assert got["observed"]["calls"][0]["tool"] == "apm_transaction_trace"  # 코드 어휘
    assert {"ord", "pay", "api"} <= san.tokens
    assert _gate(san, [rec]).check(_staged(out)) == []


def test_gate_blocks_token_left_in_structured_value() -> None:
    """치환을 건너뛴 구조 칸 값에 토큰이 남으면 관문 `pii_value`(위치만) — 키·코드 어휘 칸은
    제외."""
    rec = _record()
    san, out = _run([rec])
    staged = _staged(out)
    line = json.loads(staged["trace.jsonl"].splitlines()[0])
    line["observed"]["apm_source_clarification"] = {"original_query": "ord 업무 지연"}
    line["observed"]["tool_note"] = {"ord": 1}  # 키는 토큰 대조 대상이 아니다
    line["covers"] = ["COV-ord"]  # 코드 어휘 칸
    bad = {**staged, "trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"}
    violations = _gate(san, [rec]).check(bad)
    fields = [v["field"] for v in violations if v["rule"] == "pii_value"]
    assert fields == ["observed.apm_source_clarification.original_query"]
    assert "ord 업무" not in json.dumps(violations, ensure_ascii=False)


def test_two_char_and_joined_tokens_in_dictionary() -> None:
    policy = hy.TermPolicy(frozenset({"was"}))
    tokens = hy.name_tokens(["kb-order-was01", "OrdPay01", "x"], policy)
    assert {"kb", "order", "ord", "pay", "ordpay"} <= tokens
    assert "was" not in tokens and "01" not in tokens and "x" not in tokens


def test_gate_without_sanitizer_is_closed() -> None:
    gate = hy.make_gate(inventory_names=["ord-was01"], placeholders={}, user_values={},
                        canaries=[])
    rules = {v["rule"] for v in gate.check({"report.md": "가짜 GUID " + GUID + " 1.2.3.4\n"})}
    assert "pii_regex" in rules


def test_write_outputs_refuses_staged_leak_check(tmp_path: Path) -> None:
    rec = _record()
    san, _out = _run([rec])
    with pytest.raises(ValueError):
        hy.write_outputs(tmp_path, {"leak_check.json": "{}"}, _gate(san, [rec]))


def test_term_risk_note_has_no_inventory_word() -> None:
    lines = hy.term_risk_lines(hy.parse_term_policy({"terms": ["WAS"]}))
    assert "1개" in lines[1]
    assert not any(w in " ".join(lines) for w in ("주문", "ORD", "WAS"))


# --- 근거 묶음 ----------------------------------------------------------------------------


def test_failed_turn_evidence_has_no_missing_field() -> None:
    rec = _record()
    _san, out = _run([rec])
    _staged(out)
    assert hy.check_evidence(out.records) == []
    ev = out.records[0]["evidence"]
    assert ev["level"] == "full"
    # 관측상 없음은 표지, 값은 그대로
    assert ev["sanitize"]["changes"] == hy.NONE
    assert ev["reselect"] == hy.NONE
    assert ev["execution"]["empty"] == [0]
    assert ev["response"]["clarify_text"] == hy.NONE
    assert ev["versions"]["F1"] == "9c2eb9cd7418"
    assert ev["decompose"]["view_rows"] == ["apm.sql_stats", "apm.trace"]
    # 치환본으로 만든다 — 원이름 없음
    assert "ord-was01" not in json.dumps(ev, ensure_ascii=False)


def test_evidence_excludes_fewshot_text() -> None:
    rec = _record()
    _san, out = _run([rec])
    ev = hy.build_evidence(out.records[0], versions=VERSIONS)
    dumped = json.dumps(ev, ensure_ascii=False)
    assert "반출 금지 문장" not in dumped
    assert ev["classification"]["fewshot_copy"] == {"view": "apm.trace", "example_index": 2,
                                                    "args": {"n": 5}}


@pytest.mark.parametrize("mutate, field", [
    (lambda r, v: r["observed"].pop("calls"), "execution.tools"),
    (lambda r, v: r["observed"].pop("plan_raw"), "decompose.llm_raw"),
    (lambda r, v: r["observed"].pop("targets"), "target.texts"),
    (lambda r, v: r.update(sent_at=""), "window.sent_at"),
    (lambda r, v: r["verdict"].pop("locus"), "classification.locus"),
    (lambda r, v: v["fingerprints"].pop("F4"), "versions.F4"),
    (lambda r, v: v.pop("apm_section"), "decompose.apm_section.fingerprint"),
])
def test_missing_field_injection_detected(mutate: Any, field: str) -> None:
    rec = _record()
    _san, out = _run([rec])
    versions = copy.deepcopy(VERSIONS)
    mutate(out.records[0], versions)
    out.records[0]["evidence"] = hy.build_evidence(out.records[0], versions=versions)
    missing = hy.check_evidence(out.records)
    assert ("APM-T1", 0, field) in missing


def test_passed_and_skipped_turns_are_summaries() -> None:
    passed = _record()
    passed["verdict"] = {"turn_pass": True, "axes": {"views": "pass"}}
    skipped = _record(turn=1, verdict={"skipped": "e2_skip"})
    _san, out = _run([passed, skipped])
    _staged(out)
    assert out.records[0]["evidence"]["level"] == "summary"
    assert out.records[0]["evidence"]["tools"] == ["apm_transaction_trace"]
    assert out.records[1]["evidence"] == {"level": "skipped", "reason": "e2_skip"}
    assert hy.check_evidence(out.records) == []


def test_unjudged_turn_needs_question_and_versions_only() -> None:
    rec = _record(observed={"error": "timeout"},
                  verdict={"turn_pass": False, "unjudged": "turn_error:timeout"})
    _san, out = _run([rec])
    _staged(out)
    assert hy.check_evidence(out.records) == []
    out.records[0]["evidence"]["versions"]["code_sha"] = None
    assert hy.check_evidence(out.records) == [("APM-T1", 0, "versions.code_sha")]


def test_extra_diagnostics_are_sanitized() -> None:
    """`extra`(plan_summary · source_auto_answer 되묻기 문구)도 치환·관문 대상이다."""
    rec = _record(extra={
        "plan_summary": {"tasks": [{"agent": "apm_query", "summary": "ord-was01 SQL 통계"}]},
        "source_auto_answer": {"source": "common", "sent": "공동존",
                               "first": {"apm_called": False, "llm_kinds": ["plan"],
                                         "clarification": "pay-was01 은 어느 제니퍼인가요?"}},
        "llm_kinds": ["plan", "select"],
    })
    san, out = _run([rec])
    extra = out.records[0]["extra"]
    dumped = json.dumps(extra, ensure_ascii=False)
    assert "ord-was01" not in dumped and "pay-was01" not in dumped
    assert extra["llm_kinds"] == ["plan", "select"]
    assert _gate(san, [rec]).check(_staged(out)) == []


def test_unused_placeholder_values_are_still_replaced() -> None:
    """이번 run 레코드가 쓰지 않은 자리표 값이 응답에만 나와도 치환된다(관문은 파일 전체를 본다)."""
    rec = _record()
    rec["observed"] = {**rec["observed"], "response": "앞 행 application /shop/cart?id=1"}
    every = {**rec["placeholders"], "url_1": "/shop/cart"}
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([rec], inventory_names=INVENTORY, placeholders=every)
    assert "/shop/cart" not in out.records[0]["observed"]["response"]
    gate = hy.make_gate(inventory_names=INVENTORY, placeholders=every, user_values={},
                        canaries=[CANARY], sanitizer=san)
    assert gate.rules(out.records[0]["observed"]["response"], schema_section=False) == []
