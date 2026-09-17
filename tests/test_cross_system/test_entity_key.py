"""값 기반 서버 식별 키 판정 · 정규화 · 매칭 등급 (plans/102 X-1 · D-224 ②④).

컬럼 이름이 아니라 **값**으로 키를 판정한다는 것이 이 모듈의 존재 이유다 — 종전 이름 판정은
자산관리 결과(`sevrHostName`·`iPCtnt`)를 식별 컬럼으로 인정하지 않아 후속 조회가 막혔다.

순수 함수만 다루므로 LLM·DB·네트워크 0(D-127).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    KEY_FQDN,
    KEY_HOSTNAME,
    KEY_IPV4,
    KEY_IPV6,
    KEY_UNKNOWN,
    ROW_MULTIPLICITY_PER_IP,
    ManifestError,
    TargetEntity,
    cell_family,
    classify_column,
    classify_value,
    collect_family_values,
    detect_key_columns,
    grade_matches,
    is_short_name_match,
    normalize_value,
    parse_manifest,
    render_match_note,
    typed_tokens,
)


class TestClassifyValue:
    @pytest.mark.parametrize("value,expected", [
        ("svr-web-01", KEY_HOSTNAME),
        ("SVR-WEB-02", KEY_HOSTNAME),
        ("svr-web-03.synth.example", KEY_FQDN),
        ("svr-web-03.synth.example.", KEY_FQDN),   # 절대 표기 끝 점
        ("10.0.1.1", KEY_IPV4),
        ("2001:DB8::0:1", KEY_IPV6),
    ])
    def test_key_types(self, value, expected):
        assert classify_value(value) == expected

    @pytest.mark.parametrize("value", [
        "12345",          # 숫자 ID — RFC 1123은 허용하지만 키로 받지 않는다(D-100 계열)
        "4.0",            # EAV 숫자 문자열
        "1.2.3",          # IP도 호스트명도 아님
        "010.0.1.1",      # 선행 0 IPv4는 파싱 거부
        "-bad",           # 하이픈 시작
        "bad-",           # 하이픈 끝
        "host_1",         # 밑줄은 RFC 1123 밖
        "a" * 64,         # 레이블 63자 초과
        "N",              # 한 글자 — 플래그·자리표시 값
        "N/A",            # 셀 분해 전 원문은 구분자를 포함해 판정 불가
        "",
        "   ",
        None,
        True,
    ])
    def test_unknown_is_never_a_key(self, value):
        assert classify_value(value) == KEY_UNKNOWN

    def test_total_length_limit(self):
        label = "a" * 60
        name = ".".join([label] * 5)  # 304자
        assert classify_value(name) == KEY_UNKNOWN


class TestNormalize:
    def test_ipv6_rfc5952_compressed_lowercase(self):
        assert normalize_value("2001:DB8:0:0:0:0:0:1") == "2001:db8::1"
        assert normalize_value("2001:db8::0:1") == normalize_value("2001:DB8::1")

    def test_hostname_casefold_rfc4343(self):
        assert normalize_value("SVR-WEB-02") == "svr-web-02"
        assert normalize_value("Svr-Web-03.Synth.Example.") == "svr-web-03.synth.example"

    def test_unknown_normalizes_to_empty(self):
        assert normalize_value("12345") == ""


class TestMultiValueCell:
    def test_multi_ip_cell_yields_each_ip(self):
        tokens = typed_tokens("10.0.1.1, 10.0.1.2;10.0.1.3 / 10.0.1.4")
        assert [t.normalized for t in tokens] == ["10.0.1.1", "10.0.1.2", "10.0.1.3", "10.0.1.4"]
        assert {t.key_type for t in tokens} == {KEY_IPV4}

    def test_mixed_family_cell_has_no_family(self):
        assert cell_family("svr-web-01,10.0.1.1") is None

    def test_uniform_cell_family(self):
        assert cell_family("10.0.1.1,10.0.1.2") == FAMILY_IP
        assert cell_family("svr-web-01") == FAMILY_HOSTNAME


class TestClassifyColumn:
    def test_threshold_boundary_80_percent(self):
        # N/A → N·A 한 글자 → 판정 불가
        values = [f"svr-{i:02d}" for i in range(8)] + ["12345", "N/A"]
        assert classify_column(values) == FAMILY_HOSTNAME        # 8/10 = 80%

    def test_below_threshold_is_unknown_not_a_guess(self):
        values = [f"svr-{i:02d}" for i in range(7)] + ["12345", "N/A", "4.0"]
        assert classify_column(values) is None                   # 7/10 = 70%

    def test_short_and_fqdn_mix_is_one_family(self):
        values = ["svr-web-01", "svr-web-03.synth.example", "SVR-WEB-02"]
        assert classify_column(values) == FAMILY_HOSTNAME

    def test_sample_is_capped(self):
        values = [f"svr-{i:03d}" for i in range(50)] + ["12345"] * 200
        assert classify_column(values) == FAMILY_HOSTNAME        # 뒤쪽 200개는 표본 밖

    def test_placeholder_flag_column_is_not_a_key(self):
        assert classify_column(["N/A", "Y", "N", "-", "N/A"]) is None

    def test_blank_values_are_not_counted(self):
        assert classify_column([None, "", "  ", "10.0.1.1"]) == FAMILY_IP

    def test_numeric_id_column_never_becomes_a_key(self):
        assert classify_column([str(i) for i in range(1, 60)]) is None


class TestDetectKeyColumns:
    def test_asset_style_column_names_are_recognized_by_value(self):
        """자산관리 결과 컬럼명은 이름 목록에 없어도 값으로 키가 된다(§1.2 실행 재현의 반대)."""
        rows = [
            {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1", "elapsNoy": 5},
            {"sevrHostName": "SVR-WEB-02", "iPCtnt": "10.0.1.2,10.0.1.3", "elapsNoy": 7},
        ]
        found = {kc.column: kc.family for kc in detect_key_columns(rows)}
        assert found == {"sevrHostName": FAMILY_HOSTNAME, "iPCtnt": FAMILY_IP}

    def test_strong_key_ranks_first(self):
        rows = [{"label": "svr-a", "hostname": "svr-b"}, {"label": "svr-c", "hostname": "svr-d"}]
        ordered = detect_key_columns(rows, name_hint=lambda c: c == "hostname")
        assert [kc.column for kc in ordered][0] == "hostname"
        assert ordered[0].strong is True

    def test_repeated_category_value_ranks_below_distinct_hosts(self):
        rows = [
            {"kind": "server.Server", "host": f"svr-{i:02d}"} for i in range(3)
        ]
        ordered = detect_key_columns(rows)
        assert [kc.column for kc in ordered] == ["host", "kind"]

    def test_excluded_columns_are_skipped(self):
        rows = [{"_source_db": "polestar", "h": "svr-a"}]
        assert [kc.column for kc in detect_key_columns(rows, exclude=("_source_db",))] == ["h"]

    def test_collect_family_values_dedups_by_normalized_form(self):
        rows = [{"h": "svr-a"}, {"h": "SVR-A"}, {"h": "svr-b"}]
        got = collect_family_values(rows, "h", FAMILY_HOSTNAME)
        assert [t.normalized for t in got] == ["svr-a", "svr-b"]


class TestManifest:
    def test_parse_polestar_style(self):
        m = parse_manifest("db", {
            "entity": "server", "table": "cmm_resource",
            "keys": [
                {"type": "ip", "column": "ipaddress", "priority": 2},
                {"type": "hostname", "column": "hostname", "priority": 1, "compare": "casefold"},
            ],
        })
        assert m.families() == (FAMILY_HOSTNAME, FAMILY_IP)
        assert m.key_for(FAMILY_IP).column == "ipaddress"

    def test_parse_per_ip_multiplicity(self):
        m = parse_manifest("db", {
            "entity": "server", "table": "t",
            "keys": [{"type": "ip", "column": "c", "multi_value": True}],
            "row_multiplicity": "per_ip",
        })
        assert m.row_multiplicity == ROW_MULTIPLICITY_PER_IP
        assert m.keys[0].multi_value is True

    @pytest.mark.parametrize("data", [
        None,
        {"entity": "server", "keys": [{"type": "ip", "column": "c"}]},           # table 없음
        {"entity": "server", "table": "t", "keys": []},
        {"entity": "server", "table": "t", "keys": [{"type": "serial", "column": "c"}]},
        {"entity": "server", "table": "t", "keys": [{"type": "ip", "column": ""}]},
        {"entity": "server", "table": "t",
         "keys": [{"type": "ip", "column": "c", "compare": "fuzzy"}]},
        {"entity": "server", "table": "t",
         "keys": [{"type": "ip", "column": "c"}], "row_multiplicity": "x"},
    ])
    def test_malformed_manifest_is_rejected_not_partially_accepted(self, data):
        with pytest.raises(ManifestError):
            parse_manifest("db", data)


class TestGradeMatches:
    TARGETS = (
        TargetEntity("e1", frozenset({"svr-web-01"})),
        TargetEntity("e2", frozenset({"svr-web-02"})),
        TargetEntity("e3", frozenset({"svr-web-03.synth.example"})),
        TargetEntity("e4", frozenset({"dup"})),
        TargetEntity("e5", frozenset({"dup"})),
    )

    def test_four_grades_and_total_equals_n(self):
        report = grade_matches(
            ["svr-web-01", "svr-web-02", "svr-web-03", "zzz", "dup"],
            self.TARGETS, family=FAMILY_HOSTNAME,
        )
        assert set(report.link) == {"svr-web-01", "svr-web-02"}
        assert set(report.possible) == {"svr-web-03"}
        assert report.non_link == ("zzz",)
        assert set(report.ambiguous) == {"dup"}
        assert report.total == 5
        assert report.coverage == pytest.approx(3 / 5)

    def test_ambiguous_is_not_included(self):
        report = grade_matches(["dup", "svr-web-01"], self.TARGETS, family=FAMILY_HOSTNAME)
        assert report.included_entities() == ("e1",)

    def test_per_ip_rows_of_one_entity_are_not_ambiguous(self):
        """같은 엔터티의 IP별 행은 호출부가 한 엔터티로 묶어 넘긴다(X-T5)."""
        targets = [TargetEntity("svr-web-04", frozenset({"10.0.4.1", "10.0.4.2"}))]
        report = grade_matches(["10.0.4.1", "10.0.4.2"], targets, family=FAMILY_IP)
        assert set(report.link) == {"10.0.4.1", "10.0.4.2"}
        assert report.ambiguous == {}

    def test_ip_family_has_no_possible_grade(self):
        targets = [TargetEntity("x", frozenset({"10.0.1.10"}))]
        report = grade_matches(["10.0.1.1"], targets, family=FAMILY_IP)
        assert report.non_link == ("10.0.1.1",)

    def test_two_fqdns_with_same_short_name_are_not_possible(self):
        assert is_short_name_match("svr.a.example", "svr.b.example") is False
        assert is_short_name_match("svr", "svr.b.example") is True

    def test_exact_match_wins_over_short_name(self):
        targets = [
            TargetEntity("full", frozenset({"svr.a.example"})),
            TargetEntity("short", frozenset({"svr"})),
        ]
        report = grade_matches(["svr"], targets, family=FAMILY_HOSTNAME)
        assert report.link == {"svr": ("short",)}

    def test_note_renders_counts_and_key(self):
        report = grade_matches(["svr-web-01", "zzz"], self.TARGETS, family=FAMILY_HOSTNAME)
        note = render_match_note(report, target_label="자산관리", key_label="hostname")
        assert note == "선행 2대 → 자산관리: 일치 1 · 가능 0 · 미발견 1(zzz) · 모호 0 · 키=hostname"


def test_module_is_pure_domain():
    """도메인 계층 — 표준 라이브러리 밖·프로젝트 다른 계층을 import하지 않는다(I/O·LLM 0)."""
    source = Path("src/domain/entity_key.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"__future__", "collections", "ipaddress", "re", "dataclasses", "typing"}
    assert imported <= allowed, imported
