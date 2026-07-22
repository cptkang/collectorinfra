"""폴스타 DB 노이즈 컨텍스트 조회 리포지토리 (Plan 52, Phase E1/E2).

알람 발송 판단(notification gate)에 필요한 **억제 신호**를 폴스타 DB에서 **고정 SQL**
(LLM 미사용, 읽기 전용 SELECT 단일문)로 수집한다. 기존 DBHub(MCP) 경로
(`DBRegistry.get_client(db_id)` → `execute_sql`)를 재사용한다(Plan 47 history repo 패턴 계승).

Phase E1 수집 범위 (Plan 52 §10):
    1. **자산 중요도** — `cmm_resource.IMPORTANCE_ID` (원값 그대로, 매핑은 정책 계층 책임)
    2. **유지보수 상태** — `cmm_resource.IS_MAINTENANCE` (불리언)
    3. **폴스타 알림정책** — `cmm_alarm_def_noti` 통보대상 존재 여부 (보수적 판정)

Phase E2 수집 범위 (Plan 52 §3.6 — `collect_dependency=True` 게이팅, 기본 off):
    4. **의존성/부모 상태** — server.Server 행의 `AVAIL_DEPEND_RESOURCE_ID`로 부모
       cmm_resource를 self-join하여 부모의 `AVAIL_STATUS`(0=정상, ≠0=비정상)를 조회.
       부모 비정상이면 자식 알람을 연쇄 노이즈로 억제 가능(판정은 결정 파이프라인 책임).
       `fetch(collect_dependency=True)`일 때만 의존성 SQL을 실행하며, 기본(False)이면
       의존성 SQL을 실행하지 않고 `parent_avail_status=None`(E1 동작·기존 계약 동결).

서버 매칭 규칙 (Plan 47 §5.3, Known Mistakes 2026-06-10 계승):
    - 알람 이벤트의 `server_name`(=${platformName})은 폴스타 등록 서버명으로, server.Server
      행의 `CMM_RESOURCE.NAME`과 매칭한다.
    - 공동존 폴스타(polestar_cm_gp/yd)는 r.name(=SVR.NAME)으로 매칭한다 — hostname 매칭 금지.
    - 중요도/유지보수는 **서버 본체(server.Server) 행**의 값이므로, 알람 하위 자원에서
      COALESCE로 거슬러 올라갈 필요 없이 server.Server 행을 NAME으로 직접 식별한다.

안전 제약:
    - 읽기 전용 SELECT 단일문만 생성한다. DML/DDL 절대 금지.
    - 외부 입력(alarm_name/server_name)은 반드시 `_sql_literal`로 이스케이프한다
      (DBHub execute_sql은 파라미터 바인딩 미지원).
    - D-022: RESOURCE_CONF_ID JOIN 절대 금지. 고정 SQL에 불확실 조건(is_lob 등) 추가 금지.
    - graceful degradation: 미등록 db_id / 조회 실패 / 빈 결과에 예외를 전파하지 않는다 —
      `source="unavailable"` 또는 부분 None을 반환한다.
    - 캐시/타임아웃은 구현하지 않는다(enricher 책임) — 순수 fetch만 수행한다.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Optional

from src.alarm.domain.alarm import AlarmEvent

logger = logging.getLogger(__name__)

# 서버 매칭 식 — {server_name}에 안전 처리된 SQL 리터럴이 보간된다.
# SVR은 server.Server 행(= cmm_resource) 별칭이므로 SVR.NAME = 폴스타 등록 서버명이다.
_DEFAULT_SERVER_MATCH = "SVR.NAME = {server_name}"

# db_id별 서버 매칭 식 분기 (미등록 db_id는 기본식 사용).
# 공동존 폴스타(gp/yd)는 r.name(=SVR.NAME) 매칭을 명시 고정한다 — hostname 매칭 금지.
_SERVER_MATCH_BY_DB_ID: dict[str, str] = {
    "polestar_cm_gp": _DEFAULT_SERVER_MATCH,
    "polestar_cm_yd": _DEFAULT_SERVER_MATCH,
}

# 폴스타 테이블은 'polestar' 스키마에 존재한다 — search_path에 포함되지 않으므로
# 반드시 스키마로 한정해야 한다 (미한정 시 public에서 relation does not exist).
# 인스턴스별 스키마가 다르면 여기서 db_id별로 분기한다 (미등록 db_id는 기본값 사용).
_DEFAULT_SCHEMA = "polestar"
_SCHEMA_BY_DB_ID: dict[str, str] = {}


def _table(db_id: str, name: str) -> str:
    """db_id에 해당하는 스키마로 한정된 테이블 참조를 반환한다."""
    schema = _SCHEMA_BY_DB_ID.get(db_id, _DEFAULT_SCHEMA)
    return f"{schema}.{name}"


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다 (작은따옴표 이스케이프).

    DBHub execute_sql이 파라미터 바인딩을 지원하지 않으므로, 폴스타 알람 정의명·서버명
    등 외부 입력 문자열은 반드시 이 함수를 거쳐 보간한다 (Plan 47 §5.3 계승).
    """
    cleaned = value.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def build_resource_signal_sql(db_id: str, server_name: str) -> str:
    """서버 본체(server.Server)의 중요도/유지보수 신호 조회 SQL을 조립한다.

    읽기 전용 SELECT 단일문. server.Server 행을 NAME(서버명)으로 직접 식별한다
    (알람 하위 자원에서 시작하지 않으므로 PLATFORM_RESOURCE_ID COALESCE 불필요).
    - DTIME IS NULL — 삭제된 리소스 제외
    - RESOURCE_CONF_ID JOIN 미포함 (D-022)
    - LIMIT 1 — 단일 서버행만 필요
    - SVR.ID(resource_id) — Plan 60 E4 노드 식별용(다홉 조상 BFS 시작점). 동일 쿼리·하위호환.
    """
    server_match = _SERVER_MATCH_BY_DB_ID.get(db_id, _DEFAULT_SERVER_MATCH).format(
        server_name=_sql_literal(server_name),
    )
    t_resource = _table(db_id, "cmm_resource")
    return (
        "SELECT\n"
        "    SVR.ID AS resource_id,\n"
        "    SVR.IMPORTANCE_ID AS importance_id,\n"
        "    SVR.IS_MAINTENANCE AS maintenance\n"
        f"FROM {t_resource} SVR\n"
        "WHERE SVR.DTIME IS NULL\n"
        "  AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        f"  AND {server_match}\n"
        "LIMIT 1"
    )


def build_noti_policy_sql(db_id: str, alarm_name: str) -> str:
    """폴스타 알림정책(통보대상) 존재 여부 조회 SQL을 조립한다.

    읽기 전용 SELECT 단일문. 알람 정의명(D.NAME)으로 정의를 식별하고, 해당 정의에
    매핑된 통보정의(cmm_alarm_def_noti) 행 수를 센다.

    조인 규칙(query_generator 템플릿 실측 — src/prompts/query_generator.py:413):
        DN.DEFINITION_ID = D.MASTERDEFINITION_ID
    (cmm_alarm 이력 조인의 CA.DEFINITION_ID = D.ID 와 키가 다름에 주의)

    보수적 판정: 통보정의 행이 존재하면 "통보대상이 지정됨"으로 본다. 행이 없을 때
    "비통보로 설정됨"이라 단정하지 않는다(부재는 기본/상속 통보일 수 있고, 명시적
    suppress를 가리키는 컬럼 의미가 불확실하므로). → 행 부재는 None으로 처리한다.
    - RESOURCE_CONF_ID JOIN 미포함 (D-022)
    """
    t_alarm_def = _table(db_id, "cmm_alarm_def")
    t_noti = _table(db_id, "cmm_alarm_def_noti")
    return (
        "SELECT COUNT(*) AS noti_count\n"
        f"FROM {t_alarm_def} D\n"
        f"JOIN {t_noti} DN ON DN.DEFINITION_ID = D.MASTERDEFINITION_ID\n"
        f"WHERE D.NAME = {_sql_literal(alarm_name)}"
    )


def build_dependency_sql(db_id: str, server_name: str) -> str:
    """서버 본체(server.Server)의 가용성 의존 부모 리소스 상태 조회 SQL을 조립한다.

    (Plan 52 §3.6 의존성/토폴로지 억제, Phase E2 — `collect_dependency=True`에서만 실행)

    읽기 전용 SELECT 단일문. server.Server 행을 NAME(서버명)으로 식별하고, 그 행의
    `AVAIL_DEPEND_RESOURCE_ID`(가용성 의존 부모)를 키로 **같은 cmm_resource를 self-join**
    하여 부모의 `AVAIL_STATUS`(0=정상, ≠0=비정상, Plan 52 §2.2)를 조회한다. 부모가
    비정상이면 자식 알람은 연쇄 노이즈로 억제 가능(판정은 결정 파이프라인 책임 — 본
    SQL은 신호만 수집한다).

    동작/안전:
    - `SVR.AVAIL_DEPEND_RESOURCE_ID`가 NULL이면(도커 testdata 등 의존성 미설정) JOIN이
      0행 → 부모 None → **억제 안 함(보수적)**. (실측: 도커 폴스타 testdata는 전부 NULL)
    - RESOURCE_CONF_ID JOIN 미포함 (D-022).
    - DTIME IS NULL — 삭제된 리소스 제외(자식 SVR·부모 P 양쪽).
    - LIMIT 1 — 단일 부모행만 필요.
    - server_match는 `build_resource_signal_sql`과 동일하게 `_SERVER_MATCH_BY_DB_ID`
      (gp/yd는 SVR.NAME) 재사용 — hostname 매칭 금지.
    - `AVAIL_DEPEND_RESOURCE_ID_2`(보조 의존)는 E2 1차에서 **생략**한다. 필요 시 향후
      JOIN 조건을 `ON P.ID = SVR.AVAIL_DEPEND_RESOURCE_ID OR P.ID =
      SVR.AVAIL_DEPEND_RESOURCE_ID_2`로 확장(다부모 OR 매칭 — LIMIT 1과 정렬 규칙 별도 검토).
    """
    server_match = _SERVER_MATCH_BY_DB_ID.get(db_id, _DEFAULT_SERVER_MATCH).format(
        server_name=_sql_literal(server_name),
    )
    t_resource = _table(db_id, "cmm_resource")
    return (
        "SELECT\n"
        "    P.AVAIL_STATUS AS parent_avail_status\n"
        f"FROM {t_resource} SVR\n"
        f"JOIN {t_resource} P ON P.ID = SVR.AVAIL_DEPEND_RESOURCE_ID\n"
        "WHERE SVR.DTIME IS NULL\n"
        "  AND SVR.RESOURCE_TYPE = 'server.Server'\n"
        f"  AND {server_match}\n"
        "  AND P.DTIME IS NULL\n"
        "LIMIT 1"
    )


def _row_value(row: dict[str, Any], key: str) -> Any:
    """행에서 컬럼 값을 대소문자 무시하고 조회한다 (DB 드라이버별 키 케이스 상이)."""
    if key in row:
        return row[key]
    upper = key.upper()
    if upper in row:
        return row[upper]
    for k, v in row.items():
        if k.lower() == key:
            return v
    return None


def _to_bool(value: Any) -> Optional[bool]:
    """IS_MAINTENANCE 값을 bool로 변환한다 (1/true→True, 0/false→False, 미상→None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "t", "y", "yes"}:
            return True
        if text in {"0", "false", "f", "n", "no"}:
            return False
    return None


def _parse_importance(rows: list[dict[str, Any]]) -> Optional[Any]:
    """중요도 신호를 파싱한다. **원값을 그대로 보존**한다 (낮음/높음 매핑은 정책 계층 책임).

    행이 없거나 값이 비어 있으면 None을 반환한다. 예외는 잡아 None으로 강등한다.
    """
    try:
        if not rows:
            return None
        value = _row_value(rows[0], "importance_id")
        if value is None or value == "":
            return None
        return value
    except Exception:  # noqa: BLE001 — graceful degradation: 어떤 파싱 오류도 None으로
        logger.debug("중요도 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_maintenance(rows: list[dict[str, Any]]) -> Optional[bool]:
    """유지보수 상태를 bool로 파싱한다. 행 없음·미상은 None. 예외는 None으로 강등."""
    try:
        if not rows:
            return None
        return _to_bool(_row_value(rows[0], "maintenance"))
    except Exception:  # noqa: BLE001
        logger.debug("유지보수 상태 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_noti_policy(rows: list[dict[str, Any]]) -> Optional[str]:
    """폴스타 알림정책을 보수적으로 판정한다.

    통보정의(cmm_alarm_def_noti) 행이 1건 이상 존재하면 "notify",
    그렇지 않으면 None을 반환한다. **"suppress"는 반환하지 않는다** — 명시적 비통보를
    가리키는 컬럼 의미가 불확실하므로 보수적으로 단정하지 않는다(Plan 52 §6.2/§6.3).
    """
    try:
        if not rows:
            return None
        count = _row_value(rows[0], "noti_count")
        if count is None:
            return None
        return "notify" if int(count) > 0 else None
    except (TypeError, ValueError):
        logger.debug("알림정책 카운트 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_parent_avail_status(rows: list[dict[str, Any]]) -> Optional[int]:
    """부모 리소스의 가용 상태(AVAIL_STATUS)를 int로 파싱한다 (Plan 52 §3.6 의존성 억제).

    행이 없으면(=의존성 미설정·부모 미존재·미수집) None을 반환한다 — **보수적**(억제 안 함).
    값이 있으면 int로 변환하고, 변환 실패·미상은 None으로 강등한다(graceful degradation).
    AVAIL_STATUS 의미: 0=정상, 0이 아닌 값=비정상(Plan 52 §2.2) — 의미 해석은 정책 계층 책임.
    """
    try:
        if not rows:
            return None
        value = _row_value(rows[0], "parent_avail_status")
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        logger.debug("부모 가용상태 파싱 실패 — None 처리", exc_info=True)
        return None


def _parse_resource_id(rows: list[dict[str, Any]]) -> Optional[str]:
    """서버 본체의 리소스 ID(SVR.ID)를 파싱한다 (Plan 60 E4 다홉 조상 BFS 시작점).

    행이 없거나 값이 비면 None(다홉 미산출·1홉 폴백). 예외는 None으로 강등.
    """
    try:
        if not rows:
            return None
        value = _row_value(rows[0], "resource_id")
        if value is None or value == "":
            return None
        return str(value)
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.debug("리소스 ID 파싱 실패 — None 처리", exc_info=True)
        return None


# ── noise_ctx 동결 계약 키 단일 출처(§7.2) ─────────────────────────
# 세 구성점(_unavailable·정상 fetch dict·enricher)이 이 목록을 공유해 키 불일치를
# 원천 차단한다. Redis 캐시(alarm:noisectx:*)로 직렬화 왕복하므로 계약이 안정해야 한다.
# root_notified는 **동적**이라 여기 포함하지 않는다 — enricher가 캐시 이후 신선 산출한다
# (§6.2 하이브리드). 게이트는 noise_ctx.get()으로 소비하므로 구버전 캐시(신규 키 부재)와
# 하위호환된다.
_NOISE_CTX_KEYS: tuple[str, ...] = (
    "importance_id",
    "maintenance",
    "noti_policy",
    "parent_avail_status",      # 1홉 의존성(하위호환 유지)
    "cascaded",                 # (E4) 다홉 조상 비정상 여부(bool). 미수집/비PostgreSQL이면 None
    "root_resource",            # (E4) 최상위 비정상 조상 리소스 ID
    "root_resource_name",       # (E4) root 리소스 NAME — enricher의 root_notified 산출용
    "change_nearby",            # (E5) 알람 직전 창에 변경 근접 여부(bool). 미산출/off면 None
    "change_candidates",        # (E5) 근접 변경 후보 리스트(dict, 감사·원인성 표기용). 미산출/off면 None
)


def _empty_noise_ctx() -> dict[str, Any]:
    """계약 키를 전부 None으로 채운 dict(source 제외)를 반환한다(§7.2 단일 출처)."""
    return {k: None for k in _NOISE_CTX_KEYS}


def _unavailable() -> dict[str, Any]:
    """신호 수집 불가 시 반환하는 동결 dict (모든 신호 None, source='unavailable')."""
    ctx = _empty_noise_ctx()
    ctx["source"] = "unavailable"
    return ctx


def _is_postgres_dialect(db_id: str) -> bool:
    """db_id의 엔진이 PostgreSQL인지 판정한다 (Plan 60 E4 — b0(DB2) 다홉 제외).

    domain_config의 db_engine을 단일 출처로 사용한다(D-057 계승). 미등록 db_id·판정 실패는
    보수적으로 False(다홉 미시도 → 1홉 폴백)로 처리한다. E4 1차 범위 = gp/yd/로컬(PostgreSQL).
    """
    try:
        from src.routing.domain_config import get_domain_by_id

        domain = get_domain_by_id(db_id)
        return bool(domain is not None and domain.db_engine == "postgresql")
    except Exception:  # noqa: BLE001 — 판정 실패는 보수적 False(1홉 폴백)
        return False


class PolestarNoiseContextRepository:
    """폴스타 DB에서 알람 노이즈 컨텍스트(중요도/유지보수/알림정책)를 고정 SQL로 조회한다."""

    def __init__(self, registry, alarm_cfg) -> None:  # noqa: ANN001
        """리포지토리를 초기화한다.

        Args:
            registry: DBRegistry (get_client/is_registered 사용)
            alarm_cfg: AlarmConfig (인터페이스 일관성용 — 본 Phase E1 SQL은 미사용)
        """
        self._registry = registry
        self._alarm_cfg = alarm_cfg
        # (Plan 60 E4) 다홉 토폴로지 로더 — db_id별 정적 엣지 그래프를 캐시(이벤트 간 유지).
        # 지연 생성(첫 다홉 조회 시)으로 multi_hop off 경로에 부담을 주지 않는다.
        self._topology_loader = None
        # (Plan 60 E5) 변경 피드 어댑터(무상태) — 지연 생성(첫 변경 상관 조회 시).
        self._change_feed = None

    def is_db_registered(self, db_id: str) -> bool:
        """event.db_id가 DB 레지스트리에 등록되어 있는지 확인한다."""
        return bool(self._registry.is_registered(db_id))

    async def _compute_multi_hop_cascade(
        self,
        client,  # noqa: ANN001 — DBClient (execute_sql)
        db_id: str,
        resource_id: Optional[str],
        *,
        topology_max_hops: int,
        topology_cache_ttl_seconds: int,
    ) -> tuple[Optional[bool], Optional[str], Optional[str]]:
        """다홉 조상 연쇄를 산출한다 (E4, §6.2). (cascaded, root_resource, root_resource_name).

        정적 엣지 그래프(캐시) 로드 → 이벤트 리소스의 조상 BFS(홉 상한) → 조상 ID들의
        비정상 상태(AVAIL_STATUS≠0) 신선 조회 → is_cascaded/find_root/name_of 산출.
        리소스 ID 미해소·그래프 로드 실패·상태 조회 실패는 (None, None, None)(1홉 폴백).
        """
        if resource_id is None:
            return None, None, None
        if self._topology_loader is None:
            from src.alarm.infrastructure.topology_loader import TopologyLoader

            self._topology_loader = TopologyLoader()
        graph = await self._topology_loader.load_graph(
            client,
            db_id,
            cache_ttl_seconds=topology_cache_ttl_seconds,
            max_hops=topology_max_hops,
        )
        if graph is None:
            return None, None, None
        ancestors = graph.ancestors(resource_id, max_hops=topology_max_hops)
        state = await self._topology_loader.fetch_ancestor_state(
            client, db_id, ancestors
        )
        if state is None:
            return None, None, None
        abnormal, anc_names = state
        cascaded = graph.is_cascaded(resource_id, abnormal)
        root = graph.find_root(resource_id, abnormal)
        # root 이름: 조상 신선 조회 맵 우선(부모 없는 root 보강) → 엣지 그래프 name_of 폴백.
        root_name = None
        if root is not None:
            root_name = anc_names.get(root) or graph.name_of(root)
        return cascaded, root, root_name

    async def _compute_change_correlation(
        self,
        client,  # noqa: ANN001 — DBClient (execute_sql)
        event: AlarmEvent,
        resource_id: Optional[str],
        *,
        change_window_seconds: int,
    ) -> tuple[Optional[bool], Optional[list]]:
        """변경 근접 상관을 산출한다 (E5, §7.2). (change_nearby, change_candidates).

        변경 피드(cmm_resource_lifecycle_history)에서 알람 직전 창의 변경을 조회하고,
        영향 리소스(resource_id)로 오버레이 매칭해 원인 후보를 만든다. **억제가 아니라
        승격 신호**다(게이트 step9 promote). 피드 부재·조회 실패·빈 결과 → (False, [])
        (변경 없음). 조회 자체가 예외로 실패하면 호출부 try/except가 (None, None)로 강등한다.

        기준 시각(reference_epoch)은 알람 발생 시각(`event.alarm_time`)의 epoch다. dev 더미는
        event_time 단위가 미확정이라 실 오버레이가 나오지 않는다(graceful — 빈 후보).
        alarm_time이 naive datetime이면 timestamp()가 로컬 tz를 가정한다(실운영 편입 시
        폴스타 timestamp 단위·tz 관례로 재확인 — change_feed 주석 참조).
        """
        from src.alarm.domain.change_correlation import overlay_changes

        if self._change_feed is None:
            from src.alarm.infrastructure.change_feed import ChangeFeed

            self._change_feed = ChangeFeed()

        try:
            reference_epoch = int(event.alarm_time.timestamp())
        except (AttributeError, ValueError, OSError):
            # alarm_time 부재·변환 불가 → 변경 상관 스킵(원인성 미보강, 보수적).
            return False, []

        # 영향 리소스 스코프: E4용으로 파싱된 SVR.ID를 재사용(있으면 그 리소스로 스코프).
        resource_ids = [resource_id] if resource_id else None
        changes = await self._change_feed.fetch_recent_changes(
            client,
            event.db_id,
            change_window_seconds,
            reference_epoch=reference_epoch,
            resource_ids=resource_ids,
        )
        affected = {str(resource_id)} if resource_id else None
        candidates = overlay_changes(
            (reference_epoch - max(int(change_window_seconds), 0), reference_epoch),
            changes,
            affected_resource_ids=affected,
        )
        # noise_ctx는 Redis로 직렬화 왕복하므로 후보는 plain dict로 저장한다(JSON 직렬화 가능).
        change_candidates = [asdict(c) for c in candidates]
        return bool(candidates), change_candidates

    async def fetch(
        self,
        event: AlarmEvent,
        *,
        collect_dependency: bool = False,
        multi_hop: bool = False,
        topology_max_hops: int = 5,
        topology_cache_ttl_seconds: int = 86400,
        change_correlation: bool = False,
        change_window_seconds: int = 3600,
    ) -> dict[str, Any]:
        """노이즈 컨텍스트를 수집하여 동결 dict로 반환한다 (Plan 52 §8.2 + Plan 60 E4 계약).

        Args:
            event: 알람 이벤트(db_id/server_name/alarm_name 사용).
            collect_dependency: True면 의존성/부모 상태(E2, §3.6)를 **별도 SQL**로 수집한다.
                기본 False(E1 동작 동결) — 의존성 SQL을 실행하지 않고
                `parent_avail_status=None`을 반환한다.
            multi_hop: True면 다홉 토폴로지 연쇄(E4, §6.2)를 산출한다 — 조상 BFS + 조상
                비정상 상태 신선 조회로 `cascaded`/`root_resource`/`root_resource_name`을
                채운다. 기본 False면 미산출(None → 게이트 1홉 폴백). **PostgreSQL(gp/yd)만
                시도**하며 b0(DB2)/비PostgreSQL·조회 실패는 None(1홉 폴백·보수적).
            topology_max_hops: 다홉 BFS 홉 상한(비용/순환 가드).
            topology_cache_ttl_seconds: 정적 엣지 그래프 캐시 TTL(변경 드묾 — 장기).
            change_correlation: True면 변경 피드(E5, §7.2)를 조회·오버레이해 change_nearby/
                change_candidates를 채운다. 기본 False면 미산출(None). **PostgreSQL(gp/yd)만
                시도**하며 b0(DB2)/비PostgreSQL·조회 실패는 None(미보강·보수적).
            change_window_seconds: 알람 직전 이 창(초) 내 변경만 오버레이한다(E5).

        반환 dict (동결 계약 = `_NOISE_CTX_KEYS` + source):
            {"importance_id": int|str|None,   # 원값 그대로
             "maintenance": bool|None,
             "noti_policy": "notify"|None,     # 보수적 — "suppress" 미반환
             "parent_avail_status": int|None,  # 부모 AVAIL_STATUS(E2·1홉). off면 None
             "cascaded": bool|None,            # (E4) 다홉 조상 비정상 여부. 미산출이면 None
             "root_resource": str|None,        # (E4) 최상위 비정상 조상 리소스 ID
             "root_resource_name": str|None,   # (E4) root 리소스 NAME(root_notified 산출용)
             "change_nearby": bool|None,       # (E5) 변경 근접 여부. off/미산출이면 None
             "change_candidates": list|None,   # (E5) 근접 변경 후보(dict 리스트). off/미산출이면 None
             "source": "polestar_db"|"unavailable"}
             (root_notified는 enricher가 캐시 이후 신선 산출 — 이 dict에는 미포함)

        graceful degradation:
            - 미등록 db_id → 즉시 source="unavailable" (DB 미접근).
            - 조회/연결 예외 → 잡아서 source="unavailable" (예외 전파 금지).
            - 빈 결과/파싱 실패 → 해당 신호만 None, source="polestar_db" (부분 반환).
            - **의존성 조회는 resource/noti와 독립된 별도 try/except** — 의존성 조회 실패가
              importance/maintenance/noti_policy 신호를 절대 손실시키지 않는다(D-048 계승).
        """
        if not self.is_db_registered(event.db_id):
            logger.warning(
                "노이즈 컨텍스트 조회 건너뜀 — 미등록 db_id: %s (alarm_id=%s)",
                event.db_id,
                event.alarm_id,
            )
            return _unavailable()

        resource_sql = build_resource_signal_sql(event.db_id, event.server_name)
        noti_sql = build_noti_policy_sql(event.db_id, event.alarm_name)
        resource_rows: list[dict[str, Any]] = []
        noti_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        # (Plan 60 E4) 다홉 산출값 — 미산출(1홉 폴백)이면 None 유지.
        cascaded: Optional[bool] = None
        root_resource: Optional[str] = None
        root_resource_name: Optional[str] = None
        # (Plan 60 E5) 변경 상관 산출값 — 미산출(off·비PostgreSQL·실패)이면 None 유지.
        change_nearby: Optional[bool] = None
        change_candidates: Optional[list] = None
        got_any = False
        try:
            async with self._registry.get_client(event.db_id) as client:
                # resource 신호(중요도/유지보수)와 알림정책을 **독립 수집** — 한쪽 실패
                # (테이블 부재·권한 등)가 다른 쪽 신호를 손실시키지 않는다
                # (§5 부분 실패 허용·graceful degradation, Plan 47 enricher 계승).
                try:
                    resource_result = await client.execute_sql(resource_sql)
                    resource_rows = list(getattr(resource_result, "rows", []) or [])
                    got_any = True
                except Exception as exc:  # noqa: BLE001 — resource만 실패, noti는 계속
                    logger.warning(
                        "노이즈 컨텍스트 resource 조회 실패 — db_id=%s alarm_id=%s: %s",
                        event.db_id, event.alarm_id, exc,
                    )
                try:
                    noti_result = await client.execute_sql(noti_sql)
                    noti_rows = list(getattr(noti_result, "rows", []) or [])
                    got_any = True
                except Exception as exc:  # noqa: BLE001 — noti만 실패, resource는 보존
                    logger.warning(
                        "노이즈 컨텍스트 noti 조회 실패 — db_id=%s alarm_id=%s: %s",
                        event.db_id, event.alarm_id, exc,
                    )
                # 의존성/부모 상태(E2, §3.6)는 **게이팅(collect_dependency)** 뒤에서만,
                # 그리고 resource/noti와 **독립된 별도 try/except**로 수집한다 — 의존성
                # 조회 실패가 importance/maintenance/noti_policy 신호를 절대 손실시키지
                # 않는다(D-048 graceful degradation 계승). 기본(False)이면 SQL 미실행.
                if collect_dependency:
                    dependency_sql = build_dependency_sql(
                        event.db_id, event.server_name
                    )
                    try:
                        dependency_result = await client.execute_sql(dependency_sql)
                        dependency_rows = list(
                            getattr(dependency_result, "rows", []) or []
                        )
                        got_any = True
                    except Exception as exc:  # noqa: BLE001 — 의존성만 실패, 타 신호 보존
                        logger.warning(
                            "노이즈 컨텍스트 dependency 조회 실패 — db_id=%s alarm_id=%s: %s",
                            event.db_id, event.alarm_id, exc,
                        )
                # 다홉 토폴로지 연쇄(E4, §6.2)는 **게이팅(multi_hop)** 뒤에서만, PostgreSQL
                # (gp/yd)일 때만, 그리고 **독립된 별도 try/except**로 산출한다 — 다홉 조회
                # 실패가 타 신호를 손실시키지 않고 미산출(None → 게이트 1홉 폴백)로 강등된다.
                if multi_hop and _is_postgres_dialect(event.db_id):
                    try:
                        cascaded, root_resource, root_resource_name = (
                            await self._compute_multi_hop_cascade(
                                client,
                                event.db_id,
                                _parse_resource_id(resource_rows),
                                topology_max_hops=topology_max_hops,
                                topology_cache_ttl_seconds=topology_cache_ttl_seconds,
                            )
                        )
                        got_any = True
                    except Exception as exc:  # noqa: BLE001 — 다홉만 실패, 타 신호 보존
                        logger.warning(
                            "노이즈 컨텍스트 다홉 연쇄 산출 실패 — 1홉 폴백: "
                            "db_id=%s alarm_id=%s: %s",
                            event.db_id, event.alarm_id, exc,
                        )
                        cascaded = root_resource = root_resource_name = None
                # 변경 상관(E5, §7.2)은 **게이팅(change_correlation)** 뒤에서만, PostgreSQL
                # (gp/yd)일 때만, 그리고 **독립된 별도 try/except**로 산출한다 — 변경 피드 조회
                # 실패가 타 신호를 손실시키지 않고 미산출(None → 게이트 미보강)로 강등된다.
                if change_correlation and _is_postgres_dialect(event.db_id):
                    try:
                        change_nearby, change_candidates = (
                            await self._compute_change_correlation(
                                client,
                                event,
                                _parse_resource_id(resource_rows),
                                change_window_seconds=change_window_seconds,
                            )
                        )
                        got_any = True
                    except Exception as exc:  # noqa: BLE001 — 변경 상관만 실패, 타 신호 보존
                        logger.warning(
                            "노이즈 컨텍스트 변경 상관 산출 실패 — 미보강: "
                            "db_id=%s alarm_id=%s: %s",
                            event.db_id, event.alarm_id, exc,
                        )
                        change_nearby = change_candidates = None
        except Exception as exc:  # noqa: BLE001 — 연결 자체 실패는 unavailable
            logger.warning(
                "노이즈 컨텍스트 연결 실패 — db_id=%s alarm_id=%s: %s",
                event.db_id, event.alarm_id, exc,
            )
            return _unavailable()

        # 연결은 됐으나 모든 조회 실패 → 신호 없음 → 보수적 unavailable
        if not got_any:
            return _unavailable()
        # 동결 계약 단일 출처(_NOISE_CTX_KEYS)로 dict를 구성한 뒤 신호를 채운다(§7.2).
        context = _empty_noise_ctx()
        context["importance_id"] = _parse_importance(resource_rows)
        context["maintenance"] = _parse_maintenance(resource_rows)
        context["noti_policy"] = _parse_noti_policy(noti_rows)
        # collect_dependency=False면 dependency_rows=[] → None(E1 동작 동결).
        context["parent_avail_status"] = _parse_parent_avail_status(dependency_rows)
        # (E4) 다홉 산출값 — multi_hop off·비PostgreSQL·실패면 None(1홉 폴백).
        context["cascaded"] = cascaded
        context["root_resource"] = root_resource
        context["root_resource_name"] = root_resource_name
        # (E5) 변경 상관 산출값 — change_correlation off·비PostgreSQL·실패면 None(미보강).
        context["change_nearby"] = change_nearby
        context["change_candidates"] = change_candidates
        context["source"] = "polestar_db"
        logger.debug(
            "노이즈 컨텍스트 조회 완료: db_id=%s server=%s alarm=%s ctx=%r",
            event.db_id,
            event.server_name,
            event.alarm_name,
            context,
        )
        return context
