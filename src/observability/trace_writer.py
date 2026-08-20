"""실패 요청 단계 트레이스를 JSONL로 덤프한다 (D-141).

정상 요청은 **파일을 만들지 않는다**. `logs/trace/YYYY-MM-DD/<request_id>.jsonl`에
첫 줄 요약 헤더 + 단계별 한 줄로 기록한다.

SQL 원문은 담지 않는다 — 해시만 남기고 실제 문장은 `logs/sql/`에서 찾는다(중복 저장 회피).
민감 값은 기록 전에 가리며, 파일 권한은 `0600`으로 만든다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.observability import trace_collector as tc
from src.observability.levels import failure_triggers, severity_for
from src.security.data_masker import DataMasker

logger = logging.getLogger(__name__)

_MASK = "********"

#: 트레이스에 담는 문자열 하나의 최대 길이. 진단에는 앞부분이면 충분하고, 상한이 없으면
#: 두 가지가 동시에 터진다 — 마스킹 정규식이 입력 길이에 **이차로** 반응하고(20KB에 1.4초),
#: 파일·버퍼 크기가 예산을 넘는다. 30KB 질의 + 20단계 × 20KB payload로 덤프가 28초 걸린
#: 실측(2026-08-19)이 이 상한의 근거다.
_MAX_TEXT_LEN = 2000

#: 잘렸다는 사실을 남긴다 — 조용히 자르면 "여기서 끝났다"로 오독된다.
_TRUNCATED_SUFFIX = "…(생략)"

#: `request_id`는 파일명이 되므로 경로 구분자·상위 참조가 섞이면 안 된다.
#: 호출부는 내부 uuid를 넘기지만 `flush_if_failed`는 공개 함수이므로 여기서 막는다.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: 키 이름만으로 마스킹할 대상. 값 패턴에 안 걸리는 평문 비밀번호를 잡는다.
_SENSITIVE_KEY = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|credential|authorization)",
    re.IGNORECASE,
)

#: `scheme://user:password@host` 형태의 자격증명. DB 연결 실패 메시지에 자주 섞인다.
#: 호스트는 진단에 필요하므로 비밀번호 부분만 가린다.
#:
#: 스킴에 길이 상한을 둔 이유: 종전 `[\w+.-]*`는 무제한 greedy라 `://`가 없는 긴 문자열에서
#: **모든 시작 위치마다** 끝까지 확장했다가 되돌아왔다(20KB에 1.4초 — 2026-08-19 실측).
#: 실제 URL 스킴은 길어야 십수 자다.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]{0,32}://[^:/@\s]{1,256}:)(?P<pw>[^@/\s]{1,256})(?=@)")

#: 값 **전체**가 시크릿 형태인지 판정. `DataMasker`의 패턴을 재사용한다
#: (인스턴스가 아닌 클래스 속성이라 SecurityConfig 의존 없이 쓸 수 있다).
_SECRET_VALUE_PATTERNS = DataMasker.SENSITIVE_VALUE_PATTERNS

#: 문장 **안에 섞인** 자격증명. 전체 일치 검사만으로는
#: `"auth failed with sk-…"` 같은 에러 메시지의 키를 놓친다(2026-08-19 실측) —
#: 트레이스는 실패 시 반드시 기록되므로 그대로 두면 상시 노출 경로가 된다.
#:
#: 접두사가 뚜렷한 토큰류만 넣는다. Base64 일반형·주민번호·카드번호처럼 경계가 모호한
#: 패턴을 문장 검색으로 돌리면 정상 진단 정보(해시·ID·타임스탬프)를 지워
#: 트레이스의 존재 이유를 없앤다 — 그쪽은 값 전체 일치 검사로만 남긴다.
_INLINE_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,512}"),                                # OpenAI 계열 API 키
    re.compile(r"eyJ[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{1,4096}(?:\.[A-Za-z0-9_-]{1,4096})?"),  # JWT
    re.compile(r"AKIA[0-9A-Z]{16}"),                                      # AWS Access Key
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                                   # GitHub PAT
    re.compile(r"glpat-[A-Za-z0-9_-]{20,512}"),                           # GitLab PAT
    re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}"),                     # bcrypt 해시
]

#: payload에서 해시로 대체할 키. 원문은 logs/sql/에 이미 있다.
_HASHED_KEYS = ("sql", "generated_sql")


def _truncate(text: str) -> str:
    """문자열을 기록 상한까지 자른다. 잘린 경우 표시를 남긴다."""
    if len(text) <= _MAX_TEXT_LEN:
        return text
    return text[:_MAX_TEXT_LEN] + _TRUNCATED_SUFFIX


def _is_safe_request_id(request_id: str) -> bool:
    """파일명으로 써도 안전한 형태인지 판정한다(경로 탈출 방지)."""
    return bool(_SAFE_REQUEST_ID.match(request_id)) and request_id not in (".", "..")


def _sql_hash(sql: str) -> str:
    """SQL 식별용 짧은 해시. `logs/sql/`의 레코드와 대조할 때 쓴다."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def _mask_text(text: str) -> str:
    """문자열 안의 자격증명·시크릿을 가린다.

    3단계로 처리한다:
    1. URL 자격증명 — 비밀번호 부분만 치환(호스트는 진단에 필요하므로 남긴다)
    2. 문장 내 토큰 — 접두사가 뚜렷한 자격증명을 제자리 치환(문맥은 보존)
    3. 값 전체가 시크릿 형태 — 통째로 마스킹
    """
    # 마스킹 **전에** 자른다. 정규식이 입력 길이에 이차로 반응하므로 자르지 않고 넣으면
    # 긴 문자열 하나가 덤프 전체를 수 초씩 붙든다.
    text = _truncate(text)

    # `://`가 없으면 URL 패턴을 아예 시도하지 않는다(대부분의 로그 문자열이 여기 해당).
    masked = (
        _URL_CREDENTIALS.sub(lambda m: m.group("scheme") + _MASK, text)
        if "://" in text else text
    )

    for pattern in _INLINE_SECRET_PATTERNS:
        masked = pattern.sub(_MASK, masked)

    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.match(masked):
            return _MASK
    return masked


def _sanitize(value: Any, *, key: str = "") -> Any:
    """payload 값 하나를 기록 가능한 형태로 정리한다 (마스킹 + 중첩 재귀).

    dict는 `_sanitize_payload`로 되돌려 **중첩 깊이와 무관하게 같은 규칙**을 적용한다 —
    종전에는 top-level `sql`만 `sql_hash`로 바뀌고 중첩 `sql`은 키가 남아, 로그 소비자가
    `payload.outer.sql`을 원문으로 오인할 수 있었다.
    """
    if _SENSITIVE_KEY.search(key):
        return _MASK
    if isinstance(value, Mapping):
        return _sanitize_payload(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def _sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """payload를 정리한다. `sql` 계열 키는 값을 해시로 바꾸고 **키도 `sql_hash`로** 바꾼다.

    키까지 바꾸는 이유: 값만 해시로 두면 `sql` 키가 남아 원문처럼 읽힌다.
    원문은 `logs/sql/`에 있으므로 여기서는 대조용 해시만 남긴다.
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        key = str(k)
        if key in _HASHED_KEYS and isinstance(v, str):
            out["sql_hash"] = _sql_hash(v)
            continue
        out[key] = _sanitize(v, key=key)
    return out


def _node_path(steps: list) -> list[str]:
    """실행 경로를 노드 이름 순서로 요약한다.

    노드마다 enter/exit 두 단계가 쌓이므로 **연속 중복만** 접는다. 재시도로 같은
    노드에 다시 온 경우는 사이에 다른 노드가 끼므로 그대로 남아, 루프 횟수가 보인다
    (`query_generator → query_validator → query_generator`).
    """
    path: list[str] = []
    for s in steps:
        if not path or path[-1] != s.node:
            path.append(s.node)
    return path


def _write_atomic(path: Path, content: str) -> None:
    """0600 권한으로 파일을 쓴다.

    `open()` 이후 `chmod`하면 그 사이에 기본 권한으로 노출되는 창이 생기므로,
    `os.open`에 mode를 직접 준다.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)


def flush_if_failed(
    request_id: str,
    state: Mapping[str, Any] | None = None,
    *,
    project_root: str | Path | None = None,
    enabled: bool = True,
) -> Path | None:
    """요청이 실패로 판정되면 트레이스를 파일로 덤프한다.

    성공·실패 어느 쪽이든 **버퍼는 해제한다** — 요청이 끝났으므로 더 쌓을 이유가 없다.

    Args:
        request_id: 요청 추적 ID
        state: 최종 AgentState. **생략하면** 노드가 실행 중 관찰해 둔 신호를 쓴다 —
            덕분에 호출부(미들웨어·CLI)가 최종 state를 몰라도 된다.
        project_root: 프로젝트 루트 (None이면 cwd)
        enabled: False면 판정·덤프를 건너뛴다 (OBS_TRACE_ENABLED)

    Returns:
        기록한 파일 경로. 정상 요청·비활성·실패 시 None.
    """
    try:
        if not request_id:
            return None
        if not _is_safe_request_id(request_id):
            # 로그에 원본을 그대로 싣지 않는다(경로가 통째로 노출된다).
            logger.warning("트레이스 request_id 형식이 안전하지 않아 덤프를 건너뜁니다(%d자)",
                           len(request_id))
            tc.end_request(request_id)
            return None
        if not enabled:
            tc.end_request(request_id)
            return None

        steps = tc.steps_for(request_id)
        meta = tc.meta_for(request_id)
        if meta is None:
            return None  # 추적되지 않은 요청

        signals = state if state is not None else tc.observed_state(request_id)
        triggers = failure_triggers(signals)
        severity = severity_for(triggers)
        if severity is None:
            return None

        total_ms = tc.elapsed_ms_for(request_id)
        now = datetime.now()
        root = Path.cwd() if project_root is None else Path(project_root)
        out_dir = root / "logs" / "trace" / now.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)

        header = {
            "kind": "summary",
            "ts": now.astimezone().isoformat(timespec="milliseconds"),
            "request_id": request_id,
            "thread_id": meta.get("thread_id"),
            "user_query": _mask_text(str(meta.get("user_query", ""))),
            "severity": severity,
            "triggers": [t.value for t in triggers],
            "total_ms": round(total_ms, 1),
            "node_path": _node_path(steps),
            "step_count": len(steps),
        }

        lines = [json.dumps(header, ensure_ascii=False)]
        for s in steps:
            lines.append(json.dumps({
                "ts": now.astimezone().isoformat(timespec="milliseconds"),
                "request_id": request_id,
                "thread_id": meta.get("thread_id"),
                "step": s.step,
                "node": s.node,
                "level": s.level.value,
                "event": s.event,
                "elapsed_ms": round(s.elapsed_ms, 1),
                "reason": s.reason,
                "payload": _sanitize_payload(s.payload),
            }, ensure_ascii=False))

        out_path = out_dir / f"{request_id}.jsonl"
        _write_atomic(out_path, "\n".join(lines) + "\n")

        logger.info(
            "실패 트레이스 기록: %s (severity=%s triggers=%s 단계=%d)",
            out_path, severity, header["triggers"], len(steps),
        )
        return out_path

    except Exception as e:
        # 트레이스 기록 실패가 요청 처리를 깨뜨리면 안 된다. 다만 침묵시키지 않는다.
        logger.warning("실패 트레이스 기록 실패(request_id=%s): %s", request_id, e)
        return None
    finally:
        tc.end_request(request_id)
