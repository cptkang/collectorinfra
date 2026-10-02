"""DB별 서버 엔터티 키 매니페스트 로더 (plans/102 §3.3-① · D-224 ③).

시스템 간 키 대응(어느 컬럼을 어떤 방식으로 비교하는가)은 공용 코드가 아니라 **DB별 데이터**에
둔다. 구조 정본인 `config/db_profiles/{db_id}.yaml`의 `entity_keys` 블록을 읽어
`src.domain.entity_key.EntityKeyManifest`로 돌려준다 — 판정·비교는 도메인이 한다.

로컬 하네스 전용 매니페스트(`testdata/itam/entity_keys.local.yaml` — 운영 식별자 미확정 가정)는
`load_manifest_file`로 **명시 경로에서만** 읽는다. 런타임 로더는 testdata를 보지 않는다
(샌드박스에서 운영 정본을 파생하지 않는다 — plans/95 §4.6.2).

계층: infrastructure (파일 I/O).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from src.domain.entity_key import EntityKeyManifest, ManifestError, parse_manifest

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = PROJECT_ROOT / "config" / "db_profiles"
#: 프로필 안의 블록 이름.
BLOCK_KEY = "entity_keys"


def load_manifest_file(path: str | Path, db_id: str) -> EntityKeyManifest | None:
    """YAML 파일의 `entity_keys` 블록을 읽는다. 파일·블록이 없으면 None.

    블록 구조가 틀리면 **None + WARNING**이다 — 틀린 대응으로 조회하면 0건이 정상 결과처럼
    보이므로, 매니페스트가 없는 것(=키 브리지 미적용 사유 노출)으로 취급하는 편이 안전하다.
    """
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — 프로필 파싱 실패가 조회 경로를 멈추면 안 된다
        logger.warning("엔터티 키 매니페스트 파싱 실패(%s): %s", target, exc)
        return None
    if not isinstance(data, dict) or BLOCK_KEY not in data:
        return None
    try:
        return parse_manifest(db_id, data[BLOCK_KEY], source=str(target))
    except ManifestError as exc:
        logger.warning("엔터티 키 매니페스트 구조 오류 — 미적용: %s", exc)
        return None


@lru_cache(maxsize=64)
def _load_cached(db_id: str, profiles_dir: str) -> EntityKeyManifest | None:
    return load_manifest_file(Path(profiles_dir) / f"{db_id}.yaml", db_id)


def load_entity_key_manifest(
    db_id: str, *, profiles_dir: str | Path | None = None,
) -> EntityKeyManifest | None:
    """구조 정본 프로필에서 db_id의 매니페스트를 읽는다(프로세스 캐시).

    Args:
        db_id: 대상 DB
        profiles_dir: 프로필 디렉터리(기본 `config/db_profiles`) — 테스트 주입용

    Returns:
        매니페스트, 또는 프로필·블록이 없거나 구조가 틀리면 None
    """
    if not db_id:
        return None
    return _load_cached(db_id, str(profiles_dir or PROFILES_DIR))


def clear_manifest_cache() -> None:
    """캐시를 비운다(테스트·프로필 갱신용)."""
    _load_cached.cache_clear()
