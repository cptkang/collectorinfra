/**
 * 알람 노이즈 캔슬링 관제 화면 (Plan 54 모듈 6).
 *
 * 억제는 곧 "보여주지 않음"이므로 이 화면은 그 반대로 **억제 내역을 가장 잘 보여주는 곳**이다.
 * 집계(KPI·퍼널·추이)·메타모니터링·실시간 피드·결정 추적·침묵 관리·정책 열람을 담당한다.
 *
 * 스택 정합: 바닐라 JS · 외부 CDN 0(폐쇄망) · 색은 전부 테마 토큰(라이트/다크 양쪽).
 *
 * 피드의 완전성에 관하여: PAGE 티어는 즉시 통보 경로라 SSE(alarm_bus)에 실리지 않는다.
 * 그래서 피드는 **SSE(즉시성) + 주기 재동기화(완전성)** 를 병행한다 — 스트림만 믿으면
 * 가장 중요한 알람이 관제 화면에서 빠진다.
 */

(function () {
    "use strict";

    var token = localStorage.getItem("admin_token") || localStorage.getItem("user_token");
    if (!token) {
        window.location.href = "/login?next=/static/admin/noise.html";
        return;
    }

    var API = "/api/v1/admin/noise";
    var RESYNC_MS = 30000;     // 피드 재동기화 주기 — PAGE가 최대 이 시간 안에 나타난다
    var SUMMARY_MS = 60000;    // 집계 갱신 주기
    var FEED_MAX = 60;         // 피드 보관 상한(메모리·DOM 가드)

    var state = {
        range: "24h",
        feed: [],              // {alarm_id, alarm_name, server_name, tier, stage, reason, ts}
        stages: [],            // 결정 단계 메타(단계 필터·드로어 라벨용)
        suppressCap: 2,
        silenceEnabled: false,
        es: null,
        esRetryMs: 1000,
    };

    var alertError = document.getElementById("alertError");
    var alertSuccess = document.getElementById("alertSuccess");

    // ── 공통 ────────────────────────────────────────────────────────────

    function apiRequest(method, url, body) {
        var options = {
            method: method,
            headers: {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        };
        if (body) options.body = JSON.stringify(body);
        return fetch(url, options).then(function (res) {
            if (res.status === 401) {
                localStorage.removeItem("admin_token");
                window.location.href = "/login?next=/static/admin/noise.html";
            }
            return res;
        });
    }

    async function getJSON(path) {
        var res = await apiRequest("GET", path);
        if (res.status === 403) {
            window.location.href = "/";
            return null;
        }
        if (!res.ok) {
            var detail = await res.json().catch(function () { return {}; });
            throw new Error(detail.detail || ("요청 실패 (" + res.status + ")"));
        }
        return res.json();
    }

    function showError(message) {
        alertError.textContent = message;
        alertError.style.display = "block";
        alertSuccess.style.display = "none";
        setTimeout(function () { alertError.style.display = "none"; }, 6000);
    }

    function showSuccess(message) {
        alertSuccess.textContent = message;
        alertSuccess.style.display = "block";
        alertError.style.display = "none";
        setTimeout(function () { alertSuccess.style.display = "none"; }, 4000);
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function fmtInt(value) {
        return Number(value || 0).toLocaleString("ko-KR");
    }

    function fmtPct(ratio) {
        return Math.round(Number(ratio || 0) * 1000) / 10 + "%";
    }

    function fmtTime(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    function fmtAge(seconds) {
        if (seconds === null || seconds === undefined) return "—";
        var s = Math.round(Number(seconds));
        if (s < 60) return s + "초 전";
        if (s < 3600) return Math.round(s / 60) + "분 전";
        if (s < 86400) return Math.round(s / 3600) + "시간 전";
        return Math.round(s / 86400) + "일 전";
    }

    // ── 헤더 파형 ───────────────────────────────────────────────────────

    (function buildWave() {
        var wave = document.getElementById("wave");
        for (var i = 0; i < 9; i++) {
            var bar = el("i");
            bar.style.height = (4 + ((i * 5) % 15)) + "px";
            bar.style.animationDelay = (i * 0.12) + "s";
            wave.appendChild(bar);
        }
    })();

    // ── 집계: KPI + 퍼널 ────────────────────────────────────────────────

    async function loadSummary() {
        var data = await getJSON(API + "/summary?range=" + state.range);
        if (!data) return;

        document.getElementById("kpiRaw").textContent = fmtInt(data.raw);
        document.getElementById("kpiRawSub").textContent =
            state.range.toUpperCase() + " · " + (data.gate_enabled ? "게이트 동작 중" : "게이트 꺼짐");
        document.getElementById("kpiPage").textContent = fmtInt(data.tiers.page);
        document.getElementById("kpiTicket").textContent = fmtInt(data.tiers.ticket);
        document.getElementById("kpiDashboard").textContent = fmtInt(data.tiers.dashboard);
        document.getElementById("kpiSuppress").textContent = fmtInt(data.tiers.suppress);
        document.getElementById("kpiRatio").textContent = fmtPct(data.suppress_ratio);
        document.getElementById("kpiActionable").textContent =
            "액션가능 " + fmtPct(data.actionable_ratio);

        document.getElementById("funnelRaw").textContent = fmtInt(data.raw);
        document.getElementById("funnelPage").textContent = fmtInt(data.tiers.page);

        renderFunnel(data);
        renderTierCards(data);
        state.stages = data.stages;
        fillStageFilter(data.stages);
    }

    function renderFunnel(data) {
        var host = document.getElementById("stages");
        host.innerHTML = "";
        var raw = data.raw || 0;

        data.stages.forEach(function (stage) {
            var row = el("div", "stage-row" + (stage.terminated ? "" : " is-idle"));
            row.appendChild(el("div", "name", stage.label));

            var bar = el("div", "bar");
            // 살아남아 다음 단계로 간 비율(live)과 여기서 캔슬된 비율(gone)을 함께 그린다.
            var livePct = raw ? ((stage.residual - stage.terminated) / raw) * 100 : 0;
            var cutPct = raw ? (stage.cut / raw) * 100 : 0;
            var live = el("div", "live");
            live.style.width = Math.max(0, livePct) + "%";
            var gone = el("div", "gone");
            gone.style.left = Math.max(0, livePct) + "%";
            gone.style.width = cutPct + "%";
            bar.appendChild(live);
            bar.appendChild(gone);
            row.appendChild(bar);

            var label = stage.terminated
                ? "-" + fmtInt(stage.terminated) + (stage.cut ? " (캔슬 " + fmtInt(stage.cut) + ")" : "")
                : "—";
            row.appendChild(el("div", "val", label));
            row.title = stage.label + " · 도달 " + fmtInt(stage.residual)
                + " · 종결 " + fmtInt(stage.terminated) + " · 캔슬 " + fmtInt(stage.cut);
            host.appendChild(row);
        });
    }

    function renderTierCards(data) {
        var host = document.getElementById("tierCards");
        host.innerHTML = "";
        var cards = [
            { key: "page", cls: "t-page", label: "PAGE", note: "즉시 통보" },
            { key: "ticket", cls: "t-ticket", label: "TICKET", note: "대기열" },
            { key: "dashboard", cls: "t-dash", label: "DASHBOARD", note: "표시만" },
            { key: "suppress", cls: "t-sup", label: "SUPPRESS", note: "기록 유지" },
        ];
        cards.forEach(function (card) {
            var count = data.tiers[card.key] || 0;
            var pct = data.raw ? Math.round((count / data.raw) * 1000) / 10 : 0;
            var node = el("div", "tier-card " + card.cls);
            node.appendChild(el("div", "t", card.label));
            node.appendChild(el("div", "v", fmtInt(count)));
            node.appendChild(el("div", "p", pct + "% · " + card.note));
            host.appendChild(node);
        });
    }

    // ── 집계: 추이 ──────────────────────────────────────────────────────

    var BUCKET_BY_RANGE = { "1h": "5m", "24h": "2h", "7d": "6h", "30d": "1d" };

    async function loadTimeseries() {
        var bucket = BUCKET_BY_RANGE[state.range] || "2h";
        var data = await getJSON(API + "/timeseries?range=" + state.range + "&bucket=" + bucket);
        if (!data) return;

        document.getElementById("chartHint").textContent =
            state.range.toUpperCase() + " · " + bucket + " 버킷";

        var host = document.getElementById("chart");
        host.innerHTML = "";
        var max = 1;
        data.points.forEach(function (p) {
            max = Math.max(max, p.page + p.ticket + p.dashboard + p.suppress);
        });

        data.points.forEach(function (p) {
            var col = el("div", "col");
            col.title = fmtTime(p.bucket_ts)
                + "\nPAGE " + p.page + " · TICKET " + p.ticket
                + " · DASHBOARD " + p.dashboard + " · SUPPRESS " + p.suppress;
            [["suppress", "t-sup"], ["dashboard", "t-dash"], ["ticket", "t-ticket"], ["page", "t-page"]]
                .forEach(function (pair) {
                    var count = p[pair[0]];
                    if (!count) return;
                    var seg = el("div", "seg-bar " + pair[1]);
                    seg.style.height = Math.max(2, (count / max) * 120) + "px";
                    col.appendChild(seg);
                });
            host.appendChild(col);
        });
    }

    // ── 메타모니터링 ────────────────────────────────────────────────────

    async function loadHealth() {
        var data = await getJSON(API + "/health");
        if (!data) return;

        var pill = document.getElementById("healthPill");
        pill.className = "pill " + (data.healthy ? "pill--ok" : "pill--warn");
        pill.innerHTML = "";
        pill.appendChild(el("span", "dot"));
        pill.appendChild(document.createTextNode(
            data.healthy ? "억제기 정상" : "억제기 경고 " + data.alerts.length + "건"
        ));

        var ratio = Math.round(Number(data.suppress_ratio || 0) * 100);
        var ring = document.getElementById("ratioRing");
        var overThreshold = data.suppress_ratio > data.suppress_ratio_threshold;
        ring.style.background = "conic-gradient("
            + (overThreshold ? "var(--error)" : "var(--accent)") + " " + ratio + "%,"
            + " var(--border) 0)";
        document.getElementById("ratioText").textContent = ratio + "%";
        document.getElementById("ratioNote").innerHTML =
            "임계 " + Math.round(data.suppress_ratio_threshold * 100) + "% · 창 "
            + Math.round(data.window_seconds / 60) + "분<br>"
            + "집계 " + fmtInt(data.total) + "건"
            + (overThreshold ? "<br><strong>임계 초과 — 과억제 점검 필요</strong>" : "");

        var watchRing = document.getElementById("watchRing");
        var stale = data.alerts.some(function (a) { return a.type === "no_events"; });
        watchRing.style.background = "conic-gradient("
            + (stale ? "var(--warning)" : "var(--success)") + " 100%, var(--border) 0)";
        document.getElementById("watchText").textContent = stale ? "무수신" : "LIVE";
        document.getElementById("watchNote").innerHTML =
            "마지막 결정 " + fmtAge(data.last_event_age_seconds) + "<br>"
            + (data.last_event_ts ? fmtTime(data.last_event_ts) : "기록 없음")
            + (stale ? "<br><strong>수신이 끊겼습니다</strong>" : "");
    }

    async function loadTopSuppressed() {
        var data = await getJSON(API + "/top-suppressed?range=" + state.range);
        if (!data) return;

        var body = document.getElementById("topSuppressed");
        body.innerHTML = "";
        if (!data.items.length) {
            var row = el("tr");
            var cell = el("td", null, "억제된 알람이 없습니다.");
            cell.colSpan = 2;
            cell.style.color = "var(--text-muted)";
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        data.items.forEach(function (item) {
            var row = el("tr");
            row.appendChild(el("td", "k", item.alarm_name));
            row.appendChild(el("td", null, fmtInt(item.count) + " · " + item.label));
            body.appendChild(row);
        });
    }

    // ── 실시간 피드 (SSE + 주기 재동기화) ───────────────────────────────

    function feedKey(item) {
        return String(item.alarm_id || "") + "|" + String(item.ts || "");
    }

    function renderFeed() {
        var host = document.getElementById("feed");
        host.innerHTML = "";
        if (!state.feed.length) {
            host.appendChild(el("div", "empty", "표시할 결정이 없습니다."));
            return;
        }
        state.feed.slice(0, FEED_MAX).forEach(function (item) {
            var node = el("div", "feed-item t-" + (item.tier || "suppress"));
            node.setAttribute("role", "button");
            node.tabIndex = 0;

            var row1 = el("div", "row1");
            row1.appendChild(el("div", "nm", item.alarm_name || "(알람명 미기록)"));
            row1.appendChild(el("span", "badge-tier t-" + (item.tier || "suppress"),
                String(item.tier || "").toUpperCase()));
            node.appendChild(row1);

            var meta = (item.server_name ? item.server_name + " · " : "")
                + (item.stage_label || item.stage || "") + " · " + fmtTime(item.ts);
            node.appendChild(el("div", "rs", meta));
            node.appendChild(el("div", "rs", item.reason || ""));

            function open() { openDrawer(item.alarm_id); }
            node.addEventListener("click", open);
            node.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
            });
            host.appendChild(node);
        });
    }

    async function resyncFeed() {
        var data = await getJSON(API + "/decisions?range=" + state.range + "&size=" + FEED_MAX);
        if (!data) return;
        // 서버 이력이 정본이다 — SSE로 먼저 들어온 항목도 여기서 정리된다(중복·누락 해소).
        state.feed = data.items.map(function (item) {
            return {
                alarm_id: item.alarm_id,
                alarm_name: item.alarm_name,
                server_name: item.server_name,
                tier: item.tier,
                stage: item.stage,
                stage_label: item.stage_label,
                reason: item.reason,
                ts: item.ts,
            };
        });
        renderFeed();
    }

    function connectStream() {
        if (state.es) state.es.close();
        // EventSource는 헤더를 실을 수 없어 쿠키 인증에 기댄다(운영자 판정은 서버가 한다).
        var es = new EventSource(API + "/stream");
        state.es = es;

        es.onopen = function () {
            state.esRetryMs = 1000;
            setStreamPill("ok", "스트림 연결됨");
        };
        es.onmessage = function (event) {
            var payload;
            try { payload = JSON.parse(event.data); } catch (e) { return; }
            if (!payload || payload.type === "ping") return;
            if (!payload.tier) return;

            var item = {
                alarm_id: payload.alarm_id,
                alarm_name: payload.alarm_name,
                server_name: payload.server_name || payload.hostname,
                tier: payload.tier,
                stage: payload.stage,
                stage_label: "",
                reason: payload.tier_reason,
                ts: payload.received_at || payload.alarm_time || new Date().toISOString(),
            };
            var exists = state.feed.some(function (f) { return feedKey(f) === feedKey(item); });
            if (!exists) {
                state.feed.unshift(item);
                state.feed = state.feed.slice(0, FEED_MAX);
                renderFeed();
            }
        };
        es.onerror = function () {
            setStreamPill("err", "스트림 끊김 — 재연결 중");
            es.close();
            state.es = null;
            // 지수 백오프(최대 30초) — 서버 재시작 중에 재연결 폭주를 만들지 않는다.
            setTimeout(connectStream, state.esRetryMs);
            state.esRetryMs = Math.min(state.esRetryMs * 2, 30000);
        };
    }

    function setStreamPill(kind, text) {
        var pill = document.getElementById("streamPill");
        pill.className = "pill " + (kind === "ok" ? "pill--ok" : kind === "err" ? "pill--err" : "");
        pill.innerHTML = "";
        pill.appendChild(el("span", "dot"));
        pill.appendChild(document.createTextNode(text));
    }

    // ── 결정 추적 드로어 ────────────────────────────────────────────────

    var drawer = document.getElementById("drawer");
    var scrim = document.getElementById("scrim");
    var drawerContext = null;

    async function openDrawer(alarmId) {
        if (!alarmId) return;
        try {
            var data = await getJSON(API + "/decisions/" + encodeURIComponent(alarmId));
            if (!data) return;
            drawerContext = data.decision;

            document.getElementById("drawerName").textContent =
                data.decision.alarm_name || "(알람명 미기록)";
            document.getElementById("drawerMeta").textContent =
                [data.decision.server_name, data.decision.alarm_id, fmtTime(data.decision.ts)]
                    .filter(Boolean).join(" · ");

            var trace = document.getElementById("drawerTrace");
            trace.innerHTML = "";
            data.timeline.forEach(function (step, index) {
                var row = el("div", "trace-row " + step.status);
                var mark = el("div", "mark");
                mark.appendChild(el("div", "dot2"));
                if (index < data.timeline.length - 1) mark.appendChild(el("div", "line"));
                row.appendChild(mark);

                var note = step.status === "decided" ? " — 여기서 결정"
                    : step.status === "short_circuited" ? " (단락 — 평가되지 않음)"
                    : "";
                var text = el("div", "txt", step.label + note);
                if (step.status === "decided" && data.decision.reason) {
                    text.appendChild(el("div", "rs", data.decision.reason));
                }
                row.appendChild(text);
                trace.appendChild(row);
            });

            var signals = document.getElementById("drawerSignals");
            signals.innerHTML = "";
            var snapshot = data.decision.signals || {};
            Object.keys(snapshot).forEach(function (key) {
                var row = el("tr");
                row.appendChild(el("td", null, key));
                var value = snapshot[key];
                row.appendChild(el("td", null,
                    value === null || value === undefined ? "—" : String(value)));
                signals.appendChild(row);
            });

            drawer.classList.add("on");
            drawer.setAttribute("aria-hidden", "false");
            scrim.classList.add("on");
        } catch (err) {
            showError(err.message);
        }
    }

    function closeDrawer() {
        drawer.classList.remove("on");
        drawer.setAttribute("aria-hidden", "true");
        scrim.classList.remove("on");
    }

    document.getElementById("closeDrawer").addEventListener("click", closeDrawer);
    scrim.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeDrawer();
    });

    // 드로어에서 곧바로 침묵 규칙을 준비한다(억제 교정 동선 — 값만 채우고 저장은 사용자가).
    document.getElementById("silenceThisBtn").addEventListener("click", function () {
        if (!drawerContext) return;
        closeDrawer();
        switchPane("silence");
        document.getElementById("slcServer").value = drawerContext.server_name || "";
        document.getElementById("slcAlarm").value = drawerContext.alarm_name || "";
        document.getElementById("slcReason").value = "";
        document.getElementById("slcReason").focus();
        showSuccess("침묵 규칙 초안을 채웠습니다 — 사유를 적고 추가하십시오.");
    });

    // ── 침묵 관리 ───────────────────────────────────────────────────────

    async function loadSilences() {
        var includeInactive = document.getElementById("showInactive").checked;
        var data = await getJSON(API + "/silences?include_inactive=" + includeInactive);
        if (!data) return;

        state.silenceEnabled = data.enabled;
        document.getElementById("silenceDisabledNote").style.display =
            data.enabled ? "none" : "block";

        var body = document.getElementById("silenceBody");
        body.innerHTML = "";
        if (!data.items.length) {
            var row = el("tr");
            var cell = el("td", null, "등록된 침묵 규칙이 없습니다.");
            cell.colSpan = 5;
            cell.style.color = "var(--text-muted)";
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        data.items.forEach(function (rule) {
            var row = el("tr");
            row.appendChild(el("td", "k", rule.matcher_summary));
            row.appendChild(el("td", null, rule.reason));
            row.appendChild(el("td", null, rule.created_by));
            row.appendChild(el("td", null,
                rule.revoked_at ? "해제됨" : rule.active ? fmtTime(rule.expires_at) : "만료됨"));

            var actionCell = el("td");
            if (rule.active) {
                var button = el("button", "btn btn-secondary", "해제");
                button.style.cssText = "padding:4px 10px;font-size:11px;";
                button.addEventListener("click", function () { revokeSilence(rule.id); });
                actionCell.appendChild(button);
            }
            row.appendChild(actionCell);
            body.appendChild(row);
        });
    }

    async function addSilence() {
        var body = {
            server_name: document.getElementById("slcServer").value.trim(),
            alarm_name: document.getElementById("slcAlarm").value.trim(),
            resource_name: document.getElementById("slcResource").value.trim(),
            max_severity: parseInt(document.getElementById("slcSeverity").value, 10),
            reason: document.getElementById("slcReason").value.trim(),
            duration_seconds: parseInt(document.getElementById("slcDuration").value, 10),
        };
        try {
            var res = await apiRequest("POST", API + "/silences", body);
            var payload = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                // 서버가 막은 이유(전체 침묵·심각도 상한·만료 초과)를 그대로 보여준다.
                showError(payload.detail || "침묵 규칙을 만들지 못했습니다.");
                return;
            }
            showSuccess("침묵 규칙을 추가했습니다: " + payload.rule.matcher_summary);
            document.getElementById("slcReason").value = "";
            loadSilences();
        } catch (err) {
            showError(err.message);
        }
    }

    async function revokeSilence(ruleId) {
        try {
            var res = await apiRequest("DELETE", API + "/silences/" + encodeURIComponent(ruleId));
            if (!res.ok) {
                var payload = await res.json().catch(function () { return {}; });
                showError(payload.detail || "해제하지 못했습니다.");
                return;
            }
            showSuccess("침묵 규칙을 해제했습니다.");
            loadSilences();
        } catch (err) {
            showError(err.message);
        }
    }

    // ── 결정 이력 조회 ──────────────────────────────────────────────────

    function fillStageFilter(stages) {
        var select = document.getElementById("decStage");
        if (select.options.length > 1) return;   // 한 번만 채운다
        stages.forEach(function (stage) {
            var option = document.createElement("option");
            option.value = stage.stage;
            option.textContent = stage.label;
            select.appendChild(option);
        });
    }

    async function loadDecisions() {
        var params = [
            "range=" + state.range,
            "size=100",
            "q=" + encodeURIComponent(document.getElementById("decSearch").value.trim()),
            "tier=" + document.getElementById("decTier").value,
            "stage=" + document.getElementById("decStage").value,
        ];
        var data = await getJSON(API + "/decisions?" + params.join("&"));
        if (!data) return;

        document.getElementById("decTotal").textContent = fmtInt(data.total) + "건";
        var body = document.getElementById("decisionBody");
        body.innerHTML = "";
        if (!data.items.length) {
            var row = el("tr");
            var cell = el("td", null, "조건에 맞는 결정이 없습니다.");
            cell.colSpan = 6;
            cell.style.color = "var(--text-muted)";
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        data.items.forEach(function (item) {
            var row = el("tr");
            row.style.cursor = "pointer";
            row.addEventListener("click", function () { openDrawer(item.alarm_id); });
            row.appendChild(el("td", "k", fmtTime(item.ts)));
            row.appendChild(el("td", null, item.alarm_name || "—"));
            row.appendChild(el("td", "k", item.server_name || "—"));

            var tierCell = el("td");
            tierCell.appendChild(el("span", "badge-tier t-" + item.tier,
                String(item.tier || "").toUpperCase()));
            row.appendChild(tierCell);

            row.appendChild(el("td", null, item.stage_label || item.stage || "—"));
            row.appendChild(el("td", null, item.reason || "—"));
            body.appendChild(row);
        });
    }

    // ── 정책 (읽기 전용) ────────────────────────────────────────────────

    async function loadPolicy() {
        var data = await getJSON(API + "/policy");
        if (!data) return;

        var matrix = document.getElementById("policyMatrix");
        matrix.innerHTML = "";
        matrix.appendChild(el("div", "h"));
        ["높음", "보통", "낮음"].forEach(function (importance) {
            matrix.appendChild(el("div", "h", "중요도 " + importance));
        });
        data.matrix.forEach(function (row) {
            var head = el("div", "h", "심각도 " + row.severity + " ");
            if (row.locked) head.appendChild(el("span", "lock", "잠금"));
            matrix.appendChild(head);
            ["높음", "보통", "낮음"].forEach(function (importance) {
                var tier = row.cells[importance];
                matrix.appendChild(el("div", "cell t-" + tier, String(tier).toUpperCase()));
            });
        });

        var list = document.getElementById("policySettings");
        list.innerHTML = "";
        data.settings.forEach(function (setting) {
            var item = el("div", "policy-item");
            var label = el("div");
            label.appendChild(el("div", null, setting.key));
            label.appendChild(el("div", "env", setting.env_key));
            item.appendChild(label);
            if (setting.locked) item.appendChild(el("span", "lock", "안전 고정"));
            item.appendChild(el("span", "val", String(setting.value)));

            var link = el("a", "btn btn-secondary", "설정에서 변경");
            link.href = data.editor_path + "#" + setting.env_key;
            link.style.cssText = "padding:4px 10px;font-size:11px;text-decoration:none;";
            item.appendChild(link);
            list.appendChild(item);
        });
    }

    // ── 탭·범위 전환 ────────────────────────────────────────────────────

    function switchPane(name) {
        document.querySelectorAll(".mgmt-tabs button").forEach(function (button) {
            button.classList.toggle("on", button.dataset.pane === name);
        });
        document.querySelectorAll(".pane").forEach(function (pane) {
            pane.classList.toggle("on", pane.id === "pane-" + name);
        });
        if (name === "decisions") loadDecisions();
        if (name === "policy") loadPolicy();
        if (name === "silence") loadSilences();
    }

    document.querySelectorAll(".mgmt-tabs button").forEach(function (button) {
        button.addEventListener("click", function () { switchPane(button.dataset.pane); });
    });

    document.getElementById("rangeSeg").addEventListener("click", function (event) {
        var button = event.target.closest("button[data-range]");
        if (!button) return;
        state.range = button.dataset.range;
        document.querySelectorAll("#rangeSeg button").forEach(function (b) {
            b.classList.toggle("on", b === button);
        });
        refreshAggregates();
    });

    document.getElementById("addSilenceBtn").addEventListener("click", addSilence);
    document.getElementById("showInactive").addEventListener("change", loadSilences);
    document.getElementById("decSearchBtn").addEventListener("click", loadDecisions);
    document.getElementById("decSearch").addEventListener("keydown", function (e) {
        if (e.key === "Enter") loadDecisions();
    });

    // ── 부트스트랩 ──────────────────────────────────────────────────────

    function refreshAggregates() {
        // 한 축이 실패해도 나머지는 그린다 — 화면 전체가 빈 칸이 되지 않게 개별 처리한다.
        loadSummary().catch(function (err) { showError("집계 조회 실패: " + err.message); });
        loadTimeseries().catch(function (err) { showError("추이 조회 실패: " + err.message); });
        loadTopSuppressed().catch(function () { /* 표시 실패는 조용히 넘긴다 */ });
        resyncFeed().catch(function () { /* 피드는 SSE가 보완한다 */ });
    }

    refreshAggregates();
    loadHealth().catch(function (err) { showError("헬스 조회 실패: " + err.message); });
    loadSilences().catch(function () { /* 탭 진입 시 재시도된다 */ });
    connectStream();

    setInterval(function () {
        loadHealth().catch(function () {});
        loadSummary().catch(function () {});
    }, SUMMARY_MS);
    setInterval(function () {
        resyncFeed().catch(function () {});
    }, RESYNC_MS);
})();
