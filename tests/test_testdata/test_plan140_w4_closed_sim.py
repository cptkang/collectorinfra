"""ITAM 외부망 모의 DB 생성기 (plans/140 W4 · D-311 ②⑤).

도커·DB 접속 없이 돈다. 픽스처는 합성 이름(`t_a`·`가상코드` …)만 쓴다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.schema_cache.ddl_schema_parser import parse_ddl

_ROOT = Path(__file__).resolve().parents[2]
_SIM = _ROOT / "testdata" / "itam_closed_sim"
_EXPORT = _ROOT / "results" / "itam_bench" / "20261006-152938"


def _load_generator():
    spec = importlib.util.spec_from_file_location("itam_closed_sim_generate", _SIM / "generate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass가 모듈을 sys.modules에서 찾는다
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


def _col(name: str, type_: str = "char", *, nullable: bool = False, **extra: Any) -> dict[str, Any]:
    node = {
        "name": name,
        "meaning": None,
        "meaning_source": "none",
        "type": type_,
        "nullable": nullable,
    }
    node.update(extra)
    return node


def _catalog() -> dict[str, Any]:
    """부모 t_a(기본키 가상키) ← 자식 t_b · 복합키 t_c · 근거 칸을 고루 담은 합성 카탈로그."""
    rel_ab = {
        "from": "t_b",
        "to": "t_a",
        "columns": [["부모키", "가상키"]],
        "kind": "p1",
        "origin": "name_match",
        "overlap": 0.98,
        "sampled": 100,
        "accepted": True,
        "unique_parent": True,
        "error": None,
    }
    rel_rejected = {**rel_ab, "from": "t_c", "columns": [["가상키", "가상키"]], "accepted": False}
    return {
        "db_id": "itam",
        "tables": {
            "t_a": {
                "meaning": "가상 '부모' 테이블",
                "meaning_source": "db_comment",
                "key": ["가상키"],
                "relations": [rel_ab],
                "columns": [
                    _col("가상키", "char(12)"),
                    _col("가상코드", "char(3)", nullable=True),
                    _col(
                        "가상상태",
                        "char(1)",
                        meaning="상태 (1:정상, 2:장애, 9:폐기)",
                        meaning_source="db_comment",
                    ),
                    _col("가상여부", "char(1)", profile={"candidate": "code", "flag": ["Y", "N"]}),
                    _col(
                        "가상일자", "char(8)", nullable=True, profile={"formats": {"date8": 0.99}}
                    ),
                    _col("가상일시", "char", profile={"formats": {"datetime14": 1.0}}),
                    _col("가상주소", "varchar(15)", profile={"formats": {"ipv4": 0.97}}),
                    _col(
                        "가상장비", "varchar", profile={"formats": {"hostname": 0.96, "date8": 0.5}}
                    ),
                    _col(
                        "가상설명",
                        "varchar",
                        nullable=True,
                        meaning="설명 \\ 'x'",
                        meaning_source="db_comment",
                    ),
                    _col("가상금액", "decimal", nullable=True),
                    _col(
                        "가상비고",
                        "varchar(40)",
                        nullable=True,
                        meaning="llm 설명",
                        meaning_source="cache_description",
                    ),
                ],
            },
            "t_b": {
                "meaning": None,
                "meaning_source": "none",
                "key": [],
                "relations": [rel_ab],
                "columns": [
                    _col("부모키", "char(12)"),
                    _col("가상값", "decimal(10,2)", nullable=True),
                ],
            },
            "t_c": {
                "meaning": None,
                "meaning_source": "none",
                "key": ["가상코드", "순번"],
                "relations": [rel_rejected],
                "columns": [
                    _col("가상코드", "char(3)"),
                    _col("순번", "decimal(5,0)"),
                    _col("가상키", "char(12)"),
                ],
            },
        },
    }


_SAMPLES = {
    "db_id": "itam",
    "columns": {
        "t_a.가상코드": {"distinct": 3, "substitution": "ok", "values": ["K01", "K02", "K03"]},
        "t_a.가상상태": {"distinct": 3, "substitution": "ok", "values": ["7", "8", "6"]},
        "t_c.가상코드": {"distinct": 2, "substitution": "ok", "values": ["Q1", "Q2"]},
        "t_c.순번": {"distinct": 10, "substitution": "exhausted"},
    },
}


@pytest.fixture
def catalog():
    return gen.load_catalog(_catalog())


@pytest.fixture
def samples():
    return gen.load_code_samples(_SAMPLES)


class TestDDL:
    def test_quoting_not_null_pk_and_default_types(self, catalog):
        ddl = gen.render_table_ddl(catalog.tables["t_a"])
        assert "`가상키` CHAR(12) NOT NULL" in ddl
        assert "`가상코드` CHAR(3)," in ddl  # NULL 허용 — NOT NULL 없음
        assert "`가상일시` CHAR(100) NOT NULL" in ddl
        assert "`가상장비` VARCHAR(500) NOT NULL" in ddl
        assert "`가상금액` DECIMAL(38,10)" in ddl
        assert "PRIMARY KEY (`가상키`)" in ddl
        assert gen.DEFAULT_TYPE_ARGS == {"char": "100", "varchar": "500", "decimal": "38,10"}

    def test_comment_only_from_db_comment_and_escaped(self, catalog):
        ddl = gen.render_table_ddl(catalog.tables["t_a"])
        assert "COMMENT '설명 \\\\ ''x'''" in ddl
        assert "llm 설명" not in ddl  # cache_description은 싣지 않는다
        assert "COMMENT='가상 ''부모'' 테이블'" in ddl

    def test_backtick_in_identifier_doubled(self):
        assert gen.quote_ident("a`b") == "`a``b`"

    def test_type_with_length_kept_and_suffix(self):
        assert gen._parse_type("decimal(15, 2) unsigned") == ("decimal", "15,2", "unsigned")
        assert gen._parse_type("double precision") == ("double precision", None, "")
        assert gen._parse_type("VARCHAR(30)") == ("varchar", "30", "")

    def test_schema_reparses_with_parse_ddl(self, catalog):
        text = gen.render_schema_sql(catalog, "fixture")
        result = parse_ddl(text, "mariadb")
        assert not result.warnings
        assert list(result.schema.tables) == ["t_a", "t_b", "t_c"]  # 이름순 결정적
        t_a = result.schema.tables["t_a"]
        assert len(t_a.columns) == 11
        assert [c.name for c in t_a.columns if c.is_primary_key] == ["가상키"]
        assert result.comments["t_a.가상설명"] == "설명 \\ 'x'"
        assert result.comments["t_a"] == "가상 '부모' 테이블"
        t_c = result.schema.tables["t_c"]
        assert [c.name for c in t_c.columns if c.is_primary_key] == ["가상코드", "순번"]

    def test_limits_detect_oversized_row_and_key(self):
        cols = [_col(f"c{i}", "varchar(2000)") for i in range(10)]
        data = {"tables": {"t_x": {"key": ["c0", "c1"], "columns": cols}}}
        problems = gen.check_limits(gen.load_catalog(data).tables["t_x"])
        assert any("행 최대 바이트" in p for p in problems)
        assert any("기본키 바이트" in p for p in problems)

    def test_only_accepted_p1_relations_kept(self, catalog):
        assert [(r.child, r.parent, r.pairs) for r in catalog.relations] == [
            ("t_b", "t_a", (("부모키", "가상키"),))
        ]


class TestRows:
    def _rows(self, catalog, samples, n=60, seed=7):
        return gen.generate_rows(catalog, samples, n_rows=n, seed=seed)

    def test_relation_values_come_from_parent_and_parent_unique(self, catalog, samples):
        res = self._rows(catalog, samples)
        parent_keys = [r["가상키"] for r in res.rows["t_a"]]
        assert len(parent_keys) == len(set(parent_keys)) == 60
        assert {r["부모키"] for r in res.rows["t_b"]} <= set(parent_keys)
        assert list(res.rows).index("t_a") < list(res.rows).index("t_b")  # 부모 먼저 생성

    def test_composite_pk_unique_and_shortfall_when_domain_small(self, catalog, samples):
        res = self._rows(catalog, samples)
        keys = [(r["가상코드"], r["순번"]) for r in res.rows["t_c"]]
        assert len(keys) == len(set(keys)) == 60
        assert {k[0] for k in keys} <= {"Q1", "Q2"}
        # 유한 값만으로 된 유일 컬럼은 값 수를 넘겨 만들 수 없다
        data = {"tables": {"t_y": {"key": ["가상코드"], "columns": [_col("가상코드", "char(3)")]}}}
        small = gen.generate_rows(
            gen.load_catalog(data), {("t_y", "가상코드"): ["A", "B"]}, n_rows=5, seed=1
        )
        assert len(small.rows["t_y"]) == 2 and small.shortfall == {"t_y": 3}

    def test_code_samples_used_and_comment_enum_wins(self, catalog, samples):
        res = self._rows(catalog, samples)
        codes = {r["가상코드"] for r in res.rows["t_a"]} - {None}
        assert codes and codes <= {"K01", "K02", "K03"}
        # 가상상태는 치환값(7·8·6)도 있지만 주석 열거(1·2·9)가 우선한다
        assert {r["가상상태"] for r in res.rows["t_a"]} <= {"1", "2", "9"}

    def test_exhausted_code_falls_back_to_type(self, samples):
        assert ("t_c", "순번") not in samples
        assert samples[("t_c", "가상코드")] == ["Q1", "Q2"]

    def test_flag_and_formats(self, catalog, samples):
        rows = self._rows(catalog, samples).rows["t_a"]
        assert {r["가상여부"] for r in rows} <= {"Y", "N"}
        assert all(
            re.fullmatch(r"20\d{6}", r["가상일자"]) for r in rows if r["가상일자"] is not None
        )
        assert all(re.fullmatch(r"20\d{12}", r["가상일시"]) for r in rows)
        nets = gen.DOC_IPV4_NETS
        assert all(r["가상주소"].rsplit(".", 1)[0] in nets for r in rows)
        assert all(r["가상장비"].startswith("sim-") for r in rows)  # hostname 0.96 > date8 0.5

    def test_nullable_columns_partly_null_not_null_never(self, catalog, samples):
        rows = self._rows(catalog, samples).rows["t_a"]
        assert any(r["가상설명"] is None for r in rows)
        assert any(r["가상설명"] is not None for r in rows)
        for name in ("가상키", "가상여부", "가상일시", "가상주소", "가상장비"):
            assert all(r[name] is not None for r in rows)

    def test_deterministic_by_seed(self, catalog, samples):
        a = gen.render_rows_sql(
            catalog, self._rows(catalog, samples, seed=3), "f", n_rows=60, seed=3
        )
        b = gen.render_rows_sql(
            catalog, self._rows(catalog, samples, seed=3), "f", n_rows=60, seed=3
        )
        c = gen.render_rows_sql(
            catalog, self._rows(catalog, samples, seed=4), "f", n_rows=60, seed=4
        )
        assert a == b and a != c

    def test_synthetic_text_has_no_person_like_shapes(self, catalog, samples):
        sql = gen.render_rows_sql(catalog, self._rows(catalog, samples), "f", n_rows=60, seed=7)
        assert not re.search(r"\d{6}-\d{7}", sql)  # 주민번호 형태
        assert not re.search(r"01\d-\d{3,4}-\d{4}", sql)  # 전화 형태

    def test_rows_sql_inserts_parent_before_child(self, catalog, samples):
        sql = gen.render_rows_sql(catalog, self._rows(catalog, samples), "f", n_rows=60, seed=7)
        assert sql.index("INSERT INTO `t_a`") < sql.index("INSERT INTO `t_b`")


class TestOutputGuard:
    def test_generated_dir_allowed(self):
        assert gen.ensure_safe_out(gen.DEFAULT_OUT / "sub") == (gen.DEFAULT_OUT / "sub").resolve()

    def test_outside_repo_allowed(self, tmp_path):
        assert gen.ensure_safe_out(tmp_path) == tmp_path.resolve()

    def test_tracked_repo_path_rejected(self):
        target = _SIM / "not_ignored_out"
        with pytest.raises(ValueError, match="출력 경로 거부"):
            gen.ensure_safe_out(target)
        assert not target.exists()

    def test_cli_rejects_before_writing(self, tmp_path, capsys):
        export = tmp_path / "export"
        export.mkdir()
        (export / "schema_catalog.yaml").write_text(
            yaml.safe_dump(_catalog(), allow_unicode=True), "utf-8"
        )
        assert (
            gen.main(["ddl", str(export), "--out", str(_ROOT / "testdata" / "itam_closed_sim")])
            == 2
        )
        assert "출력 경로 거부" in capsys.readouterr().err

    def test_cli_writes_schema_rows_and_readonly(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        (export / "schema_catalog.yaml").write_text(
            yaml.safe_dump(_catalog(), allow_unicode=True), "utf-8"
        )
        (export / "code_samples.yaml").write_text(
            yaml.safe_dump(_SAMPLES, allow_unicode=True), "utf-8"
        )
        out = tmp_path / "out"
        assert gen.main(["ddl", str(export), "--out", str(out)]) == 0
        assert gen.main(["rows", str(export), "--rows", "5", "--out", str(out)]) == 0
        assert sorted(p.name for p in out.iterdir()) == [
            "01_schema.sql",
            "02_rows.sql",
            "09_readonly_user.sql",
        ]
        assert "K0" in (out / "02_rows.sql").read_text("utf-8")  # 옆의 code_samples를 읽었다


class TestRepoFiles:
    def test_compose_binds_loopback_3308_and_generated_init(self):
        compose = yaml.safe_load((_SIM / "docker-compose.yml").read_text("utf-8"))
        service = compose["services"]["itam-closed-sim"]
        assert service["ports"] == ["127.0.0.1:3308:3306"]
        assert service["image"] == "mariadb:11.4"
        assert service["environment"]["MARIADB_DATABASE"] == "INST1"
        assert "./generated:/docker-entrypoint-initdb.d:ro" in service["volumes"]
        assert service["container_name"] != "itam_mariadb"
        assert "--character-set-server=utf8mb4" in service["command"]

    def test_generated_dir_is_gitignored(self):
        assert "generated/" in (_SIM / ".gitignore").read_text("utf-8").splitlines()

    def test_readonly_user_grants_select_only(self):
        sql = (_SIM / "readonly_user.sql").read_text("utf-8")
        assert "GRANT SELECT ON" in sql
        assert not re.search(r"GRANT\s+(ALL|INSERT|UPDATE|DELETE)", sql, re.IGNORECASE)

    def test_readme_type_table_matches_constants(self):
        readme = (_SIM / "README.md").read_text("utf-8")
        for base, args in gen.DEFAULT_TYPE_ARGS.items():
            assert f"`{base.upper()}({args})`" in readme


@pytest.mark.skipif(
    not (_EXPORT / "schema_catalog.yaml").is_file(), reason="1회차 반출 원본 없음(gitignore)"
)
def test_first_export_ddl_reproduces_108_tables(tmp_path):
    data = yaml.safe_load((_EXPORT / "schema_catalog.yaml").read_text("utf-8"))
    catalog = gen.load_catalog(data)
    assert not [p for t in catalog.tables.values() for p in gen.check_limits(t)]
    result = parse_ddl(gen.render_schema_sql(catalog, "export"), "mariadb")
    assert not result.warnings
    assert len(result.schema.tables) == 108
    assert sum(len(t.columns) for t in result.schema.tables.values()) == 1988
