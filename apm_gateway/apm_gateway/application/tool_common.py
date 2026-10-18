"""도구 공용 정의 — 창·건수·노트 상수 · 지표 카탈로그 캐시 · 조회 창 · `[한계]` 목록 · 인자 검증 ·
인스턴스 키·호출 묶음·행 보조(plans/178 W1에서 `tools.py`에서 옮김 · 도구군·형제 모듈 공용)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.application.masking import mask_identifier, mask_ip, mask_text, mask_url
from apm_gateway.application.resolver import Resolution
from apm_gateway.domain.errors import CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError

EVENTS_DEFAULT_MINUTES = 30
HEALTH_DEFAULT_LOOKBACK = 30
# 창이 이보다 길면 시 단위 애플리케이션 통계를 함께 싣는다(맥락 — 조회 상한이 아니다).
HOURLY_AFTER_MINUTES = 10
TREND_INTERVAL_MINUTE = 5
DEFAULT_N = 10
FILE_ONLY_COLUMNS = ("instance_oid",)
# `mask_identifier`로 가리는 칸(SPEC-coverage §4.3) — 원값이 있었으면 봉투 고지 대상(L-5)
IDENTIFIER_FIELDS = ("client_id", "user_id")
# plans/134 W2(N-5~N-7)
SERIES_DEFAULT_MINUTES = 60
SERIES_INTERVAL_NOTE = (
    "[한계] interval_minute 허용값·1회 조회 창 상한은 미공개다(W10) — 서버가 거부하면"
    " 그 사유를 그대로 싣는다"
)
# 정렬 기준·지표 이름은 허용값이 미공개라(COV E-05·E-06) 식별자 형식만 본다
# (값 검증은 카탈로그·서버).
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
# plans/134 W6 A-1 — 명시 시각 상한(9999-01-01 UTC · 시 경계 올림·표시가 넘치지 않게)
ABSOLUTE_MAX_MS = 253370764800000


@dataclass
class Catalog:
    """소스 1개의 지표 카탈로그 캐시(TTL) — 다시 읽었을 때 지문이 바뀌었으면 `change_note`."""

    fetched_at: float
    scopes: dict[str, list[str]]
    fingerprint: str
    change_note: str | None = None
    # 모양 위반 군(목록 아님·문자열 아닌 항목) — 빈 군이 아니라 「검증 불가」(W2V-G4)
    invalid: tuple[str, ...] = ()


def now_iso(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds")


class Window:
    def __init__(self, start_ms: int, end_ms: int, end_is_now: bool, tz: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.end_is_now = end_is_now
        self.tz = tz

    @property
    def minutes(self) -> int:
        return max(1, round((self.end_ms - self.start_ms) / 60_000))

    def as_dict(self) -> dict[str, Any]:
        zone = ZoneInfo(self.tz)
        return {
            "start": datetime.fromtimestamp(self.start_ms / 1000, zone).isoformat(
                timespec="seconds"
            ),
            "end": datetime.fromtimestamp(self.end_ms / 1000, zone).isoformat(timespec="seconds"),
            "minutes": self.minutes,
        }


class Limits(list[str]):
    """`[한계]` 목록 — 일부 단위 조회 실패를 `fail`로 적으면 봉투 `partial`이 선다(문구 그대로).
    선택 조건을 빼거나 바꿔 조회했으면 `unresolve`로 사용자용 한 줄을 남긴다(봉투 고지
    `apm_unresolved_condition` — 값은 지표·정렬 이름만)."""

    partial: bool = False

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.unresolved: list[str] = []

    def fail(self, text: str) -> None:
        self.partial = True
        self.append(text)

    def unresolve(self, text: str) -> None:
        if text not in self.unresolved:
            self.unresolved.append(text)


def limit_notice(
    e: ApmError, subject: str, where: str = "", tail: str = "", *, detail: str | None = None
) -> str:
    """조회 실패 한 건의 `[한계]` 고지 — `[한계] {subject} 조회 실패{where}: {detail}{tail}`
    (`detail`을 비우면 오류 코드). 계약 위반(`contract_violation` — 게이트웨이 버그)은 고지로
    강등하지 않고 그대로 다시 던진다(plans/166 M3 — 사본마다 쓰던 재발생 규칙의 단일 출처)."""
    if e.code == CONTRACT_VIOLATION:
        raise e
    return f"[한계] {subject} 조회 실패{where}: {e.code if detail is None else detail}{tail}"


def positive_int(value: Any, name: str, default: int) -> int:
    """양의 정수 인자(비면 기본값) — 1 미만·정수 아님은 `invalid_argument`."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())  # ASCII 숫자만(위첨자·전각 숫자는 int()가 못 읽는다 — W2V-G6)
    else:
        number = 0
    if number < 1:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 양의 정수여야 한다: {value!r}")
    return number


def nonneg_int(value: Any, name: str) -> int:
    """0 이상의 정수 인자(프로파일 번호 등) — 그 밖은 `invalid_argument`."""
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    else:
        number = -1
    if number < 0:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 0 이상의 정수여야 한다: {value!r}")
    return number


def identifier_arg(value: Any, name: str) -> str | None:
    """식별자 형식 인자(정렬 기준 등 — 허용값 미공개라 형식만 본다). 비면 None."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not _IDENT.fullmatch(text):
        raise ApmError(
            INVALID_ARGUMENT, f"{name}는 영문으로 시작하는 영문·숫자·밑줄이어야 한다: {value!r}"
        )
    return text


def catalog_fingerprint(scopes: dict[str, list[str]]) -> str:
    raw = json.dumps(scopes, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def check_n(n: Any, default: int | None = DEFAULT_N) -> int | None:
    """상위 N — 1 이상이면 상한 없음(SPEC §2.1). 없으면 도구별 기본(None = 전부)."""
    if n is None:
        return default
    value = int(n)
    if value < 1:
        raise ApmError(INVALID_ARGUMENT, f"n은 1 이상이어야 한다: {n}")
    return value


def select_rows(rows: list[Any], n: int | None, full: bool) -> list[Any]:
    """정렬된 행에서 화면 행을 고른다 — `full`이면 전부, 아니면 앞 `n`(None = 전부)."""
    return list(rows) if full or n is None else rows[:n]


InstKey = tuple[str, int]


def inst_key(rec: dict[str, Any]) -> InstKey:
    """인스턴스 식별 키 — 도메인·인스턴스 id는 소스마다 따로 매겨 겹칠 수 있다(S-4)."""
    return rec["source_id"], rec["instance_id"]


def group_by_domain(resolution: Resolution) -> dict[tuple[str, int], list[int]]:
    """호출 묶음 = (소스, 도메인) — 호출은 그 소스 서버로만 나간다."""
    groups: dict[tuple[str, int], list[int]] = {}
    for inst in resolution.instances:
        groups.setdefault((inst["source_id"], inst["domain_id"]), []).append(inst["instance_id"])
    return groups


def tag_records(
    records: list[dict[str, Any]], source_id: str, domain_id: int | None = None
) -> list[dict[str, Any]]:
    """소스 id를 붙인다 — 응답에 도메인 id가 없으면 호출 묶음의 도메인으로 채운다."""
    out = []
    for r in records:
        rec = {**r, "source_id": source_id}
        if domain_id is not None and rec.get("domain_id") is None:
            rec["domain_id"] = domain_id
        out.append(rec)
    return out


def inst_meta(inst: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": inst["source_id"],
        "instance_id": inst["instance_id"],
        "instance_name": inst["instance_name"],
        "domain_id": inst["domain_id"],
    }


def hour_floor(ms: int) -> int:
    return ms - ms % 3_600_000


def hour_ceil(ms: int) -> int:
    floor = hour_floor(ms)
    return floor if floor == ms else floor + 3_600_000


def masked_full_text(text: str) -> str:
    """원문 텍스트 전문(마스킹본) — 줄마다 자르지 않고 가린다."""
    return "\n".join(mask_text(line, limit=max(len(line), 1)) for line in text.splitlines())


def tx_fields(tx: dict[str, Any]) -> dict[str, Any]:
    """트랜잭션 레코드(X-View·상세) → 반환 칸(마스킹 · 식별자 가림)."""
    return {
        "domain_id": tx.get("domain_id"),
        "domain_name": tx.get("domain_name", ""),
        "instance_id": tx.get("instance_id"),
        "instance_name": tx.get("instance_name", ""),
        "instance_oid": tx.get("instance_oid"),
        "application": mask_url(tx.get("application", "")),
        "txid": tx.get("txid", ""),
        "guid": tx.get("guid", ""),
        "response_time_ms": tx.get("response_time_ms"),
        "cpu_ms": tx.get("cpu_ms"),
        "sql_ms": tx.get("sql_ms"),
        "fetch_ms": tx.get("fetch_ms"),
        "external_ms": tx.get("external_ms"),
        "network_ms": tx.get("network_ms"),
        "frontend_ms": tx.get("frontend_ms"),
        "sql_count": tx.get("sql_count"),
        "fetch_count": tx.get("fetch_count"),
        "external_call_count": tx.get("external_call_count"),
        "error_type": tx.get("error_type", ""),
        "start_time_ms": tx.get("start_time_ms"),
        "end_time_ms": tx.get("end_time_ms"),
        "collect_time_ms": tx.get("collect_time_ms"),
        "client_ip": mask_ip(tx.get("client_ip", "")),
        "client_id": mask_identifier(tx.get("client_id", "")),
        "user_id": mask_identifier(tx.get("user_id", "")),
        "is_async": tx.get("is_async"),
        "link_root": tx.get("link_root"),
        "has_stacktrace": tx.get("has_stacktrace"),
        "business_ids": list(tx.get("business_ids") or []),
        "business_names": list(tx.get("business_names") or []),
    }


def identifier_fields(records: Iterable[dict[str, Any] | None]) -> set[str]:
    """식별자 가림이 실제로 적용된 칸 이름(원값이 비어 있지 않은 칸)."""
    return {f for rec in records if rec for f in IDENTIFIER_FIELDS if rec.get(f)}


def profile_ref_of(rec: dict[str, Any], time_key: str) -> dict[str, Any] | None:
    if not rec.get("txid"):
        return None
    return {
        "source_id": rec["source_id"],
        "domain_id": rec.get("domain_id"),
        "txid": rec["txid"],
        "time_ms": rec.get(time_key),
    }


def is_given(value: Any) -> bool:
    """대상 인자(hostname·instance_name)가 비지 않았는가."""
    return value is not None and bool(str(value).strip())


def target_text(hostname: str | None, instance_name: str | None) -> str:
    """오류 문구의 대상 — hostname이 있으면 종전 문구 그대로."""
    return f"hostname {hostname!r}" if is_given(hostname) else f"instance_name {instance_name!r}"


def with_hostnames(rows: list[dict[str, Any]], resolution: Resolution) -> list[dict[str, Any]]:
    """인스턴스 이름으로 찾은 조회 — (소스, 인스턴스) 행에 역정합 hostname을 붙인다(본체가 서버
    기준 결과와 합친다 · plans/130 N-3). 이미 있는 `hostname` 칸은 두고, 인스턴스 칸이 없는 행
    (묶음 합계 등)은 봉투 `instance_resolution.instance_refs[].hostname`으로 잇는다."""
    hosts = {inst_key(i): str(i.get("hostname") or "") for i in resolution.instances}
    out = []
    for row in rows:
        if isinstance(row, dict) and "hostname" not in row:
            key = (row.get("source_id"), row.get("instance_id"))
            if key in hosts:
                row = {**row, "hostname": hosts[key]}
        out.append(row)
    return out
