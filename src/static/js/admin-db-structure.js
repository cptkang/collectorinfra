/**
 * 운영자 대시보드 「DB 구조」 탭 (plans/104 A-7).
 *
 * MCP 소스 목록 · 변경 점검 · 구조 분석 초안(필드별 diff·검증 4종)·승인/반려 · 버전 이력·되돌리기 ·
 * 레거시 분석본(승인 버전 없는 Redis 구조 정보) → 초안 · 신규 연동 단계(O-1~O-9) · 설명 초안 검토·적용 ·
 * DB 설명 적용 · 설정 조각 내려받기 · 잡 진행 폴링.
 *
 * - 토큰·401 처리·알림은 admin.js가 노출한 `window.AdminApi`를 그대로 쓴다(인증 게이트에서 멈췄으면 이 스크립트도 멈춘다).
 * - 보안: 서버 값은 전부 createElement/textContent로 넣는다 — HTML 문자열 주입 API를 쓰지 않는다(XSS 방지).
 * - 권한 판정은 서버(require_admin_user)가 한다. 이 화면의 버튼 활성·비활성은 안내일 뿐이다.
 */

(function () {
    "use strict";

    var api = window.AdminApi;
    var tabButton = document.querySelector('.tab[data-tab="dbstructure"]');
    var tabContent = document.getElementById("tab-dbstructure");
    if (!api || !tabButton || !tabContent) return;

    var BASE = "/api/v1/admin/db-structure";
    var POLL_INTERVAL_MS = 3000;
    var POLL_MAX_FAILURES = 5;

    var REGISTER_STEPS = ["probe", "schema", "descriptions", "db_description", "seeds", "value_index"];
    var STEP_LABELS = {
        probe: "연결 확인",
        schema: "스키마 수집·캐시 등록",
        descriptions: "컬럼 설명·유사어 초안",
        db_description: "DB 설명 초안",
        seeds: "유사어 시드 로드",
        value_index: "값 인덱스",
    };
    var LLM_STEPS = { descriptions: true, db_description: true };
    var CHECK_LABELS = {
        refs_exist: "참조 테이블·컬럼 실존",
        join_types: "조인 컬럼 타입 호환",
        sample_sql_safe: "샘플 SQL 읽기 전용·LIMIT",
        sample_exec: "샘플 실행 성공·행 존재",
    };
    var VERSION_KIND_LABELS = {
        baseline: "적용 전 원본 보관",
        external_change: "외부 변경 보관",
        approved: "승인 적용",
        rollback: "되돌리기",
    };
    var DRAFT_STATUS_LABELS = { pending: "대기", approved: "승인됨", rejected: "반려됨", applied: "적용됨", discarded: "폐기됨" };

    var state = {
        loaded: false,
        list: null,          // GET /sources 응답
        selected: null,      // 상세를 연 소스
        detail: null,        // GET /{source}
        registration: null,  // GET /{source}/registration
        jobs: {},            // job_id -> {source, label, kind, record, failures, done}
    };

    // --- DOM 헬퍼 ---

    function byId(id) { return document.getElementById(id); }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function appendAll(parent, children) {
        children.forEach(function (child) {
            if (child === null || child === undefined) return;
            parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
        });
        return parent;
    }

    function badge(text, variant, title) {
        var node = el("span", "badge" + (variant ? " badge--" + variant : ""), text);
        if (title) node.title = title;
        return node;
    }

    function pill(text, tone, title) {
        var node = el("span", "step-data-badge step-data-badge--" + tone, text);
        if (title) node.title = title;
        return node;
    }

    function button(text, className, onClick) {
        var node = el("button", "btn " + (className || "btn-secondary") + " dbs-btn", text);
        node.type = "button";
        node.addEventListener("click", onClick);
        return node;
    }

    function textInput(placeholder, value) {
        var input = document.createElement("input");
        input.type = "text";
        input.placeholder = placeholder || "";
        input.value = value || "";
        input.autocomplete = "off";
        return input;
    }

    function labeled(labelText, control) {
        var label = el("label");
        label.appendChild(document.createTextNode(labelText));
        label.appendChild(control);
        return label;
    }

    function section(id, title) {
        var node = el("div", "dbs-section");
        node.id = "dbs-sec-" + id;
        node.appendChild(el("h3", null, title));
        return node;
    }

    function muted(text) { return el("div", "dbs-muted", text); }

    function jsonText(value) {
        try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
    }

    function preBlock(value) {
        return el("pre", "dbs-pre", typeof value === "string" ? value : jsonText(value));
    }

    function collapsible(summaryText, content) {
        var node = el("details");
        node.appendChild(el("summary", null, summaryText));
        node.appendChild(content);
        return node;
    }

    function formatTime(iso) {
        if (!iso) return "-";
        var date = new Date(iso);
        if (isNaN(date.getTime())) return String(iso);
        return date.toLocaleString("ko-KR", { hour12: false });
    }

    function splitList(text) {
        return String(text || "").split(",").map(function (part) { return part.trim(); })
            .filter(function (part) { return part !== ""; });
    }

    function normEnv(value) {
        return String(value || "").trim().replace(/\/+$/, "").toLowerCase();
    }

    function shortHash(value) { return value ? String(value).slice(0, 12) : "-"; }

    function scrollToSection(id) {
        var node = byId("dbs-sec-" + id);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function downloadText(filename, text, mime) {
        var blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    // 상태 → 배지 색: 등록 단계(ok·warning·failed·draft·skipped·applied·discarded) · 잡(running·succeeded·failed·interrupted) · 초안
    function statusTone(status) {
        if (["ok", "applied", "succeeded", "approved"].indexOf(status) >= 0) return "success";
        if (["failed", "error", "interrupted", "rejected", "unknown"].indexOf(status) >= 0) return "error";
        return "info";
    }

    // --- API ---

    async function call(method, path, body) {
        var response = await api.request(method, BASE + path, body);
        var data = null;
        try { data = await response.json(); } catch (e) { data = null; }
        if (!response.ok) {
            var error = new Error(api.errorMessage(data, "요청에 실패했습니다 (HTTP " + response.status + ")"));
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function sourcePath(source) { return "/" + encodeURIComponent(source); }

    // --- 탭 진입 · 공통 버튼 ---

    tabButton.addEventListener("click", function () {
        if (state.loaded) return;  // 최초 1회만 로드 — 이후는 새로고침 버튼
        state.loaded = true;
        loadSources();
    });
    byId("dbsRefreshBtn").addEventListener("click", loadSources);
    byId("dbsDetailReloadBtn").addEventListener("click", function () {
        if (state.selected) loadDetail(state.selected);
    });
    byId("dbsDetailCloseBtn").addEventListener("click", function () {
        state.selected = null;
        state.detail = null;
        state.registration = null;
        byId("dbsDetail").style.display = "none";
    });

    // --- 목록 ---

    async function loadSources() {
        var loading = byId("dbsLoading");
        loading.classList.add("active");
        try {
            var data = await call("GET", "/sources");
            state.list = data;
            renderEnvBanner(data);
            renderSourceRows(data.sources || []);
        } catch (err) {
            api.showError("DB 소스 목록을 불러오지 못했습니다: " + err.message);
        } finally {
            loading.classList.remove("active");
        }
    }

    function renderEnvBanner(data) {
        var banner = byId("dbsMcpBanner");
        banner.textContent = "";
        banner.className = "settings-banner " + (data.mcp_available ? "settings-banner--restart" : "settings-banner--warn");
        var envLine = el("div");
        appendAll(envLine, ["실행 환경(DBHUB_SERVER_URL): ", el("code", null, data.env || "(미설정)")]);
        if (data.local_sandbox) {
            appendAll(envLine, [" ", badge("LOCAL SANDBOX", "override", "로컬 샌드박스 산출물은 운영 정본 재료로 쓰지 마세요")]);
        }
        banner.appendChild(envLine);
        if (data.provider) {
            banner.appendChild(el("div", null, "구조 분석·설명 생성 LLM: " + (data.provider.provider || "-") + " / " + (data.provider.model || "-")));
        }
        if (!data.mcp_available) {
            banner.appendChild(el("div", null, "⚠ MCP 소스 목록을 조회하지 못해 연결·불일치 칸은 확인 못 함으로 표시합니다: " + (data.mcp_error || "원인 미상")));
        }
        banner.style.display = "block";
    }

    function renderSourceRows(rows) {
        var body = byId("dbsBody");
        body.textContent = "";
        byId("dbsTable").style.display = rows.length ? "table" : "none";
        byId("dbsEmpty").style.display = rows.length ? "none" : "block";

        rows.forEach(function (row) {
            var tr = document.createElement("tr");
            tr.appendChild(cell([el("code", null, row.source), row.error ? muted(row.error) : null]));
            tr.appendChild(cell(engineCell(row)));
            tr.appendChild(cell(healthCell(row)));
            tr.appendChild(cell(registeredCell(row)));
            tr.appendChild(cell(mismatchCell(row)));
            tr.appendChild(cell(structureCell(row.structure || {})));
            tr.appendChild(cell(lastCheckCell(row.last_check)));
            tr.appendChild(cell(registrationCell(row.registration || {})));
            tr.appendChild(cell(readinessCell(row.readiness)));

            var actions = el("div", "dbs-cell-stack");
            actions.appendChild(button("상세", "btn-secondary", function () { openDetail(row.source, null); }));
            if (row.structure && row.structure.legacy_candidate) {
                actions.appendChild(button("레거시 분석본으로 초안 만들기", "btn-primary", function () { startLegacyDraft(row.source, true); }));
            }
            if (!row.active) {
                actions.appendChild(button("신규 연동", "btn-primary", function () { openDetail(row.source, "onboarding"); }));
            }
            tr.appendChild(cell([actions]));
            body.appendChild(tr);
        });
    }

    function cell(children) {
        var td = document.createElement("td");
        appendAll(td, Array.isArray(children) ? children : [children]);
        return td;
    }

    function engineCell(row) {
        var stack = el("div", "dbs-cell-stack");
        stack.appendChild(el("span", null, row.engine || "-"));
        if (row.readonly === true) stack.appendChild(badge("읽기 전용", "meta"));
        if (row.engine_mismatch) {
            stack.appendChild(badge("엔진 불일치", "override", "MCP type ≠ 레지스트리 engine(" + (row.registry_engine || "-") + ")"));
        }
        return [stack];
    }

    function healthCell(row) {
        var health = row.health;
        if (!health) return [el("span", "dbs-muted", "확인 못 함")];
        if (health.healthy) {
            var latency = health.latency_ms !== null && health.latency_ms !== undefined
                ? " · " + Math.round(health.latency_ms) + "ms" : "";
            return [pill("정상" + latency, "success")];
        }
        return [pill("실패", "error", health.error || ""), health.error ? muted(health.error) : null];
    }

    function registeredCell(row) {
        var stack = el("div", "dbs-cell-stack");
        stack.appendChild(row.registered ? badge("레지스트리 등록", "curated") : badge("미등록"));
        if (row.registered && row.registry_enabled === false) stack.appendChild(badge("레지스트리 비활성", "unconsumed"));
        stack.appendChild(row.active ? badge("활성", "immediate") : badge("비활성"));
        return [stack];
    }

    function mismatchCell(row) {
        if (row.mismatch === "mcp_only") return [badge("MCP에만 있음", "unconsumed", "레지스트리 등록 후 앱 재기동이 필요합니다")];
        if (row.mismatch === "app_only") return [badge("앱에만 있음", "override", "MCP 서버에 이 소스가 없습니다")];
        return [el("span", "dbs-muted", "-")];
    }

    function structureCell(structure) {
        var stack = el("div", "dbs-cell-stack");
        if (structure.status === "manual") {
            stack.appendChild(badge("수동 프로필", "curated"));
        } else if (structure.status === "approved") {
            stack.appendChild(badge("승인본" + (structure.latest_ver !== null && structure.latest_ver !== undefined ? " v" + structure.latest_ver : ""), "immediate"));
        } else {
            stack.appendChild(badge("없음"));
        }
        if (structure.drift) stack.appendChild(badge("파일 변경 감지", "unconsumed", "최신 버전 이후 프로필 파일이 바뀌었습니다(배포·수동 편집)"));
        if (structure.pending_drafts) stack.appendChild(badge("초안 대기 " + structure.pending_drafts, "reload"));
        if (structure.environment === "local_sandbox") stack.appendChild(badge("LOCAL SANDBOX", "override"));
        if (structure.legacy_candidate) {
            stack.appendChild(badge("레거시 분석본 — 승인 필요", "override",
                "승인 버전 없는 Redis 구조 정보(예전 질의 경로 LLM 분석)는 질의에 쓰이지 않습니다 — 초안으로 만들어 검증·승인하세요"));
        }
        if (structure.warning === "active_without_structure") stack.appendChild(badge("활성인데 구조 정보 없음", "override"));
        return [stack];
    }

    function diffSummaryText(summary) {
        if (!summary || typeof summary !== "object") return summary ? String(summary) : "-";
        var parts = [];
        function pair(label, added, removed) {
            if (added || removed) parts.push(label + " +" + (added || 0) + " −" + (removed || 0));
        }
        pair("테이블", summary.tables_added, summary.tables_removed);
        pair("컬럼", summary.columns_added, summary.columns_removed);
        [["type_changed", "타입 변경"], ["nullable_changed", "NULL 변경"], ["pk_changed", "PK 변경"], ["fk_changed", "FK 변경"], ["new_code_values", "신규 코드값"]]
            .forEach(function (entry) {
                if (summary[entry[0]]) parts.push(entry[1] + " " + summary[entry[0]]);
            });
        return parts.length ? parts.join(" · ") : "변경 없음";
    }

    function lastCheckCell(lastCheck) {
        if (!lastCheck) return [el("span", "dbs-muted", "점검 전")];
        var stack = el("div", "dbs-cell-stack");
        stack.appendChild(el("span", "dbs-muted", formatTime(lastCheck.at)));
        stack.appendChild(el("span", null, diffSummaryText(lastCheck.summary)));
        if (lastCheck.reanalysis_required) stack.appendChild(badge("재분석 필요", "override"));
        return [stack];
    }

    function registrationCell(registration) {
        var stack = el("div", "dbs-cell-stack");
        var any = false;
        REGISTER_STEPS.forEach(function (step) {
            var record = registration[step];
            if (!record) return;
            any = true;
            var count = record.count !== null && record.count !== undefined ? " " + record.count : "";
            stack.appendChild(pill(STEP_LABELS[step] + count, statusTone(record.status),
                (record.status || "") + " · " + formatTime(record.at)));
        });
        if (!any) stack.appendChild(el("span", "dbs-muted", "등록 기록 없음"));
        return [stack];
    }

    function readinessCell(readiness) {
        if (!readiness) return [el("span", "dbs-muted", "-")];
        var unmet = readiness.unmet_required || [];
        var title = unmet.map(function (item) { return item.label + (item.detail ? " — " + item.detail : ""); }).join("\n");
        return [pill(readiness.summary || "-", readiness.ready ? "success" : "error", title)];
    }

    // --- 상세 ---

    async function openDetail(source, focusSection) {
        state.selected = source;
        state.focusSection = focusSection;
        byId("dbsDetail").style.display = "block";
        byId("dbsDetailTitle").textContent = "소스 상세 — " + source;
        await loadDetail(source);
        byId("dbsDetail").scrollIntoView({ behavior: "smooth", block: "start" });
        if (focusSection) scrollToSection(focusSection);
    }

    async function loadDetail(source) {
        var loading = byId("dbsDetailLoading");
        var body = byId("dbsDetailBody");
        loading.classList.add("active");
        body.textContent = "";
        var results = await Promise.allSettled([
            call("GET", sourcePath(source)),
            call("GET", sourcePath(source) + "/registration"),
        ]);
        loading.classList.remove("active");
        if (state.selected !== source) return;  // 그사이 다른 소스를 열었다

        if (results[0].status === "rejected") {
            state.detail = null;
            body.appendChild(el("div", "dbs-unmet", "상세를 불러오지 못했습니다: " + results[0].reason.message));
            return;
        }
        state.detail = results[0].value;
        state.registration = results[1].status === "fulfilled"
            ? results[1].value
            : { _error: results[1].reason.message };
        renderDetail();
    }

    function renderDetail() {
        var body = byId("dbsDetailBody");
        body.textContent = "";
        var detail = state.detail;
        appendAll(body, [
            summarySection(detail),
            legacySection(detail),
            onboardingSection(detail, state.registration || {}),
            checkSection(detail),
            analyzeSection(detail),
            draftsSection(detail),
            versionsSection(detail),
            descriptionDraftsSection(detail, state.registration || {}),
            dbDescriptionSection(detail),
            snippetsSection(detail),
        ]);
        renderJobBanner();
    }

    function summarySection(detail) {
        var node = section("summary", "요약");
        var envLine = el("div", "dbs-cell-stack");
        appendAll(envLine, ["환경: ", el("code", null, detail.env || "-")]);
        if (detail.local_sandbox) envLine.appendChild(badge("LOCAL SANDBOX", "override"));
        if (detail.provider) envLine.appendChild(badge("LLM " + (detail.provider.provider || "-") + "/" + (detail.provider.model || "-"), "meta"));
        node.appendChild(envLine);

        var profile = detail.profile || {};
        var structure = detail.structure || {};
        var profileLine = el("div", "dbs-cell-stack");
        profileLine.appendChild(el("span", null, "구조 정보:"));
        appendAll(profileLine, structureCell(Object.assign({}, structure, { pending_drafts: 0, warning: null })));
        profileLine.appendChild(muted(profile.exists
            ? "프로필 파일 있음 · source=" + (profile.source || "-") + " · 최신 버전 " + (profile.latest_ver !== null && profile.latest_ver !== undefined ? "v" + profile.latest_ver + "(" + (VERSION_KIND_LABELS[profile.latest_kind] || profile.latest_kind || "-") + ")" : "없음")
            : "프로필 파일 없음"));
        node.appendChild(profileLine);

        var snapshot = detail.snapshot;
        node.appendChild(muted(snapshot
            ? "스냅샷: " + formatTime(snapshot.taken_at) + " · 테이블 " + (snapshot.table_count !== null && snapshot.table_count !== undefined ? snapshot.table_count : "-") + " · 해시 " + shortHash(snapshot.hash) + " · env " + (snapshot.env || "-")
            : "스냅샷 없음 — 변경 점검 또는 스키마 수집을 먼저 실행하세요."));
        return node;
    }

    // --- 레거시 분석본 (승인 버전 없는 Redis 구조 정보 → 초안) ---

    function legacySection(detail) {
        var structure = detail.structure || {};
        if (!structure.legacy_candidate) return null;
        var node = section("legacy", "레거시 분석본 — 승인 필요");
        node.appendChild(el("div", "dbs-unmet", "승인 버전 없이 Redis에만 남은 구조 정보(예전 질의 경로 LLM 분석)가 있습니다. "
            + "질의·양식 매핑은 이 값을 구조 정보로 쓰지 않습니다 — 초안으로 만들어 결정적 검증을 통과하면 승인하세요(승인하면 버전으로 남습니다)."));
        var summary = detail.legacy_meta || structure.legacy_meta || {};
        node.appendChild(muted("패턴 " + valueOr(summary.pattern_count) + "개 · query_guide " + valueOr(summary.query_guide_length)
            + "자 · 샘플 " + (summary.has_samples ? "있음(초안은 샘플을 새로 만듭니다)" : "없음")));
        var actions = el("div", "dbs-form");
        actions.appendChild(button("레거시 분석본으로 초안 만들기", "btn-primary", function () { startLegacyDraft(detail.source, false); }));
        node.appendChild(actions);
        return node;
    }

    async function startLegacyDraft(source, openFirst) {
        if (!window.confirm("레거시 분석본으로 구조 초안을 만듭니다. LLM 구조 분석은 하지 않지만, 패턴이 있으면 샘플 SQL 생성에 LLM을 1회 호출합니다.\n"
            + "과금 provider라면 승인 절차를 확인하세요. 실행할까요?")) return;
        if (openFirst) await openDetail(source, "drafts");
        startJob(source, "legacy_draft", "/legacy/draft", null, "레거시 분석본 초안");
    }

    // --- 신규 연동 단계 표시줄 (O-1~O-9) ---

    function onboardingSection(detail, registration) {
        var node = section("onboarding", "신규 연동 · 등록 단계 (O-1~O-9)");
        var steps = detail.registration || {};
        var readiness = detail.readiness || {};
        var items = readiness.items || [];

        // 미충족 필수 항목을 맨 위에 보인다(G-7 — 활성화를 막지 않고 알린다)
        var unmetRequired = items.filter(function (item) { return item.grade === "required" && item.ok === false; });
        var unmetRecommended = items.filter(function (item) { return item.grade === "recommended" && item.ok === false; });
        if (unmetRequired.length) {
            var box = el("div", "dbs-unmet");
            box.appendChild(el("strong", null, "필수 미충족 " + unmetRequired.length + "건 — " + (readiness.summary || "")));
            var list = el("ul");
            unmetRequired.forEach(function (item) {
                list.appendChild(el("li", null, item.code + " " + item.label + (item.detail ? " — " + item.detail : "")));
            });
            box.appendChild(list);
            node.appendChild(box);
        } else if (readiness.summary) {
            node.appendChild(el("div", null, "준비도: " + readiness.summary + " — 필수 항목을 모두 충족했습니다."));
        }
        if (unmetRecommended.length) {
            node.appendChild(muted("권장 미충족: " + unmetRecommended.map(function (item) {
                return item.label + (item.detail ? "(" + item.detail + ")" : "");
            }).join(" · ")));
        }
        if (registration._error) node.appendChild(muted("등록 상태를 불러오지 못했습니다: " + registration._error));

        var estimate = registration.estimate || {};
        var provider = estimate.provider || detail.provider || {};
        var providerText = typeof provider === "string" ? provider : ((provider.provider || "-") + "/" + (provider.model || "-"));
        node.appendChild(muted(
            "예상 LLM 호출 — 컬럼 설명 " + valueOr(estimate.description_calls) + "회 · 구조 분석 " + valueOr(estimate.structure_calls)
            + "회 · 설명 범위 테이블 " + valueOr(estimate.scope_tables) + " · 동시성 " + valueOr(estimate.concurrency)
            + " · provider " + providerText + (estimate.note ? " — " + estimate.note : "")
        ));

        var scopeInput = textInput("비우면 기본 범위(프로필 allowed_tables 또는 전체)");
        var form = el("div", "dbs-form");
        form.appendChild(labeled("O-3 설명 범위 테이블(쉼표 구분)", scopeInput));
        node.appendChild(form);

        function runRegister(stepNames) {
            var needsLlm = stepNames.some(function (step) { return LLM_STEPS[step]; });
            if (needsLlm && !window.confirm(
                "LLM을 호출하는 단계입니다(" + stepNames.map(function (s) { return STEP_LABELS[s]; }).join(", ") + ").\n"
                + "예상 호출: 컬럼 설명 " + valueOr(estimate.description_calls) + "회 · provider " + providerText + "\n"
                + "과금 provider라면 승인 절차를 확인하세요. 실행할까요?"
            )) return;
            var tables = splitList(scopeInput.value);
            startJob(detail.source, "register", "/register", { steps: stepNames, tables: tables.length ? tables : null },
                "등록: " + stepNames.map(function (s) { return STEP_LABELS[s]; }).join(", "));
        }

        var list = el("ul", "dbs-steps");
        list.appendChild(stepRow("O-1", "발견·연결 확인", [steps.probe], [button("실행", "btn-secondary", function () { runRegister(["probe"]); })]));
        list.appendChild(stepRow("O-2", "스키마 수집·캐시 등록", [steps.schema], [button("실행", "btn-secondary", function () { runRegister(["schema"]); })]));
        list.appendChild(stepRow("O-3", "범위 선정", [], [], "위 입력란에서 설명 대상 테이블을 정하고 예상 호출 수를 확인합니다."));
        list.appendChild(stepRow("O-4", "컬럼 설명·유사어 초안", [steps.descriptions], [
            button("초안 생성", "btn-secondary", function () { runRegister(["descriptions"]); }),
            button("초안 검토로 이동", "btn-secondary", function () { scrollToSection("descdrafts"); }),
        ]));
        list.appendChild(stepRow("O-5", "DB 설명", [steps.db_description], [
            button("초안 생성", "btn-secondary", function () { runRegister(["db_description"]); }),
            button("적용으로 이동", "btn-secondary", function () { scrollToSection("dbdesc"); }),
        ]));
        var structure = detail.structure || {};
        list.appendChild(stepRow("O-6", "구조 분석·승인", [], [
            button("구조 분석으로 이동", "btn-secondary", function () { scrollToSection("analyze"); }),
        ], structure.status === "none" ? "구조 정보 없음" : (structure.status === "manual" ? "수동 프로필" : "승인본 v" + valueOr(structure.latest_ver)),
            structure.status === "none" ? "error" : "success"));
        list.appendChild(stepRow("O-7", "부속 등록(시드·값 인덱스)", [steps.seeds, steps.value_index], [
            button("시드 로드", "btn-secondary", function () { runRegister(["seeds"]); }),
            button("값 인덱스", "btn-secondary", function () { runRegister(["value_index"]); }),
        ]));
        list.appendChild(stepRow("O-8", "설정 조각 내보내기", [], [
            button("조각으로 이동", "btn-secondary", function () { scrollToSection("snippets"); }),
        ], "레지스트리 반영은 사람이 합니다(앱은 파일을 쓰지 않음) · 반영 뒤 앱 재기동"));
        list.appendChild(stepRow("O-9", "준비도 판정·활성화", [], [], (readiness.summary || "-")
            + " · 필수 항목을 채운 뒤 「환경변수 설정」 탭에서 ACTIVE_DB_IDS에 추가하고 [설정 리로드]하세요.",
            readiness.ready ? "success" : "error"));
        node.appendChild(list);

        var serverVariables = registration.server_variables || (steps.probe && steps.probe.detail && steps.probe.detail.server_variables);
        if (serverVariables) node.appendChild(collapsible("O-1 서버 변수(방언 판단 근거)", preBlock(serverVariables)));
        return node;
    }

    function valueOr(value) { return value === null || value === undefined ? "-" : value; }

    function stepRow(code, name, records, actions, note, noteTone) {
        var li = el("li", "dbs-step");
        li.appendChild(el("span", "dbs-step-code", code));
        li.appendChild(el("span", null, name));
        var status = el("div", "dbs-cell-stack");
        var present = records.filter(Boolean);
        present.forEach(function (record) {
            var count = record.count !== null && record.count !== undefined ? " · " + record.count + "건" : "";
            status.appendChild(pill((record.status || "기록") + count, statusTone(record.status)));
            status.appendChild(el("span", "dbs-muted", formatTime(record.at) + (record.by ? " · " + record.by : "")
                + (record.provider ? " · " + (typeof record.provider === "string" ? record.provider : jsonText(record.provider)) : "")));
            if (record.detail && Object.keys(record.detail).length) {
                status.appendChild(collapsible("상세", preBlock(record.detail)));
            }
        });
        if (!present.length && records.length) status.appendChild(el("span", "dbs-muted", "미실행"));
        if (note) status.appendChild(noteTone ? pill(note, noteTone) : el("span", "dbs-muted", note));
        li.appendChild(status);
        var actionBox = el("div", "dbs-cell-stack");
        appendAll(actionBox, actions);
        li.appendChild(actionBox);
        return li;
    }

    // --- 변경 점검 ---

    function checkSection(detail) {
        var node = section("check", "변경 점검 (스키마 스냅샷 diff · LLM 0)");
        var codeInput = textInput("예: table.column, table.column");
        var form = el("div", "dbs-form");
        form.appendChild(labeled("코드성 컬럼(값 목록 수집 · 선택)", codeInput));
        form.appendChild(button("변경 점검 실행", "btn-primary", function () {
            var columns = splitList(codeInput.value);
            startJob(detail.source, "check", "/check", { code_columns: columns.length ? columns : null }, "변경 점검");
        }));
        node.appendChild(form);

        var check = detail.last_check;
        if (!check) {
            node.appendChild(muted("점검 기록이 없습니다."));
            return node;
        }
        var head = el("div", "dbs-cell-stack");
        appendAll(head, [
            el("span", null, "마지막 점검 " + formatTime(check.at)),
            el("span", "dbs-muted", "env " + (check.env || "-") + " · " + (check.by || "-") + " · 테이블 " + valueOr(check.table_count)),
            el("strong", null, diffSummaryText(check.summary)),
        ]);
        if (check.reanalysis_required) head.appendChild(badge("재분석 필요", "override", "구조 정보가 참조하는 테이블·컬럼이 바뀌었습니다"));
        if (check.diff && check.diff.baseline) head.appendChild(badge("기준선(첫 점검)", "meta"));
        node.appendChild(head);

        var diff = check.diff || {};
        var diffBox = el("div");
        [
            ["tables_added", "추가된 테이블"], ["tables_removed", "삭제된 테이블"],
            ["columns_added", "추가된 컬럼"], ["columns_removed", "삭제된 컬럼"],
            ["type_changed", "타입 변경"], ["nullable_changed", "NULL 변경"],
            ["pk_changed", "PK 변경"], ["fk_changed", "FK 변경"],
        ].forEach(function (entry) {
            var values = diff[entry[0]] || [];
            if (!values.length) return;
            var block = el("div", "diff-entry");
            var key = el("div", "diff-key");
            appendAll(key, [el("code", null, entry[1]), badge(values.length + "건", "meta")]);
            block.appendChild(key);
            values.forEach(function (value) { block.appendChild(el("div", "diff-line", diffItemText(value))); });
            diffBox.appendChild(block);
        });
        if (diffBox.childNodes.length) node.appendChild(diffBox);

        var hits = (check.impact && check.impact.hits) || [];
        if (hits.length) {
            var impact = el("div", "dbs-unmet");
            impact.appendChild(el("strong", null, "구조 정보 영향 " + hits.length + "건 — 구조 분석을 다시 실행하세요"));
            var impactList = el("ul");
            hits.forEach(function (hit) {
                impactList.appendChild(el("li", null, (hit.path || "-") + ": " + hit.table + (hit.column ? "." + hit.column : "") + " (" + (hit.change || "-") + ")"));
            });
            impact.appendChild(impactList);
            node.appendChild(impact);
        }

        var codeValues = check.code_values || [];
        if (codeValues.length) {
            var codeBox = el("div");
            codeBox.appendChild(el("strong", null, "코드값 대조"));
            codeValues.forEach(function (item) {
                var line = el("div", "dbs-cell-stack");
                appendAll(line, [el("code", null, item.key), badge(item.origin || "-", "meta")]);
                if (item.error) {
                    line.appendChild(pill("수집 실패", "error", item.error));
                    line.appendChild(muted(item.error));
                } else {
                    line.appendChild(el("span", "dbs-muted", "값 " + (item.values || []).length + "개" + (item.truncated ? "(상한 도달)" : "")));
                    if (item.baseline) line.appendChild(badge("기준선", "meta"));
                    var fresh = item.new_values || [];
                    if (fresh.length) line.appendChild(pill("신규 " + fresh.length + ": " + fresh.slice(0, 20).join(", ") + (fresh.length > 20 ? " …" : ""), "info"));
                }
                codeBox.appendChild(line);
            });
            node.appendChild(codeBox);
        }
        return node;
    }

    function diffItemText(value) {
        if (typeof value === "string") return value;
        if (!value || typeof value !== "object") return String(value);
        var name = value.table ? value.table + (value.column ? "." + value.column : "") : "";
        if (value.old !== undefined || value.new !== undefined) return name + ": " + jsonText(value.old) + " → " + jsonText(value.new);
        if (value.added || value.removed) {
            return name + ": +" + (value.added || []).join(", ") + " / −" + (value.removed || []).join(", ");
        }
        if (value.type) return name + " (" + value.type + ")";
        return name || jsonText(value);
    }

    // --- 구조 분석 ---

    function analyzeSection(detail) {
        var node = section("analyze", "구조 분석 (초안 생성)");
        var provider = detail.provider || {};
        node.appendChild(muted("실행 LLM: " + (provider.provider || "-") + " / " + (provider.model || "-")
            + " — 호출 수 = FK 묶음 수 + 샘플 SQL 생성 1회(패턴이 있을 때)."));

        var scope = document.createElement("select");
        [["all", "전체"], ["changed", "마지막 점검에서 바뀐 테이블"], ["tables", "선택 테이블"]].forEach(function (entry) {
            var option = el("option", null, entry[1]);
            option.value = entry[0];
            scope.appendChild(option);
        });
        var tablesInput = textInput("scope=선택 테이블일 때 · 쉼표 구분");
        var codeInput = textInput("예: table.column (선택)");
        var form = el("div", "dbs-form");
        appendAll(form, [
            labeled("범위", scope),
            labeled("테이블", tablesInput),
            labeled("코드성 컬럼(값 목록 → 초안 code_values)", codeInput),
            button("구조 분석 실행", "btn-primary", function () {
                var tables = splitList(tablesInput.value);
                if (scope.value === "tables" && !tables.length) {
                    api.showError("선택 테이블 범위에는 테이블을 1개 이상 입력하세요.");
                    return;
                }
                if (!window.confirm("구조 분석은 LLM(" + (provider.provider || "-") + ")을 호출합니다. 과금 provider라면 승인 절차를 확인하세요. 실행할까요?")) return;
                var columns = splitList(codeInput.value);
                startJob(detail.source, "analyze", "/analyze", {
                    scope: scope.value,
                    tables: tables.length ? tables : null,
                    code_columns: columns.length ? columns : null,
                }, "구조 분석");
            }),
        ]);
        node.appendChild(form);
        return node;
    }

    // --- 구조 초안 ---

    function draftsSection(detail) {
        var node = section("drafts", "구조 초안");
        var drafts = detail.drafts || [];
        if (!drafts.length) {
            node.appendChild(muted("초안이 없습니다 — 구조 분석을 실행하세요."));
            return node;
        }
        drafts.forEach(function (draft) { node.appendChild(draftCard(detail, draft)); });
        return node;
    }

    function draftCard(detail, draft) {
        var card = el("div", "dbs-draft");
        var head = el("div", "dbs-cell-stack");
        var provider = draft.provider || {};
        appendAll(head, [
            el("code", null, draft.draft_id),
            pill(DRAFT_STATUS_LABELS[draft.status] || draft.status || "-", statusTone(draft.status)),
            el("span", "dbs-muted", formatTime(draft.created_at) + " · " + (draft.created_by || "-")
                + " · 범위 " + (draft.scope || "-") + " · 분석 " + (draft.analysis_status || "-")
                + " · " + (provider.provider || "-") + "/" + (provider.model || "-")),
        ]);
        if (draft.local_sandbox) head.appendChild(badge("LOCAL SANDBOX", "override"));
        card.appendChild(head);

        var envMismatch = normEnv(draft.env) !== normEnv(detail.env);
        if (envMismatch) {
            card.appendChild(el("div", "dbs-unmet", "다른 환경에서 만든 초안입니다(초안 env " + (draft.env || "-") + " · 현재 env " + (detail.env || "-") + ") — 승인할 수 없습니다."));
        }
        (draft.analysis_errors || []).forEach(function (message) {
            card.appendChild(el("div", "dbs-unmet", "분석 오류: " + message));
        });

        var validation = draft.validation || {};
        var validationLine = el("div", "dbs-cell-stack");
        appendAll(validationLine, ["결정적 검증: ", pill(validation.passed ? "통과" : "실패", validation.passed ? "success" : "error")]);
        card.appendChild(validationLine);
        var checks = el("ul", "dbs-checks");
        (validation.checks || []).forEach(function (check) {
            var li = el("li");
            appendAll(li, [pill(check.passed ? "통과" : "실패", check.passed ? "success" : "error"), " " + (CHECK_LABELS[check.code] || check.code)]);
            (check.failures || []).forEach(function (failure) { li.appendChild(muted("· " + failure)); });
            checks.appendChild(li);
        });
        card.appendChild(checks);

        if (draft.comment_lines_in_current) {
            card.appendChild(el("div", "dbs-unmet", "현행 프로필의 주석 " + draft.comment_lines_in_current
                + "줄은 승인 적용 시 사라집니다(적용 직전 원문은 버전으로 보관되어 되돌리기로 복원됩니다)."));
        }

        card.appendChild(fieldDiffBlock(draft.field_diff || []));
        card.appendChild(collapsible("패턴·쿼리 가이드(초안 meta)", preBlock(draft.meta || {})));
        if (draft.samples && Object.keys(draft.samples).length || (draft.sample_attempts || []).length || draft.sample_error) {
            card.appendChild(collapsible("샘플 SQL·실행 결과", preBlock({
                samples: draft.samples, attempts: draft.sample_attempts, error: draft.sample_error,
            })));
        }
        if ((draft.code_value_results || []).length) {
            card.appendChild(collapsible("코드값 수집 결과", preBlock(draft.code_value_results)));
        }

        var actions = el("div", "dbs-form");
        if (draft.status === "pending") {
            var reasonInput = textInput("승인·반려 사유(감사 기록)");
            var approveBtn = button("승인", "btn-primary", function () { approveDraft(detail.source, draft, reasonInput.value); });
            if (!validation.passed || envMismatch) {
                approveBtn.disabled = true;
                approveBtn.title = !validation.passed ? "결정적 검증을 통과한 초안만 승인할 수 있습니다" : "현재 환경에서 만든 초안만 승인할 수 있습니다";
            }
            appendAll(actions, [
                labeled("사유", reasonInput),
                approveBtn,
                button("반려", "btn-danger", function () { rejectDraft(detail.source, draft, reasonInput.value); }),
            ]);
        } else if (draft.status === "approved") {
            actions.appendChild(muted("승인 v" + valueOr(draft.approved_ver) + " · " + (draft.approved_by || "-") + " · " + formatTime(draft.approved_at) + (draft.approve_reason ? " · " + draft.approve_reason : "")));
        } else if (draft.status === "rejected") {
            actions.appendChild(muted("반려 · " + (draft.rejected_by || "-") + " · " + formatTime(draft.rejected_at) + (draft.reject_reason ? " · " + draft.reject_reason : "")));
        }
        actions.appendChild(button("차이 보고서 내려받기", "btn-secondary", function () { downloadDiffReport(detail.source, draft.draft_id); }));
        card.appendChild(actions);
        return card;
    }

    function fieldDiffBlock(fieldDiff) {
        var box = el("div");
        box.appendChild(el("strong", null, "현행 프로필 대비 필드별 변경 " + fieldDiff.length + "건"));
        if (!fieldDiff.length) {
            box.appendChild(muted("변경 없음"));
            return box;
        }
        fieldDiff.forEach(function (change) {
            var entry = el("div", "diff-entry");
            var key = el("div", "diff-key");
            var labels = { added: "추가", removed: "삭제", changed: "변경" };
            appendAll(key, [el("code", null, change.path), badge(labels[change.change] || change.change, change.change === "removed" ? "override" : "reload")]);
            entry.appendChild(key);
            if (change.change !== "added") entry.appendChild(el("div", "diff-line diff-line--old", jsonText(change.before)));
            if (change.change !== "removed") entry.appendChild(el("div", "diff-line diff-line--new", jsonText(change.after)));
            box.appendChild(entry);
        });
        return box;
    }

    async function approveDraft(source, draft, reason) {
        if (!window.confirm("초안 " + draft.draft_id + "을 승인하면 " + source + " 프로필(config/db_profiles)에 적용되고 질의가 이 구조 정보를 씁니다. 적용 직전 원문은 버전으로 보관됩니다. 승인할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/drafts/" + encodeURIComponent(draft.draft_id) + "/approve", { reason: reason || "" });
            var ver = result.version && result.version.ver;
            api.showSuccess("승인했습니다 — v" + valueOr(ver) + " 적용" + (result.recomputed ? " (승인 시점 프로필로 병합을 다시 계산했습니다)" : "") + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("승인 실패: " + err.message);
        }
    }

    async function rejectDraft(source, draft, reason) {
        if (!window.confirm("초안 " + draft.draft_id + "을 반려할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/drafts/" + encodeURIComponent(draft.draft_id) + "/reject", { reason: reason || "" });
            api.showSuccess("반려했습니다." + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("반려 실패: " + err.message);
        }
    }

    async function downloadDiffReport(source, draftId) {
        try {
            var response = await api.request("GET", BASE + sourcePath(source) + "/diff-report?draft_id=" + encodeURIComponent(draftId));
            if (!response.ok) {
                var data = null;
                try { data = await response.json(); } catch (e) { data = null; }
                throw new Error(api.errorMessage(data, "HTTP " + response.status));
            }
            downloadText(source + "-" + draftId + "-diff.yaml", await response.text(), "text/yaml;charset=utf-8");
        } catch (err) {
            api.showError("차이 보고서를 내려받지 못했습니다: " + err.message);
        }
    }

    function auditNote(result) {
        return result && result.audit_logged === false ? " (감사 기록 불가 — 감사 저장소 미구성)" : "";
    }

    // --- 버전 이력 ---

    function versionsSection(detail) {
        var node = section("versions", "버전 이력 · 되돌리기");
        var versions = (detail.versions || []).slice().reverse();  // 최신이 위
        if (!versions.length) {
            node.appendChild(muted("버전 기록이 없습니다(승인 적용 시 직전 원문이 v0으로 보관됩니다)."));
            return node;
        }
        var wrap = el("div", "users-table-scroll");
        var table = el("table", "settings-table");
        var head = el("thead");
        var headRow = el("tr");
        ["버전", "종류", "작업자", "시각", "환경", "사유", "초안", "비고", ""].forEach(function (title) {
            headRow.appendChild(el("th", null, title));
        });
        head.appendChild(headRow);
        table.appendChild(head);
        var body = el("tbody");
        versions.forEach(function (version) {
            var tr = el("tr");
            var notes = [];
            if (version.rolled_back_from !== null && version.rolled_back_from !== undefined) notes.push("v" + version.rolled_back_from + "에서 되돌림");
            if (version.comment_lines_dropped) notes.push("주석 " + version.comment_lines_dropped + "줄 소실");
            appendAll(tr, [
                el("td", null, "v" + version.ver),
                el("td", null, VERSION_KIND_LABELS[version.kind] || version.kind || "-"),
                el("td", null, version.by || "-"),
                el("td", null, formatTime(version.created_at)),
                el("td", null, version.env || "-"),
                el("td", null, version.reason || "-"),
                el("td", null, version.draft_id || "-"),
                el("td", null, notes.join(" · ") || "-"),
            ]);
            var actionCell = el("td");
            actionCell.appendChild(button("되돌리기", "btn-secondary", function () { rollbackVersion(detail.source, version.ver); }));
            tr.appendChild(actionCell);
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        node.appendChild(wrap);
        return node;
    }

    async function rollbackVersion(source, ver) {
        var reason = window.prompt("v" + ver + " 원문으로 프로필을 되돌립니다. 사유를 입력하세요(감사 기록).", "");
        if (reason === null) return;
        try {
            var result = await call("POST", sourcePath(source) + "/versions/" + encodeURIComponent(ver) + "/rollback", { reason: reason });
            api.showSuccess("v" + ver + "로 되돌렸습니다 — 새 버전 v" + valueOr(result.version && result.version.ver) + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("되돌리기 실패: " + err.message);
        }
    }

    // --- 컬럼 설명·유사어 초안 (O-4 · G-8 (a)) ---

    function descriptionDraftsSection(detail, registration) {
        var node = section("descdrafts", "컬럼 설명·유사어 초안 (테이블 단위 검토)");
        var drafts = registration.description_drafts || [];
        if (!drafts.length) {
            node.appendChild(muted("설명 초안이 없습니다 — 등록 단계 O-4 「초안 생성」을 실행하세요."));
            return node;
        }
        drafts.forEach(function (draft) { node.appendChild(descriptionDraftCard(detail.source, draft)); });
        return node;
    }

    function descriptionDraftCard(source, draft) {
        var card = el("div", "dbs-draft");
        var provider = draft.provider || {};
        var head = el("div", "dbs-cell-stack");
        appendAll(head, [
            el("code", null, draft.draft_id),
            pill(DRAFT_STATUS_LABELS[draft.status] || draft.status || "-", statusTone(draft.status)),
            el("span", "dbs-muted", formatTime(draft.created_at) + " · " + (draft.created_by || "-") + " · env " + (draft.env || "-")
                + " · " + (typeof provider === "string" ? provider : (provider.provider || "-") + "/" + (provider.model || "-"))),
        ]);
        card.appendChild(head);
        if ((draft.failed_tables || []).length) {
            card.appendChild(el("div", "dbs-unmet", "생성 실패 테이블 " + draft.failed_tables.length + "개: " + draft.failed_tables.join(", ")));
        }

        // 초안은 테이블 단위로 저장된다: descriptions {table: {"table.column": 설명}} · synonyms {table: {"table.column": [단어]}}
        var descriptions = draft.descriptions || {};
        var synonyms = draft.synonyms || {};
        var excluded = {};
        var pending = draft.status === "pending";
        Object.keys(descriptions).sort().forEach(function (table) {
            var columns = descriptions[table] || {};
            var tableSynonyms = synonyms[table] || {};
            var block = el("div", "diff-entry");
            var key = el("div", "diff-key");
            if (pending) {
                var checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.addEventListener("change", function () { excluded[table] = checkbox.checked; });
                appendAll(key, [labeled("제외 ", checkbox)]);
            }
            appendAll(key, [el("code", null, table), badge(Object.keys(columns).length + "개 컬럼", "meta")]);
            block.appendChild(key);
            Object.keys(columns).sort().forEach(function (columnKey) {
                var words = synonymWords(tableSynonyms[columnKey]);
                var columnName = columnKey.indexOf(table + ".") === 0 ? columnKey.slice(table.length + 1) : columnKey;
                block.appendChild(el("div", "diff-line", columnName + " — " + (columns[columnKey] || "(설명 없음)")
                    + (words.length ? " · 유사어: " + words.join(", ") : "")));
            });
            card.appendChild(block);
        });
        if (draft.status === "applied") {
            card.appendChild(muted("적용 · " + (draft.applied_by || "-") + " · " + formatTime(draft.applied_at)
                + ((draft.excluded_tables || []).length ? " · 제외 " + draft.excluded_tables.join(", ") : "")));
        } else if (draft.status === "discarded") {
            card.appendChild(muted("폐기 · " + (draft.discarded_by || "-") + " · " + formatTime(draft.discarded_at)));
        }

        if (pending) {
            var actions = el("div", "dbs-form");
            appendAll(actions, [
                button("적용", "btn-primary", function () {
                    var excludeTables = Object.keys(excluded).filter(function (t) { return excluded[t]; });
                    applyDescriptionDraft(source, draft.draft_id, excludeTables);
                }),
                button("폐기", "btn-danger", function () { discardDescriptionDraft(source, draft.draft_id); }),
            ]);
            card.appendChild(actions);
        }
        return card;
    }

    function synonymWords(entry) {
        if (!entry) return [];
        if (Array.isArray(entry)) return entry.map(String);
        if (Array.isArray(entry.words)) return entry.words.map(String);
        return [];
    }

    async function applyDescriptionDraft(source, draftId, excludeTables) {
        var note = excludeTables.length ? "\n제외 테이블: " + excludeTables.join(", ") : "";
        if (!window.confirm("설명 초안 " + draftId + "을 적용합니다. 운영자·시드 유사어와 수동 설명은 보존됩니다." + note + "\n적용할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/description-drafts/" + encodeURIComponent(draftId) + "/apply",
                { exclude_tables: excludeTables.length ? excludeTables : null });
            api.showSuccess("설명 초안을 적용했습니다 — 테이블 " + (result.applied_tables || []).length + "개 · 설명 "
                + valueOr(result.description_count) + "건 · 유사어 컬럼 " + valueOr(result.synonym_columns) + "개" + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("설명 초안 적용 실패: " + err.message);
        }
    }

    async function discardDescriptionDraft(source, draftId) {
        if (!window.confirm("설명 초안 " + draftId + "을 폐기할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/description-drafts/" + encodeURIComponent(draftId) + "/discard");
            api.showSuccess("설명 초안을 폐기했습니다." + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("설명 초안 폐기 실패: " + err.message);
        }
    }

    // --- DB 설명 (O-5) ---

    function dbDescriptionSection(detail) {
        var node = section("dbdesc", "DB 상세 설명 (라우팅 설명 보강)");
        var record = (detail.registration || {}).db_description || {};
        var draftText = record.detail && record.detail.draft_text ? String(record.detail.draft_text) : "";
        node.appendChild(muted("레지스트리 description(정본·사람·git)과 별개로 Redis 상세 설명을 적용합니다. 직접 입력은 출처 manual로 저장되어 LLM 재생성이 덮지 않습니다."));
        var textarea = el("textarea", "dbs-textarea");
        textarea.value = draftText;
        textarea.placeholder = "DB 상세 설명";
        node.appendChild(textarea);
        var actions = el("div", "dbs-form");
        var adoptBtn = button("LLM 초안 그대로 적용", "btn-secondary", function () {
            setDbDescription(detail.source, draftText, "llm");
        });
        if (!draftText) {
            adoptBtn.disabled = true;
            adoptBtn.title = "O-5 「초안 생성」을 먼저 실행하세요";
        }
        appendAll(actions, [
            adoptBtn,
            button("입력한 설명 적용(수동)", "btn-primary", function () {
                if (!textarea.value.trim()) {
                    api.showError("설명을 입력하세요.");
                    return;
                }
                setDbDescription(detail.source, textarea.value, "manual");
            }),
        ]);
        node.appendChild(actions);
        return node;
    }

    async function setDbDescription(source, text, origin) {
        try {
            var result = await call("PUT", sourcePath(source) + "/db-description", { text: text, origin: origin });
            if (result.saved === false) {
                api.showError("DB 설명을 적용하지 않았습니다: " + (result.reason || "사유 미상") + auditNote(result));
            } else {
                api.showSuccess("DB 설명을 적용했습니다(출처 " + origin + ")." + auditNote(result));
            }
            refreshAfterChange(source);
        } catch (err) {
            api.showError("DB 설명 적용 실패: " + err.message);
        }
    }

    // --- 설정 조각 (O-8 · 파일 쓰기 0) ---

    function snippetsSection(detail) {
        var node = section("snippets", "설정 조각 내보내기");
        node.appendChild(muted("레지스트리 항목 조각을 내려받아 사람이 검토·반영합니다(앱은 config/db_registry.yaml·.env·MCP 설정을 쓰지 않습니다). 반영 뒤 앱 재기동과 준비도 재판정이 필요합니다."));
        var output = el("div");
        var actions = el("div", "dbs-form");
        actions.appendChild(button("조각 불러오기", "btn-secondary", async function () {
            output.textContent = "";
            try {
                var data = await call("GET", sourcePath(detail.source) + "/config-snippets");
                if (data.local_sandbox) output.appendChild(badge("LOCAL SANDBOX — 운영 정본 재료로 쓰지 말 것", "override"));
                var notes = el("ul");
                (data.notes || []).forEach(function (text) { notes.appendChild(el("li", null, text)); });
                if (notes.childNodes.length) output.appendChild(notes);
                output.appendChild(preBlock(data.registry_yaml || ""));
                output.appendChild(button("registry 조각 내려받기", "btn-primary", function () {
                    downloadText(detail.source + "-db_registry-snippet.yaml", data.registry_yaml || "", "text/yaml;charset=utf-8");
                }));
            } catch (err) {
                api.showError("설정 조각을 불러오지 못했습니다: " + err.message);
            }
        }));
        node.appendChild(actions);
        node.appendChild(output);
        return node;
    }

    // --- 잡 시작 · 진행 폴링 ---

    async function startJob(source, kind, path, body, label) {
        try {
            var job = await call("POST", sourcePath(source) + path, body);
            state.jobs[job.job_id] = { source: source, kind: kind, label: label, record: job, failures: 0, done: false };
            api.showSuccess(label + " 작업을 시작했습니다." + auditNote(job));
            renderJobBanner();
            setTimeout(function () { pollJob(job.job_id); }, POLL_INTERVAL_MS);
        } catch (err) {
            api.showError(label + " 시작 실패: " + err.message);
        }
    }

    async function pollJob(jobId) {
        var entry = state.jobs[jobId];
        if (!entry || entry.done) return;
        try {
            entry.record = await call("GET", "/jobs/" + encodeURIComponent(jobId));
            entry.failures = 0;
        } catch (err) {
            entry.failures += 1;
            if (err.status === 404 || entry.failures >= POLL_MAX_FAILURES) {
                entry.done = true;
                entry.record = Object.assign({}, entry.record, { status: "unknown", error: "진행 상태를 조회하지 못했습니다: " + err.message });
                renderJobBanner();
                return;
            }
        }
        if (entry.record.status === "running") {
            renderJobBanner();
            setTimeout(function () { pollJob(jobId); }, POLL_INTERVAL_MS);
            return;
        }
        entry.done = true;
        renderJobBanner();
        if (entry.record.status === "succeeded") {
            api.showSuccess(entry.label + " 작업이 끝났습니다.");
        } else {
            api.showError(entry.label + " 작업이 " + entry.record.status + " 상태로 끝났습니다: " + (entry.record.error || "사유 미상"));
        }
        refreshAfterChange(entry.source);
    }

    function renderJobBanner() {
        var banner = byId("dbsJobBanner");
        banner.textContent = "";
        var ids = Object.keys(state.jobs).filter(function (id) { return state.jobs[id].source === state.selected; });
        if (!ids.length) {
            banner.style.display = "none";
            return;
        }
        ids.forEach(function (id) {
            var entry = state.jobs[id];
            var record = entry.record || {};
            var line = el("div", "dbs-cell-stack");
            var progress = record.progress || {};
            appendAll(line, [
                el("strong", null, entry.label),
                pill(record.status === "running" ? "진행 중" : (record.status || "-"), record.status === "running" ? "info" : statusTone(record.status)),
                el("span", "dbs-muted", (progress.total ? progress.done + "/" + progress.total + " " : "") + (progress.label || "") + " · 시작 " + formatTime(record.started_at)),
            ]);
            if (record.error) line.appendChild(muted(record.error));
            if (entry.done) {
                var summary = jobResultSummary(entry.kind, record.result);
                if (summary) line.appendChild(el("span", null, summary));
                if (record.result) line.appendChild(collapsible("결과", preBlock(record.result)));
                line.appendChild(button("닫기", "btn-secondary", function () {
                    delete state.jobs[id];
                    renderJobBanner();
                }));
            }
            banner.appendChild(line);
        });
        banner.style.display = "block";
    }

    function jobResultSummary(kind, result) {
        if (!result) return "";
        if (kind === "check") {
            return diffSummaryText(result.summary) + (result.reanalysis_required ? " · 재분석 필요" : "");
        }
        if (kind === "analyze" || kind === "legacy_draft") {
            if (!result.draft_id) return "초안 없음 — " + (result.errors || []).join(" / ");
            return "초안 " + result.draft_id + " · 검증 " + (result.validation_passed ? "통과" : "실패")
                + " · LLM 호출 " + jsonText(result.llm_calls || {});
        }
        if (kind === "register") {
            var steps = result.steps || {};
            return Object.keys(steps).map(function (step) {
                return (STEP_LABELS[step] || step) + " " + (steps[step].status || "-");
            }).join(" · ");
        }
        return "";
    }

    function refreshAfterChange(source) {
        loadSources();
        if (state.selected === source) loadDetail(source);
    }
})();
