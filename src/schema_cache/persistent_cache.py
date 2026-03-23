"""파일 기반 영구 스키마 캐시.

DB별 스키마 분석 결과를 JSON 파일로 저장/로드하며,
fingerprint 기반 변경 감지를 통해 캐시 유효성을 판단한다.

3단계 캐시 구조:
  1차: 메모리 캐시 (TTL 기반, 기존 SchemaCache)
  2차: 파일 캐시 (fingerprint 기반, 이 모듈)
  3차: DB 전체 조회 (캐시 미스 또는 변경 감지 시)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from src.schema_cache.fingerprint import compute_fingerprint_from_schema_dict

logger = logging.getLogger(__name__)

# 캐시 파일 메타데이터 버전 (포맷 변경 시 증가)
CACHE_FORMAT_VERSION = 1


class PersistentSchemaCache:
    """파일 기반 영구 스키마 캐시.

    DB별로 독립적인 캐시 파일을 관리하며,
    fingerprint 비교를 통해 변경 감지를 수행한다.
    """

    def __init__(
        self,
        cache_dir: str = ".cache/schema",
        enabled: bool = True,
    ) -> None:
        """영구 캐시를 초기화한다.

        Args:
            cache_dir: 캐시 파일 저장 디렉토리 경로
            enabled: 캐시 활성화 여부 (False이면 항상 캐시 미스)
        """
        self._cache_dir = Path(cache_dir)
        self._enabled = enabled

        if self._enabled:
            self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """캐시 디렉토리가 존재하지 않으면 생성한다."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                "캐시 디렉토리 생성 실패 (%s): %s. 영구 캐시가 비활성화됩니다.",
                self._cache_dir,
                e,
            )
            self._enabled = False

    def _cache_file_path(self, db_id: str) -> Path:
        """DB 식별자에 대한 캐시 파일 경로를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            캐시 파일 경로
        """
        # 파일명에 안전하지 않은 문자 제거
        safe_id = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in db_id)
        return self._cache_dir / f"{safe_id}_schema.json"

    def load(self, db_id: str) -> Optional[dict]:
        """캐시 파일에서 스키마 정보를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            캐시된 스키마 데이터 (없거나 손상 시 None)
        """
        if not self._enabled:
            return None

        cache_path = self._cache_file_path(db_id)
        if not cache_path.exists():
            logger.debug("캐시 파일 없음: %s", cache_path)
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 포맷 버전 확인
            if data.get("_cache_version") != CACHE_FORMAT_VERSION:
                logger.info(
                    "캐시 포맷 버전 불일치 (기대: %d, 실제: %s). 캐시 무시.",
                    CACHE_FORMAT_VERSION,
                    data.get("_cache_version"),
                )
                return None

            logger.debug(
                "캐시 로드 성공: db_id=%s, fingerprint=%s",
                db_id,
                data.get("_fingerprint", "N/A"),
            )
            return data

        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(
                "캐시 파일 로드 실패 (%s): %s. 전체 조회로 폴백합니다.",
                cache_path,
                e,
            )
            # 손상된 캐시 파일 삭제
            self._safe_delete(cache_path)
            return None

    def save(
        self,
        db_id: str,
        schema_dict: dict,
        fingerprint: Optional[str] = None,
    ) -> bool:
        """스키마 정보를 캐시 파일에 저장한다.

        Args:
            db_id: DB 식별자
            schema_dict: 스키마 딕셔너리 (tables, relationships 등)
            fingerprint: 스키마 fingerprint (None이면 자동 계산)

        Returns:
            저장 성공 여부
        """
        if not self._enabled:
            return False

        if fingerprint is None:
            fingerprint = compute_fingerprint_from_schema_dict(schema_dict)

        cache_data = {
            "_cache_version": CACHE_FORMAT_VERSION,
            "_fingerprint": fingerprint,
            "_db_id": db_id,
            "_cached_at": time.time(),
            "_cached_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "schema": schema_dict,
        }

        cache_path = self._cache_file_path(db_id)

        try:
            # 원자적 쓰기: 임시 파일에 쓴 후 이동
            tmp_path = cache_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            tmp_path.replace(cache_path)
            logger.info(
                "스키마 캐시 저장: db_id=%s, fingerprint=%s, 파일=%s",
                db_id,
                fingerprint,
                cache_path,
            )
            return True

        except OSError as e:
            logger.warning("캐시 저장 실패 (%s): %s", cache_path, e)
            # 임시 파일 정리
            self._safe_delete(cache_path.with_suffix(".tmp"))
            return False

    def get_cached_fingerprint(self, db_id: str) -> Optional[str]:
        """캐시된 스키마의 fingerprint를 반환한다.

        전체 캐시 데이터를 로드하지 않고 fingerprint만 확인할 때 사용한다.

        Args:
            db_id: DB 식별자

        Returns:
            캐시된 fingerprint 또는 None
        """
        data = self.load(db_id)
        if data is None:
            return None
        return data.get("_fingerprint")

    def get_schema(self, db_id: str) -> Optional[dict]:
        """캐시된 스키마 딕셔너리만 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            스키마 딕셔너리 또는 None
        """
        data = self.load(db_id)
        if data is None:
            return None
        return data.get("schema")

    def is_changed(self, db_id: str, current_fingerprint: str) -> bool:
        """현재 DB fingerprint와 캐시된 fingerprint를 비교하여 변경 여부를 판단한다.

        Args:
            db_id: DB 식별자
            current_fingerprint: DB에서 방금 조회한 fingerprint

        Returns:
            True이면 스키마가 변경됨 (캐시 갱신 필요)
        """
        cached_fingerprint = self.get_cached_fingerprint(db_id)
        if cached_fingerprint is None:
            # 캐시 없음 = 변경으로 간주
            return True
        return cached_fingerprint != current_fingerprint

    def invalidate(self, db_id: str) -> bool:
        """특정 DB의 캐시를 무효화(삭제)한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        cache_path = self._cache_file_path(db_id)
        return self._safe_delete(cache_path)

    def invalidate_all(self) -> int:
        """모든 캐시 파일을 삭제한다.

        Returns:
            삭제된 파일 수
        """
        if not self._cache_dir.exists():
            return 0

        count = 0
        for cache_file in self._cache_dir.glob("*_schema.json"):
            if self._safe_delete(cache_file):
                count += 1
        logger.info("전체 캐시 삭제: %d개 파일", count)
        return count

    def list_cached_dbs(self) -> list[dict[str, Any]]:
        """캐시된 DB 목록과 메타정보를 반환한다.

        Returns:
            캐시 정보 목록 (db_id, fingerprint, cached_at 등)
        """
        if not self._cache_dir.exists():
            return []

        result = []
        for cache_file in sorted(self._cache_dir.glob("*_schema.json")):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "db_id": data.get("_db_id", cache_file.stem.replace("_schema", "")),
                    "fingerprint": data.get("_fingerprint", ""),
                    "cached_at": data.get("_cached_at_iso", ""),
                    "cache_version": data.get("_cache_version", 0),
                    "file_path": str(cache_file),
                })
            except (json.JSONDecodeError, OSError):
                result.append({
                    "db_id": cache_file.stem.replace("_schema", ""),
                    "fingerprint": "",
                    "cached_at": "",
                    "cache_version": 0,
                    "file_path": str(cache_file),
                    "error": "파일 손상",
                })
        return result

    def _safe_delete(self, path: Path) -> bool:
        """파일을 안전하게 삭제한다.

        Args:
            path: 삭제할 파일 경로

        Returns:
            삭제 성공 여부
        """
        try:
            if path.exists():
                path.unlink()
                logger.debug("캐시 파일 삭제: %s", path)
                return True
        except OSError as e:
            logger.warning("캐시 파일 삭제 실패 (%s): %s", path, e)
        return False

    def update_field(self, db_id: str, field: str, value: Any) -> bool:
        """캐시 파일의 특정 필드를 업데이트한다.

        기존 캐시 파일을 로드하여 필드를 수정하고 다시 저장한다.

        Args:
            db_id: DB 식별자
            field: 업데이트할 필드 이름
            value: 새 값

        Returns:
            업데이트 성공 여부
        """
        data = self.load(db_id)
        if data is None:
            return False

        data[field] = value
        cache_path = self._cache_file_path(db_id)
        try:
            tmp_path = cache_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(cache_path)
            return True
        except OSError as e:
            logger.warning("캐시 파일 필드 업데이트 실패 (%s): %s", cache_path, e)
            self._safe_delete(tmp_path)
            return False

    @property
    def enabled(self) -> bool:
        """캐시 활성화 여부."""
        return self._enabled

    @property
    def cache_dir(self) -> Path:
        """캐시 디렉토리 경로."""
        return self._cache_dir
