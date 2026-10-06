/**
 * 운영자 대시보드 「DB 구조」 탭 (plans/104 A-7).
 *
 * MCP 소스 목록 · 변경 점검 · 구조 분석 초안(필드별 diff·검증 4종)·승인/반려 · 버전 이력·되돌리기 ·
 * 레거시 분석본(승인 버전 없는 Redis 구조 정보) → 초안 · 신규 연동 단계(O-1~O-9) · DDL 스키마 등록(D-292) ·
 * 설명 초안 검토·적용 · DB 설명 적용 · 설정 조각 내려받기 · 잡 진행 폴링.
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
    var DRAFT_STATUS_LABELS = { pending: "대기", approved: "승인됨", rejected: "반려됨", applied: "적용됨", discarded: "폐기됨", partially_applied: "일부 적용" };

    var state = {
        loaded: false,
        list: null,          // GET /sources 응답
        selected: null,      // 상세를 연 소스
        detail: null,        // GET /{source}
        registration: null,  // GET /{source}/registration
        ddl: null,           // DDL 등록 입력 {source, engine, text, preview} — 상세 새로고침에도 유지
        assets: null,        // GET /{source}/assets (D-294 자산 자동 생성)
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
            call("GET", sourcePath(source) + "/assets"),
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
        state.assets = results[2].status === "fulfilled"
            ? results[2].value
            : { _error: results[2].reason.message };
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
            ddlSection(detail),
            assetSection(detail, state.assets || {}),
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
                + " · 출처 " + (snapshot.origin === "ddl" ? "DDL" : "MCP")
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
        // 정보 항목의 경고(C11 테이블 정의 등 — 활성화를 막지 않는다)
        items.filter(function (item) { return item.grade === "info" && item.ok === false; }).forEach(function (item) {
            node.appendChild(muted("경고 — " + item.code + " " + item.label + (item.detail ? ": " + item.detail : "")));
        });
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
        list.appendChild(stepRow("O-2", "스키마 수집·캐시 등록", [steps.schema], [
            button("실행", "btn-secondary", function () { runRegister(["schema"]); }),
            button("DDL로 등록", "btn-secondary", function () { scrollToSection("ddl"); }),
        ]));
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

    // --- DDL로 스키마 등록 (D-292 — 붙여넣기·.sql → 분석(미리보기) → 등록 · LLM 0 · DB 접속 0) ---

    var DDL_ENGINES = [["postgresql", "PostgreSQL"], ["db2", "DB2"], ["mariadb", "MariaDB"]];
    var DDL_ENGINE_ALIASES = { postgres: "postgresql", mysql: "mariadb" };
    var DDL_MAX_CHARS = 5000000;  // 서버 본문 상한과 같다 — 파일은 바이트로 같은 값을 넘지 않게 본다
    var DDL_TABLE_LIST_MAX = 300;  // 미리보기 테이블 목록 표시 상한(건수 요약은 전체)
    var DDL_WARNING_LIST_MAX = 50;

    function ddlState(detail) {
        if (!state.ddl || state.ddl.source !== detail.source) {
            var rows = (state.list && state.list.sources) || [];
            var row = rows.filter(function (r) { return r.source === detail.source; })[0];
            var engine = String((row && row.engine) || "").toLowerCase();
            engine = DDL_ENGINE_ALIASES[engine] || engine;
            if (!DDL_ENGINES.some(function (entry) { return entry[0] === engine; })) engine = "";
            state.ddl = { source: detail.source, engine: engine, text: "", preview: null };
        }
        return state.ddl;
    }

    // UTF-8로 읽어 깨진 글자(U+FFFD)가 있으면 EUC-KR로 다시 읽는다 — 국내 도구가 내보낸 DDL의 한글 주석
    function readDdlFile(file) {
        return new Promise(function (resolve, reject) {
            function read(encoding, fallback) {
                var reader = new FileReader();
                reader.onload = function () {
                    var text = String(reader.result || "");
                    if (fallback && text.indexOf("�") !== -1) { read(fallback, null); return; }
                    resolve(text);
                };
                reader.onerror = function () { reject(reader.error || new Error("파일을 읽지 못했습니다")); };
                reader.readAsText(file, encoding);
            }
            read("utf-8", "euc-kr");
        });
    }

    function ddlSection(detail) {
        var ddl = ddlState(detail);
        var node = section("ddl", "DDL로 스키마 등록 (붙여넣기·.sql 업로드 · LLM 0 · DB 접속 0)");
        node.appendChild(muted("O-2 스키마 수집을 MCP 대신 DDL로 합니다. DDL을 붙여 넣거나 .sql 파일을 불러와 「분석」을 누르면 해석 결과와 "
            + "현재 기준선 대비 차이를 보여 주고, 「스키마 등록」을 눌러야 스키마 캐시·변경 점검 기준선에 저장됩니다. "
            + "SQL은 실행하지 않습니다 — CREATE TABLE · ALTER TABLE(PK·FK·컬럼 추가) · COMMENT ON만 읽습니다."));

        var engineSelect = document.createElement("select");
        engineSelect.id = "dbsDdlEngine";
        var blank = el("option", null, "엔진 선택");
        blank.value = "";
        engineSelect.appendChild(blank);
        DDL_ENGINES.forEach(function (entry) {
            var option = el("option", null, entry[1]);
            option.value = entry[0];
            engineSelect.appendChild(option);
        });
        engineSelect.value = ddl.engine;
        var fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.id = "dbsDdlFile";
        fileInput.accept = ".sql";
        var form = el("div", "dbs-form");
        form.appendChild(labeled("DDL 방언(엔진)", engineSelect));
        form.appendChild(labeled(".sql 파일 불러오기", fileInput));
        node.appendChild(form);

        var textarea = el("textarea", "dbs-textarea dbs-ddl-text");
        textarea.id = "dbsDdlText";
        textarea.spellcheck = false;
        textarea.placeholder = "CREATE TABLE ... ( ... );\nALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY (...);";
        textarea.value = ddl.text;
        node.appendChild(textarea);

        var previewBox = el("div");
        previewBox.id = "dbsDdlPreview";
        var analyzeBtn = button("분석", "btn-primary", analyze);
        analyzeBtn.id = "dbsDdlAnalyzeBtn";
        var registerBtn = button("스키마 등록", "btn-primary", register);
        registerBtn.id = "dbsDdlRegisterBtn";
        var actions = el("div", "dbs-form");
        appendAll(actions, [analyzeBtn, registerBtn]);
        node.appendChild(actions);
        node.appendChild(previewBox);
        renderDdlPreview(previewBox, registerBtn, ddl.preview);

        function invalidate() {
            ddl.preview = null;
            renderDdlPreview(previewBox, registerBtn, null);
        }
        engineSelect.addEventListener("change", function () {
            ddl.engine = engineSelect.value;
            invalidate();
        });
        textarea.addEventListener("input", function () {
            ddl.text = textarea.value;
            if (ddl.preview) invalidate();
        });
        fileInput.addEventListener("change", async function () {
            var file = fileInput.files && fileInput.files[0];
            fileInput.value = "";  // 같은 파일을 다시 고를 수 있게
            if (!file) return;
            if (file.size > DDL_MAX_CHARS) {
                api.showError(".sql 파일이 너무 큽니다(최대 5MB) — 필요한 테이블 정의만 골라 붙여 넣으세요.");
                return;
            }
            try {
                textarea.value = await readDdlFile(file);
            } catch (err) {
                api.showError(".sql 파일을 읽지 못했습니다: " + err.message);
                return;
            }
            ddl.text = textarea.value;
            invalidate();
            api.showSuccess(file.name + " 파일을 불러왔습니다 — 「분석」을 누르세요.");
        });

        async function analyze() {
            if (!ddl.engine) { api.showError("DDL 방언(엔진)을 선택하세요."); return; }
            if (!ddl.text.trim()) { api.showError("DDL을 붙여 넣거나 .sql 파일을 불러오세요."); return; }
            if (ddl.text.length > DDL_MAX_CHARS) { api.showError("DDL이 너무 깁니다(최대 500만 자)."); return; }
            var engine = ddl.engine;
            var text = ddl.text;
            analyzeBtn.disabled = true;
            try {
                var result = await call("POST", sourcePath(detail.source) + "/ddl/preview", { engine: engine, text: text });
                if (ddl.engine !== engine || ddl.text !== text) return;  // 분석 중에 입력이 바뀌었다
                ddl.preview = { engine: engine, text: text, result: result };
                renderDdlPreview(previewBox, registerBtn, ddl.preview);
            } catch (err) {
                api.showError("DDL 분석 실패: " + err.message);
            } finally {
                analyzeBtn.disabled = false;
            }
        }

        function register() {
            var preview = ddl.preview;
            if (!preview) { api.showError("먼저 「분석」으로 해석 결과를 확인하세요."); return; }
            var result = preview.result;
            var diff = result.diff || {};
            var removed = (diff.tables_removed || []).length;
            if (!window.confirm("스키마 캐시와 변경 점검 기준선을 이 DDL 해석 결과(테이블 " + result.table_count + "개)로 바꿉니다.\n"
                + "현재 기준선 대비: " + (result.current_snapshot ? diffSummaryText(diff.summary) : "기준선 없음(첫 등록)") + "\n"
                + (removed ? "DDL에 없는 테이블 " + removed + "개는 캐시에서 빠지고 그 컬럼 설명·유사어가 정리됩니다.\n" : "")
                + "계속할까요?")) return;
            startJob(detail.source, "ddl_import", "/ddl/import",
                { engine: preview.engine, text: preview.text, expected_hash: result.snapshot_hash }, "DDL 스키마 등록");
            invalidate();  // 등록 뒤 기준선이 바뀌므로 이 미리보기의 차이는 더 이상 맞지 않는다
        }

        return node;
    }

    function renderDdlPreview(box, registerBtn, preview) {
        box.textContent = "";
        var result = preview && preview.result;
        registerBtn.disabled = !(result && result.table_count);
        if (!result) return;

        var summary = el("div", "dbs-cell-stack");
        appendAll(summary, [
            el("strong", null, "해석 결과"),
            pill("테이블 " + result.table_count, result.table_count ? "success" : "error"),
            el("span", null, "컬럼 " + result.column_count + " · PK 있는 테이블 " + result.primary_key_tables
                + " · 관계(FK) " + result.relationship_count + " · 문장 " + result.statement_count),
            el("span", "dbs-muted", "엔진 " + result.engine + " · 해시 " + shortHash(result.snapshot_hash)),
        ]);
        box.appendChild(summary);
        if (!result.table_count) box.appendChild(el("div", "dbs-unmet", "해석된 테이블이 없습니다 — CREATE TABLE 문장과 엔진 선택을 확인하세요."));

        var current = result.current_snapshot;
        box.appendChild(muted(current
            ? "현재 기준선(" + (current.origin === "ddl" ? "DDL" : "MCP") + " · " + formatTime(current.taken_at) + " · 테이블 "
                + valueOr(current.table_count) + ") 대비: " + diffSummaryText((result.diff || {}).summary)
            : "현재 기준선 없음 — 첫 등록입니다."));
        var diff = result.diff || {};
        if (current && ((diff.tables_added || []).length || (diff.tables_removed || []).length)) {
            box.appendChild(collapsible("추가·삭제 테이블", preBlock({ 추가: diff.tables_added || [], 삭제: diff.tables_removed || [] })));
        }

        var warnings = result.warnings || [];
        if (warnings.length) {
            var warnBox = el("div", "dbs-unmet");
            warnBox.appendChild(el("strong", null, "확인 필요 " + warnings.length + "건"));
            var list = el("ul");
            warnings.slice(0, DDL_WARNING_LIST_MAX).forEach(function (message) { list.appendChild(el("li", null, message)); });
            if (warnings.length > DDL_WARNING_LIST_MAX) list.appendChild(el("li", null, "… 외 " + (warnings.length - DDL_WARNING_LIST_MAX) + "건"));
            warnBox.appendChild(list);
            box.appendChild(warnBox);
        }
        var skipped = result.skipped || {};
        var skippedKinds = Object.keys(skipped);
        if (skippedKinds.length) {
            box.appendChild(muted("읽지 않은 문장: " + skippedKinds.map(function (kind) { return kind + " " + skipped[kind]; }).join(" · ")));
        }

        var tables = result.tables || [];
        if (!tables.length) return;
        var listNode = el("div");
        tables.slice(0, DDL_TABLE_LIST_MAX).forEach(function (table) { listNode.appendChild(ddlTableBlock(table)); });
        if (tables.length > DDL_TABLE_LIST_MAX) listNode.appendChild(muted("… 외 " + (tables.length - DDL_TABLE_LIST_MAX) + "개 테이블(등록은 전체)"));
        box.appendChild(collapsible("테이블 목록 (" + tables.length + ")", listNode));
    }

    function ddlTableBlock(table) {
        var columns = table.columns || [];
        var wrap = el("div", "users-table-scroll");
        var grid = el("table", "settings-table");
        var head = el("tr");
        ["컬럼", "타입", "NULL", "PK", "참조", "주석"].forEach(function (title) { head.appendChild(el("th", null, title)); });
        var thead = el("thead");
        thead.appendChild(head);
        grid.appendChild(thead);
        var tbody = el("tbody");
        columns.forEach(function (column) {
            var tr = el("tr");
            appendAll(tr, [
                el("td", null, column.name),
                el("td", null, column.type),
                el("td", null, column.nullable ? "Y" : "N"),
                el("td", null, column.primary_key ? "PK" : ""),
                el("td", null, column.references || ""),
                el("td", null, column.comment || ""),
            ]);
            tbody.appendChild(tr);
        });
        grid.appendChild(tbody);
        wrap.appendChild(grid);
        var title = table.name + " (" + columns.length + "열)" + (table.comment ? " — " + table.comment : "");
        return collapsible(title, wrap);
    }

    // --- 스키마 자산 자동 생성 (D-294 — 프로파일링(LLM 0) → LLM 보조(선택) → 자산별 승인) ---

    var ASSET_LABELS = {
        relationships: "테이블 관계(조인)",
        allowed_tables: "조회 대상 테이블",
        code_values: "코드값·의미",
        entity_keys: "교차 질의 식별 키",
        query_rules: "쿼리 규칙",
        query_examples: "쿼리 예시(LLM · 실행 검증)",
        seeds: "유사어 시드",
        prompt_template: "DB 전용 규칙 섹션(LLM · 검증)",
        table_definitions: "테이블 정의(테이블별 관리 정보)",
    };
    var ASSET_ORDER = Object.keys(ASSET_LABELS);
    var ASSET_FILE_LABELS = { seeds: "유사어 시드", prompt_template: "DB 전용 규칙 섹션" };

    function assetCount(kind, value) {
        if (!value) return 0;
        if (kind === "entity_keys") return (value.keys || []).length;
        if (kind === "seeds") return Object.keys(value.column_synonyms || {}).length + Object.keys(value.column_values || {}).length;
        if (kind === "prompt_template") return value.section ? 1 : 0;
        if (Array.isArray(value)) return value.length;
        return Object.keys(value).length;
    }

    function assetPreview(assets) {
        var labels = assets.code_labels || {};
        return {
            관계: (assets.relationships || []).map(function (r) { return r.from + " = " + r.to + " (" + r.origin + (r.overlap !== null && r.overlap !== undefined ? " · 겹침 " + r.overlap : "") + ")"; }),
            조회_대상: assets.allowed_tables || [],
            코드값: Object.keys(assets.code_values || {}).map(function (key) {
                var map = labels[key] || {};
                return key + ": " + (assets.code_values[key] || []).map(function (v) { return map[v] ? v + "=" + map[v] : v; }).join(", ");
            }),
            식별_키: assets.entity_keys || null,
            쿼리_규칙: assets.query_rules || [],
            유사어_시드: assets.seeds || null,
            쿼리_예시: assets.query_examples || [],
        };
    }

    function assetSection(detail, assets) {
        var node = section("assets", "스키마 자산 자동 생성 (프로필 키 · 유사어 시드 · DB 전용 규칙)");
        node.appendChild(muted("스냅샷(O-2 또는 DDL 등록)과 읽기 전용 데이터 조회로 테이블 관계·코드값·쿼리 규칙·식별 키·조회 대상·유사어 시드를 만듭니다(LLM 0). "
            + "「LLM 보조 실행」은 쿼리 예시와 DB 전용 규칙 섹션을 만들고 실제 실행으로 검증합니다. 승인한 자산만 프로필·시드·지식 파일에 적용되고 버전으로 되돌릴 수 있습니다."));
        if (assets._error) node.appendChild(muted("자산 현황을 불러오지 못했습니다: " + assets._error));

        var scopeInput = textInput("비우면 프로필 allowed_tables 또는 전체");
        var form = el("div", "dbs-form");
        form.appendChild(labeled("프로파일링 범위 테이블(쉼표 구분)", scopeInput));
        var runBtn = button("프로파일링 실행", "btn-primary", function () {
            var tables = splitList(scopeInput.value);
            startJob(detail.source, "asset_profile", "/assets/profile", { tables: tables.length ? tables : null }, "자산 프로파일링");
        });
        runBtn.id = "dbsAssetProfileBtn";
        form.appendChild(runBtn);
        node.appendChild(form);

        var definitionInfo = assets.table_definitions || {};
        node.appendChild(muted("테이블 정의 — 승인 " + valueOr(definitionInfo.approved) + "개 · 조회 대상 " + valueOr(definitionInfo.allowed)
            + "개 중 " + valueOr(definitionInfo.covered) + "개 정의. 프로파일링은 테이블 주석을 정의 초안으로 넣고, 주석 없는 테이블은 초안 카드의 「테이블 정의 LLM 초안」으로 만듭니다."));
        node.appendChild(definitionImportForm(detail, definitionInfo));

        var drafts = assets.drafts || [];
        if (!drafts.length) node.appendChild(muted("자산 초안이 없습니다 — 「프로파일링 실행」으로 만드세요."));
        drafts.forEach(function (draft) { node.appendChild(assetDraftCard(detail, draft, definitionInfo)); });
        node.appendChild(assetFilesBlock(detail, assets.files || {}));
        return node;
    }

    function assetDraftCard(detail, draft, definitionInfo) {
        var card = el("div", "dbs-draft");
        var pending = draft.status === "pending";
        var imported = draft.kind === "import";
        var head = el("div", "dbs-cell-stack");
        appendAll(head, [
            el("strong", null, "자산 초안 " + draft.draft_id),
            imported ? pill("테이블 정의 가져오기", "info") : null,
            pill(DRAFT_STATUS_LABELS[draft.status] || draft.status || "-", pending ? "info" : statusTone(draft.status)),
            el("span", "dbs-muted", formatTime(draft.created_at) + " · " + (draft.created_by || "-") + " · 엔진 " + (draft.engine || "-") + " · env " + (draft.env || "-")),
        ]);
        card.appendChild(head);

        var evidence = draft.evidence || {};
        var budget = evidence.budget || {};
        var provider = draft.provider ? (draft.provider.provider || "-") + "/" + (draft.provider.model || "-") : null;
        card.appendChild(muted((imported ? "가져오기(데이터 조회 0)"
            : "데이터 조회 " + valueOr(budget.used) + "/" + valueOr(budget.limit) + "회"
            + (budget.skipped ? " · 예산 초과로 생략 " + budget.skipped + "건" : "")
            + " · 주석 " + valueOr((evidence.catalog || {}).comments) + "건")
            + (draft.llm_calls ? " · LLM " + draft.llm_calls + "회(" + provider + ")" : " · LLM 0")));
        if (evidence.offline) card.appendChild(el("div", "dbs-unmet", "DB에 연결하지 못해 스키마만으로 만들었습니다(관계 검증·코드값·값 형식 없음): " + evidence.offline));
        if (draft.description_draft_id) card.appendChild(muted("주석으로 만든 컬럼 설명 초안 " + draft.description_draft_id + " — 아래 「컬럼 설명·유사어 초안」에서 검토·적용하세요."));
        var envMismatch = normEnv(draft.env) !== normEnv(detail.env);
        if (pending && envMismatch) card.appendChild(el("div", "dbs-unmet", "다른 환경에서 만든 초안이라 승인할 수 없습니다."));

        var assets = draft.assets || {};
        var validation = draft.validation || {};
        // 현행 프로필에 조회 대상 테이블이 이미 있으면 그 목록으로 표를 채우고 자산 체크는 끈다
        // (관리자가 고른 목록은 승인 시 그대로 덮어쓰므로 기본값이 기존 목록을 지우지 않게)
        var currentProfile = (detail.profile || {}).profile || {};
        var currentAllowed = Array.isArray(currentProfile.allowed_tables) && currentProfile.allowed_tables.length
            ? currentProfile.allowed_tables.map(function (t) { return String(t).split(".").pop().toLowerCase(); })
            : null;
        var checks = {};
        var list = el("ul", "dbs-checks");
        ASSET_ORDER.forEach(function (kind) {
            var count = assetCount(kind, assets[kind]);
            var check = validation[kind];
            var needsValidation = kind === "query_examples" || kind === "prompt_template";
            var ok = count > 0 && (!needsValidation || Boolean(check && check.passed));
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = ok && !(kind === "allowed_tables" && currentAllowed);
            cb.disabled = !pending || !ok || envMismatch;
            checks[kind] = cb;
            var li = el("li");
            var label = el("label");
            appendAll(label, [cb, " " + ASSET_LABELS[kind] + " — " + count + "건"]);
            li.appendChild(label);
            if (needsValidation && check && !check.passed) li.appendChild(muted("검증 실패: " + (check.errors || []).join(" / ")));
            if (needsValidation && !check) li.appendChild(muted("「LLM 보조 실행」 뒤에 생깁니다."));
            list.appendChild(li);
        });
        card.appendChild(list);

        var tableChecks = {};
        var tableRows = evidence.allowed_tables || [];
        if (tableRows.length) {
            var proposed = {};
            (assets.allowed_tables || []).forEach(function (t) { proposed[t] = true; });
            var wrap = el("div", "users-table-scroll");
            var grid = el("table", "settings-table");
            var headRow = el("tr");
            ["선택", "테이블", "군", "행 수", "관계", "주석"].forEach(function (title) { headRow.appendChild(el("th", null, title)); });
            var thead = el("thead");
            thead.appendChild(headRow);
            grid.appendChild(thead);
            var tbody = el("tbody");
            tableRows.forEach(function (row) {
                var cb = document.createElement("input");
                cb.type = "checkbox";
                cb.checked = currentAllowed
                    ? currentAllowed.indexOf(String(row.table).split(".").pop().toLowerCase()) >= 0
                    : Boolean(proposed[row.table]);
                cb.disabled = !pending;
                tableChecks[row.table] = cb;
                var tr = el("tr");
                var pick = el("td");
                pick.appendChild(cb);
                appendAll(tr, [pick, el("td", null, row.table), el("td", null, row.family || "-"),
                    el("td", null, valueOr(row.rows)), el("td", null, row.connected ? "있음" : "-"), el("td", null, row.comment || "")]);
                tbody.appendChild(tr);
            });
            grid.appendChild(tbody);
            wrap.appendChild(grid);
            card.appendChild(collapsible("조회 대상 테이블 선택 (" + tableRows.length + ")"
                + (currentAllowed ? " — 현행 프로필 목록으로 표시 · 승인하면 고른 목록으로 바뀝니다" : ""), wrap));
        }

        var definitions = definitionTable(draft, pending && !envMismatch, (definitionInfo || {}).kinds || []);
        if (definitions) card.appendChild(definitions.node);

        card.appendChild(collapsible("자산 내용 보기", preBlock(assetPreview(assets))));
        if (assets.prompt_template && assets.prompt_template.section) {
            card.appendChild(collapsible("DB 전용 규칙 섹션 미리보기", preBlock(assets.prompt_template.section)));
        }
        if (validation.prompt_template || validation.query_examples) {
            card.appendChild(collapsible("LLM 자산 검증 결과", preBlock(validation)));
        }
        if (!imported) card.appendChild(collapsible("근거(관계 값 겹침 · 코드 컬럼 · 값 형식 · 조회 예산)", preBlock(evidence)));

        if (pending) {
            var reasonInput = textInput("사유(감사 기록)");
            var actions = el("div", "dbs-form");
            actions.appendChild(labeled("사유", reasonInput));
            if (!imported) {
                actions.appendChild(button("LLM 보조 실행", "btn-secondary", function () {
                    var info = detail.provider || {};
                    if (!window.confirm("LLM을 2회 호출합니다(쿼리 예시 · DB 전용 규칙 섹션) · provider " + (info.provider || "-") + "/" + (info.model || "-")
                        + ".\n결과 SQL은 실제로 실행해 검증합니다(읽기 전용). 과금 provider라면 승인 절차를 확인하세요. 실행할까요?")) return;
                    startJob(detail.source, "asset_llm", "/asset-drafts/" + encodeURIComponent(draft.draft_id) + "/llm", null, "자산 LLM 보조");
                }));
            }
            var estimateNode = el("span", "dbs-muted", "예상 호출 수는 실행 전에 보여 줍니다.");
            var definitionLlmBtn = button("테이블 정의 LLM 초안", "btn-secondary", function () {
                startDefinitionLlm(detail, draft, false, estimateNode);
            });
            definitionLlmBtn.disabled = envMismatch;
            actions.appendChild(definitionLlmBtn);
            var definitionReport = (validation.table_definitions || {});
            if ((definitionReport.batches || []).some(function (b) { return b.status !== "ok"; })) {
                var retryBtn = button("실패 묶음만 재실행", "btn-secondary", function () {
                    startDefinitionLlm(detail, draft, true, estimateNode);
                });
                retryBtn.disabled = envMismatch;
                actions.appendChild(retryBtn);
            }
            if (definitions && definitions.editable) {
                actions.appendChild(button("테이블 정의 편집 저장", "btn-secondary", function () {
                    saveDefinitionEdits(detail.source, draft, definitions.inputs);
                }));
            }
            var approveBtn = button("선택 자산 승인", "btn-primary", function () {
                approveAssets(detail.source, draft, checks, tableChecks, reasonInput.value, definitions ? definitions.checks : {});
            });
            approveBtn.disabled = envMismatch;
            actions.appendChild(approveBtn);
            actions.appendChild(button("반려", "btn-danger", function () {
                rejectAssets(detail.source, draft, reasonInput.value);
            }));
            card.appendChild(actions);
            card.appendChild(estimateNode);
        } else if (draft.applied) {
            card.appendChild(muted("적용 결과: " + jsonText(draft.applied)));
        }
        return card;
    }

    async function approveAssets(source, draft, checks, tableChecks, reason, definitionChecks) {
        var include = ASSET_ORDER.filter(function (kind) { return checks[kind] && checks[kind].checked && !checks[kind].disabled; });
        if (!include.length) { api.showError("적용할 자산을 하나 이상 고르세요."); return; }
        var body = { include: include, reason: reason || "" };
        if (include.indexOf("allowed_tables") >= 0 && Object.keys(tableChecks).length) {
            body.allowed_tables = Object.keys(tableChecks).filter(function (table) { return tableChecks[table].checked; });
            if (!body.allowed_tables.length) { api.showError("조회 대상 테이블을 하나 이상 고르세요."); return; }
        }
        if (include.indexOf("table_definitions") >= 0) {
            body.table_definition_tables = Object.keys(definitionChecks || {}).filter(function (table) {
                return definitionChecks[table].checked && !definitionChecks[table].disabled;
            });
            if (!body.table_definition_tables.length) { api.showError("승인할 테이블 정의를 하나 이상 고르세요(오류 행은 고를 수 없습니다)."); return; }
        }
        if (!window.confirm("선택한 자산(" + include.map(function (k) { return ASSET_LABELS[k]; }).join(", ") + ")을 적용합니다.\n"
            + "프로필 키는 사람이 쓴 값을 보존해 병합하고, 시드·DB 전용 규칙 파일은 새 버전으로 기록합니다. 계속할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/asset-drafts/" + encodeURIComponent(draft.draft_id) + "/approve", body);
            var applied = result.applied || {};
            var unchanged = (applied.profile || {}).unchanged || [];
            var keptManual = (applied.profile || {}).table_definitions_kept_manual || [];
            if (keptManual.length) {
                api.showSuccess("사람이 고친 테이블 정의는 보존했습니다: " + keptManual.join(", "));
            }
            if (result.partial) {
                api.showError("일부 자산만 적용됐습니다 — 실패: " + Object.keys(applied).filter(function (k) { return applied[k] && applied[k].error; })
                    .map(function (k) { return (ASSET_LABELS[k] || k) + "(" + applied[k].error + ")"; }).join(", ") + auditNote(result));
            } else if (unchanged.length) {
                api.showSuccess("자산을 적용했습니다 — 기존 값을 보존해 바뀌지 않은 자산: "
                    + unchanged.map(function (k) { return ASSET_LABELS[k] || k; }).join(", ") + auditNote(result));
            } else {
                api.showSuccess("자산을 적용했습니다." + auditNote(result));
            }
            refreshAfterChange(source);
        } catch (err) {
            api.showError("자산 승인 실패: " + err.message);
        }
    }

    // --- 테이블 정의 (D-308 — 가져오기 · 주석 · LLM 묶음 초안 → 테이블 단위 검토·승인) ---

    var DEFINITION_ORIGIN_LABELS = { "import": "가져오기", llm: "LLM", comment: "주석", manual: "사람" };

    function definitionImportForm(detail, info) {
        var form = el("div", "dbs-form");
        var fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.accept = ".yaml,.yml";
        form.appendChild(labeled("테이블 정의 YAML(최상위 tables:)", fileInput));
        var importBtn = button("테이블 정의 가져오기", "btn-secondary", async function () {
            var file = fileInput.files && fileInput.files[0];
            if (!file) { api.showError("가져올 YAML 파일을 고르세요."); return; }
            var text = await file.text();
            var max = info.import_max_chars || 1000000;
            if (text.length > max) { api.showError("파일이 너무 큽니다(" + text.length + "자 · 상한 " + max + "자)."); return; }
            try {
                var result = await call("POST", sourcePath(detail.source) + "/table-definitions/import", { text: text });
                var s = result.summary || {};
                api.showSuccess("테이블 정의 초안 " + (result.draft_id || "-") + "을(를) 만들었습니다 — " + valueOr(s.total) + "행 · 유효 "
                    + valueOr(s.valid) + " · 오류 " + valueOr(s.invalid) + auditNote(result));
                refreshAfterChange(detail.source);
            } catch (err) {
                api.showError("테이블 정의 가져오기 실패: " + err.message);
            }
        });
        importBtn.id = "dbsDefinitionImportBtn";
        form.appendChild(importBtn);
        return form;
    }

    function definitionKindSelect(kinds, value) {
        var select = document.createElement("select");
        var options = [""].concat(kinds);
        if (value && options.indexOf(value) < 0) options.push(value);  // 허용 밖 값도 보이게(저장하면 검증이 거절)
        options.forEach(function (kind) {
            var option = el("option", null, kind || "(없음)");
            option.value = kind;
            select.appendChild(option);
        });
        select.value = value || "";
        return select;
    }

    // 초안의 테이블 정의 표 — 사용자 입력은 textContent·입력 값으로만 렌더한다(HTML 문자열 삽입 금지)
    function definitionTable(draft, editable, kinds) {
        var rows = (draft.assets || {}).table_definitions || {};
        var names = Object.keys(rows);
        if (!names.length) return null;
        var report = (draft.validation || {}).table_definitions || {};
        var errors = report.errors || {};
        var batches = report.batches || [];
        var failedBatches = batches.filter(function (b) { return b.status !== "ok"; }).length;
        var invalid = names.filter(function (name) { return (errors[name] || []).length > 0; }).length;
        var result = { node: null, checks: {}, inputs: {}, editable: editable };
        var wrap = el("div");
        wrap.appendChild(muted(names.length + "행 · 유효 " + (names.length - invalid) + " · 오류 " + invalid
            + (batches.length ? " · LLM 묶음 " + batches.length + "개(실패·부분 " + failedBatches + ")" : "")
            + " — 오류 행은 승인할 수 없습니다. 고쳐 저장한 행은 출처가 「사람」이 되고 다시 만들어도 보존됩니다."));
        var scroll = el("div", "users-table-scroll");
        var grid = el("table", "settings-table");
        var headRow = el("tr");
        ["선택", "영역", "테이블", "관리 정보", "대표 컬럼", "성격", "출처", "주의", "검증"].forEach(function (title) { headRow.appendChild(el("th", null, title)); });
        var thead = el("thead");
        thead.appendChild(headRow);
        grid.appendChild(thead);
        var tbody = el("tbody");
        names.forEach(function (name) {
            var row = rows[name] || {};
            var rowErrors = errors[name] || [];
            var cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = editable && !rowErrors.length;
            cb.disabled = !editable || rowErrors.length > 0;
            result.checks[name] = cb;
            var keyText = (row.key_columns || []).join(", ");
            var cells;
            if (editable) {
                var inputs = {
                    row: row,
                    manages: textInput("관리하는 정보(1~2문장)", row.manages || ""),
                    key_columns: textInput("컬럼, 컬럼", keyText),
                    kind: definitionKindSelect(kinds, row.kind || ""),
                    notes: textInput("주의(선택)", row.notes || ""),
                };
                result.inputs[name] = inputs;
                cells = [inputs.manages, inputs.key_columns, inputs.kind];
            } else {
                cells = [row.manages || "", keyText, row.kind || ""];
            }
            var tr = el("tr");
            var pick = el("td");
            pick.appendChild(cb);
            tr.appendChild(pick);
            tr.appendChild(el("td", null, row.group || "-"));
            tr.appendChild(el("td", null, name));
            cells.forEach(function (value) {
                var td = el("td");
                if (typeof value === "string") td.textContent = value; else td.appendChild(value);
                tr.appendChild(td);
            });
            tr.appendChild(el("td", null, DEFINITION_ORIGIN_LABELS[row.origin] || row.origin || "-"));
            var notesCell = el("td");
            if (editable) notesCell.appendChild(result.inputs[name].notes); else notesCell.textContent = row.notes || "";
            tr.appendChild(notesCell);
            tr.appendChild(rowErrors.length ? el("td", "dbs-unmet", rowErrors.join(" / ")) : el("td", null, "통과"));
            tbody.appendChild(tr);
        });
        grid.appendChild(tbody);
        scroll.appendChild(grid);
        wrap.appendChild(scroll);
        result.node = collapsible("테이블 정의 (" + names.length + "행" + (invalid ? " · 오류 " + invalid : "") + ")", wrap);
        return result;
    }

    async function saveDefinitionEdits(source, draft, inputs) {
        var edits = {};
        Object.keys(inputs).forEach(function (name) {
            var input = inputs[name];
            var row = input.row || {};
            var next = {
                manages: input.manages.value.trim(),
                notes: input.notes.value.trim(),
                kind: input.kind.value || null,
                key_columns: splitList(input.key_columns.value),
            };
            var changed = next.manages !== (row.manages || "") || next.notes !== (row.notes || "")
                || (next.kind || "") !== (row.kind || "") || next.key_columns.join(",") !== (row.key_columns || []).join(",");
            if (changed) edits[name] = next;
        });
        var count = Object.keys(edits).length;
        if (!count) { api.showError("바뀐 테이블 정의가 없습니다."); return; }
        try {
            var result = await call("PUT", sourcePath(source) + "/asset-drafts/" + encodeURIComponent(draft.draft_id) + "/table-definitions", { edits: edits });
            api.showSuccess("테이블 정의 " + count + "행을 저장했습니다(출처: 사람)." + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("테이블 정의 저장 실패: " + err.message);
        }
    }

    async function startDefinitionLlm(detail, draft, onlyFailed, estimateNode) {
        var path = "/asset-drafts/" + encodeURIComponent(draft.draft_id) + "/table-definitions";
        var estimate;
        try {
            estimate = await call("GET", sourcePath(detail.source) + path + "/estimate" + (onlyFailed ? "?only_failed=true" : ""));
        } catch (err) {
            api.showError("예상 호출 수 조회 실패: " + err.message);
            return;
        }
        var skipped = estimate.skipped || {};
        var text = "테이블 정의 예상 LLM 호출 " + valueOr(estimate.calls) + "회(테이블 " + valueOr(estimate.tables) + "개 · 묶음당 최대 "
            + valueOr(estimate.batch_size) + "개 · 동시성 " + valueOr(estimate.concurrency) + ")"
            + (onlyFailed ? " — 실패 묶음만" : " — 건너뜀: 주석 " + valueOr(skipped.comment) + " · 다른 출처 정의 "
                + valueOr(skipped.defined) + " · 사람 정의 " + valueOr(skipped.manual));
        estimateNode.textContent = text;
        if (!estimate.calls) { api.showError("LLM 초안을 만들 테이블이 없습니다."); return; }
        var info = estimate.provider || {};
        if (!window.confirm(text + "\nprovider " + (info.provider || "-") + "/" + (info.model || "-")
            + " · 표본 값은 보내지 않습니다. 과금 provider라면 승인 절차를 확인하세요. 실행할까요?")) return;
        startJob(detail.source, "asset_table_definitions", path + "/llm", { only_failed: onlyFailed },
            onlyFailed ? "테이블 정의 LLM 재실행(실패 묶음)" : "테이블 정의 LLM 초안");
    }

    async function rejectAssets(source, draft, reason) {
        if (!window.confirm("자산 초안 " + draft.draft_id + "을(를) 반려합니다. 계속할까요?")) return;
        try {
            var result = await call("POST", sourcePath(source) + "/asset-drafts/" + encodeURIComponent(draft.draft_id) + "/reject", { reason: reason || "" });
            api.showSuccess("자산 초안을 반려했습니다." + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("자산 초안 반려 실패: " + err.message);
        }
    }

    function assetFilesBlock(detail, files) {
        var node = el("div");
        Object.keys(ASSET_FILE_LABELS).forEach(function (kind) {
            var info = files[kind] || {};
            var versions = (info.versions || []).slice().reverse();
            var block = el("div");
            block.appendChild(muted(ASSET_FILE_LABELS[kind] + " 파일: " + (info.path || "-") + " · " + (info.exists ? "있음" : "없음")));
            if (versions.length) {
                var wrap = el("div", "users-table-scroll");
                var grid = el("table", "settings-table");
                var headRow = el("tr");
                ["버전", "종류", "작업자", "시각", "환경", "사유", ""].forEach(function (title) { headRow.appendChild(el("th", null, title)); });
                var thead = el("thead");
                thead.appendChild(headRow);
                grid.appendChild(thead);
                var tbody = el("tbody");
                versions.forEach(function (version) {
                    var tr = el("tr");
                    appendAll(tr, [el("td", null, "v" + version.ver), el("td", null, VERSION_KIND_LABELS[version.kind] || version.kind || "-"),
                        el("td", null, version.by || "-"), el("td", null, formatTime(version.created_at)), el("td", null, version.env || "-"),
                        el("td", null, version.reason || "-")]);
                    var actionCell = el("td");
                    actionCell.appendChild(button("되돌리기", "btn-secondary", function () { rollbackAsset(detail.source, kind, version.ver); }));
                    tr.appendChild(actionCell);
                    tbody.appendChild(tr);
                });
                grid.appendChild(tbody);
                wrap.appendChild(grid);
                block.appendChild(collapsible(ASSET_FILE_LABELS[kind] + " 버전 (" + versions.length + ")", wrap));
            }
            node.appendChild(block);
        });
        return node;
    }

    async function rollbackAsset(source, kind, ver) {
        var reason = window.prompt(ASSET_FILE_LABELS[kind] + " 파일을 v" + ver + " 원문으로 되돌립니다. 사유를 입력하세요(감사 기록).", "");
        if (reason === null) return;
        try {
            var result = await call("POST", sourcePath(source) + "/assets/" + encodeURIComponent(kind) + "/versions/" + ver + "/rollback", { reason: reason });
            api.showSuccess(ASSET_FILE_LABELS[kind] + "을(를) 되돌렸습니다." + auditNote(result));
            refreshAfterChange(source);
        } catch (err) {
            api.showError("되돌리기 실패: " + err.message);
        }
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
        if (kind === "asset_profile") {
            var s = result.summary || {};
            return "자산 초안 " + (result.draft_id || "-") + " · 관계 " + valueOr(s.relationships) + " · 코드 컬럼 " + valueOr(s.code_values)
                + " · 규칙 " + valueOr(s.query_rules) + " · 테이블 정의(주석) " + valueOr(s.table_definitions) + " · 조회 " + ((result.budget || {}).used || 0) + "회"
                + (result.offline ? " · DB 연결 없음(스키마만)" : "");
        }
        if (kind === "asset_table_definitions") {
            var d = result.summary || {};
            return result.note ? result.note : "테이블 정의 LLM 호출 " + valueOr(result.llm_calls) + "회 · 실패 묶음 " + valueOr(result.failed_batches)
                + " · 정의 " + valueOr(d.total) + "행(유효 " + valueOr(d.valid) + " · 오류 " + valueOr(d.invalid) + ")";
        }
        if (kind === "asset_llm") {
            return "쿼리 예시 " + valueOr(result.query_examples) + "건 · DB 전용 규칙 " + (result.prompt_template_passed ? "검증 통과" : "검증 실패")
                + " · LLM 호출 " + valueOr(result.llm_calls);
        }
        if (kind === "register" || kind === "ddl_import") {
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
