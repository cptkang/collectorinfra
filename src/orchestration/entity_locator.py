"""식별자 소재 프로브 — 사다리 3단 노드 `entity_locator` (plans/102 §3.4 · D-224 ⑤ · X-6).

**무엇을 하나.** 질의가 서버를 **명시 식별자**(호스트명·FQDN·IP)로 지목하면, 라우터가 고른
시스템과 비교할 수 있는 다른 시스템에서 그 식별자가 실제로 어디 있는지 **고정 조회로 먼저
확인**하고, 결정표(`src/domain/host_discovery.py` `decide_systems`)대로 조회 대상을 좁히거나
조회하지 않고 사유를 낸다.

**배선.** `CROSS_SYSTEM_PROBE_ENABLED` on이고 3단 `semantic_router`가 등록될 때만
`semantic_router → entity_locator → (기존 분기)`로 들어간다(`src/graph.py`). off면 노드가 없다.
라우터(`src/routing/`, infrastructure)에 넣지 않는 이유: 실행 규약을 빌려 오는 `host_sweep`이
orchestration 계층이라 안쪽 계층에서 부를 수 없다(`arch_check`).

## 규약

1. **LLM 0회.** 노드 시그니처에 LLM이 없다. 판정은 라우터 구조화 출력(`required_capabilities`)과
   입력 파서의 `filter_conditions`만 본다 — 질의 원문을 키워드로 훑지 않는다(D-004).
2. **시스템(다중 존 시스템은 존)당 고정 조회 1회.** 존 순회 시스템은 인가된 존만
   (`host_sweep.authorized_zones`), 단일 DB 시스템은 DB별 키 매니페스트(`entity_keys`)로
   조립한 SELECT 1개다.
3. **시스템·DB별 개별 try.** 한 곳의 실패가 다른 곳 판정을 막지 않고, 실패는 "확인하지 못함"으로
   남는다 — 미발견과 합치지 않는다(D7). 매니페스트가 없는 DB도 "확인하지 못함"이다.
4. **캐시하지 않는다.** 0건·실패를 캐시하면 방금 등록된 서버가 TTL 동안 "없는 서버"가 된다
   (`host_sweep` 규약 3). 적중 캐시도 두지 않는다 — 프로브 비용은 고정 조회 몇 건이다.

## 발동 (G-5 기본 가정 — 소유 시스템 우선)

라우팅 의도가 데이터 조회이고(`data_query`·`alarm_query`·None) 존 역질문·폼필이 아니며,
명시 식별자가 있을 때만. 그다음은 `plan_probe`가 정한다 — 필요한 시스템이 둘 이상(a) ·
소유 시스템에서 못 찾음(b) · 소유 모호 영역(c). 한 시스템만 있는 배포는 고를 시스템이 없어
발동하지 않는다.

**명시 식별자 판정**: 호스트명 계열은 **식별 필드(`HOST_IDENTIFIER_FIELDS`) ∧ 값 판정**을
함께 요구한다. 값만 보면 *"OS가 LINUX인 서버"*의 `LINUX`도 호스트명 구문이라 오발동한다.
IP는 값 구문이 그 자체로 분명하므로(`ipaddress` 파싱) 필드명을 보지 않는다(plans/102 §3.2 —
값 기반 판정).

계층: orchestration.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage

from src.config import AppConfig, load_config
from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    KEY_FQDN,
    KEY_IPV6,
    ROW_MULTIPLICITY_PER_IP,
    EntityKeyManifest,
    ManifestKey,
    TargetEntity,
    TypedKey,
    classify_value,
    family_of,
    grade_matches,
    normalize_value,
    typed_tokens,
)
from src.domain.host_discovery import (
    ACTION_HALT,
    ACTION_QUERY,
    SystemProbe,
    decide_systems,
    pending_systems,
    plan_probe,
    system_trace_payload,
)
from src.orchestration.host_sweep import _zone_label, authorized_zones, sweep_order
from src.routing.domain_config import get_domain_by_id
from src.routing.registry import DBRegistry as RegistrySpec
from src.routing.registry import get_registry
from src.schema_cache.entity_key_manifest import load_entity_key_manifest
from src.security.sql_guard import SQLGuard
from src.state import AgentState
from src.utils.prior_dependency import NOTE_PROBE
from src.utils.query_gen_common import HOST_IDENTIFIER_FIELDS, is_demonstrative_identifier

logger = logging.getLogger(__name__)

#: 프로브를 발동하는 라우팅 의도 — `sequential_runner._DATA_INTENTS`와 같은 집합.
_DATA_INTENTS: tuple[Any, ...] = (None, "data_query", "alarm_query")
#: 식별자로 받는 연산자 — 부정·범위·부분 일치는 "그 서버"를 지목하지 않는다.
_ANCHOR_OPS: frozenset[str] = frozenset({"", "=", "==", "in"})

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 존 순회 조회 주입점 — `(db_id, names, prefixes, ips) -> 행`. 실패는 **예외**로 알린다.
HostLookupFn = Callable[
    [str, Sequence[str], Sequence[str], Sequence[str]], Awaitable[list[dict[str, Any]]]
]
#: 단일 DB 조회 주입점 — `(db_id, sql) -> 행`. 실패는 예외.
SqlRunnerFn = Callable[[str, str], Awaitable[list[dict[str, Any]]]]
#: 매니페스트 로더 주입점 — `db_id -> 매니페스트 | None`.
ManifestLoaderFn = Callable[[str], EntityKeyManifest | None]


# ──────────────────────────────────────────────
# 명시 식별자
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeIdentifiers:
    """프로브할 식별자 — 정규형 기준 중복 제거."""

    hostnames: tuple[TypedKey, ...] = ()
    ips: tuple[TypedKey, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.hostnames or self.ips)

    def all_keys(self) -> tuple[TypedKey, ...]:
        return self.hostnames + self.ips


def _condition_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def extract_identifiers(parsed_requirements: Mapping[str, Any] | None) -> ProbeIdentifiers:
    """`filter_conditions`에서 명시 식별자를 뽑는다(모듈 독스트링 「명시 식별자 판정」)."""
    hostnames: dict[str, TypedKey] = {}
    ips: dict[str, TypedKey] = {}
    for cond in (parsed_requirements or {}).get("filter_conditions") or []:
        if not isinstance(cond, Mapping):
            continue
        if str(cond.get("op") or "").strip().lower() not in _ANCHOR_OPS:
            continue
        identity_field = str(cond.get("field", "")).lower() in HOST_IDENTIFIER_FIELDS
        for raw in _condition_values(cond.get("value")):
            if raw is None or is_demonstrative_identifier(raw):
                continue
            for tk in typed_tokens(raw):
                if tk.family == FAMILY_IP:
                    ips.setdefault(tk.normalized, tk)
                elif tk.family == FAMILY_HOSTNAME and identity_field:
                    hostnames.setdefault(tk.normalized, tk)
    return ProbeIdentifiers(hostnames=tuple(hostnames.values()), ips=tuple(ips.values()))


def _host_candidates(
    keys: Sequence[TypedKey], *, exact: bool = False
) -> tuple[list[str], list[str]]:
    """호스트 키 → (완전 일치 후보, FQDN 후보를 찾을 단일 레이블).

    FQDN 입력은 단축명도 후보에 넣고, 단일 레이블 입력은 `x.%` 후보를 연다 — 매칭 등급 `possible`
    (FQDN ↔ 단일 레이블)을 SQL이 놓치지 않게 넓히고, 확정은 `grade_matches`가 한다.
    """
    names: list[str] = []
    prefixes: list[str] = []
    for key in keys:
        value = key.raw.rstrip(".") if exact else key.normalized
        names.append(value)
        if key.key_type == KEY_FQDN:
            names.append(value.split(".", 1)[0])
        else:
            prefixes.append(value)
    return list(dict.fromkeys(names)), list(dict.fromkeys(prefixes))


def _ip_candidates(keys: Sequence[TypedKey]) -> list[str]:
    """IP 키 → 저장 표기 후보(입력 원문 · 정규형 · IPv6는 대문자 정규형까지)."""
    out: list[str] = []
    for key in keys:
        out.extend([key.raw, key.normalized])
        if key.key_type == KEY_IPV6:
            out.append(key.normalized.upper())
    return list(dict.fromkeys(out))


# ──────────────────────────────────────────────
# 조회 결과 → 엔터티 → 등급
# ──────────────────────────────────────────────


def _row_get(row: Mapping[str, Any], column: str) -> Any:
    """드라이버별 키 대소문자 차이를 흡수한다."""
    if column in row:
        return row[column]
    lowered = column.lower()
    for key, value in row.items():
        if str(key).lower() == lowered:
            return value
    return None


def _compare_value(key: TypedKey, exact: bool) -> str:
    return key.raw.rstrip(".") if exact and key.family == FAMILY_HOSTNAME else key.normalized


def _cell_keys(value: Any, family: str, *, multi_value: bool) -> list[TypedKey]:
    """셀의 키 토큰. 다중값 컬럼만 구분자로 나눈다 — 단일값 컬럼(등록명 등)의 공백을 쪼개면
    `"web server"`가 `web`·`server` 두 키가 되어 엉뚱한 식별자와 일치한다."""
    if multi_value:
        return [tk for tk in typed_tokens(value) if tk.family == family]
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    kt = classify_value(text)
    if family_of(kt) != family:
        return []
    return [TypedKey(key_type=kt, normalized=normalize_value(text, kt), raw=text)]


def _matched_entities(report: Any, value: str) -> tuple[str, ...]:
    """등급 보고에서 이 식별자가 걸린 엔터티 — 세 등급 모두 "있다"의 증거다."""
    return (
        report.link.get(value)
        or report.possible.get(value)
        or report.ambiguous.get(value)
        or ()
    )


#: 존 순회 조회(`probe_hosts`)가 돌려주는 행의 고정 투영. 이 세 컬럼만 온다.
_ZONED_HOST_COLUMN = "hostname"
#: 등록명 — 폴스타에서 hostname이 아니다(D-061). 매니페스트가 선언하지 않으면 **보조 증거**다.
_ZONED_ALT_HOST_COLUMN = "name"
_ZONED_IP_COLUMN = "ipaddress"
#: 보조 컬럼의 사용자 표기 — 결정표 ②′ 사유 문장에 쓴다.
_ZONED_WEAK_LABEL = "등록명"


def zoned_key_columns(
    db_id: str, manifest_loader: ManifestLoaderFn | None,
) -> tuple[tuple[tuple[str, bool, bool], ...], tuple[tuple[str, bool, bool], ...]]:
    """존 순회 행의 키 컬럼과 **선언 여부** — 매니페스트 선언이 1순위다(권고 G).

    투영은 `probe_hosts`가 고정하므로 컬럼 목록 자체는 바뀌지 않는다. 바뀌는 것은 강도다:
    매니페스트가 키로 선언한 컬럼이 강한 증거이고, 선언에 없는 `name`은 보조 증거다.
    매니페스트를 읽지 못하면 종전 기본값(hostname·ipaddress가 선언)으로 둔다 — 로더 실패가
    존 순회 프로브 전체를 "보조 증거뿐"으로 뒤집지 않게 한다.
    """
    manifest = None
    if manifest_loader is not None:
        try:
            manifest = manifest_loader(db_id)
        except Exception as exc:  # noqa: BLE001 — 로더 실패는 강도 판정만 기본값으로 되돌린다
            logger.warning(
                "키 매니페스트 로드 실패(존 순회 강도 판정): db_id=%s err=%s", db_id, exc
            )
    declared = (
        {k.column.lower() for k in manifest.keys}
        if manifest is not None
        else {_ZONED_HOST_COLUMN, _ZONED_IP_COLUMN}
    )
    hosts = tuple(
        (col, False, col in declared)
        for col in (_ZONED_HOST_COLUMN, _ZONED_ALT_HOST_COLUMN)
    )
    return hosts, ((_ZONED_IP_COLUMN, False, _ZONED_IP_COLUMN in declared),)


@dataclass
class _Entities:
    """조회 행을 엔터티로 묶은 것 — 계열별 키 집합과 엔터티 → db_id.

    `strong`은 **키 선언 컬럼**으로 얻은 키만 따로 모은 것이다 — 보조 컬럼(폴스타 `name` 등)
    으로만 맞은 식별자를 "저 시스템에 등록돼 있다"의 근거로 쓰지 않기 위해서다(권고 K · G-3).
    """

    host: dict[str, set[str]]
    ip: dict[str, set[str]]
    db_of: dict[str, str]
    strong: dict[str, dict[str, set[str]]]

    @classmethod
    def empty(cls) -> _Entities:
        return cls(host={}, ip={}, db_of={}, strong={FAMILY_HOSTNAME: {}, FAMILY_IP: {}})

    def add_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        db_id: str,
        host_columns: Sequence[tuple[str, bool, bool]],
        ip_columns: Sequence[tuple[str, bool, bool]],
        per_ip: bool,
        exact_host: bool = False,
    ) -> None:
        """행을 엔터티로 등록한다. `per_ip`면 같은 호스트 값의 행(IP별 행)을 한 엔터티로
        묶는다(X-T5).

        컬럼은 `(이름, multi_value, 선언 컬럼인가)` 3쌍이다.
        """
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            host_keys: set[str] = set()
            host_strong: set[str] = set()
            for col, multi, declared in host_columns:
                found = {
                    _compare_value(tk, exact_host)
                    for tk in _cell_keys(_row_get(row, col), FAMILY_HOSTNAME, multi_value=multi)
                }
                host_keys |= found
                if declared:
                    host_strong |= found
            ip_keys: set[str] = set()
            ip_strong: set[str] = set()
            for col, multi, declared in ip_columns:
                found = {
                    tk.normalized
                    for tk in _cell_keys(_row_get(row, col), FAMILY_IP, multi_value=multi)
                }
                ip_keys |= found
                if declared:
                    ip_strong |= found
            if per_ip and host_keys:
                entity_id = f"{db_id}:{sorted(host_keys)[0]}"
            else:
                entity_id = f"{db_id}:#{index}"
            self.db_of[entity_id] = db_id
            self.host.setdefault(entity_id, set()).update(host_keys)
            self.ip.setdefault(entity_id, set()).update(ip_keys)
            self.strong[FAMILY_HOSTNAME].setdefault(entity_id, set()).update(host_strong)
            self.strong[FAMILY_IP].setdefault(entity_id, set()).update(ip_strong)

    def grade(
        self,
        identifiers: ProbeIdentifiers,
        *,
        exact_host: bool = False,
        skip_families: Sequence[str] = (),
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, int]], tuple[str, ...]]:
        """식별자별 발견 DB · 계열별 등급 건수 · **보조 컬럼으로만 맞은 식별자**.

        `link`·`possible`·`ambiguous`는 전부 "이 시스템에 있다"다 — 모호는 **어느 엔터티인지**가
        불분명한 것이지 존재가 불분명한 것이 아니다(여러 존에 같은 이름이 있으면 그 존들을
        조회한다).
        """
        hits: dict[str, tuple[str, ...]] = {}
        grades: dict[str, dict[str, int]] = {}
        weak: list[str] = []
        families = (
            (FAMILY_HOSTNAME, identifiers.hostnames, self.host),
            (FAMILY_IP, identifiers.ips, self.ip),
        )
        for family, keys, keyed in families:
            if not keys or family in skip_families:
                continue
            exact = exact_host and family == FAMILY_HOSTNAME
            targets = [
                TargetEntity(entity_id=eid, keys=frozenset(ks)) for eid, ks in keyed.items() if ks
            ]
            report = grade_matches([_compare_value(k, exact) for k in keys], targets, family=family)
            grades[family] = {
                "link": len(report.link),
                "possible": len(report.possible),
                "non_link": len(report.non_link),
                "ambiguous": len(report.ambiguous),
            }
            # 선언 컬럼 키만으로 같은 등급 판정을 한 번 더 한다 — 같은 규칙(완전 일치·단축명)을
            # 써야 "보조 컬럼 때문에 맞은 것"과 "선언 컬럼으로 맞은 것"이 정확히 갈린다.
            declared = self.strong.get(family) or {}
            strong_report = grade_matches(
                [_compare_value(k, exact) for k in keys],
                [
                    TargetEntity(entity_id=eid, keys=frozenset(ks))
                    for eid, ks in declared.items() if ks
                ],
                family=family,
            )
            for key in keys:
                value = _compare_value(key, exact)
                entity_ids = _matched_entities(report, value)
                db_ids = tuple(dict.fromkeys(self.db_of[e] for e in entity_ids))
                if not db_ids:
                    continue
                hits[key.raw] = db_ids
                if not _matched_entities(strong_report, value):
                    weak.append(key.raw)
        return hits, grades, tuple(dict.fromkeys(weak))


# ──────────────────────────────────────────────
# 매니페스트 기반 조회 SQL (단일 DB 시스템)
# ──────────────────────────────────────────────


def _literal(value: str) -> str:
    return "'" + value.replace("\x00", "").replace("'", "''") + "'"


def _identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"매니페스트 식별자 형식 오류: {name!r}")
    return name


def table_reference(db_id: str, table: str) -> str:
    """매니페스트 테이블의 참조 — 레지스트리 `db_schema`가 있으면 스키마 한정(D-057)."""
    parts = table.split(".")
    if len(parts) > 2:
        raise ValueError(f"매니페스트 테이블 형식 오류: {table!r}")
    for part in parts:
        _identifier(part)
    domain = get_domain_by_id(db_id)
    schema = (getattr(domain, "db_schema", "") or "") if domain else ""
    if schema and len(parts) == 1:
        return f"{_identifier(schema)}.{table}"
    return table


def build_manifest_probe_sql(
    manifest: EntityKeyManifest,
    *,
    table_ref: str,
    identifiers: ProbeIdentifiers,
) -> str:
    """매니페스트 키로 소재 확인 SELECT 1개를 조립한다(읽기 전용 · 검증 후 반환).

    - 호스트 키: `LOWER(col) IN (정규형·단축명) OR LOWER(col) LIKE 'x.%'`
      (`compare: exact`면 LOWER 없이)
    - IP 키: `col IN (…)`
    - `multi_value` 키: 한 칸에 값이 여럿이라 `col LIKE '%x%'`로 **넓게** 좁히고, 토큰 확정은
      코드가 한다(X-T6)

    Raises:
        ValueError: 받을 수 있는 키가 없거나 식별자 형식이 틀렸거나 안전한 SELECT가 아닐 때
    """
    host_key = manifest.key_for(FAMILY_HOSTNAME) if identifiers.hostnames else None
    ip_key = manifest.key_for(FAMILY_IP) if identifiers.ips else None
    if host_key is None and ip_key is None:
        raise ValueError("매니페스트가 이 식별자 종류를 받지 않습니다")

    columns = [
        _identifier(k.column)
        for k in (manifest.key_for(FAMILY_HOSTNAME), manifest.key_for(FAMILY_IP))
        if k is not None
    ]
    conditions: list[str] = []
    if host_key is not None:
        conditions.extend(_host_conditions(host_key, identifiers.hostnames))
    if ip_key is not None:
        conditions.extend(_ip_conditions(ip_key, identifiers.ips))
    sql = (
        f"SELECT {', '.join(dict.fromkeys(columns))} FROM {table_ref} "
        f"WHERE {' OR '.join(conditions)}"
    )
    safe, reason = SQLGuard().is_safe_select(sql)
    if not safe or not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError(f"소재 프로브 SQL이 안전한 SELECT가 아닙니다: {reason}")
    return sql


def _host_conditions(key: ManifestKey, hostnames: Sequence[TypedKey]) -> list[str]:
    exact = key.compare == "exact"
    column = _identifier(key.column)
    expr = column if exact else f"LOWER({column})"
    names, prefixes = _host_candidates(hostnames, exact=exact)
    if key.multi_value:
        return [f"{expr} LIKE {_literal('%' + v + '%')}" for v in dict.fromkeys(names + prefixes)]
    out = [f"{expr} IN ({', '.join(_literal(v) for v in names)})"]
    out.extend(f"{expr} LIKE {_literal(p + '.%')}" for p in prefixes)
    return out


def _ip_conditions(key: ManifestKey, ips: Sequence[TypedKey]) -> list[str]:
    column = _identifier(key.column)
    values = _ip_candidates(ips)
    if key.multi_value:
        return [f"{column} LIKE {_literal('%' + v + '%')}" for v in values]
    return [f"{column} IN ({', '.join(_literal(v) for v in values)})"]


# ──────────────────────────────────────────────
# 시스템별 확인 (개별 try)
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeRun:
    """시스템 하나의 확인 결과 + 트레이스 부가 정보."""

    probe: SystemProbe
    grades: dict[str, dict[str, int]]
    latency_ms: float


async def probe_zoned_system(
    system: str,
    *,
    label: str,
    scope: Sequence[str],
    identifiers: ProbeIdentifiers,
    lookup: HostLookupFn,
    db_labels: Mapping[str, str],
    manifest_loader: ManifestLoaderFn | None = None,
) -> ProbeRun:
    """존 순회 시스템 — 인가된 존마다 고정 조회 1회. 한 존의 실패가 순회를 멈추지 않는다.

    조회 SQL은 `hostname`과 `name`을 함께 보지만 **브리지 키는 `hostname`뿐**이다(G-3 · D-061).
    그래서 매니페스트 선언으로 두 컬럼의 강도를 갈라 두고, `name`으로만 맞은 식별자는
    결정표 ②(HALT)의 근거로 쓰지 않는다(권고 K).
    """
    started = time.monotonic()
    names, prefixes = _host_candidates(identifiers.hostnames)
    ips = _ip_candidates(identifiers.ips)
    entities = _Entities.empty()
    errors: dict[str, str] = {}
    for db_id in scope:
        try:
            rows = await lookup(db_id, names, prefixes, ips)
        except Exception as exc:  # noqa: BLE001 — 한 존의 실패는 "확인하지 못함"이지 순회 중단이 아니다
            logger.warning("소재 프로브 조회 실패: system=%s db_id=%s err=%s", system, db_id, exc)
            errors[db_id] = type(exc).__name__
            continue
        host_columns, ip_columns = zoned_key_columns(db_id, manifest_loader)
        entities.add_rows(
            rows,
            db_id=db_id,
            host_columns=host_columns,
            ip_columns=ip_columns,
            per_ip=False,
        )
    hits, grades, weak_only = entities.grade(identifiers)
    return ProbeRun(
        probe=SystemProbe(
            system=system,
            label=label,
            identifiers=tuple(k.raw for k in identifiers.all_keys()),
            probed_db_ids=tuple(scope),
            hits=hits,
            errors=errors,
            db_labels=dict(db_labels),
            weak_only=weak_only,
            weak_label=_ZONED_WEAK_LABEL,
        ),
        grades=grades,
        latency_ms=(time.monotonic() - started) * 1000,
    )


async def probe_manifest_system(
    system: str,
    *,
    label: str,
    scope: Sequence[str],
    identifiers: ProbeIdentifiers,
    manifest_loader: ManifestLoaderFn,
    run_sql: SqlRunnerFn,
    db_labels: Mapping[str, str],
) -> ProbeRun:
    """단일 DB 시스템 — DB별 키 매니페스트로 SELECT 1회. 매니페스트가 없으면 "확인하지 못함"."""
    started = time.monotonic()
    entities = _Entities.empty()
    errors: dict[str, str] = {}
    supported: set[str] = set()
    exact_host = False
    for db_id in scope:
        try:
            manifest = manifest_loader(db_id)
        except Exception as exc:  # noqa: BLE001 — 로더 실패도 "확인하지 못함"이다
            logger.warning("키 매니페스트 로드 실패: db_id=%s err=%s", db_id, exc)
            errors[db_id] = type(exc).__name__
            continue
        if manifest is None:
            errors[db_id] = "키 매니페스트 없음"
            continue
        host_key = manifest.key_for(FAMILY_HOSTNAME)
        ip_key = manifest.key_for(FAMILY_IP)
        try:
            sql = build_manifest_probe_sql(
                manifest,
                table_ref=table_reference(db_id, manifest.table),
                identifiers=identifiers,
            )
            rows = await run_sql(db_id, sql)
        except Exception as exc:  # noqa: BLE001 — 이 DB의 실패는 "확인하지 못함"으로 남긴다
            logger.warning("소재 프로브 조회 실패: system=%s db_id=%s err=%s", system, db_id, exc)
            errors[db_id] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            continue
        if host_key is not None:
            supported.add(FAMILY_HOSTNAME)
            exact_host = exact_host or host_key.compare == "exact"
        if ip_key is not None:
            supported.add(FAMILY_IP)
        # 여기는 컬럼 자체가 매니페스트 선언이라 전부 강한 증거다(권고 G — 선언이 1순위).
        entities.add_rows(
            rows,
            db_id=db_id,
            host_columns=((host_key.column, host_key.multi_value, True),) if host_key else (),
            ip_columns=((ip_key.column, ip_key.multi_value, True),) if ip_key else (),
            per_ip=manifest.row_multiplicity == ROW_MULTIPLICITY_PER_IP,
            exact_host=host_key is not None and host_key.compare == "exact",
        )
    unsupported = [f for f in (FAMILY_HOSTNAME, FAMILY_IP) if supported and f not in supported]
    unchecked = tuple(k.raw for k in identifiers.all_keys() if k.family in unsupported)
    if unchecked:
        errors[""] = f"키 매니페스트가 받지 않는 식별자: {', '.join(unchecked)}"
    hits, grades, weak_only = entities.grade(
        identifiers, exact_host=exact_host, skip_families=unsupported
    )
    return ProbeRun(
        probe=SystemProbe(
            system=system,
            label=label,
            identifiers=tuple(k.raw for k in identifiers.all_keys()),
            probed_db_ids=tuple(scope),
            hits=hits,
            errors=errors,
            unchecked=unchecked,
            db_labels=dict(db_labels),
            weak_only=weak_only,
        ),
        grades=grades,
        latency_ms=(time.monotonic() - started) * 1000,
    )


# ──────────────────────────────────────────────
# 판정 입력 — 필요한 시스템 · 확인 범위
# ──────────────────────────────────────────────


def required_systems(
    state: Mapping[str, Any], registry: RegistrySpec
) -> tuple[tuple[str, ...], bool]:
    """(필요한 시스템, 소유 모호 여부).

    `required_capabilities`(라우터 구조화 출력)가 있으면 그 소유 시스템, 비었으면(소유 플래그 off)
    현재 `target_databases`의 시스템 집합으로 대신한다 — 이때는 모호 판정 근거가 없다.

    모호 영역 목록은 **레지스트리 선언**(`capabilities[].ambiguous_owner`)에서 읽는다 — 코드
    상수로 두면 소유 데이터의 두 번째 출처가 된다(D-053 · G-1).
    """
    capabilities = [
        c for c in (state.get("required_capabilities") or []) if isinstance(c, str) and c
    ]
    owners: list[str] = []
    for capability in capabilities:
        for owner in registry.capability_owners(capability):
            if owner not in owners:
                owners.append(owner)
    if owners:
        ambiguous_codes = registry.ambiguous_capabilities()
        return tuple(owners), all(c in ambiguous_codes for c in capabilities)
    for target in state.get("target_databases") or []:
        system = (
            registry.system_of(target.get("db_id", "")) if isinstance(target, Mapping) else None
        )
        if system and system not in owners:
            owners.append(system)
    return tuple(owners), False


def probe_scope(
    system: str,
    registry: RegistrySpec,
    *,
    active_db_ids: Sequence[str],
    targeted_db_ids: Sequence[str],
    allowed_db_ids: Sequence[str] | None,
) -> list[str]:
    """시스템의 확인 범위 — 라우터가 고른 그 시스템 DB(없으면 활성 DB 전부) ∩ 인가.

    존 배정 DB는 `authorized_zones`(인가 필터 + `query_order`)를 그대로 쓰고, 존 미배정 DB는 뒤에
    붙인다 — 실행 그룹 분할이 존 미배정 DB를 버리므로(X-T7) 그대로 쓰면 그 시스템이 **말없이**
    빠진다.
    """
    members = [d for d in registry.system_db_ids(system) if d in set(active_db_ids)]
    base = [d for d in targeted_db_ids if d in members] or members
    zoned = set(sweep_order(base))
    unzoned = [d for d in dict.fromkeys(base) if d not in zoned]
    allowed = None if allowed_db_ids is None else set(allowed_db_ids)
    return authorized_zones(base, allowed_db_ids) + [
        d for d in unzoned if allowed is None or d in allowed
    ]


def _db_label(registry: RegistrySpec, db_id: str) -> str:
    label = _zone_label(db_id)
    if label != db_id:
        return label
    entry = registry.get(db_id)
    return (entry.display_name or db_id) if entry else db_id


def _is_zoned_system(registry: RegistrySpec, system: str) -> bool:
    """솔루션(다중 존) 시스템인가 — 존 순회 호스트 조회를 쓴다. 아니면 DB별 키 매니페스트 조회."""
    return any(spec.code == system for spec in registry.solutions())


async def _default_host_lookup(
    app_config: AppConfig,
    db_id: str,
    names: Sequence[str],
    prefixes: Sequence[str],
    ips: Sequence[str],
) -> list[dict[str, Any]]:
    from noise_gate.infrastructure.polestar_hostname_resolver import probe_hosts

    return await probe_hosts(
        app_config, db_id, names=list(names), prefixes=list(prefixes), ips=list(ips)
    )


async def _default_run_sql(app_config: AppConfig, db_id: str, sql: str) -> list[dict[str, Any]]:
    from src.routing.db_registry import DBRegistry

    async with DBRegistry(app_config).get_client(db_id) as client:
        result = await client.execute_sql(sql)
    return [row for row in result.rows if isinstance(row, dict)]


# ──────────────────────────────────────────────
# 노드
# ──────────────────────────────────────────────


def probe_halted(state: Mapping[str, Any]) -> bool:
    """이번 턴 프로브가 "조회하지 않고 사유 노출"로 끝냈는가 — 그래프 분기 함수가 쓴다."""
    probe = state.get("entity_probe")
    return isinstance(probe, Mapping) and probe.get("action") == ACTION_HALT


def _skip(state: Mapping[str, Any]) -> dict[str, Any]:
    """발동하지 않는 경로. 이전 판정(`entity_probe`)이 남아 있으면 지운다.

    체크포인터는 델타만 병합하므로, 라우트가 초기화하지 않은 진입에서 지난 턴의 HALT 표지가
    분기를 끝내 버리지 않게 한다.
    """
    return {"entity_probe": None} if state.get("entity_probe") is not None else {}


def _probe_targets(
    state: Mapping[str, Any], db_ids: Sequence[str], reason: str
) -> list[dict[str, Any]]:
    original = {
        t.get("db_id"): t for t in (state.get("target_databases") or []) if isinstance(t, Mapping)
    }
    targets: list[dict[str, Any]] = []
    for db_id in db_ids:
        if db_id in original:
            targets.append(dict(original[db_id]))
        else:
            targets.append(
                {
                    "db_id": db_id,
                    "relevance_score": 1.0,
                    "sub_query_context": state.get("user_query", ""),
                    "user_specified": False,
                    "reason": reason,
                }
            )
    return targets


async def entity_locator(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
    host_lookup: HostLookupFn | None = None,
    sql_runner: SqlRunnerFn | None = None,
    manifest_loader: ManifestLoaderFn | None = None,
) -> dict[str, Any]:
    """명시 식별자의 소재를 확인해 조회 대상을 좁히거나, 조회하지 않고 사유를 낸다.

    Args:
        state: 에이전트 상태(`semantic_router` 산출 포함)
        app_config: 앱 설정
        host_lookup: 존 순회 조회 대역(테스트) — 기본은 `noise_gate...probe_hosts`
        sql_runner: 단일 DB 조회 대역(테스트) — 기본은 `DBRegistry.get_client().execute_sql`
        manifest_loader: 매니페스트 로더 대역(테스트) — 기본은 구조 정본 프로필 `entity_keys`

    Returns:
        발동하지 않으면 `{}`(상태 무변경 — 이전 판정이 남아 있을 때만 `entity_probe=None`으로
        자기정리). 발동하면 `entity_probe`·`dependency_notes`(NOTE_PROBE 1건), QUERY면
        `target_databases`·`is_multi_db`·`active_db_id`, HALT면 `final_response`·`messages`.
    """
    if state.get("routing_intent") not in _DATA_INTENTS:
        return _skip(state)
    if state.get("template_structure") or state.get("zone_clarification"):
        return _skip(state)
    identifiers = extract_identifiers(state.get("parsed_requirements"))
    if not identifiers:
        return _skip(state)

    config = app_config or load_config()
    registry = get_registry()
    active = config.multi_db.get_active_db_ids()
    allowed = state.get("allowed_db_ids")
    targeted = [
        t.get("db_id", "") for t in (state.get("target_databases") or []) if isinstance(t, Mapping)
    ]
    required, ambiguous = required_systems(state, registry)
    available = [
        s
        for s in dict.fromkeys(registry.system_of(d) for d in active)
        if s
        and probe_scope(
            s, registry, active_db_ids=active, targeted_db_ids=(), allowed_db_ids=allowed
        )
    ]
    plan = plan_probe(required, ambiguous=ambiguous, available_systems=available)
    if plan is None:
        return _skip(state)

    started = time.monotonic()
    lookup: HostLookupFn = host_lookup or (
        lambda db_id, names, prefixes, ips: _default_host_lookup(
            config, db_id, names, prefixes, ips
        )
    )
    run_sql: SqlRunnerFn = sql_runner or (lambda db_id, sql: _default_run_sql(config, db_id, sql))
    loader: ManifestLoaderFn = manifest_loader or load_entity_key_manifest

    runs: dict[str, ProbeRun] = {}
    while True:
        batch = pending_systems(plan, {name: run.probe for name, run in runs.items()})
        if not batch:
            break
        for system in batch:
            runs[system] = await _probe_system(
                system,
                registry,
                identifiers,
                active=active,
                targeted=targeted,
                allowed=allowed,
                lookup=lookup,
                run_sql=run_sql,
                loader=loader,
            )

    probes = {name: run.probe for name, run in runs.items()}
    decision = decide_systems(plan, probes)
    trace = system_trace_payload(plan, probes, decision)
    for name, run in runs.items():
        trace["probes"][name]["grades"] = run.grades
        trace["probes"][name]["latency_ms"] = round(run.latency_ms, 1)
    trace["identifiers"] = [{"value": k.raw, "type": k.key_type} for k in identifiers.all_keys()]
    trace["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    logger.info(
        "소재 프로브: mode=%s row=%s action=%s systems=%s db_ids=%s 조회시스템=%s 지연=%.1fms",
        plan.mode,
        decision.row,
        decision.action,
        list(decision.systems),
        list(decision.db_ids),
        list(runs),
        trace["latency_ms"],
    )

    notes = list(state.get("dependency_notes") or [])
    notes.append(
        {"kind": NOTE_PROBE, "task_id": None, "reason": decision.row, "detail": decision.message}
    )
    out: dict[str, Any] = {
        "entity_probe": trace,
        "dependency_notes": notes,
        "current_node": "entity_locator",
    }
    if decision.action == ACTION_HALT:
        out.update(
            {
                "final_response": decision.message,
                "messages": [AIMessage(content=decision.message)],
                "target_databases": [],
                "is_multi_db": False,
                "active_db_id": None,
            }
        )
    elif decision.action == ACTION_QUERY and decision.db_ids:
        # 소유 시스템 모델 밖의 대상(답변 영역 선언이 없는 DB)은 이 판정의 대상이 아니다
        # — 그대로 둔다.
        outside = [
            d for d in targeted if d and registry.system_of(d) is None and d not in decision.db_ids
        ]
        targets = _probe_targets(
            state, list(decision.db_ids) + outside, reason="소재 프로브로 확인한 DB"
        )
        out.update(
            {
                "target_databases": targets,
                "is_multi_db": len(targets) > 1,
                "active_db_id": targets[0]["db_id"],
            }
        )
    return out


async def _probe_system(
    system: str,
    registry: RegistrySpec,
    identifiers: ProbeIdentifiers,
    *,
    active: Sequence[str],
    targeted: Sequence[str],
    allowed: Sequence[str] | None,
    lookup: HostLookupFn,
    run_sql: SqlRunnerFn,
    loader: ManifestLoaderFn,
) -> ProbeRun:
    scope = probe_scope(
        system, registry, active_db_ids=active, targeted_db_ids=targeted, allowed_db_ids=allowed
    )
    label = registry.system_label(system)
    db_labels = {d: _db_label(registry, d) for d in scope}
    if _is_zoned_system(registry, system):
        return await probe_zoned_system(
            system,
            label=label,
            scope=scope,
            identifiers=identifiers,
            lookup=lookup,
            db_labels=db_labels,
            manifest_loader=loader,
        )
    return await probe_manifest_system(
        system,
        label=label,
        scope=scope,
        identifiers=identifiers,
        manifest_loader=loader,
        run_sql=run_sql,
        db_labels=db_labels,
    )
