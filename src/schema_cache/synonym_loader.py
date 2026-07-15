"""파일 기반 글로벌 유사단어 사전 로더.

YAML/JSON 파일에서 글로벌 유사단어 사전을 로드하여
Redis synonyms:global, synonyms:resource_types, synonyms:eav_names에 저장한다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SynonymLoadResult:
    """유사단어 로드 결과."""

    status: str  # "success" | "partial" | "error"
    file_path: str
    columns_loaded: int = 0
    resource_types_loaded: int = 0
    eav_names_loaded: int = 0
    column_values_loaded: int = 0
    total_words: int = 0
    merge_mode: bool = True
    errors: list[str] = field(default_factory=list)
    message: str = ""


class SynonymLoader:
    """파일에서 글로벌 유사단어 사전을 로드하여 Redis에 저장한다."""

    DEFAULT_FILE = "config/global_synonyms.yaml"

    def __init__(
        self,
        redis_cache: Any,  # RedisSchemaCache 인스턴스
        config_dir: str = "config",
    ) -> None:
        """SynonymLoader를 초기화한다.

        Args:
            redis_cache: RedisSchemaCache 인스턴스
            config_dir: 설정 파일 디렉토리 경로
        """
        self._redis_cache = redis_cache
        self._config_dir = config_dir
        self._last_loaded_at: Optional[str] = None
        self._last_file_mtime: Optional[float] = None
        self._last_file_path: Optional[str] = None
        self._last_stats: dict = {}

    async def load_from_yaml(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """YAML 파일에서 글로벌 유사단어를 로드하여 Redis에 저장한다.

        Args:
            file_path: YAML 파일 경로
            merge: True이면 기존 데이터와 병합, False이면 덮어쓰기

        Returns:
            로드 결과
        """
        try:
            import yaml
        except ImportError:
            logger.error("PyYAML이 설치되지 않았습니다: pip install pyyaml")
            return SynonymLoadResult(
                status="error",
                file_path=file_path,
                message="PyYAML이 설치되지 않았습니다.",
                errors=["PyYAML 미설치"],
            )

        try:
            resolved = Path(file_path).resolve()
            if not resolved.exists():
                return SynonymLoadResult(
                    status="error",
                    file_path=file_path,
                    message=f"파일을 찾을 수 없습니다: {file_path}",
                    errors=[f"파일 없음: {file_path}"],
                )

            with open(resolved, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                return SynonymLoadResult(
                    status="error",
                    file_path=file_path,
                    message="YAML 파일 형식이 올바르지 않습니다 (dict 아님).",
                    errors=["잘못된 YAML 형식"],
                )

            result = await self._process_synonym_data(data, str(resolved), merge)
            logger.info(
                "YAML 유사단어 로드 완료: %s (%s)", file_path, result.message
            )
            return result

        except Exception as e:
            logger.error("YAML 유사단어 로드 실패 (%s): %s", file_path, e)
            return SynonymLoadResult(
                status="error",
                file_path=file_path,
                message=f"YAML 로드 실패: {e}",
                errors=[str(e)],
            )

    async def load_from_json(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """JSON 파일에서 글로벌 유사단어를 로드하여 Redis에 저장한다.

        Args:
            file_path: JSON 파일 경로
            merge: True이면 기존 데이터와 병합, False이면 덮어쓰기

        Returns:
            로드 결과
        """
        try:
            resolved = Path(file_path).resolve()
            if not resolved.exists():
                return SynonymLoadResult(
                    status="error",
                    file_path=file_path,
                    message=f"파일을 찾을 수 없습니다: {file_path}",
                    errors=[f"파일 없음: {file_path}"],
                )

            with open(resolved, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return SynonymLoadResult(
                    status="error",
                    file_path=file_path,
                    message="JSON 파일 형식이 올바르지 않습니다 (dict 아님).",
                    errors=["잘못된 JSON 형식"],
                )

            result = await self._process_synonym_data(data, str(resolved), merge)
            logger.info(
                "JSON 유사단어 로드 완료: %s (%s)", file_path, result.message
            )
            return result

        except json.JSONDecodeError as e:
            logger.error("JSON 파싱 실패 (%s): %s", file_path, e)
            return SynonymLoadResult(
                status="error",
                file_path=file_path,
                message=f"JSON 파싱 실패: {e}",
                errors=[str(e)],
            )
        except Exception as e:
            logger.error("JSON 유사단어 로드 실패 (%s): %s", file_path, e)
            return SynonymLoadResult(
                status="error",
                file_path=file_path,
                message=f"JSON 로드 실패: {e}",
                errors=[str(e)],
            )

    async def load_auto(
        self,
        file_path: str | None = None,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """파일 확장자를 자동 감지하여 적절한 로더를 호출한다.

        Args:
            file_path: 파일 경로 (None이면 DEFAULT_FILE 사용)
            merge: True이면 기존 데이터와 병합, False이면 덮어쓰기

        Returns:
            로드 결과
        """
        if file_path is None:
            file_path = self.DEFAULT_FILE

        try:
            ext = Path(file_path).suffix.lower()

            if ext in (".yaml", ".yml"):
                return await self.load_from_yaml(file_path, merge=merge)
            elif ext == ".json":
                return await self.load_from_json(file_path, merge=merge)
            else:
                return SynonymLoadResult(
                    status="error",
                    file_path=file_path,
                    message=f"지원하지 않는 파일 확장자입니다: {ext}",
                    errors=[f"지원하지 않는 확장자: {ext}"],
                )
        except Exception as e:
            logger.error("자동 로드 실패 (%s): %s", file_path, e)
            return SynonymLoadResult(
                status="error",
                file_path=file_path or "",
                message=f"자동 로드 실패: {e}",
                errors=[str(e)],
            )

    async def check_and_reload(self) -> Optional[SynonymLoadResult]:
        """파일 변경을 감지하고, 변경되었으면 자동 리로드한다.

        이전에 로드한 파일의 mtime을 비교하여 변경 여부를 판단한다.

        Returns:
            리로드 결과 또는 None (변경 없음 / 이전 로드 이력 없음)
        """
        if self._last_file_path is None:
            return None

        try:
            if not os.path.exists(self._last_file_path):
                logger.warning(
                    "이전 로드 파일이 삭제됨: %s", self._last_file_path
                )
                return None

            current_mtime = os.path.getmtime(self._last_file_path)
            if (
                self._last_file_mtime is not None
                and current_mtime <= self._last_file_mtime
            ):
                return None

            logger.info(
                "유사단어 파일 변경 감지, 리로드: %s", self._last_file_path
            )
            return await self.load_auto(self._last_file_path, merge=True)

        except Exception as e:
            logger.error("파일 변경 감지 실패: %s", e)
            return None

    async def export_to_yaml(self, output_path: str) -> bool:
        """현재 Redis의 글로벌 유사단어를 YAML 파일로 내보낸다.

        출력 구조:
            version: "1.0"
            domain: "infrastructure"
            updated_at: ISO 형식 타임스탬프
            columns: {컬럼명: {words: [...], description: "..."}}
            resource_type_values: {값: {words: [...]}}
            eav_name_values: {이름: {words: [...]}}

        Args:
            output_path: 출력 YAML 파일 경로

        Returns:
            내보내기 성공 여부
        """
        try:
            import yaml
        except ImportError:
            logger.error("PyYAML이 설치되지 않았습니다: pip install pyyaml")
            return False

        try:
            # columns 섹션
            columns_full = await self._redis_cache.load_global_synonyms_full()
            columns_section: dict[str, dict] = {}
            for col_name, entry in columns_full.items():
                col_data: dict[str, Any] = {"words": entry.get("words", [])}
                desc = entry.get("description", "")
                if desc:
                    col_data["description"] = desc
                columns_section[col_name] = col_data

            # resource_type_values 섹션
            rt_synonyms = await self._redis_cache.load_resource_type_synonyms()
            rt_section: dict[str, dict] = {}
            for rt_value, words in rt_synonyms.items():
                rt_section[rt_value] = {"words": words}

            # eav_name_values 섹션
            eav_synonyms = await self._redis_cache.load_eav_name_synonyms()
            eav_section: dict[str, dict] = {}
            for eav_name, words in eav_synonyms.items():
                eav_section[eav_name] = {"words": words}

            # column_value_synonyms 섹션
            cv_synonyms = await self._redis_cache.load_column_value_synonyms()
            cv_section: dict[str, dict] = {}
            for col_name, value_map in cv_synonyms.items():
                cv_section[col_name] = {"values": value_map}

            data: dict[str, Any] = {
                "version": "1.0",
                "domain": "infrastructure",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "columns": columns_section,
                "resource_type_values": rt_section,
                "eav_name_values": eav_section,
                "column_value_synonyms": cv_section,
            }

            # 부모 디렉토리 생성
            output_resolved = Path(output_path).resolve()
            output_resolved.parent.mkdir(parents=True, exist_ok=True)

            with open(output_resolved, "w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

            logger.info("YAML 내보내기 완료: %s", output_path)
            return True

        except Exception as e:
            logger.error("YAML 내보내기 실패 (%s): %s", output_path, e)
            return False

    async def export_to_json(self, output_path: str) -> bool:
        """현재 Redis의 글로벌 유사단어를 JSON 파일로 내보낸다.

        출력 구조는 export_to_yaml과 동일하다.

        Args:
            output_path: 출력 JSON 파일 경로

        Returns:
            내보내기 성공 여부
        """
        try:
            # columns 섹션
            columns_full = await self._redis_cache.load_global_synonyms_full()
            columns_section: dict[str, dict] = {}
            for col_name, entry in columns_full.items():
                col_data: dict[str, Any] = {"words": entry.get("words", [])}
                desc = entry.get("description", "")
                if desc:
                    col_data["description"] = desc
                columns_section[col_name] = col_data

            # resource_type_values 섹션
            rt_synonyms = await self._redis_cache.load_resource_type_synonyms()
            rt_section: dict[str, dict] = {}
            for rt_value, words in rt_synonyms.items():
                rt_section[rt_value] = {"words": words}

            # eav_name_values 섹션
            eav_synonyms = await self._redis_cache.load_eav_name_synonyms()
            eav_section: dict[str, dict] = {}
            for eav_name, words in eav_synonyms.items():
                eav_section[eav_name] = {"words": words}

            # column_value_synonyms 섹션
            cv_synonyms = await self._redis_cache.load_column_value_synonyms()
            cv_section: dict[str, dict] = {}
            for col_name, value_map in cv_synonyms.items():
                cv_section[col_name] = {"values": value_map}

            data: dict[str, Any] = {
                "version": "1.0",
                "domain": "infrastructure",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "columns": columns_section,
                "resource_type_values": rt_section,
                "eav_name_values": eav_section,
                "column_value_synonyms": cv_section,
            }

            # 부모 디렉토리 생성
            output_resolved = Path(output_path).resolve()
            output_resolved.parent.mkdir(parents=True, exist_ok=True)

            with open(output_resolved, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("JSON 내보내기 완료: %s", output_path)
            return True

        except Exception as e:
            logger.error("JSON 내보내기 실패 (%s): %s", output_path, e)
            return False

    def get_last_loaded_at(self) -> Optional[str]:
        """마지막 로드 시각을 반환한다.

        Returns:
            ISO 형식 타임스탬프 또는 None
        """
        return self._last_loaded_at

    def get_loaded_stats(self) -> dict:
        """마지막 로드 통계를 반환한다.

        Returns:
            통계 딕셔너리 (file_path, loaded_at, column_count 등)
        """
        return self._last_stats.copy()

    # === 내부 메서드 ===

    async def _process_synonym_data(
        self,
        data: dict,
        file_path: str,
        merge: bool,
    ) -> SynonymLoadResult:
        """파싱된 사전 데이터를 Redis에 저장한다.

        Args:
            data: 파싱된 YAML/JSON 딕셔너리
            file_path: 원본 파일 경로
            merge: True이면 기존 데이터와 병합, False이면 덮어쓰기

        Returns:
            로드 결과
        """
        result = SynonymLoadResult(
            status="success", file_path=file_path, merge_mode=merge
        )

        try:
            # 1. columns 섹션 처리
            columns = data.get("columns", {})
            if columns:
                if merge:
                    # 각 컬럼별로 add_global_synonym으로 병합 (중복 제거)
                    for col_name, col_info in columns.items():
                        words = col_info.get("words", [])
                        description = col_info.get("description", "")
                        if words:
                            await self._redis_cache.add_global_synonym(
                                col_name, words
                            )
                        if description:
                            await self._redis_cache.update_global_description(
                                col_name, description
                            )
                        result.columns_loaded += 1
                        result.total_words += len(words)
                else:
                    # 덮어쓰기: 전체를 한번에 저장
                    synonyms_dict: dict[str, dict] = {}
                    for col_name, col_info in columns.items():
                        entry: dict[str, Any] = {
                            "words": col_info.get("words", []),
                        }
                        desc = col_info.get("description", "")
                        if desc:
                            entry["description"] = desc
                        synonyms_dict[col_name] = entry
                        result.columns_loaded += 1
                        result.total_words += len(entry["words"])
                    await self._redis_cache.save_global_synonyms(synonyms_dict)

            # 2. resource_type_values 섹션 처리
            rt_values = data.get("resource_type_values", {})
            if rt_values:
                rt_synonyms: dict[str, list[str]] = {}
                for rt_value, rt_info in rt_values.items():
                    words = rt_info.get("words", [])
                    rt_synonyms[rt_value] = words
                    result.total_words += len(words)
                await self._redis_cache.save_resource_type_synonyms(rt_synonyms)
                result.resource_types_loaded = len(rt_synonyms)

            # 3. eav_name_values 섹션 처리
            eav_values = data.get("eav_name_values", {})
            if eav_values:
                eav_synonyms: dict[str, list[str]] = {}
                for eav_name, eav_info in eav_values.items():
                    words = eav_info.get("words", [])
                    eav_synonyms[eav_name] = words
                    result.total_words += len(words)
                    # EAV 속성명도 global에 등록하여 통합 비교 인프라 공유
                    if words:
                        await self._redis_cache.add_global_synonym(eav_name, words)
                await self._redis_cache.save_eav_name_synonyms(eav_synonyms)
                result.eav_names_loaded = len(eav_synonyms)

            # 4. column_value_synonyms 섹션 처리
            col_val_synonyms = data.get("column_value_synonyms", {})
            if col_val_synonyms:
                cv_map: dict[str, dict[str, dict]] = {}
                for col_name, col_info in col_val_synonyms.items():
                    values = col_info.get("values", {})
                    if values:
                        cv_map[col_name.upper()] = values
                        result.column_values_loaded += 1
                if cv_map:
                    await self._redis_cache.save_column_value_synonyms(cv_map)

            # 통계 업데이트
            self._last_loaded_at = datetime.now(timezone.utc).isoformat()
            self._last_file_path = file_path
            self._last_file_mtime = os.path.getmtime(file_path)
            self._last_stats = {
                "file_path": file_path,
                "loaded_at": self._last_loaded_at,
                "column_count": result.columns_loaded,
                "resource_type_count": result.resource_types_loaded,
                "eav_name_count": result.eav_names_loaded,
                "column_value_count": result.column_values_loaded,
                "total_words": result.total_words,
            }

            result.message = (
                f"로드 완료: columns={result.columns_loaded}, "
                f"resource_types={result.resource_types_loaded}, "
                f"eav_names={result.eav_names_loaded}, "
                f"column_values={result.column_values_loaded}, "
                f"total_words={result.total_words}"
            )

            # 부분 실패 체크
            if result.errors:
                result.status = "partial"

            return result

        except Exception as e:
            logger.error("유사단어 데이터 처리 실패: %s", e)
            result.status = "error"
            result.errors.append(str(e))
            result.message = f"데이터 처리 실패: {e}"
            return result

    # =========================================================================
    # per-DB 시드 파일 로드/내보내기 (Plan 61 트랙 B 후속 — 시딩·마이그레이션)
    # docs/synonym_seed_migration_review.md 설계. 시드 파일은
    # scripts/synonym_seeds.py derive 가 시맨틱 모델·프로필에서 결정적으로 생성한다.
    # =========================================================================

    async def load_seed_yaml(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """per-DB 시드 파일(config/synonym_seeds/{db_id}.yaml)을 Redis에 로드한다.

        시드 스키마:
            db_id: polestar_cm_gp            # 필수
            source_tag: operator             # E5-3 메타 source (감쇠 보호 — llm만 감쇠됨)
            column_synonyms: {schema.table.column: [words]}   # per-DB 사전(E5-1 게이트 참여)
            eav_names: {AttrName: [words]}                    # synonyms:eav_names 병합
            column_values: {column: {word: {op, value}}}      # synonyms:column_values 병합

        병합 정책(멱등·무손실):
        - column_synonyms: ``add_synonyms``(단어 합집합, source 태깅) — 기존
          LLM 발견·운영자 등록 단어를 절대 삭제하지 않는다. 재실행 안전.
        - eav_names/column_values: 기존 값을 읽어 **합집합 후 저장**(HSET은
          field 단위 덮어쓰기이므로 read-union-save로 무손실 보장).
        - merge=False여도 삭제는 하지 않는다(시드는 추가 전용 — 사전 정리는
          E5-3 감쇠/운영자 삭제 경로 소관).

        Args:
            file_path: 시드 YAML 경로
            merge: 병합 모드(현재 추가 전용이므로 동작 동일 — 시그니처 일관용)

        Returns:
            로드 결과(columns_loaded=column_synonyms 키 수)
        """
        try:
            import yaml
        except ImportError:
            return SynonymLoadResult(
                status="error", file_path=file_path,
                message="PyYAML이 설치되지 않았습니다.", errors=["PyYAML 미설치"],
            )

        result = SynonymLoadResult(
            status="success", file_path=file_path, merge_mode=True
        )
        try:
            resolved = Path(file_path).resolve()
            if not resolved.exists():
                result.status = "error"
                result.message = f"파일을 찾을 수 없습니다: {file_path}"
                result.errors.append(result.message)
                return result

            with open(resolved, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            db_id = data.get("db_id")
            if not db_id:
                result.status = "error"
                result.message = "시드 파일에 db_id가 없습니다."
                result.errors.append(result.message)
                return result

            source_tag = str(data.get("source_tag") or "operator")

            # 1. per-DB column_synonyms — 합집합 병합 + source 태깅(E5-3 감쇠 보호)
            for column_key, words in (data.get("column_synonyms") or {}).items():
                clean = [w.strip() for w in (words or []) if w and len(w.strip()) >= 2]
                if not clean:
                    continue
                ok = await self._redis_cache.add_synonyms(
                    db_id, column_key, clean, source=source_tag
                )
                if ok:
                    result.columns_loaded += 1
                    result.total_words += len(clean)
                else:
                    result.errors.append(f"column_synonyms 저장 실패: {column_key}")

            # 2. eav_names — read-union-save (무손실)
            seed_eav = data.get("eav_names") or {}
            if seed_eav:
                existing = await self._redis_cache.load_eav_name_synonyms()
                merged: dict[str, list[str]] = {}
                for name, words in seed_eav.items():
                    clean = [w.strip() for w in (words or []) if w and len(w.strip()) >= 2]
                    if not clean:
                        continue
                    union = list(dict.fromkeys((existing.get(name) or []) + clean))
                    merged[name] = union
                    # 기존 글로벌 로더와 동일하게 통합 비교 인프라 공유
                    await self._redis_cache.add_global_synonym(name, clean)
                if merged:
                    await self._redis_cache.save_eav_name_synonyms(merged)
                    result.eav_names_loaded = len(merged)
                    result.total_words += sum(len(v) for v in merged.values())

            # 3. column_values — read-union-save (word 단위 병합, 기존 값 우선)
            seed_cv = data.get("column_values") or {}
            if seed_cv:
                existing_cv = await self._redis_cache.load_column_value_synonyms()
                merged_cv: dict[str, dict[str, dict]] = {}
                for col, value_map in seed_cv.items():
                    key = col.upper()
                    cur = dict(existing_cv.get(key) or {})
                    for word, spec in (value_map or {}).items():
                        cur.setdefault(word, spec)  # 기존 등록 우선(시드는 보충)
                    merged_cv[key] = cur
                if merged_cv:
                    await self._redis_cache.save_column_value_synonyms(merged_cv)
                    result.column_values_loaded = len(merged_cv)

            result.message = (
                f"시드 로드 완료(db={db_id}): columns={result.columns_loaded}, "
                f"eav_names={result.eav_names_loaded}, "
                f"column_values={result.column_values_loaded}, "
                f"total_words={result.total_words}"
            )
            if result.errors:
                result.status = "partial"
            logger.info("per-DB 시드 로드: %s (%s)", file_path, result.message)
            return result

        except Exception as e:
            logger.error("per-DB 시드 로드 실패 (%s): %s", file_path, e)
            result.status = "error"
            result.errors.append(str(e))
            result.message = f"시드 로드 실패: {e}"
            return result

    async def export_seed_yaml(self, db_id: str, output_path: str) -> bool:
        """운영 Redis의 per-DB 사전을 시드 형식 YAML로 내보낸다(마이그레이션용).

        운영 중 LLM 발견→사람 승인으로 누적된 사전을 파일로 내려 다른 환경
        (폐쇄망 프로덕션·신규 DB 편입)에 load_seed_yaml로 반입하는 왕복을 완성한다.
        내보내는 범위: per-DB column_synonyms + 글로벌 eav_names/column_values 스냅샷.

        Args:
            db_id: DB 식별자
            output_path: 출력 YAML 경로

        Returns:
            성공 여부
        """
        try:
            import yaml
        except ImportError:
            logger.error("PyYAML이 설치되지 않았습니다: pip install pyyaml")
            return False

        try:
            column_synonyms = await self._redis_cache.load_synonyms(db_id)
            eav_names = await self._redis_cache.load_eav_name_synonyms()
            column_values = await self._redis_cache.load_column_value_synonyms()

            payload = {
                "version": "1.0",
                "db_id": db_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "source_tag": "operator",
                "column_synonyms": {
                    k: sorted(v) for k, v in sorted(column_synonyms.items())
                },
                "eav_names": {k: sorted(v) for k, v in sorted(eav_names.items())},
                "column_values": {
                    k: dict(sorted(v.items()))
                    for k, v in sorted(column_values.items())
                },
            }
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    payload, f, allow_unicode=True, sort_keys=False,
                    default_flow_style=False,
                )
            logger.info(
                "per-DB 시드 내보내기 완료: db=%s → %s (columns=%d)",
                db_id, output_path, len(column_synonyms),
            )
            return True
        except Exception as e:
            logger.error("per-DB 시드 내보내기 실패 (db=%s): %s", db_id, e)
            return False
