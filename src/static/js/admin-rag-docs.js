/**
 * 관리자 「문서 검색 시험」 탭 (plans/126 W4 · T-3).
 *
 * 라우팅 없이 문서 엔진을 부르는 시험 화면이다. 접속 정보 **편집은 여기서 하지 않는다** —
 * `RAG_*` 키가 「환경변수 설정」 탭에 이미 있으므로 그 화면으로 보낸다(정본 이중화 금지).
 * 토큰은 서버가 마스킹해서 내려주며, 이 화면은 원문을 다루지 않는다.
 */
(function () {
    "use strict";

    var API = "/api/v1/doc";

    // admin.js와 같은 순서 — break-glass 운영자 토큰(admin_token)이 있으면 우선, 없으면 사용자
    // 토큰(user_token). 일반 로그인(/login)한 관리자 계정은 user_token만 가진다.
    var TOKEN_KEYS = ["admin_token", "user_token"];

    function authHeaders() {
        var token = "";
        for (var i = 0; i < TOKEN_KEYS.length && !token; i++) {
            token = localStorage.getItem(TOKEN_KEYS[i]) || "";
        }
        return token ? { Authorization: "Bearer " + token } : {};
    }

    function authError(status) {
        if (status === 401) return new Error("로그인이 필요합니다(인증 토큰 없음 또는 만료).");
        if (status === 403) return new Error("관리자 권한이 필요합니다.");
        return null;
    }

    function el(id) { return document.getElementById(id); }

    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function setSummary(text, kind) {
        var box = el("ragSummary");
        if (!box) return;
        var colors = {
            ok: ["var(--success-bg, #e8f5e9)", "var(--success, #2e7d32)"],
            warn: ["var(--warning-bg, #fff8e1)", "var(--warning, #ef6c00)"],
            err: ["var(--danger-bg, #fdecea)", "var(--danger, #c62828)"]
        };
        var c = colors[kind] || colors.warn;
        box.style.display = "block";
        box.style.background = c[0];
        box.style.color = c[1];
        box.innerHTML = text;
    }

    function renderCollections(payload) {
        var body = el("ragStatusBody");
        var select = el("ragCollectionSelect");
        if (!body || !select) return;
        body.innerHTML = "";
        select.innerHTML = "";

        var cols = payload.collections || [];
        var usable = cols.filter(function (c) { return c.usable; });

        cols.forEach(function (c) {
            var tr = document.createElement("tr");
            var conn = [
                "주소: " + escapeHtml(c.endpoint || "(비어 있음)"),
                "토큰/클라이언트 키: " + escapeHtml(c.token_masked || "(비어 있음)") +
                    " / " + escapeHtml(c.client_key_masked || "(비어 있음)"),
                "자산 ID: " + escapeHtml(c.retrieval_id || "(비어 있음)") +
                    (c.asset_recorded_at ? " (기준일 " + escapeHtml(c.asset_recorded_at) + ")" : "")
            ].join("<br>");
            var reason = c.disabled_reason
                ? '<div style="color: var(--warning, #ef6c00); margin-top: 6px;">' +
                  escapeHtml(c.disabled_reason) + "</div>"
                : "";
            tr.innerHTML =
                "<td><strong>" + escapeHtml(c.title) + "</strong><br>" +
                '<span style="font-size: 0.75rem; color: var(--text-muted);">' + escapeHtml(c.id) + "</span></td>" +
                "<td>" + (c.usable
                    ? '<span style="color: var(--success, #2e7d32);">사용 가능</span>'
                    : '<span style="color: var(--warning, #ef6c00);">비활성</span>') + "</td>" +
                '<td style="font-size: 0.8125rem;">' + conn + reason + "</td>";
            body.appendChild(tr);

            if (c.usable) {
                var opt = document.createElement("option");
                opt.value = c.id;
                opt.textContent = c.title;
                select.appendChild(opt);
            }
        });

        el("ragStatusTable").style.display = cols.length ? "table" : "none";
        el("ragStatusLoading").style.display = "none";

        if (!payload.enabled) {
            setSummary("문서 검색 기능이 <strong>꺼져 있습니다</strong>. 「환경변수 설정」 탭에서 " +
                       "<code>RAG_ENABLED=true</code> 로 바꾸고 <code>설정 반영</code>을 누르세요.", "warn");
        } else if (!usable.length) {
            setSummary("접속 정보가 채워진 문서군이 없습니다. 「환경변수 설정」 탭의 <code>RAG_*</code> 키 " +
                       "4개(주소·토큰·클라이언트 키·자산 ID)를 문서군마다 함께 넣으세요.", "warn");
        } else {
            setSummary("사용 가능한 문서군 " + usable.length + "개 / 전체 " + cols.length + "개.", "ok");
        }

        var runnable = usable.length > 0 && payload.enabled;
        el("ragRunBtn").disabled = !runnable;
        el("ragQuery").disabled = !runnable;
    }

    function loadCollections() {
        el("ragStatusLoading").style.display = "block";
        el("ragStatusTable").style.display = "none";
        fetch(API + "/collections", { headers: authHeaders() })
            .then(function (r) {
                var denied = authError(r.status);
                if (denied) throw denied;
                if (!r.ok) throw new Error("상태 조회 실패(HTTP " + r.status + ")");
                return r.json();
            })
            .then(renderCollections)
            .catch(function (e) {
                el("ragStatusLoading").style.display = "none";
                setSummary(escapeHtml(e.message), "err");
            });
    }

    function statusBadge(status) {
        var map = {
            ok: ["사용 가능한 답변", "var(--success, #2e7d32)"],
            empty: ["검색 결과 0건", "var(--warning, #ef6c00)"],
            stale_id: ["자산 ID 폐기 — 접속 정보 교체 필요", "var(--danger, #c62828)"],
            disabled: ["비활성 — 설정 확인", "var(--warning, #ef6c00)"],
            timeout: ["시간 초과", "var(--danger, #c62828)"],
            error: ["오류", "var(--danger, #c62828)"],
            blocked_pii: ["개인정보 필터 차단", "var(--danger, #c62828)"]
        };
        var m = map[status] || [status, "var(--text-muted)"];
        return '<span style="color: ' + m[1] + '; font-weight: 600;">' + escapeHtml(m[0]) + "</span>";
    }

    function renderResult(payload) {
        var box = el("ragResult");
        var d = payload.diagnostics || {};
        var cites = (payload.citations || []).map(function (c, i) {
            return "<li>" + escapeHtml(c.collection) + " — " + escapeHtml(c.title) +
                (c.subtitle ? " <code>" + escapeHtml(c.subtitle) + "</code>" : "") +
                (c.truncated ? ' <span style="color: var(--text-muted);">(본문 일부만)</span>' : "") +
                "</li>";
        }).join("");

        var diag = [
            "LLM 호출 " + (d.llm_calls != null ? d.llm_calls : "-") + "회",
            "검색 " + (d.search_ms != null ? d.search_ms + "ms" : "-"),
            "전체 " + (d.total_ms != null ? d.total_ms + "ms" : "-"),
            "건수 " + JSON.stringify(d.counts || {}),
            "점수 " + (d.score_min != null ? d.score_min + "~" + d.score_max : "-"),
            "HyDE " + (d.hyde_applied ? "발동" : "미발동")
        ].join(" · ");

        box.style.display = "block";
        box.innerHTML =
            '<div style="margin-bottom: 8px;">' + statusBadge(payload.status) +
            (payload.reason ? ' <span style="color: var(--text-muted); font-size: 0.8125rem;">' +
                escapeHtml(payload.reason) + "</span>" : "") + "</div>" +
            '<pre style="white-space: pre-wrap; background: var(--bg-subtle, #f7f7f8); padding: 12px; border-radius: 6px; font-size: 0.85rem;">' +
            escapeHtml(payload.answer) + "</pre>" +
            (cites ? '<div style="font-size: 0.8125rem; margin-top: 10px;"><strong>참고 문서</strong><ul>' + cites + "</ul></div>" : "") +
            '<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 10px;">' + escapeHtml(diag) + "</div>" +
            (payload.raw ? '<details style="margin-top: 10px;"><summary style="cursor: pointer; font-size: 0.8125rem;">원시 응답</summary>' +
                '<pre style="white-space: pre-wrap; font-size: 0.75rem; max-height: 320px; overflow: auto;">' +
                escapeHtml(JSON.stringify(payload.raw, null, 2)) + "</pre></details>" : "");
    }

    function runSearch() {
        var query = (el("ragQuery").value || "").trim();
        if (!query) {
            setSummary("질문을 입력하세요.", "warn");
            return;
        }
        var collection = el("ragCollectionSelect").value;
        if (!collection) {
            setSummary("사용 가능한 문서군이 없습니다.", "warn");
            return;
        }
        var btn = el("ragRunBtn");
        btn.disabled = true;
        btn.textContent = "검색 중...";
        fetch(API + "/search", {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
            body: JSON.stringify({
                collection_ids: [collection],
                query: query,
                search_only: el("ragSearchOnly").checked,
                include_raw: el("ragIncludeRaw").checked
            })
        })
            .then(function (r) {
                var denied = authError(r.status);
                if (denied) throw denied;
                return r.json().then(function (body) {
                    if (!r.ok) throw new Error(body.detail || ("실패(HTTP " + r.status + ")"));
                    return body;
                });
            })
            .then(renderResult)
            .catch(function (e) {
                el("ragResult").style.display = "block";
                el("ragResult").innerHTML = '<div style="color: var(--danger, #c62828);">' +
                    escapeHtml(e.message) + "</div>";
            })
            .finally(function () {
                btn.disabled = false;
                btn.textContent = "검색 실행";
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var refresh = el("refreshRagBtn");
        if (refresh) refresh.addEventListener("click", loadCollections);
        var run = el("ragRunBtn");
        if (run) run.addEventListener("click", runSearch);
        document.querySelectorAll('.tab[data-tab="ragdocs"]').forEach(function (tab) {
            tab.addEventListener("click", loadCollections);
        });
    });
})();
