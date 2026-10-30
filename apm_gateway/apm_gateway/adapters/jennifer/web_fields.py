"""제니퍼 웹 내부 API 응답 파서 — `/agent/upgrade/list` 허용 칸만 남긴다 (plans/202 §2.1 · D-355 ①).

- 원소마다 `instId`·`sid`·`hostName`·`ip`·`hostAddress`·`status`·`statusMsg`·`lastLiveTime`만 남기고
  **나머지는 파싱 직후 버린다**(`jmx.*` JMX 자격증명 포함 — 게이트웨이 밖으로 나가지 않는다).
  남긴 칸은 벤더 무지 이름(`inst_id`·`host_address`…)으로 바꿔 응용 계층에 넘긴다(벤더 필드명은
  이 패키지 `adapters/jennifer/`에만 — `tests/test_boundary.py`).
- 모양이 다르면(배열 아님 · 원소가 객체 아님 · `instId` 없음) `WebShapeError` — 호출자는 `error`로
  드러내고 캐시를 갱신하지 않는다(침묵 폴백 금지 · 비공개 API 변경 감지).
- `ip`·`hostAddress`는 IPv4 점 표기만 받는다. 형식 밖이면 그 원소의 주소를 비운다.
- 도메인 종류(`domain_kind`): 주소를 둘 다 가진 원소가 전부 `hostAddress ≠ ip`면 `pod` · 전부 같으면
  `vm` · 섞이면 `mixed` · 판정할 원소가 없으면 빈 값(모름).

표준 라이브러리만 쓰는 순수 함수다.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# 웹 응답 칸 → 응용 계층 이름(허용 칸 정본 — 이 밖의 칸은 버린다)
KEEP_FIELDS: dict[str, str] = {
    "instId": "inst_id",
    "sid": "sid",
    "hostName": "host_name",
    "ip": "ip",
    "hostAddress": "host_address",
    "status": "status",
    "statusMsg": "status_msg",
    "lastLiveTime": "last_live_time",
}
_ADDRESS_FIELDS = ("ip", "host_address")
_TEXT_FIELDS = ("host_name", "status_msg")

KIND_POD = "pod"
KIND_VM = "vm"
KIND_MIXED = "mixed"


class WebShapeError(ValueError):
    """응답 모양이 예상과 다르다(비공개 내부 API 변경 의심) — 사유에 응답 값은 싣지 않는다."""


def ipv4_or_empty(value: Any) -> str:
    """IPv4 점 표기면 정규 문자열, 아니면 빈 값(앞자리 0·IPv6·호스트명·공백 포함 거부)."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text != value:
        return ""
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        return ""


def _scalar(value: Any) -> Any:
    """숫자·문자열·None만 남긴다(중첩 객체·배열은 허용 칸이라도 버린다)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return None


def parse_upgrade_list(data: Any) -> list[dict[str, Any]]:
    """응답 JSON → 허용 칸만 남긴 행 목록. 모양이 다르면 `WebShapeError`."""
    if not isinstance(data, list):
        raise WebShapeError(f"응답이 배열이 아니다({type(data).__name__})")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise WebShapeError(f"원소 {index}가 객체가 아니다({type(item).__name__})")
        if "instId" not in item or item.get("instId") in (None, ""):
            raise WebShapeError(f"원소 {index}에 인스턴스 id 칸이 없다")
        row = {name: _scalar(item.get(raw)) for raw, name in KEEP_FIELDS.items()}
        for name in _ADDRESS_FIELDS:
            row[name] = ipv4_or_empty(row[name])
        for name in _TEXT_FIELDS:
            row[name] = "" if row[name] is None else str(row[name])
        rows.append(row)
    return rows


def is_pod_row(row: dict[str, Any]) -> bool | None:
    """같은 행의 `ip`와 `host_address`로만 판정한다 — 다르면 True · 같으면 False · 하나라도 비면
    None(판정 불가)."""
    ip = row.get("ip") or ""
    host = row.get("host_address") or ""
    if not ip or not host:
        return None
    return ip != host


def domain_kind(rows: list[dict[str, Any]]) -> str:
    """도메인 종류 — `pod`·`vm`·`mixed` 또는 빈 값(판정할 원소 없음)."""
    verdicts = {v for v in (is_pod_row(r) for r in rows) if v is not None}
    if not verdicts:
        return ""
    if verdicts == {True}:
        return KIND_POD
    if verdicts == {False}:
        return KIND_VM
    return KIND_MIXED
