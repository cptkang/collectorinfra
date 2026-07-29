"""질의 이력 초기 적재 스크립트 단위 테스트 (Plan 67 트랙 N / N2, D-133).

Redis 없이 검증하는 축:
    1. 원천 파싱 — 골드셋 26건(db_id는 item 선언값) + 프로필 query_examples.
    2. 원천 무변경 — 스크립트는 읽기 전용(파일 내용 해시 불변).
    3. 병합·중복 제거 — 동일 질의-SQL 쌍은 한 항목으로 접히고 골드셋 smq가 남는다.
    4. 실패 처리 — Redis 미가용 시 침묵하지 않고 exit 1 + 사유 출력.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLD_DIR = _REPO_ROOT / "testdata" / "text2sql_gold"
_PROFILE_DIR = _REPO_ROOT / "config" / "db_profiles"


def _load_script():
    """scripts/query_history_seed.py를 모듈로 로드한다(scripts는 패키지가 아님)."""
    path = _REPO_ROOT / "scripts" / "query_history_seed.py"
    spec = importlib.util.spec_from_file_location("query_history_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SEED = _load_script()

_FIXED_TS = "2026-07-29T00:00:00+0900"
_SELECT = "SELECT hostname FROM t"


def _dir_digest(directory: Path) -> dict[str, str]:
    """디렉터리 파일별 내용 해시(원천 무변경 확인용)."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.glob("*.yaml"))
    }


# ──────────────────────────────────────────────
# 1. 원천 파싱
# ──────────────────────────────────────────────

def test_gold_entries_use_declared_db_id():
    """골드셋 db_id는 파일명(gp)이 아니라 item 선언값(polestar_cm_gp)이다."""
    collected = _SEED.collect_gold_entries(verified_at=_FIXED_TS)
    assert set(collected) == {"polestar_cm_gp", "polestar_cm_yd", "polestar_b0"}
    assert sum(len(v) for v in collected.values()) == 26  # README 큐레이션 합계
    assert all(
        e["source"] == "gold" and e["verified_at"] == _FIXED_TS
        for entries in collected.values() for e in entries
    )


def test_gold_entries_preserve_smq():
    """gold_smq가 있는 항목은 중간표현을 함께 보존한다(트랙 S tool 재료)."""
    collected = _SEED.collect_gold_entries(verified_at=_FIXED_TS)
    with_smq = [
        e for entries in collected.values() for e in entries if e.get("smq")
    ]
    assert with_smq, "gold_smq 보유 항목이 하나도 없다"
    assert all("pattern" in e["smq"] for e in with_smq)


def test_profile_entries_use_filename_stem_as_db_id():
    """프로필 db_id는 파일명 stem이며, 예시 없는 프로필은 제외된다."""
    collected = _SEED.collect_profile_entries(verified_at=_FIXED_TS)
    assert set(collected) == {"polestar_cm_gp", "polestar_cm_yd", "polestar_b0"}
    assert "test_db" not in collected  # query_examples 미보유
    assert all(
        e["source"] == "profile_example"
        for entries in collected.values() for e in entries
    )


def test_collect_entries_merges_both_sources():
    """두 원천이 db_id별로 합쳐진다(실측 55건 = 골드 26 + 프로필 29)."""
    merged = _SEED.collect_entries(verified_at=_FIXED_TS)
    assert sum(len(v) for v in merged.values()) == 55
    sources = {e["source"] for entries in merged.values() for e in entries}
    assert sources == {"gold", "profile_example"}


def test_sources_are_read_only():
    """적재 수집은 원천 파일을 건드리지 않는다."""
    before = (_dir_digest(_GOLD_DIR), _dir_digest(_PROFILE_DIR))
    _SEED.collect_entries()
    assert (_dir_digest(_GOLD_DIR), _dir_digest(_PROFILE_DIR)) == before


# ──────────────────────────────────────────────
# 2. 병합·중복 제거
# ──────────────────────────────────────────────

def test_duplicate_pair_is_collapsed_keeping_gold(tmp_path: Path):
    """양쪽 원천에 같은 쌍이 있으면 하나로 접히고 골드셋 항목(smq)이 남는다."""
    import yaml

    gold_dir = tmp_path / "gold"
    profile_dir = tmp_path / "profiles"
    gold_dir.mkdir()
    profile_dir.mkdir()
    (gold_dir / "x.yaml").write_text(
        yaml.safe_dump({
            "items": [{
                "id": "x-1", "db_id": "db_x", "query": "서버 목록 조회",
                "gold_sql": _SELECT, "gold_smq": {"pattern": "A"},
            }]
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (profile_dir / "db_x.yaml").write_text(
        yaml.safe_dump({
            "query_examples": [
                {"question": "서버 목록 조회", "sql": _SELECT},
                {"question": "알람 건수 조회", "sql": "SELECT count(*) FROM a"},
            ]
        }, allow_unicode=True),
        encoding="utf-8",
    )

    merged = _SEED.collect_entries(
        gold_dir=gold_dir, profile_dir=profile_dir, verified_at=_FIXED_TS,
    )
    entries = merged["db_x"]
    assert len(entries) == 2
    dup = next(e for e in entries if e["query"] == "서버 목록 조회")
    assert dup["source"] == "gold" and dup["smq"] == {"pattern": "A"}


def test_incomplete_source_rows_are_skipped(tmp_path: Path):
    """질의·SQL이 빠진 원천 행은 조용히 건너뛴다(적재 실패로 만들지 않음)."""
    import yaml

    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "x.yaml").write_text(
        yaml.safe_dump({
            "items": [
                {"id": "x-1", "db_id": "db_x", "query": "질의만"},
                {"id": "x-2", "db_id": "", "query": "q", "gold_sql": _SELECT},
                {"id": "x-3", "db_id": "db_x", "query": "정상", "gold_sql": _SELECT},
            ]
        }, allow_unicode=True),
        encoding="utf-8",
    )
    collected = _SEED.collect_gold_entries(gold_dir, verified_at=_FIXED_TS)
    assert [e["query"] for e in collected["db_x"]] == ["정상"]


# ──────────────────────────────────────────────
# 3. CLI 동작
# ──────────────────────────────────────────────

async def test_load_dry_run_needs_no_redis(capsys):
    """--dry-run은 Redis 없이 파싱 결과만 출력하고 성공으로 끝난다."""
    rc = await _SEED.cmd_load("all", replace=False, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run]" in out and "polestar_cm_gp" in out


async def test_load_filters_by_db(capsys):
    """--db 인자로 대상 DB를 한정한다."""
    rc = await _SEED.cmd_load("polestar_b0", replace=False, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "polestar_b0" in out and "polestar_cm_gp" not in out


async def test_load_reports_redis_failure_loudly(monkeypatch, capsys):
    """Redis 미가용은 침묵 성공이 아니라 exit 1 + 사유 출력이다."""
    async def _boom():
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(_SEED, "_open_store", _boom)
    rc = await _SEED.cmd_load("all", replace=False, dry_run=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Redis에 연결할 수 없어" in err and "Connection refused" in err


async def test_load_writes_via_store(monkeypatch, capsys):
    """정상 경로는 저장소 save_entries로 db별 항목을 기록한다."""
    saved: dict[str, int] = {}

    class _Store:
        async def save_entries(self, db_id, entries, *, replace=False):
            saved[db_id] = len(list(entries))
            return saved[db_id]

        async def disconnect(self):
            return None

    async def _open():
        return _Store()

    monkeypatch.setattr(_SEED, "_open_store", _open)
    rc = await _SEED.cmd_load("polestar_b0", replace=True, dry_run=False)
    assert rc == 0
    assert saved == {"polestar_b0": 12}  # 골드 5 + 프로필 7
    assert "[ok] polestar_b0" in capsys.readouterr().out


def test_main_dispatches_load(capsys):
    """CLI 진입점이 load 서브커맨드를 실행한다."""
    assert _SEED.main(["load", "--dry-run", "--db", "polestar_b0"]) == 0
    assert "polestar_b0" in capsys.readouterr().out


def test_main_requires_subcommand():
    """서브커맨드 없이 호출하면 argparse가 거부한다."""
    with pytest.raises(SystemExit):
        _SEED.main([])
