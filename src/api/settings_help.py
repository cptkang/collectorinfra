"""설정 옵션 상세 도움말 — 운영자용 "이 값을 이렇게 두면 이렇게 동작한다" (D-191).

관리자 화면의 설정 행을 클릭하면 오른쪽 패널에 뜨는 내용을 만든다. 카탈로그
(`settings_catalog`)가 *무엇을 설정할 수 있는가*를 답한다면, 여기는 *그렇게 설정하면
무슨 일이 벌어지는가*를 답한다.

원천은 2계층이다.

1. **큐레이션**(`config/settings_help/{group}.yaml`) — 사람이 쓴 사례·성능·안정성 설명.
   운영 지식이 필요한 설명은 코드가 지어낼 수 없으므로 정본 YAML에 둔다.
2. **자동 파생** — 큐레이션이 없는 키는 카탈로그 메타(타입·기본값·선택지·반영 시점·
   오버라이드·소비 여부)와 키 이름의 트레이드오프 축에서 **결정적으로** 생성한다.
   LLM을 쓰지 않는다 — 같은 입력이면 항상 같은 문장이다.

「반영·주의」 절(`operational`)은 두 경우 모두 **항상 자동 생성**한다. 반영 시점과
오버라이드는 카탈로그가 아는 사실이고, YAML에 손으로 옮겨 적으면 어긋나기 때문이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel

from src.api.settings_catalog import (
    GROUP_TITLES,
    FieldSpec,
    field_index,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HELP_DIR = _PROJECT_ROOT / "config" / "settings_help"


# ──────────────────────────────────────────────
# 응답 모델
# ──────────────────────────────────────────────


class HelpOption(BaseModel):
    """값 하나(또는 값 구간)를 골랐을 때의 동작."""

    value: str                      # "true" · "gemini" · "크게 (600 이상)"
    label: Optional[str] = None     # 화면 표시명 ("운영 기본" 등)
    effect: str                     # 이 값이면 무엇이 어떻게 동작하는가
    is_default: bool = False
    is_current: bool = False


class SettingHelp(BaseModel):
    """설정 1건의 상세 도움말 — 오른쪽 패널 1화면."""

    env_key: str
    group_key: str
    group_title: str
    source: str                     # "curated" | "derived"
    summary: str                    # 한 줄 요약
    behavior: Optional[str] = None  # 무엇을 제어하는가 (동작 서술)
    options: list[HelpOption] = []  # 값별 동작 사례
    example: Optional[str] = None   # 구체 사례 (시나리오 서술)
    performance: Optional[str] = None
    stability: Optional[str] = None
    recommendation: Optional[str] = None
    caveats: list[str] = []
    operational: list[str] = []     # 반영 시점·오버라이드·소비 여부 (항상 자동)
    related: list[str] = []         # 함께 보는 키
    references: list[str] = []      # 근거 문서


# ──────────────────────────────────────────────
# 큐레이션 YAML 로더
# ──────────────────────────────────────────────


@lru_cache(maxsize=1)
def curated_index() -> dict[str, dict[str, Any]]:
    """`config/settings_help/*.yaml`을 병합해 env_key → 큐레이션 dict로 돌려준다.

    파일이 없거나 깨져도 예외를 올리지 않는다 — 도움말 부재가 설정 화면 전체를
    막으면 안 되기 때문이다. 대신 경고를 남기고 자동 파생으로 내려간다.
    """
    merged: dict[str, dict[str, Any]] = {}
    if not _HELP_DIR.exists():
        return merged

    for path in sorted(_HELP_DIR.glob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:  # noqa: BLE001 — 어떤 이유든 화면을 막지 않는다
            logger.warning("설정 도움말 로드 실패 %s: %s", path.name, e)
            continue

        settings = data.get("settings")
        if not isinstance(settings, dict):
            logger.warning("설정 도움말 %s: 'settings' 매핑이 없습니다", path.name)
            continue

        for env_key, entry in settings.items():
            if not isinstance(entry, dict):
                logger.warning("설정 도움말 %s: %s 항목이 매핑이 아닙니다", path.name, env_key)
                continue
            if env_key in merged:
                logger.warning("설정 도움말 %s: %s 중복 정의 — 뒤 파일이 이깁니다", path.name, env_key)
            merged[env_key] = entry

    return merged


def reset_help_cache() -> None:
    """YAML 캐시를 비운다 (테스트·설정 리로드용)."""
    curated_index.cache_clear()


# ──────────────────────────────────────────────
# 자동 파생 — 숫자형 트레이드오프 축
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class Axis:
    """숫자형 설정이 놓인 트레이드오프 축.

    키 이름으로 판정한다. 축을 모르면 `None`이며, 그때는 방향 설명 없이
    타입·기본값만 안내한다(지어내지 않는다).
    """

    name: str
    raise_effect: str
    lower_effect: str
    performance: str
    stability: str


_AXES: tuple[tuple[tuple[str, ...], Axis], ...] = (
    (
        ("_TIMEOUT", "_TIMEOUT_SECONDS", "TIMEOUT_SECONDS", "_TIMEOUT_SEC"),
        Axis(
            name="시간 예산",
            raise_effect="느린 응답까지 기다려 주므로 일시적으로 굼뜬 대상에도 성공합니다.",
            lower_effect="빨리 포기하므로 화면 반응이 빨라지지만, 정상이지만 느린 요청까지 실패로 끊깁니다.",
            performance="길게 잡으면 실패를 확정하는 데까지 그만큼 걸립니다 — 동시 처리 슬롯이 오래 점유돼 다른 요청이 밀립니다.",
            stability="짧게 잡으면 대상이 잠깐 느려진 것뿐인데도 실패로 처리돼, 재시도가 없는 경로에서는 결과가 통째로 비게 됩니다.",
        ),
    ),
    (
        ("_RETRY", "_RETRIES", "_RETRY_COUNT", "_MAX_RETRY", "_MAX_RETRIES",
         "MAX_REPLAN", "_RECURSION_LIMIT"),
        Axis(
            name="재시도 예산",
            raise_effect="일시적인 오류나 형식 오류를 스스로 고쳐 다시 시도하므로 최종 성공률이 올라갑니다.",
            lower_effect="첫 실패에서 바로 끝나므로 빠르게 실패를 알 수 있지만, 한 번만 삐끗해도 답이 나오지 않습니다.",
            performance="재시도는 앞 단계를 통째로 다시 밟습니다 — LLM 호출이 반복되므로 최악의 응답 시간과 비용이 재시도 횟수에 비례해 늘어납니다.",
            stability="크게 잡으면 실패가 확정되기까지 사용자가 오래 기다립니다. 근본 원인이 고정적(예: 없는 컬럼)이면 재시도는 전부 같은 이유로 실패하므로 시간만 씁니다.",
        ),
    ),
    (
        ("_MAX_ROWS", "_MAX_RESULTS", "_LIMIT", "_TOP_N", "_MAX_ITEMS",
         "_MAX_TARGETS", "_MAX_CANDIDATES", "_MAX_LINES", "_MAX_SAMPLES"),
        Axis(
            name="조회·처리 상한",
            raise_effect="한 번에 더 많이 가져오므로 필요한 행이 잘려 나갈 일이 줄어듭니다.",
            lower_effect="가볍고 빠르지만, 상한에서 잘린 뒤쪽 데이터는 분석 근거에서 빠집니다.",
            performance="크게 잡을수록 DB 부하·전송량·메모리가 함께 늘고, 결과가 LLM 프롬프트로 들어가는 경로에서는 토큰 비용도 비례해 증가합니다.",
            stability="너무 크면 한 건의 광역 질의가 DB와 응답 시간을 통째로 잡아먹습니다. 너무 작으면 '데이터가 없다'가 아니라 '잘렸다'인데 그렇게 보이지 않습니다.",
        ),
    ),
    (
        ("_THRESHOLD", "_MIN_SCORE", "_CUTOFF", "_MIN_CONFIDENCE"),
        Axis(
            name="판정 임계",
            raise_effect="기준이 엄격해져 확실한 것만 통과합니다 — 잘못 걸리는 경우(오탐)가 줄어듭니다.",
            lower_effect="기준이 느슨해져 애매한 것도 통과합니다 — 놓치는 경우(미탐)가 줄어듭니다.",
            performance="임계 자체는 비용이 거의 없지만, 느슨하게 잡아 통과량이 늘면 뒤따르는 처리(LLM 호출·조회)가 함께 늘어납니다.",
            stability="오탐과 미탐을 동시에 줄이는 값은 없습니다. 어느 쪽 실수가 더 비싼지를 정하고 그 반대편으로 옮기는 것이 이 값의 용도입니다.",
        ),
    ),
    (
        ("_TTL", "_TTL_SECONDS", "_TTL_DAYS", "_TTL_HOURS", "_CACHE_TTL", "_CACHE_TTL_SECONDS"),
        Axis(
            name="캐시 수명",
            raise_effect="한 번 계산한 결과를 오래 재사용하므로 반복 조회가 빨라지고 대상 시스템 부하가 줄어듭니다.",
            lower_effect="원본이 바뀌면 금방 반영되지만, 그만큼 자주 다시 조회·계산합니다.",
            performance="길게 잡을수록 적중률이 올라 응답이 빨라집니다. 짧게 잡으면 캐시가 사실상 없는 것과 같아져 매 요청이 원본을 칩니다.",
            stability="길게 잡으면 원본이 바뀐 뒤에도 낡은 값이 그 시간만큼 계속 보입니다 — 설정을 바꿨는데 화면이 그대로인 원인이 대개 여기입니다.",
        ),
    ),
    (
        ("_WINDOW", "_WINDOW_SECONDS", "_LOOKBACK", "_LOOKBACK_DAYS", "_LOOKBACK_HOURS"),
        Axis(
            name="관측 시간창",
            raise_effect="더 긴 구간을 근거로 삼으므로 표본이 늘어 판단이 덜 흔들립니다.",
            lower_effect="최근 상황에 민감하게 반응하지만, 표본이 적어 우연한 한두 건에 판단이 좌우됩니다.",
            performance="넓힐수록 조회 범위와 메모리 보관량이 함께 늘어납니다 — 이력 조회가 붙는 경로에서는 응답 시간에 그대로 드러납니다.",
            stability="지나치게 넓히면 이미 끝난 상황이 현재 판단에 계속 섞여, 복구된 뒤에도 한동안 옛 상태로 취급됩니다.",
        ),
    ),
    (
        ("_INTERVAL", "_INTERVAL_SECONDS", "_PERIOD_SECONDS", "_EVERY_SECONDS"),
        Axis(
            name="실행 주기",
            raise_effect="덜 자주 실행하므로 자원 소모와 대상 시스템 부하가 줄어듭니다.",
            lower_effect="자주 실행해 상태 변화를 빨리 따라잡지만, 그만큼 자원을 계속 씁니다.",
            performance="짧게 잡으면 배경 작업이 상시 도는 상태가 됩니다 — 앞단 요청 처리와 자원을 나눠 쓰게 됩니다.",
            stability="길게 잡으면 그 주기만큼 상태 반영이 늦습니다. 주기보다 짧게 끝나지 않는 작업이면 실행이 겹칠 수 있습니다.",
        ),
    ),
    (
        ("_RATIO", "_RATE", "_PERCENT", "_FRACTION"),
        Axis(
            name="비율",
            raise_effect="기준 대비 더 큰 몫을 허용합니다.",
            lower_effect="기준 대비 더 작은 몫으로 조입니다.",
            performance="비율이 무엇에 대한 몫인지에 따라 다릅니다 — 아래 「무엇을 제어하는가」의 대상과 함께 읽으십시오.",
            stability="0과 1(또는 100)의 양 끝값은 기능을 끄거나 항상 켜는 것과 같은 뜻이 되는 경우가 많습니다.",
        ),
    ),
    (
        ("_CONCURRENCY", "_MAX_WORKERS", "_POOL_SIZE", "_PARALLELISM", "_MAX_CONCURRENT"),
        Axis(
            name="동시 실행 폭",
            raise_effect="여러 건을 동시에 처리해 전체 처리량과 체감 속도가 올라갑니다.",
            lower_effect="한 번에 적게 처리하므로 대상 시스템에 부담을 덜 주지만 대기열이 길어집니다.",
            performance="대상(DB·외부 API)이 감당하는 선을 넘으면 늘려도 빨라지지 않고 오히려 전체가 느려집니다 — 병목은 이쪽이 아니라 대상 쪽입니다.",
            stability="크게 잡으면 커넥션 고갈·상대 서버 과부하로 이어질 수 있습니다. 폐쇄망 대상 시스템에는 보수적으로 잡는 편이 안전합니다.",
        ),
    ),
    (
        ("_DAYS", "_RETENTION_DAYS", "_MAX_AGE_DAYS"),
        Axis(
            name="보존 기간",
            raise_effect="오래된 기록까지 남겨 두므로 나중에 거슬러 올라가 조사할 수 있습니다.",
            lower_effect="오래된 기록을 일찍 지워 저장 공간을 아낍니다.",
            performance="길게 잡을수록 저장 용량과 조회 대상 건수가 늘어납니다.",
            stability="짧게 잡으면 사후 조사에 필요한 근거가 이미 사라진 뒤일 수 있습니다 — 감사·규정 요건이 있다면 그쪽이 하한입니다.",
        ),
    ),
)


def _axis_for(env_key: str) -> Optional[Axis]:
    """키 이름으로 트레이드오프 축을 판정한다 (모르면 None)."""
    for suffixes, axis in _AXES:
        for suffix in suffixes:
            if env_key.endswith(suffix) or suffix in env_key:
                return axis
    return None


# ──────────────────────────────────────────────
# 자동 파생 — 「반영·주의」(큐레이션 여부와 무관하게 항상 생성)
# ──────────────────────────────────────────────


_APPLY_MODE_TEXT: dict[str, str] = {
    "immediate": "저장하면 다음 요청부터 바로 적용됩니다 — 재시작도, 리로드도 필요 없습니다.",
    "reload": "저장한 뒤 [설정 리로드] 버튼을 눌러야 적용됩니다. 리로드는 그래프를 다시 만들고 캐시를 비우므로, 처리 중이던 요청은 이전 설정으로 끝까지 갑니다.",
    "restart": "저장만으로는 적용되지 않습니다 — 서버를 재시작해야 반영됩니다. 이 값은 기동할 때 한 번 읽혀 그대로 굳기 때문입니다.",
}


def _operational_notes(spec: FieldSpec, item: Optional[dict[str, Any]] = None) -> list[str]:
    """반영 시점·오버라이드·소비 여부를 문장으로 만든다.

    카탈로그가 이미 아는 사실만 옮긴다 — YAML에 손으로 적으면 코드와 어긋나므로
    큐레이션 항목에도 이 절은 자동으로 붙인다.
    """
    notes: list[str] = [_APPLY_MODE_TEXT.get(spec.apply_mode, _APPLY_MODE_TEXT["restart"])]

    if spec.is_secret:
        notes.append(
            "이 항목은 `.encenv`에서 관리하는 시크릿입니다. 이 화면에서 `.env`를 고쳐도 "
            "값이 바뀌지 않습니다 — `.encenv`를 직접 편집한 뒤 재시작해야 합니다."
        )

    override = (item or {}).get("override")
    if override == "os":
        notes.append(
            "지금 OS 환경변수가 이 값을 덮어쓰고 있습니다. `.env`보다 OS 환경변수가 우선하므로 "
            "여기서 저장해도 적용되지 않습니다 — 실행 환경의 환경변수를 먼저 걷어내십시오."
        )
    elif override == "encenv":
        notes.append(
            "지금 `.encenv` 값이 이 값을 덮어쓰고 있습니다. 여기서 저장해도 적용되지 않습니다."
        )

    if not spec.consumed:
        notes.append(
            "현재 코드가 이 값을 읽지 않습니다(미소비). 무엇으로 바꾸든 동작은 달라지지 않습니다 — "
            "과거에 쓰였거나 앞으로 쓸 자리만 남아 있는 항목입니다."
        )

    return notes


# ──────────────────────────────────────────────
# 자동 파생 — 본문
# ──────────────────────────────────────────────


_BOOL_LABELS: dict[str, tuple[str, str]] = {
    "true": ("켬", "이 기능이 동작합니다."),
    "false": ("끔", "이 기능이 동작하지 않습니다."),
}


def _derive_options(spec: FieldSpec, current: Optional[str]) -> list[HelpOption]:
    """타입별로 "이 값을 고르면" 목록을 만든다."""
    default = spec.default

    def _mk(value: str, label: Optional[str], effect: str) -> HelpOption:
        return HelpOption(
            value=value,
            label=label,
            effect=effect,
            is_default=(default is not None and value == default),
            is_current=(current is not None and value == current),
        )

    if spec.type == "bool":
        return [
            _mk("true", "켬", "기능이 동작합니다."),
            _mk("false", "끔", "기능이 동작하지 않습니다 — 이 기능이 없던 것과 같은 흐름으로 처리됩니다."),
        ]

    if spec.type == "tristate":
        return [
            _mk("(미설정)", "자동", "다른 설정 상태를 보고 켤지 끌지 스스로 정합니다. 환경에 따라 결과가 달라지므로, 고정하려면 아래 둘 중 하나를 명시하십시오."),
            _mk("true", "항상 켬", "환경과 무관하게 항상 동작합니다."),
            _mk("false", "항상 끔", "환경과 무관하게 항상 동작하지 않습니다."),
        ]

    if spec.type == "enum" and spec.enum_choices:
        options = [
            _mk(choice, None, f"`{choice}` 방식으로 동작합니다.")
            for choice in spec.enum_choices
        ]
        if spec.optional:
            options.insert(0, _mk("(미설정)", "미설정", "값을 지정하지 않습니다 — 이 항목을 쓰지 않거나 코드 기본 흐름을 따릅니다."))
        return options

    axis = _axis_for(spec.env_key) if spec.type in ("int", "float") else None
    if axis:
        return [
            _mk("값을 올리면", None, axis.raise_effect),
            _mk("값을 내리면", None, axis.lower_effect),
        ]

    return []


def _derive_summary(spec: FieldSpec) -> str:
    """한 줄 요약 — 설명이 있으면 그 첫 문장, 없으면 타입으로 최소 서술."""
    if spec.description:
        first = spec.description.strip().split(". ")[0].strip()
        return first if first.endswith(".") or len(first) < 200 else spec.description.strip()

    type_text = {
        "bool": "켜고 끄는 항목입니다.",
        "tristate": "켬·끔·자동 중에서 고르는 항목입니다.",
        "enum": "정해진 선택지 중 하나를 고르는 항목입니다.",
        "int": "정수 값을 지정하는 항목입니다.",
        "float": "실수 값을 지정하는 항목입니다.",
        "csv": "쉼표로 구분한 목록을 지정하는 항목입니다.",
        "json_list": "JSON 배열 형식의 목록을 지정하는 항목입니다.",
        "secret": "비밀 값(시크릿)입니다.",
    }.get(spec.type, "값을 지정하는 항목입니다.")
    return f"{GROUP_TITLES.get(spec.group_key, spec.group_key)} 영역의 설정으로, {type_text}"


def _derive_help(spec: FieldSpec, item: Optional[dict[str, Any]]) -> SettingHelp:
    """큐레이션이 없는 키의 도움말을 카탈로그 메타에서 결정적으로 만든다."""
    current = (item or {}).get("effective_value")
    axis = _axis_for(spec.env_key) if spec.type in ("int", "float") else None

    behavior_parts: list[str] = []
    if spec.description:
        behavior_parts.append(spec.description.strip())
    if spec.section:
        behavior_parts.append(f"「{spec.section}」 구획에 속한 설정입니다.")
    if spec.default is not None:
        behavior_parts.append(f"지정하지 않으면 `{spec.default}`로 동작합니다.")
    else:
        behavior_parts.append("코드 기본값이 없어, 지정하지 않으면 이 항목을 쓰지 않습니다.")

    return SettingHelp(
        env_key=spec.env_key,
        group_key=spec.group_key,
        group_title=GROUP_TITLES.get(spec.group_key, spec.group_key),
        source="derived",
        summary=_derive_summary(spec),
        behavior=" ".join(behavior_parts),
        options=_derive_options(spec, current),
        performance=axis.performance if axis else None,
        stability=axis.stability if axis else None,
        recommendation=None,
        caveats=[],
        operational=_operational_notes(spec, item),
        related=[],
        references=[],
    )


# ──────────────────────────────────────────────
# 큐레이션 병합
# ──────────────────────────────────────────────


def _as_str_list(raw: Any) -> list[str]:
    """YAML의 문자열 또는 문자열 목록을 목록으로 정규화한다."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [str(raw)]


def _curated_options(
    raw: Any,
    spec: FieldSpec,
    current: Optional[str],
) -> list[HelpOption]:
    """YAML `options`를 HelpOption으로 변환한다.

    `is_default`/`is_current`는 YAML이 아니라 카탈로그 실측값으로 채운다 —
    기본값이 코드에서 바뀌었는데 YAML이 옛 표시를 붙들고 있는 사태를 막는다.
    """
    if not isinstance(raw, list):
        return []

    options: list[HelpOption] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("value", "")).strip()
        effect = str(entry.get("effect", "")).strip()
        if not value or not effect:
            continue
        label = entry.get("label")
        options.append(
            HelpOption(
                value=value,
                label=str(label).strip() if label else None,
                effect=effect,
                is_default=(spec.default is not None and value == spec.default),
                is_current=(current is not None and value == current),
            )
        )
    return options


def _curated_help(
    entry: dict[str, Any],
    spec: FieldSpec,
    item: Optional[dict[str, Any]],
) -> SettingHelp:
    """YAML 큐레이션 항목을 응답 모델로 만든다.

    비어 있는 절은 자동 파생으로 메운다 — 사람이 쓴 부분은 그대로 살리고,
    빠뜨린 절만 기계가 채우는 편이 "일부만 작성된 항목"을 쓸모 있게 만든다.
    """
    current = (item or {}).get("effective_value")
    derived = _derive_help(spec, item)

    options = _curated_options(entry.get("options"), spec, current)

    def _text(key: str) -> Optional[str]:
        value = entry.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return SettingHelp(
        env_key=spec.env_key,
        group_key=spec.group_key,
        group_title=GROUP_TITLES.get(spec.group_key, spec.group_key),
        source="curated",
        summary=_text("summary") or derived.summary,
        behavior=_text("behavior") or derived.behavior,
        options=options or derived.options,
        example=_text("example"),
        performance=_text("performance") or derived.performance,
        stability=_text("stability") or derived.stability,
        recommendation=_text("recommendation"),
        caveats=_as_str_list(entry.get("caveats")),
        operational=_operational_notes(spec, item),
        related=_as_str_list(entry.get("related")),
        references=_as_str_list(entry.get("references")),
    )


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────


def build_help(env_key: str, item: Optional[dict[str, Any]] = None) -> Optional[SettingHelp]:
    """설정 1건의 도움말을 만든다.

    Args:
        env_key: 설정 키 (카탈로그에 없으면 None)
        item: 해당 키의 카탈로그 항목 dict (현재값·오버라이드 표시용, 선택)

    Returns:
        도움말. 카탈로그에 없는 키면 None.
    """
    spec = field_index().get(env_key)
    if spec is None:
        return None

    entry = curated_index().get(env_key)
    if entry:
        return _curated_help(entry, spec, item)
    return _derive_help(spec, item)


def curated_keys() -> frozenset[str]:
    """큐레이션 설명이 존재하는 키 집합 (커버리지 게이트용)."""
    return frozenset(curated_index().keys())
