"""노드 주소 보기(`apm.node_address`) 고정 배선 — plans/210 W2 §3.6 · D-300 ⑧.

분해 LLM이 닫힌 보기 목록에서 `apm.node_address`를 고르면 처리기가 게이트웨이 도구
`apm_node_addresses`를 **1회** 부른다(단어 매칭 0). 이 모듈은 그 보기의 순수 규칙만 둔다.

- 도구 인자: 보기 조건 `domain_id`(도구 인자 `domain_ids`)는 정수 1개라 목록으로 싣는다.
- 미등록: 게이트웨이는 `JENNIFER_NODE_ADDRESS_ENABLED`가 꺼져 있으면 도구를 등록하지 않는다 —
  FastMCP가 `Unknown tool: apm_node_addresses`로 답하므로 그 호출의 오류를 비활성 사유로 바꾼다
  (예외 종류 이름만 남기지 않는다 · 침묵 금지).
- 결정적 답 줄: 봉투 `node_address_sources`(소스별 상태·사유·상태별 인스턴스 수)로 확인 수와
  `ok`가 아닌 소스·인스턴스 사유를 싣는다(LLM 산문에 맡기지 않는다).

도구·인자 이름은 게이트웨이 계약 이름이다(D-274 ③). 게이트웨이 설정 이름은 비활성 사유 문구
하나뿐이다(계획서 §3.6 지정 문구 — 운영자가 켤 설정을 가리킨다).

plans/210 W8 A — 대상 통로 × 처리(보기 `target: none` 그대로 · 보기 id로 배선 — 받지 않는 통로로 온
대상을 조용히 버리지 않는다). 대상이 하나라도 있으면 해석 인스턴스의 (소스, 도메인)마다 도구 1회
(`instance_ids` — 그 인스턴스만 · 답 줄은 인스턴스마다):

| 통로 | 처리 |
|---|---|
| 분해 `targets` 텍스트 | 적용 — 대상 해석(`_resolve_target_texts` · 인스턴스 이름·업무명 |
| | 검색 · 업무명 간선)의 인스턴스 |
| 파서 서버명(등록명 간선이 잇지 못한 것) | 적용 — 대상 텍스트(`auto`)로 같은 해석 |
| 파서 hostname(이번 턴 식별자 · 선행 결과 · | 적용 — 게이트웨이 정합 |
| 지시어면 직전 서버 — `scoped_targets`) | `apm_instance_map(hostname=…)`의 인스턴스 · |
| | 없으면 「찾지 못함」 |
| 직전 대상 승계(`last_targets`) | 적용 — 대상별 보기로 센다(`_per_target_views`) · 분해 |
| | `targets`처럼 싣고 승계 고지 · 노드 주소 보기도 대상별 보기로 세므로 혼합 보기 |
| | (예 노드 주소 + 응답시간) 후속 턴 승계가 새로 성립한다(플래그 off 포함 · W8 의도 편차) |
| 보기 조건 `view_args[apm.node_address].targets` | 대신 적용 — 분해가 대상을 보기 조건에 넣은 |
| | 경우(실측 q4) · 모양만 보고 task `targets`로 옮긴다(`lift_node_targets` · 문자열은 |
| | `auto`) · 이후 분해 `targets`와 같다 · 정제 뒤 빈 값이면 종전 「버린 조건」 고지 |
| 보기 조건 `domain_id` | 대상이 있으면 해석 인스턴스와 AND(다 빠지면 고지 · 호출 0) · |
| | 없으면 W2 현행(`domain_ids`) |
| 대상 없음 | W2 현행 — 도구 1회(도메인 조건 또는 전 도메인) |

대상이 있는데 해석 0건이면 「찾지 못함」+ 후보이고 도구를 부르지 않는다(D-290 ⑥ — 전 도메인 조회로
대신하지 않는다). 해석 행에 `host_address` 칸이 없으면(게이트웨이 플래그 off 표지) W2 현행 1회로
가고, 이 보기 때문에만 생긴 해석·승계 고지는 싣지 않는다(답 바이트 동일 · 해석 0건이면 표지를 몰라
「찾지 못함」이다).

plans/210 W8 C — 해석 인스턴스별로 부르는 다른 보기(응답시간·런타임 등)는 해석 행에 표지가 있을
때만 처리기 1회 안에서 (소스, 도메인)마다 도구 1회(보기 여러 개가 공유)를 부르고 `pod: true`
인스턴스만 결정적 답 줄을 붙인다(`pod_node_lines`). 파드 판정은 게이트웨이 `pod` 표지만이다(원문
단어 매칭 0). 이 보기를 함께 골랐으면 그 보기가 답한다(부착하지 않는다).

plans/210 W9 B — 서버명 직행 호출(「web01 응답시간」 · `_host_calls` — `c.instance` 없음)은 해석
행이 없어 C 표지를 볼 수 없다. 그래서 그 호출의 봉투(배치면 항목에서 복원한 대상별 봉투)가
`instance_resolution.node_address: true`(게이트웨이 플래그 on 표지)를 달았을 때만
`instance_refs`를 C 해석 행과 합쳐 같은 규칙으로 부착한다(`marked_refs` — (소스, 도메인)마다 1회
공유 · `pod: true`만). 표지가 없으면 시도 0이다. 통로별:

| 통로 | 처리 |
|---|---|
| 파서·분해 서버명 → 대상 보기(`required_input`) | 적용 — 봉투 표지 · 오류 호출은 뺀다 |
| 배치(`targets`) 대상 | 적용 — 복원된 대상별 봉투(배치 복원 뒤 부착) · |
| | 접수(작업 승격)면 봉투가 없어 0 |
| 첫 홉 부하 순위가 고른 서버 | 미적용 — 사용자가 말한 서버가 아니다(대상 없는 질의의 |
| | 호출 수 불변) |
| 인스턴스 목록 보기(`apm.instances`)의 서버 | 미적용 — 목록 행이 이미 `host_address` 칸을 싣는다 |

서버 정합을 따로 부르지 않는다(`apm_instance_map(hostname)` — 플래그 off에서도 호출이 생긴다).
"""

from __future__ import annotations

from typing import Any

#: 보기 id(레지스트리 `solutions[apm].views`) · 게이트웨이 도구 이름 · 도메인 목록 인자.
NODE_ADDRESS_VIEW = "apm.node_address"
NODE_ADDRESS_TOOL = "apm_node_addresses"
DOMAIN_IDS_ARG = "domain_ids"
#: 인스턴스 대상 인자(plans/210 W8 A — 게이트웨이 가산 인자) · 해석 행의 노드 주소 표지 칸.
INSTANCE_IDS_ARG = "instance_ids"
HOST_ADDRESS_KEY = "host_address"
#: 데이터 도구 봉투의 서버 정합 칸 · 그 안의 노드 주소 표지(plans/210 W9 B — 게이트웨이 플래그 on만)
RESOLUTION_KEY = "instance_resolution"
RESOLUTION_MARK = "node_address"
#: 도구 미등록 오류 코드(처리기 안내 코드 — 사유까지 답에 보인다) · 사유.
NODE_ADDRESS_DISABLED = "node_address_disabled"
DISABLED_REASON = "게이트웨이 노드 주소 조회 비활성(JENNIFER_NODE_ADDRESS_ENABLED)"
#: 집계 운반 키(게이트웨이 봉투 → `meta["aggregates"]`) · 인스턴스 대상 호출의 행 요약.
SOURCES_KEY = "node_address_sources"
NODE_ROWS_KEY = "node_rows"
#: 그 호출의 전체 인스턴스 수(게이트웨이가 센 값 — 인라인 상한을 넘는 행 포함 · W8 교정)
NODE_TOTAL_KEY = "node_total"
#: 인스턴스 답 줄 상한 — 넘으면 「외 n개」(대상 고지의 5개 관례 · 전체는 결과 표).
LINE_MAX = 5

_OK = "ok"
_NOT_FOUND = "not_found"
_NOT_FOUND_TEXT = "웹 목록에 없음"
_ROW_KEYS = ("source_id", "instance_name", "instance_id", "host_address", "pod", "status")


def marked(rows: list[dict[str, Any]]) -> bool:
    """해석 행이 게이트웨이 노드 주소 표지(`host_address` 칸 — 플래그 on일 때만)를 달았는가."""
    return bool(rows) and all(HOST_ADDRESS_KEY in row for row in rows)


def marked_refs(envelope: dict[str, Any] | None) -> list[dict[str, Any]]:
    """서버명 직행 호출 봉투의 정합 인스턴스(plans/210 W9 B) — 표지가 없으면 빈 목록.

    게이트웨이 플래그 on이면 `instance_resolution.node_address is True`가 가산된다(off = 키 없음).
    그때만 `instance_refs`의 (소스, 도메인, 인스턴스 id)를 해석 행 모양으로 돌려준다.
    """
    res = (envelope or {}).get(RESOLUTION_KEY)
    if not isinstance(res, dict) or res.get(RESOLUTION_MARK) is not True:
        return []
    return [{k: ref.get(k) for k in ("source_id", "domain_id", "instance_id")}
            for ref in res.get("instance_refs") or [] if isinstance(ref, dict)]


def target_groups(rows: list[dict[str, Any]]) -> list[tuple[Any, Any, list[Any]]]:
    """해석 행 → (소스, 도메인, 인스턴스 id 목록) — 처음 나온 순서 · id 중복 없이."""
    groups: dict[tuple[Any, Any], list[Any]] = {}
    for row in rows:
        ids = groups.setdefault((row.get("source_id"), row.get("domain_id")), [])
        if row.get("instance_id") not in ids:
            ids.append(row.get("instance_id"))
    return [(source, domain, ids) for (source, domain), ids in groups.items()]


def targeted_args(base: dict[str, Any], source: Any, domain: Any, ids: list[Any]
                  ) -> dict[str, Any]:
    """(소스, 도메인) 1개의 도구 인자 — 도메인을 모르면 `instance_ids`만(게이트웨이가 찾는다)."""
    args = {**base, "source_ids": [source], INSTANCE_IDS_ARG: list(ids)}
    if domain is None:
        args.pop(DOMAIN_IDS_ARG, None)
    else:
        args[DOMAIN_IDS_ARG] = [domain]
    return args


def node_rows_of(args: dict[str, Any], envelope: dict[str, Any]) -> list[dict[str, Any]] | None:
    """인스턴스 대상 호출(`instance_ids`)의 행 요약 — 답 줄 재료. 다른 호출이면 None."""
    if args.get(INSTANCE_IDS_ARG) is None or not isinstance(envelope.get(SOURCES_KEY), list):
        return None
    return [{k: row.get(k) for k in _ROW_KEYS}
            for row in envelope.get("rows") or [] if isinstance(row, dict)]


def row_total(envelope: dict[str, Any]) -> int:
    """게이트웨이가 센 전체 행 수 — `total_row_count` → 소스별 `instances` 합 → 인라인 행 수.

    인라인 행은 게이트웨이 상한(500)에서 잘리므로 「외 n개」는 이 값으로 센다(W8 교정).
    """
    inline = len([r for r in envelope.get("rows") or [] if isinstance(r, dict)])
    total = envelope.get("total_row_count")
    if isinstance(total, int) and not isinstance(total, bool):
        return max(total, inline)
    sources = envelope.get(SOURCES_KEY)
    counts = [s.get("instances") for s in sources if isinstance(s, dict)] \
        if isinstance(sources, list) else []
    ints = [n for n in counts if isinstance(n, int) and not isinstance(n, bool)]
    if counts and len(ints) == len(counts):
        return max(sum(ints), inline)
    return inline


def _reasons(sources: Any) -> dict[str, str]:
    return {str(src.get("source_id")): str(src.get("reason") or "")
            for src in sources if isinstance(src, dict)} if isinstance(sources, list) else {}


def _row_line(scope: Any, row: dict[str, Any], reasons: dict[str, str]) -> str:
    """인스턴스 1줄 — 노드 주소(상태 ok) 또는 확인 못 한 상태·사유."""
    name = row.get("instance_name") or row.get("instance_id")
    status = str(row.get("status") or "")
    host = row.get(HOST_ADDRESS_KEY) or ""
    if status == _OK:
        return f"{scope} · {name}: 노드 주소 {host or '빈 값'}(상태 ok)"
    why = _NOT_FOUND_TEXT if status == _NOT_FOUND else reasons.get(str(row.get("source_id")), "")
    return f"{scope} · {name}: 노드 주소 확인 못 함 — {status}" + (f"({why})" if why else "")


def _capped(scope: Any, lines: list[str], unlisted: int = 0) -> list[str]:
    """줄 상한 `LINE_MAX` — 넘는 줄과 인라인 밖 행(`unlisted`)을 「외 n개」 한 줄로 센다."""
    hidden = max(len(lines) - LINE_MAX, 0) + max(unlisted, 0)
    if not hidden:
        return lines
    return [*lines[:LINE_MAX], f"{scope}: 외 {hidden}개(전체는 결과 표)"]


def node_address_args(args: dict[str, Any]) -> dict[str, Any]:
    """도구 인자 — 정수 1개로 검증된 `domain_ids`를 목록으로(그 밖 인자는 그대로)."""
    value = args.get(DOMAIN_IDS_ARG)
    if value is not None and not isinstance(value, list):
        args[DOMAIN_IDS_ARG] = [value]
    return args


def missing_tool_error(tool: str, error_text: str) -> str | None:
    """게이트웨이가 노드 주소 도구를 등록하지 않았다는 호출 오류면 비활성 사유(아니면 None)."""
    if tool == NODE_ADDRESS_TOOL and f"Unknown tool: {NODE_ADDRESS_TOOL}" in error_text:
        return f"{NODE_ADDRESS_DISABLED}: {DISABLED_REASON}"
    return None


def node_address_lines(agg: dict[str, Any]) -> list[str] | None:
    """노드 주소 집계 줄 — 노드 주소 집계가 아니면 None.

    「노드 주소 확인 n/N개」(+ 확인 못 한 인스턴스의 상태별 수) · `ok`가 아닌 소스마다
    「소스 {id} {상태} — {사유}」. 값은 게이트웨이가 센 그대로다.
    """
    sources = agg.get(SOURCES_KEY)
    if not isinstance(sources, list):
        return None
    scope = agg.get("scope") or agg.get("view")
    rows = agg.get(NODE_ROWS_KEY)
    if isinstance(rows, list):  # 인스턴스 대상(plans/210 W8 A) — 그 인스턴스마다 한 줄
        reasons = _reasons(sources)
        total = agg.get(NODE_TOTAL_KEY)
        unlisted = total - len(rows) if isinstance(total, int) else 0
        lines = _capped(scope, [_row_line(scope, row, reasons) for row in rows], unlisted)
        seen = {str(row.get("source_id")) for row in rows}
        for src in sources:  # 행 없이 실패한 소스(인벤토리 조회 불가 등)만 — 행 줄에 사유가 있다
            if (isinstance(src, dict) and src.get("status") != _OK
                    and str(src.get("source_id")) not in seen):
                reason = f" — {src['reason']}" if src.get("reason") else ""
                lines.append(f"{scope}: 소스 {src.get('source_id')} 노드 주소"
                             f" {src.get('status')}{reason}")
        return lines
    counts: dict[str, int] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        for status, n in (src.get("instance_status") or {}).items():
            if isinstance(n, int) and not isinstance(n, bool):
                counts[str(status)] = counts.get(str(status), 0) + n
    total = sum(counts.values())
    others = " · ".join(f"{s} {n}개" for s, n in counts.items() if s != _OK and n)
    lines = [f"{scope}: 노드 주소 확인 {counts.get(_OK, 0)}/{total}개"
             + (f"(확인 못 함 — {others})" if others else "")]
    for src in sources:
        if isinstance(src, dict) and src.get("status") != _OK:
            reason = f" — {src['reason']}" if src.get("reason") else ""
            lines.append(f"{scope}: 소스 {src.get('source_id')} 노드 주소 {src.get('status')}"
                         f"{reason}")
    return lines


def pod_node_lines(scope: str, results: list[tuple[str | None, dict[str, Any] | None]]
                   ) -> list[str]:
    """다른 보기에 붙이는 파드 노드 주소 줄(plans/210 W8 C) — (호출 오류 문구 | None, 봉투) 목록.

    `pod is True` 인스턴스만 한 줄(노드 주소 또는 상태·사유) · `pod is False`(VM)는 없음 ·
    `pod is None`이면서 `ok`·`not_found`가 아닌 행은 소스마다 사유 한 줄(침묵 금지) · 호출 오류는
    문구 한 줄(같은 문구 한 번). 인라인 밖 행(`row_total` − 인라인)은 파드 여부를 몰라 「외 n개」
    수에만 센다.
    """
    lines: list[str] = []
    notes: list[str] = []
    unlisted = 0
    for error, envelope in results:
        if error:
            note = f"{scope}: {error}"
            if note not in notes:
                notes.append(note)
            continue
        env = envelope or {}
        unlisted += row_total(env) - len([r for r in env.get("rows") or [] if isinstance(r, dict)])
        reasons = _reasons(env.get(SOURCES_KEY))
        for row in env.get("rows") or []:
            if not isinstance(row, dict):
                continue
            if row.get("pod") is True:
                lines.append(_row_line(scope, row, reasons))
            elif row.get("pod") is None and str(row.get("status")) not in (_OK, _NOT_FOUND):
                sid = str(row.get("source_id"))
                why = f" — {reasons[sid]}" if reasons.get(sid) else ""
                note = f"{scope}: 소스 {sid} 노드 주소 {row.get('status')}{why}"
                if note not in notes:
                    notes.append(note)
    return [*_capped(scope, lines, unlisted), *notes]
