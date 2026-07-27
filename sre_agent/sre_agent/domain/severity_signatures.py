"""중요도 2차 판정 시그니처 치트시트 + 판정 규칙 (Plan 02 §6, D-035 계승).

**순수 도메인 모듈**(stdlib만·외부 의존 없음). HolmesGPT 도구의 **원시 출력 문자열**에
대해 결정적 시그니처 매칭을 수행한다 — LLM의 서술이 아니라 raw 출력이 입력이므로 LLM
환각이 판정에 개입할 수 없다(결정적=판단·LLM=보조).

핵심 불변식 — **escalate-only**:
- 판정 결과 level은 **게이트 판정(baseline)보다 낮아질 수 없다**(하향·소급 변경 금지,
  Plan 01 §8 역방향 계약). `judge()`는 `max(baseline, ...)`로만 상향하며, 하향 경로가
  코드에 존재하지 않는다.

원격 배치(Plan 06/D-019):
- dmesg/journal 원문은 원격 2축(Prometheus + 폴스타 MCP)에서 수집되지 않는다.
  OOM 등 로그 시그니처는 Prometheus 카운터(예: `node_vmstat_oom_kill`) 대체 시그니처로
  매칭하고, 대체 신호도 없으면 "증거 불충분"(상향 보류) 경로를 따른다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 중요도 레벨 (게이트 severity 의미와 정합, Plan 02 §5.3) ──────────────
# 0=해소 · 1=주의 · 2=경고 · 3=심각.
LEVEL_NONE = 0
LEVEL_NOTICE = 1
LEVEL_WARNING = 2
LEVEL_CRITICAL = 3

LEVEL_NAMES: dict[int, str] = {
    LEVEL_NONE: "해소",
    LEVEL_NOTICE: "주의",
    LEVEL_WARNING: "경고",
    LEVEL_CRITICAL: "심각",
}


def clamp_level(value: int) -> int:
    """게이트 severity를 유효 레벨(0~3)로 클램프한다."""
    if value < LEVEL_NONE:
        return LEVEL_NONE
    if value > LEVEL_CRITICAL:
        return LEVEL_CRITICAL
    return value


@dataclass(frozen=True)
class Signature:
    """단일 OS 장애 시그니처(치트시트 항목).

    category: "strong"(상향 강) | "medium"(상향 중).
    source:   "log"(dmesg/journal 등 로그 원문) | "metric"(Prometheus 카운터 — 원격 대체).
    """

    name: str
    category: str
    source: str
    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Signal:
    """매칭된 시그니처와 그 근거(인용용 발췌)."""

    name: str
    category: str
    source: str
    label: str
    evidence: str  # 매칭된 라인 발췌(브리핑 인용용)


@dataclass(frozen=True)
class ImportanceVerdict:
    """중요도 2차 판정 결과.

    level: 최종 레벨명("해소"|"주의"|"경고"|"심각"). **항상 baseline 이상**(escalate-only).
    confidence: "high"(강 시그니처) | "medium"(중 시그니처) | "none"(무매칭).
    escalate: 게이트 baseline보다 **엄밀히 상향**됐는지(level > baseline).
    signals: 매칭된 시그니처 목록.
    evidence_insufficient: 원격 배치에서 로그·대체 카운터 모두 무매칭 → 상향 보류·증거 불충분.
    """

    level: str
    confidence: str
    escalate: bool
    signals: list[Signal] = field(default_factory=list)
    evidence_insufficient: bool = False


# ── 시그니처 치트시트 (Plan 02 §6 표 — OOM/soft lockup/hung task/FS 오류/conntrack/segfault) ──
# 로그 원문 시그니처(source="log")는 로컬 배치·향후 로그 스택 편입 시 그대로 재사용한다.
# 메트릭 대체 시그니처(source="metric")는 원격 배치(Prometheus 카운터)에서만 매칭한다.
# 메트릭은 카운터 명 뒤에 **0이 아닌 값**이 이어질 때만 매칭(값 0 나열 오탐 방지).
SIGNATURES: tuple[Signature, ...] = (
    # ── 강(strong) — 로그 원문 ───────────────────────────────
    Signature(
        "oom_kill", "strong", "log", "메모리 고갈(OOM Killer)",
        re.compile(r"out of memory: killed process|killed process \d+|invoked oom-killer", re.I),
    ),
    Signature(
        "fs_readonly", "strong", "log", "파일시스템 read-only 리마운트",
        re.compile(r"read-only file system|remounting filesystem read-only|ext4-fs error", re.I),
    ),
    Signature(
        "service_restart_loop", "strong", "log", "서비스 재시작 루프",
        re.compile(r"start-limit-hit|start-limit|start request repeated too quickly", re.I),
    ),
    Signature(
        "soft_lockup", "strong", "log", "CPU soft lockup",
        re.compile(r"soft lockup|bug: soft lockup", re.I),
    ),
    Signature(
        "hung_task", "strong", "log", "커널 hung task",
        re.compile(r"hung_task|blocked for more than \d+ seconds|task .+ blocked", re.I),
    ),
    Signature(
        "segfault", "strong", "log", "segfault / GPF",
        re.compile(r"segfault at |general protection fault", re.I),
    ),
    Signature(
        "conntrack_full", "strong", "log", "conntrack 테이블 고갈",
        re.compile(r"nf_conntrack: table full|conntrack table full|nf_conntrack: nf_conntrack", re.I),
    ),
    # ── 중(medium) — 로그 원문 ───────────────────────────────
    Signature(
        "fd_exhaustion", "medium", "log", "파일 디스크립터 고갈",
        re.compile(r"too many open files", re.I),
    ),
    Signature(
        "inode_or_disk_full", "medium", "log", "디스크/inode 고갈",
        re.compile(r"no space left on device|cannot create .+ no space", re.I),
    ),
    # ── 강/중 — 메트릭 대체(원격, Prometheus 카운터) ─────────
    Signature(
        "oom_kill_metric", "strong", "metric", "메모리 고갈(node_vmstat_oom_kill)",
        re.compile(r"node_vmstat_oom_kill\D*[1-9]", re.I),
    ),
    Signature(
        "fs_readonly_metric", "strong", "metric", "파일시스템 read-only(node_filesystem_readonly)",
        re.compile(r"node_filesystem_readonly\D*[1-9]", re.I),
    ),
)


def _first_matching_line(text: str, pattern: re.Pattern[str]) -> str:
    """패턴이 매칭된 첫 라인을 인용 근거로 반환한다(없으면 매칭 스팬 발췌)."""
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()[:200]
    match = pattern.search(text)
    return (match.group(0) if match else "")[:200]


def match_signatures(tool_outputs: list[str]) -> list[Signal]:
    """도구 원시 출력 문자열들에서 시그니처를 매칭해 Signal 목록을 반환한다.

    로그·메트릭 시그니처를 모두 스캔한다(배치 구분 없이 존재하는 신호는 채택).
    동일 시그니처가 여러 출력에서 매칭돼도 첫 매칭만 채택한다(중복 제거).
    """
    seen: set[str] = set()
    signals: list[Signal] = []
    for text in tool_outputs:
        if not text:
            continue
        for sig in SIGNATURES:
            if sig.name in seen:
                continue
            if sig.pattern.search(text):
                seen.add(sig.name)
                signals.append(
                    Signal(
                        name=sig.name,
                        category=sig.category,
                        source=sig.source,
                        label=sig.label,
                        evidence=_first_matching_line(text, sig.pattern),
                    )
                )
    return signals


def judge(
    gate_severity: int,
    tool_outputs: list[str],
    *,
    remote: bool = False,
) -> ImportanceVerdict:
    """중요도 2차 판정(escalate-only).

    baseline = 게이트 severity. 매칭된 강/중 시그니처로 상향만 한다(하향 없음).
    - 강(strong) 신호 → 후보 레벨 심각(3), 신뢰도 high.
    - 중(medium) 신호 → 후보 레벨 = min(심각, baseline+1), 신뢰도 medium.
    - level = max(baseline, 후보) — **절대 baseline 미만이 되지 않는다**.
    - escalate = level > baseline(엄밀 상향 시에만 True).

    remote=True + 무매칭 → evidence_insufficient=True(상향 보류·증거 불충분).
    로컬(remote=False) 무매칭은 "로그 확보됐으나 신호 없음(자기 복구)"이므로 불충분이 아니다.
    """
    baseline = clamp_level(gate_severity)
    signals = match_signatures(tool_outputs)

    strong = any(s.category == "strong" for s in signals)
    medium = any(s.category == "medium" for s in signals)

    proposed = baseline
    if strong:
        proposed = max(proposed, LEVEL_CRITICAL)
    elif medium:
        proposed = max(proposed, min(LEVEL_CRITICAL, baseline + 1))

    level = max(baseline, proposed)  # escalate-only 불변식(하향 경로 없음)
    escalate = level > baseline

    if strong:
        confidence = "high"
    elif medium:
        confidence = "medium"
    else:
        confidence = "none"

    evidence_insufficient = remote and not signals

    return ImportanceVerdict(
        level=LEVEL_NAMES[level],
        confidence=confidence,
        escalate=escalate,
        signals=signals,
        evidence_insufficient=evidence_insufficient,
    )


__all__ = [
    "LEVEL_NONE",
    "LEVEL_NOTICE",
    "LEVEL_WARNING",
    "LEVEL_CRITICAL",
    "LEVEL_NAMES",
    "clamp_level",
    "Signature",
    "Signal",
    "ImportanceVerdict",
    "SIGNATURES",
    "match_signatures",
    "judge",
]
