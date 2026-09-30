"""문서 코퍼스 정본 로더 계약 (plans/126 §4.1 · W1).

실 LLM 0 · 외부 호출 0.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from src.infrastructure import doc_sources as ds


def _write(tmp_path, body: str):
    path = tmp_path / "rag_collections.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    ds.load_collection_meta.cache_clear()
    return str(path)


def _cfg(**kw):
    """`AppConfig.rag` 대역 — 기능은 켠 상태가 기본이고 접속 필드만 사례별로 준다."""
    kw.setdefault("enabled", True)
    kw.setdefault("collections_file", "")
    return SimpleNamespace(**kw)


_MINIMAL = """
    version: 1
    collections:
      - id: hq_manual
        title: 본부 전산관리매뉴얼
        description: 규정과 절차
        surface_terms: [전산관리매뉴얼, 본부매뉴얼]
      - id: arch_docs
        title: 아키텍처 설계문서
        description: 구성과 설계 근거
        surface_terms: [아키텍처, 설계문서]
"""


def test_meta_loads_two_collections(tmp_path):
    path = _write(tmp_path, _MINIMAL)
    metas = ds.load_collection_meta(path)
    assert [m.id for m in metas] == ["hq_manual", "arch_docs"]
    assert metas[0].surface_terms == ("전산관리매뉴얼", "본부매뉴얼")


def test_duplicate_id_rejected(tmp_path):
    path = _write(tmp_path, """
        version: 1
        collections:
          - id: dup
            title: A
          - id: dup
            title: B
    """)
    with pytest.raises(ds.DocSourcesError, match="중복"):
        ds.load_collection_meta(path)


def test_empty_id_rejected(tmp_path):
    path = _write(tmp_path, """
        version: 1
        collections:
          - title: 제목만 있음
    """)
    with pytest.raises(ds.DocSourcesError, match="id"):
        ds.load_collection_meta(path)


@pytest.mark.parametrize("key", ["top_k", "threshold", "rerank_threshold", "hyde"])
def test_platform_control_keys_rejected_at_top_level(tmp_path, key):
    """검색 파라미터는 플랫폼 전속 — 정본에 제어용 사본을 만들 수 없다(§3.3 ①)."""
    path = _write(tmp_path, f"""
        version: 1
        collections:
          - id: hq_manual
            title: A
            {key}: 3
    """)
    with pytest.raises(ds.DocSourcesError, match="플랫폼 전속"):
        ds.load_collection_meta(path)


def test_platform_params_snapshot_is_allowed_as_record(tmp_path):
    """기록 목적의 스냅샷은 허용된다 — 단 코드가 필터에 쓰지 않는다."""
    path = _write(tmp_path, """
        version: 1
        collections:
          - id: hq_manual
            title: A
            platform_params_snapshot:
              top_k: 3
              rerank: BGE-M3
    """)
    meta = ds.load_collection_meta(path)[0]
    assert meta.platform_params_snapshot["top_k"] == 3


def test_snapshot_values_are_never_used_as_filters():
    """회귀 방지: 플랫폼 파라미터를 읽어 임계·건수 필터로 쓰는 코드가 없다(§3.3 ①).

    플랫폼이 이미 threshold·rerank·top_k 로 걸러 보낸 결과를 우리 척도로 다시 거르면 남의
    판정을 뒤집고, 플랫폼이 임계를 낮출 때 우리 값이 조용히 진짜 필터로 바뀐다. 그래서
    ①`rank_score` 비교 ②`platform_params_snapshot` 값을 조건으로 쓰는 코드를 금지한다.
    (`FORBIDDEN_CONTROL_KEYS` 같은 *거부 목록*은 필터가 아니므로 이름만으로 판정하지 않는다.)
    """
    from pathlib import Path
    root = Path(ds.__file__).resolve().parent.parent.parent
    for rel in ("src/infrastructure/doc_sources.py", "src/clients/fabrix_retrieval.py"):
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            if "rank_score" in code and any(op in code for op in ("<", ">")):
                raise AssertionError(f"{rel}:{n} rank_score 비교(임계 필터) 발견: {line.strip()}")
            if "platform_params_snapshot" in code and (
                code.lstrip().startswith(("if ", "elif ", "while ", "assert "))
                or any(op in code for op in ("<", ">", "==", "!="))
            ):
                raise AssertionError(
                    f"{rel}:{n} 스냅샷을 조건으로 사용: {line.strip()}"
                )


def test_missing_file_is_not_fatal(tmp_path):
    ds.load_collection_meta.cache_clear()
    assert ds.load_collection_meta(str(tmp_path / "nope.yaml")) == ()


def test_incomplete_connection_set_is_degraded_with_reason(tmp_path):
    """접속 4종 중 하나라도 비면 비활성 + 사유(침묵 금지 · 부분 설정이 오진의 주 원인)."""
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(
        hq_manual_endpoint="http://x/retrieval",
        hq_manual_token="t",
        hq_manual_client_key="c",
        hq_manual_retrieval_id="",          # ← 하나만 비었다
    )
    resolved = {c.id: c for c in ds.resolve_collections(cfg, path=path)}
    assert not resolved["hq_manual"].usable
    assert "RAG_HQ_MANUAL_RETRIEVAL_ID" in resolved["hq_manual"].disabled_reason
    # 필드가 아예 없는 컬렉션도 조용히 활성되지 않는다.
    assert not resolved["arch_docs"].usable


def test_complete_connection_set_is_usable(tmp_path):
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(
        hq_manual_endpoint="http://x/retrieval", hq_manual_token="tok1234",
        hq_manual_client_key="cli5678", hq_manual_retrieval_id="20260928181958_abc",
    )
    usable = ds.usable_collections(cfg, path=path)
    assert [c.id for c in usable] == ["hq_manual"]
    assert usable[0].asset_recorded_at == "2026-09-28 18:19"
    masked = usable[0].masked_connection()
    assert masked["token"] == "…1234" and masked["client_key"] == "…5678"
    assert "tok1234" not in str(masked)


def test_disabled_in_manifest_stays_disabled(tmp_path):
    path = _write(tmp_path, """
        version: 1
        collections:
          - id: hq_manual
            title: A
            enabled: false
    """)
    cfg = _cfg(
        hq_manual_endpoint="http://x", hq_manual_token="t",
        hq_manual_client_key="c", hq_manual_retrieval_id="r",
    )
    col = ds.find_collection(cfg, "hq_manual", path=path)
    assert col is not None and not col.usable
    assert "비활성" in col.disabled_reason


def test_asset_recorded_at_blank_when_format_differs(tmp_path):
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(
        hq_manual_endpoint="e", hq_manual_token="t",
        hq_manual_client_key="c", hq_manual_retrieval_id="not-a-timestamp",
    )
    assert ds.find_collection(cfg, "hq_manual", path=path).asset_recorded_at == ""


def test_startup_summary_names_active_and_inactive(tmp_path):
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(
        hq_manual_endpoint="e", hq_manual_token="t",
        hq_manual_client_key="c", hq_manual_retrieval_id="20260928181958_a",
    )
    summary = ds.startup_summary(cfg, path=path)
    assert "1/2" in summary
    assert "hq_manual" in summary and "arch_docs" in summary


class TestStripSurfacePrefix:
    """질의 규칙 3 — 머리에 붙은 컬렉션 표면어만 떼어낸다(용어 보존이 규칙 1)."""

    def _cols(self, tmp_path):
        path = _write(tmp_path, _MINIMAL)
        return ds.load_collection_meta(path)

    def test_removes_leading_term_with_particle(self, tmp_path):
        cols = self._cols(tmp_path)
        assert ds.strip_surface_prefix(
            "전산관리매뉴얼에서 계정 신청 절차", cols
        ) == "계정 신청 절차"

    def test_keeps_term_in_the_middle(self, tmp_path):
        """문장 중간의 용어는 사용자가 의도한 검색어일 수 있어 건드리지 않는다."""
        cols = self._cols(tmp_path)
        q = "계정 신청 절차가 전산관리매뉴얼 몇 장에 있나"
        assert ds.strip_surface_prefix(q, cols) == q

    def test_never_returns_empty(self, tmp_path):
        cols = self._cols(tmp_path)
        assert ds.strip_surface_prefix("아키텍처", cols) == "아키텍처"

    def test_no_collections_is_identity(self):
        assert ds.strip_surface_prefix("  계정 신청  ", []) == "계정 신청"


def test_feature_flag_off_disables_every_collection(tmp_path):
    """RAG_ENABLED=false 면 접속 정보가 완전해도 전부 비활성 + 사유(기본 off · D-162)."""
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(
        enabled=False,
        hq_manual_endpoint="e", hq_manual_token="t",
        hq_manual_client_key="c", hq_manual_retrieval_id="20260928181958_a",
    )
    items = ds.resolve_collections(cfg, path=path)
    assert all(not c.usable for c in items)
    assert "RAG_ENABLED=false" in items[0].disabled_reason


def test_collections_file_setting_is_used_when_path_omitted(tmp_path):
    """경로 인자가 없으면 RAG_COLLECTIONS_FILE 을 읽는다."""
    path = _write(tmp_path, _MINIMAL)
    cfg = _cfg(collections_file=path)
    assert [c.id for c in ds.resolve_collections(cfg)] == ["hq_manual", "arch_docs"]


def test_unknown_collection_has_no_config_fields(tmp_path):
    """정본에만 있고 설정 필드가 없는 코퍼스는 조용히 켜지지 않는다(CONNECTION_FIELD_MAP 부재)."""
    path = _write(tmp_path, """
        version: 1
        collections:
          - id: brand_new_corpus
            title: 신규
    """)
    col = ds.find_collection(_cfg(), "brand_new_corpus", path=path)
    assert col is not None and not col.usable
    assert "RAG_BRAND_NEW_CORPUS_ENDPOINT" in col.disabled_reason
