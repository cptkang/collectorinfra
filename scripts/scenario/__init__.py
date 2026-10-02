"""기능·성능 시나리오 자동 실행 하네스 (plans/94).

단일 진입점은 `python -m scripts.scenario` 하나다. 인자 없는 기본 동작은 무과금이며
(카탈로그 검증 -> 모의 실행 -> 예상치), 실 LLM 경로는 `--run` + `RUN_E2E=1` 뒤에만 열린다.
모르고 실행해도 돈이 나가지 않는 것이 D-127을 코드로 지키는 방식이다.

모듈 경계 (plans/94 §4.1):
  catalog      시나리오 YAML 로더·검증
  client       HTTP/SSE 클라이언트
  assertions   L1·L2·LR 단언 평가기 (결정적 · LLM 미사용)
  server       프로파일별 서버 기동·헬스 대기·사다리 확정 파싱·종료 (플랫폼 분기)
  mockserver   무과금 모의 서버 (LLM·DB 미호출)
  runner       실행 오케스트레이션·JSONL 적재·재개
  report       산출물 B — 리포트 생성기
  analyze      산출물 D — 분석기 (제안 문서만 생성 · 어떤 파일도 수정하지 않는다)
  import_docs  문서 145건 -> YAML 초안 파서 (1회성)

`src/`는 수정하지 않는다. 이 패키지는 운영 경로에 배선되지 않는다.
"""

from __future__ import annotations

__all__ = ["REPO_ROOT", "utf8_open", "run_capture"]

import locale
import subprocess
from pathlib import Path
from typing import IO, Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def utf8_open(path: Path, mode: str = "r") -> IO[Any]:
    """텍스트 파일을 연다 - encoding과 newline을 **함께** 명시한다.

    Windows에서 텍스트 모드 쓰기는 "\\n"을 "\\r\\n"으로 바꾼다. raw.jsonl이 CRLF가 되면
    바이트 비교와 재개(resume) 대조가 어긋난다. 기존 로거들은 encoding만 지정하고
    newline은 지정하지 않는다(sql_file_logger.py:130) - 같은 실수를 반복하지 않는다.
    (plans/94 부록 A.1-4 · 요구사항 W4)
    """
    path = Path(path)
    if any(flag in mode for flag in ("w", "a", "x")):
        path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, mode, encoding="utf-8", newline="\n")


def run_capture(cmd: list[str], timeout: float = 10.0) -> str:
    """외부 명령의 표준출력을 문자열로 받는다. **절대 예외를 던지지 않는다.**

    Windows 도구(powercfg·netsh)와 git 은 **콘솔 코드페이지**로 쓴다(한국어 Windows = cp949).
    그런데 이 하네스는 한글 출력을 위해 `PYTHONUTF8=1` 을 요구하고, 그러면 `text=True` 의
    기본 디코딩이 UTF-8 이 되어 cp949 바이트에서 UnicodeDecodeError 가 난다.

    더 나쁜 것은 **그 디코딩이 subprocess 의 reader 스레드에서 일어난다**는 점이다. 예외가
    거기서 터지므로 `except (OSError, subprocess.SubprocessError)` 로는 잡히지 않고,
    호출부가 받는 `completed.stdout` 은 조용히 `None` 이 된다. 그 다음 `.strip()` 이
    AttributeError 로 런 전체를 죽인다 (실측 2026-09-11 폐쇄망 Windows - 시나리오 한 건도
    실행되기 전에 사망).

    그래서 **바이트로 받아 직접 디코딩한다.** provenance 수집 실패가 런을 죽이면 안 된다.
    """
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    raw = completed.stdout or b""
    if not raw:
        return ""
    candidates = ["utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in candidates:
        candidates.append(preferred)
    for name in candidates + ["cp949"]:
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
