"""제니퍼(APM) 알람 노이즈 컨텍스트 공급자 — 유형 정책 표 기반 (plans/144 §4.1~4.3).

폴스타 `PolestarNoiseContextRepository`가 DB 고정 SQL로 채우는 노이즈 컨텍스트(`_NOISE_CTX_KEYS`)를,
제니퍼 알람에 대해서는 **정책 표**(`config/apm_noise_policy.yaml`)로 채운다. 플래그
`NOISE_APM_NOISE_POLICY_ENABLED`가 켜졌을 때만 워커가 기동 시 1회 생성해 enricher에 주입한다
(off면 생성하지 않아 제니퍼 알람은 현행 그대로 `unavailable → PAGE`).

계약 키별 값:
    - `importance_id` — 중요도 **라벨**(높음|보통|낮음). ① `instances:` 인스턴스 오버라이드
      ② 유형 행의 `importance` ③ 없으면 「보통」(D-048.3). 정책 계층은 `source="apm_policy"`일 때
      이 값을 코드 매핑(`importance_value_map`) 없이 라벨로 읽는다.
    - `maintenance` — None. 운영자 침묵 규칙은 정책 step 6.2(`silence.match_rules`)가 이미
      db_id·server_name·alarm_name·resource_name(제니퍼 인스턴스 이름)으로 매칭하므로 여기서
      중복하지 않는다(공급자가 `source="apm_policy"`를 주면 step 5 수집 실패 단락이 풀려 침묵
      단계까지 도달한다).
    - `noti_policy` — 유형 행 `notify: page` → `"notify"`, 그 밖 → None. `"notify"`를 기본값으로
      두지 않는다(step 9 승격 우선 규칙이 지속 조건 강등을 무시하게 된다).
    - 나머지(`parent_avail_status`·`cascaded`·`change_*` 등) — None.
    - `source` — `"apm_policy"`. 정책 파일 적재 실패면 `_unavailable()` 그대로(현행 PAGE).

WAS 판정(유형 → was_signals kind)은 게이트웨이 소관이다(D-195 ② · D-274 ⑤). 표의 `kind` 열은
정보용 메타데이터이며 이 모듈은 판정에 쓰지 않는다. `raw.apm.was_signals`는 정책 판정에 읽지
않는다 — `correlation_extra`만 게이트웨이가 실어 보낸 kind **값**을 상관 토큰으로 옮긴다
(D-195 ② 개정 · plans/144 §4.4 — 묶음 용도, 판정 재구현 아님).

유형 정규화(§4.3): 게이트웨이가 싣는 `raw.apm.event_type_norm`을 쓰고, 없으면(구버전 게이트웨이)
`alarm_name`을 같은 규칙(대문자 · `ERROR_`/`WARNING_` 접두 1회 제거)으로 정규화한다.
apm_gateway 패키지는 import하지 않는다(D-139 — `alarm:raw` 계약만).

계층: infrastructure — domain(`notification_policy` 상수) + 같은 계층의 계약 키 단일 출처만 쓴다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import NOISE_SOURCE_APM_POLICY
from noise_gate.infrastructure.polestar_noise_context import _NOISE_CTX_KEYS, _unavailable

logger = logging.getLogger(__name__)

#: 정책 표 정본 경로 — 설정 키를 두지 않는다(다른 선언 파일과 같은 고정 경로).
DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "apm_noise_policy.yaml"

#: 표에 없는 유형이 쓰는 행 키.
DEFAULT_ROW = "default"

#: 게이트웨이 `event_kind` 어휘 중 지표형(지속 조건 대상).
EVENT_KIND_METRIC = "metric"

_EVENT_TYPE_PREFIXES = ("ERROR_", "WARNING_")

#: 상관 토큰 분해 구분자(`correlation.signature_tokens`와 같은 문자 집합) — 값을 한 토큰으로 붙인다.
_TOKEN_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)

_Importance = Literal["높음", "보통", "낮음"]


class PersistenceRule(BaseModel):
    """지속 조건 — 창(초) 안에 같은 지문이 min_count회 이상이어야 매트릭스 그대로 간다."""

    model_config = ConfigDict(extra="forbid")

    window_seconds: int = Field(gt=0)
    min_count: int = Field(ge=1)


class TypePolicy(BaseModel):
    """유형 정책 행."""

    model_config = ConfigDict(extra="forbid")

    notify: Literal["page", "dashboard", "suppress"]
    importance: _Importance | None = None
    persistence: PersistenceRule | None = None
    kind: str = ""  # 정보용 메타데이터 — 판정에 쓰지 않는다


class ApmNoisePolicy(BaseModel):
    """정책 표 전체."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    types: dict[str, TypePolicy]
    instances: dict[str, _Importance] = Field(default_factory=dict)

    @field_validator("types")
    @classmethod
    def _normalize_type_keys(cls, value: dict[str, TypePolicy]) -> dict[str, TypePolicy]:
        """유형 키를 정규화 표기로 맞추고 `default` 행을 요구한다.

        정규화 뒤 같은 키가 되는 행(`ERROR_X`·`X`)은 어느 쪽이 이길지 모호하므로 스키마 위반으로
        거부한다(적재 실패 → unavailable PAGE).
        """
        if DEFAULT_ROW not in value:
            raise ValueError(f"types에 '{DEFAULT_ROW}' 행이 없다")
        rows: dict[str, TypePolicy] = {}
        origin: dict[str, str] = {}
        for raw_key, row in value.items():
            key = raw_key if raw_key == DEFAULT_ROW else normalize_event_type(raw_key)
            if key in rows:
                raise ValueError(
                    f"types 키 '{origin[key]}'와 '{raw_key}'가 정규화 뒤 같은 유형 '{key}'다"
                )
            rows[key] = row
            origin[key] = raw_key
        return rows

    @field_validator("instances", mode="before")
    @classmethod
    def _none_instances(cls, value: Any) -> Any:
        """`instances:` 빈 값(None)은 빈 표로 읽는다."""
        return {} if value is None else value


@dataclass(frozen=True)
class ApmPolicyMatch:
    """알람 1건에 대한 정책 표 조회 결과."""

    event_type_norm: str
    row_key: str  # 적용된 행 키(정규화 유형 또는 "default") — 적재 실패면 ""
    row: TypePolicy | None  # 적재 실패면 None


def normalize_event_type(raw: Any) -> str:
    """이벤트 유형을 대문자로 바꾸고 `ERROR_`·`WARNING_` 접두를 1회 뗀다(게이트웨이와 같은 규칙)."""
    text = str(raw or "").strip().upper()
    for prefix in _EVENT_TYPE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def apm_payload(event: AlarmEvent) -> dict[str, Any]:
    """원문 페이로드의 `apm` 블록(게이트웨이 계약). 없거나 dict가 아니면 빈 dict."""
    raw = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    apm = raw.get("apm")
    return apm if isinstance(apm, dict) else {}


def event_type_norm(event: AlarmEvent) -> str:
    """정규화 유형 — `raw.apm.event_type_norm`, 없으면 `alarm_name`을 정규화한 값."""
    norm = apm_payload(event).get("event_type_norm")
    if isinstance(norm, str) and norm.strip():
        return norm.strip()
    return normalize_event_type(event.alarm_name)


def event_kind(event: AlarmEvent) -> str:
    """게이트웨이 `event_kind`(`error`|`metric`|빈 값)."""
    return str(apm_payload(event).get("event_kind") or "").strip().lower()


def correlation_extra(event: AlarmEvent) -> str:
    """크로스호스트 상관 토큰에 더할 문자열 — `raw.apm.was_signals` kind와 `domain_id` (§4.4).

    plans/144 §4.4: 같은 제니퍼 도메인의 여러 인스턴스가 같은 WAS kind를 짧은 창에 함께 내면
    한 군집으로 묶이게 한다. 값만 읽는다(kind 판정은 게이트웨이 `EVENT_TYPE_SIGNALS` 소관 —
    여기서 다시 만들지 않음).

    `signature_tokens`가 비단어·밑줄 경계로 쪼개므로 각 값을 구분자 없는 한 토큰으로 만들고
    접두를 붙인다(`apmkind<kind>` · `apmdomain<id>`). 쪼개지면 kind 낱말(`db`·`heap` 등)이
    알람 이름 토큰과 겹쳐 한 차원이 여러 번 세어지고, 맨 숫자 domain_id는 다른 숫자 토큰과
    충돌할 수 있다.

    Returns:
        공백으로 이은 토큰 문자열(kind 정렬 → domain 순). 둘 다 없으면 빈 문자열.
    """
    apm = apm_payload(event)
    signals = apm.get("was_signals")
    kinds = sorted(
        {
            _atomic_token(sig.get("kind"))
            for sig in (signals if isinstance(signals, list) else [])
            if isinstance(sig, dict)
        }
        - {""}
    )
    tokens = [f"apmkind{k}" for k in kinds]
    domain = apm.get("domain_id")
    domain_token = "" if isinstance(domain, bool) else _atomic_token(domain)
    if domain_token:
        tokens.append(f"apmdomain{domain_token}")
    return " ".join(tokens)


def was_signal_kinds(event: AlarmEvent) -> tuple[str, ...]:
    """`raw.apm.was_signals[*].kind` 값(정렬 · 중복 제거) — 크로스소스 규칙의 증상 선택용(§5.1).

    D-195 ② 개정(plans/144 Q-1 (a)): 게이트웨이가 실어 보낸 kind **값**만 읽는다(판정 재구현 없음).
    """
    signals = apm_payload(event).get("was_signals")
    kinds = {
        str(sig.get("kind")).strip()
        for sig in (signals if isinstance(signals, list) else [])
        if isinstance(sig, dict) and sig.get("kind")
    }
    return tuple(sorted(kinds - {""}))


def _atomic_token(value: Any) -> str:
    """값을 소문자·구분자 없는 한 토큰으로 만든다. None·빈 값은 빈 문자열."""
    if value is None:
        return ""
    return _TOKEN_SEPARATORS.sub("", str(value)).lower()


def load_policy(path: Path) -> ApmNoisePolicy:
    """정책 표를 읽고 검증한다. `notify: suppress` 행은 경고 후 dashboard로 바꾼다(G-1 미확정).

    Raises:
        OSError · yaml.YAMLError · pydantic.ValidationError — 호출부가 적재 실패로 처리한다.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy = ApmNoisePolicy.model_validate(data if data is not None else {})
    suppressed = sorted(k for k, row in policy.types.items() if row.notify == "suppress")
    if suppressed:
        logger.warning(
            "APM 유형 정책: notify=suppress 행 %s — 완전 억제는 미확정(G-1)이라 "
            "dashboard로 취급한다",
            suppressed,
        )
        policy.types.update(
            {k: policy.types[k].model_copy(update={"notify": "dashboard"}) for k in suppressed}
        )
    return policy


class ApmNoiseContext:
    """제니퍼 알람 노이즈 컨텍스트 공급자(정책 표 기반 · 순수 메모리 조회)."""

    def __init__(self, policy: ApmNoisePolicy | None, error: str = "") -> None:
        """정책(적재 실패면 None)과 실패 사유로 만든다. 보통은 `load()`를 쓴다."""
        self._policy = policy
        self.error = error

    @classmethod
    def load(cls, path: Path | None = None) -> ApmNoiseContext:
        """정책 표를 1회 적재한다. 실패해도 예외 없이 `available=False` 공급자를 돌려준다.

        실패 시 경고 로그 1줄을 남긴다 — 이후 제니퍼 알람은 수집 실패(현행 PAGE)로 간다.
        """
        target = path or DEFAULT_POLICY_PATH
        try:
            return cls(load_policy(target))
        except Exception as exc:  # noqa: BLE001 — 파일 없음·YAML 오류·스키마 위반 모두 보수 처리
            reason = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            logger.warning(
                "APM 유형 정책 적재 실패 — 제니퍼 알람은 보수적 PAGE로 처리: path=%s 사유=%s",
                target,
                reason,
            )
            return cls(None, error=reason)

    @property
    def available(self) -> bool:
        """정책 표가 적재됐는지."""
        return self._policy is not None

    def match(self, event: AlarmEvent) -> ApmPolicyMatch:
        """정규화 유형으로 정책 행을 찾는다(없으면 `default` 행)."""
        norm = event_type_norm(event)
        if self._policy is None:
            return ApmPolicyMatch(event_type_norm=norm, row_key="", row=None)
        types = self._policy.types
        key = norm if norm in types else DEFAULT_ROW
        return ApmPolicyMatch(event_type_norm=norm, row_key=key, row=types[key])

    def fetch(self, event: AlarmEvent) -> dict[str, Any]:
        """노이즈 컨텍스트(`_NOISE_CTX_KEYS` + source)를 돌려준다. 적재 실패면 `_unavailable()`."""
        if self._policy is None:
            return _unavailable()
        m = self.match(event)
        row = m.row
        instance = str(apm_payload(event).get("instance_name") or event.resource_name or "")
        importance = (
            self._policy.instances.get(instance)
            or (row.importance if row is not None else None)
            or "보통"
        )
        ctx: dict[str, Any] = {k: None for k in _NOISE_CTX_KEYS}
        ctx["importance_id"] = importance
        ctx["noti_policy"] = "notify" if row is not None and row.notify == "page" else None
        ctx["source"] = NOISE_SOURCE_APM_POLICY
        return ctx
