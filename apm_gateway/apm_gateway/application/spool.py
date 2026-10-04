"""결과 스풀 — 작업 기록·결과 청크·텍스트 부분의 파일 배치 (plans/134 W0-B N-18 ·
SPEC-apm-question-coverage §3.3).

```
<APM_SPOOL_DIR>/
  tmp/                       메모리 임계를 넘은 응답 본문 임시 파일
                             (어댑터가 파싱 뒤 지운다 · 기동 시 잔여 삭제)
  <job_id>/job.json          작업 기록(원자적 쓰기: 임시 파일 → rename)
  <job_id>/rows-00000.jsonl  결과 행 청크(JSON Lines · 청크마다 행 수·바이트·sha256)
  <job_id>/text-<part>.txt   원문 텍스트 부분(마스킹본)
```

들어오는 행·텍스트는 이미 자격증명 제거(어댑터)와 마스킹(도구)을 지난 값이다. 작업 ID·부분 이름은
형식을 검사한 값만 경로로 쓴다(경로 조작 차단). 본체는 이 경로를 모른다 — 작업 도구로만 읽는다.
디렉터리는 0700 · 파일은 0600으로 만든다(다른 계정이 결과를 읽지 못하게 — umask와 무관).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from apm_gateway.domain.jobs import is_job_id, is_part_name

logger = logging.getLogger(__name__)

RECORD = "job.json"
TMP_DIR = "tmp"


class SpoolCorruptedError(Exception):
    """청크 내용이 기록의 sha256과 다르다."""


_DIR_MODE = 0o700
_FILE_MODE = 0o600


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _mkdir(path: Path) -> None:
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)


def chunk_name(index: int) -> str:
    return f"rows-{index:05d}.jsonl"


class Spool:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def tmp_dir(self) -> Path:
        return self.root / TMP_DIR

    def ensure_root(self) -> None:
        """루트를 0700으로 만든다(이미 있으면 권한을 좁힌다)."""
        _mkdir(self.root)
        os.chmod(self.root, _DIR_MODE)

    def job_dir(self, job_id: str) -> Path:
        if not is_job_id(job_id):
            raise ValueError("작업 ID 형식이 아니다")
        return self.root / job_id

    # ── 작업 기록 ─────────────────────────────────────────

    def write_record(self, job_id: str, record: dict[str, Any]) -> None:
        directory = self.job_dir(job_id)
        if not self.root.exists():
            self.ensure_root()
        _mkdir(directory)
        data = json.dumps(record, ensure_ascii=False, default=str, indent=1).encode("utf-8")
        _atomic_write(directory / RECORD, data)

    def read_record(self, job_id: str) -> dict[str, Any] | None:
        """기록(없거나 읽을 수 없으면 None)."""
        try:
            path = self.job_dir(job_id) / RECORD
        except ValueError:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def job_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and is_job_id(p.name))

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def delete_results(self, job_id: str) -> None:
        """결과 조각만 지운다(취소·중단 — 부분 결과를 전체로 오인하지 않게). 기록은 남긴다."""
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            return
        for path in directory.iterdir():
            if path.name != RECORD:
                path.unlink(missing_ok=True)

    def clear_tmp(self) -> int:
        """응답 임시 파일 잔여를 지운다(기동 시 — 진행 중 응답이 없을 때만)."""
        if not self.tmp_dir.is_dir():
            return 0
        removed = 0
        for path in self.tmp_dir.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    # ── 결과 ──────────────────────────────────────────────

    def write_result(
        self,
        job_id: str,
        rows: list[Any],
        text_parts: dict[str, str],
        chunk_rows: int,
    ) -> dict[str, Any]:
        """행을 청크로, 텍스트를 부분 파일로 쓴다 → 산출물 매니페스트(SPEC §3.3 `artifact`)."""
        directory = self.job_dir(job_id)
        if not self.root.exists():
            self.ensure_root()
        _mkdir(directory)
        columns = list(dict.fromkeys(k for row in rows if isinstance(row, dict) for k in row))
        chunks: list[dict[str, Any]] = []
        step = max(1, int(chunk_rows))
        for index, start in enumerate(range(0, len(rows), step)):
            part = rows[start : start + step]
            data = "".join(
                json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
                for row in part
            ).encode("utf-8")
            _atomic_write(directory / chunk_name(index), data)
            chunks.append(
                {
                    "index": index,
                    "rows": len(part),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        parts: list[dict[str, Any]] = []
        for name, text in text_parts.items():
            if not is_part_name(name):
                raise ValueError(f"텍스트 부분 이름 형식 밖: {name!r}")
            data = str(text).encode("utf-8")
            _atomic_write(directory / f"text-{name}.txt", data)
            parts.append({"name": name, "bytes": len(data)})
        return {
            "total_rows": len(rows),
            "chunks": chunks,
            "chunk_rows": step,
            "columns": columns,
            "text_parts": parts,
        }

    def read_chunk(self, job_id: str, chunk: dict[str, Any]) -> list[Any]:
        """청크 1개의 행(기록의 sha256과 맞춰 본다)."""
        data = (self.job_dir(job_id) / chunk_name(int(chunk["index"]))).read_bytes()
        if hashlib.sha256(data).hexdigest() != chunk.get("sha256"):
            raise SpoolCorruptedError(f"결과 조각 {chunk['index']} 내용이 기록과 다르다")
        return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]

    def read_text(self, job_id: str, name: str) -> str:
        if not is_part_name(name):
            raise ValueError("텍스트 부분 이름 형식 밖")
        return (self.job_dir(job_id) / f"text-{name}.txt").read_text(encoding="utf-8")
