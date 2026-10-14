"""plans/164 W1·W3 독립 검증(verifier) — 구현자 테스트와 별도.

- W3 `error_stages` 건수 맞추기 경계(같은 도구 다중 실패 · 순서 섞임 · 감사 줄 없음 · 배치 모양 ·
  코드 없는 처리기 실패) — 해석 단계 분류가 `gw_error`를 **부당하게 지우는** 방향이 있는지
- 턴 통과·5축이 오류 단계 구분과 무관한지(실 시나리오 전 턴 합성 레코드 × 오류 주입 변형)
- W1 `VIEW_KEYS`·`_code_only` 가 계획 칸(`plan_final[].views`)·호출 칸(`calls[].view`)의 원이름을
  반출본에서 지우는지 · `_dropped` 칸이 `--compare`(load_run·compare)를 깨지 않는지
- 사용자 정보 같은 값 여러 종류 · 관문 2차 방어가 토큰 사전 없이도 도는지

순수 로직 — 네트워크·LLM 0. 이름은 전부 합성 값이다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import driver as dv
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import judge as jd
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver
from tests.test_scripts.test_apm_bench_hygiene import CANARY, VERSIONS, _record
from tests.test_scripts.test_apm_bench_judge import _base, _judge, synth


@pytest.fixture(autouse=True)
def _quarantine_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문 실패 격리본이 실제 홈(`~/apm_bench_quarantine`)에 쌓이지 않게 한다(plans/164 W2a)."""
    from scripts.apm_bench import rundir

    monkeypatch.setattr(rundir, "default_quarantine_base", lambda: tmp_path / "quarantine")


RESOLVE_TOOL = "apm_instance_map"
IU = "instance_unresolved"


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


def _err(tool: str, code: str) -> dict[str, Any]:
    return {"tool": tool, "args": {}, "window": None, "rows": None, "error": code,
            "sources": []}


def _biz(registry: sc.RegistryInfo) -> tuple[sc.Scenario, dict[str, Any]]:
    s, r = _base(registry, prompt="{{biz_1}} 최근 30분 응답시간", target="biz:1")
    r["observed"]["view_failures"] = []
    return s, r


def _stages(r: dict[str, Any], registry: sc.RegistryInfo) -> list[str]:
    return [x["stage"] for x in jd.error_stages(r["observed"], registry)]


# --- W3 · error_stages 경계 ------------------------------------------------------------------


def test_same_view_tool_two_failures_both_view(registry) -> None:
    """같은 보기 도구가 두 번 실패(대상 둘) — 둘 다 보기 단계 · gw_error 유지."""
    s, r = _biz(registry)
    tool = registry.views["apm.app_health"].tool
    r["observed"]["calls"] = [_err(tool, IU), _err(tool, IU)]
    r["observed"]["view_failures"] = [{"view": "apm.app_health", "code": IU}] * 2
    assert _stages(r, registry) == [jd.STAGE_VIEW, jd.STAGE_VIEW]
    v = _judge(s, r, registry)
    assert "gw_error" in v["categories"] and v["axis_detail"]["results"]["resolve_miss"] == 0


def test_mixed_order_instances_tool_counts_are_symmetric(registry) -> None:
    """보기 호출이 해석 호출보다 먼저 감사돼도(순서 섞임) 건수는 보기 1 · 해석 1 — gw_error 유지."""
    s, r = _biz(registry)
    r["observed"]["calls"] = [_err(RESOLVE_TOOL, IU), _err(RESOLVE_TOOL, IU),
                              _err(RESOLVE_TOOL, IU)]
    r["observed"]["view_failures"] = [{"view": jd.INSTANCES_VIEW, "code": IU}]
    stages = _stages(r, registry)
    assert stages.count(jd.STAGE_VIEW) == 1 and stages.count(jd.STAGE_RESOLVE) == 2
    v = _judge(s, r, registry)
    assert "gw_error" in v["categories"]  # 오류 3 > 해석 2


def test_view_failures_without_audit_lines_is_conservative(registry) -> None:
    """감사 줄 없이 처리기 실패만(작업 폴링·배치 부분 실패) — 오류 없음이라 gw_error 도 resolve 도
    0."""
    s, r = _biz(registry)
    r["observed"]["view_failures"] = [{"view": "apm.app_health", "code": "job_not_ready"}]
    assert jd.error_stages(r["observed"], registry) == []
    details, cats = jd.judge_results(r, registry)
    assert "gw_error" not in cats and details["resolve_miss"] == 0


def test_batch_shape_more_failures_than_audit_is_unknown(registry) -> None:
    """배치 1 호출(감사 1줄)이 대상 2개 실패(처리기 실패 2) — 건수 불성립 → 전부 unknown."""
    s, r = _biz(registry)
    r["observed"]["calls"] = [_err(RESOLVE_TOOL, IU)]
    r["observed"]["view_failures"] = [{"view": jd.INSTANCES_VIEW, "code": IU}] * 2
    assert _stages(r, registry) == [jd.STAGE_UNKNOWN]
    _details, cats = jd.judge_results(r, registry)
    assert cats[0] == "gw_error"


def test_other_view_failure_leftover_voids_resolve(registry) -> None:
    """맞지 않는 보기 실패가 남으면(코드 다름) 해석 줄도 resolve 로 가르지 않는다(전부 unknown)."""
    s, r = _biz(registry)
    tool = registry.views["apm.app_health"].tool
    r["observed"]["calls"] = [_err(RESOLVE_TOOL, IU), _err(tool, "apm_api_error")]
    r["observed"]["view_failures"] = [{"view": "apm.app_health", "code": "source_unavailable"}]
    assert _stages(r, registry) == [jd.STAGE_UNKNOWN, jd.STAGE_UNKNOWN]


def test_uncoded_instances_view_failure_should_stay_ambiguous(registry) -> None:
    s, r = _biz(registry)
    r["observed"]["calls"] = [_err(RESOLVE_TOOL, IU)]
    r["observed"]["view_failures"] = [{"view": jd.INSTANCES_VIEW, "code": None}]
    _details, cats = jd.judge_results(r, registry)
    assert "gw_error" in cats


@pytest.mark.parametrize("reason, code", [
    ("instance_unresolved: 대상 'ord-was01' 없음", IU),
    ("apm_api_error: 403", "apm_api_error"),
    ("batch_missing: 배치 결과에 이 대상의 항목이 없습니다", "batch_missing"),
    ("TimeoutError: ", None),  # `_lookup` 예외 문형(대문자 클래스 이름)
    ("apm_gateway 도구 오류(apm_instance_map): x", None),  # SourceMcpError 문형
    ("대상 'ord-was01' 해석 0건", None),
    ("ord-was01: instance_unresolved", None),  # 원이름 접두 — 코드 모양이 아니면 버린다
    (None, None),
])
def test_error_code_keeps_only_code_prefix(reason: Any, code: Any) -> None:
    assert dv._error_code(reason) == code


def test_error_code_never_carries_names_into_observed() -> None:
    """`view_failures`에는 보기·코드만 — 사유 원문(서버 이름 섞임)은 없다."""
    done = {"thread_id": "t", "response": "x"}
    captures = [{"kind": "investigation_result", "meta": {
        "failures": [{"view": "apm.app_health", "hostname": "ord-was01",
                      "reason": "instance_unresolved: ord-was01 없음"}],
        "targets": {"texts": [{"text": "주문", "kind": "business", "searched": ["a", "b"],
                               "instances": 0, "suggestions": [],
                               "failures": ["ord-was01 인스턴스 정합 실패"]}]}}}]
    obs = dv.build_observed(obs=type("O", (), {"error": None})(), done=done,
                            captures=captures, audit=[])
    assert obs["view_failures"] == [{"view": "apm.app_health", "code": IU}]
    assert obs["targets"][0]["searched"] == 2 and obs["targets"][0]["failures"] == 1
    assert "ord-was01" not in json.dumps(obs["view_failures"]) + json.dumps(
        {k: v for k, v in obs["targets"][0].items() if k in ("searched", "failures")})


# --- W3 · 턴 통과·5축 불변(오류 구분과 무관) ------------------------------------------------


def _variants(registry: sc.RegistryInfo, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """같은 레코드에 오류·처리기 실패를 여러 모양으로 주입한 변형들."""
    out = []
    for calls, failures in (
        ([_err(RESOLVE_TOOL, IU)], []),
        ([_err(RESOLVE_TOOL, IU)], None),
        ([_err(RESOLVE_TOOL, IU), _err(RESOLVE_TOOL, IU)], [{"view": jd.INSTANCES_VIEW,
                                                           "code": IU}]),
        ([_err("apm_app_health", "apm_api_error")], [{"view": "apm.app_health",
                                                      "code": "apm_api_error"}]),
    ):
        r = copy.deepcopy(rec)
        r["observed"]["calls"] = [*calls, *(r["observed"].get("calls") or [])]
        if failures is None:
            r["observed"].pop("view_failures", None)
        else:
            r["observed"]["view_failures"] = failures
        out.append(r)
    return out


def test_turn_pass_and_axes_independent_of_error_stages(registry) -> None:
    """실 시나리오 전 턴의 합성 레코드 × 오류 주입 변형 — 단계 구분(새 판정기)과 `registry=None`
    (판별 포기 = 시작 SHA 와 같은 gw_error 규칙)의 턴 통과·5축이 같고, 분류는 gw_error 만 다르다."""
    scenarios = sc.load_scenarios()
    checked = 0
    for s in scenarios:
        for i in range(len(s.turns)):
            base = synth(s, i, registry)
            for r in _variants(registry, base):
                new = jd.judge_turn(s, i, copy.deepcopy(r), registry)
                old_rec = copy.deepcopy(r)
                old_rec["observed"].pop("view_failures", None)  # 시작 SHA 규칙(전부 gw_error)
                old = jd.judge_turn(s, i, old_rec, registry)
                assert new["turn_pass"] == old["turn_pass"], (s.id, i)
                assert new["axes"] == old["axes"], (s.id, i)
                assert [c for c in new["categories"] if c != "gw_error"] == \
                    [c for c in old["categories"] if c != "gw_error"], (s.id, i)
                checked += 1
    assert checked > 400


# --- W1 · 보기 칸·코드 칸 원이름 -------------------------------------------------------------


def _sanitize(records: list[dict[str, Any]], users: dict[str, str] | None = None,
              inventory: list[str] | None = None) -> tuple[hy.RunSanitizer, hy.SanitizedRun]:
    san = hy.RunSanitizer(canaries=[CANARY], user_values=users or {})
    return san, san.sanitize(records, inventory_names=inventory or ["ord-was01", "pay-was01"])


def _staged(out: hy.SanitizedRun) -> dict[str, str]:
    for rec in out.records:
        rec["evidence"] = hy.build_evidence(rec, versions=VERSIONS)
    return hy.stage_outputs(out.records, out.run_meta, out.probe_rows, "# c\n", "# r\n")


def test_raw_names_in_plan_views_and_call_view_dropped(tmp_path: Path) -> None:
    """LLM 이 보기 자리에 원이름을 낸 계획(`plan_final[].views`)·호출 짝(`calls[].view`) —
    치환 없이 남던 SKIP 칸이 반출본에서 사라지고 건수만 남는다."""
    rec = _record()
    rec["observed"]["plan_final"] = [{"task_id": "t1", "agent": "apm_query",
                                      "views": ["apm.app_health", "ord-was01"]}]
    rec["observed"]["calls"][0]["view"] = "ord-was01"
    san, out = _sanitize([rec])
    staged = _staged(out)
    assert "ord-was01" not in staged["trace.jsonl"]
    task = out.records[0]["observed"]["plan_final"][0]
    assert task["views"] == ["apm.app_health"] and task["views_dropped"] == 1
    call = out.records[0]["observed"]["calls"][0]
    assert call["view"] is None and call["view_dropped"] == 1
    gate = hy.make_gate(inventory_names=["ord-was01", "pay-was01"],
                        placeholders=rec["placeholders"], user_values={}, canaries=[CANARY],
                        sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path, staged, gate)
    assert ok and violations == []


def test_target_candidates_still_substituted_not_exempt() -> None:
    """`target.candidates`(인스턴스 원이름)는 SKIP 밖 — 원이름 치환을 받는다(면제 금지)."""
    assert "candidates" not in hy.SKIP_KEYS and "candidates" not in hy.VIEW_KEYS
    rec = _record()
    rec["observed"]["targets"] = [{"text": "주문", "kind": "business", "resolved": False,
                                   "candidates": ["ord-was01", "pay-was01"], "selected": []}]
    _san, out = _sanitize([rec])
    cands = out.records[0]["observed"]["targets"][0]["candidates"]
    assert len(cands) == 2 and "ord-was01" not in cands and "pay-was01" not in cands


def test_code_leaves_checked_even_without_token_dictionary() -> None:
    """관문 2차 방어(`_code_leaves`)는 토큰 사전이 비어도 돈다(종전 `_tokens is None` 조기 반환
    제거)."""
    gate = hy.ApmLeakGate(
        policy=hy.ColumnPolicy(db_id="apm", scope="t", tables={}, canary_literals=()),
        vault=hy.PiiVault(()), user_values={}, fakes=None, originals=[], hex_ids=None,
        tokens=[])
    line = {"verdict": {"locus": ["F1:apm.trace:args.주문 인자"]}, "observed": {"response": "ok"}}
    violations = gate.check({"trace.jsonl": json.dumps(line, ensure_ascii=False) + "\n"})
    assert [v["rule"] for v in violations] == ["pii_value"]
    assert "주문" not in json.dumps(violations, ensure_ascii=False)


# --- W1 · 사용자 정보 ------------------------------------------------------------------------


def test_same_value_for_two_kinds_counted_once_and_masked(tmp_path: Path) -> None:
    users = {"os_user": "zbench", "login_id": "zbench", "host": "zz-host-9",
             "home": "/home/zbench"}
    rec = _record()
    rec["observed"]["response"] = "소유자 zbench · 경로 /home/zbench/x · zz-host-9"
    san, out = _sanitize([rec], users)
    resp = out.records[0]["observed"]["response"]
    assert "zbench" not in resp and "zz-host-9" not in resp
    assert san.user_info_counts == {"home": 1, "os_user": 1, "host": 1}  # login_id 는 앞 종류로
    staged = _staged(out)
    gate = hy.make_gate(inventory_names=["ord-was01", "pay-was01"],
                        placeholders=rec["placeholders"], user_values=users, canaries=[CANARY],
                        sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path, staged, gate)
    assert ok and violations == []


def test_user_info_outside_free_text_still_blocks(tmp_path: Path) -> None:
    """자유 문장 4칸 밖 문장 칸(`reselect.reason`)의 사용자 정보는 치환하지 않는다 — 관문이 막는다
    (규칙 미완화 · 닫힌 쪽)."""
    users = {"os_user": "zbench"}
    rec = _record()
    rec["observed"]["reselect"] = {"happened": True, "candidates": ["apm.app_health"],
                                   "parsed_views": ["apm.app_health"], "parsed_view_args": {},
                                   "result_views": ["apm.app_health"],
                                   "reason": "zbench 계정 질문"}
    san, out = _sanitize([rec], users)
    staged = _staged(out)
    gate = hy.make_gate(inventory_names=["ord-was01", "pay-was01"],
                        placeholders=rec["placeholders"], user_values=users, canaries=[CANARY],
                        sanitizer=san)
    ok, violations = hy.write_outputs(tmp_path, staged, gate)
    assert not ok and {v["rule"] for v in violations} == {"user_info"}


# --- W1 · `_dropped` 칸과 소비처 -----------------------------------------------------------


def _hostile_driver() -> Any:
    base = _fake_driver({})

    def run(*a: Any, **k: Any) -> Any:
        records, meta = base(*a, **k)
        obs = records[0]["observed"]
        obs["reselect"] = {"happened": False, "candidates": ["apm.instances", "evil host"],
                           "parsed_views": ["ord-host 9"], "parsed_view_args": {},
                           "result_views": ["apm.instances"], "reason": None}
        obs["plan_final"][0]["areas"] = ["was_instance", "주문 영역"]
        return records, meta

    return run


def test_dropped_keys_survive_compare(tmp_path: Path) -> None:
    """`<키>_dropped` 칸이 실린 두 run 을 `--compare`가 읽는다(load_run·compare·render)."""
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        args = cli.build_parser().parse_args(
            ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out)])
        assert cli.cmd_run(args, driver_run=_hostile_driver()) == 0
        text = (out / "trace.jsonl").read_text(encoding="utf-8")
        assert "evil host" not in text and "ord-host 9" not in text and "주문 영역" not in text
        line = json.loads(text.splitlines()[0])
        assert line["observed"]["reselect"]["candidate_views_dropped"] == 1
        assert line["observed"]["reselect"]["parsed_views_dropped"] == 1
        assert line["evidence"]["level"] in ("summary", "full")
        outs.append(out)
    cmp = cli.build_parser().parse_args(["--compare", str(outs[0]), str(outs[1]),
                                         "--out", str(tmp_path / "cmp")])
    assert cli.cmd_compare(cmp) == 0
    assert (tmp_path / "cmp" / "compare.md").exists()
