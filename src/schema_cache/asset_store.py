"""생성 자산 파일 저장소 — 유사어 시드 · DB 전용 프롬프트 섹션 (D-294 · plans/133 W3).

자산 자동 생성이 승인되면 프로필 밖 파일 두 종류를 앱이 직접 쓴다(D-294 — D-227 ⑤ R11 개정):

- ``seeds``: ``config/synonym_seeds/{db_id}.yaml``
- ``prompt_template``: ``config/knowledge/{db_id}/prompt_template.yaml``
- 버전: ``<backup_root>/{db_id}/assets/{kind}/v{N}.yaml``

쓰는 곳은 이 저장소 한 곳뿐이다(프로필은 종전대로 `StructureStore.apply_profile`). 버전 규칙은
프로필과 같다 — 현행 파일이 있는데 버전이 0건이면 `baseline` v0, 최신 버전 원문과 다르면
`external_change`로 원문을 보관하고, 새 버전을 기록한 뒤 현행 파일을 원자 교체한다. 되돌리기는
버전 원문을 바이트 그대로 쓴다. 로컬 샌드박스에서는 머리말과 `environment: local_sandbox`를 붙인다
(git 추적 게이트 대상).

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.profile_merge import LOCAL_SANDBOX_ENVIRONMENT
from src.schema_cache.structure_store import (
    _atomic_write_text,
    _dump_yaml_exact,
    _now_iso,
    _sha256_text,
    validate_db_id,
)

logger = logging.getLogger(__name__)

#: 자산 종류 → 현행 파일 상대 경로(`{db_id}` 자리표시)
ASSET_PATHS: Mapping[str, str] = {
    "seeds": "config/synonym_seeds/{db_id}.yaml",
    "prompt_template": "config/knowledge/{db_id}/prompt_template.yaml",
}
KIND_BASELINE = "baseline"
KIND_EXTERNAL_CHANGE = "external_change"
KIND_APPROVED = "approved"
KIND_ROLLBACK = "rollback"
_VERSION_FILE_RE = re.compile(r"v(\d+)\.yaml")
_ARCHIVE_REASONS: Mapping[str, str] = {
    KIND_BASELINE: "적용 전 현행 파일 원문 보관",
    KIND_EXTERNAL_CHANGE: "버전 밖에서 바뀐 현행 파일 원문 보관",
}


class AssetFileStore:
    """생성 자산 파일(시드·프롬프트 섹션)의 적용 · 버전 · 되돌리기."""

    def __init__(self, repo_root: Path, backup_root: Path) -> None:
        """저장소를 만든다.

        Args:
            repo_root: 현행 파일 기준 경로(작업 디렉터리 — `config/`가 있는 곳)
            backup_root: 버전 보관 루트(`StructureStore.backup_root`와 같은 곳)
        """
        self._repo_root = Path(repo_root)
        self._backup_root = Path(backup_root)
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @staticmethod
    def _check_kind(kind: str) -> str:
        if kind not in ASSET_PATHS:
            raise ValueError(f"모르는 자산 종류: {kind!r}")
        return kind

    def path(self, db_id: str, kind: str) -> Path:
        """현행 파일 경로."""
        return self._repo_root / ASSET_PATHS[self._check_kind(kind)].format(
            db_id=validate_db_id(db_id)
        )

    def _versions_dir(self, db_id: str, kind: str) -> Path:
        return self._backup_root / validate_db_id(db_id) / "assets" / self._check_kind(kind)

    def _lock(self, db_id: str, kind: str) -> asyncio.Lock:
        return self._locks.setdefault((db_id, kind), asyncio.Lock())

    def _version_numbers(self, db_id: str, kind: str) -> list[int]:
        directory = self._versions_dir(db_id, kind)
        if not directory.is_dir():
            return []
        return sorted(
            int(m.group(1)) for p in directory.iterdir()
            if (m := _VERSION_FILE_RE.fullmatch(p.name))
        )

    def _read_version(self, db_id: str, kind: str, ver: int) -> dict[str, Any] | None:
        path = self._versions_dir(db_id, kind) / f"v{ver}.yaml"
        try:
            data = yaml.safe_load(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            logger.warning("자산 버전 읽기 실패 (%s): %s", path, e)
            return None
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            return None
        return {**data, "ver": ver}

    def read_current(self, db_id: str, kind: str) -> dict[str, Any] | None:
        """현행 파일 원문·파싱값·SHA-256(없으면 None · 파싱 실패면 data None)."""
        path = self.path(db_id, kind)
        if not path.is_file():
            return None
        content = path.read_bytes().decode("utf-8")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            data = None
        return {"content": content, "data": data if isinstance(data, dict) else None,
                "sha256": _sha256_text(content), "path": str(path)}

    async def list_versions(self, db_id: str, kind: str) -> list[dict[str, Any]]:
        """버전 목록(원문 제외 · ver 오름차순)."""
        out: list[dict[str, Any]] = []
        for ver in self._version_numbers(db_id, kind):
            entry = self._read_version(db_id, kind, ver)
            if entry is not None:
                out.append({k: v for k, v in entry.items() if k != "content"})
        return out

    async def apply(
        self,
        db_id: str,
        kind: str,
        data: Mapping[str, Any] | None,
        *,
        by: str | None,
        reason: str,
        env: str,
        draft_id: str | None,
        local_sandbox: bool,
        header: str,
        content: str | None = None,
        rolled_back_from: int | None = None,
    ) -> dict[str, Any]:
        """자산 파일을 쓰고 새 버전 항목을 돌려준다.

        Args:
            data: 쓸 값(``content``가 있으면 쓰지 않는다) — 로컬 샌드박스면 `environment`를 덧붙인다
            header: 파일 머리 주석(여러 줄 가능 · 각 줄 `#`로 시작해야 한다)
            content: 바이트 그대로 쓸 원문(되돌리기)

        Raises:
            ValueError: db_id·kind 형식 · 머리말이 주석이 아님 · content가 YAML 매핑이 아님
        """
        validate_db_id(db_id)
        self._check_kind(kind)
        if content is None:
            lines = [line for line in header.splitlines() if line.strip()]
            if any(not line.startswith("#") for line in lines):
                raise ValueError("머리말은 줄마다 '#'로 시작해야 합니다")
            body = dict(data or {})
            if local_sandbox:
                body["environment"] = LOCAL_SANDBOX_ENVIRONMENT
            else:
                body.pop("environment", None)
            new_content = "\n".join(lines) + ("\n" if lines else "") + _dump_yaml_exact(body)
            kind_label = KIND_APPROVED
        else:
            if not isinstance(yaml.safe_load(content), dict):
                raise ValueError("content가 YAML 매핑이 아닙니다")
            new_content = content
            kind_label = KIND_ROLLBACK

        async with self._lock(db_id, kind):
            current = self.read_current(db_id, kind)
            numbers = self._version_numbers(db_id, kind)
            created_at = _now_iso()
            if current is not None:
                archive: str | None = None
                if not numbers:
                    archive = KIND_BASELINE
                else:
                    latest = self._read_version(db_id, kind, numbers[-1])
                    if latest is None or _sha256_text(latest["content"]) != current["sha256"]:
                        archive = KIND_EXTERNAL_CHANGE
                if archive is not None:
                    archive_ver = numbers[-1] + 1 if numbers else 0
                    self._write(db_id, kind, {
                        "ver": archive_ver, "kind": archive, "content": current["content"],
                        "content_sha256": current["sha256"], "created_at": created_at, "by": by,
                        "reason": _ARCHIVE_REASONS[archive], "env": env, "draft_id": None,
                        "rolled_back_from": None,
                    })
                    numbers.append(archive_ver)
            new_ver = numbers[-1] + 1 if numbers else 1
            entry = {
                "ver": new_ver, "kind": kind_label, "content": new_content,
                "content_sha256": _sha256_text(new_content), "created_at": created_at, "by": by,
                "reason": reason, "env": env, "draft_id": draft_id,
                "rolled_back_from": rolled_back_from,
            }
            self._write(db_id, kind, entry)
            path = self.path(db_id, kind)
            _atomic_write_text(path, new_content)
        logger.info("생성 자산 적용: db_id=%s, kind=%s, ver=%d, by=%s, path=%s",
                    db_id, kind, new_ver, by, path)
        return {k: v for k, v in entry.items() if k != "content"}

    async def rollback(
        self, db_id: str, kind: str, ver: int, *, by: str | None, reason: str, env: str
    ) -> dict[str, Any]:
        """버전 vK 원문을 바이트 그대로 현행 파일에 되돌린다(새 `rollback` 버전).

        Raises:
            ValueError: 해당 버전이 없거나 읽을 수 없음
        """
        target = (
            self._read_version(db_id, kind, ver)
            if ver in self._version_numbers(db_id, kind) else None
        )
        if target is None:
            raise ValueError(f"되돌릴 자산 버전이 없습니다: db_id={db_id}, kind={kind}, ver={ver}")
        return await self.apply(
            db_id, kind, None, by=by, reason=reason, env=env, draft_id=None,
            local_sandbox=False, header="", content=target["content"], rolled_back_from=ver,
        )

    def _write(self, db_id: str, kind: str, entry: dict[str, Any]) -> None:
        path = self._versions_dir(db_id, kind) / f"v{entry['ver']}.yaml"
        _atomic_write_text(path, _dump_yaml_exact(entry))
