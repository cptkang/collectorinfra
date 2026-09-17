"""관리자 DB 구조 자산 저장소 (plans/104 §3.3 · A-6 · Wave 2 계약 §3 · G-2 · G-4).

관리자 흐름(점검·분석·승인·등록)이 만드는 자산을 Redis에 두고, 구조 프로필은 파일이 정본이다.

구조 프로필(G-2):
  현행본   `{profiles_dir}/{db_id}.yaml` (기본 `config/db_profiles` — 수동 프로필과 같은
           경로·형식, 소비처 수정 0). 쓰기는 `apply_profile`·`rollback` 한 곳뿐이며 호출자는
           관리자 서비스뿐이다.
  버전 이력 `{backup_root}/{db_id}/versions/v{N}.yaml` (기본 `.cache/structure`) — 항목
           `{ver, kind, content, content_sha256, profile, created_at, by, reason, env, draft_id,
           rolled_back_from, field_diff, comment_lines_dropped}`. `content`는 파일 원문(주석
           포함)이라 되돌리기가 바이트 그대로 복원한다. 배포로 현행본이 덮여도 버전에서 복원한다.
  Redis    `schema:{db_id}:structure_meta` = 현행 프로필에서 `source`만 뺀 dict(적용본 캐시).
           버전 목록 Redis 캐시는 두지 않는다(파일이 정본). 승인 버전(approved·rollback) 없이
           이 키에만 있는 값(레거시 분석본 · 수동 프로필 사본)은 적용본이 아니다.

Redis 키 (전부 JSON String):
  schema:{db_id}:schema_snapshot     -> 마지막 스냅샷 기록 {snapshot, hash, taken_at, env, by}
  schema:{db_id}:check_result        -> 마지막 변경 점검 결과
  schema:{db_id}:structure_drafts    -> 구조 초안 목록
  schema:{db_id}:registration        -> 단계별 등록 상태 {step: {...}}
  schema:{db_id}:description_drafts  -> 컬럼 설명·유사어 초안 목록
  schema:{db_id}:structure_meta      -> 적용본 캐시(기존 키 — 런타임 소비처 무변경)

설명 백업(B-6): `{backup_root}/{db_id}/descriptions.yaml`.

스냅샷·점검 결과·초안·등록 상태 쓰기 API는 Redis 미연결이면 `StructureStoreUnavailable`을 던진다.
읽기 API는 미연결이면 빈 값을 돌려준다. 프로필 적용·되돌리기는 Redis 미연결이어도 파일을 쓰고
WARNING을 남긴다(파일이 정본). 같은 db_id의 쓰기는 프로세스 안에서 직렬화한다(단일 프로세스 서버 —
동시 승인이 같은 ver를 만들지 않게).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.profile_merge import profile_field_diff
from src.schema_cache.redis_cache import RedisSchemaCache

logger = logging.getLogger(__name__)

_DB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VERSION_FILE_RE = re.compile(r"^v(\d+)\.yaml$")

DEFAULT_PROFILES_DIR = Path("config/db_profiles")

SNAPSHOT_SUFFIX = "schema_snapshot"
CHECK_RESULT_SUFFIX = "check_result"
DRAFTS_SUFFIX = "structure_drafts"
REGISTRATION_SUFFIX = "registration"
DESCRIPTION_DRAFTS_SUFFIX = "description_drafts"
APPLIED_SUFFIX = "structure_meta"
VALUE_INDEX_SUFFIX = "column_value_index"

# 버전 항목 kind
KIND_BASELINE = "baseline"  # 적용 직전 현행 파일 자동 보관(첫 보관 = v0)
KIND_EXTERNAL_CHANGE = "external_change"  # 최신 버전과 현행 파일이 달라 자동 보관(배포·수동 편집)
KIND_APPROVED = "approved"
KIND_ROLLBACK = "rollback"
APPLY_KINDS: tuple[str, ...] = (KIND_APPROVED, KIND_ROLLBACK)

_ARCHIVE_REASONS = {
    KIND_BASELINE: "적용 직전 현행 파일 자동 보관(첫 보관)",
    KIND_EXTERNAL_CHANGE: "최신 버전과 현행 파일이 달라 자동 보관(배포·수동 편집)",
}


class StructureStoreUnavailable(RuntimeError):  # noqa: N818 - 계약 확정 이름
    """Redis 미연결로 관리자 자산을 쓸 수 없다."""


def validate_db_id(db_id: str) -> str:
    """db_id가 키·파일 경로에 안전한 형식(`^[A-Za-z0-9_-]+$`)인지 검증한다.

    Raises:
        ValueError: 형식이 맞지 않을 때
    """
    if not isinstance(db_id, str) or not _DB_ID_RE.fullmatch(db_id):
        raise ValueError(f"허용되지 않는 db_id 형식: {db_id!r}")
    return db_id


def _now_iso() -> str:
    """현재 시각(로컬 시간대 오프셋 포함 ISO 8601, 초 단위)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    """UTF-8 바이트의 SHA-256 16진 문자열."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def count_comment_lines(text: str | None) -> int:
    """전체 줄 주석(공백 뒤 `#`로 시작하는 줄) 수. PyYAML 덤프가 잃는 주석의 근사치다."""
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def _atomic_write_text(path: Path, text: str) -> None:
    """같은 디렉터리 임시 파일에 UTF-8 바이트로 쓴 뒤 `os.replace`로 교체한다(줄바꿈 변환 없음)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_bytes(text.encode("utf-8"))
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _dump_yaml_exact(data: dict[str, Any]) -> str:
    """다시 읽으면 같은 값이 되는 YAML 텍스트를 만든다.

    사람이 읽기 좋은 `allow_unicode=True`를 먼저 쓰고, 일부 유니코드 줄 구분 문자(NEL 등)가 왕복에서
    바뀌면 이스케이프 표기(`allow_unicode=False`)로 다시 만든다. 그래도 다르면 ValueError — 값(버전
    원문 포함)을 그대로 되읽지 못하는 기록은 남기지 않는다.
    """
    for allow_unicode in (True, False):
        text = yaml.safe_dump(data, allow_unicode=allow_unicode, sort_keys=False)
        if yaml.safe_load(text) == data:
            return str(text)
    raise ValueError("YAML 왕복에서 값이 바뀌어 버전을 기록할 수 없습니다")


class StructureStore:
    """관리자 DB 구조 자산(스냅샷·점검·초안·등록 상태 — Redis)과 구조 프로필(파일 정본) 저장소."""

    def __init__(
        self,
        redis_cache: RedisSchemaCache | None,
        backup_root: Path,
        profiles_dir: Path = DEFAULT_PROFILES_DIR,
    ) -> None:
        """저장소를 만든다.

        Args:
            redis_cache: Redis 스키마 캐시(None이면 Redis 자산 읽기는 빈 값 · 프로필 파일은
                그대로 동작)
            backup_root: 버전 이력·설명 백업 루트(예: `.cache/structure`)
            profiles_dir: 현행 구조 프로필 디렉터리(기본 `config/db_profiles` — 테스트는 tmp 주입)
        """
        self._redis_cache = redis_cache
        self._backup_root = Path(backup_root)
        self._profiles_dir = Path(profiles_dir)
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def redis_cache(self) -> RedisSchemaCache | None:
        """이 저장소가 쓰는 Redis 스키마 캐시."""
        return self._redis_cache

    @property
    def backup_root(self) -> Path:
        """버전 이력·설명 백업 루트."""
        return self._backup_root

    @property
    def profiles_dir(self) -> Path:
        """현행 구조 프로필 디렉터리."""
        return self._profiles_dir

    @property
    def available(self) -> bool:
        """마지막으로 확인된 Redis 연결 상태. 각 API는 호출 시 연결을 다시 시도한다."""
        return self._redis_cache is not None and bool(self._redis_cache.connected)

    # --- 내부 ---

    @staticmethod
    def _key(db_id: str, suffix: str) -> str:
        return f"schema:{db_id}:{suffix}"

    async def _ready(self) -> bool:
        if self._redis_cache is None:
            return False
        return bool(await self._redis_cache.ensure_connected())

    async def _require(self) -> RedisSchemaCache:
        if self._redis_cache is None or not await self._ready():
            raise StructureStoreUnavailable(
                "Redis에 연결할 수 없어 관리자 DB 구조 자산을 저장할 수 없습니다"
            )
        return self._redis_cache

    def _lock(self, db_id: str) -> asyncio.Lock:
        lock = self._locks.get(db_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[db_id] = lock
        return lock

    async def _load_list(self, db_id: str, suffix: str) -> list[dict[str, Any]]:
        if not await self._ready():
            return []
        assert self._redis_cache is not None
        raw = await self._redis_cache.get_json(self._key(db_id, suffix))
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    async def _load_dict(self, db_id: str, suffix: str) -> dict[str, Any] | None:
        if not await self._ready():
            return None
        assert self._redis_cache is not None
        raw = await self._redis_cache.get_json(self._key(db_id, suffix))
        return raw if isinstance(raw, dict) else None

    async def _save_value(self, db_id: str, suffix: str, value: Any) -> None:
        redis_cache = await self._require()
        await redis_cache.set_json(self._key(db_id, suffix), value)

    # --- 스냅샷·점검 결과 ---

    async def save_snapshot(self, db_id: str, record: dict[str, Any]) -> None:
        """마지막 스키마 스냅샷 기록을 저장한다(`{snapshot, hash, taken_at, env, by}`)."""
        validate_db_id(db_id)
        await self._save_value(db_id, SNAPSHOT_SUFFIX, record)

    async def load_snapshot(self, db_id: str) -> dict[str, Any] | None:
        """마지막 스키마 스냅샷 기록(없으면 None)."""
        validate_db_id(db_id)
        return await self._load_dict(db_id, SNAPSHOT_SUFFIX)

    async def save_check_result(self, db_id: str, result: dict[str, Any]) -> None:
        """마지막 변경 점검 결과를 저장한다."""
        validate_db_id(db_id)
        await self._save_value(db_id, CHECK_RESULT_SUFFIX, result)

    async def load_check_result(self, db_id: str) -> dict[str, Any] | None:
        """마지막 변경 점검 결과(없으면 None)."""
        validate_db_id(db_id)
        return await self._load_dict(db_id, CHECK_RESULT_SUFFIX)

    # --- 초안 목록 공통 ---

    async def _add_item(self, db_id: str, suffix: str, item: dict[str, Any]) -> dict[str, Any]:
        redis_cache = await self._require()
        entry = {
            **item,
            "draft_id": uuid.uuid4().hex[:12],
            "created_at": _now_iso(),
            "status": "pending",
        }
        async with self._lock(db_id):
            items = await self._load_list(db_id, suffix)
            items.append(entry)
            await redis_cache.set_json(self._key(db_id, suffix), items)
        return entry

    async def _get_item(self, db_id: str, suffix: str, draft_id: str) -> dict[str, Any] | None:
        for item in await self._load_list(db_id, suffix):
            if item.get("draft_id") == draft_id:
                return item
        return None

    async def _update_item(
        self, db_id: str, suffix: str, draft_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        redis_cache = await self._require()
        async with self._lock(db_id):
            items = await self._load_list(db_id, suffix)
            for idx, item in enumerate(items):
                if item.get("draft_id") == draft_id:
                    updated = {**item, **fields}
                    items[idx] = updated
                    await redis_cache.set_json(self._key(db_id, suffix), items)
                    return updated
        return None

    # --- 구조 초안 ---

    async def add_draft(self, db_id: str, draft: dict[str, Any]) -> dict[str, Any]:
        """구조 초안을 추가한다. `draft_id`·`created_at`·`status="pending"`을 채워 반환한다."""
        validate_db_id(db_id)
        return await self._add_item(db_id, DRAFTS_SUFFIX, draft)

    async def list_drafts(self, db_id: str) -> list[dict[str, Any]]:
        """구조 초안 목록(최신순)."""
        validate_db_id(db_id)
        return list(reversed(await self._load_list(db_id, DRAFTS_SUFFIX)))

    async def get_draft(self, db_id: str, draft_id: str) -> dict[str, Any] | None:
        """구조 초안 1건(없으면 None)."""
        validate_db_id(db_id)
        return await self._get_item(db_id, DRAFTS_SUFFIX, draft_id)

    async def update_draft(self, db_id: str, draft_id: str, **fields: Any) -> dict[str, Any] | None:
        """구조 초안 필드를 갱신하고 갱신본을 반환한다(없으면 None)."""
        validate_db_id(db_id)
        return await self._update_item(db_id, DRAFTS_SUFFIX, draft_id, fields)

    # --- 구조 프로필(현행본 · 버전 이력) ---

    def _profile_path(self, db_id: str) -> Path:
        return self._profiles_dir / f"{validate_db_id(db_id)}.yaml"

    def _versions_dir(self, db_id: str) -> Path:
        return self._backup_root / validate_db_id(db_id) / "versions"

    def _version_numbers(self, db_id: str) -> list[int]:
        """버전 파일 번호(오름차순). 읽을 수 없는 파일도 번호는 점유한다(덮어쓰지 않게)."""
        directory = self._versions_dir(db_id)
        if not directory.is_dir():
            return []
        numbers = []
        for path in directory.iterdir():
            match = _VERSION_FILE_RE.fullmatch(path.name)
            if match:
                numbers.append(int(match.group(1)))
        return sorted(numbers)

    def _read_version(self, db_id: str, ver: int) -> dict[str, Any] | None:
        """버전 항목 1건. 읽을 수 없거나 `content`가 없으면 WARNING 후 None."""
        path = self._versions_dir(db_id) / f"v{ver}.yaml"
        try:
            data = yaml.safe_load(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            logger.warning("구조 프로필 버전 읽기 실패 (%s): %s", path, e)
            return None
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            logger.warning("구조 프로필 버전 형식 오류 (%s): content 없음", path)
            return None
        return {**data, "ver": ver}

    def _latest_version(self, db_id: str) -> dict[str, Any] | None:
        """읽을 수 있는 가장 큰 ver의 버전 항목(없으면 None)."""
        for ver in reversed(self._version_numbers(db_id)):
            entry = self._read_version(db_id, ver)
            if entry is not None:
                return entry
        return None

    def _write_version(self, db_id: str, entry: dict[str, Any]) -> Path:
        path = self._versions_dir(db_id) / f"v{entry['ver']}.yaml"
        _atomic_write_text(path, _dump_yaml_exact(entry))
        return path

    def read_current_profile(self, db_id: str) -> dict[str, Any] | None:
        """현행 구조 프로필 파일을 읽는다(없으면 None).

        Returns:
            `{"exists": True, "content": 원문, "sha256": 원문 바이트 SHA-256, "profile": dict|None
            (YAML 매핑이 아니거나 파싱 실패면 None — WARNING), "source", "environment"}`
        """
        path = self._profile_path(db_id)
        if not path.is_file():
            return None
        content = path.read_bytes().decode("utf-8")
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as e:
            logger.warning("구조 프로필 YAML 파싱 실패 (%s): %s", path, e)
            parsed = None
        profile = parsed if isinstance(parsed, dict) else None
        return {
            "exists": True,
            "content": content,
            "sha256": _sha256_text(content),
            "profile": profile,
            "source": profile.get("source") if profile else None,
            "environment": profile.get("environment") if profile else None,
        }

    async def list_versions(self, db_id: str) -> list[dict[str, Any]]:
        """버전 이력(ver 오름차순 · 파일 정본). 읽을 수 없는 버전 파일은 WARNING 후 건너뛴다.

        항목: `{ver, kind, content, content_sha256, profile, created_at, by, reason, env, draft_id,
        rolled_back_from, field_diff, comment_lines_dropped}`
        """
        validate_db_id(db_id)
        entries = []
        for ver in self._version_numbers(db_id):
            entry = self._read_version(db_id, ver)
            if entry is not None:
                entries.append(entry)
        return entries

    def has_versions(self, db_id: str) -> bool:
        """읽을 수 있는 버전이 1개 이상 있는지."""
        return self._latest_version(db_id) is not None

    def latest_applied_version(self, db_id: str) -> dict[str, Any] | None:
        """읽을 수 있는 승인 버전(kind ∈ `APPLY_KINDS` = approved·rollback) 중 ver가 가장 큰 항목.

        `baseline`·`external_change`는 적용 직전 현행 파일을 보관한 기록일 뿐이라 승인 버전이
        아니다 — 그것만 있으면 None.
        """
        for ver in reversed(self._version_numbers(db_id)):
            entry = self._read_version(db_id, ver)
            if entry is not None and entry.get("kind") in APPLY_KINDS:
                return entry
        return None

    def has_approved_version(self, db_id: str) -> bool:
        """읽을 수 있는 승인 버전(approved·rollback)이 1개 이상 있는지."""
        return self.latest_applied_version(db_id) is not None

    def profile_state(self, db_id: str) -> dict[str, Any]:
        """현행 프로필과 최신 버전의 요약.

        Returns:
            `{"exists", "source", "environment", "sha256", "latest_ver", "latest_kind", "drift"}` —
            `drift`는 버전이 있을 때만 판정한다(현행 원문 SHA-256 ≠ 최신 버전 원문 SHA-256 · 현행
            파일이 없어도 True). 버전이 없으면 False.
        """
        current = self.read_current_profile(db_id)
        latest = self._latest_version(db_id)
        sha = current["sha256"] if current else None
        return {
            "exists": current is not None,
            "source": current["source"] if current else None,
            "environment": current["environment"] if current else None,
            "sha256": sha,
            "latest_ver": latest["ver"] if latest else None,
            "latest_kind": latest.get("kind") if latest else None,
            "drift": latest is not None and sha != _sha256_text(latest["content"]),
        }

    async def _cache_applied(self, db_id: str, profile: dict[str, Any]) -> None:
        """적용본 캐시(`structure_meta` = `source` 제외 dict)를 갱신하고 값 인덱스를 지운다.

        Redis 미연결·명령 실패는 WARNING 후 계속한다(파일이 정본).
        """
        if not await self._ready():
            logger.warning(
                "Redis 미연결 — 구조 적용본 캐시 갱신 생략 (db_id=%s, 파일이 정본)", db_id
            )
            return
        assert self._redis_cache is not None
        meta = {k: v for k, v in profile.items() if k != "source"}
        try:
            await self._redis_cache.set_json(self._key(db_id, APPLIED_SUFFIX), meta)
            await self._redis_cache.delete_keys(self._key(db_id, VALUE_INDEX_SUFFIX))
        except Exception as e:
            logger.warning(
                "구조 적용본 캐시 갱신 실패 (db_id=%s, 파일이 정본): %s", db_id, e
            )

    def _new_entry(
        self,
        *,
        ver: int,
        kind: str,
        content: str,
        profile: dict[str, Any] | None,
        created_at: str,
        by: str | None,
        reason: str,
        env: str,
        draft_id: str | None,
        rolled_back_from: int | None,
        field_diff: list[dict[str, Any]],
        comment_lines_dropped: int,
    ) -> dict[str, Any]:
        return {
            "ver": ver,
            "kind": kind,
            "content": content,
            "content_sha256": _sha256_text(content),
            "profile": profile,
            "created_at": created_at,
            "by": by,
            "reason": reason,
            "env": env,
            "draft_id": draft_id,
            "rolled_back_from": rolled_back_from,
            "field_diff": field_diff,
            "comment_lines_dropped": comment_lines_dropped,
        }

    async def apply_profile(
        self,
        db_id: str,
        merged_profile: dict[str, Any],
        *,
        by: str | None,
        reason: str,
        env: str,
        draft_id: str | None,
        field_diff: list[dict[str, Any]],
        kind: str = KIND_APPROVED,
        rolled_back_from: int | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """구조 프로필을 현행 파일에 적용하고 새 버전 항목을 반환한다.

        순서:
          ① 현행 파일이 있고 (버전 파일 0건 → `baseline` v0) 또는 (최신 버전 원문과 SHA-256이 다름 →
             `external_change` 최댓값+1)이면 현행 원문을 보관한다.
          ② 새 버전(최댓값+1 · 버전 0건이고 보관도 없으면 v1)을 기록한다. `content`가 없으면
             헤더 주석 1줄 + `merged_profile` YAML 덤프, 있으면 그 원문을 바이트 그대로 쓴다
             (되돌리기).
          ③ 현행 파일을 원자 교체한다.
          ④ Redis 적용본 캐시 갱신·값 인덱스 삭제(미연결·실패는 WARNING 후 계속 — 파일이 정본).

        `comment_lines_dropped`: `content`가 없으면(덤프) 직전 원문의 주석 줄 수, 있으면 직전
        원문 대비 줄어든 주석 줄 수(음수는 0). 승인 화면의 주석 소실 경고용이다.

        Args:
            db_id: DB 식별자
            merged_profile: 적용할 프로필 dict(`content`가 있으면 쓰지 않고 `content`를 파싱한다)
            by: 적용자
            reason: 사유
            env: 적용 환경(MCP 서버 URL)
            draft_id: 구조 초안 ID(되돌리기면 None)
            field_diff: 필드별 diff(`profile_field_diff` 결과)
            kind: `approved` | `rollback`
            rolled_back_from: 되돌린 원본 ver
            content: 파일에 그대로 쓸 원문(되돌리기)

        Returns:
            새 버전 항목

        Raises:
            ValueError: db_id 형식 · kind · `content`가 YAML 매핑이 아님
            TypeError: `content` 없이 `merged_profile`이 dict가 아님
            OSError: 버전·프로필 파일 쓰기 실패(프로필 쓰기 실패는 ERROR 로그 — 버전은 남아
                drift로 보인다)
        """
        validate_db_id(db_id)
        if kind not in APPLY_KINDS:
            raise ValueError(f"허용되지 않는 적용 kind: {kind!r}")
        if content is None:
            if not isinstance(merged_profile, dict):
                raise TypeError("merged_profile은 dict여야 합니다")
            profile = merged_profile
        else:
            parsed = yaml.safe_load(content)
            if not isinstance(parsed, dict):
                raise ValueError("content가 YAML 매핑이 아닙니다")
            profile = parsed

        async with self._lock(db_id):
            current = self.read_current_profile(db_id)
            numbers = self._version_numbers(db_id)
            created_at = _now_iso()

            if current is not None:
                archive_kind: str | None = None
                if not numbers:
                    archive_kind = KIND_BASELINE
                else:
                    latest = self._latest_version(db_id)
                    if latest is None or _sha256_text(latest["content"]) != current["sha256"]:
                        archive_kind = KIND_EXTERNAL_CHANGE
                if archive_kind is not None:
                    archive_ver = numbers[-1] + 1 if numbers else 0
                    self._write_version(db_id, self._new_entry(
                        ver=archive_ver, kind=archive_kind, content=current["content"],
                        profile=current["profile"], created_at=created_at, by=by,
                        reason=_ARCHIVE_REASONS[archive_kind], env=env, draft_id=None,
                        rolled_back_from=None, field_diff=[], comment_lines_dropped=0,
                    ))
                    numbers.append(archive_ver)
                    logger.info(
                        "구조 프로필 현행 파일 보관: db_id=%s, ver=%d, kind=%s",
                        db_id, archive_ver, archive_kind,
                    )

            new_ver = numbers[-1] + 1 if numbers else 1
            previous = current["content"] if current else None
            if content is None:
                # 헤더 값의 줄바꿈이 주석 밖 YAML로 새지 않게 공백 1개로 접는다
                header_by = " ".join(str(by or "-").split())
                header_env = " ".join(str(env).split())
                header = (
                    f"# plans/104 관리자 승인 적용 v{new_ver} · {created_at} · "
                    f"{header_by} · env={header_env}\n"
                )
                new_content = header + _dump_yaml_exact(profile)
                dropped = count_comment_lines(previous)
            else:
                new_content = content
                dropped = max(0, count_comment_lines(previous) - count_comment_lines(content))

            entry = self._new_entry(
                ver=new_ver, kind=kind, content=new_content, profile=profile,
                created_at=created_at, by=by, reason=reason, env=env, draft_id=draft_id,
                rolled_back_from=rolled_back_from, field_diff=field_diff,
                comment_lines_dropped=dropped,
            )
            self._write_version(db_id, entry)
            profile_path = self._profile_path(db_id)
            try:
                _atomic_write_text(profile_path, new_content)
            except OSError as e:
                logger.error(
                    "구조 프로필 v%d 버전은 기록했으나 현행 파일 쓰기 실패 (%s): %s",
                    new_ver, profile_path, e,
                )
                raise
            await self._cache_applied(db_id, profile)

        logger.info(
            "구조 프로필 적용: db_id=%s, ver=%d, kind=%s, by=%s, path=%s",
            db_id, new_ver, kind, by, profile_path,
        )
        return entry

    async def rollback(
        self, db_id: str, ver: int, *, by: str | None, reason: str, env: str
    ) -> dict[str, Any]:
        """버전 vK의 원문을 바이트 그대로 현행 파일에 되돌린다(새 `rollback` 버전).

        Raises:
            ValueError: 해당 ver가 없거나 읽을 수 없음 · 원문이 YAML 매핑이 아님
        """
        validate_db_id(db_id)
        target = (
            self._read_version(db_id, ver) if ver in self._version_numbers(db_id) else None
        )
        if target is None:
            raise ValueError(f"되돌릴 버전이 없습니다: db_id={db_id}, ver={ver}")
        target_profile = yaml.safe_load(target["content"])
        if not isinstance(target_profile, dict):
            raise ValueError(f"v{ver} 원문이 YAML 매핑이 아니라 되돌릴 수 없습니다")
        current = self.read_current_profile(db_id)
        field_diff = profile_field_diff(current["profile"] if current else None, target_profile)
        return await self.apply_profile(
            db_id, target_profile, by=by, reason=reason, env=env, draft_id=None,
            field_diff=field_diff, kind=KIND_ROLLBACK, rolled_back_from=ver,
            content=target["content"],
        )

    async def restore_applied(self, db_id: str) -> dict[str, Any] | None:
        """최신 승인 버전 프로필로 Redis 적용본 캐시를 채우고 적용본(`source` 제외 dict)을 반환한다.

        파일은 쓰지 않는다(질의 경로 ② — 배포로 현행 파일이 덮여도 적용본을 쓴다). 승인 버전
        (`latest_applied_version`)이 없으면 None — `baseline`·`external_change`만 있거나, Redis에
        승인 버전 없는 `structure_meta`(레거시 분석본·수동 프로필 사본)만 있어도 적용본이 아니다.
        Redis 미연결이면 캐시만 건너뛰고 적용본은 반환한다.
        """
        validate_db_id(db_id)
        latest = self.latest_applied_version(db_id)
        if latest is None:
            return None
        profile = latest.get("profile")
        if not isinstance(profile, dict):
            logger.warning(
                "구조 프로필 최신 버전에 profile이 없어 적용본을 복원하지 못함 (db_id=%s, ver=%d)",
                db_id, latest["ver"],
            )
            return None
        meta = {k: v for k, v in profile.items() if k != "source"}
        if await self._ready():
            assert self._redis_cache is not None
            try:
                await self._redis_cache.set_json(self._key(db_id, APPLIED_SUFFIX), meta)
                logger.info(
                    "구조 적용본 캐시를 버전에서 복원: db_id=%s, ver=%d", db_id, latest["ver"]
                )
            except Exception as e:
                logger.warning("구조 적용본 캐시 복원 실패 (db_id=%s): %s", db_id, e)
        return meta

    # --- 컬럼 설명·유사어 백업 (B-6) ---

    def _descriptions_backup_path(self, db_id: str) -> Path:
        return self._backup_root / validate_db_id(db_id) / "descriptions.yaml"

    def backup_descriptions(
        self,
        db_id: str,
        descriptions: dict[str, str],
        synonyms: dict[str, dict[str, Any]],
    ) -> Path:
        """적용한 컬럼 설명·유사어를 `{backup_root}/{db_id}/descriptions.yaml`에 원자 기록한다.

        Args:
            db_id: DB 식별자
            descriptions: `{"table.column": 설명}`
            synonyms: `{"table.column": {"words": [...], "sources": {...}}}`(출처 태그 dict 형태)

        Returns:
            기록한 파일 경로
        """
        path = self._descriptions_backup_path(db_id)
        record = {
            "saved_at": _now_iso(),
            "descriptions": dict(descriptions),
            "synonyms": dict(synonyms),
        }
        _atomic_write_text(path, _dump_yaml_exact(record))
        return path

    def load_descriptions_backup(self, db_id: str) -> dict[str, Any] | None:
        """설명 백업을 읽는다. 없으면 None, 읽기 실패·형식 오류는 WARNING 후 None.

        Returns:
            `{"descriptions", "synonyms", "saved_at"}`
        """
        path = self._descriptions_backup_path(db_id)
        if not path.is_file():
            return None
        try:
            data = yaml.safe_load(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            logger.warning("설명 백업 읽기 실패 (%s): %s", path, e)
            return None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("descriptions"), dict)
            or not isinstance(data.get("synonyms"), dict)
        ):
            logger.warning("설명 백업 형식 오류 (%s): descriptions·synonyms 없음", path)
            return None
        return {
            "descriptions": data["descriptions"],
            "synonyms": data["synonyms"],
            "saved_at": data.get("saved_at"),
        }

    # --- 등록 상태 ---

    async def record_step(
        self,
        db_id: str,
        step: str,
        *,
        status: str,
        count: int | None = None,
        by: str | None = None,
        env: str | None = None,
        provider: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """등록 단계 상태를 기록하고 그 단계 항목을 반환한다(`at`은 자동)."""
        validate_db_id(db_id)
        redis_cache = await self._require()
        entry = {
            "status": status,
            "count": count,
            "at": _now_iso(),
            "by": by,
            "env": env,
            "provider": provider,
            "detail": detail,
        }
        async with self._lock(db_id):
            registration = await self._load_dict(db_id, REGISTRATION_SUFFIX) or {}
            registration[step] = entry
            await redis_cache.set_json(
                self._key(db_id, REGISTRATION_SUFFIX), registration
            )
        return entry

    async def load_registration(self, db_id: str) -> dict[str, dict[str, Any]]:
        """단계별 등록 상태(없으면 빈 dict)."""
        validate_db_id(db_id)
        registration = await self._load_dict(db_id, REGISTRATION_SUFFIX) or {}
        return {k: v for k, v in registration.items() if isinstance(v, dict)}

    # --- 컬럼 설명·유사어 초안 ---

    async def add_description_draft(self, db_id: str, draft: dict[str, Any]) -> dict[str, Any]:
        """설명·유사어 초안을 추가한다.

        `draft_id`·`created_at`·`status="pending"`을 채워 반환한다.
        """
        validate_db_id(db_id)
        return await self._add_item(db_id, DESCRIPTION_DRAFTS_SUFFIX, draft)

    async def list_description_drafts(self, db_id: str) -> list[dict[str, Any]]:
        """설명·유사어 초안 목록(최신순)."""
        validate_db_id(db_id)
        return list(
            reversed(await self._load_list(db_id, DESCRIPTION_DRAFTS_SUFFIX))
        )

    async def get_description_draft(self, db_id: str, draft_id: str) -> dict[str, Any] | None:
        """설명·유사어 초안 1건(없으면 None)."""
        validate_db_id(db_id)
        return await self._get_item(db_id, DESCRIPTION_DRAFTS_SUFFIX, draft_id)

    async def update_description_draft(
        self, db_id: str, draft_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        """설명·유사어 초안 필드를 갱신하고 갱신본을 반환한다(없으면 None)."""
        validate_db_id(db_id)
        return await self._update_item(
            db_id, DESCRIPTION_DRAFTS_SUFFIX, draft_id, fields
        )
