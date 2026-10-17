"""`DBRegistrationService.config_snippets` 특성화 — 입력 조합 × 설정 조각 출력 골든.

plans/173 T8 · 166 I5.

plans/173 W2 T8 특성화 스냅샷 — Extract Function 전에 채록했다. 기대값은 「맞는 값」이 아니라
「지금 값」이다(D-337 ⑤). 리팩터 뒤 스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다.

입력 매트릭스(전 조합):

- 레지스트리(`_REGISTRIES`) — 조회 실패 · 항목 없음 · PostgreSQL(스키마·별칭·존) ·
  DB2(스키마·설명 없음) · 한글 식별자 허용 항목
- 등록 기록(`_REGISTRATIONS`) — 없음 · probe `mcp_type` · DB 설명 LLM 초안 · 둘 다
- 스냅샷(`_SNAPSHOTS`) — 없음 · 스키마 다수결 · 한글 컬럼(dict·list 컬럼) · 스키마 필드 없음
- 실행 환경(`_ENVS`) — 원격 · 로컬 샌드박스

출력: 반환 dict 전 필드(registry_yaml 원문 · local_sandbox · entry · notes)와 모듈 로그.
생성 시각(`_now_iso`)은 모듈 전역을 고정값으로 바꾼다. 외부 I/O 0(가짜 저장소·가짜 레지스트리).

스냅샷 (재)채록: `UPDATE_PLAN173_T8_SNAPSHOT=1 python -m pytest <이 파일> -q -p no:cacheprovider`
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.routing.registry import DBEntry
from src.schema_cache import db_registration_service as mod
from src.schema_cache.db_registration_service import DBRegistrationService

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan173_t8" / "config_snippets.json"
)
_UPDATE_ENV = "UPDATE_PLAN173_T8_SNAPSHOT"
SRC = "acme_src"


class _Registry:
    def __init__(self, entry: DBEntry | None) -> None:
        self._entry = entry

    def get(self, db_id: str) -> DBEntry | None:
        return self._entry if self._entry is not None and self._entry.db_id == db_id else None


class _Store:
    def __init__(self, registration: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
        self.registration, self.snapshot = registration, snapshot

    async def load_registration(self, source: str) -> dict[str, Any]:
        return self.registration

    async def load_snapshot(self, source: str) -> dict[str, Any] | None:
        return self.snapshot


def _raise() -> Any:
    raise RuntimeError("레지스트리 파일 없음")


_REGISTRIES: dict[str, Any] = {
    "fail": _raise,
    "absent": lambda: _Registry(None),
    "pg": lambda: _Registry(DBEntry(
        db_id=SRC, display_name="가상 시스템", description="현행 설명", aliases=("가상", "acme"),
        engine="postgresql", db_schema="public", zone="gongjon")),
    "db2_nodesc": lambda: _Registry(DBEntry(
        db_id=SRC, display_name="", description="", engine="db2", db_schema="")),
    "hangul_on": lambda: _Registry(DBEntry(
        db_id=SRC, display_name="한글", description="한글 DB", engine="mariadb",
        db_schema="APPDB", allow_hangul_identifiers=True)),
}

_REGISTRATIONS: dict[str, dict[str, Any]] = {
    "none": {},
    "probe": {"probe": {"detail": {"mcp_type": "db2"}}},
    "draft": {"db_description": {"detail": {"draft_text": "LLM이 쓴 초안 설명"}}},
    "both": {"probe": {"detail": {"mcp_type": "mariadb"}},
             "db_description": {"detail": {"draft_text": "초안: 자산 계약 DB"}}},
}

_SNAPSHOTS: dict[str, dict[str, Any] | None] = {
    "none": None,
    "schemas": {"snapshot": {"tables": {
        "a": {"schema": "S1", "columns": {"id": {}}},
        "b": {"schema": "S2", "columns": {"id": {}}},
        "c": {"schema": "S2", "columns": ["id"]},
        "d": "not-a-mapping",
    }}},
    "hangul": {"snapshot": {"tables": {
        "a": {"schema": "HS", "columns": {"자산명": {}, "id": {}}},
        "b": {"schema": "HS", "columns": ["등록일", "상태코드값", "x"]},
    }}},
    "no_schema": {"snapshot": {"tables": {"a": {"columns": {"id": {}}}}}},
}

_ENVS: dict[str, str] = {
    "remote": "http://mcp.internal:9099/sse",
    "local": "http://localhost:9099/sse",
}


def _run(reg_id: str, rec_id: str, snap_id: str, env_id: str,
         caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    config = SimpleNamespace(dbhub=SimpleNamespace(server_url=_ENVS[env_id]))
    service = DBRegistrationService(
        config, cache_mgr=object(), registry_getter=_REGISTRIES[reg_id],  # type: ignore[arg-type]
        store=_Store(_REGISTRATIONS[rec_id], _SNAPSHOTS[snap_id]),  # type: ignore[arg-type]
    )
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(service.config_snippets(SRC))
    return {
        "result": result,
        "logs": [[r.name, r.levelname, r.getMessage()] for r in caplog.records
                 if r.name.startswith("src.")],
    }


def _collect(monkeypatch: pytest.MonkeyPatch,
             caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    monkeypatch.setattr(mod, "_now_iso", lambda: "2026-10-16T09:00:00+09:00")
    out: dict[str, Any] = {}
    for reg_id in _REGISTRIES:
        for rec_id in _REGISTRATIONS:
            for snap_id in _SNAPSHOTS:
                for env_id in _ENVS:
                    out[f"{reg_id}|{rec_id}|{snap_id}|{env_id}"] = _run(
                        reg_id, rec_id, snap_id, env_id, caplog)
    return out


def test_config_snippets_matches_snapshot(monkeypatch, caplog):
    actual = json.loads(json.dumps(_collect(monkeypatch, caplog), ensure_ascii=False))
    if os.environ.get(_UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = ",\n".join(
            json.dumps(k, ensure_ascii=False) + ": "
            + json.dumps(actual[k], ensure_ascii=False, sort_keys=True)
            for k in sorted(actual)
        )
        SNAPSHOT_PATH.write_text("{\n" + lines + "\n}\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected)
    for cid in expected:
        assert actual[cid] == expected[cid], cid


def test_invalid_source_raises_before_store_access():
    config = SimpleNamespace(dbhub=SimpleNamespace(server_url=_ENVS["remote"]))
    service = DBRegistrationService(
        config, cache_mgr=object(), registry_getter=_raise,  # type: ignore[arg-type]
        store=_Store({}, None),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        asyncio.run(service.config_snippets("../etc"))
