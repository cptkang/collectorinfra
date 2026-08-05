"""kind별 L1 보강 프로파일 (Plan 60 E6 §16 — 순수 도메인·stdlib만).

알람 kind(cpu/memory/disk/network/process/log)를 **어떤 L1 신호를 첨부할지 + 사람이
읽는 요지 제목**으로 결정적으로 매핑한다. 데이터 조회는 하지 않는다(순수 매핑·문자열
조립만) — 실제 수집은 application(alarm_context_enricher)이, 렌더링은 alarm_notifier가
담당한다.

설계 원칙:
    - 결정적(deterministic) 매핑 — LLM/외부 의존 없음. 완전 테스트 가능.
    - `enrichment_profile_map_csv`(config) 오버라이드로 kind→요지 제목을 운영 중 교체.
    - has_l1_data: 관제 L1 데이터 소스가 **확정된** kind만 True(disk/network=host-wide
      프로세스 재사용). 소스 미확정 kind(process/log)는 False → 요지 제목만 첨부(graceful).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnrichmentProfile:
    """kind별 L1 보강 프로파일 (불변 값 객체).

    Attributes:
        kind: 알람 종류("cpu"|"memory"|"disk"|"network"|"process"|"log").
        title: 사람이 읽는 요지 제목(통보 본문 헤더).
        signals: 첨부·서술할 L1 신호 라벨(사람이 읽는 튜플).
        has_l1_data: 관제 L1 데이터 소스 확정 여부. True면 host-wide 프로세스 스냅샷을
            참고로 첨부(disk/network), False면 요지 제목만(process/log — 소스 미확정).
    """

    kind: str
    title: str
    signals: tuple[str, ...]
    has_l1_data: bool


# kind → 기본 프로파일 (Plan 60 E6 §16.2). cpu/memory는 기존 ProcessSnapshot 표로
# 처리되므로 이 매핑의 title은 참고용(요지 조립·테스트용)이며 notifier는 신규 kind만 렌더한다.
_DEFAULT_PROFILES: dict[str, EnrichmentProfile] = {
    "cpu": EnrichmentProfile(
        kind="cpu",
        title="영향 프로세스 상위",
        signals=("CPU 상위 점유 프로세스",),
        has_l1_data=True,
    ),
    "memory": EnrichmentProfile(
        kind="memory",
        title="영향 프로세스 상위",
        signals=("메모리 상위 점유 프로세스",),
        has_l1_data=True,
    ),
    "disk": EnrichmentProfile(
        kind="disk",
        title="용량/마운트 상위 소비",
        signals=("호스트 프로세스 상위(참고)",),
        has_l1_data=True,
    ),
    "network": EnrichmentProfile(
        kind="network",
        title="연결/트래픽 상위",
        signals=("호스트 프로세스 상위(참고)",),
        has_l1_data=True,
    ),
    "process": EnrichmentProfile(
        kind="process",
        title="생존·재시작 이력",
        signals=("프로세스 생존·재시작 이력",),
        has_l1_data=False,
    ),
    "log": EnrichmentProfile(
        kind="log",
        title="조건 로그 시그니처",
        signals=("조건 로그 시그니처",),
        has_l1_data=False,
    ),
}


def parse_profile_map_csv(csv: str) -> dict[str, str]:
    """`enrichment_profile_map_csv` → {kind: 요지 제목 오버라이드} 파싱.

    형식: "disk=사용자 정의 제목,log=커스텀" (CSV, '=' 구분 — config CSV 패턴 계승).
    잘못된 항목(= 미포함/빈 kind/빈 title)은 무시한다. 빈 문자열이면 빈 dict.

    Args:
        csv: kind=title 오버라이드 CSV 문자열.

    Returns:
        {kind: title} 매핑(소문자 kind 키). 파싱 불가 시 빈 dict.
    """
    result: dict[str, str] = {}
    for pair in (csv or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        kind, _, title = pair.partition("=")
        kind = kind.strip().lower()
        title = title.strip()
        if kind and title:
            result[kind] = title
    return result


def resolve_profile(
    kind: Optional[str], override_titles: Optional[dict[str, str]] = None
) -> Optional[EnrichmentProfile]:
    """kind에 대응하는 보강 프로파일을 반환한다 (없으면 None).

    override_titles(parse_profile_map_csv 결과)에 kind가 있으면 요지 제목만 교체하고
    나머지(signals·has_l1_data)는 기본 프로파일을 유지한다.

    Args:
        kind: 알람 종류(대소문자 무시). None/미정의 kind면 None.
        override_titles: kind→제목 오버라이드 매핑(선택).

    Returns:
        EnrichmentProfile 또는 None(비대상 kind).
    """
    if not kind:
        return None
    base = _DEFAULT_PROFILES.get(kind.lower())
    if base is None:
        return None
    if override_titles:
        override = override_titles.get(base.kind)
        if override:
            return EnrichmentProfile(
                kind=base.kind,
                title=override,
                signals=base.signals,
                has_l1_data=base.has_l1_data,
            )
    return base


def build_summary(title: str, signals: tuple[str, ...]) -> str:
    """요지 문자열을 결정적으로 조립한다.

    형식: "{title} — {signals를 ' · '로 연결}". signals가 비면 title만 반환한다.
    프로파일(EnrichmentProfile)의 title/signals뿐 아니라 MessageEnrichment의 동일
    필드에도 재사용할 수 있도록 값(title·signals)만 받는다.

    Args:
        title: 요지 제목.
        signals: 서술 신호 라벨 튜플.

    Returns:
        사람이 읽는 요지 문자열.
    """
    if not signals:
        return title
    return f"{title} — {' · '.join(signals)}"
