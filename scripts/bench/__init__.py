"""벤치마크 하네스 (plans/93 · D-211).

설정 노브를 **측정으로** 판정하기 위한 도구 모음이다. 두 갈래로 나뉜다.

  트랙 T (여기 구현) : 설정 파일 전수 검증 — LLM을 한 번도 부르지 않는다
  트랙 A~C (후속)     : 성능 스위프·리포트·최적화 제안 — 실 LLM 필요

경계 규칙(`CAPABILITY-MAP-93.md`):
  * 신규 config 필드 0 · 신규 `enable_*` 플래그 0 (D-162)
  * `src/`를 수정하지 않는다 — 전량 `scripts/` 아래
  * `.env`·`.encenv`·`config.py`를 **읽기만** 한다

사용법은 `python -m scripts.bench --help` 또는 `plans/93`의 「실행 가이드 ① 퀵」 참조.
"""

from __future__ import annotations

__all__ = ["catalog", "probe", "validate", "axes", "report"]
