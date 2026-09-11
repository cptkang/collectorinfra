"""설정 카탈로그 어댑터 — 축 후보 필터(F1)와 정합 검증(L1).

`src.api.settings_catalog`(D-129 SSOT)를 감싸 벤치마크가 쓰는 형태로 바꾼다.
**카탈로그를 다시 만들지 않는다** — 인트로스펙션 원천은 하나다.

두 가지를 낸다.

  F1 필터 : 335필드에서 "성능에 닿을 수 없는 것"을 사유와 함께 제외 (plans/93 §3.1)
  L1 정합 : `.env` / `.env.example` / 카탈로그 4자 대조 (plans/93 §3.5.2)

제외 사유를 반드시 문자열로 남기는 이유는 커버리지 장부가 **미측정에 사유를 강제**하기
때문이다(plans/93 §5.4.2 — 사유 없는 미측정은 리포트 생성 실패).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.api import settings_catalog as sc  # noqa: E402

#: 성능 축이 될 수 없는 그룹 — 질의 응답 경로 밖이다.
#: 알람 파이프라인은 워크로드 성격이 완전히 다르므로 별도 벤치마크 대상(plans/93 G-4).
_NON_QUERY_GROUPS: frozenset[str] = frozenset({"noise_gate", "alarm", "workb"})

#: 접속 대상·저장 정책을 가리키는 이름 꼬리. 동작 모드가 아니다.
_INFRA_SUFFIXES: tuple[str, ...] = (
    "_url", "_host", "_port", "_dir", "_path", "_file",
    "_retention_days", "_maxlen", "_key", "_token", "_password", "_secret",
)

#: 타입 자체가 접속 정보인 것.
_INFRA_TYPES: frozenset[str] = frozenset({"secret"})


@dataclass(frozen=True)
class KnobSpec:
    """벤치마크가 보는 설정 노브 1건."""

    env_key: str
    group_key: str
    field_name: str
    type: str
    enum_choices: Optional[list[str]]
    default: Optional[str]
    consumed: bool
    is_secret: bool
    is_sensitive: bool
    apply_mode: str
    description: Optional[str]

    @property
    def is_bool(self) -> bool:
        return self.type == "bool"

    @property
    def is_numeric(self) -> bool:
        return self.type in ("int", "float")


@dataclass(frozen=True)
class ExcludedKnob:
    """F1에서 제외된 노브와 그 **사유**."""

    env_key: str
    reason: str


@dataclass(frozen=True)
class IntegrityFinding:
    """L1 정합 검사 결과 1건."""

    env_key: str
    kind: str      # orphan | missing_example | undocumented | encenv_shadow_candidate
    detail: str

    @property
    def severity(self) -> str:
        """즉시 조치(high) 대상인지. plans/93 §5.4.1 1절 기준."""
        return "high" if self.kind == "orphan" else "medium"


def parse_commented_keys(path: Path) -> set[str]:
    """`# KEY=...` 형태로 **주석 처리된** 키를 뽑는다.

    `settings_catalog._parse_env_keys`는 주석 줄을 건너뛴다. 그래서 그것만 쓰면
    `.env.example`에 `# ACTIVE_DB_IDS=polestar_b0,...`처럼 **예시로 제공되지만 기본 비활성**인
    키가 전부 "누락"으로 잡힌다(2026-09-11 실측: 111건 중 다수가 이 유형).

    "아예 없는 키"와 "주석으로 안내된 선택 키"는 신규 설치자에게 전혀 다른 의미이므로 나눈다.
    """
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        body = stripped.lstrip("#").strip()
        if "=" not in body:
            continue
        candidate = body.split("=", 1)[0].strip()
        # 산문 주석("… ACTIVE_DB_IDS가 있으면 …")이 섞이지 않도록 env 키 꼴만 인정한다.
        if candidate and candidate.replace("_", "").isalnum() and candidate.isupper():
            keys.add(candidate)
    return keys


def load_knobs() -> list[KnobSpec]:
    """카탈로그 전건을 `KnobSpec`으로 옮긴다(값은 읽지 않는다)."""
    index = sc.field_index()
    return [
        KnobSpec(
            env_key=spec.env_key,
            group_key=spec.group_key,
            field_name=spec.field_name,
            type=spec.type,
            enum_choices=list(spec.enum_choices) if spec.enum_choices else None,
            default=spec.default,
            consumed=spec.consumed,
            is_secret=spec.is_secret,
            is_sensitive=spec.is_sensitive,
            apply_mode=spec.apply_mode,
            description=spec.description,
        )
        for spec in sorted(index.values(), key=lambda s: s.env_key)
    ]


def f1_exclusion_reason(knob: KnobSpec) -> Optional[str]:
    """F1 제외 사유를 돌려준다. 축 후보로 남으면 None.

    규칙 순서가 곧 사유의 우선순위다 — 여러 규칙에 걸려도 **먼저 맞는 하나**만 기록한다.
    (사유가 여러 개면 장부가 읽히지 않는다)
    """
    if not knob.consumed:
        return "미소비(UNCONSUMED_KEYS) — 읽는 코드가 없다"
    if knob.is_secret or knob.is_sensitive or knob.type in _INFRA_TYPES:
        return "크레덴셜·민감값 — 성능 축이 아니다"
    if knob.group_key in _NON_QUERY_GROUPS:
        return f"질의 경로 밖 그룹({knob.group_key}) — 별도 벤치마크 대상"
    lowered = knob.env_key.lower()
    for suffix in _INFRA_SUFFIXES:
        if lowered.endswith(suffix):
            return f"접속·저장 정책({suffix}) — 동작 모드가 아니다"
    return None


def f1_filter(knobs: Iterable[KnobSpec]) -> tuple[list[KnobSpec], list[ExcludedKnob]]:
    """축 후보와 제외 목록으로 가른다."""
    kept: list[KnobSpec] = []
    dropped: list[ExcludedKnob] = []
    for knob in knobs:
        reason = f1_exclusion_reason(knob)
        if reason is None:
            kept.append(knob)
        else:
            dropped.append(ExcludedKnob(env_key=knob.env_key, reason=reason))
    return kept, dropped


def check_integrity(
    *,
    env_path: Optional[Path] = None,
    example_path: Optional[Path] = None,
    knobs: Optional[list[KnobSpec]] = None,
) -> list[IntegrityFinding]:
    """L1 — `.env` / `.env.example` / 카탈로그 대조.

    Args:
        env_path: 기본은 저장소 `.env`. 테스트는 반드시 주입한다.
        example_path: 기본은 저장소 `.env.example`.
        knobs: 기본은 `load_knobs()`.

    Returns:
        불일치 목록. 빈 리스트면 정합.
    """
    knobs = knobs if knobs is not None else load_knobs()
    known = {k.env_key for k in knobs}
    env_keys = sc._parse_env_keys(env_path) if env_path else sc._parse_env_keys(_PROJECT_ROOT / ".env")
    example_keys = (
        sc._parse_env_keys(example_path) if example_path
        else sc._parse_env_keys(_PROJECT_ROOT / ".env.example")
    )
    encenv_keys = sc.encenv_managed_keys()

    findings: list[IntegrityFinding] = []

    # 고아 키 — 파일에는 있는데 코드가 읽지 않는다.
    # `.encenv` 관리 키는 별도 소스라 고아가 아니다.
    for key in sorted((env_keys | example_keys) - known - encenv_keys):
        where = []
        if key in env_keys:
            where.append(".env")
        if key in example_keys:
            where.append(".env.example")
        findings.append(IntegrityFinding(
            env_key=key,
            kind="orphan",
            detail=f"{'·'.join(where)}에만 있고 카탈로그에 없다 — 읽는 코드가 없거나 키 이름이 바뀌었다",
        ))

    # 누락 키 — 코드에는 있는데 예시 파일이 알려주지 않는다.
    # 주석으로 안내된 키(`# KEY=값`)는 "선택 키"이지 누락이 아니다.
    commented = (
        parse_commented_keys(example_path) if example_path
        else parse_commented_keys(_PROJECT_ROOT / ".env.example")
    )
    missing = known - example_keys - commented - encenv_keys
    for key in sorted(missing):
        findings.append(IntegrityFinding(
            env_key=key,
            kind="missing_example",
            detail=".env.example에 활성·주석 어느 쪽으로도 없다 — 신규 설치자가 이 키의 존재를 알 수 없다",
        ))

    # 설명 부재 — 웹UI 도움말과 `.env.example` 주석의 원천이 비어 있다.
    # 누락 키는 설명이 없는 것이 당연하므로 **중복 보고하지 않는다**(상위 사유가 누락이다).
    for knob in knobs:
        if knob.env_key in missing:
            continue
        if not (knob.description or "").strip():
            findings.append(IntegrityFinding(
                env_key=knob.env_key,
                kind="undocumented",
                detail="설명이 없다 — 웹UI 도움말·예시 주석이 비어 있다",
            ))

    # `.encenv` 가림 후보 — 값 비교는 L3가 한다. 여기서는 대상만 표시한다.
    for key in sorted(known & encenv_keys):
        findings.append(IntegrityFinding(
            env_key=key,
            kind="encenv_shadow_candidate",
            detail=".encenv가 관리하는 키 — .env 수정이 무효일 수 있다(L3에서 실효 확인)",
        ))

    return findings
