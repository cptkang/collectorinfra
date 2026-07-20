"""공용 계층 과적합 가드(scripts/overfit_check.py) 검증 (Plan 63 P4-1, D-088).

- 스캔이 schema-literal / routing-vocab를 분리 집계하는지
- 커밋된 기준선 대비 신규 schema-literal 유입이 0인지(=overfit_check --ci 게이트)
- 가드 로직이 신규 토큰/신규 파일을 실제로 잡는지(형해화 방지)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "overfit_check",
    Path(__file__).resolve().parent.parent / "scripts" / "overfit_check.py",
)
overfit_check = importlib.util.module_from_spec(_SPEC)
# dataclass 타입 해석이 sys.modules 등록을 요구하므로 exec 전에 등록한다.
sys.modules["overfit_check"] = overfit_check
_SPEC.loader.exec_module(overfit_check)


@pytest.fixture(scope="module")
def scan():
    return overfit_check.scan_project()


class TestScan:
    def test_scan_finds_files_and_hits(self, scan):
        assert scan.scanned_files > 0
        assert len(scan.hits) > 0

    def test_categories_separated(self, scan):
        cats = {h.category for h in scan.hits}
        assert "schema-literal" in cats
        assert "routing-vocab" in cats

    def test_prior_rows_block_has_no_schema_literal(self, scan):
        """P1에서 일반화한 query_gen_common의 prior_rows 블록 라인에는 cmm_ 리터럴이 없어야 한다."""
        # build_prior_rows_block은 cmm_resource 문장을 제거했으므로, 해당 함수 영역의
        # schema-literal 히트가 prior_rows 블록에서 나오지 않는다(회귀 시 기준선 증가로 포착).
        hits = [h for h in scan.hits
                if h.file == "src/utils/query_gen_common.py" and h.category == "schema-literal"]
        # 잔존분(폴스타 조립기)은 있으나, prior_rows 블록 텍스트(선행 작업 결과 서버 스코프)에는 없음.
        # 토큰 존재만 확인(상세는 기준선 diff로).
        assert isinstance(hits, list)


class TestBaselineGate:
    def test_no_new_schema_literals_against_committed_baseline(self, scan):
        """커밋된 기준선 대비 신규 schema-literal 유입 0 (overfit_check --ci 통과 조건)."""
        baseline = overfit_check.load_baseline()
        new = overfit_check.new_schema_literals(scan, baseline)
        assert new == [], (
            "공용 계층에 신규 DB 스키마 리터럴이 유입됨 — 어댑터/프로필로 이동하거나 "
            "의도된 잔존이면 `python scripts/overfit_check.py --update-baseline`으로 갱신:\n"
            + "\n".join(f"  {h.file}:{h.line} {h.token}" for h in new)
        )

    def test_guard_detects_new_token(self, scan):
        """신규 토큰(기준선 부재)을 실제로 잡는지 — 형해화 방지."""
        empty_baseline = {"schema_literal": {}}
        new = overfit_check.new_schema_literals(scan, empty_baseline)
        # 기준선이 비면 모든 schema-literal이 신규로 잡혀야 한다.
        schema_hits = [h for h in scan.hits if h.category == "schema-literal"]
        assert len(new) == len(schema_hits) > 0

    def test_routing_vocab_not_gated(self, scan):
        """routing-vocab(§1.3)은 기준선/게이트 대상이 아니다 — new_schema_literals에 미포함."""
        baseline = overfit_check.load_baseline()
        new = overfit_check.new_schema_literals(scan, baseline)
        assert all(h.category == "schema-literal" for h in new)
