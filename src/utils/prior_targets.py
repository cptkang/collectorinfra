"""조사 대상 해소 공통 모듈 (Plan 78 W1 · G2 + G5 동시 해소).

복합 질의에서 **선행 조회 결과가 후속 조사의 대상으로 흐르게** 하고(G2), 세 소비 경로가
**같은 함수**를 쓰게 한다(G5). 각자 구현하면 개선이 한쪽에만 들어간다(§2.2 G5 실측).

    process_query          ← 채팅 · 실시간 프로세스 조회
    fault_diagnosis        ← 채팅 · sre_agent 위임 (CW-B)
    investigation_trigger  ← 이벤트 · 알람 자동 조사 (CW-A)

## 왜 `utils`인가 (SPEC-composite-orchestration C-1)

`plans/78` §6.1은 `src/orchestration/`을 지정했으나 **성립하지 않는다** — 소비자 셋 중 둘이
`application` 계층(`src/nodes/`·`noise_gate/application/nodes/`)이고, `application`의 허용
의존은 `{domain, config, utils, prompts, infrastructure}`라 orchestration을 import할 수 없다.
계획서의 "arch_check 정합" 주석은 **모듈 자신의 나가는 의존만** 보고 **소비자의 들어오는
의존**을 놓쳤다. `utils`는 최하위라 모든 계층이 쓸 수 있고, `noise_gate → src.utils`는 이미
존재하는 허용 최소 집합이라 역방향 결합도 신설되지 않는다.

**대가**: `utils`는 `src.config`를 import할 수 없다 → 상한·플래그는 **인자로 주입**한다.
유사어 조회(2단)·LLM 컬럼 지목(3단)도 **주입 콜러블**로 받는다.

## 해석 3단 + 결정적 확정 (§3.2.3 · P2)

3단은 **컬럼명만** 고른다. 값은 코드가 그 컬럼에서 그대로 읽는다 — LLM이 대상 값을
생성·재해석하지 않는다(TDG 고정). 출력이 *실재하는 컬럼명의 닫힌 집합*으로 제한되므로
Action-Selector 패턴이다. 1·2단에서 확정되면 **3단은 호출하지 않는다**.

확정(3-2)은 LLM 지목 여부와 무관하게 **항상** 적용되며, **검증 탈락 시 3단으로 되돌아가지
않는다** — 사유를 남기고 대상 미확정으로 종료한다(무한 재시도 금지).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.utils.query_gen_common import (
    HOST_IDENTIFIER_FIELDS,
    is_demonstrative_identifier,
    is_server_identity_col,
    looks_like_process_rows,
)

logger = logging.getLogger(__name__)

# 해소 출처 — 우선순위 순(78 W1-4). ①이 ②를 이긴다: 사용자가 명시 지목했으면 그것이 우선.
SOURCE_FILTER = "filter_conditions"
SOURCE_PRIOR = "prior_targets"
SOURCE_PREVIOUS = "previous_entities"
SOURCE_ALARM = "alarm_payload"

# hostname 계열 컬럼 힌트 — 폴스타는 server_name ≠ hostname이므로 섞으면 0건이 된다(D-046/D-061).
_HOSTNAME_COL_HINTS: tuple[str, ...] = ("hostname", "host_name")

#: 멀티 DB 병합이 행마다 붙이는 **출처 태그** 키(`multi_db_executor._merge_results`).
#: 대상의 db_id 정본으로 쓰되(D-176), 서버 식별자 컬럼 후보에서는 배제한다 — 값이
#: "polestar_cm_gp" 같은 DB 식별자라 hostname으로 오인되면 조회가 0건이 된다.
SOURCE_DB_KEY = "_source_db"
# filter_conditions/previous_entities의 field가 hostname을 가리키는 표면형.
_HOSTNAME_FIELD_NAMES: frozenset[str] = frozenset({"hostname", "host_name", "호스트명"})

# 대상 미확정 사유 코드 — 침묵 폴백 금지(80 §5.4-④). 사용자 응답·감사에 그대로 싣는다.
REASON_NO_ROWS = "no_rows"
REASON_PROCESS_ROWS = "process_rows"
REASON_NO_COLUMN = "no_identifier_column"
REASON_HALLUCINATED_COLUMN = "column_not_in_rows"
REASON_DEMONSTRATIVE = "demonstrative_value"
REASON_EMPTY_VALUE = "empty_value"


class TargetRef(BaseModel):
    """조사 대상 1건의 타입 계약 (78 W1-6).

    `dict` 키 오타가 런타임까지 사는 구조 위에 G5 대칭화를 얹지 않기 위한 계약이다.

    **필드가 넷인 이유**(SPEC C-2): `mcp_server` 고수준 도구의 식별 인자가 갈린다 —
    `polestar_metric_trend`·`polestar_resource_status`는 `server_name`을,
    `polestar_os_config`·`polestar_process_snapshot`은 `hostname`을 받는다. 폴스타는
    server_name ≠ hostname이므로(D-046) 한 필드로 뭉개면 절반이 0건이 된다.

    상태 저장은 **`dict`**(`model_dump()`)로 한다 — `AgentState`는 `TypedDict`이고 LangGraph
    체크포인터가 직렬화하므로 모델 객체를 그대로 싣지 않는다(78 W1-6).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    server_name: Optional[str] = None
    hostname: Optional[str] = None
    ip: Optional[str] = None
    db_id: Optional[str] = None

    @model_validator(mode="after")
    def _require_identifier(self) -> "TargetRef":
        """식별자가 하나도 없는 대상은 만들지 않는다.

        db_id만 있는 TargetRef는 "어느 호스트인지 모르는 조사 대상"이라 의미가 없다.
        """
        if not (self.server_name or self.hostname or self.ip):
            raise ValueError(
                "TargetRef에는 server_name·hostname·ip 중 최소 하나가 있어야 합니다"
            )
        return self

    @property
    def key(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """중복 제거용 키(hostname 우선, 없으면 server_name/ip)."""
        return (self.hostname or None, self.server_name or None, self.ip or None)


class TargetResolution(BaseModel):
    """대상 해소 결과 컨테이너 — 절단·탈락 사실을 **함께** 싣는다.

    절단·탈락을 로그로만 남기면 사용자는 부분 결과를 전체로 오인한다(침묵 폴백 금지).
    """

    model_config = ConfigDict(extra="forbid")

    targets: list[TargetRef] = Field(default_factory=list)
    source: str = ""
    column: str = ""
    truncated: bool = False
    truncated_count: int = 0
    dropped: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: int = 0

    @property
    def resolved(self) -> bool:
        """대상이 하나라도 확정됐는가."""
        return bool(self.targets)

    def as_state_value(self) -> list[dict[str, Any]]:
        """`AgentState`에 실을 **dict** 목록(체크포인터 직렬화 회귀 방지 — 78 W1-6)."""
        return [t.model_dump() for t in self.targets]


def _drop(reason: str, **detail: Any) -> dict[str, Any]:
    """탈락 사유 레코드를 만든다(구조화 — 로그로만 끝내지 않는다)."""
    return {"reason": reason, **detail}


def _pick_identifier_column(
    columns: list[str],
    *,
    synonym_lookup: Optional[Callable[[list[str]], Optional[str]]] = None,
    llm_pick_column: Optional[Callable[[list[str]], Optional[str]]] = None,
) -> tuple[str, int]:
    """어느 컬럼이 서버 식별자인지 **해석 3단**으로 고른다.

    1·2단에서 확정되면 3단(LLM)은 **호출하지 않는다** — 비용·지연·비결정성 최소화.

    Args:
        columns: 결과 행의 컬럼명 목록
        synonym_lookup: 2단 — 유사어 저장소 조회(주입). 컬럼명 하나 또는 None 반환
        llm_pick_column: 3단 — LLM 컬럼 지목(주입). 컬럼명 하나 또는 None 반환

    Returns:
        (선택된 컬럼명 또는 "", LLM 호출 횟수)
    """
    # 내부 태그는 후보에서 제외한다(D-176) — 사용자 데이터가 아니라 병합이 붙인 메타다.
    columns = [c for c in columns if str(c) != SOURCE_DB_KEY]

    # 1단 — 결정적 매칭. hostname 계열을 server_name 계열보다 우선한다(조회 키가 hostname).
    hostname_cols = [c for c in columns if any(h in str(c).lower() for h in _HOSTNAME_COL_HINTS)]
    if hostname_cols:
        return hostname_cols[0], 0
    exact = [c for c in columns if str(c).strip().lower() in HOST_IDENTIFIER_FIELDS]
    if exact:
        return exact[0], 0
    identity = [c for c in columns if is_server_identity_col(c)]
    if identity:
        return identity[0], 0

    # 2단 — 유사어 저장소(기존 자산 재사용). 없으면 건너뛴다.
    if synonym_lookup is not None:
        try:
            picked = synonym_lookup(list(columns))
        except Exception:  # noqa: BLE001 — 유사어 조회 실패는 3단으로 넘긴다
            logger.warning("prior_targets 2단 유사어 조회 실패 — 3단으로", exc_info=True)
            picked = None
        if picked:
            return str(picked), 0

    # 3단 — LLM 컬럼 지목. **컬럼명만** 고른다(값 생성 금지).
    if llm_pick_column is not None:
        picked = llm_pick_column(list(columns))
        if picked:
            return str(picked), 1
        return "", 1

    return "", 0


def build_prior_targets(
    rows: list[dict] | None,
    *,
    db_id: Optional[str] = None,
    max_targets: int = 10,
    synonym_lookup: Optional[Callable[[list[str]], Optional[str]]] = None,
    llm_pick_column: Optional[Callable[[list[str]], Optional[str]]] = None,
) -> TargetResolution:
    """선행 조회 결과 행에서 조사 대상을 추출한다 (78 W1-3 · 해석 3단 + 결정적 확정).

    Args:
        rows: 선행 task 결과 행
        db_id: 대상이 속한 DB(있으면 각 TargetRef에 실는다)
        max_targets: fan-out 상한. 초과분은 **절단하고 절단 사실을 결과에 실는다**
        synonym_lookup: 해석 2단 콜러블(주입)
        llm_pick_column: 해석 3단 콜러블(주입). None이면 3단을 쓰지 않는다

    Returns:
        `TargetResolution` — 대상·출처·컬럼·절단·탈락 사유·LLM 호출 횟수
    """
    if not rows:
        return TargetResolution(source=SOURCE_PRIOR, dropped=[_drop(REASON_NO_ROWS)])

    # 프로세스 결과 행 제외 — `pid`를 가진 행은 서버가 아니다(context_resolver:196이 지적한 함정).
    if looks_like_process_rows(rows):
        return TargetResolution(
            source=SOURCE_PRIOR, dropped=[_drop(REASON_PROCESS_ROWS, row_count=len(rows))]
        )

    first = next((r for r in rows if isinstance(r, dict)), None)
    if first is None:
        return TargetResolution(source=SOURCE_PRIOR, dropped=[_drop(REASON_NO_ROWS)])

    columns = [str(c) for c in first.keys()]
    column, llm_calls = _pick_identifier_column(
        columns, synonym_lookup=synonym_lookup, llm_pick_column=llm_pick_column
    )
    if not column:
        return TargetResolution(
            source=SOURCE_PRIOR,
            llm_calls=llm_calls,
            dropped=[_drop(REASON_NO_COLUMN, columns=columns)],
        )

    # 확정 ① 환각 컬럼 차단 — 지목된 컬럼이 **결과 행에 실제 존재**하는가.
    # 검증 탈락 시 3단으로 되돌아가지 않는다(무한 재시도 금지 — 78 W1-3-2).
    if column not in columns:
        return TargetResolution(
            source=SOURCE_PRIOR,
            column=column,
            llm_calls=llm_calls,
            dropped=[_drop(REASON_HALLUCINATED_COLUMN, column=column, columns=columns)],
        )

    is_hostname_col = any(h in column.lower() for h in _HOSTNAME_COL_HINTS)
    targets: list[TargetRef] = []
    dropped: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(column)
        if value is None or not str(value).strip():
            dropped.append(_drop(REASON_EMPTY_VALUE, column=column))
            continue
        # 확정 ② 지시어 차단 — "해당 서버"는 식별자가 아니다.
        if is_demonstrative_identifier(value):
            dropped.append(_drop(REASON_DEMONSTRATIVE, value=str(value)))
            continue
        text = str(value).strip()
        # 행별 출처 우선(D-176 · plans/82 §2.2): 팬아웃 결과는 행마다 `_source_db`를 갖는다.
        # 호출부가 준 db_id 하나를 전 대상에 찍으면 abd00이 공동존에 있어도 은행존으로
        # 표기돼 후속 단계가 엉뚱한 존의 API를 친다(§2.4 — 탐색형을 막던 실질 병목).
        # 태그가 없거나 비면 종전대로 호출부 값 폴백 — 단일 DB 경로는 비트 동일.
        _row_src = row.get(SOURCE_DB_KEY)
        ref_db = str(_row_src).strip() if _row_src and str(_row_src).strip() else db_id
        ref = (
            TargetRef(hostname=text, db_id=ref_db)
            if is_hostname_col
            else TargetRef(server_name=text, db_id=ref_db)
        )
        if ref.key in seen:
            continue
        seen.add(ref.key)
        targets.append(ref)

    # 확정 ③ 상한 절단 — 절단 사실을 결과에 실는다(부분 결과를 전체로 오인시키지 않는다).
    truncated = len(targets) > max_targets
    truncated_count = len(targets) - max_targets if truncated else 0
    if truncated:
        targets = targets[:max_targets]

    return TargetResolution(
        targets=targets,
        source=SOURCE_PRIOR,
        column=column,
        truncated=truncated,
        truncated_count=truncated_count,
        dropped=dropped,
        llm_calls=llm_calls,
    )


def _targets_from_conditions(
    conditions: Any, *, db_id: Optional[str], max_targets: int, source: str
) -> TargetResolution:
    """`[{field, value}]` 형태(filter_conditions/previous_entities)에서 대상을 만든다.

    **같은 종류는 나누고, 다른 종류는 합친다.**

        [{hostname: a}, {hostname: b}]        → 대상 2건   (G3 fan-out — 서로 다른 서버)
        [{hostname: h9}, {server_name: srv-9}] → 대상 1건   (같은 서버의 두 표기)

    후자를 나누면 `sre_diagnose(server_name?, hostname?)` 계약이 깨진다 — 종전
    `fault_diagnosis._extract_targets`가 두 종류를 **누적 병합**하던 동작이 그것이다.
    전자를 합치면 G3(fan-out 갭)이 그대로 남는다. 종류별 목록을 만든 뒤 **위치로 짝지어**
    둘 다 만족시킨다("서버 A(호스트 a), 서버 B(호스트 b)"의 자연스러운 해석이기도 하다).

    `_resolve_hostname`(단일 반환 래퍼)이 종전 순서를 유지할 수 있도록, **처음 매칭된
    field 종류**를 `column`에 남긴다.
    """
    hostnames: list[str] = []
    names: list[str] = []
    dropped: list[dict[str, Any]] = []
    first_field = ""

    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("field", "")).strip().lower()
        if field not in HOST_IDENTIFIER_FIELDS:
            continue
        value = cond.get("value")
        if value is None or not str(value).strip():
            dropped.append(_drop(REASON_EMPTY_VALUE, field=field))
            continue
        if is_demonstrative_identifier(value):
            dropped.append(_drop(REASON_DEMONSTRATIVE, value=str(value)))
            continue
        text = str(value).strip()
        if not first_field:
            first_field = field
        bucket = hostnames if field in _HOSTNAME_FIELD_NAMES else names
        if text not in bucket:
            bucket.append(text)

    targets: list[TargetRef] = []
    for i in range(max(len(hostnames), len(names))):
        targets.append(
            TargetRef(
                hostname=hostnames[i] if i < len(hostnames) else None,
                server_name=names[i] if i < len(names) else None,
                db_id=db_id,
            )
        )

    truncated = len(targets) > max_targets
    truncated_count = len(targets) - max_targets if truncated else 0
    if truncated:
        targets = targets[:max_targets]

    return TargetResolution(
        targets=targets,
        source=source if targets else "",
        column=first_field,
        truncated=truncated,
        truncated_count=truncated_count,
        dropped=dropped,
    )


def resolve_targets(
    *,
    filter_conditions: Any = None,
    prior_targets: Any = None,
    previous_entities: Any = None,
    alarm_payload: Any = None,
    db_id: Optional[str] = None,
    max_targets: int = 10,
) -> TargetResolution:
    """세 진입 경로가 공유하는 **단일** 대상 해소 함수 (78 W1-4 · G5).

    우선순위(전 경로 동일):
        ① 이번 턴 `filter_conditions` → ② `prior_targets` → ③ `previous_entities`
        → ④ (이벤트 경로) 알람 페이로드

    **①이 ②를 이긴다** — 사용자가 이번 턴에 명시 지목했으면 그것이 우선이다
    (Known Mistakes: "승계값이 이번 턴 파싱을 덮어쓰지 않는지").

    Args:
        filter_conditions: 이번 턴 파싱 결과의 식별 필터
        prior_targets: 선행 task가 남긴 대상(`TargetRef` dict 목록 — `build_prior_targets` 산출)
        previous_entities: 직전 턴 식별 엔티티
        alarm_payload: 알람 이벤트 페이로드(`{server_name, hostname, db_id}` 류)
        db_id: 대상이 속한 DB
        max_targets: 상한

    Returns:
        `TargetResolution` — 어느 출처에서 왔는지(`source`)를 반드시 싣는다
    """
    # ① 이번 턴 명시 지목
    res = _targets_from_conditions(
        filter_conditions, db_id=db_id, max_targets=max_targets, source=SOURCE_FILTER
    )
    if res.resolved:
        return res

    # ② 선행 결과 대상
    prior_refs: list[TargetRef] = []
    prior_dropped: list[dict[str, Any]] = []
    for item in prior_targets or []:
        if isinstance(item, TargetRef):
            prior_refs.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            prior_refs.append(TargetRef(**item))
        except Exception as exc:  # noqa: BLE001 — 계약 위반 항목만 격리 탈락(항목 단위)
            prior_dropped.append(_drop("invalid_target_ref", detail=str(exc)))
    if prior_refs:
        truncated = len(prior_refs) > max_targets
        return TargetResolution(
            targets=prior_refs[:max_targets],
            source=SOURCE_PRIOR,
            truncated=truncated,
            truncated_count=len(prior_refs) - max_targets if truncated else 0,
            dropped=prior_dropped,
        )

    # ③ 직전 턴 식별 엔티티
    res = _targets_from_conditions(
        previous_entities, db_id=db_id, max_targets=max_targets, source=SOURCE_PREVIOUS
    )
    if res.resolved:
        res.dropped = [*prior_dropped, *res.dropped]
        return res

    # ④ 알람 페이로드(이벤트 경로 — 페이로드가 1순위인 경로에서는 호출부가 먼저 넘긴다)
    if isinstance(alarm_payload, dict):
        candidate = {
            k: alarm_payload.get(k)
            for k in ("server_name", "hostname", "ip", "db_id")
            if alarm_payload.get(k)
        }
        if candidate:
            candidate.setdefault("db_id", db_id)
            try:
                return TargetResolution(
                    targets=[TargetRef(**candidate)],
                    source=SOURCE_ALARM,
                    dropped=prior_dropped,
                )
            except Exception as exc:  # noqa: BLE001
                prior_dropped.append(_drop("invalid_alarm_payload", detail=str(exc)))

    return TargetResolution(dropped=[*prior_dropped, *res.dropped])
