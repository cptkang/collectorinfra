"""미들웨어 OS 근사 식별 (Plan 78 W7-1 · D-168).

**무엇을 하나.** 프로세스 목록(`ps -eo pid,args` 상당)만 받아 **무엇이 돌고 있는지**를 판정한다.
프로세스를 **수집하지는 않는다** — 수집·원격 실행은 `sre_agent` 소관이고 이 모듈은 순수 판정이다
(D-118 경계 · 양방향 import 0).

**왜 OS 근사인가.** 미들웨어 장애의 정본 소스는 APM(W7-2)이지만 도입 시점·벤더가 미정이다(R-11).
OS 근사는 **선행조건이 0**이라 지금 착수할 수 있고, **APM 도입 후에도 존치**한다 —
APM 에이전트가 붙지 않는 레거시·경량 미들웨어가 남고, **APM 자체가 죽었을 때** 볼 수단이 필요하다
(관측 도구의 장애를 관측 도구로만 진단할 수 없다 · §4.7.1).

**왜 결정적인가.** 판정에 LLM을 쓰지 않는다(D-035). 같은 입력에 같은 출력이어야 조사 결과를
신뢰할 수 있고, 미식별이 *"모델이 못 맞혔다"* 가 아니라 *"규칙에 없다"* 로 귀결되어야
규칙을 고쳐 개선할 수 있다.

**규칙은 코드가 아니라 선언 파일에 있다** — `config/middleware_signatures.yaml`
(하네스 문서 표 29 G: *"정책이 코드가 아닌 선언적 파일에 있는가"*). 새 미들웨어 추가에
코드 변경이 필요하면 그것은 설계 실패다.

계층: domain (`scripts/arch_check.py` `src.domain` 매핑).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml
from pydantic import BaseModel, Field

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "middleware_signatures.yaml"


class MiddlewareSignature(BaseModel):
    """선언 파일의 규칙 한 줄. 코드가 아니라 데이터다."""

    kind: str
    match_any: list[str] = Field(default_factory=list)
    match_regex: Optional[str] = None
    exclude_any: list[str] = Field(default_factory=list)
    instance_from: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    ports: list[int] = Field(default_factory=list)


class MatchedMiddleware(BaseModel):
    """식별된 미들웨어 하나."""

    pid: int
    kind: str
    instance: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    cmdline: str = ""


class UnmatchedProcess(BaseModel):
    """식별하지 못한 프로세스 — **사유를 반드시 남긴다**(빈 결과 금지)."""

    pid: int
    cmdline: str
    reason: str


class MiddlewareScan(BaseModel):
    """판정 결과 전체."""

    matched: list[MatchedMiddleware] = Field(default_factory=list)
    unmatched: list[UnmatchedProcess] = Field(default_factory=list)
    note: str = ""


@lru_cache(maxsize=1)
def _raw_config() -> dict[str, Any]:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


def load_signatures() -> list[MiddlewareSignature]:
    """선언 파일에서 규칙을 읽는다."""
    data = _raw_config()
    return [MiddlewareSignature(**s) for s in data.get("signatures", [])]


def global_excludes() -> list[str]:
    """모든 규칙에 공통 적용되는 제외 패턴 — 조사 명령 자신이 잡히는 것을 막는다."""
    return list(_raw_config().get("global_exclude", []))


def _first_group(pattern: Optional[str], text: str) -> str:
    if not pattern:
        return ""
    m = re.search(pattern, text)
    if not m:
        return ""
    return m.group(1) if m.groups() else m.group(0)


def _is_candidate(sig: MiddlewareSignature, cmdline: str) -> bool:
    if any(x in cmdline for x in sig.exclude_any):
        return False
    if any(x in cmdline for x in sig.match_any):
        return True
    return bool(sig.match_regex and re.search(sig.match_regex, cmdline))


def identify(
    processes: Sequence[dict],
    *,
    signatures: Optional[Sequence[MiddlewareSignature]] = None,
) -> MiddlewareScan:
    """프로세스 목록에서 미들웨어를 식별한다. 판정은 100% 결정적이다(D-035).

    Args:
        processes: `[{"pid": int, "cmdline": str}, ...]` — 수집은 호출자(`sre_agent`) 소관.
        signatures: 규칙 목록. 미지정 시 선언 파일에서 읽는다(테스트·확장용 주입점).

    Returns:
        식별 결과. **미식별도 사유와 함께 반환**한다 — 빈 결과는 "미들웨어 없음"과
        "판정 실패"를 구분하지 못한다.
    """
    sigs = list(signatures) if signatures is not None else load_signatures()
    excludes = global_excludes()

    if not processes:
        return MiddlewareScan(
            note="입력 프로세스 목록이 비어 있습니다 — 수집이 실패했는지 확인이 필요합니다."
        )

    matched: list[MatchedMiddleware] = []
    unmatched: list[UnmatchedProcess] = []

    for proc in processes:
        pid = int(proc.get("pid", 0))
        cmdline = str(proc.get("cmdline", ""))

        if any(x in cmdline for x in excludes):
            unmatched.append(UnmatchedProcess(
                pid=pid, cmdline=cmdline,
                reason="global_exclude 패턴에 해당(조사 명령 자신으로 판단)",
            ))
            continue

        hit = next((s for s in sigs if _is_candidate(s, cmdline)), None)
        if hit is None:
            unmatched.append(UnmatchedProcess(
                pid=pid, cmdline=cmdline,
                reason="일치하는 선언 규칙 없음 — config/middleware_signatures.yaml에 규칙 추가 필요",
            ))
            continue

        matched.append(MatchedMiddleware(
            pid=pid,
            kind=hit.kind,
            instance=_first_group(hit.instance_from, cmdline),
            attributes={
                name: value
                for name, pat in hit.attributes.items()
                if (value := _first_group(pat, cmdline))
            },
            cmdline=cmdline,
        ))

    return MiddlewareScan(
        matched=matched,
        unmatched=unmatched,
        note=f"규칙 {len(sigs)}종으로 판정 · 식별 {len(matched)} · 미식별 {len(unmatched)}",
    )
