"""APM 조회 벤치 — 시나리오 로더·린트·레지스트리 정합·분할·커버리지 (plans/152 W1 · D-323).

LLM·네트워크에 붙지 않는다. 레지스트리는 본체 로더(`load_registry`)로 실 파일을 읽고, 대응표는
`spec/CAPABILITY-MAP-134.md` 실 파일을 읽는다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.apm_bench import SCENARIOS_PATH
from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import scenarios as sc


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


@pytest.fixture(scope="module")
def cap() -> sc.CapabilityMap:
    return sc.parse_capability_map()


@pytest.fixture(scope="module")
def real() -> list[sc.Scenario]:
    return sc.load_scenarios(SCENARIOS_PATH)


def _raw(**overrides: Any) -> dict[str, Any]:
    """정상 단일 턴 시나리오 원자료(덮어쓰기 가능)."""
    turn = {
        "prompt": "{{inst_1}} 오늘 실행 시간이 가장 긴 SQL 10개",
        "expect": {
            "agents": ["apm_query"],
            "views": ["apm.sql_stats"],
            "target": "inst:1",
            "window": "hourly:today",
            "args": {"sort_by": "responseTime", "n": 10},
        },
    }
    raw: dict[str, Any] = {
        "id": "T-1",
        "split": "dev",
        "covers": ["COV-STAT-SQL", "COV-STAT-SQL.sort_by_metrics"],
        "turns": [turn],
    }
    raw.update(overrides)
    return raw


def _with_expect(**changes: Any) -> dict[str, Any]:
    raw = _raw()
    expect = raw["turns"][0]["expect"]
    for key, value in changes.items():
        if value is None:
            expect.pop(key, None)
        else:
            expect[key] = value
    return raw


def _parse(*raws: dict[str, Any]) -> tuple[list[sc.Scenario], list[sc.Issue]]:
    return sc.parse_scenarios([copy.deepcopy(r) for r in raws])


def _rules(issues: list[sc.Issue], level: str = "error") -> set[str]:
    return {i.rule for i in issues if i.level == level}


# --- 실제 시나리오 파일 -------------------------------------------------------------------


def test_real_file_counts(real: list[sc.Scenario]) -> None:
    turns = [t for s in real for t in s.turns]
    assert len(real) == 121
    assert len(turns) == 135
    assert sum(s.split == "dev" for s in real) == 85
    assert sum(s.split == "holdout" for s in real) == 36
    heavy = [s for s in real if s.heavy]
    assert len(heavy) == 5 and all(s.split == "dev" for s in heavy)
    was = [s for s in real if s.was_touch]
    assert len(was) == 6
    assert sum(1 for s in was if s.e2_skip == "was_touch_limit") == 3
    assert sum(1 for t in turns if t.expect.refusal) == 3
    assert sum(1 for t in turns if t.expect.clarify == "required_arg") == 1
    assert sum(1 for t in turns if set(t.expect.agents) - {"apm_query"}) == 2


def test_real_file_has_no_violations(
    real: list[sc.Scenario], cap: sc.CapabilityMap, registry: sc.RegistryInfo
) -> None:
    issues, split = cli.collect_issues(real, [], cap, registry)
    errors = [i.line() for i in issues if i.level == "error"]
    assert errors == []
    assert abs(split.ratio - 0.30) <= 0.03
    e2_only = {i.scenario_id for i in issues if i.rule == "P-e2-only"}
    assert e2_only == {"COV-ACTIVE-DETAIL", "COV-ENV", "COV-LOADED-CLASS"}


def test_real_file_full_coverage(
    real: list[sc.Scenario], cap: sc.CapabilityMap, registry: sc.RegistryInfo
) -> None:
    report = sc.build_coverage(real, cap, registry)
    sizes = {name: len(items) for name, items in report.groups()}
    assert sizes == {
        "표 A 경로": 40,
        "표 B 선택 인자": 26,
        "레지스트리 보기": 29,
        "레지스트리 보기 인자": 59,
    }
    assert report.uncovered == 0
    excluded = dict(report.excluded_args)
    assert "코드가 채움" in excluded["COV-INSTANCE.domain_id"]
    assert "미지원" in excluded["COV-TX-TIME.time_pattern"]
    assert "미전달" in excluded["COV-TX-SQL.key"]
    assert "거부" in excluded["(전 경로 공통).token"]


def test_capability_map_selected_args(cap: sc.CapabilityMap) -> None:
    keys = {a.key for a in cap.selected_args}
    # 「미기재」 칸도 선택으로 센다 · `|` 이스케이프 칸(`level_mode`)도 깨지지 않는다
    assert {"COV-PROC-INSTANCE.processId", "COV-ACTIVE-DETAIL.sessionId", "COV-EVENT.level"} <= keys
    assert "COV-RT-INSTANCE.domain_id" not in keys
    assert len(cap.paths) == 40 and cap.paths[0].path == "/api/domain"


def test_capability_map_missing_tables_raises(tmp_path: Path) -> None:
    path = tmp_path / "map.md"
    path.write_text("# 제목\n\n## 2. 표 A\n\n본문 없음\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sc.parse_capability_map(path)


# --- 구조 -------------------------------------------------------------------------------


def test_valid_minimal_scenario(registry: sc.RegistryInfo) -> None:
    scenarios, issues = _parse(_raw())
    assert issues == []
    s = scenarios[0]
    assert s.turns[0].expect.args == {"sort_by": "responseTime", "n": 10}
    assert s.cov_paths == {"COV-STAT-SQL"}
    assert s.cov_args == {"COV-STAT-SQL.sort_by_metrics"}
    assert sc.check_registry(scenarios, registry) == []


@pytest.mark.parametrize(
    ("raw", "rule"),
    [
        (_raw(split="test"), "S-split"),
        (_raw(covers=["cov-x"]), "S-covers"),
        (_raw(covers=[]), "S-covers"),
        (_raw(extra=1), "S-key"),
        (_raw(heavy="yes"), "S-flag"),
        (_raw(turns=[]), "S-turns"),
        (_with_expect(target="server:1"), "S-target"),
        (_with_expect(window="range:abc"), "S-window"),
        (_with_expect(clarify="maybe"), "S-clarify"),
        (_with_expect(views=None), "S-views"),
        (_with_expect(agents=["data_query"]), "S-agents"),
        (_with_expect(target="ref"), "S-ref-first"),
        (_with_expect(target="host:2"), "S-target-prompt"),
        (_with_expect(refusal="write_control"), "S-refusal"),
    ],
)
def test_structure_violations(raw: dict[str, Any], rule: str) -> None:
    _scenarios, issues = _parse(raw)
    assert rule in _rules(issues)


def test_duplicate_id_and_reply_to() -> None:
    two_turn = _raw(id="T-2")
    two_turn["turns"].append(
        {
            "prompt": "은행존",
            "reply_to": "source",
            "expect": copy.deepcopy(two_turn["turns"][0]["expect"]),
        }
    )
    _s, issues = _parse(_raw(), _raw(), two_turn)
    assert "S-id-dup" in _rules(issues)
    assert any(i.rule == "S-reply-to" and i.scenario_id == "T-2" for i in issues)


def test_refusal_scenario_may_have_no_covers() -> None:
    raw = _raw(
        covers=[],
        turns=[
            {
                "prompt": "{{inst_1}} 재기동해줘",
                "expect": {
                    "agents": [],
                    "target": "inst:1",
                    "window": "none",
                    "refusal": "write_control",
                },
            }
        ],
    )
    scenarios, issues = _parse(raw)
    assert issues == [] and scenarios[0].turns[0].expect.views == ()


def test_load_scenarios_raises_on_structure(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump([_raw(split="x")], allow_unicode=True), encoding="utf-8")
    with pytest.raises(sc.ScenarioError) as exc:
        sc.load_scenarios(path)
    assert {i.rule for i in exc.value.issues} == {"S-split"}


# --- 린트 (R2) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "rule"),
    [
        ("{{inst_1}} apm_status_stats 로 봐줘", "R2-tool-name"),
        ("{{inst_1}} apm.sql_stats 보기", "R2-view-name"),
        ("{{inst_1}} /api/status/sql 결과", "R2-api-path"),
        ("{{inst_1}} SQL sort_by 응답시간", "R2-arg-key"),
        ("{{inst_1}} SQL 상위 n 개", "R2-arg-key"),
        ("{{inst_1}} 10.20.30.40 서버 SQL", "R2-ip"),
        ("{{inst_1}} {{prod_db_1}} SQL", "R2-placeholder"),
        ("{{inst_1}} {{inst_2 SQL", "R2-placeholder"),
    ],
)
def test_lint_catches_injection(prompt: str, rule: str, registry: sc.RegistryInfo) -> None:
    raw = _raw()
    raw["turns"][0]["prompt"] = prompt
    scenarios, structure = _parse(raw)
    issues = sc.lint_scenarios(scenarios, registry.arg_keys)
    assert rule in _rules(issues), [i.line() for i in issues + structure]


def test_lint_allows_natural_english(registry: sc.RegistryInfo) -> None:
    found = sc.lint_text(
        "GUID {{guid_1}} 거래 · 이름에 Order 들어간 클래스 · p95 · JAVA_HOME · fatal",
        registry.arg_keys,
    )
    assert found == []


def test_lint_hostlike_is_warning_only(registry: sc.RegistryInfo) -> None:
    found = sc.lint_text("kbwas01 응답시간", registry.arg_keys)
    assert [(rule, level) for rule, _msg, level in found] == [("R2-hostlike", "warning")]


def test_lint_placeholder_in_expected_arg(registry: sc.RegistryInfo) -> None:
    raw = _with_expect(args={"sort_by": "{{secret_1}}"})
    scenarios, _ = _parse(raw)
    assert "R2-placeholder" in _rules(sc.lint_scenarios(scenarios, registry.arg_keys))


# --- 레지스트리 정합 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "rule"),
    [
        ({"views": ["apm.sql_top"]}, "G-view"),
        ({"args": {"sort_by": "responseTime", "limit": 10}}, "G-arg"),
        ({"args": {"top_k": 3}}, "G-arg"),
        ({"args": {"n": 0}}, "G-arg-value"),
        ({"args": {"full": "yes"}}, "G-arg-value"),
        ({"apm_sources": ["mars"]}, "G-source"),
    ],
)
def test_registry_mismatch(changes: dict[str, Any], rule: str, registry: sc.RegistryInfo) -> None:
    scenarios, structure = _parse(_with_expect(**changes))
    assert structure == []
    assert rule in _rules(sc.check_registry(scenarios, registry))


def test_registry_enum_and_any_value(registry: sc.RegistryInfo) -> None:
    raw = _with_expect(
        views=["apm.events"], args={"level": "fatal", "level_mode": "exact", "error_type": "*"}
    )
    scenarios, _ = _parse(raw)
    assert sc.check_registry(scenarios, registry) == []
    raw = _with_expect(views=["apm.events"], args={"level": "FATAL"})
    scenarios, _ = _parse(raw)
    assert _rules(sc.check_registry(scenarios, registry)) == {"G-arg-value"}


def test_registry_required_arg(registry: sc.RegistryInfo) -> None:
    raw = _with_expect(views=["apm.process"], target="none", window="none", args=None)
    raw["turns"][0]["prompt"] = "프로세스 번호로 WAS 인스턴스 찾아줘"
    scenarios, _ = _parse(raw)
    assert "G-required-arg" in _rules(sc.check_registry(scenarios, registry))
    raw["turns"][0]["expect"]["clarify"] = "required_arg"
    scenarios, _ = _parse(raw)
    assert sc.check_registry(scenarios, registry) == []
    raw["turns"][0]["expect"]["args"] = {"process_id": "{{pid_1}}"}
    scenarios, _ = _parse(raw)
    assert "G-required-arg" in _rules(sc.check_registry(scenarios, registry))


def test_covers_against_capability_map(cap: sc.CapabilityMap) -> None:
    scenarios, _ = _parse(
        _raw(id="A", covers=["COV-NOPE"]),
        _raw(id="B", covers=["COV-STAT-SQL.nope"]),
        _raw(id="C", covers=["COV-TX-TIME.time_pattern"]),
    )
    issues = sc.check_covers(scenarios, cap)
    assert {i.scenario_id for i in issues} == {"A", "B", "C"}


# --- 분할 -------------------------------------------------------------------------------


def _split_set(n_dev: int, n_hold: int, **flags: Any) -> list[sc.Scenario]:
    raws = [_raw(id=f"D{i}", covers=["COV-A", "COV-B"]) for i in range(n_dev)]
    raws += [_raw(id=f"H{i}", split="holdout", covers=["COV-A", "COV-B"]) for i in range(n_hold)]
    for raw in raws[: flags.get("heavy", 0)]:
        raw["heavy"] = True
    scenarios, issues = _parse(*raws)
    assert issues == []
    return scenarios


def test_split_ok() -> None:
    summary = sc.check_split(_split_set(7, 3), ["COV-A", "COV-B"])
    assert summary.issues == [] and summary.holdout == 3
    assert summary.per_path["COV-A"] == (tuple(f"D{i}" for i in range(7)), ("H0", "H1", "H2"))


def test_split_ratio_and_missing_side() -> None:
    rules = _rules(sc.check_split(_split_set(9, 1), ["COV-A", "COV-C"]).issues)
    assert {"P-ratio", "P-path-sides"} <= rules


def test_split_heavy_rules() -> None:
    assert "P-heavy-count" in _rules(sc.check_split(_split_set(14, 6, heavy=6), ["COV-A"]).issues)
    scenarios, _ = _parse(
        *[_raw(id=f"D{i}", covers=["COV-A"]) for i in range(7)],
        *[_raw(id=f"H{i}", split="holdout", covers=["COV-A"], heavy=(i == 0)) for i in range(3)],
    )
    assert _rules(sc.check_split(scenarios, ["COV-A"]).issues) == {"P-heavy-split"}


def test_split_was_touch_and_e2_only() -> None:
    raws = [_raw(id=f"D{i}", covers=["COV-A"]) for i in range(7)]
    raws[0]["was_touch"] = True  # 대상 inst:1 — inst_w 가 아니다
    hold = [
        _raw(id=f"H{i}", split="holdout", covers=["COV-A"], e2_skip="was_touch_limit")
        for i in range(3)
    ]
    scenarios, _ = _parse(*raws, *hold)
    issues = sc.check_split(scenarios, ["COV-A"]).issues
    assert _rules(issues) == {"P-was-touch"}
    assert _rules(issues, "warning") == {"P-e2-only"}


# --- 자리표 -----------------------------------------------------------------------------


def test_fill_placeholders() -> None:
    text = "{{inst_1}} 와 {{host_2}} 비교"
    assert sc.fill_placeholders(text, {"inst_1": "W1", "host_2": "H2"}) == "W1 와 H2 비교"
    with pytest.raises(sc.PlaceholderError) as exc:
        sc.fill_placeholders(text, {"inst_1": "W1"})
    assert exc.value.names == ["host_2"]
    with pytest.raises(sc.PlaceholderError):
        sc.fill_placeholders("{{inst_1}} {{host_", {"inst_1": "W1"})


# --- CLI · coverage.md ------------------------------------------------------------------


def test_cli_dry_run_and_coverage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--dry-run"]) == 0
    assert "결과       : 통과" in capsys.readouterr().out
    assert cli.main(["--coverage", "--out", str(tmp_path)]) == 0
    text = (tmp_path / "coverage.md").read_text(encoding="utf-8")
    for heading in (
        "## 요약",
        "## 표 A",
        "## 표 B",
        "### 표 B 제외",
        "## 레지스트리 보기",
        "## 분할",
    ):
        assert heading in text
    assert "미커버 합계: 0" in text


def test_cli_dry_run_fails_on_violation(tmp_path: Path) -> None:
    raws = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    raws[0]["turns"][0]["prompt"] = "apm_instance_map 결과 보여줘"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raws, allow_unicode=True), encoding="utf-8")
    assert cli.main(["--dry-run", "--scenarios", str(path)]) == 1


def test_cli_coverage_fails_on_uncovered(tmp_path: Path) -> None:
    raws = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    kept = [r for r in raws if "COV-RDB-EXPORT" not in r["covers"]]
    path = tmp_path / "few.yaml"
    path.write_text(yaml.safe_dump(kept, allow_unicode=True), encoding="utf-8")
    assert cli.main(["--coverage", "--scenarios", str(path), "--out", str(tmp_path)]) == 1
    assert "`COV-RDB-EXPORT` **미커버**" in (tmp_path / "coverage.md").read_text(encoding="utf-8")


def test_render_extra_sections(
    real: list[sc.Scenario], cap: sc.CapabilityMap, registry: sc.RegistryInfo
) -> None:
    report = sc.build_coverage(real, cap, registry)
    text = sc.render_coverage_md(report, extra_sections=[lambda _r: "## 예문 커버리지 × 실패율"])
    assert text.rstrip().endswith("## 예문 커버리지 × 실패율")
