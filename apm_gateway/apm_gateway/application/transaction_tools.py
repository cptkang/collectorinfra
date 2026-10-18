"""트랜잭션 도구군 `TransactionTools`(`apm_slow_transactions`·`apm_active_services`·`apm_events`·
`apm_transaction_profile` · plans/178 W3에서 `tools.py`의 `ApmTools`에서 옮김)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.masking import mask_identifier, mask_ip, mask_sql, mask_text, mask_url
from apm_gateway.application.tool_common import (
    EVENTS_DEFAULT_MINUTES,
    FILE_ONLY_COLUMNS,
    HOURLY_AFTER_MINUTES,
    InstKey,
    Limits,
    check_n,
    group_by_domain,
    identifier_fields,
    inst_key,
    limit_notice,
    masked_full_text,
    nonneg_int,
    profile_ref_of,
    select_rows,
    tag_records,
    target_text,
    tx_fields,
)
from apm_gateway.domain import jobs as js
from apm_gateway.domain import signals as sig
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import (
    INVALID_ARGUMENT,
    PROFILE_REF_MISMATCH,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    ApmError,
)

SLOW_TX_DEFAULT_MINUTES = 10
# 화면용 발췌 — 전문은 결과 파일(`artifact.text_parts` profile)
PROFILE_EXCERPT_LINES = 60
PROFILE_EXCERPT_CHARS = 4000
# 프로파일 행은 트랜잭션이 중첩 칸이다(W1 검증 L-3)
PROFILE_FILE_ONLY = ("transaction.instance_oid",)
_PROFILE_BUDGET_TTL = 3600.0
# plans/134 W5 — 프로파일 예산을 쓰지 않는 주체(채팅 전용 토큰 · `APM_GATEWAY_BEARER_TOKENS`의 키)
CHAT_PRINCIPAL = "chat"
_UNSPECIFIED = "_unspecified"
KEY_ARG_NOTE = "[한계] key 인자는 의미·값 출처 미확인(W10) — 보내지 않았다"


class TransactionTools:
    """트랜잭션·액티브 서비스·이벤트·프로파일 도구군 — 코어(`core`)를 받아 쓴다."""

    def __init__(self, core: ApmCore) -> None:
        self.core = core
        self._profile_budget: dict[tuple[str, str], tuple[int, float]] = {}

    async def apm_slow_transactions(
        self,
        hostname: str | None = None,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        full: bool = False,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_slow_transactions"
        top_n = check_n(n)
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=SLOW_TX_DEFAULT_MINUTES
        )
        assert window is not None
        limits = Limits()
        txs, ok = await self.core.xview(res, window, limits)
        hourly = (
            await self.core.hourly(res, window, limits)
            if window.minutes > HOURLY_AFTER_MINUTES
            else None
        )
        if not ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "트랜잭션 조회 실패")
        ranked = sorted(txs, key=lambda t: t.get("response_time_ms") or 0, reverse=True)
        selected = select_rows(ranked, top_n, full)
        rows = [
            {
                "source_id": tx["source_id"],
                **tx_fields(tx),
                "profile_ref": profile_ref_of(tx, "end_time_ms"),
            }
            for tx in selected
        ]
        judged = ranked[:top_n] if top_n is not None else ranked
        signals: list[dict[str, Any]] = []
        per_inst: dict[InstKey, list[dict[str, Any]]] = {}
        for tx in ranked:
            per_inst.setdefault(inst_key(tx), []).append(tx)
        for (sid, iid), items in per_inst.items():
            signals += tag_records(
                sig.judge_transactions(items[:top_n], self.core.th, instance_id=iid), sid
            )
        summary = {**self.core.window_stats(txs), **sig.transaction_shares(judged)}
        extra: dict[str, Any] = {"summary": summary}
        if hourly is not None:
            extra["hourly"] = hourly
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            masked=identifier_fields(selected),
            **extra,
        )

    async def apm_active_services(
        self,
        hostname: str | None = None,
        instance_id: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        full: bool = False,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_active_services"
        top_n = check_n(n)
        res = await self.core.resolve(hostname, instance_id, source_ids, instance_name)
        limits = Limits(
            [
                "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
                "[한계] elapsed_ms는 경과 시간 필드 단위 미기재로 ms로 가정(U-12)",
            ]
        )
        wanted = {inst_key(i) for i in res.instances}
        services: list[dict[str, Any]] = []
        ok = False
        groups = group_by_domain(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                services += [
                    s
                    for s in tag_records(
                        await self.core.api(sid).active_services(domain_id, ids), sid, domain_id
                    )
                    if inst_key(s) in wanted
                ]
                ok = True
            except ApmError as e:
                where = self.core.sources.where(sid, domain_id)
                limits.fail(limit_notice(e, "액티브 서비스", f"({where})"))
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        ranked = sorted(services, key=lambda s: s.get("elapsed_ms") or 0, reverse=True)
        rows = [
            {
                "source_id": s["source_id"],
                "domain_id": s.get("domain_id"),
                "domain_name": s.get("domain_name", ""),
                "instance_id": s["instance_id"],
                "instance_name": s.get("instance_name", ""),
                "instance_oid": s.get("instance_oid"),
                "application": mask_url(s["application"]),
                "application_alias": mask_url(s.get("application_alias", "")),
                "status": s["status"],
                "status_name": s["status_name"],
                "status_message": mask_text(s.get("status_message", ""), limit=None),
                "elapsed_ms": s["elapsed_ms"],
                "status_elapsed_ms": s["status_elapsed_ms"],
                "running_ms": s.get("running_ms"),
                "cpu_ms": s.get("cpu_ms"),
                "sql_count": s.get("sql_count"),
                "fetch_count": s.get("fetch_count"),
                "running_mode": s["running_mode"],
                "running_text": mask_text(s["running_text"], limit=None),
                "running_hash": s.get("running_hash"),
                "running_sherpa_oracle_instance": s.get("running_sherpa_oracle_instance", ""),
                "running_sherpa_oracle_seq": s.get("running_sherpa_oracle_seq"),
                "datasource": s["datasource"],
                "client_ip": mask_ip(s["client_ip"]),
                "txid": s["txid"],
                "session_id": s.get("session_id"),
                "thread_hash": s.get("thread_hash"),
                "start_time_ms": s["start_time_ms"],
                "business_ids": list(s.get("business_ids") or []),
                "business_names": list(s.get("business_names") or []),
                # 실행 중 요청 상세(F-15 · W7)의 입력 — 소스·도메인·txid·세션·스레드 해시
                "active_ref": {
                    "source_id": s["source_id"],
                    "domain_id": s.get("domain_id"),
                    "txid": s["txid"],
                    "session_id": s.get("session_id"),
                    "thread_hash": s.get("thread_hash"),
                }
                if s.get("txid")
                else None,
            }
            for s in select_rows(ranked, top_n, full)
        ]
        signals: list[dict[str, Any]] = []
        for key in sorted(wanted):
            signals += tag_records(
                sig.judge_active_services(
                    [s for s in services if inst_key(s) == key],
                    self.core.th,
                    instance_id=key[1],
                    source_tool=tool,
                ),
                key[0],
            )
        summary = {
            "total": len(services),
            "by_running_mode": dict(Counter(s["running_mode"] or "(없음)" for s in services)),
            "by_status": dict(
                Counter(s["status_name"] or s["status"] or "(없음)" for s in services)
            ),
        }
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            summary=summary,
        )

    async def apm_events(
        self,
        hostname: str | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        level: str | None = None,
        source_ids: list[str] | None = None,
        level_mode: str | None = None,
        error_type: str | None = None,
        record: str | None = None,
        n: int | None = None,
        full: bool = False,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_events"
        lvl, mode, etype, rec = self.core.event_options(level, level_mode, error_type, record)
        top_n = check_n(n, default=None)
        res = await self.core.resolve(hostname, None, source_ids, instance_name)
        limits = Limits()
        window = self.core.window(
            reference_time, lookback_minutes, default_minutes=EVENTS_DEFAULT_MINUTES
        )
        assert window is not None
        if mode == "exact":
            limits.append(
                f"[한계] level={lvl} 정확 일치 — API level 필터 의미 미확인(W10) · 응답을 다시"
                " 정확 일치로 걸렀다"
            )
        wanted = {inst_key(i) for i in res.instances}
        events: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        events_ok = errors_ok = False
        groups = group_by_domain(res)
        expect_calls(len(groups) * 2)
        for (sid, domain_id), ids in groups.items():
            api = self.core.api(sid)
            where = self.core.sources.where(sid, domain_id)
            try:
                batch = await api.events(
                    domain_id,
                    ids,
                    window.start_ms,
                    window.end_ms,
                    level=lvl if mode == "exact" else None,
                )
                events += [e for e in tag_records(batch, sid, domain_id) if inst_key(e) in wanted]
                events_ok = True
            except ApmError as e:
                limits.fail(limit_notice(e, "이벤트", f"({where})"))
            try:
                batch = await self.core.fetch_errors(
                    api, domain_id, ids, window, etype, where, limits
                )
                errors += [e for e in tag_records(batch, sid, domain_id) if inst_key(e) in wanted]
                errors_ok = True
            except ApmError as e:
                limits.fail(limit_notice(e, "오류 기록", f"({where})"))
        if (rec == "event" and not events_ok) or (rec == "error" and not errors_ok):
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        events = self.core.filter_events(events, lvl, mode, etype)
        if etype is not None:
            errors = [e for e in errors if self.core.api(e["source_id"]).same_error_type(
                e["error_type"], etype
            )]
        events.sort(key=lambda e: e.get("time_ms") or 0, reverse=True)
        errors.sort(key=lambda e: e.get("time_ms") or 0, reverse=True)
        signals: list[dict[str, Any]] = []
        for ev in events:
            s = sig.signal_from_event(
                self.core.api(ev["source_id"]).event_signal(ev["event_type"]),
                ev["event_type"],
                instance_id=ev["instance_id"],
                source_tool=tool,
            )
            if s is not None:
                signals.append({**s, "source_id": ev["source_id"]})
        if rec == "event":
            rows = [self.core.event_row(ev) for ev in select_rows(events, top_n, full)]
        else:
            rows = [self._error_row(er) for er in select_rows(errors, top_n, full)]
        error_summary = Counter(e["error_type"] or "(없음)" for e in errors).most_common()
        return self.core.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            record=rec,
            errors_by_type=[{"error_type": k, "count": v} for k, v in error_summary],
        )

    @staticmethod
    def _error_row(er: dict[str, Any]) -> dict[str, Any]:
        ref = profile_ref_of(er, "time_ms")
        if ref is not None and er.get("profile_index") is not None:
            # 오류가 난 프로파일 번호 — apm_transaction_profile `profile_no`로 그대로 넘긴다(W5)
            ref["profile_no"] = er["profile_index"]
        return {
            "time_ms": er["time_ms"],
            "error_type": er["error_type"],
            "value": er.get("value"),
            "message": mask_text(er["message"], limit=None),
            "source_id": er["source_id"],
            "domain_id": er.get("domain_id"),
            "domain_name": er.get("domain_name", ""),
            "instance_id": er["instance_id"],
            "instance_name": er.get("instance_name", ""),
            "instance_oid": er.get("instance_oid"),
            "application": mask_url(er["application"]),
            "txid": er["txid"],
            "profile_index": er.get("profile_index"),
            "profile_ref": ref,
        }

    def _consume_profile_budget(
        self, principal: str, investigation_id: str | None, owner: str | None
    ) -> None:
        """프로파일 예산 1회 — 채팅 주체는 쓰지 않는다. 그 밖 주체의 칸은 (주체, `investigation_id`
        → `owner` → `_unspecified`)이고 주체가 다르면 같은 칸을 쓰지 않는다(종전 `_anonymous` 전
        주체 공유 폐지). 주체는 전송 토큰에서만 온다 — 인자(`investigation_id`·`owner`)는 칸을 고를
        뿐 면제를 주지 않는다."""
        if principal == CHAT_PRINCIPAL:
            return
        now = self.core.clock()
        for key in [
            k for k, (_, t0) in self._profile_budget.items() if now - t0 > _PROFILE_BUDGET_TTL
        ]:
            del self._profile_budget[key]
        inv = str(investigation_id).strip() if investigation_id is not None else ""
        own = str(owner).strip() if owner is not None else ""
        key = (principal, inv or own or _UNSPECIFIED)
        count, t0 = self._profile_budget.get(key, (0, now))
        limit = self.core.cfg.runtime.profile_calls_per_investigation
        if count >= limit:
            which = (
                f"investigation_id={inv}"
                if inv
                else (f"owner={own}" if own else "investigation_id·owner 없음")
            )
            raise ApmError(
                RATE_LIMITED,
                f"조사당 프로파일 호출 상한 {limit}회 초과(주체 {principal} · {which})",
            )
        self._profile_budget[key] = (count + 1, t0)

    async def apm_transaction_profile(
        self,
        hostname: str | None = None,
        domain_id: int | None = None,
        txid: str | int | None = None,
        time_ms: int | None = None,
        top_k: int | None = None,
        investigation_id: str | None = None,
        source_id: str | None = None,
        profile_no: int | None = None,
        include_param_key: bool | None = None,
        instance_name: str | None = None,
        *,
        owner: str | None = None,
        principal: str = js.ANONYMOUS_PRINCIPAL,
    ) -> dict[str, Any]:
        """개별 트랜잭션 프로파일. `profile_no`(오류 행 `profile_ref.profile_no`)와
        `include_param_key`는 SQL 조회에만 싣는다. `principal`은 서버가 전송 토큰으로 정한 호출
        주체다(MCP 인자 아님)."""
        tool = "apm_transaction_profile"
        k = check_n(top_k, default=None)  # 없으면 SQL 전부(W1 — 종전 ≤20·기본 10 상한 제거)
        pno = None if profile_no is None else nonneg_int(profile_no, "profile_no")
        if include_param_key is not None and not isinstance(include_param_key, bool):
            raise ApmError(INVALID_ARGUMENT, "include_param_key는 true·false여야 한다")
        if domain_id is None or txid is None or time_ms is None:
            raise ApmError(
                INVALID_ARGUMENT,
                "profile_ref(domain_id·txid·time_ms)가 필요하다 — apm_slow_transactions·apm_events"
                " 결과의 profile_ref를 그대로 넘길 것",
            )
        if (
            not str(txid).lstrip("-").isdigit()
            or not str(time_ms).isdigit()
            or not str(domain_id).isdigit()
        ):
            raise ApmError(INVALID_ARGUMENT, "domain_id·txid·time_ms는 정수여야 한다")
        self.core.sources.require_configured()
        if not source_id:
            if len(self.core.sources) > 1:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"소스가 {len(self.core.sources)}개라 profile_ref의 source_id가 필요하다"
                    f"(설정된 소스: {self.core.sources.ids}) — profile_ref를 그대로 넘길 것",
                )
            source_id = self.core.sources.ids[0]
        sid = str(source_id).strip()
        # 정합은 그 소스에서만 한다 — profile_ref가 가리키지 않는 소스는 부르지 않는다.
        res = await self.core.resolve(hostname, None, [sid], instance_name)
        if (sid, int(domain_id)) not in res.source_domains:
            raise ApmError(
                PROFILE_REF_MISMATCH,
                f"domain_id {domain_id}는 {target_text(hostname, instance_name)}의 정합 도메인"
                f" {sorted(d for _, d in res.source_domains)}이 아니다"
                + (f"(소스 {sid})" if len(self.core.sources) > 1 else ""),
            )
        self._consume_profile_budget(principal, investigation_id, owner)
        api = self.core.api(sid)
        d, tx_id, t_ms = int(domain_id), int(txid), int(time_ms)
        limits = Limits(
            [
                "[한계] 프로파일 텍스트 형식 미검증(J0-L-b 녹화 전) — 단계 요약 없이 마스킹 발췌",
                KEY_ARG_NOTE,
            ]
        )
        if include_param_key:
            limits.append(
                "[한계] include_param_key 응답 모양은 미공개다(W10) — SQL 문자열 칸만 읽어 리터럴을"
                " 가렸다(mask_sql)"
            )
        expect_calls(3)
        detail = excerpt = None
        truncated = False
        full_text: str | None = None
        sqls: list[str] = []
        bind_masked = False
        ok = False
        try:
            detail = await api.transaction_detail(d, tx_id, t_ms)
            ok = True
        except ApmError as e:
            limits.fail(limit_notice(e, "트랜잭션 상세"))
        try:
            text = await api.profile_text(d, tx_id, t_ms)
            lines = text.splitlines()
            shown = [mask_text(line, limit=400) for line in lines[:PROFILE_EXCERPT_LINES]]
            excerpt = "\n".join(shown)[:PROFILE_EXCERPT_CHARS]
            truncated = len(lines) > PROFILE_EXCERPT_LINES or any(
                len(line) > 400 for line in lines[:PROFILE_EXCERPT_LINES]
            ) or len("\n".join(shown)) > PROFILE_EXCERPT_CHARS
            if truncated:
                full_text = masked_full_text(text)
                limits.append(
                    f"[한계] profile_excerpt는 화면용 발췌(앞 {PROFILE_EXCERPT_LINES}줄) —"
                    " 전문(마스킹본)은 결과 파일(artifact.text_parts profile)"
                )
            ok = True
        except ApmError as e:
            limits.fail(limit_notice(e, "프로파일 텍스트"))
        try:
            raw_sqls = await api.transaction_sqls(
                d, tx_id, t_ms, k, profile_no=pno, include_param_key=include_param_key
            )
            # 응답 모양이 미공개라(COV E-11) SQL 칸으로 모은 문자열에 바인드 값이 섞일 수 있다 —
            # 문인지는 **출처 칸 이름**으로 가른다(키워드로 가르면 `{call …}`·`BEGIN … END`가
            # 훼손되고 키워드가 섞인 바인드 값이 새어 나간다 · plans/134 W7 AUDIT-8 · VG-1). SQL 문
            # 칸은 리터럴·개인정보를 가리고, 그 밖 칸은 앞 1자만 남긴다(G-11 미결 동안).
            sqls = [mask_sql(s) if is_sql else mask_identifier(s) for is_sql, s in raw_sqls]
            if any(not is_sql for is_sql, _ in raw_sqls):
                bind_masked = True
                limits.append(
                    "[한계] SQL 응답에 SQL 문 칸이 아닌 문자열(바인드 값일 수 있음)이 있어"
                    " 앞 1자만 남기고 가렸다(응답 모양 미공개 — W10)"
                )
            ok = True
        except ApmError as e:
            limits.fail(limit_notice(e, "SQL"))
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        transaction = tx_fields(detail) if detail else None
        row = {
            "source_id": sid,
            "domain_id": d,
            "txid": str(tx_id),
            "time_ms": t_ms,
            "profile_no": pno,
            "transaction": transaction,
            "profile_excerpt": excerpt,
            "profile_truncated": truncated,
            "sqls": sqls,
        }
        return self.core.ok(
            tool,
            [row],
            resolution=res,
            limits=limits,
            partial=limits.partial,
            file_only=PROFILE_FILE_ONLY,
            text_parts={"profile": full_text} if full_text is not None else None,
            masked=identifier_fields([detail]) | ({"sqls"} if bind_masked else set()),
        )
