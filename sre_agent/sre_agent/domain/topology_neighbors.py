"""토폴로지 1홉 이웃과 외부 원인 후보 — 결정적 경량판 (plans/198 I-7 · D-350 ④).

**순수 도메인 모듈**(stdlib + 같은 domain). 입력은 이미 받은 도구 행·페이로드 연결 힌트·이웃
알람 시각이며, 도구를 부르거나 now()를 쓰지 않는다. 배선은 `application/evidence_prefetch`가
플래그 뒤에서 한다.

- **이웃 출처는 확정 연결만**(D-350 ④ Q4): 등록된 토폴로지 1홉 행(`topology_neighbors`)과 확정
  연결 힌트(`link_neighbor` — 이전 확정 연결 `previous_confirmed`만). 사용자 선택(확정 대기)·
  미등록·선택 대기·유사 후보 같은 추정·미확정 연결은 쓰지 않는다.
- **선택**(`select_neighbors`): 확정 연결 → 토폴로지 순으로 이름 중복(대소문자 무시)과 대상 자신을
  빼고 상한까지만 남긴다.
- **외부 원인 후보**(`neighbor_finding`): 이웃 첫 알람이 대상 첫 알람보다 앞서면 후보다. 대상
  첫 알람이 없으면 기준시각(`NEIGHBOR_LEAD_ANCHOR` = offset 0)을 대상 시점으로 본다 — 사전수집은
  같은 기준점을 상관 계산(`correlation.leads(…, anchor=NEIGHBOR_LEAD_ANCHOR)`)에도 넘겨 이웃
  블록과 「연관 서버 … 선행」 노트가 엇갈리지 않게 한다. 순위는 이웃 첫 알람이 이른 순
  (같으면 이름순)으로 결정적이다(D-341 ④). 상관은 인과가 아니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sre_agent.domain.correlation import AlarmPoint, format_offset, leads, offset_minutes

#: 이웃 출처 어휘 — 우선순위 순(확정 연결이 앞).
ORIGIN_LINK = "confirmed_link"
ORIGIN_TOPOLOGY = "topology"
ORIGIN_LABELS: dict[str, str] = {ORIGIN_LINK: "확정 연결", ORIGIN_TOPOLOGY: "폴스타 토폴로지"}
#: 확정 연결로 인정하는 연결 힌트 근거(이전 확정 연결만 — 사용자 선택은 확정 대기라 제외).
CONFIRMED_LINK_BASIS = "previous_confirmed"
#: 외부 원인 후보 근거 문장의 고지.
NOT_CAUSATION = "상관 ≠ 인과"
#: 이웃 알람 이름 노출 상한(브리핑·지침).
ALARM_NAMES_MAX = 3
#: 대상 첫 알람이 없을 때 선행 판정에 쓰는 대상 시점(기준시각 = 트리거 알람 · offset 0).
NEIGHBOR_LEAD_ANCHOR = 0


@dataclass(frozen=True)
class Neighbor:
    """이웃 호스트 1대 — 조회할 소스(db_id)·폴스타 등록명·출처."""

    name: str
    source: str
    origin: str


def topology_neighbors(rows: Iterable[Mapping[str, Any]], source: str) -> list[Neighbor]:
    """토폴로지 행 → 1홉 이웃. `hop`이 있으면 1인 행만(DB2 폴백·`hop` 없는 행은 1홉으로 본다).

    이름은 `resource_name`(mcp_server 열) 또는 `name`. 이름 없는 행은 버린다.
    """
    out: list[Neighbor] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        hop = row.get("hop")
        if hop is not None:
            try:
                if int(hop) != 1:
                    continue
            except (TypeError, ValueError):
                continue
        name = str(row.get("resource_name") or row.get("name") or "").strip()
        if name:
            out.append(Neighbor(name=name, source=source, origin=ORIGIN_TOPOLOGY))
    return out


def link_neighbor(link: Mapping[str, Any] | None) -> Neighbor | None:
    """확정 연결 힌트(`meta.hints.polestar_link` — D-348) → 이웃. 확정(`linked` · 근거
    `previous_confirmed` · db_id·server_name 모두 있음)이 아니면 None."""
    if not isinstance(link, Mapping) or link.get("status") != "linked":
        return None
    if str(link.get("basis") or "").strip() != CONFIRMED_LINK_BASIS:
        return None
    db_id = str(link.get("db_id") or "").strip()
    server = str(link.get("server_name") or "").strip()
    if not db_id or not server:
        return None
    return Neighbor(name=server, source=db_id, origin=ORIGIN_LINK)


def select_neighbors(
    target: str, candidates: Iterable[Neighbor], limit: int
) -> tuple[list[Neighbor], int]:
    """출처 우선순위(확정 연결 → 토폴로지) · 입력 순서로 대상 자신과 중복을 빼고 상한까지.

    (선택 목록, 상한으로 뺀 수)를 돌려준다.
    """
    rank = {ORIGIN_LINK: 0, ORIGIN_TOPOLOGY: 1}
    ordered = sorted(enumerate(candidates), key=lambda p: (rank.get(p[1].origin, 9), p[0]))
    seen = {target.strip().lower()}
    picked: list[Neighbor] = []
    for _, n in ordered:
        key = n.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(n)
    cap = max(int(limit), 0)
    return picked[:cap], max(len(picked) - cap, 0)


def neighbor_finding(
    reference_time: str,
    target_first_alarm: int | None,
    neighbors: Sequence[Neighbor],
    alarms: Mapping[str, list[AlarmPoint]],
    *,
    errors: Mapping[str, str] | None = None,
    limit: int,
    dropped: int = 0,
    tool_calls: int = 0,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """이웃별 첫 알람·선행 여부와 외부 원인 후보(선행 순)를 결정적으로 만든다.

    대상 첫 알람이 없으면(0건·알람 축 미조회) 기준시각(트리거 알람 · offset 0)을 대상 시점으로 본다.
    `errors`의 이웃은 조회 결손이라 선행을 판정하지 않는다(0건으로 단정하지 않는다).
    """
    anchor = NEIGHBOR_LEAD_ANCHOR if target_first_alarm is None else int(target_first_alarm)
    errors = errors or {}
    hosts: list[dict[str, Any]] = []
    leaders: list[tuple[int, str, dict[str, Any]]] = []
    for n in neighbors:
        row: dict[str, Any] = {"name": n.name, "source": n.source, "origin": n.origin}
        if n.name in errors:
            row.update(
                {
                    "alarm_count": None,
                    "first_alarm_offset_min": None,
                    "leads": None,
                    "error": errors[n.name],
                }
            )
            hosts.append(row)
            continue
        points = alarms.get(n.name) or []
        offsets = sorted(offset_minutes(reference_time, p.time) for p in points)
        first = offsets[0] if offsets else None
        lead = first is not None and leads(first, target_first_alarm, NEIGHBOR_LEAD_ANCHOR)
        row.update({"alarm_count": len(points), "first_alarm_offset_min": first, "leads": lead})
        hosts.append(row)
        if lead and first is not None:
            names = sorted({p.name for p in points})[:ALARM_NAMES_MAX]
            leaders.append(
                (
                    first,
                    n.name,
                    {
                        "host": n.name,
                        "source": n.source,
                        "origin": n.origin,
                        "first_alarm_offset_min": first,
                        "lead_min": anchor - first,
                        "alarm_names": names,
                        "basis": (
                            f"이웃 {n.name} 첫 알람 {format_offset(first)} — 대상 시점 "
                            f"{format_offset(anchor)}보다 {anchor - first}분 선행({NOT_CAUSATION})"
                        ),
                    },
                )
            )
    leaders.sort(key=lambda x: (x[0], x[1]))
    candidates = [{"rank": i, **c} for i, (_, _, c) in enumerate(leaders, start=1)]
    return {
        "hosts": hosts,
        "external_cause_candidates": candidates,
        "target_first_alarm_offset_min": target_first_alarm,
        "limit": int(limit),
        "dropped": int(dropped),
        "tool_calls": int(tool_calls),
        "notes": list(notes),
    }


__all__ = [
    "ORIGIN_LINK",
    "ORIGIN_TOPOLOGY",
    "ORIGIN_LABELS",
    "CONFIRMED_LINK_BASIS",
    "NOT_CAUSATION",
    "ALARM_NAMES_MAX",
    "NEIGHBOR_LEAD_ANCHOR",
    "Neighbor",
    "topology_neighbors",
    "link_neighbor",
    "select_neighbors",
    "neighbor_finding",
]
