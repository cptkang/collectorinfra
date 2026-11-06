"""업무 서비스 연계 계산 — 상수·입력·설정·결과 데이터클래스·공용 정규화 (plans/215 §3 · D-368).

`biz_service.compute_proposal`과 그 하위 규칙 모듈(`biz_service_signals`·`biz_service_aux`·
`biz_service_apm`·`biz_service_analysis`·`biz_service_apply`·`biz_service_state`)이 함께 쓰는
모양만 둔다. 계산 규칙은 두지 않는다.

- 입력은 전부 frozen 데이터클래스다. 저장소·스냅샷 행을 이 모양으로 옮기는 일은 호출부(W3)가 한다.
- 서버 식별자 `ServerRef`는 `(db_id, resource_id)`이고, 구성원 정규 키는 `biz_service_keys`로만
  만든다.
- 설정(`BizServiceConfig`)은 application 로더가 YAML을 읽어 채운다. 값 오류로 끈 신호의 사유는
  `disabled_reasons`에 실려 실행 기록에 그대로 나간다(침묵 금지).

계층: domain — stdlib + `src.domain.host_names` + 같은 domain 모듈만 쓴다. 관측 DB 표·칼럼
이름은 여기 두지 않는다(D-088).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from noise_gate.domain.biz_service_keys import sms_member_key

# ── 신호 이름(근거 `basis` 값과 같다) ───────────────────────────────────
SIG_BIZ_SUFFIX = "biz_suffix"        # S1 등록명 업무 꼬리(강)
SIG_APM_DOMAIN = "apm_domain"        # S2 APM 도메인(중)
SIG_ITAM = "itam"                    # S3 ITAM 업무(중)
SIG_HOST_STEM = "host_stem"          # S4 호스트 이름 계열(약 · 후보 전용)
SIG_NAME_TOKENS = "name_tokens"      # S5 이름 토큰 유사(약 · 병합 후보 전용)
SIG_HOST_PREFIX = "host_prefix"      # S6 호스트 접두 업무코드(중)
SIG_TREE_GROUP = "tree_group"        # S7 자원 트리 그룹(중)
SIG_REGISTERED_BATCH = "registered_batch"      # S8
SIG_IP_SUBNET = "ip_subnet"                    # S9
SIG_ITAM_CONTRACT = "itam_contract"            # S10
SIG_ALARM_COOCCURRENCE = "alarm_cooccurrence"  # S11
SIG_AVAIL_DEPENDENCY = "avail_dependency"      # S12
AUX_SIGNALS = (
    SIG_REGISTERED_BATCH, SIG_IP_SUBNET, SIG_ITAM_CONTRACT,
    SIG_ALARM_COOCCURRENCE, SIG_AVAIL_DEPENDENCY,
)
#: 순도 보정 대상(§3.2.3 4 — S6·S7 포함).
PURITY_SIGNALS = (SIG_HOST_PREFIX, SIG_TREE_GROUP, *AUX_SIGNALS)

BASIS_APM_HOST_MAP = "apm_host_map"
BASIS_MANUAL = "manual"


def aux_basis(signal: str) -> str:
    """보조 신호 후보 근거 이름 `aux:<신호>`."""
    return f"aux:{signal}"


# ── 상태 어휘 ───────────────────────────────────────────────────────────
ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"
CONF_CONFIRMED = "confirmed"
CONF_INFERRED = "inferred"
SVC_ACTIVE = "active"
SVC_AMBIGUOUS = "ambiguous"
SVC_DISABLED = "disabled"
MEMBER_ACTIVE = "active"
MEMBER_EXCLUDED = "excluded"
MEMBER_TARGET_GONE = "target_gone"
CAND_MEMBER = "member"
CAND_MERGE = "merge"
CAND_APM_UNLINKED = "apm_unlinked"
CAND_OPEN = "open"
CAND_ACCEPTED = "accepted"
CAND_REJECTED = "rejected"
LAYERS = ("web", "was", "db", "batch", "etc")
LAYER_ORIGIN_AUTO = "auto"
LAYER_ORIGIN_MANUAL = "manual"
PREFIX_ONLY_INFERRED = "inferred"
PREFIX_ONLY_CANDIDATE = "candidate"

#: 충돌 종류 — 강 신호 서비스 둘 이상을 잇는 중 신호 · APM 대상이 서비스 둘 이상으로 갈림.
CONFLICT_SMS = "sms_bridge"
CONFLICT_APM = "apm_split"

#: 신호를 끈 사유(존별 `SignalStatus.reason`).
REASON_CONFIG_OFF = "config_off"           # 설정 enabled=false
REASON_CONFIG_INVALID = "config_invalid"   # 설정 값 오류(로더가 끔)
REASON_ATTR_FAILED = "attr_failed"         # 보강 읽기 실패
REASON_FEW_GROUPS = "few_groups"           # 평가 묶음 부족
REASON_LOW_PURITY = "low_purity"           # 순도 미달

#: 서비스당 SMS 구성원 상한 기본값(`NOISE_BIZ_SERVICE_MAX_MEMBERS`).
DEFAULT_MAX_MEMBERS = 200
#: 자동 구성원 `target_gone` 뒤 삭제까지 일수(§3.6).
GONE_PURGE_DAYS = 180

#: 서버 식별자 `(db_id, resource_id)`.
ServerRef = tuple[str, str]


# ── 공용 정규화 ─────────────────────────────────────────────────────────
_SPACES = re.compile(r"\s+")
_TOKEN_SPLIT = re.compile(r"[^0-9a-z가-힣]+")


def text(value: Any) -> str:
    """None·비문자 → 앞뒤 공백을 뺀 문자열."""
    return str(value if value is not None else "").strip()


def norm_name(value: Any) -> str:
    """업무명 정규형 — NFKC · 공백 제거 · 소문자(§3.2 S1)."""
    return _SPACES.sub("", unicodedata.normalize("NFKC", text(value))).lower()


def fold_terms(terms: Collection[str]) -> frozenset[str]:
    """일반어 목록 → 정규형 집합."""
    return frozenset(t for t in (norm_name(x) for x in terms) if t)


def is_generic_name(value: Any, generic: Collection[str]) -> bool:
    """업무명이 일반어뿐인가 — 정규형이 일반어이거나, 낱말로 쪼갠 조각이 전부 일반어다.

    빈 이름도 일반어로 본다(신호 아님).
    """
    whole = norm_name(value)
    if not whole or whole in generic:
        return True
    lowered = unicodedata.normalize("NFKC", text(value)).lower()
    tokens = [t for t in _TOKEN_SPLIT.split(lowered) if t]
    return bool(tokens) and all(t in generic for t in tokens)


def ref_key(ref: ServerRef) -> str:
    """서버 식별자 → SMS 구성원 정규 키(만들 수 없으면 빈 문자열)."""
    return sms_member_key(ref[0], ref[1]) or ""


# ── 입력 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SmsServer:
    """SMS 서버 1대(210 스냅샷 SMS 서버 행 + 존)."""

    db_id: str
    resource_id: str
    server_name: str
    hostname: str = ""
    ip: str = ""
    nic_ips: tuple[str, ...] = ()
    zone: str = ""

    @property
    def ref(self) -> ServerRef:
        return (text(self.db_id), text(self.resource_id))

    @property
    def member_key(self) -> str:
        return ref_key(self.ref)


@dataclass(frozen=True)
class ApmInventoryRow:
    """APM 인벤토리 1행(210 스냅샷 APM 행)."""

    source_id: str
    domain_id: str
    domain_name: str = ""
    instance_id: str = ""
    instance_name: str = ""
    workload_key: str = ""


@dataclass(frozen=True)
class ItamAppRow:
    """ITAM 업무 1행(코드·명)."""

    app_code: str
    app_name: str = ""


@dataclass(frozen=True)
class ItamServerRow:
    """ITAM 서버 1행 — 업무 코드·명 칸이 비면 단서 자유 기재 칸으로 업무를 찾는다."""

    hostname: str
    ips: tuple[str, ...] = ()
    active: bool | None = None
    clue_text: str = ""
    app_code: str = ""
    app_name: str = ""


@dataclass(frozen=True)
class ApmMapRow:
    """APM·SMS 연계 매핑 1행(210 `apm_host_map`) — `targets`는 210 대상 모양 그대로."""

    map_id: int
    source_id: str
    domain_id: str
    scope: str
    match_key: str
    state: str
    origin: str
    verified: bool = False
    targets: tuple[Mapping[str, Any], ...] = ()
    domain_name: str = ""
    instance_example: str = ""


@dataclass(frozen=True)
class TreeFolder:
    """자원 트리 폴더 1개(id · 이름)."""

    folder_id: str
    name: str


@dataclass(frozen=True)
class ServerAttr:
    """보강 속성 1대(§3.2.4) — `tree_path`는 루트·자기 자신을 뺀 폴더(루트 쪽 → 부모 순).

    `registered_at_ms`는 등록 시각(ms epoch) · `depends`는 가용성 의존 대상 자원 id(같은 db).
    계약 칸은 S10이 켜졌을 때만 채운다.
    """

    db_id: str
    resource_id: str
    tree_path: tuple[TreeFolder, ...] = ()
    registered_at_ms: int | None = None
    depends: tuple[str, ...] = ()
    contract_no: str = ""
    maint_contract_no: str = ""

    @property
    def ref(self) -> ServerRef:
        return (text(self.db_id), text(self.resource_id))


@dataclass(frozen=True)
class AlarmMinute:
    """알람 1건의 (서버 · 분) — S11 입력. 기간(`lookback_days`) 거르기는 호출부가 한다."""

    db_id: str
    resource_id: str
    minute: int

    @property
    def ref(self) -> ServerRef:
        return (text(self.db_id), text(self.resource_id))


@dataclass(frozen=True)
class AttrFailure:
    """보강 읽기 실패 — 그 신호를 그 존(None = 전 존)에서 끈다."""

    signal: str
    reason: str
    zone: str | None = None


@dataclass(frozen=True)
class StoredMember:
    """저장된 구성원 1건(`biz_service_member`)."""

    kind: str
    member_key: str
    origin: str = ORIGIN_AUTO
    state: str = MEMBER_ACTIVE
    confidence: str = CONF_INFERRED
    basis: str = ""
    layer: str | None = None
    layer_origin: str | None = None
    apm_map_id: int | None = None
    gone_since: str | None = None


@dataclass(frozen=True)
class StoredService:
    """저장된 업무 서비스 1건과 구성원(`biz_service`)."""

    service_id: int
    service_key: str
    display_name: str
    origin: str = ORIGIN_AUTO
    state: str = SVC_ACTIVE
    verified: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)
    members: tuple[StoredMember, ...] = ()

    @property
    def immutable(self) -> bool:
        """자동 계산이 서비스·SMS 구성을 바꾸지 않는가(manual · verified · disabled)."""
        return self.origin == ORIGIN_MANUAL or self.verified or self.state == SVC_DISABLED


@dataclass(frozen=True)
class StoredCandidate:
    """저장된 후보 1건(`biz_service_candidate`) — 거절은 다시 내지 않는다."""

    kind: str
    service_key: str
    target_key: str
    status: str = CAND_OPEN


@dataclass(frozen=True)
class StoredState:
    """저장 상태 전체."""

    services: tuple[StoredService, ...] = ()
    candidates: tuple[StoredCandidate, ...] = ()


@dataclass(frozen=True)
class ProposalInputs:
    """계산 입력 1벌 — 스냅샷 + 연계 매핑 + 보강 속성 + 저장 상태.

    `scanned_db_ids`가 None이면 `servers`에 나온 db_id를 끝까지 읽은 것으로 본다(읽지 못한 db의
    구성원을 「사라짐」으로 판정하지 않는다).
    """

    servers: tuple[SmsServer, ...] = ()
    apm_inventory: tuple[ApmInventoryRow, ...] = ()
    apm_maps: tuple[ApmMapRow, ...] = ()
    itam_apps: tuple[ItamAppRow, ...] = ()
    itam_servers: tuple[ItamServerRow, ...] = ()
    attrs: tuple[ServerAttr, ...] = ()
    alarm_minutes: tuple[AlarmMinute, ...] = ()
    attr_failures: tuple[AttrFailure, ...] = ()
    stored: StoredState = field(default_factory=StoredState)
    generic_terms: frozenset[str] = frozenset()
    scanned_db_ids: frozenset[str] | None = None


# ── 설정 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class HostPrefixConfig:
    """S6 설정(§3.2.1 6)."""

    enabled: bool = True
    length: int = 3
    min_share: float = 0.8
    prefix_only_services: str = PREFIX_ONLY_INFERRED
    exclude_prefixes: tuple[str, ...] = ()
    strip_prefix_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class TreeGroupConfig:
    """S7 설정."""

    enabled: bool = True
    min_share: float = 0.8


@dataclass(frozen=True)
class RegisteredBatchConfig:
    enabled: bool = True
    gap_seconds: int = 120
    max_batch: int = 20


@dataclass(frozen=True)
class IpSubnetConfig:
    enabled: bool = True
    prefix_len: int = 24
    exclude_cidrs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItamContractConfig:
    enabled: bool = False


@dataclass(frozen=True)
class AlarmCooccurrenceConfig:
    enabled: bool = False
    min_co: int = 3
    lookback_days: int = 14
    burst_hosts: int = 10


@dataclass(frozen=True)
class AvailDependencyConfig:
    enabled: bool = True


@dataclass(frozen=True)
class AuxSignalsConfig:
    """보조 신호 공통 값 + 신호별 설정(§3.2.3 「설정」)."""

    min_anchors: int = 2
    min_purity: float = 0.8
    min_groups: int = 5
    registered_batch: RegisteredBatchConfig = field(default_factory=RegisteredBatchConfig)
    ip_subnet: IpSubnetConfig = field(default_factory=IpSubnetConfig)
    itam_contract: ItamContractConfig = field(default_factory=ItamContractConfig)
    alarm_cooccurrence: AlarmCooccurrenceConfig = field(
        default_factory=AlarmCooccurrenceConfig)
    avail_dependency: AvailDependencyConfig = field(default_factory=AvailDependencyConfig)

    def enabled(self, signal: str) -> bool:
        """보조 신호가 설정에서 켜져 있는가."""
        block = {
            SIG_REGISTERED_BATCH: self.registered_batch.enabled,
            SIG_IP_SUBNET: self.ip_subnet.enabled,
            SIG_ITAM_CONTRACT: self.itam_contract.enabled,
            SIG_ALARM_COOCCURRENCE: self.alarm_cooccurrence.enabled,
            SIG_AVAIL_DEPENDENCY: self.avail_dependency.enabled,
        }
        return bool(block.get(signal, False))


@dataclass(frozen=True)
class SmsThreshold:
    """SMS 사용률 임계(%) — None이면 그 단계 미설정."""

    warn: float | None = None
    crit: float | None = None


@dataclass(frozen=True)
class BizServiceConfig:
    """계산 설정 1벌 — `disabled_reasons`는 로더가 값 오류로 끈 신호 → 사유."""

    host_prefix: HostPrefixConfig = field(default_factory=HostPrefixConfig)
    tree_group: TreeGroupConfig = field(default_factory=TreeGroupConfig)
    aux: AuxSignalsConfig = field(default_factory=AuxSignalsConfig)
    sms_thresholds: Mapping[str, SmsThreshold] = field(default_factory=dict)
    layer_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    max_members: int = DEFAULT_MAX_MEMBERS
    disabled_reasons: Mapping[str, str] = field(default_factory=dict)

    def signal_enabled(self, signal: str) -> bool:
        """S6·S7·보조 신호가 설정에서 켜져 있는가."""
        if signal == SIG_HOST_PREFIX:
            return self.host_prefix.enabled
        if signal == SIG_TREE_GROUP:
            return self.tree_group.enabled
        return self.aux.enabled(signal)


# ── 결과 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProposedMember:
    """제안 구성원 1건 — `service_key`의 서비스에 속한다."""

    service_key: str
    kind: str
    member_key: str
    basis: str
    confidence: str
    display: Mapping[str, Any] = field(default_factory=dict)
    prefix_code: str = ""
    apm_map_id: int | None = None
    layer: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProposedService:
    """제안 서비스 1건(SMS 구성원이 있는 자동 서비스)."""

    service_key: str
    display_name: str
    state: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateSignal:
    """후보 근거 1개 — 신호 · 묶음 키 · 닻 수 · 그 존 신호 순도(없으면 None)."""

    basis: str
    bundle_key: str = ""
    anchors: int = 0
    purity: float | None = None


@dataclass(frozen=True)
class Candidate:
    """후보 1건 — `member`(서버 → 서비스) · `merge`(서비스 ↔ 서비스) · `apm_unlinked`.

    `alt_count`는 같은 대상이 후보로 오른 서비스 수(「후보 2개」 표시) · `new_service_name`이
    있으면 아직 없는 서비스(`prefix_only_services: candidate`)로의 후보다.
    """

    kind: str
    service_key: str
    target_kind: str
    target_key: str
    signals: tuple[CandidateSignal, ...] = ()
    alt_count: int = 1
    new_service_name: str = ""


@dataclass(frozen=True)
class Conflict:
    """충돌 1건 — 대상(구성원 키)이 서비스 둘 이상으로 갈려 잇지 않았다."""

    kind: str
    target_key: str
    service_keys: tuple[str, ...]
    signal: str
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalStatus:
    """존별 신호 상태 — 켜짐 · 순도 · 평가 묶음 수 · 끈 사유."""

    signal: str
    zone: str
    enabled: bool
    purity: float | None = None
    evaluated: int = 0
    pure: int = 0
    reason: str = ""
    reason_text: str = ""


@dataclass(frozen=True)
class Proposal:
    """자동 계산 결과 1벌 — W3 미리보기 응답 · 적용(`plan_apply`) 입력.

    `active_map_ids`는 이번 입력의 `state=active` 연계 매핑 id(APM 구성원 출처 소멸 판정) ·
    `present_member_keys`·`scanned_db_ids`는 SMS 구성원 「사라짐」 판정 재료다.
    """

    services: tuple[ProposedService, ...] = ()
    members: tuple[ProposedMember, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    signal_status: tuple[SignalStatus, ...] = ()
    unassigned: tuple[str, ...] = ()
    active_map_ids: frozenset[int] = frozenset()
    present_member_keys: frozenset[str] = frozenset()
    scanned_db_ids: frozenset[str] = frozenset()
    summary: Mapping[str, Any] = field(default_factory=dict)
