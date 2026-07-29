#!/usr/bin/env python3
"""프롬프트 정본 렌더 전후 diff 리포트 (Plan 67 R1-2).

폴스타 시스템 프롬프트를 두 가지로 만들어 비교한다:

    기존(폴백 상수) : ``POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`` — 지식이 문자열에 박혀 있던 형태
    정본 렌더       : ``render_system_template()`` — 지표 목록을 config/knowledge 카탈로그에서 렌더

diff 0이면 무해 전환(플래그 불요), 차이가 있으면 **전건 사유를 명시**해야 전환할 수 있다
(계획서 §6.2 리스크 3 — 프롬프트 바이트 변화는 LLM 출력 분포를 바꾼다).

사용법:
    python scripts/prompt_render_diff.py          # unified diff 출력
    python scripts/prompt_render_diff.py --ci     # 차이가 있으면 exit 1

프롬프트 지식 변경 이력 (정본 대조로 발견·교정한 건 — 전건 사유 명시):
    2026-07-29 Template A/B의 호스트명·IP 조회를 EAV(`cc.name='Hostname'`/`'IPaddress'`)에서
        직접 컬럼(`c.hostname`/`c.ipaddress`)으로 교정. 사유: 해당 EAV 속성은 실측상 비어 있어
        (D-058/D-061, 카탈로그도 두 속성을 제외) 예제대로 생성한 SQL은 호스트명·IP가 NULL이 됐다.
        Template B의 `hi` 서브쿼리는 그 빈 값이 조인 키(`ON svr.ipaddress = hi.ipaddress`)여서
        CPU 코어수·메모리 용량까지 함께 NULL이 되는 경로였다. R1 정본 일원화가 드러낸 사본 드리프트.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.db_adapters.polestar.prompts import (  # noqa: E402
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    render_system_template,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="프롬프트 정본 렌더 diff 리포트")
    parser.add_argument("--ci", action="store_true", help="차이가 있으면 exit 1")
    args = parser.parse_args()

    before = POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
    after = render_system_template()

    diff = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="폴백 상수(전)",
        tofile="정본 렌더(후)",
        n=2,
    ))

    print("=" * 70)
    print("폴스타 시스템 프롬프트 — 정본 렌더 전후 diff")
    print("=" * 70)
    print(f"전: {len(before)}자 / 후: {len(after)}자")
    if not diff:
        print("\n차이 0 — 바이트 동일(무해 전환)")
        return 0
    print(f"\n차이 {sum(1 for line in diff if line.startswith(('+', '-')) and not line.startswith(('+++', '---')))}행\n")
    sys.stdout.writelines(diff)
    return 1 if args.ci else 0


if __name__ == "__main__":
    sys.exit(main())
