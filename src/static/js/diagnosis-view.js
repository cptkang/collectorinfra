/**
 * 「장애 진단」 뷰 탭 (plans/187 W15 · §5.6.3 ⓓ · G-34~G-40 · D-341).
 *
 * 진행 중인 조사(플레이북·HolmesGPT)를 실시간으로 보는 읽기 전용 화면이다. 구성:
 *   - 맨 앞 고정 탭 「진행 중 목록」(처음 `/active`, 이후 SSE `summary`) + 조사별 하위 탭.
 *   - 하위 탭은 같은 조사를 다시 열면 이동만 한다(중복 0). 상한·자동 닫기가 없다(G-40) — 탭 줄은
 *     가로 스크롤하고 「▾ 열린 탭 n」 메뉴가 전체 목록·「종결된 탭 모두 닫기」를 준다.
 *   - 갱신은 **EventSource 1개**다(G-39). 탭을 열고 닫으면 `watch`(조사별 마지막 seq)를 바꿔
 *     다시 연결하고, 종결된 조사는 `watch`에서 뺀다. 300초 상한·안전 상한이 없다 — 종결이 곧 멈춤이다.
 *   - 연결 상태를 머리에 보인다(● 실시간 / ◌ 재연결 중 / ⚠ 끊김 — 사유). 자동 재연결이 3회 연속
 *     실패하면 15초 커서 조회로 내려가고 그 사실을 보인다(침묵 강등 금지). 60초 무수신이면 「마지막 수신
 *     n초 전」을 덧붙인다. 401·403·404면 다시 연결하지 않는다(재연결 루프 금지 — docs/18 2026-07-14).
 *   - 실행 기록은 최신이 위, 탭당 300행 상한. AI 메모(`kind=ai_note`)는 서버가 관리자에게만 보내며
 *     화면도 운영자가 아니면 버린다(G-38 · D-035 — 결론처럼 보이지 않게 다른 배경 + 「검증 전」 배지).
 *   - 중간 결과는 `briefing-view.js` 상세형 렌더를 그대로 쓴다(새 렌더 0). 종결되면 조사를 다시 읽어
 *     상세형 전체 + [조사 대화로 열기] + 기존 [다시 분석](공용 렌더의 analyze 옵션)을 그린다.
 *
 * 보안: 화면은 읽기만 한다 — 조사 취소·명령 실행 버튼이 없다(D-003). 한 줄·발췌·AI 메모는 서버
 * 텍스트라 `textContent`로만 넣는다. `innerHTML`은 공용 렌더(`BriefingView.renderBody` — 자체
 * 이스케이프) 출력에만 쓴다.
 *
 * 전역은 `window.DiagnosisView` 하나다. 순수 함수(행 변환·병합·watch·연결 문구)와 의존성 주입형
 * 컨트롤러(`create`)를 node로 시험한다(`tests/test_api/test_ui_plan187_w15_diagnosis.py`).
 */
(function (root) {
    "use strict";

    var API = "/api/v1/alarm/investigations";
    var ROW_CAP = 300;              // 탭당 화면 행 상한(허브 버퍼 상한과 같다)
    var FAIL_LIMIT = 3;             // 자동 재연결 연속 실패 → 조회 강등
    var FALLBACK_POLL_MS = 15000;   // 강등 시 커서 조회 간격
    var IDLE_WARN_S = 60;           // 무수신 경고 기준
    var RECONNECT_MS = 3000;        // 브라우저가 재연결을 포기했을 때 직접 다시 여는 간격
    var WATCH_DEBOUNCE_MS = 150;    // 탭 여러 개를 잇달아 열 때 재연결을 1번으로 모은다
    var TICK_MS = 5000;             // 경과·무수신 표시 갱신
    var STORE_KEY = "diagnosis_open_tabs";
    var ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
    var TERMINAL = { done: 1, failed: 1, timeout: 1, rejected: 1, episode_existing: 1, none: 1 };
    // ⚠로 그리는 상태 — holmes 도구 결과의 성공 외 상태(`no_data`·`approval_required`·`frontend_pause`·`unknown`) 포함
    var WARN_STATUS = {
        error: 1, failed: 1, timeout: 1, incomplete: 1, missing: 1,
        no_data: 1, approval_required: 1, frontend_pause: 1, unknown: 1,
    };
    var SSE_TYPES = ["summary", "activity", "partial", "poll_error", "done", "denied", "ping"];

    var TEXT = {
        title: "장애 진단",
        readOnly: "읽기 전용 — 조사를 멈추거나 명령을 실행하는 버튼은 없습니다.",
        listTab: "진행 중 목록",
        listEmpty: "진행 중인 조사가 없습니다.",
        listFailed: "진행 중 목록을 불러오지 못했습니다 — 사유: {reason}",
        listHead: ["대상", "유형", "엔진", "상태", "경과", "기원"],
        ownerHead: "요청자",
        menu: "▾ 열린 탭 {n}",
        closeDone: "종결된 탭 모두 닫기",
        closeTab: "탭 닫기",
        connecting: "◌ 연결 중",
        live: "● 실시간",
        idle: " · 마지막 수신 {n}초 전",
        reconnecting: "◌ 재연결 중 {n}초",
        polling: "⚠ 실시간 연결 끊김 — 15초 간격 조회 중",
        stopped: "⚠ 끊김 — {reason}",
        stopReasons: {
            401: "인증이 만료되었습니다 — 다시 로그인하십시오",
            403: "장애 진단을 볼 권한이 없습니다",
            404: "장애 진단 기능이 꺼졌습니다",
        },
        logHead: "실행 기록",
        partialHead: "중간 결과",
        finalHead: "조사 결과",
        autoScroll: "자동 스크롤",
        aiFilter: "AI 메모",
        aiBadge: "AI 메모 — 검증 전",
        logEmpty: "아직 실행 기록이 없습니다. 조사 서비스의 실행 기록(SRE_ACTIVITY_LOG_ENABLED)이 꺼져 있으면 종결 때까지 비어 있습니다.",
        truncated: "오래된 기록 일부는 버퍼 상한으로 빠졌습니다.",
        clipped: "화면 상한 300행을 넘어 오래된 행을 접었습니다.",
        archive: "조사 서비스 기록이 만료돼 보관 요약을 보입니다(발췌 없음).",
        toolsOnly: "도구 결과 {n}건 수집 — 결론은 종결 후",
        partialEmpty: "끝난 단계가 생기면 여기에 표가 쌓입니다.",
        partialRunning: "조사가 진행 중입니다 — 끝난 단계가 실시간으로 더해집니다.",
        loading: "조사 기록을 불러오는 중…",
        loadFailed: "조사 기록을 불러오지 못했습니다 — 사유: {reason}",
        openConversation: "조사 대화로 열기",
        pollError: "갱신 실패 — {reason}",
        denied: {
            invalid: "조사 id 형식이 올바르지 않아 열 수 없습니다.",
            not_found: "조사 기록을 찾지 못했습니다.",
            forbidden: "이 조사를 볼 권한이 없습니다.",
            unavailable: "열 수 없는 조사 — 없거나 권한이 없습니다.",
        },
        queued: "⏸ 대기 순번 {n}",
        queuedNoPos: "⏸ 대기",
        running: "⟳ 진행",
        submitted: "⏸ 접수",
        progress: "진행 S{done}/{total}",
    };

    var ENGINE_LABELS = { playbook: "플레이북", holmes: "HolmesGPT", system: "시스템" };
    var ORIGIN_LABELS = { chat: "채팅", manual: "요청" };
    // sre_agent `activity_log.PHASES` 어휘와 같은 키 — 모르는 phase는 영문 대신 「진행 중」
    var PHASE_LABELS = {
        queued: "대기", collecting: "수집", narrating: "서술", deep_dive: "심층 조사", finalizing: "브리핑 조립",
    };
    var PHASE_UNKNOWN = "진행 중";

    function fill(template, values) {
        return String(template).replace(/\{(\w+)\}/g, function (_m, key) {
            var v = values[key];
            return v === undefined || v === null ? "" : String(v);
        });
    }

    function isObj(value) { return !!value && typeof value === "object" && !Array.isArray(value); }
    function asList(value) { return Array.isArray(value) ? value : []; }
    function isTerminal(status) { return !!TERMINAL[String(status || "")]; }

    function pad(n) { return n < 10 ? "0" + n : String(n); }

    function fmtClock(epochS) {
        var n = Number(epochS);
        if (!isFinite(n) || n <= 0) return "";
        var d = new Date(n * 1000);
        return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    }

    function fmtMs(ms) {
        var n = Number(ms);
        if (!isFinite(n) || n < 0) return "";
        return n < 1000 ? Math.round(n) + "ms" : (n / 1000).toFixed(1) + "s";
    }

    function fmtElapsed(seconds) {
        var n = Number(seconds);
        if (!isFinite(n) || n < 0) return "";
        n = Math.floor(n);
        if (n < 60) return n + "초";
        var m = Math.floor(n / 60);
        if (m < 60) return m + "분 " + (n % 60) + "초";
        return Math.floor(m / 60) + "시간 " + (m % 60) + "분";
    }

    function textOf(value) {
        if (value === null || value === undefined) return "";
        if (typeof value !== "object") return String(value);
        return String(value.name || value.label || value.text || value.id || "");
    }

    // ─── 순수 함수 ───────────────────────────────────────────────────────

    // 행 상태 아이콘(⟳ 진행 · ✓ 성공 · ⚠ 실패 · ↺ 캐시 · ⏸ 대기)
    function rowIcon(e) {
        var kind = String(e.kind || "");
        var status = String(e.status || "");
        if (kind === "ai_note") return "";
        if (kind === "queued" || status === "waiting") return "⏸";
        if (/_start$/.test(kind)) return "⟳";
        if (status === "cached") return "↺";
        if (WARN_STATUS[status]) return "⚠";
        return "✓";
    }

    // 시작·끝 짝 — 끝 행이 오면 같은 짝의 진행 행을 지운다(행 하나가 「진행」에서 「결과」로 바뀐다)
    function pairKey(e) {
        var m = /^(.*)_(start|end)$/.exec(String(e.kind || ""));
        if (!m) return "";
        return [e.engine || "", m[1], e.step || "", e.tool || ""].join("|");
    }

    function firstLine(text, limit) {
        var line = String(text || "").split("\n")[0];
        return line.length > limit ? line.slice(0, limit) + "…" : line;
    }

    /** 이벤트 1건 → 행 모델(서버 텍스트는 그대로 두고 화면이 textContent로 넣는다). */
    function eventRow(e) {
        var ai = e.kind === "ai_note";
        var text = String(e.label || "");
        if (ai && e.note) text = (text ? text + " — " : "") + firstLine(e.note, 160);
        var detail = ai ? String(e.note || "") : String(e.excerpt || "");
        var key = pairKey(e);
        return {
            seq: Number(e.seq) || 0,
            time: fmtClock(e.at),
            engine: String(e.engine || "system"),
            engineLabel: ENGINE_LABELS[e.engine] || String(e.engine || "시스템"),
            step: String(e.step || ""),
            icon: rowIcon(e),
            text: text,
            elapsed: e.elapsed_ms !== undefined && e.elapsed_ms !== null ? fmtMs(e.elapsed_ms) : "",
            rows: e.rows !== undefined && e.rows !== null ? String(e.rows) + "행" : "",
            detail: detail,
            ai: ai,
            warn: !!WARN_STATUS[String(e.status || "")],
            pending: /_start$/.test(String(e.kind || "")) ? key : "",
            ends: /_end$/.test(String(e.kind || "")) ? key : "",
        };
    }

    /**
     * 새 이벤트를 행 목록(최신이 위)에 합친다. 같은 seq는 한 번만, 운영자가 아니면 AI 메모를 버리고,
     * 상한을 넘으면 오래된 행을 뺀다. `state = {rows, seen, clipped, tools}`를 고쳐 쓴다.
     */
    function mergeEvents(state, events, operator, cap) {
        var limit = cap || ROW_CAP;
        var added = 0;
        asList(events).slice().sort(function (a, b) { return (a.seq || 0) - (b.seq || 0); }).forEach(function (e) {
            if (!isObj(e)) return;
            var seq = Number(e.seq) || 0;
            if (seq && state.seen[seq]) return;
            if (seq) state.seen[seq] = true;
            if (e.kind === "ai_note" && !operator) return;
            if (e.kind === "tool_end") state.tools += 1;
            var row = eventRow(e);
            if (row.ends) {
                for (var i = 0; i < state.rows.length; i++) {
                    if (state.rows[i].pending === row.ends) { state.rows.splice(i, 1); break; }
                }
            }
            state.rows.unshift(row);
            added += 1;
        });
        if (state.rows.length > limit) {
            state.rows.length = limit;
            state.clipped = true;
        }
        return added;
    }

    /** 열린 탭 중 종결·거부가 아닌 조사만 `id:seq`로 잇는다(SSE `watch`). */
    function watchParam(tabs) {
        return asList(tabs).filter(function (t) { return t && !t.done && !t.denied; })
            .map(function (t) { return t.id + ":" + (Number(t.seq) || 0); }).join(",");
    }

    function connText(conn, nowMs) {
        var state = conn && conn.state;
        if (state === "live") {
            var idle = conn.lastRecv ? Math.floor((nowMs - conn.lastRecv) / 1000) : 0;
            return TEXT.live + (idle >= IDLE_WARN_S ? fill(TEXT.idle, { n: idle }) : "");
        }
        if (state === "reconnecting") {
            return fill(TEXT.reconnecting, { n: Math.max(0, Math.floor((nowMs - (conn.since || nowMs)) / 1000)) });
        }
        if (state === "polling") return TEXT.polling;
        if (state === "stopped") return fill(TEXT.stopped, { reason: conn.reason || "" });
        return TEXT.connecting;
    }

    function statusText(item) {
        if (!item) return "";
        if (item.phase === "queued") {
            return item.queue_position ? fill(TEXT.queued, { n: item.queue_position }) : TEXT.queuedNoPos;
        }
        if (item.status === "submitted") return TEXT.submitted;
        var phase = item.phase ? (PHASE_LABELS[item.phase] || PHASE_UNKNOWN) : "";
        return TEXT.running + (phase ? " · " + phase : "");
    }

    function progressText(progress) {
        if (!isObj(progress) || !Number(progress.total)) return "";
        return fill(TEXT.progress, { done: Number(progress.done) || 0, total: Number(progress.total) }) +
            (progress.current_step ? " " + String(progress.current_step) : "");
    }

    function kindLabel(kind) {
        var bv = root.BriefingView;
        var labels = bv && bv.PB_TEXT && bv.PB_TEXT.kindLabels;
        return (labels && labels[kind]) || String(kind || "");
    }

    function originLabel(item) {
        return ORIGIN_LABELS[item.origin] || (item.kind === "manual" ? ORIGIN_LABELS.manual : "자동");
    }

    // 엔진 — 목록 항목의 `engines`를 먼저 쓰고, 없으면 진척 블록·기록에서 본 엔진으로 적는다(없으면 「—」)
    function enginesText(engines) {
        var names = [];
        if (engines.playbook) names.push(ENGINE_LABELS.playbook);
        if (engines.holmes) names.push(ENGINE_LABELS.holmes);
        return names.length ? names.join(" + ") : "—";
    }

    // ─── 컨트롤러 ───────────────────────────────────────────────────────

    /**
     * opts:
     *   root(뷰 본문 요소) · tabButton(뷰 탭 버튼) · badge(뷰 탭 배지) · operator(관리자 화면 여부)
     *   headers() — fetch 인증 헤더(EventSource는 쿠키로 인증한다)
     *   onActivate() — 장애 진단 뷰 탭으로 전환
     *   onOpenConversation(id) — 종결 화면 [조사 대화로 열기](기존 조사 대화 진입 함수)
     *   analyzeFor(id, view) — 공용 렌더 analyze 옵션(기존 [다시 분석]) 또는 null
     *   deps: {fetch, EventSource, setTimeout, clearTimeout, now, storage, document} — 시험용 주입
     */
    function create(opts) {
        var deps = opts.deps || {};
        var doc = deps.document || root.document;
        var fetchFn = deps.fetch || function (u, i) { return root.fetch(u, i); };
        var ES = deps.EventSource || root.EventSource;
        var setT = deps.setTimeout || function (f, ms) { return setTimeout(f, ms); };
        var clearT = deps.clearTimeout || function (id) { clearTimeout(id); };
        var now = deps.now || function () { return Date.now(); };
        var storage = deps.storage !== undefined ? deps.storage : safeSession();
        var operator = !!opts.operator;

        var enabled = false;
        var stopped = false;
        var polling = false;
        var es = null;
        var esCount = 0;
        var failures = 0;
        var conn = { state: "connecting" };
        var watchKey = null;
        var timers = { watch: null, reconnect: null, poll: null, tick: null };
        var items = [];
        var itemsAt = 0;
        var listError = "";
        var tabs = {};
        var order = [];
        var current = "list";
        var dom = null;
        var firstItems = null;      // 사전 게이트(/active) 응답 — 화면을 만든 뒤 그린다
        var firstError = "";

        function safeSession() {
            try { return root.sessionStorage || null; } catch (_) { return null; }
        }

        function el(tag, cls, text) {
            var node = doc.createElement(tag);
            if (cls) node.className = cls;
            if (text !== undefined && text !== null) node.textContent = String(text);
            return node;
        }

        function headers() { return opts.headers ? opts.headers() : {}; }

        function getJSON(url) {
            return Promise.resolve()
                .then(function () { return fetchFn(url, { headers: headers() }); })
                .then(function (resp) {
                    return Promise.resolve(resp.json ? resp.json() : {}).catch(function () { return {}; })
                        .then(function (body) { return { ok: !!resp.ok, status: resp.status, body: body || {} }; });
                });
        }

        function reasonOf(res) {
            var d = res && res.body && res.body.detail;
            if (d && typeof d === "object") d = d.reason || d.message || "";
            return d ? String(d) : "HTTP " + (res && res.status);
        }

        // ── 화면 골격 ──
        function build() {
            var r = opts.root;
            r.textContent = "";
            var head = el("div", "dv-head");
            head.appendChild(el("h2", null, TEXT.title));
            var connEl = el("span", "dv-conn", TEXT.connecting);
            connEl.setAttribute("role", "status");
            connEl.setAttribute("aria-live", "polite");
            head.appendChild(connEl);
            head.appendChild(el("span", "dv-readonly", TEXT.readOnly));
            r.appendChild(head);

            var bar = el("div", "dv-tabbar");
            var strip = el("div", "dv-tabs");
            strip.setAttribute("role", "tablist");
            var listBtn = el("button", "dv-tab dv-tab--list", TEXT.listTab);
            listBtn.type = "button";
            listBtn.setAttribute("role", "tab");
            listBtn.addEventListener("click", function () { select("list"); });
            strip.appendChild(listBtn);
            bar.appendChild(strip);
            var menuBtn = el("button", "dv-menu-btn", fill(TEXT.menu, { n: 0 }));
            menuBtn.type = "button";
            menuBtn.setAttribute("aria-haspopup", "true");
            menuBtn.setAttribute("aria-expanded", "false");
            var menu = el("div", "dv-menu");
            menu.hidden = true;
            menuBtn.addEventListener("click", function () { toggleMenu(); });
            bar.appendChild(menuBtn);
            bar.appendChild(menu);
            r.appendChild(bar);

            var panels = el("div", "dv-panels");
            var listPanel = el("div", "dv-panel dv-panel--list");
            var listNote = el("div", "dv-note");
            var table = el("table", "dv-list");
            var thead = el("thead");
            var hr = el("tr");
            TEXT.listHead.concat(operator ? [TEXT.ownerHead] : []).forEach(function (h) { hr.appendChild(el("th", null, h)); });
            thead.appendChild(hr);
            table.appendChild(thead);
            var tbody = el("tbody");
            table.appendChild(tbody);
            listPanel.appendChild(listNote);
            listPanel.appendChild(table);
            panels.appendChild(listPanel);
            r.appendChild(panels);

            dom = { conn: connEl, strip: strip, listBtn: listBtn, menuBtn: menuBtn, menu: menu,
                panels: panels, listPanel: listPanel, listNote: listNote, tbody: tbody };
        }

        function setVisible(on) {
            if (opts.tabButton) opts.tabButton.style.display = on ? "" : "none";
        }

        // ── 진행 중 목록 ──
        function setItems(list) {
            items = asList(list).filter(isObj);
            itemsAt = now();
            listError = "";
            items.forEach(function (it) {
                var tab = tabs[it.investigation_id];
                if (!tab || tab.done) return;
                // 배경 탭은 summary만 받는다(G-40) — 상태·경과만 옮긴다
                tab.status = it.status || tab.status;
                setState(tab, it);
                if (asList(it.alarm_ids).length && !tab.alarmId) tab.alarmId = String(it.alarm_ids[0]);
                renderTabButton(tab);
                if (current === tab.id) renderTabHead(tab);
            });
            renderList();
            renderBadge();
        }

        function renderBadge() {
            var b = opts.badge;
            if (!b) return;
            var n = enabled ? items.length : 0;
            b.textContent = n > 99 ? "99+" : String(n);
            if (b.classList) b.classList.toggle("has-unread", n > 0);
        }

        function liveElapsed(base, at) {
            var n = Number(base);
            if (!isFinite(n) || base === null || base === undefined) return "";
            return fmtElapsed(n + Math.max(0, now() - at) / 1000);
        }

        function renderList() {
            if (!dom) return;
            dom.tbody.textContent = "";
            dom.listNote.textContent = listError ? fill(TEXT.listFailed, { reason: listError })
                : (items.length ? "" : TEXT.listEmpty);
            items.forEach(function (it) {
                var tr = el("tr", "dv-list-row");
                tr.setAttribute("tabindex", "0");
                var engines = {};
                if (Array.isArray(it.engines) && it.engines.length) {
                    // 서버 `engines`(W14) 우선 — 없거나 빈 리스트일 때만 진행·열린 탭 이벤트로 채운다
                    it.engines.forEach(function (k) { engines[k] = true; });
                } else {
                    var prog = isObj(it.progress) ? it.progress : {};
                    if (prog.engine) engines[prog.engine] = true;
                    var tab = tabs[it.investigation_id];
                    if (tab) Object.keys(tab.engines).forEach(function (k) { engines[k] = true; });
                }
                var cells = [
                    textOf(it.subject) || String(it.investigation_id || ""),
                    kindLabel(it.kind),
                    enginesText(engines),
                    statusText(it) + (it.poll_error ? " · " + fill(TEXT.pollError, { reason: it.poll_error }) : ""),
                    liveElapsed(it.elapsed_s, itemsAt),
                    originLabel(it),
                ];
                if (operator) cells.push(String(it.owner_sub || ""));
                cells.forEach(function (c) { tr.appendChild(el("td", null, c)); });
                var id = String(it.investigation_id || "");
                tr.addEventListener("click", function () { open(id); });
                tr.addEventListener("keydown", function (e) { if (e && e.key === "Enter") open(id); });
                dom.tbody.appendChild(tr);
            });
        }

        // ── 하위 탭 ──
        function newTab(id) {
            return {
                id: id, seq: 0, status: "", view: null, done: false, denied: "", error: "",
                phase: null, queue: null, elapsed: null, elapsedAt: 0, progress: null, pollError: "",
                partial: null, truncated: false, source: "", engines: {}, alarmId: null,
                log: { rows: [], seen: {}, clipped: false, tools: 0 },
                filter: { playbook: true, holmes: true, system: true, ai: true },
                autoScroll: true, expanded: {}, finalPanel: null, dom: null,
            };
        }

        function tabLabel(tab) {
            var v = tab.view || {};
            return textOf(v.subject) || tab.subject || tab.id;
        }

        function tabState(tab) {
            if (tab.denied || tab.error) return "⚠";
            if (tab.done) return WARN_STATUS[tab.status] ? "⚠" : "✓";
            if (tab.phase === "queued" || tab.status === "submitted") return "⏸";
            return "⟳";
        }

        function buildTab(tab) {
            var wrap = el("span", "dv-subtab");
            var btn = el("button", "dv-tab");
            btn.type = "button";
            btn.setAttribute("role", "tab");
            var label = el("span", "dv-tab-label");
            var state = el("span", "dv-tab-state");
            btn.appendChild(label);
            btn.appendChild(state);
            btn.addEventListener("click", function () { select(tab.id); });
            var close = el("button", "dv-tab-close", "×");
            close.type = "button";
            close.setAttribute("aria-label", TEXT.closeTab);
            close.title = TEXT.closeTab;
            close.addEventListener("click", function () { closeTab(tab.id); });
            wrap.appendChild(btn);
            wrap.appendChild(close);
            dom.strip.appendChild(wrap);

            var panel = el("div", "dv-panel dv-panel--tab");
            panel.hidden = true;
            var head = el("div", "dv-tab-head");
            var title = el("div", "dv-tab-title");
            var meta = el("div", "dv-tab-meta");
            var warn = el("div", "dv-note dv-note--warn");
            warn.hidden = true;
            head.appendChild(title);
            head.appendChild(meta);
            head.appendChild(warn);
            panel.appendChild(head);

            var grid = el("div", "dv-grid");
            var logBox = el("section", "dv-log");
            var logBar = el("div", "dv-log-bar");
            logBar.appendChild(el("span", "dv-label", TEXT.logHead));
            var filters = el("span", "dv-filters");
            logBar.appendChild(filters);
            var autoLabel = el("label", "dv-check");
            var auto = el("input");
            auto.type = "checkbox";
            auto.checked = true;
            auto.addEventListener("change", function () { tab.autoScroll = !!auto.checked; });
            autoLabel.appendChild(auto);
            autoLabel.appendChild(el("span", null, TEXT.autoScroll));
            logBar.appendChild(autoLabel);
            logBox.appendChild(logBar);
            var logNote = el("div", "dv-note");
            logBox.appendChild(logNote);
            var rows = el("ul", "dv-rows");
            logBox.appendChild(rows);
            grid.appendChild(logBox);
            var partBox = el("section", "dv-partial");
            partBox.appendChild(el("div", "dv-label", TEXT.partialHead));
            var partBody = el("div", "dv-partial-body", TEXT.loading);
            partBox.appendChild(partBody);
            grid.appendChild(partBox);
            panel.appendChild(grid);

            var finalBox = el("section", "dv-final");
            finalBox.hidden = true;
            finalBox.appendChild(el("div", "dv-label", TEXT.finalHead));
            var finalBody = el("div", "dv-final-body");
            finalBox.appendChild(finalBody);
            var actions = el("div", "dv-final-actions");
            var conv = el("button", "bv-open-conv dv-open-conv", TEXT.openConversation);
            conv.type = "button";
            conv.addEventListener("click", function () { if (opts.onOpenConversation) opts.onOpenConversation(tab.id); });
            actions.appendChild(conv);
            finalBox.appendChild(actions);
            panel.appendChild(finalBox);
            dom.panels.appendChild(panel);

            tab.dom = { wrap: wrap, btn: btn, label: label, state: state, panel: panel, title: title, meta: meta,
                warn: warn, filters: filters, logNote: logNote, rows: rows, partBox: partBox, partBody: partBody,
                finalBox: finalBox, finalBody: finalBody };
            renderTabButton(tab);
        }

        function renderTabButton(tab) {
            if (!tab.dom) return;
            tab.dom.label.textContent = tabLabel(tab);
            var el2 = tab.done ? "" : liveElapsed(tab.elapsed, tab.elapsedAt);
            tab.dom.state.textContent = " " + tabState(tab) + (el2 ? " " + el2 : "");
            tab.dom.wrap.className = "dv-subtab" + (tab.done ? " dv-subtab--done" : "") +
                (current === tab.id ? " active" : "");
        }

        function renderTabHead(tab) {
            if (!tab.dom) return;
            var v = tab.view || {};
            tab.dom.title.textContent = tabLabel(tab);
            var parts = [kindLabel(v.kind || tab.kind || ""), enginesText(tab.engines)];
            var prog = progressText(tab.progress);
            if (prog) parts.push(prog);
            var bv = root.BriefingView;
            if (tab.done) parts.push(bv ? bv.statusChip(v.status ? v : { status: tab.status }).text : tab.status);
            else parts.push(statusText({ phase: tab.phase, queue_position: tab.queue, status: tab.status }));
            var elapsed = liveElapsed(tab.elapsed, tab.elapsedAt);
            if (elapsed) parts.push(elapsed);
            if (!tab.done && !tab.denied) parts.push(connText(conn, now()));
            tab.dom.meta.textContent = parts.filter(Boolean).join(" · ");
            var warn = tab.denied || tab.error || (tab.pollError ? fill(TEXT.pollError, { reason: tab.pollError }) : "");
            tab.dom.warn.textContent = warn;
            tab.dom.warn.hidden = !warn;
        }

        function renderFilters(tab) {
            var f = tab.dom.filters;
            f.textContent = "";
            var parallel = tab.engines.playbook && tab.engines.holmes;
            var keys = parallel ? ["playbook", "holmes"] : [];
            var hasAi = operator && tab.log.rows.some(function (r) { return r.ai; });
            if (hasAi) keys.push("ai");
            keys.forEach(function (key) {
                var label = el("label", "dv-check");
                var box = el("input");
                box.type = "checkbox";
                box.checked = tab.filter[key] !== false;
                box.addEventListener("change", function () { tab.filter[key] = !!box.checked; renderRows(tab); });
                label.appendChild(box);
                label.appendChild(el("span", null, key === "ai" ? TEXT.aiFilter : ENGINE_LABELS[key]));
                f.appendChild(label);
            });
        }

        function rowNode(tab, row) {
            var li = el("li", "dv-row" + (row.ai ? " dv-row--ai" : "") + (row.warn ? " dv-row--warn" : ""));
            li.appendChild(el("span", "dv-time", row.time));
            li.appendChild(el("span", "dv-engine dv-engine--" + row.engine.replace(/[^a-z]/g, ""), row.engineLabel));
            if (row.step) li.appendChild(el("span", "dv-step", row.step));
            if (row.ai) li.appendChild(el("span", "dv-ai-badge", TEXT.aiBadge));
            else li.appendChild(el("span", "dv-icon", row.icon));
            li.appendChild(el("span", "dv-text", row.text));
            if (row.elapsed) li.appendChild(el("span", "dv-meta", row.elapsed));
            if (row.rows) li.appendChild(el("span", "dv-meta", row.rows));
            if (row.detail) {
                var open = !!tab.expanded[row.seq];
                var toggle = el("button", "dv-expand", open ? "▾" : "▸");
                toggle.type = "button";
                toggle.setAttribute("aria-expanded", open ? "true" : "false");
                var pre = el("pre", "dv-excerpt", row.detail);
                pre.hidden = !open;
                toggle.addEventListener("click", function () {
                    var on = !tab.expanded[row.seq];
                    tab.expanded[row.seq] = on;
                    pre.hidden = !on;
                    toggle.textContent = on ? "▾" : "▸";
                    toggle.setAttribute("aria-expanded", on ? "true" : "false");
                });
                li.appendChild(toggle);
                li.appendChild(pre);
            }
            return li;
        }

        function renderRows(tab) {
            if (!tab.dom) return;
            var list = tab.dom.rows;
            var keep = list.scrollTop;
            list.textContent = "";
            var shown = tab.log.rows.filter(function (r) {
                if (r.ai) return operator && tab.filter.ai !== false;
                return tab.filter[r.engine] !== false;
            });
            shown.forEach(function (r) { list.appendChild(rowNode(tab, r)); });
            var notes = [];
            if (!tab.log.rows.length) notes.push(TEXT.logEmpty);
            if (tab.truncated) notes.push(TEXT.truncated);
            if (tab.log.clipped) notes.push(TEXT.clipped);
            if (tab.source === "archive") notes.push(TEXT.archive);
            tab.dom.logNote.textContent = notes.join(" ");
            list.scrollTop = tab.autoScroll ? 0 : keep;   // 최신이 위
            renderFilters(tab);
        }

        function renderPartial(tab) {
            if (!tab.dom || tab.done) return;
            var bv = root.BriefingView;
            var body = tab.dom.partBody;
            if (bv && bv.hasPlaybook({ partial_briefing: tab.partial })) {
                // 공용 상세형 렌더(자체 이스케이프) — 끝난 단계부터 그린다(새 렌더 0)
                body.innerHTML = bv.renderBody({
                    status: tab.status || "running", investigation_id: tab.id,
                    partial_briefing: tab.partial, progress: tab.progress,
                }, { mode: "detail", runningText: TEXT.partialRunning });
                return;
            }
            body.textContent = tab.engines.playbook ? TEXT.partialEmpty : fill(TEXT.toolsOnly, { n: tab.log.tools });
        }

        function renderFinal(tab, view) {
            if (!tab.dom || tab.finalPanel) return;
            var bv = root.BriefingView;
            tab.dom.partBox.hidden = true;
            tab.dom.finalBox.hidden = false;
            if (!bv) { tab.dom.finalBody.textContent = String(view.status || ""); return; }
            tab.finalPanel = bv.attach(tab.dom.finalBody, {
                initial: view,
                load: function () { return loadView(tab.id, null); },
                render: { mode: "detail" },
                onOpenConversation: opts.onOpenConversation,
                analyze: opts.analyzeFor ? opts.analyzeFor(tab.id, view, tab.alarmId) : null,
            });
            tab.finalPanel.open();
        }

        function select(id) {
            if (id !== "list" && !tabs[id]) id = "list";
            current = id;
            if (!dom) return;
            dom.listPanel.hidden = id !== "list";
            dom.listBtn.className = "dv-tab dv-tab--list" + (id === "list" ? " active" : "");
            dom.listBtn.setAttribute("aria-selected", id === "list" ? "true" : "false");
            order.forEach(function (tid) {
                var t = tabs[tid];
                t.dom.panel.hidden = tid !== id;
                t.dom.btn.setAttribute("aria-selected", tid === id ? "true" : "false");
                renderTabButton(t);
            });
            if (tabs[id]) {
                renderTabHead(tabs[id]);
                if (tabs[id].dom.wrap.scrollIntoView) tabs[id].dom.wrap.scrollIntoView({ block: "nearest", inline: "nearest" });
            }
            persist();
        }

        function renderMenu() {
            if (!dom) return;
            dom.menuBtn.textContent = fill(TEXT.menu, { n: order.length });
            dom.menu.textContent = "";
            order.forEach(function (tid) {
                var t = tabs[tid];
                var b = el("button", "dv-menu-item", tabState(t) + " " + tabLabel(t));
                b.type = "button";
                b.addEventListener("click", function () { toggleMenu(false); select(tid); });
                dom.menu.appendChild(b);
            });
            var closeDone = el("button", "dv-menu-item dv-menu-close", TEXT.closeDone);
            closeDone.type = "button";
            closeDone.addEventListener("click", function () { toggleMenu(false); closeFinished(); });
            dom.menu.appendChild(closeDone);
        }

        function toggleMenu(force) {
            if (!dom) return;
            var show = force === undefined ? dom.menu.hidden : !!force;
            if (show) renderMenu();
            dom.menu.hidden = !show;
            dom.menuBtn.setAttribute("aria-expanded", show ? "true" : "false");
        }

        function persist() {
            if (!storage) return;
            try {
                storage.setItem(STORE_KEY, JSON.stringify({ tabs: order.slice(), current: current }));
            } catch (_) { /* 저장 불가(사생활 모드 등) — 복원만 못 한다 */ }
        }

        function restore() {
            if (!storage) return null;
            try {
                var saved = JSON.parse(storage.getItem(STORE_KEY) || "null");
                return isObj(saved) ? saved : null;
            } catch (_) { return null; }
        }

        // ── 조사 상태 반영 ──
        function setState(tab, src) {
            if ("phase" in src) tab.phase = src.phase;
            if ("queue_position" in src) tab.queue = src.queue_position;
            if (src.elapsed_s !== undefined && src.elapsed_s !== null) { tab.elapsed = src.elapsed_s; tab.elapsedAt = now(); }
            if (isObj(src.progress)) {
                tab.progress = src.progress;
                if (src.progress.engine) tab.engines[src.progress.engine] = true;
            }
            if ("poll_error" in src) tab.pollError = src.poll_error || "";
        }

        function applyActivity(tab, act) {
            if (!isObj(act)) return;
            asList(act.events).forEach(function (e) { if (isObj(e) && (e.engine === "playbook" || e.engine === "holmes")) tab.engines[e.engine] = true; });
            mergeEvents(tab.log, act.events, operator, ROW_CAP);
            var next = Number(act.next_seq);
            if (isFinite(next) && next > tab.seq) tab.seq = next;
            if ("truncated" in act) tab.truncated = !!act.truncated;
            if (act.source) tab.source = act.source;
        }

        function applyView(tab, view) {
            if (!isObj(view)) return;
            tab.view = view;
            // SSE `done`을 먼저 받았으면 조회 응답이 늦게 따라와도 종결 상태를 되돌리지 않는다
            if (!(tab.done && !isTerminal(view.status))) tab.status = view.status || tab.status;
            tab.kind = view.kind || tab.kind;
            if (!tab.alarmId && view.parent_alarm_id) tab.alarmId = String(view.parent_alarm_id);
            if (isObj(view.activity)) applyActivity(tab, view.activity);
            setState(tab, view);
            if (isObj(view.partial_briefing)) tab.partial = view.partial_briefing;
            if (isObj(view.briefing) && isObj(view.briefing.playbook)) tab.engines.playbook = true;
            renderRows(tab);
            if (tab.done || isTerminal(tab.status)) {
                var wasDone = tab.done;
                tab.done = true;
                renderFinal(tab, isTerminal(view.status) ? view : Object.assign({}, view, { status: tab.status }));
                if (!wasDone) scheduleWatch();
            } else {
                renderPartial(tab);
            }
            renderTabButton(tab);
            renderTabHead(tab);
        }

        function loadView(id, since) {
            var url = API + "/" + encodeURIComponent(id) + (since === null ? "" : "?activity_since=" + since);
            return getJSON(url).then(function (res) {
                if (!res.ok) throw new Error(reasonOf(res));
                return res.body;
            });
        }

        function loadTab(tab) {
            return loadView(tab.id, tab.seq).then(function (view) {
                if (!tabs[tab.id]) return;
                tab.error = "";
                applyView(tab, view);
            }, function (err) {
                if (!tabs[tab.id]) return;
                tab.error = fill(TEXT.loadFailed, { reason: (err && err.message) || String(err || "") });
                if (tab.dom) tab.dom.partBody.textContent = tab.error;
                renderTabButton(tab);
                renderTabHead(tab);
            });
        }

        // ── 열기·닫기 ──
        function open(id, o) {
            o = o || {};
            id = String(id || "");
            if (!enabled || !ID_RE.test(id)) return false;
            if (!o.silent && opts.onActivate) opts.onActivate();
            if (tabs[id]) {
                if (!o.background) select(id);
                return true;
            }
            var tab = newTab(id);
            var item = items.filter(function (it) { return it.investigation_id === id; })[0];
            if (item) {
                tab.subject = textOf(item.subject);
                tab.kind = item.kind;
                if (asList(item.alarm_ids).length) tab.alarmId = String(item.alarm_ids[0]);
                tab.status = item.status || "";
                setState(tab, item);
            }
            if (o.alarmId) tab.alarmId = String(o.alarmId);
            tabs[id] = tab;
            order.push(id);
            buildTab(tab);
            renderRows(tab);
            if (!o.background) select(id);
            else persist();
            renderMenu();
            scheduleWatch();
            loadTab(tab);
            return true;
        }

        function closeTab(id) {
            var tab = tabs[id];
            if (!tab) return;
            if (tab.finalPanel) tab.finalPanel.close();
            if (tab.dom) {
                dom.strip.removeChild(tab.dom.wrap);
                dom.panels.removeChild(tab.dom.panel);
            }
            delete tabs[id];
            order.splice(order.indexOf(id), 1);
            if (current === id) select(order.length ? order[order.length - 1] : "list");
            else persist();
            renderMenu();
            scheduleWatch();
        }

        function closeFinished() {
            order.slice().forEach(function (id) {
                var t = tabs[id];
                if (t.done || t.denied) closeTab(id);
            });
        }

        // ── SSE (연결 1개) ──
        function liveTabs() { return order.map(function (id) { return tabs[id]; }); }

        function scheduleWatch() {
            if (!enabled || stopped) return;
            if (timers.watch !== null) return;
            timers.watch = setT(function () {
                timers.watch = null;
                var key = watchParam(liveTabs());
                if (key === watchKey && (es || polling)) return;
                watchKey = key;
                if (!polling) connect();
            }, WATCH_DEBOUNCE_MS);
        }

        function closeStream() {
            if (es) {
                var old = es;
                es = null;
                try { old.close(); } catch (_) { /* 이미 닫힘 */ }
            }
        }

        function setConn(state, extra) {
            conn = { state: state, since: now(), lastRecv: conn.lastRecv || 0 };
            if (extra) Object.keys(extra).forEach(function (k) { conn[k] = extra[k]; });
            renderConn();
        }

        function renderConn() {
            if (!dom) return;
            var text = connText(conn, now());
            dom.conn.textContent = text;
            dom.conn.className = "dv-conn dv-conn--" + conn.state +
                (conn.state === "live" && conn.lastRecv && now() - conn.lastRecv >= IDLE_WARN_S * 1000 ? " dv-conn--idle" : "");
            if (tabs[current]) renderTabHead(tabs[current]);
        }

        function connect() {
            if (!enabled || stopped || polling || !ES) return;
            closeStream();
            if (timers.reconnect !== null) { clearT(timers.reconnect); timers.reconnect = null; }
            var key = watchParam(liveTabs());
            watchKey = key;
            var src = new ES(API + "/stream" + (key ? "?watch=" + encodeURIComponent(key) : ""));
            esCount += 1;
            es = src;
            if (conn.state !== "reconnecting") setConn("connecting");
            src.onopen = function () {
                if (src !== es) return;
                failures = 0;
                conn.lastRecv = now();
                setConn("live");
            };
            SSE_TYPES.forEach(function (type) {
                src.addEventListener(type, function (e) {
                    if (src !== es) return;
                    failures = 0;
                    conn.lastRecv = now();
                    if (conn.state !== "live") setConn("live");
                    var data = {};
                    try { data = JSON.parse((e && e.data) || "{}") || {}; } catch (_) { data = {}; }
                    handle(type, data);
                });
            });
            src.onerror = function () { onStreamError(src); };
        }

        function onStreamError(src) {
            if (src !== es) return;
            failures += 1;
            if (failures >= FAIL_LIMIT) {
                closeStream();
                startPolling();
                return;
            }
            if (conn.state !== "reconnecting") setConn("reconnecting");
            else renderConn();
            if (src.readyState === 2) {
                // 브라우저가 재연결을 포기했다(HTTP 오류) — 401·403·404면 루프 없이 멈춘다
                closeStream();
                gate().then(function (code) {
                    if (stopped || polling) return;
                    if (TEXT.stopReasons[code]) { stop(TEXT.stopReasons[code]); return; }
                    timers.reconnect = setT(function () { timers.reconnect = null; connect(); }, RECONNECT_MS);
                });
            }
        }

        // `/active` 상태 코드 — 끊김 사유 판정(성공이면 목록도 갱신한다)
        function gate() {
            return getJSON(API + "/active").then(function (res) {
                if (res.ok) setItems(res.body.items);
                return res.status;
            }, function () { return 0; });
        }

        function handle(type, data) {
            var tab = data && data.investigation_id ? tabs[String(data.investigation_id)] : null;
            if (type === "summary") { setItems(data.items); return; }
            if (type === "ping" || !tab) { renderConn(); return; }
            if (type === "activity") {
                tab.status = data.status || tab.status;
                applyActivity(tab, data);
                setState(tab, data);
                renderRows(tab);
                renderPartial(tab);
            } else if (type === "partial") {
                if (isObj(data.partial_briefing)) tab.partial = data.partial_briefing;
                renderPartial(tab);
            } else if (type === "poll_error") {
                tab.pollError = data.reason || "";
            } else if (type === "done") {
                tab.status = data.status || "done";
                tab.done = true;
                scheduleWatch();
                // 종결 — 최종 브리핑(과 남은 기록)을 다시 읽어 상세형 전체를 그린다
                loadTab(tab);
            } else if (type === "denied") {
                tab.denied = TEXT.denied[data.reason] || String(data.reason || "");
                scheduleWatch();
            }
            renderTabButton(tab);
            renderTabHead(tab);
        }

        // ── 강등: 15초 커서 조회 ──
        function startPolling() {
            polling = true;
            setConn("polling");
            pollOnce();
        }

        function pollOnce() {
            timers.poll = null;
            if (stopped || !polling) return;
            getJSON(API + "/active").then(function (res) {
                if (TEXT.stopReasons[res.status]) { stop(TEXT.stopReasons[res.status]); return; }
                if (res.ok) setItems(res.body.items);
                else { listError = reasonOf(res); renderList(); }
                return Promise.all(liveTabs().filter(function (t) { return !t.done && !t.denied; }).map(loadTab));
            }, function (err) {
                listError = (err && err.message) || String(err || "");
                renderList();
            }).then(function () {
                if (!stopped && polling) timers.poll = setT(pollOnce, FALLBACK_POLL_MS);
            });
        }

        function stop(reason) {
            stopped = true;
            polling = false;
            closeStream();
            Object.keys(timers).forEach(function (k) {
                if (timers[k] !== null && k !== "tick") { clearT(timers[k]); timers[k] = null; }
            });
            setConn("stopped", { reason: reason });
        }

        function tick() {
            timers.tick = null;
            if (!enabled) return;
            renderConn();
            if (current === "list") renderList();
            order.forEach(function (id) { renderTabButton(tabs[id]); });
            timers.tick = setT(tick, TICK_MS);
        }

        /**
         * 사전 게이트(docs/18 2026-07-14 — 권한 없는 EventSource 재연결 루프 금지): `/active`가 200일 때만
         * 탭을 보이고 스트림을 연다. 404(플래그 off)·401·403이면 탭을 숨긴 채 아무것도 열지 않는다.
         */
        function probe() {
            return getJSON(API + "/active").then(function (res) {
                if (res.status === 404 || res.status === 401 || res.status === 403) return false;
                if (res.ok) firstItems = res.body.items;
                else firstError = reasonOf(res);
                return true;
            }, function (err) {
                firstError = (err && err.message) || String(err || "");
                return true;
            }).then(function (on) {
                if (!on) { setVisible(false); return false; }
                enable();
                return true;
            });
        }

        function enable() {
            enabled = true;
            build();
            setVisible(true);
            setItems(firstItems || []);
            if (firstError) { listError = firstError; renderList(); }
            var saved = restore();
            if (saved) {
                asList(saved.tabs).forEach(function (id) { if (ID_RE.test(String(id))) open(String(id), { silent: true, background: true }); });
            }
            select(saved && tabs[saved.current] ? saved.current : "list");
            scheduleWatch();   // 복원한 탭이 몇 개든 연결은 1번 연다
            timers.tick = setT(tick, TICK_MS);
        }

        return {
            probe: probe,
            open: open,
            close: closeTab,
            closeFinished: closeFinished,
            select: select,
            enabled: function () { return enabled; },
            // 시험·진단용 읽기
            state: function () {
                return { conn: conn, failures: failures, polling: polling, stopped: stopped, esCount: esCount,
                    watch: watchKey, tabs: order.slice(), current: current, items: items.length };
            },
            tab: function (id) { return tabs[id] || null; },
        };
    }

    // 페이지당 하나 — app.js가 `init`으로 만들고 진입 배선은 `openIfEnabled`로 묻는다.
    var instance = null;

    var api = {
        TEXT: TEXT,
        ROW_CAP: ROW_CAP,
        FAIL_LIMIT: FAIL_LIMIT,
        FALLBACK_POLL_MS: FALLBACK_POLL_MS,
        eventRow: eventRow,
        mergeEvents: mergeEvents,
        watchParam: watchParam,
        connText: connText,
        statusText: statusText,
        fmtElapsed: fmtElapsed,
        create: create,
        init: function (opts) {
            instance = create(opts);
            return instance.probe();
        },
        isEnabled: function () { return !!(instance && instance.enabled()); },
        // 켜져 있으면 그 조사의 하위 탭을 열고 true — 꺼져 있으면 false(호출부가 종전 진입을 탄다)
        openIfEnabled: function (id, o) { return !!(instance && instance.enabled() && instance.open(id, o)); },
    };
    root.DiagnosisView = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
