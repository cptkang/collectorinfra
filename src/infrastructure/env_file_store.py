"""`.env` 파일 읽기·쓰기 (plans/166 A7 — `src/api/routes/admin.py`에서 이동).

운영자 설정 화면(`/admin/settings`·`/admin/db-config`)이 `.env`를 파싱하고 고쳐 쓰는 파일 I/O다.
대상 경로는 호출부가 넘긴다 — 라우트 모듈(`admin.py`)이 `_ENV_FILE`을 소유하고 호출 시점에 읽어
넘기므로 테스트의 `src.api.routes.admin._ENV_FILE` 치환이 그대로 닿는다(D-337 ②).

계층: infrastructure.
"""

from __future__ import annotations

import os
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    """환경변수 파일을 파싱한다.

    Args:
        path: `.env` 파일 경로

    Returns:
        키-값 딕셔너리
    """
    if not path.exists():
        return {}

    settings: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 따옴표 제거
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            settings[key] = value
    return settings


def write_env_file(path: Path, settings: dict[str, str]) -> None:
    """환경변수 파일을 작성한다.

    기존 파일의 주석과 빈 줄을 보존하면서 값을 업데이트한다.

    Args:
        path: `.env` 파일 경로
        settings: 업데이트할 키-값 딕셔너리
    """
    existing_lines: list[str] = []
    updated_keys: set[str] = set()

    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing_lines = f.readlines()

    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in settings:
                new_lines.append(f"{key}={settings[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # 새로운 키 추가
    for key, value in settings.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def compose_env_content(
    path: Path,
    updates: dict[str, str],
    reset_keys: set[str],
) -> str:
    """`.env` 새 내용을 만든다 (주석·순서 보존 + 중복 키 정리 + reset 줄 제거).

    중복 등재된 키는 **첫 줄 위치에 마지막 유효값**을 남기고 이후 줄을 제거한다
    (`read_env_file`이 마지막 값을 채택하므로 실효값이 바뀌지 않는다).

    Args:
        path: `.env` 파일 경로
        updates: 새로 기록할 키-값
        reset_keys: 파일에서 제거할 키(기본값 복귀)

    Returns:
        새 파일 전체 내용
    """
    existing_lines: list[str] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing_lines = f.readlines()

    resolved = read_env_file(path)  # 중복 키는 마지막 값이 실효값
    occurrences: dict[str, int] = {}
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            occurrences[key] = occurrences.get(key, 0) + 1

    new_lines: list[str] = []
    written: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in reset_keys:
            continue
        if key in written:
            continue  # 중복 등재 — 첫 줄에 이미 기록했다
        written.add(key)

        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
        elif occurrences.get(key, 0) > 1:
            new_lines.append(f"{key}={resolved.get(key, '')}\n")
        else:
            new_lines.append(line)

    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    for key, value in updates.items():
        if key not in written:
            new_lines.append(f"{key}={value}\n")

    return "".join(new_lines)


def atomic_write_env(path: Path, content: str) -> None:
    """백업 → 임시 파일 → 원자 교체로 `.env`를 저장한다(실패 시 롤백).

    Args:
        path: `.env` 파일 경로
        content: 새 파일 전체 내용

    Raises:
        OSError: 파일 기록 실패 시(롤백 후 재전파)
    """
    original: str | None = None
    if path.exists():
        original = path.read_text(encoding="utf-8")
        backup = path.parent / (path.name + ".bak")
        backup.write_text(original, encoding="utf-8")

    tmp_file = path.parent / (path.name + ".tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, path)
    except Exception:
        if original is not None:
            path.write_text(original, encoding="utf-8")  # 롤백
        tmp_file.unlink(missing_ok=True)
        raise
