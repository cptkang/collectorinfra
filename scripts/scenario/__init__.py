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

__all__ = ["REPO_ROOT", "utf8_open"]

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
