"""관리·민감 조회 도구 4종 — `apm_config`·`apm_environment`·`apm_users`·`apm_active_detail`
(plans/134 W7 · N-15~N-17 · SPEC-apm-question-coverage §4·§5 · D-296 ①③ · D-299 ③⑦).

- 코어(`ApmTools`)의 봉투(`ok`·`err`)·정합(`_resolve`)·소스 묶음(`sources`)을 그대로 쓴다.
- **자격증명은 어댑터가 응답을 파싱한 직후 이미 걷혔다**(`domain/credentials.py` — 계정
  `password` 키째 제거 · 비밀 키 값·값 안의 자격증명 `[가림]`). 이 모듈은 가린 값만 본다.
- 개인정보는 `spec/CAPABILITY-MAP-134.md` 표 C 처분대로 가린다(G-11 미결 동안 원값 없음):
  계정 ID·이름 `mask_identifier` · 이메일은 `@` 앞 `mask_identifier` · 전화번호는 칸째 `<phone>`
  (형식과 무관 — 국제·유선·점 구분 · plans/134 W7 AUDIT-7) · 허용 IP `mask_ip` · 실행 중 요청
  사용자 ID `mask_identifier`·SQL `mask_sql`·HTTP 쿼리 `mask_query` · 룰 메시지·스크립트
  `mask_text` · 표에 없는 키(`extra`)의 문자열 `mask_text`(사용자·실행 중 요청의 `extra`는
  키가 식별자형 — `…Id`·`…ID`·`…Name`·`nickname`·`emp…` — 이면 `mask_identifier`).
  환경변수·시스템 속성·경로·클래스 이름은 「반환」·「자격증명 제거」 처분이라 **키를 골라
  줄이지 않는다**(D-299 ⑦). 환경변수 값과 데이터 서버 설정 값의 이메일·주민번호·휴대폰만
  `mask_pii`로 가린다(G-11 미결 동안 — `mask_text`는 `-Dport=8080` 같은 설정을 훼손한다 ·
  서버 IP는 가리지 않는다).
- 범위: 도메인(`hostname` → 정합 도메인 · 없으면 고른 소스의 전 도메인) · 인스턴스
  (`hostname` 필수) · 소스(대상 무관). 단위마다 1호출 · 일부 실패 = `[한계]` + `partial` ·
  전부 실패 = 오류(0건으로 세지 않는다) · 자체 상한 없음(D-296 ④).
- v2 경로의 404·405는 「이 제니퍼 버전이 경로를 지원하지 않을 수 있다」(COV E-28)로 적고
  실패로 센다. 예외는 어댑터가 정한다 — 인스턴스 개별 설정 404 = 「개별 설정 없음」(E-19) ·
  비교 룰 표기 재질의(E-01).
- 벤더 경로·필드명은 어댑터(`adapters/jennifer/manage_*`)에만 있다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from apm_gateway.adapters.jennifer.manage_api import (
    COMPARE_SPELLINGS,
    ENV_SCOPES,
    EXTRA,
    RULE_TARGETS,
    JenniferManageApi,
)
from apm_gateway.application.masking import (
    mask_identifier,
    mask_ip,
    mask_pii,
    mask_query,
    mask_sql,
    mask_text,
)
from apm_gateway.application.resolver import Resolution
from apm_gateway.application.sources import STATUS_OK, STATUS_UNAVAILABLE
from apm_gateway.application.tools import IDENTIFIER_FIELDS, ApmTools, _Limits, _positive_int
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import (
    API_ERROR,
    CONTRACT_VIOLATION,
    INVALID_ARGUMENT,
    PROFILE_REF_MISMATCH,
    ApmError,
)

T = TypeVar("T")
U = TypeVar("U")

KINDS = (
    "event_rules",
    "color_boundary",
    "process_instance",
    "data_server",
    "db_path",
    "loaded_classes",
    "rdb_export",
)
RULE_TYPES = ("error", "metric", "compare")
# kind별로 쓰는 인자(`source_ids`는 모두 쓴다) — 그 밖 인자는 빼고 조회하고 `[한계]`로 알린다
_KIND_ARGS: dict[str, frozenset[str]] = {
    "event_rules": frozenset({"hostname", "rule_type", "target", "error_type"}),
    "color_boundary": frozenset(),
    "process_instance": frozenset({"process_id", "hostname"}),
    "data_server": frozenset(),
    "db_path": frozenset({"hostname"}),
    "loaded_classes": frozenset({"hostname", "search"}),
    "rdb_export": frozenset(),
}
# 색상 경계 3개 → 4구간(작은 순 · v2 매뉴얼 — 파랑/연두 · 연두/주황 · 주황/빨강)
_COLORS = (("blue", "파랑"), ("yellowgreen", "연두"), ("orange", "주황"), ("red", "빨강"))
# 사용자 행에서 가리는 칸(원값이 있었으면 봉투 고지 `apm_masked_fields` — 칸 이름만)
_USER_MASKED = ("user_id", "user_name", "email", "phone_number", "allow_ip")
_TEXT_MAX = 256
_V2_UNSUPPORTED = "이 제니퍼 버전이 경로를 지원하지 않을 수 있다(COV E-28 · W10)"
_CURRENT_ONLY = "[한계] 현재값 전용(실행 중 요청) — 과거 사건의 증거로 쓰지 않는다"
_PROCESS_VERSION = (
    "[한계] 프로세스 → 인스턴스 조회는 제니퍼 서버 5.6.0.21 이상 · Java 에이전트 5.6.0.8 이상에서만"
    " 응답한다(COV E-03)"
)
_LOADED_CLASS_LIMIT = (
    "로드된 클래스는 6만 개 이하일 때만 응답한다(제니퍼 제약) — search로 좁혀 다시 조회할 수 있다"
)
_USER_MASK_NOTE = (
    "[한계] 사용자 ID·이름·이메일 앞부분은 앞 1자만 · 전화번호는 칸째 가림 · 허용 IP는 대역만이다"
    "(원값 표시는 G-11 미결)"
)
# 사람·계정 식별자로 보는 `extra` 키(사용자·실행 중 요청 — AUDIT-7): 표의 식별자 칸
# (`IDENTIFIER_FIELDS` — 구분자·대소문자 무시) · `…Id`·`…ID`·`…_id` 끝맺음(`guid`·`valid`처럼
# 소문자로 이어 쓴 끝은 아니다) · `…name`·`nickname…`·`emp…`(대소문자 무시).
_ID_FIELDS = frozenset(f.replace("_", "") for f in (*IDENTIFIER_FIELDS, "id"))
_ID_KEY = re.compile(r"(?:[a-z0-9]Id|ID|[_.\-](?i:id))$")
_PERSON_KEY = re.compile(r"(?i:name$|^nickname|^emp)")
_IGNORED_HOSTNAME = "소스 단위 설정이라 hostname으로 좁히지 않았다(고른 소스 전부)"


@dataclass
class _Scope:
    """도메인 범위 — 호출 단위(소스, 도메인, 도메인 이름)와 정합 결과·소스 상태·인스턴스 이름."""

    units: list[tuple[str, int, str]]
    resolution: Resolution | None = None
    statuses: list[dict[str, Any]] | None = None
    names: dict[tuple[str, int, int], str] = field(default_factory=dict)


def _text(value: Any, name: str) -> str | None:
    """자유 텍스트 인자(검색어·이름 일부·호스트) — 비면 None · 제어·서식 문자·256자 초과는 거부."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApmError(INVALID_ARGUMENT, f"{name}는 문자열이어야 한다")
    text = value.strip()
    if not text:
        return None
    if len(text) > _TEXT_MAX or any(
        unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text
    ):
        raise ApmError(INVALID_ARGUMENT, f"{name}는 제어 문자 없는 {_TEXT_MAX}자 이하여야 한다")
    return text


def _int(value: Any, name: str, *, signed: bool = False) -> int | None:
    """정수 인자(문자열 숫자 포함 · ASCII만) — 비면 None."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    else:
        raw = str(value).strip()
        body = raw[1:] if signed and raw.startswith("-") else raw
        if not (body.isascii() and body.isdigit()):
            raise ApmError(INVALID_ARGUMENT, f"{name}는 정수여야 한다: {value!r}")
        number = int(raw)
    if not signed and number < 0:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 0 이상이어야 한다: {value!r}")
    return number


def _full_text(text: str) -> str:
    return mask_text(text, limit=None)


def _mask_extra(value: Any, mask: Callable[[str], str] = _full_text) -> Any:
    """문자열 잎만 `mask`로 가린다 — 기본은 표에 없는 키의 원형(`mask_text` 전문) · 설정 값은
    `mask_pii`(이메일·주민번호·휴대폰만). 나머지 형은 그대로(반복 순회)."""
    if isinstance(value, str):
        return mask(value)
    if not isinstance(value, (dict, list)):
        return value
    root: Any = {} if isinstance(value, dict) else []
    stack: list[tuple[Any, Any]] = [(value, root)]
    while stack:
        src, dst = stack.pop()
        items = src.items() if isinstance(src, dict) else enumerate(src)
        for key, item in items:
            if isinstance(item, (dict, list)):
                child: Any = {} if isinstance(item, dict) else []
                stack.append((item, child))
            elif isinstance(item, str):
                child = mask(item)
            else:
                child = item
            if isinstance(dst, dict):
                dst[key] = child
            else:
                dst.append(child)
    return root


def _is_person_key(key: Any) -> bool:
    """사람·계정 식별자 칸 이름인가(`clientId`·`userName`·`USER_ID`·`nickname`·`empNo` ·
    AUDIT-7)."""
    text = str(key)
    return (
        text.replace("_", "").lower() in _ID_FIELDS
        or bool(_ID_KEY.search(text) or _PERSON_KEY.search(text))
    )


def _mask_people_extra(value: Any) -> Any:
    """사용자·실행 중 요청의 `extra` — 식별자형 키(가장 가까운 dict 키) 아래 문자열 잎은
    `mask_identifier`, 그 밖 문자열은 `mask_text` 전문(반복 순회)."""
    if not isinstance(value, (dict, list)):
        return _mask_extra(value)
    root: Any = {} if isinstance(value, dict) else []
    stack: list[tuple[Any, Any, bool]] = [(value, root, False)]
    while stack:
        src, dst, person = stack.pop()
        items = src.items() if isinstance(src, dict) else enumerate(src)
        for key, item in items:
            here = _is_person_key(key) if isinstance(src, dict) else person
            if isinstance(item, (dict, list)):
                child: Any = {} if isinstance(item, dict) else []
                stack.append((item, child, here))
            elif isinstance(item, str):
                child = mask_identifier(item) if here else _full_text(item)
            else:
                child = item
            if isinstance(dst, dict):
                dst[key] = child
            else:
                dst.append(child)
    return root


def _mask_email(value: str) -> str:
    """이메일 칸 — `@` 앞을 `mask_identifier`(없으면 전체 · 형식과 무관 — AUDIT-7)."""
    local, at, domain = value.partition("@")
    return mask_identifier(local) + at + domain if at else mask_identifier(value)


def _extra_note(rows: list[dict[str, Any]], what: str) -> str | None:
    """`extra`가 찬 행이 있으면 그 키 이름(값 없음)을 알린다 — 미확인 응답 모양 사유(N-17)."""
    keys = sorted({str(k) for r in rows for k in (r.get(EXTRA) or {})})
    if not keys:
        return None
    shown = ", ".join(keys[:20]) + (f" 외 {len(keys) - 20}개" if len(keys) > 20 else "")
    return (
        f"[한계] {what} 응답에 스펙 표 밖 키({shown})가 있어 extra 칸에 원형으로 실었다"
        "(문자열은 마스킹 · 비밀 키 값은 가림 · 응답 모양 확인 필요 — W10)"
    )


class ManageTools:
    """관리·민감 조회 도구 코어 — 인스턴스 하나를 프로세스에서 공유한다(상태 없음)."""

    def __init__(self, core: ApmTools) -> None:
        self.core = core

    def _api(self, source_id: str) -> JenniferManageApi:
        return JenniferManageApi(self.core.sources.get(source_id).api.client)

    def _why(self, e: ApmError, *, v2: bool = True) -> str:
        """실패 사유 꼬리 — v2 경로의 404·405는 버전 미지원 가능(E-28) · 그 밖은 서버 거부 사유."""
        if v2 and e.status in (404, 405):
            return f"{e.code} — HTTP {e.status}: {_V2_UNSUPPORTED}"
        return e.code + self.core._reason_tail(e)

    def _at(self, source_id: str, domain_id: int | None = None) -> str:
        where = self.core.sources.where(source_id, domain_id)
        return f"({where})" if where else ""

    async def _run(
        self,
        units: list[U],
        call: Callable[[U], Awaitable[T]],
        where: Callable[[U], str],
        what: str,
        limits: _Limits,
        *,
        v2: bool = True,
    ) -> tuple[list[tuple[U, T]], list[tuple[U, ApmError]]]:
        """단위마다 1호출 — 실패는 `[한계]`+partial로 적고 넘어간다. 전부 실패면 오류.
        (성공 단위·결과, 실패 단위·오류)."""
        done: list[tuple[U, T]] = []
        failed: list[tuple[U, ApmError]] = []
        for unit in units:
            try:
                done.append((unit, await call(unit)))
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failed.append((unit, e))
                limits.fail(f"[한계] {what} 조회 실패{where(unit)}: {self._why(e, v2=v2)}")
        if units and len(failed) == len(units):
            raise self.core._all_failed([(where(u).strip("()"), e) for u, e in failed])
        return done, failed

    async def _domains(
        self, hostname: str | None, source_ids: list[str] | None, limits: _Limits
    ) -> _Scope:
        """도메인 범위 — `hostname`이 있으면 정합된 (소스, 도메인) · 없으면 고른 소스의 전
        도메인."""
        if hostname is not None and str(hostname).strip():
            res = await self.core._resolve(hostname, None, source_ids)
            units: dict[tuple[str, int], str] = {}
            names: dict[tuple[str, int, int], str] = {}
            for inst in res.instances:
                units.setdefault((inst["source_id"], inst["domain_id"]), inst["domain_name"])
                names[(inst["source_id"], inst["domain_id"], inst["instance_id"])] = inst[
                    "instance_name"
                ]
            return _Scope([(s, d, n) for (s, d), n in units.items()], res, None, names)
        usable, statuses, notes = await self.core.sources.available(
            self.core.sources.select(source_ids)
        )
        limits.extend(notes)
        if any(row["status"] != STATUS_OK for row in statuses):
            limits.partial = True  # 고른 소스 중 빠진 소스가 있다(사유는 notes에)
        scope = _Scope([], None, statuses)
        for src, inv in usable:
            scope.units += [(src.source_id, d["domain_id"], d["domain_name"]) for d in inv.domains]
            for inst in inv.instances:
                key = (src.source_id, inst["domain_id"], inst["instance_id"])
                scope.names[key] = inst["instance_name"]
        return scope

    @staticmethod
    def _source_statuses(
        selected: list[str], answered: set[str], failed: list[tuple[str, ApmError]]
    ) -> list[dict[str, Any]]:
        """봉투 `sources[]` — 한 호출이라도 답한 소스는 ok(일부 실패는 `[한계]`), 아니면
        unavailable."""
        first: dict[str, ApmError] = {}
        for sid, e in failed:
            first.setdefault(sid, e)
        return [
            {"source_id": sid, "status": STATUS_OK, "reason": ""}
            if sid in answered or sid not in first
            else {
                "source_id": sid,
                "status": STATUS_UNAVAILABLE,
                # 같은 실패의 `limits`·오류 사유처럼 개인정보를 가린다(AUDIT-12)
                "reason": f"{first[sid].code}: {mask_text(first[sid].reason, limit=240)}",
            }
            for sid in selected
        ]

    # ── apm_config ──────────────────────────────────────────

    async def apm_config(
        self,
        kind: str,
        hostname: str | None = None,
        source_ids: list[str] | None = None,
        rule_type: str | None = None,
        target: str | None = None,
        error_type: str | None = None,
        process_id: int | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """제니퍼 설정·관리 조회(`kind` 7종 — 모듈 설명)."""
        tool = "apm_config"
        name = str(kind or "").strip().lower()
        if name not in KINDS:
            raise ApmError(INVALID_ARGUMENT, f"kind는 {'·'.join(KINDS)} 중 하나여야 한다: {kind!r}")
        limits = _Limits()
        given = {
            "hostname": hostname is not None and bool(str(hostname).strip()),
            "rule_type": rule_type is not None and bool(str(rule_type).strip()),
            "target": target is not None and bool(str(target).strip()),
            "error_type": error_type is not None and bool(str(error_type).strip()),
            "process_id": process_id is not None,
            "search": search is not None and bool(str(search).strip()),
        }
        ignored = sorted(k for k, v in given.items() if v and k not in _KIND_ARGS[name])
        if "hostname" in ignored:
            limits.append(f"[한계] kind {name}는 {_IGNORED_HOSTNAME}")
            ignored.remove("hostname")
            hostname = None
        if ignored:
            limits.append(
                f"[한계] kind {name}는 인자 {'·'.join(ignored)}를 쓰지 않는다 — 빼고 조회했다"
            )
        if name == "event_rules":
            return await self._event_rules(tool, hostname, source_ids, rule_type, target,
                                           error_type, limits)
        if name == "color_boundary":
            return await self._color_boundary(tool, source_ids, limits)
        if name == "process_instance":
            return await self._process_instance(tool, process_id, hostname, source_ids, limits)
        if name == "data_server":
            return await self._data_server(tool, source_ids, limits)
        if name == "db_path":
            return await self._db_path(tool, hostname, source_ids, limits)
        if name == "loaded_classes":
            return await self._loaded_classes(tool, hostname, source_ids, search, limits)
        return await self._rdb_export(tool, source_ids, limits)

    async def _event_rules(
        self,
        tool: str,
        hostname: str | None,
        source_ids: list[str] | None,
        rule_type: str | None,
        target: str | None,
        error_type: str | None,
        limits: _Limits,
    ) -> dict[str, Any]:
        rtype = str(rule_type).strip().lower() if rule_type is not None else ""
        if rtype and rtype not in RULE_TYPES:
            raise ApmError(
                INVALID_ARGUMENT,
                f"rule_type은 {'·'.join(RULE_TYPES)} 중 하나여야 한다: {rule_type!r}",
            )
        tgt = str(target).strip().lower() if target is not None else ""
        all_targets = RULE_TARGETS["metric"]
        if tgt and tgt not in all_targets:
            raise ApmError(
                INVALID_ARGUMENT, f"target은 {'·'.join(all_targets)} 중 하나여야 한다: {target!r}"
            )
        if rtype == "compare" and tgt and tgt not in RULE_TARGETS["compare"]:
            raise ApmError(
                INVALID_ARGUMENT,
                f"compare 룰 대상은 {'·'.join(RULE_TARGETS['compare'])} 중 하나다: {target!r}",
            )
        etype = None
        if error_type is not None and str(error_type).strip():
            etype = str(error_type).strip().upper()
            if not JenniferManageApi.is_error_type(etype):
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"error_type은 영문 대문자·숫자·밑줄 64자 이하여야 한다: {error_type!r}",
                )
        types = [rtype] if rtype else list(RULE_TYPES)
        if etype and "error" not in types:
            limits.append(f"[한계] rule_type {rtype}에는 error_type을 쓰지 않는다 — 빼고 조회했다")
            etype = None
        if tgt and "error" in types:
            limits.append("[한계] ERROR 룰은 대상 종류가 없어 target과 무관하게 실었다")
        if not rtype and tgt and tgt not in RULE_TARGETS["compare"]:
            limits.append(f"[한계] compare 룰에는 {tgt} 대상이 없어 비교 룰은 조회하지 않았다")
        scope = await self._domains(hostname, source_ids, limits)
        res = scope.resolution
        # 호출 단위 (소스, 도메인, 도메인 이름, 룰 종류, 대상, 인스턴스)
        calls: list[tuple[str, int, str, str, str | None, dict[str, Any] | None]] = []
        for sid, did, dname in scope.units:
            if "error" in types:
                calls.append((sid, did, dname, "error", None, None))
            for kind in ("metric", "compare"):
                if kind in types:
                    for t in RULE_TARGETS[kind]:
                        if not tgt or t == tgt:
                            calls.append((sid, did, dname, kind, t, None))
            if etype:
                calls.append((sid, did, dname, "error_applied", None, None))
                for inst in res.instances if res is not None else []:
                    if (inst["source_id"], inst["domain_id"]) == (sid, did):
                        calls.append((sid, did, dname, "error_individual", None, inst))
        expect_calls(len(calls))
        spellings: set[str] = set()

        async def fetch(c: tuple[str, int, str, str, str | None, dict[str, Any] | None]) -> Any:
            sid, did, _, kind, t, inst = c
            api = self._api(sid)
            if kind == "error":
                return await api.error_rules(did)
            if kind == "metric":
                assert t is not None
                return await api.metric_rules(did, t)
            if kind == "compare":
                assert t is not None
                found, spelled = await api.compare_rules(did, t)
                if spelled != COMPARE_SPELLINGS[0]:
                    spellings.add(sid)
                return found
            assert etype is not None
            if kind == "error_applied":
                return await api.error_rule_applied(did, etype)
            assert inst is not None
            return await api.error_rule_individual(did, etype, inst["instance_id"])

        def where(c: tuple[str, int, str, str, str | None, dict[str, Any] | None]) -> str:
            sid, did, _, kind, t, inst = c
            parts = [self.core.sources.where(sid, did), kind + (f"/{t}" if t else "")]
            if inst is not None:
                parts.append(f"인스턴스 {inst['instance_id']}")
            return "(" + " · ".join(p for p in parts if p) + ")"

        done, _ = await self._run(calls, fetch, where, "이벤트 룰", limits)
        if etype and res is None:
            limits.append(
                "[한계] 인스턴스별 개별 설정(individual-setting)은 hostname을 줄 때만 조회한다"
            )
        rows: list[dict[str, Any]] = []
        for (sid, did, dname, rule_kind, target_type, instance), result in done:
            base = {
                "source_id": sid,
                "domain_id": did,
                "domain_name": dname,
                "rule_type": rule_kind,
                "target_type": target_type,
            }
            if rule_kind == "error_applied":
                rows.append({**base, "error_type": etype, "applied": result})
            elif rule_kind == "error_individual":
                assert instance is not None
                rows.append(
                    {
                        **base,
                        "instance_id": instance["instance_id"],
                        "instance_name": instance["instance_name"],
                        "error_type": etype,
                        "individual_setting": result,
                        # 404 = 개별 설정 없음(오류 아님 · COV E-19)
                        "individual_setting_found": result is not None,
                    }
                )
            else:
                for rec in result:
                    rows.append(self._rule_row(base, rec))
        for sid in sorted(spellings):
            limits.append(
                f"[한계] 비교 룰 경로 표기{self._at(sid)} — {COMPARE_SPELLINGS[0]}가 HTTP 404라"
                f" {COMPARE_SPELLINGS[1]}으로 다시 물어 답을 받았다(매뉴얼 표기 불일치 COV E-01 ·"
                " W10)"
            )
        if any(r.get("individual_setting_found") is False for r in rows):
            limits.append(
                "[한계] 개별 설정 없음(HTTP 404)인 인스턴스는 individual_setting이 null이다"
            )
        note = _extra_note(rows, "이벤트 룰")
        if note:
            limits.append(note)
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            limits=limits,
            sources=scope.statuses,
            partial=limits.partial,
            kind="event_rules",
        )

    @staticmethod
    def _rule_row(base: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
        row = {**base, **rec}
        for key in ("custom_message", "auto_script_command"):
            if key in row and row[key] is not None:
                row[key] = mask_text(row[key], limit=None)
        row[EXTRA] = _mask_extra(rec.get(EXTRA) or {})
        return row

    async def _per_source(
        self,
        source_ids: list[str] | None,
        per_source: int,
        call: Callable[[str], Awaitable[T]],
        what: str,
        limits: _Limits,
        *,
        v2: bool = True,
    ) -> tuple[list[tuple[str, T]], list[dict[str, Any]]]:
        """소스 범위 — 고른 소스마다 `call` 1회(그 안의 호출 수 `per_source`) · 소스 상태 행."""
        selected = [s.source_id for s in self.core.sources.select(source_ids)]
        expect_calls(len(selected) * per_source)
        done, failed = await self._run(selected, call, self._at, what, limits, v2=v2)
        return done, self._source_statuses(selected, {sid for sid, _ in done}, failed)

    async def _color_boundary(
        self, tool: str, source_ids: list[str] | None, limits: _Limits
    ) -> dict[str, Any]:
        done, statuses = await self._per_source(
            source_ids, 1, lambda sid: self._api(sid).color_boundaries(), "색상 경계", limits
        )
        rows: list[dict[str, Any]] = []
        for sid, bounds in done:
            lower: list[int | float | None] = [None, *bounds]
            upper: list[int | float | None] = [*bounds, None]
            for i, (color, label) in enumerate(_COLORS):
                rows.append(
                    {
                        "source_id": sid,
                        "color": color,
                        "color_label": label,
                        "lower_bound": lower[i],
                        "upper_bound": upper[i],
                    }
                )
        limits.append(
            "[한계] 경계 값은 액티브 서비스 경과 시간 기준이며 단위는 매뉴얼에 없다(예 3000 ·"
            " W10) · lower_bound 이상 upper_bound 미만"
        )
        return self.core.ok(
            tool, rows, limits=limits, sources=statuses, partial=limits.partial,
            kind="color_boundary",
        )

    async def _process_instance(
        self,
        tool: str,
        process_id: int | None,
        hostname: str | None,
        source_ids: list[str] | None,
        limits: _Limits,
    ) -> dict[str, Any]:
        if process_id is None:
            raise ApmError(INVALID_ARGUMENT, "kind process_instance에는 process_id가 필요하다")
        pid = _positive_int(process_id, "process_id", 0)
        host = _text(hostname, "hostname")
        limits.append(_PROCESS_VERSION)
        done, statuses = await self._per_source(
            source_ids,
            1,
            lambda sid: self._api(sid).instances_by_process(pid, host),
            "프로세스 → 인스턴스",
            limits,
        )
        rows: list[dict[str, Any]] = []
        for sid, found in done:
            names: dict[tuple[int, int], str] = {}
            if found:
                inv = await self.core.sources.get(sid).resolver.inventory()
                if inv.problem() is not None:
                    limits.append(
                        f"[한계] 인스턴스 이름을 붙이지 못했다{self._at(sid)} — 인스턴스 목록"
                        " 조회 불가"
                    )
                names = {
                    (i["domain_id"], i["instance_id"]): i["instance_name"] for i in inv.instances
                }
            for rec in found:
                rows.append(
                    {
                        "source_id": sid,
                        "domain_id": rec["domain_id"],
                        "instance_id": rec["instance_id"],
                        "hostname": rec["hostname"],
                        "instance_name": names.get((rec["domain_id"], rec["instance_id"])),
                        EXTRA: _mask_extra(rec.get(EXTRA) or {}),
                    }
                )
        if not rows:
            limits.append(
                f"[한계] 프로세스 ID {pid}" + (" · 호스트 조건" if host else "")
                + "에 해당하는 인스턴스가 없다(버전 조건 미충족이어도 빈 결과일 수 있다)"
            )
        note = _extra_note(rows, "프로세스 → 인스턴스")
        if note:
            limits.append(note)
        return self.core.ok(
            tool, rows, limits=limits, sources=statuses, partial=limits.partial,
            kind="process_instance",
        )

    async def _data_server(
        self, tool: str, source_ids: list[str] | None, limits: _Limits
    ) -> dict[str, Any]:
        selected = [s.source_id for s in self.core.sources.select(source_ids)]
        sections = ("domains", "resource", "system_properties")
        units = [(sid, section) for sid in selected for section in sections]
        expect_calls(len(units))

        async def fetch(unit: tuple[str, str]) -> Any:
            sid, section = unit
            api = self._api(sid)
            if section == "domains":
                return await api.data_server_domains()
            if section == "resource":
                return await api.data_server_resource()
            return await api.data_server_properties()

        def where(unit: tuple[str, str]) -> str:
            at = self.core.sources.where(unit[0])
            return f"({at + ' · ' if at else ''}{unit[1]})"

        done, failed = await self._run(units, fetch, where, "데이터 서버", limits)
        statuses = self._source_statuses(
            selected, {sid for (sid, _), _ in done}, [(sid, e) for (sid, _), e in failed]
        )
        rows: list[dict[str, Any]] = []
        counts: dict[str, int | None] = {}
        for (sid, section), result in done:
            if section == "domains":
                count, found = result
                counts[sid] = count
            else:
                found = result
            for rec in found:
                rows.append(
                    {
                        "source_id": sid,
                        "section": section,
                        # 설정 값의 개인정보(이메일·주민번호·휴대폰)는 가린다(G-11 미결 동안)
                        **{k: _mask_extra(v, mask_pii) for k, v in rec.items() if k != EXTRA},
                        EXTRA: _mask_extra(rec.get(EXTRA) or {}),
                    }
                )
        note = _extra_note(rows, "데이터 서버")
        if note:
            limits.append(note)
        return self.core.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=limits.partial,
            kind="data_server",
            summary={"data_server_count": counts},
        )

    async def _db_path(
        self,
        tool: str,
        hostname: str | None,
        source_ids: list[str] | None,
        limits: _Limits,
    ) -> dict[str, Any]:
        scope = await self._domains(hostname, source_ids, limits)
        expect_calls(len(scope.units))
        done, _ = await self._run(
            scope.units,
            lambda u: self._api(u[0]).db_path(u[1]),
            lambda u: self._at(u[0], u[1]),
            "DB 경로",
            limits,
        )
        rows = [
            {
                "source_id": sid,
                "domain_id": did,
                "domain_name": dname,
                **{k: v for k, v in rec.items() if k != EXTRA},
                EXTRA: _mask_extra(rec.get(EXTRA) or {}),
            }
            for (sid, did, dname), rec in done
        ]
        note = _extra_note(rows, "DB 경로")
        if note:
            limits.append(note)
        return self.core.ok(
            tool,
            rows,
            resolution=scope.resolution,
            limits=limits,
            sources=scope.statuses,
            partial=limits.partial,
            kind="db_path",
        )

    async def _loaded_classes(
        self,
        tool: str,
        hostname: str | None,
        source_ids: list[str] | None,
        search: str | None,
        limits: _Limits,
    ) -> dict[str, Any]:
        if hostname is None or not str(hostname).strip():
            raise ApmError(INVALID_ARGUMENT, "kind loaded_classes에는 hostname이 필요하다")
        text = _text(search, "search")
        res = await self.core._resolve(hostname, None, source_ids)
        expect_calls(len(res.instances))
        try:
            done, failed = await self._run(
                res.instances,
                lambda i: self._api(i["source_id"]).loaded_classes(
                    i["domain_id"], i["instance_id"], text
                ),
                lambda i: f"(인스턴스 {self.core._inst_label((i['source_id'], i['instance_id']))})",
                "로드된 클래스",
                limits,
            )
        except ApmError as e:
            if e.code != API_ERROR:  # 미접속·timeout은 6만 개 제약과 무관
                raise
            raise ApmError(e.code, f"{e.reason} — {_LOADED_CLASS_LIMIT}", status=e.status) from e
        if any(e.code == API_ERROR for _, e in failed):
            limits.append(f"[한계] {_LOADED_CLASS_LIMIT}")
        rows = [
            {
                "source_id": inst["source_id"],
                "domain_id": inst["domain_id"],
                "instance_id": inst["instance_id"],
                "instance_name": inst["instance_name"],
                **{k: v for k, v in rec.items() if k != EXTRA},
                EXTRA: _mask_extra(rec.get(EXTRA) or {}),
            }
            for inst, found in done
            for rec in found
        ]
        note = _extra_note(rows, "로드된 클래스")
        if note:
            limits.append(note)
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            limits=limits,
            partial=limits.partial,
            kind="loaded_classes",
        )

    async def _rdb_export(
        self, tool: str, source_ids: list[str] | None, limits: _Limits
    ) -> dict[str, Any]:
        done, statuses = await self._per_source(
            source_ids, 1, lambda sid: self._api(sid).rdb_exports(), "수동 RDB Export", limits
        )
        rows = [
            {
                "source_id": sid,
                **{k: v for k, v in rec.items() if k != EXTRA},
                EXTRA: _mask_extra(rec.get(EXTRA) or {}),
            }
            for sid, found in done
            for rec in found
        ]
        limits.append("[한계] 작업 상태 값 목록은 미공개다(예 COMPLETED·EXPORTING · COV E-29)")
        note = _extra_note(rows, "수동 RDB Export")
        if note:
            limits.append(note)
        return self.core.ok(
            tool, rows, limits=limits, sources=statuses, partial=limits.partial,
            kind="rdb_export",
        )

    # ── apm_environment ─────────────────────────────────────

    async def apm_environment(
        self,
        hostname: str | None = None,
        source_ids: list[str] | None = None,
        scope: str | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        """환경변수(SYSTEM)·JVM 시스템 속성(JAVA) — 인스턴스별 긴 형식 행."""
        tool = "apm_environment"
        wanted_scope = str(scope).strip().upper() if scope is not None else ""
        if wanted_scope and wanted_scope not in ENV_SCOPES:
            raise ApmError(
                INVALID_ARGUMENT, f"scope는 {'·'.join(ENV_SCOPES)} 중 하나여야 한다: {scope!r}"
            )
        needle = _text(key, "key")
        limits = _Limits()
        dom = await self._domains(hostname, source_ids, limits)
        wanted = set(dom.names) if dom.resolution is not None else None
        expect_calls(len(dom.units))
        done, _ = await self._run(
            dom.units,
            lambda u: self._api(u[0]).environment(u[1]),
            lambda u: self._at(u[0], u[1]),
            "환경변수",
            limits,
        )
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int]] = set()
        other_scopes: set[str] = set()
        pii_masked = False
        for (sid, did, _), entries in done:
            for e in entries:
                ident = (sid, did, e["instance_id"])
                if wanted is not None and ident not in wanted:
                    continue
                seen.add(ident)
                if e["scope"] not in ENV_SCOPES:
                    other_scopes.add(e["scope"])
                if wanted_scope and e["scope"].upper() != wanted_scope:
                    continue
                if needle and needle.lower() not in str(e["name"] or "").lower():
                    continue
                value = _mask_extra(e["value"], mask_pii)
                pii_masked = pii_masked or value != e["value"]
                rows.append(
                    {
                        "source_id": sid,
                        "domain_id": did,
                        "instance_id": e["instance_id"],
                        "instance_name": dom.names.get(ident),
                        "scope": e["scope"],
                        "name": e["name"],
                        # 비밀 값은 중앙 경계가 이미 `[가림]` · 개인정보는 여기서(G-11 미결 동안)
                        "value": value,
                    }
                )
        if wanted is not None:
            ok_domains = {(sid, did) for (sid, did, _), _ in done}
            missing = sorted(
                ident for ident in wanted - seen if (ident[0], ident[1]) in ok_domains
            )
            if missing:
                labels = ", ".join(self.core._inst_label((s, i)) for s, _, i in missing)
                limits.append(f"[한계] 환경변수 응답에 없는 인스턴스: {labels}")
        if other_scopes and not wanted_scope:
            limits.append(
                f"[한계] 환경변수 응답에 SYSTEM·JAVA 밖 묶음({', '.join(sorted(other_scopes))})이"
                " 있어 원형으로 실었다(비밀 키 값은 가림 · 응답 모양 확인 필요 — COV E-13 · W10)"
            )
        if needle or wanted_scope:
            limits.append(
                "[한계] 조건으로 거른 결과다"
                + (f" — 묶음 {wanted_scope}" if wanted_scope else "")
                + (" — 이름 일부 일치(대소문자 무시)" if needle else "")
            )
        return self.core.ok(
            tool,
            rows,
            resolution=dom.resolution,
            limits=limits,
            sources=dom.statuses,
            partial=limits.partial,
            masked={"value"} if pii_masked else set(),
        )

    # ── apm_users ───────────────────────────────────────────

    async def apm_users(
        self, user_id: str | None = None, source_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """제니퍼 사용자 목록(사용자 목록 + 계정 목록) 또는 계정 1건 — 비밀번호는 없다."""
        tool = "apm_users"
        uid = None
        if user_id is not None and str(user_id).strip():
            uid = str(user_id).strip()
            problem = JenniferManageApi.account_id_error(uid)
            if problem:
                raise ApmError(INVALID_ARGUMENT, problem)
        limits = _Limits()
        shown_id = mask_identifier(uid) if uid else ""
        origins = ("account",) if uid else ("user_list", "accounts")
        selected = [s.source_id for s in self.core.sources.select(source_ids)]
        units = [(sid, origin) for sid in selected for origin in origins]
        expect_calls(len(units))
        absent: list[str] = []

        async def fetch(unit: tuple[str, str]) -> list[dict[str, Any]]:
            sid, origin = unit
            api = self._api(sid)
            if origin == "user_list":
                return await api.user_list()
            if origin == "accounts":
                return await api.accounts()
            assert uid is not None
            try:
                found = await api.account(uid)
            except ApmError as e:
                if e.status == 404:  # 계정이 없거나 경로 미지원 — 구분할 수 없다
                    absent.append(f"{self._at(sid)} HTTP 404".strip())
                    return []
                # 클라이언트가 경로·되울린 계정 ID를 이미 변수 표기로 바꿨다 — 남은 것도 가린 ID로
                raise ApmError(
                    e.code, f"계정 {shown_id}: {e.reason.replace(uid, shown_id)}", status=e.status
                ) from e
            if found is None:
                absent.append(self._at(sid) or "빈 응답")
                return []
            return [found]

        def where(unit: tuple[str, str]) -> str:
            at = self.core.sources.where(unit[0])
            return f"({at + ' · ' if at else ''}{unit[1]})"

        done, failed = await self._run(units, fetch, where, "사용자", limits, v2=False)
        rows: list[dict[str, Any]] = []
        masked: set[str] = set()
        for (sid, origin), found in done:
            for rec in found:
                row = {
                    "source_id": sid,
                    "origin": origin,
                    "user_id": mask_identifier(rec.get("user_id")),
                    "user_name": mask_identifier(rec.get("user_name")),
                    "email": _mask_email(rec["email"]) if rec.get("email") is not None else None,
                    # 형식과 무관하게 칸째 가린다(국제·유선·점 구분 — AUDIT-7)
                    "phone_number": ("<phone>" if rec["phone_number"] else rec["phone_number"])
                    if rec.get("phone_number") is not None
                    else None,
                    "group": rec.get("group"),
                    "allow_ip": mask_ip(rec["allow_ip"])
                    if rec.get("allow_ip") is not None
                    else None,
                    EXTRA: _mask_people_extra(rec.get(EXTRA) or {}),
                }
                masked |= {f for f in _USER_MASKED if rec.get(f)}
                rows.append(row)
        if absent:
            limits.append(
                f"[한계] 계정 {shown_id} 없음: {', '.join(absent)}"
                " (HTTP 404는 계정이 없거나 이 버전이 경로를 지원하지 않는 경우 — 구분 불가 · W10)"
            )
        limits.append(_USER_MASK_NOTE)
        note = _extra_note(rows, "사용자")
        if note:
            limits.append(note)
        statuses = self._source_statuses(
            selected, {sid for (sid, _), _ in done}, [(sid, e) for (sid, _), e in failed]
        )
        return self.core.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=limits.partial,
            masked=masked,
        )

    # ── apm_active_detail ───────────────────────────────────

    async def apm_active_detail(
        self,
        domain_id: int | None = None,
        txid: str | int | None = None,
        session_id: int | None = None,
        thread_hash: int | None = None,
        source_id: str | None = None,
        hostname: str | None = None,
    ) -> dict[str, Any]:
        """실행 중 요청 상세 — `apm_active_services` 행의 `active_ref`를 그대로 받는다."""
        tool = "apm_active_detail"
        if domain_id is None or txid is None:
            raise ApmError(
                INVALID_ARGUMENT,
                "active_ref(domain_id·txid)가 필요하다 — apm_active_services 결과의 active_ref를"
                " 그대로 넘길 것",
            )
        did = _int(domain_id, "domain_id")
        tx = str(txid).strip()
        if not JenniferManageApi.is_txid(tx):
            raise ApmError(INVALID_ARGUMENT, f"txid는 정수여야 한다(음수 허용): {txid!r}")
        session = _int(session_id, "session_id", signed=True)
        thread = _int(thread_hash, "thread_hash", signed=True)
        assert did is not None
        self.core.sources.require_configured()
        if not source_id:
            if len(self.core.sources) > 1:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"소스가 {len(self.core.sources)}개라 active_ref의 source_id가 필요하다"
                    f"(설정된 소스: {self.core.sources.ids}) — active_ref를 그대로 넘길 것",
                )
            source_id = self.core.sources.ids[0]
        sid = self.core.sources.select([str(source_id).strip()])[0].source_id
        res = None
        if hostname is not None and str(hostname).strip():
            res = await self.core._resolve(hostname, None, [sid])
            if (sid, did) not in res.source_domains:
                raise ApmError(
                    PROFILE_REF_MISMATCH,
                    f"active_ref의 domain_id {did}는 hostname {hostname!r}의 정합 도메인"
                    f" {sorted(d for _, d in res.source_domains)}이 아니다"
                    + (f"(소스 {sid})" if len(self.core.sources) > 1 else ""),
                )
        limits = _Limits([_CURRENT_ONLY])
        if session is None or thread is None:
            limits.append(
                "[한계] session_id·thread_hash 중 받지 못한 값은 보내지 않았다 — 필수 여부 미기재"
                "(COV E-15) · active_ref를 그대로 넘길 것"
            )
        expect_calls(1)
        try:
            rec = await self._api(sid).active_detail(did, tx, session, thread)
        except ApmError as e:
            if e.status in (404, 405):
                raise ApmError(
                    e.code,
                    f"실행 중 요청 상세 조회 실패: HTTP {e.status} — 요청이 이미 끝났거나"
                    f"(현재값 전용) {_V2_UNSUPPORTED}",
                    status=e.status,
                ) from e
            raise
        row = {
            "source_id": sid,
            "domain_id": did,
            "txid": tx,
            "session_id": session,
            "thread_hash": thread,
            "user_id": mask_identifier(rec.get("user_id")),
            "guid": rec.get("guid"),
            "sql": mask_sql(rec["sql"]) if rec.get("sql") is not None else None,
            "http_method": rec.get("http_method"),
            "http_query": mask_query(rec["http_query"])
            if rec.get("http_query") is not None
            else None,
            EXTRA: _mask_people_extra(rec.get(EXTRA) or {}),
        }
        note = _extra_note([row], "실행 중 요청 상세")
        if note:
            limits.append(note)
        return self.core.ok(
            tool,
            [row],
            resolution=res,
            limits=limits,
            partial=limits.partial,
            masked={"user_id"} if rec.get("user_id") else set(),
        )
