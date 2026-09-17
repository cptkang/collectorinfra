"""자산관리(ITAM) 샌드박스 init SQL 생성기 고정 (plans/95 W-S1 · §4.6.3).

도커 없이 기본 스위트에서 돈다. 커밋된 생성물이 전사본과 어긋나면(한쪽만 고침) 여기서 잡힌다.
"""

from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GEN_PATH = _ROOT / "testdata" / "itam" / "generate_init.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("itam_generate_init", _GEN_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


@pytest.fixture(scope="module")
def transcript():
    return gen.load_transcript()


class TestRegenerationIsStable:
    def test_schema_sql_diff_zero(self, transcript):
        assert gen.SCHEMA_SQL.read_text(encoding="utf-8") == gen.render_schema_sql(transcript), (
            "01_schema.sql이 전사본과 어긋난다 — python testdata/itam/generate_init.py로 재생성"
        )

    def test_seed_sql_diff_zero(self, transcript):
        assert gen.SEED_SQL.read_text(encoding="utf-8") == gen.render_seed_sql(transcript), (
            "02_seed.sql이 생성기와 어긋난다 — python testdata/itam/generate_init.py로 재생성"
        )

    def test_generated_files_declare_identifier_assumption(self):
        for path in (gen.SCHEMA_SQL, gen.SEED_SQL):
            assert "G-4 미확정" in path.read_text(encoding="utf-8").splitlines()[3]


class TestTranscriptIntegrity:
    def test_counts_and_pk(self, transcript):
        for table, expected in {"TCDMSIF80": 68, "TCDMSIF79": 9}.items():
            spec = transcript["tables"][table]
            assert len(spec["columns"]) == expected
            assert spec["primary_key"] == ["groupCoCd", "sevrHostName", "iPCtnt"]

    def test_ddl_has_all_columns_and_composite_pk(self, transcript):
        ddl = gen.render_schema_sql(transcript)
        assert ddl.count("PRIMARY KEY (`groupCoCd`, `sevrHostName`, `iPCtnt`)") == 2
        for table in ("TCDMSIF80", "TCDMSIF79"):
            for col in transcript["tables"][table]["columns"]:
                assert f"`{col['var']}` {col['type']}" in ddl

    @pytest.mark.parametrize(
        "corrupt",
        [
            # flow 매핑 쉼표 절단 — 파일은 로드되고 값만 잘린다(docs/18 2026-09-15)
            lambda d: d["tables"]["TCDMSIF80"]["columns"][17].__setitem__("type", "DECIMAL(5"),
            # `no:`/`on:` 류 불리언 키 오염 — seq가 사라진다
            lambda d: d["tables"]["TCDMSIF79"]["columns"][0].pop("seq"),
            # 컬럼 누락
            lambda d: d["tables"]["TCDMSIF79"]["columns"].pop(),
            # PK 순서 뒤바뀜
            lambda d: d["tables"]["TCDMSIF80"].__setitem__(
                "primary_key", ["sevrHostName", "groupCoCd", "iPCtnt"]
            ),
        ],
    )
    def test_validation_rejects_silent_corruption(self, transcript, corrupt):
        data = copy.deepcopy(transcript)
        corrupt(data)
        with pytest.raises(ValueError):
            gen.validate_transcript(data)


class TestSeedDesign:
    def _rows(self, transcript, table):
        columns = [c["var"] for c in transcript["tables"][table]["columns"]]
        return [dict(zip(columns, values)) for _, values in gen.build_seed_rows(transcript)[table]]

    def test_code_columns_are_synthetic_markers(self, transcript):
        """합성 코드값이 프로필 column_values로 새면 표식으로 즉시 들킨다(plans/95 §4.6.4)."""
        for table in ("TCDMSIF80", "TCDMSIF79"):
            code_cols = [c for c in transcript["tables"][table]["columns"] if c.get("code")]
            assert code_cols
            for row in self._rows(transcript, table):
                for col in code_cols:
                    width = int(re.search(r"\((\d+)\)", col["type"]).group(1))
                    assert re.fullmatch(r"Z9*", row[col["var"]]) and len(row[col["var"]]) == width

    def test_usage_rates_carry_discriminator_fraction(self, transcript):
        for row in self._rows(transcript, "TCDMSIF80"):
            for col in gen.USAGE_RATE_COLUMNS:
                assert str(row[col]).endswith(".37")

    def test_not_null_columns_filled(self, transcript):
        for table in ("TCDMSIF80", "TCDMSIF79"):
            columns = transcript["tables"][table]["columns"]
            required = [c["var"] for c in columns if not c["nullable"]]
            for row in self._rows(transcript, table):
                assert all(row[c] is not None for c in required)

    def test_host_key_forms_and_missing_eol_pair(self, transcript):
        main = {r["sevrHostName"]: r for r in self._rows(transcript, "TCDMSIF80")}
        eol_hosts = {r["sevrHostName"] for r in self._rows(transcript, "TCDMSIF79")}
        assert len(main) == 30
        assert {"svr-web-01", "SVR-WEB-02", "svr-web-03.synth.example"} <= set(main)
        assert "," in main["svr-web-04"]["iPCtnt"]
        assert set(main) - eol_hosts == {"svr-was-06"}
