/**
 * 알람 노이즈 캔슬링 관제 화면 (Plan 54 모듈 6 · D-245로 사용자 화면 공용화).
 *
 * 억제는 곧 "보여주지 않음"이므로 이 화면은 그 반대로 **억제 내역을 가장 잘 보여주는 곳**이다.
 * 집계(KPI·퍼널·추이)·메타모니터링·실시간 피드·결정 추적·침묵 관리·정책 열람을 담당한다.
 *
 * 한 파일이 두 화면을 그린다 — `<body data-noise-mode>`가 모드를 정한다:
 *     admin : `/static/admin/noise.html` — 읽기 전량 + 메타모니터링 + 침묵 + 정책 + SSE.
 *     user  : `/static/noise.html`       — 읽기 5종만. 침묵·정책·메타·SSE는 **호출하지 않는다**
 *                                          (그 DOM도 없다). API base가 `/api/v1/noise`로 갈린다.
 * 복제하지 않는 이유는 단순하다 — 두 벌이면 한쪽만 고쳐진다.
 *
 * 스택 정합: 바닐라 JS · 외부 CDN 0(폐쇄망) · 색은 전부 테마 토큰(라이트/다크 양쪽).
 *
 * 피드의 완전성에 관하여: PAGE 티어는 즉시 통보 경로라 SSE(alarm_bus)에 실리지 않는다.
 * 그래서 운영자 피드는 **SSE(즉시성) + 주기 재동기화(완전성)** 를 병행한다 — 스트림만 믿으면
 * 가장 중요한 알람이 관제 화면에서 빠진다. 사용자 화면은 스트림을 열지 않으므로(D-196 ⑤ 유지)
 * 재동기화만으로 채운다 — 최대 RESYNC_MS 만큼 늦게 보일 뿐 빠지는 결정은 없다.
 */

(function () {
    "use strict";

    // 모드는 페이지가 선언한다(미선언이면 종전 동작 = 운영자).
    var IS_ADMIN = (document.body.getAttribute("data-noise-mode") || "admin") === "admin";
    var PAGE_PATH = IS_ADMIN ? "/static/admin/noise.html" : "/noise";

    // 운영자 화면은 운영자 토큰을, 사용자 화면은 사용자 토큰을 먼저 본다.
    // (서버가 최종 판정하므로 여기 순서는 "먼저 시도할 토큰"을 고르는 것뿐이다.)
    // 고른 토큰의 **키 이름**을 기억한다 — 401이면 바로 그 키를 지워야 한다(D-247 · D-245 결함 수정).
    // 만료 토큰을 남기면 로그인 화면이 그 토큰을 보고 곧바로 이 화면으로 되돌려 무한 리다이렉트가 난다.
    var TOKEN_KEYS = IS_ADMIN ? ["admin_token", "user_token"] : ["user_token", "admin_token"];
    var tokenKey = null;
    var token = null;
    for (var i = 0; i < TOKEN_KEYS.length && !token; i++) {
        token = localStorage.getItem(TOKEN_KEYS[i]);
        if (token) tokenKey = TOKEN_KEYS[i];
    }
    if (!token) {
        window.location.href = "/login?next=" + encodeURIComponent(PAGE_PATH);
        return;
    }

    var API = IS_ADMIN ? "/api/v1/admin/noise" : "/api/v1/noise";
    var RESYNC_MS = 30000;     // 피드 재동기화 주기 — PAGE가 최대 이 시간 안에 나타난다
    var SUMMARY_MS = 60000;    // 집계 갱신 주기
    var FEED_MAX = 60;         // 피드 보관 상한(메모리·DOM 가드)

    var state = {
        range: "24h",
        feed: [],              // {alarm_id, alarm_name, server_name, tier, stage, reason, ts, record}
        stages: [],            // 결정 단계 메타(단계 필터·드로어 타임라인 순서·패널 헤더용)
        summary: null,         // 마지막 /summary 응답(패널 헤더의 단계 설명·활성 여부 원천)
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
                if (tokenKey) localStorage.removeItem(tokenKey);  // 실제로 보낸 토큰의 키
                window.location.href = "/login?next=" + encodeURIComponent(PAGE_PATH);
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
        if (!alertError) return;
        alertError.textContent = message;
        alertError.style.display = "block";
        if (alertSuccess) alertSuccess.style.display = "none";
        setTimeout(function () { alertError.style.display = "none"; }, 6000);
    }

    function showSuccess(message) {
        if (!alertSuccess) return;
        alertSuccess.textContent = message;
        alertSuccess.style.display = "block";
        if (alertError) alertError.style.display = "none";
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

    function pad2(n) { return (n < 10 ? "0" : "") + n; }

    // 월-일 시:분 — 목록 표처럼 좁은 칸용(연도·초는 드로어에서 본다).
    function fmtShortTime(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " "
            + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }

    // 소요·경과 초를 사람이 읽는 길이로(근거 칸 — 자가복구 소요·인히비터 경과·판정 창).
    function fmtDuration(seconds) {
        var s = Number(seconds);
        if (seconds === null || seconds === undefined || !isFinite(s)) return "—";
        s = Math.round(s);
        if (s < 60) return s + "초";
        if (s < 3600) return Math.floor(s / 60) + "분" + (s % 60 ? " " + (s % 60) + "초" : "");
        var h = Math.floor(s / 3600);
        var m = Math.round((s % 3600) / 60);
        return h + "시간" + (m ? " " + m + "분" : "");
    }

    // 4티어 — 시급한 것부터(표시 순서). 서버 `facets.tiers`도 이 4키를 항상 담는다.
    var TIER_KEYS = ["page", "ticket", "dashboard", "suppress"];
    var TIER_NAMES = { page: "PAGE", ticket: "TICKET", dashboard: "DASHBOARD", suppress: "SUPPRESS" };

    function tierBadge(tier) {
        return el("span", "badge-tier t-" + (tier || "suppress"),
            TIER_NAMES[tier] || String(tier || "").toUpperCase());
    }

    // 드릴다운 진입점 배선(plans/112 §2.1 — 마우스 전용 금지).
    // container는 마우스 클릭 영역(카드 전체), target은 키보드 포커스·스크린리더 대상이다.
    // 둘을 가르는 이유: 카드 안에는 (!) 설명 버튼이 함께 있어, 카드 자체를 role=button으로
    // 만들면 버튼 안에 버튼이 들어간다. (!)는 자기 클릭을 멈추므로 카드 클릭으로 번지지 않는다.
    function wireDrill(container, target, label, handler) {
        container.classList.add("is-drill");
        target.classList.add("drill");
        target.setAttribute("role", "button");
        target.tabIndex = 0;
        target.setAttribute("aria-label", label);
        container.addEventListener("click", function () { handler(target); });
        target.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(target); }
        });
    }

    function setInert(node, on) {
        if (!node) return;
        if (on) node.setAttribute("inert", "");
        else node.removeAttribute("inert");
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

        state.summary = data;
        state.stages = data.stages;
        renderFunnel(data);
        renderTierCards(data);
        fillStageFilter(data.stages);
    }

    function renderFunnel(data) {
        var host = document.getElementById("stages");
        // 60초 갱신이 행을 다시 그린다 — 그 안의 (!)가 연 팝오버는 먼저 닫는다.
        if (infoOwner && host.contains(infoOwner)) closeInfoPop();
        host.innerHTML = "";
        var raw = data.raw || 0;

        data.stages.forEach(function (stage) {
            var row = el("div", "stage-row" + (stage.terminated ? "" : " is-idle"));
            // 단계 행 = 그 단계의 판단 목록 진입점(plans/112 S3). 0건(흐린) 행도 연다 — 비어 있는
            // 이유(꺼짐 / 해당 없음)를 설명하는 것이 목적이다. 키보드 대상은 라벨이다.
            var name = el("div", "name");
            var link = el("span", "stage-link", stage.label);
            name.appendChild(link);
            link.setAttribute("data-focus-key", "stage:" + stage.stage);
            // 단계 설명(!) — 행 드릴다운과 별도 버튼(클릭이 행으로 번지지 않는다).
            name.appendChild(makeInfoButton("stage:" + stage.stage, stage.label));
            row.appendChild(name);
            wireDrill(row, link, stage.label + " 단계 판단 목록 열기 — 종결 " + fmtInt(stage.terminated)
                + "건, 캔슬 " + fmtInt(stage.cut) + "건", function (trigger) {
                openStage(stage.stage, trigger);
            });

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
        if (infoOwner && host.contains(infoOwner)) closeInfoPop();
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
            var head = el("div", "t", card.label);
            head.appendChild(makeInfoButton("kpi." + card.key, card.label));
            node.appendChild(head);
            var value = el("div", "v", fmtInt(count));
            value.setAttribute("data-focus-key", "tier:" + card.key);
            node.appendChild(value);
            node.appendChild(el("div", "p", pct + "% · " + card.note));
            // KPI 카드와 같은 프로파일로 연다(plans/112 §2.2 — 퍼널 아래 티어 카드 4종).
            wireDrill(node, value, card.label + " 세부 리스트 열기", function (trigger) {
                openProfile(card.key, trigger);
            });
            host.appendChild(node);
        });
    }

    // ── 집계: 추이 ──────────────────────────────────────────────────────

    // (plans/112 S5 · §2.8) 외부 차트 라이브러리 없이 CSS 플렉스 스택 막대를 유지하고
    // 축·눈금·시각 라벨·합계·툴팁·건수/비율 전환을 더한다. 막대 높이는 축과 **정확히 비례**한다
    // (종전 최소 2px 보정 제거 — 0이 아닌데 1px 미만인 조각만 1px로 보인다).

    var BUCKET_BY_RANGE = { "1h": "5m", "24h": "2h", "7d": "6h", "30d": "1d" };
    var BUCKET_SECONDS = { "5m": 300, "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400 };
    var BUCKET_WORDS = { "5m": "5분", "1h": "1시간", "2h": "2시간", "6h": "6시간", "1d": "1일" };
    // 쌓는 순서(아래 → 위) — 위로 갈수록 시급하다. 범례·툴팁은 위 → 아래(PAGE부터)로 읽는다.
    var STACK = [["suppress", "t-sup"], ["dashboard", "t-dash"], ["ticket", "t-ticket"], ["page", "t-page"]];
    var CHART_H = 128;          // 막대 영역 높이(px) — CSS `.chart` 높이와 같아야 한다
    var MIN_TOTAL_W = 22;       // 막대 폭이 이보다 좁으면 막대 위 합계를 생략(C-4 — 겹침 방지)
    var X_LABEL_W = 64;         // x 라벨 1개가 차지한다고 보는 폭(겹침 방지용 간격 계산)

    // hidden(C-6) — 범례로 숨긴 티어. 범위 전환·60초 갱신에도 유지된다(렌더가 매번 이 표를 본다).
    var chartState = { mode: "count", data: null, bucket: "2h", hidden: {} };
    var chartHost = document.getElementById("chart");

    // 차트 틀(축·격자·라벨 자리)은 두 화면 공통이라 여기서 만든다 — HTML에는 #chart만 둔다.
    var CH = (function buildChart() {
        var card = chartHost.parentNode;
        var title = card.querySelector("h2");
        title.classList.add("chart-title");
        var mode = el("div", "seg seg--sm chart-mode");
        mode.setAttribute("role", "group");
        mode.setAttribute("aria-label", "막대 높이 기준");
        [["count", "건수"], ["ratio", "비율"]].forEach(function (pair) {
            var button = el("button", pair[0] === chartState.mode ? "on" : "", pair[1]);
            button.type = "button";
            button.dataset.mode = pair[0];
            button.setAttribute("aria-pressed", pair[0] === chartState.mode ? "true" : "false");
            button.addEventListener("click", function () {
                chartState.mode = pair[0];
                Array.prototype.forEach.call(mode.children, function (b) {
                    b.classList.toggle("on", b === button);
                    b.setAttribute("aria-pressed", b === button ? "true" : "false");
                });
                renderChart();
            });
            mode.appendChild(button);
        });
        title.appendChild(mode);

        var read = el("p", "chart-read");
        read.id = "chartRead";
        card.insertBefore(read, chartHost);
        var frame = el("div", "chart-frame");
        card.insertBefore(frame, chartHost);
        var yname = el("div", "chart-axis-name");
        frame.appendChild(yname);
        var plot = el("div", "chart-plot");
        var yaxis = el("div", "chart-yaxis");
        yaxis.setAttribute("aria-hidden", "true");
        var area = el("div", "chart-area");
        var grid = el("div", "chart-grid");
        grid.setAttribute("aria-hidden", "true");
        plot.appendChild(yaxis);
        plot.appendChild(area);
        area.appendChild(grid);
        area.appendChild(chartHost);
        frame.appendChild(plot);
        var xaxis = el("div", "chart-xaxis");
        xaxis.setAttribute("aria-hidden", "true");
        frame.appendChild(xaxis);
        frame.appendChild(el("div", "chart-axis-name x", "구간 시작 시각(이 브라우저 시간대)"));
        chartHost.setAttribute("role", "group");
        chartHost.setAttribute("aria-label", "티어 분포 추이 막대 — 막대마다 구간 수치가 있습니다");

        var tip = el("div", "chart-tip");
        tip.id = "chartTip";
        tip.setAttribute("aria-hidden", "true");
        document.body.appendChild(tip);
        return {
            read: read, yname: yname, yaxis: yaxis, grid: grid, xaxis: xaxis, tip: tip,
            legend: document.getElementById("chartLegend"),
        };
    })();

    async function loadTimeseries() {
        var bucket = BUCKET_BY_RANGE[state.range] || "2h";
        // 버킷 격자를 브라우저 로컬 시각에 맞춘다(G-3 (b)) — KST면 2시간 구간이 짝수 시에 시작한다.
        var tz = -new Date().getTimezoneOffset();
        var data = await getJSON(API + "/timeseries?range=" + state.range + "&bucket=" + bucket
            + "&tz_offset_minutes=" + tz);
        if (!data) return;
        chartState.data = data;
        chartState.bucket = bucket;
        renderChart();
    }

    function pointTotal(p) {
        return (p.page || 0) + (p.ticket || 0) + (p.dashboard || 0) + (p.suppress || 0);
    }

    // (C-6) 보이는 티어만의 합 — 막대 높이·막대 위 합계·툴팁 합계·비율 분모가 이 값을 쓴다.
    function visibleTotal(p) {
        return STACK.reduce(function (acc, pair) {
            return acc + (chartState.hidden[pair[0]] ? 0 : (p[pair[0]] || 0));
        }, 0);
    }

    function visibleTiers() {
        return STACK.map(function (pair) { return pair[0]; })
            .filter(function (tier) { return !chartState.hidden[tier]; });
    }

    // 범례 토글 — 네 티어를 모두 숨길 수는 없다(마지막 하나는 토글하지 않는다).
    function toggleTier(tier) {
        if (!chartState.hidden[tier] && visibleTiers().length <= 1) return;
        chartState.hidden[tier] = !chartState.hidden[tier];
        renderChart();
        var again = CH.legend && CH.legend.querySelector('[data-tier="' + tier + '"]');
        if (again) again.focus();
    }

    // 축 최대 — 1·2·5×10ⁿ 계열로 올린다(C-1).
    function niceCeil(value) {
        if (value <= 1) return 1;
        var power = Math.pow(10, Math.floor(Math.log(value) / Math.LN10));
        var m = value / power;
        return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * power;
    }

    function fmtMD(d) { return (d.getMonth() + 1) + "/" + d.getDate(); }
    function fmtHM(d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); }

    // 구간 표기 — `9/22 10:00–12:00`. 자정에 끝나는 구간은 `24:00`, 1일 구간은 양끝 날짜를 적는다.
    function bucketRangeText(iso, bucketSec) {
        var start = new Date(iso);
        if (isNaN(start.getTime())) return String(iso);
        var end = new Date(start.getTime() + bucketSec * 1000);
        if (bucketSec >= 86400) return fmtMD(start) + " " + fmtHM(start) + " – " + fmtMD(end) + " " + fmtHM(end);
        var endsAtMidnight = end.getHours() === 0 && end.getMinutes() === 0 && end.getDate() !== start.getDate();
        return fmtMD(start) + " " + fmtHM(start) + "–" + (endsAtMidnight ? "24:00" : fmtHM(end));
    }

    function tierShares(p, total) {
        return STACK.slice().reverse().filter(function (pair) {
            return !chartState.hidden[pair[0]];
        }).map(function (pair) {
            var count = p[pair[0]] || 0;
            return {
                tier: pair[0], cls: pair[1], count: count,
                pct: total ? Math.round((count / total) * 100) : 0,
            };
        });
    }

    // x 라벨(C-2) — 최대 8개(폭이 좁으면 더 적게), 날짜가 바뀌는 첫 라벨에 M/D. 1일 구간은 M/D만.
    function xLabels(points, bucketSec, width) {
        var n = points.length;
        var maxLabels = width ? Math.max(2, Math.min(8, Math.floor(width / X_LABEL_W))) : 8;
        var step = Math.max(1, Math.ceil(n / maxLabels));
        var pitch = width ? width / n : 0;
        var lastDay = "";
        return points.map(function (p, i) {
            if (i % step) return "";
            if (pitch && (n - i) * pitch < X_LABEL_W - 8) return "";   // 오른쪽 끝을 넘는 라벨 생략
            var d = new Date(p.bucket_ts);
            if (isNaN(d.getTime())) return "";
            if (bucketSec >= 86400) return fmtMD(d);
            var day = d.toDateString();
            var time = d.getMinutes() ? fmtHM(d) : pad2(d.getHours()) + "시";
            var text = day !== lastDay ? fmtMD(d) + " " + time : time;
            lastDay = day;
            return text;
        });
    }

    function renderChart() {
        var data = chartState.data;
        if (!data) return;
        hideChartTip();
        var bucket = chartState.bucket;
        var bucketSec = BUCKET_SECONDS[bucket] || 7200;
        var ratio = chartState.mode === "ratio";
        var points = data.points || [];
        var totals = points.map(visibleTotal);
        var axisMax = ratio ? 100 : niceCeil(Math.max.apply(null, [0].concat(totals)));

        document.getElementById("chartHint").textContent = state.range.toUpperCase() + " · "
            + (BUCKET_WORDS[bucket] || bucket) + " 구간 · 시각은 이 브라우저 기준";
        var lead = ((HELP.items || {}).chart || {}).lead || "";
        CH.read.textContent = lead.replace("{bucket}", BUCKET_WORDS[bucket] || bucket);
        CH.yname.textContent = ratio ? "구간 안 비율(%)" : "판단 수(건/구간)";

        // y 눈금(C-1) — 0 · 중간 · 최대. 건수 보기의 중간값이 정수가 아니면 생략한다.
        var ticks = ratio ? [0, 50, 100] : [0, axisMax / 2, axisMax].filter(function (v) {
            return v === Math.floor(v);
        });
        CH.yaxis.innerHTML = "";
        CH.grid.innerHTML = "";
        ticks.forEach(function (v) {
            var pct = (v / axisMax) * 100;
            var label = el("span", "yl", ratio ? v + "%" : fmtInt(v));
            label.style.bottom = pct + "%";
            CH.yaxis.appendChild(label);
            var line = el("i", "gl" + (v === 0 ? " base" : ""));
            line.style.bottom = pct + "%";
            CH.grid.appendChild(line);
        });

        var width = chartHost.clientWidth || 0;
        var showTotals = !!width && points.length > 0 && (width / points.length) - 3 >= MIN_TOTAL_W;
        var labels = xLabels(points, bucketSec, width);

        chartHost.innerHTML = "";
        CH.xaxis.innerHTML = "";
        points.forEach(function (p, index) {
            var total = totals[index];
            var col = el("div", "col");
            var shares = tierShares(p, total);
            var rangeText = bucketRangeText(p.bucket_ts, bucketSec);
            col.tabIndex = 0;
            col.setAttribute("role", "button");
            col.setAttribute("data-focus-key", "bucket:" + p.bucket_ts);
            col.setAttribute("aria-label", rangeText + " · 합계 " + fmtInt(total) + "건 · "
                + shares.map(function (s) {
                    return TIER_NAMES[s.tier] + " " + fmtInt(s.count) + " (" + s.pct + "%)";
                }).join(" · ") + " — 누르면 이 구간의 판단 목록");

            var barH = 0;
            if (!total) {
                // 0건 구간 — "데이터 없음"이 아니라 0건임을 기준선 위 점으로 보인다(C-9).
                col.appendChild(el("i", "zero-dot"));
            } else {
                var heights = STACK.map(function (pair) {
                    var count = chartState.hidden[pair[0]] ? 0 : (p[pair[0]] || 0);
                    var h = ratio ? (count / total) * CHART_H : (count / axisMax) * CHART_H;
                    return count && h < 1 ? 1 : h;
                });
                barH = heights.reduce(function (a, b) { return a + b; }, 0);
                var target = ratio ? CHART_H : (total / axisMax) * CHART_H;
                // 1px 하한 때문에 넘친 만큼은 가장 큰 조각에서 뺀다 — 막대 합이 축을 넘지 않는다(C-8).
                if (barH > target + 0.01) {
                    var biggest = heights.indexOf(Math.max.apply(null, heights));
                    heights[biggest] -= barH - target;
                    barH = target;
                }
                STACK.forEach(function (pair, i) {
                    if (!heights[i]) return;
                    var seg = el("div", "seg-bar " + pair[1]);
                    seg.style.height = heights[i] + "px";
                    col.appendChild(seg);
                });
            }
            if (showTotals && total) {
                var tot = el("span", "tot", fmtInt(total));
                tot.style.bottom = (barH + 2) + "px";
                col.appendChild(tot);
            }
            // (S7 · C-11) 막대 = 그 구간의 판단 목록 진입점.
            col.addEventListener("click", function () { openBucket(p, bucketSec, col); });
            col.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openBucket(p, bucketSec, col);
                }
            });
            col.addEventListener("mouseenter", function () { showChartTip(col, rangeText, total, shares, barH); });
            col.addEventListener("mouseleave", hideChartTip);
            col.addEventListener("focus", function () { showChartTip(col, rangeText, total, shares, barH); });
            col.addEventListener("blur", hideChartTip);
            chartHost.appendChild(col);

            var cell = el("div", "xl");
            if (labels[index]) cell.appendChild(el("span", null, labels[index]));
            CH.xaxis.appendChild(cell);
        });

        renderLegend(points);
    }

    // 범례(C-6) — 기간 합계 병기 · 순서는 쌓는 순서의 위 → 아래(PAGE부터). 항목이 토글 버튼이다:
    // 누르면(Enter/Space 포함) 그 티어를 막대에서 숨기거나 보인다(`aria-pressed` = 보임).
    // 기간 합계 숫자는 숨김과 무관한 원값이다 — 숨긴 항목은 흐리게만 표시한다.
    function renderLegend(points) {
        if (!CH.legend) return;
        CH.legend.innerHTML = "";
        var visibleCount = visibleTiers().length;
        STACK.slice().reverse().forEach(function (pair) {
            var tier = pair[0];
            var sum = points.reduce(function (acc, p) { return acc + (p[tier] || 0); }, 0);
            var hidden = !!chartState.hidden[tier];
            var item = el("button", "lg-item" + (hidden ? " off" : ""));
            item.type = "button";
            item.setAttribute("data-tier", tier);
            item.setAttribute("aria-pressed", hidden ? "false" : "true");
            var lastVisible = !hidden && visibleCount <= 1;
            item.setAttribute("aria-label", TIER_NAMES[tier] + " 기간 합계 " + fmtInt(sum) + "건 — 막대 "
                + (hidden ? "숨김, 누르면 표시" : lastVisible ? "표시(마지막 티어라 숨길 수 없음)" : "표시, 누르면 숨김"));
            if (lastVisible) item.setAttribute("aria-disabled", "true");
            item.appendChild(el("i", "sw t-" + tier));
            item.appendChild(document.createTextNode(TIER_NAMES[tier] + " "));
            item.appendChild(el("b", null, fmtInt(sum)));
            item.addEventListener("click", function () { toggleTier(tier); });
            CH.legend.appendChild(item);
        });
        CH.legend.appendChild(el("span", "note",
            "기간 합계 · 막대는 아래 SUPPRESS → 위 PAGE 순으로 쌓음 · 범례를 누르면 그 티어를 숨기거나 보임"));
        // (m-2) 시각으로 구간을 정할 수 없어 막대에서 뺀 판단 — KPI에는 세지므로 차이를 밝힌다.
        var excluded = (chartState.data && chartState.data.excluded_no_ts) || 0;
        if (excluded > 0) {
            var ex = el("span", "note note--warn", "시각을 읽을 수 없는 " + fmtInt(excluded)
                + "건은 막대에서 제외(KPI에는 포함)");
            ex.id = "chartExcluded";
            CH.legend.appendChild(ex);
        }
    }

    // 막대 툴팁(C-5) — 네이티브 title 대신. 호버·키보드 포커스 모두에서 뜬다(층 50).
    function showChartTip(col, rangeText, total, shares, barH) {
        var tip = CH.tip;
        tip.innerHTML = "";
        tip.appendChild(el("div", "tt-range", rangeText));
        tip.appendChild(el("div", "tt-total", "합계 " + fmtInt(total) + "건"));
        shares.forEach(function (s) {
            var row = el("div", "tt-row");
            row.appendChild(el("i", "sw t-" + s.tier));
            row.appendChild(document.createTextNode(TIER_NAMES[s.tier] + " " + fmtInt(s.count)
                + " (" + s.pct + "%)"));
            tip.appendChild(row);
        });
        var hiddenNames = STACK.slice().reverse().filter(function (pair) {
            return chartState.hidden[pair[0]];
        }).map(function (pair) { return TIER_NAMES[pair[0]]; });
        if (hiddenNames.length) {
            tip.appendChild(el("div", "tt-hint", "숨긴 티어(합계 제외): " + hiddenNames.join(", ")));
        }
        tip.appendChild(el("div", "tt-hint", "누르면 이 구간의 판단 목록"));
        tip.classList.add("on");
        var r = col.getBoundingClientRect();
        var vw = window.innerWidth || document.documentElement.clientWidth || 0;
        var w = tip.offsetWidth;
        var h = tip.offsetHeight;
        var left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, vw - 8 - w));
        var top = r.bottom - barH - h - 8;
        if (top < 8) top = r.bottom + 8;
        tip.style.left = left + "px";
        tip.style.top = top + "px";
    }

    function hideChartTip() {
        if (!CH.tip.classList.contains("on")) return false;
        CH.tip.classList.remove("on");
        return true;
    }

    // 폭이 바뀌면 라벨 수·합계 표시 여부가 달라진다 — 다시 그린다.
    var chartResizeTimer = null;
    window.addEventListener("resize", function () {
        clearTimeout(chartResizeTimer);
        chartResizeTimer = setTimeout(renderChart, 150);
    });

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
            // (plans/112 S7) 행 → 알람명 정확 일치 + 단계 + 캔슬 티어 목록(총 건수 = 이 행의 수).
            // 알람명이 기록되지 않은 묶음은 알람명으로 거를 수 없어 열지 않는다.
            if (item.alarm_name && item.alarm_name !== "(미기록)") {
                row.setAttribute("data-focus-key", "top:" + item.alarm_name + "|" + item.stage);
                wireDrill(row, row, item.alarm_name + " · " + item.label + " 캔슬 판단 목록 열기", function () {
                    openTopSuppressed(item, row);
                });
            }
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
            node.setAttribute("data-focus-key", "feed:" + feedKey(item));
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

            // 재동기화로 받은 항목은 레코드 전체를 가졌다 — 그 레코드로 그린다(plans/112 F-5).
            // SSE로만 들어온 항목(레코드 없음)만 서버 조회로 폴백한다.
            function open() {
                if (item.record) openDrawerFromRecord(item.record, node);
                else openDrawer(item.alarm_id, node);
            }
            node.addEventListener("click", open);
            node.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
            });
            host.appendChild(node);
        });
    }

    async function resyncFeed() {
        var data = await getJSON(API + "/decisions?range=" + state.range + "&size=" + FEED_MAX
            + "&related=true");
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
                record: item,
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

    // 드로어는 **행의 레코드로** 그린다(plans/112 F-5). `GET /decisions/{alarm_id}`는 같은
    // alarm_id의 가장 최근 판단만 주므로, 목록의 과거 행(재통보 등)을 서버 조회로 그리면 다른
    // 판단이 뜬다. 서버 조회는 레코드가 없는 SSE 피드 항목의 폴백으로만 쓴다.

    var noiseWrap = document.querySelector(".noise-wrap");
    var drawer = document.getElementById("drawer");
    var scrim = document.getElementById("scrim");
    var drawerClose = document.getElementById("closeDrawer");
    var drawerTrace = document.getElementById("drawerTrace");
    var drawerContext = null;
    var drawerReturnFocus = null;

    function sig(item) { return (item && item.signals) || {}; }

    function evidenceOf(item) {
        var value = item && item.stage_evidence;
        return value && typeof value === "object" ? value : {};
    }

    // 심각도3 판단 — 어떤 규칙으로도 침묵되지 않으므로 "이 패턴 침묵" 동선을 주지 않는다.
    function isSev3(item) {
        return sig(item).effective_severity === 3 || (item && item.stage === "severity3");
    }

    // 드로어에 새로 더한 칸(판단 요약·단계 근거)은 두 화면 공통이라 HTML 두 벌에 복제하지
    // 않고 여기서 만든다. 결정 단계 타임라인 앞뒤에 끼운다.
    var drawerFacts = el("table", "signals drawer-facts");
    drawerFacts.id = "drawerFacts";
    var drawerFactsBody = el("tbody");
    drawerFacts.appendChild(drawerFactsBody);
    var drawerTraceHead = el("h2", "drawer-sub", "판단 경로");
    drawerTraceHead.setAttribute("data-help", "drawer.timeline");
    var drawerEvidenceHead = el("h2", "drawer-sub", "단계 근거");
    var drawerEvidence = el("table", "signals");
    drawerEvidence.id = "drawerEvidence";
    var drawerEvidenceBody = el("tbody");
    drawerEvidence.appendChild(drawerEvidenceBody);
    drawerTrace.parentNode.insertBefore(drawerFacts, drawerTrace);
    drawerTrace.parentNode.insertBefore(drawerTraceHead, drawerTrace);
    drawerTrace.parentNode.insertBefore(drawerEvidenceHead, drawerTrace.nextSibling);
    drawerTrace.parentNode.insertBefore(drawerEvidence, drawerEvidenceHead.nextSibling);

    function openDrawerFromRecord(record, trigger) {
        if (!record) return;
        renderDrawer(record, timelineFor(record));
        showDrawer(trigger);
    }

    // 폴백: 레코드가 없는 항목(SSE 피드)만 서버의 최신 판단·타임라인으로 그린다.
    async function openDrawer(alarmId, trigger) {
        if (!alarmId) return;
        try {
            var data = await getJSON(API + "/decisions/" + encodeURIComponent(alarmId));
            if (!data) return;
            renderDrawer(data.decision, data.timeline);
            showDrawer(trigger);
        } catch (err) {
            showError(err.message);
        }
    }

    // 타임라인은 서버 `noise_decision_trace`와 같은 규칙으로 클라이언트가 계산한다 —
    // 순서는 `summary.stages`(= 판정 코드의 STAGE_ORDER, `unknown` 제외)에서 온다.
    function timelineFor(record) {
        var order = (state.stages || []).filter(function (s) { return s.stage !== "unknown"; });
        var decided = -1;
        order.forEach(function (s, i) { if (s.stage === record.stage) decided = i; });
        return order.map(function (s, i) {
            var status = decided < 0 ? "unknown"
                : i < decided ? "passed"
                : i === decided ? "decided"
                : "short_circuited";
            return { stage: s.stage, label: s.label, status: status };
        });
    }

    function addFactRow(body, label, value) {
        if (value === null || value === undefined || value === "") return;
        var row = el("tr");
        row.appendChild(el("td", null, label));
        var cell = el("td");
        if (value instanceof Node) cell.appendChild(value);
        else cell.textContent = String(value);
        row.appendChild(cell);
        body.appendChild(row);
    }

    function renderDrawer(record, timeline) {
        drawerContext = record;
        document.getElementById("drawerName").textContent =
            record.alarm_name || "(알람명 미기록)";
        document.getElementById("drawerMeta").textContent =
            [record.server_name, record.alarm_id, fmtTime(record.ts)].filter(Boolean).join(" · ");

        // 판단 요약 — 값이 있는 칸만 그린다(구 레코드에 없는 필드는 줄을 만들지 않는다).
        drawerFactsBody.innerHTML = "";
        addFactRow(drawerFactsBody, "판단 결과", tierBadge(record.tier));
        addFactRow(drawerFactsBody, "결정 단계", record.stage_label || record.stage);
        addFactRow(drawerFactsBody, "존", record.db_id);
        addFactRow(drawerFactsBody, "자원", record.resource_name);
        // 운영자 경로 응답에만 온다(G-2 (c)) — 사용자 화면은 키 자체가 없어 줄이 생기지 않는다.
        addFactRow(drawerFactsBody, "울린 값", record.condition_log);
        if (record.recurrence && record.recurrence.count) {
            addFactRow(drawerFactsBody, "직전 창 재발", fmtInt(record.recurrence.count) + "회");
        }
        addFactRow(drawerFactsBody, "관련 판단", relatedText(record));

        drawerTrace.innerHTML = "";
        if (!timeline || !timeline.length) {
            drawerTrace.appendChild(el("div", "empty",
                "단계 순서를 아직 불러오지 못했습니다 — 집계가 갱신된 뒤 다시 여십시오."));
        } else {
            var decidedAny = timeline.some(function (step) { return step.status === "decided"; });
            if (!decidedAny) {
                drawerTrace.appendChild(el("div", "rs trace-note",
                    "결정 단계가 기록되지 않은 옛 판단입니다 — 어느 단계에서 확정됐는지 알 수 없습니다."));
            }
            timeline.forEach(function (step, index) {
                var row = el("div", "trace-row " + step.status);
                var mark = el("div", "mark");
                mark.appendChild(el("div", "dot2"));
                if (index < timeline.length - 1) mark.appendChild(el("div", "line"));
                row.appendChild(mark);

                var note = step.status === "decided" ? " — 여기서 결정"
                    : step.status === "short_circuited" ? " (단락 — 평가되지 않음)"
                    : "";
                var text = el("div", "txt", step.label + note);
                if (step.status === "decided" && record.reason) {
                    text.appendChild(el("div", "rs", record.reason));
                }
                row.appendChild(text);
                drawerTrace.appendChild(row);
            });
        }

        // 단계 근거(plans/112 S6) — 결정 단계의 구체 근거만 기록된다. 없으면(구 레코드·근거 없는
        // 단계) 칸을 숨긴다.
        var evidence = evidenceOf(record);
        var evidenceKeys = Object.keys(evidence);
        drawerEvidenceBody.innerHTML = "";
        evidenceKeys.forEach(function (key) {
            addFactRow(drawerEvidenceBody, evidenceLabel(key), evidenceValue(key, evidence[key]));
        });
        drawerEvidenceHead.style.display = evidenceKeys.length ? "" : "none";
        drawerEvidence.style.display = evidenceKeys.length ? "" : "none";

        var signals = document.getElementById("drawerSignals");
        signals.innerHTML = "";
        var snapshot = record.signals || {};
        Object.keys(snapshot).forEach(function (key) {
            var row = el("tr");
            row.appendChild(signalLabelCell(key));
            var value = snapshot[key];
            row.appendChild(el("td", null,
                value === null || value === undefined ? "—" : String(value)));
            signals.appendChild(row);
        });

        // 심각도3은 어떤 규칙으로도 침묵되지 않는다 — 서버가 막지만 헛된 동선을 주지 않는다.
        if (IS_ADMIN) {
            document.getElementById("silenceThisBtn").style.display = isSev3(record) ? "none" : "";
        }
    }

    function showDrawer(trigger) {
        drawerReturnFocus = returnPoint(trigger || document.activeElement);
        drawer.classList.add("on");
        drawer.setAttribute("aria-hidden", "false");
        scrim.classList.add("on");
        syncLayers();
        drawerClose.focus();
    }

    function closeDrawer() {
        if (!drawer.classList.contains("on")) return;
        if (infoOwner && drawer.contains(infoOwner)) closeInfoPop();
        drawer.classList.remove("on");
        drawer.setAttribute("aria-hidden", "true");
        scrim.classList.remove("on");
        syncLayers();
        restoreFocus(drawerReturnFocus);
        drawerReturnFocus = null;
    }

    // (plans/112 m-4) 진입점 복귀 — 노드 · 다시 찾을 키(`data-focus-key`) · 가장 가까운 안정 영역을
    // 함께 기억한다. 60초 갱신·범위 전환·resize가 진입점을 다시 그리면 노드는 문서에서 빠진다 —
    // 그때는 같은 키의 새 노드로, 그것도 없으면 영역 제목으로 보낸다(포커스가 body로 떨어지지 않게).
    function returnPoint(node) {
        if (!node || !node.getAttribute) return null;
        return {
            node: node,
            key: node.getAttribute("data-focus-key"),
            home: node.closest ? node.closest(".card, .kpi, .detail-panel") : null,
        };
    }

    function findFocusKey(key) {
        var nodes = document.querySelectorAll("[data-focus-key]");
        for (var i = 0; i < nodes.length; i++) {
            if (nodes[i].getAttribute("data-focus-key") === key) return nodes[i];
        }
        return null;
    }

    function restoreFocus(point) {
        if (!point) return;
        if (point.node && typeof point.node.focus === "function" && document.body.contains(point.node)) {
            point.node.focus();
            return;
        }
        var again = point.key ? findFocusKey(point.key) : null;
        if (again) { again.focus(); return; }
        var home = point.home;
        if (!home || !document.body.contains(home)) return;
        var target = home.querySelector("h2, h3") || home;
        if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
        target.focus();
    }

    drawerClose.addEventListener("click", closeDrawer);
    scrim.addEventListener("click", closeDrawer);

    // 드로어에서 곧바로 침묵 규칙을 준비한다(억제 교정 동선 — 값만 채우고 저장은 사용자가).
    // 사용자 화면에는 이 버튼이 없다(침묵은 운영 통제라 운영자에게 남는다).
    if (IS_ADMIN) document.getElementById("silenceThisBtn").addEventListener("click", function () {
        prepareSilence(drawerContext);
    });

    function prepareSilence(record) {
        if (!IS_ADMIN || !record || isSev3(record)) return;
        closeDrawer();
        closePanel();
        switchPane("silence");
        document.getElementById("slcServer").value = record.server_name || "";
        document.getElementById("slcAlarm").value = record.alarm_name || "";
        // 자원명이 기록된 판단(plans/112 S6)이면 매처를 그만큼 좁힌다 — 새 초안이므로 없으면 비운다.
        document.getElementById("slcResource").value = record.resource_name || "";
        document.getElementById("slcReason").value = "";
        document.getElementById("slcReason").focus();
        showSuccess("침묵 규칙 초안을 채웠습니다 — 사유를 적고 추가하십시오.");
    }

    // ── 오버레이 층 (plans/112 §2.6) ────────────────────────────────────
    // 층 값(z-index)은 noise-console.css 머리 주석의 표가 정본이다. 여기서는 두 가지만 맡는다:
    //   ① 열린 층 아래를 inert로 — 키보드 포커스가 가려진 층으로 새지 않게
    //   ② Esc·✕·스크림은 **맨 위 층 하나만** 닫는다(팝오버 > 결정 추적 드로어 > 세부 리스트 패널)

    function syncLayers() {
        var drawerOn = drawer.classList.contains("on");
        var panelOn = !!panelState;
        setInert(noiseWrap, drawerOn || panelOn);
        setInert(P.root, drawerOn);
    }

    function closeTopLayer() {
        if (closeInfoPop()) return true;
        if (hideChartTip()) return true;
        if (drawer.classList.contains("on")) { closeDrawer(); return true; }
        if (panelState) { closePanel(); return true; }
        return false;
    }

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && closeTopLayer()) e.preventDefault();
    });

    // ── 근거 해석 (목록 칸·드로어 공용) ─────────────────────────────────
    // S6 근거(`stage_evidence`)가 있으면 그것을, 없는 구 레코드는 현재 기록(signals·사유·
    // correlation_meta·related)으로 폴백한다. 판정은 서버가 했다 — 여기서는 읽기만 한다.

    var HELP = window.NOISE_HELP || {};

    function evidenceLabel(key) {
        var labels = HELP.evidence || {};
        return labels[key] || key;
    }

    // 신호 스냅샷 16키 — 영문 키 대신 한국어 이름·설명을 앞에 둔다(키는 대조용으로 남긴다).
    function signalLabelCell(key) {
        var info = (HELP.signals || {})[key];
        var cell = el("td");
        if (!info) {
            cell.textContent = key;
            return cell;
        }
        cell.appendChild(el("div", "sig-name", info.label));
        cell.appendChild(el("div", "sig-key", key));
        if (info.desc) cell.appendChild(el("div", "sig-desc", info.desc));
        return cell;
    }

    var EVIDENCE_VALUE_WORDS = {
        mode: { multi_hop: "다홉", one_hop: "1홉" },
        corroborated_by: { resolution: "해소", correlation: "상관", change_nearby: "변경 근접" },
    };

    function evidenceValue(key, value) {
        var words = EVIDENCE_VALUE_WORDS[key] || {};
        if (Array.isArray(value)) {
            return value.length
                ? value.map(function (v) { return words[v] || String(v); }).join(", ")
                : "(없음)";
        }
        if (value === true) return "예";
        if (value === false) return "아니오";
        if (value === null || value === undefined || value === "") return "—";
        if (key === "expires_at") return fmtTime(value);
        if (key === "base_tier") return TIER_NAMES[value] || String(value);
        if (/_seconds$/.test(key)) return fmtDuration(value);
        return words[value] || String(value);
    }

    // 관련 판단(`related=true` — 상관 대표·자가복구 원 발생). 못 찾으면 조용히 비우지 않는다
    // (서버가 `found: false`를 준다 — "범위 밖"과 "없음"을 화면이 구분해 보인다).
    // withKind=false는 칸 머리가 이미 종류를 말하는 목록 칸용이다.
    function relatedText(item, withKind) {
        var rel = item && item.related;
        if (!rel || typeof rel !== "object") return "";
        var representative = rel.kind === "representative";
        var kind = representative ? "대표 판단" : "원 발생 판단";
        if (!rel.found) {
            if (representative && !(item.correlation_meta && item.correlation_meta.representative_fp)) {
                return "대표 정보가 기록되지 않은 판단입니다";
            }
            return representative
                ? "대표 판단이 조회 범위(결정 기록 최근 구간) 밖입니다"
                : "원 발생 판단을 조회 범위(결정 기록 최근 구간)에서 찾지 못했습니다";
        }
        var text = [rel.alarm_name || "(알람명 미기록)", rel.server_name,
            fmtShortTime(rel.ts), TIER_NAMES[rel.tier] || rel.tier].filter(Boolean).join(" · ");
        return withKind === false ? text : kind + " · " + text;
    }

    // 매트릭스 사유 `매트릭스(심각도2×중요도보통) → ticket · 승격: … → 최종 page`에서
    // 산식 조각을 뽑는다(구 레코드 폴백 — S6 레코드는 stage_evidence에 구조화돼 있다).
    function parseMatrixReason(reason) {
        var m = /^매트릭스\(심각도(\d+)×중요도([^)]+)\) → (\w+)(.*?) → 최종 (\w+)/.exec(reason || "");
        if (!m) return null;
        var rest = m[4] || "";
        var promote = /· 승격: (.*?)(?: \(강등 신호|$)/.exec(rest);
        var demote = /· 강등: (.*)$/.exec(rest);
        return {
            severity: m[1], importance: m[2], base: m[3], final: m[5],
            promote: promote ? promote[1] : "", demote: demote ? demote[1] : "",
        };
    }

    function matrixFormula(item) {
        var s = sig(item);
        var e = evidenceOf(item);
        if (e.base_tier) {
            return "심각도" + s.effective_severity + "×중요도 " + s.importance + " → "
                + (TIER_NAMES[e.base_tier] || e.base_tier) + " → 최종 " + (TIER_NAMES[item.tier] || item.tier);
        }
        var p = parseMatrixReason(item.reason);
        if (!p) return "";
        return "심각도" + p.severity + "×중요도 " + p.importance + " → "
            + (TIER_NAMES[p.base] || p.base) + " → 최종 " + (TIER_NAMES[p.final] || p.final);
    }

    function matrixAdjust(item) {
        if (item.stage !== "matrix") return "";
        var e = evidenceOf(item);
        var promote = e.promote;
        var demote = e.demote;
        if (!Array.isArray(promote) && !Array.isArray(demote)) {
            var p = parseMatrixReason(item.reason);
            if (!p) return "";
            promote = p.promote ? [p.promote] : [];
            demote = p.demote ? [p.demote] : [];
        }
        promote = promote || [];
        demote = demote || [];
        if (promote.length) {
            return "승격: " + promote.join(", ")
                + (demote.length ? " (강등 신호 " + demote.join(", ") + "는 승격 우선으로 무시)" : "");
        }
        if (demote.length) return "강등: " + demote.join(", ");
        return "조정 없음";
    }

    function sevChain(item) {
        var s = sig(item);
        if (s.effective_severity === undefined) return "";
        var raised = s.ai_severity !== null && s.ai_severity !== undefined
            && Number(s.effective_severity) > Number(s.severity);
        return raised
            ? "실효 " + s.effective_severity + " (수신 " + s.severity + " → AI " + s.ai_severity + " 상향)"
            : "실효 " + s.effective_severity;
    }

    function silenceRuleId(item) {
        var e = evidenceOf(item);
        if (e.rule_id) return String(e.rule_id);
        var m = /^침묵 규칙\(([^)]+)\)/.exec(item.reason || "");
        return m ? m[1] : "";
    }

    function silenceRuleReason(item) {
        return String(item.reason || "").replace(/^침묵 규칙\([^)]*\)\s*—\s*/, "");
    }

    function depMode(item) {
        var e = evidenceOf(item);
        if (e.mode) return EVIDENCE_VALUE_WORDS.mode[e.mode] || e.mode;
        return sig(item).cascaded ? "다홉" : "1홉";
    }

    function depRoot(item) {
        return evidenceOf(item).root_resource_name || sig(item).root_resource || "";
    }

    function depResult(item) {
        if (item.tier === "dashboard") return "화면 표시 — 근본원인 미통보";
        if (depMode(item) === "다홉") return "억제 — 근본원인 통보됨";
        return "억제 — 부모 비정상(연쇄 노이즈)";
    }

    // 단계별 근거를 한 칸에 요약한다(SUPPRESS·DASHBOARD 목록의 "단계 근거" 칸 · §2.2).
    function stageBasis(item) {
        var e = evidenceOf(item);
        var s = sig(item);
        var meta = item.correlation_meta || {};
        switch (item.stage) {
        case "non_alarm":
            return Array.isArray(e.markers) && e.markers.length ? "표지 단어: " + e.markers.join(", ") : "";
        case "self_heal":
            if (e.heal_seconds !== undefined) {
                return "발생→해소 " + fmtDuration(e.heal_seconds)
                    + (e.fired_severity !== undefined ? " · 발생 심각도 " + e.fired_severity : "");
            }
            return item.related && item.related.found ? relatedText(item) : "";
        case "resolved":
            return item.tier === "dashboard" ? "짝 없는 해소 — 화면 표시 설정" : "짝 없는 해소 — 기록만";
        case "maintenance":
            return s.maintenance ? "유지보수 모드 신호" : "";
        case "silence":
            var ruleId = silenceRuleId(item);
            return ruleId ? "규칙 " + ruleId + (e.expires_at ? " · 만료 " + fmtShortTime(e.expires_at) : "") : "";
        case "dependency":
            return depMode(item) + (depRoot(item) ? " · 근본원인 " + depRoot(item) : "") + " · " + depResult(item);
        case "inhibition":
            return e.inhibitor ? "상위 알람 " + e.inhibitor
                + (e.inhibitor_severity !== undefined ? "(심각도 " + e.inhibitor_severity + ")" : "") : "";
        case "flapping":
            return e.flap_percent !== undefined ? "변화율 " + e.flap_percent + "% (상한 " + e.high + "%)" : "";
        case "storm":
            return e.window_count !== undefined ? "창 안 " + e.window_count + "건 / 임계 " + e.threshold + "건" : "";
        case "correlation":
            if (item.related) return relatedText(item);
            return meta.member_seq !== undefined ? "멤버 #" + meta.member_seq : "";
        case "annotation":
            return Array.isArray(e.corroborated_by) && e.corroborated_by.length
                ? "뒷받침: " + evidenceValue("corroborated_by", e.corroborated_by) : "";
        case "matrix":
            return matrixFormula(item);
        default:
            return "";
        }
    }

    // ── 설명 팝오버 (plans/112 S4 · §2.7) ───────────────────────────────
    // 항목 옆 (!) 버튼 → 클릭 팝오버. 호버 툴팁을 쓰지 않는 이유: 여러 줄 설명을 키보드·터치로도
    // 열어야 한다. 한 번에 하나만 열고, 같은 버튼·Esc·바깥 클릭으로 닫는다. 팝오버는 읽기 전용이라
    // 포커스를 옮기지 않는다(스크린리더는 role=dialog + aria-label). 문구 데이터는 noise-help.js,
    // 퍼널 단계 설명만 API(summary.stages[].description)에서 받는다.

    var infoPop = el("div", "info-pop");
    infoPop.id = "infoPop";
    infoPop.setAttribute("role", "dialog");
    document.body.appendChild(infoPop);
    var infoOwner = null;

    var HELP_ROWS = [["def", "무엇을 보나"], ["formula", "산식"], ["read", "어떻게 읽나"], ["caution", "주의"]];

    // 항목 문구 + 화면 모드 덮어쓰기(admin/user 하위 객체).
    function helpItem(key) {
        var items = HELP.items || {};
        var base = items[key];
        if (!base) return null;
        var merged = {};
        var override = base[IS_ADMIN ? "admin" : "user"] || {};
        [base, override].forEach(function (src) {
            Object.keys(src).forEach(function (k) {
                if (k !== "admin" && k !== "user") merged[k] = src[k];
            });
        });
        return merged;
    }

    function stageHelp(key) {
        var meta = stageMeta(key);
        var keys = [];
        if (meta && meta.enable_key) keys.push(meta.enable_key);
        ((HELP.stageSettings || {})[key] || []).forEach(function (k) { keys.push(k); });
        var off = stageOffNote(meta);
        return {
            title: meta ? meta.label : key,
            def: meta && meta.description ? meta.description : "설명을 불러오지 못했습니다(집계 조회 뒤 다시 여십시오).",
            status: off || (meta ? { text: "현재 설정에서 평가하는 단계입니다", keys: [] } : null),
            read: "행을 누르면 이 단계에서 확정된 판단 목록이 열립니다.",
            // 꺼진 단계는 상태 줄이 이미 켜는 설정을 보인다 — 관련 설정 목록에서 겹치지 않게 뺀다.
            keys: keys.filter(function (k) { return !off || off.keys.indexOf(k) < 0; }),
        };
    }

    function helpContent(key) {
        if (String(key).indexOf("stage:") === 0) return stageHelp(key.slice(6));
        return helpItem(key) || { title: key, def: "설명이 아직 없습니다." };
    }

    function keyList(host, keys) {
        keys.forEach(function (k, i) {
            if (i) host.appendChild(document.createTextNode(" · "));
            host.appendChild(el("code", null, k));
        });
    }

    function renderInfoPop(content) {
        infoPop.innerHTML = "";
        infoPop.appendChild(el("h4", null, content.title));
        if (content.status) {
            var status = el("p", "st" + (content.status.keys.length ? " off" : ""), content.status.text);
            if (content.status.keys.length) {
                status.appendChild(document.createTextNode(" · 켜는 설정 "));
                keyList(status, content.status.keys);
            }
            infoPop.appendChild(status);
        }
        var dl = el("dl");
        HELP_ROWS.forEach(function (pair) {
            if (!content[pair[0]]) return;
            dl.appendChild(el("dt", null, pair[1]));
            dl.appendChild(el("dd", null, content[pair[0]]));
        });
        if (content.keys && content.keys.length) {
            dl.appendChild(el("dt", null, "관련 설정"));
            var dd = el("dd");
            keyList(dd, content.keys);
            dl.appendChild(dd);
        }
        infoPop.appendChild(dl);
    }

    // 버튼 근처에 두고 화면 가장자리에서 뒤집는다(아래 → 위 · 오른쪽 넘침 → 왼쪽으로 당김).
    function positionInfoPop(button) {
        var margin = 16;
        var vw = window.innerWidth || document.documentElement.clientWidth || 0;
        var vh = window.innerHeight || document.documentElement.clientHeight || 0;
        var r = button.getBoundingClientRect();
        var w = infoPop.offsetWidth;
        var h = infoPop.offsetHeight;
        var left = Math.max(margin, Math.min(r.left, vw - margin - w));
        var top = r.bottom + 8;
        if (top + h > vh - margin && r.top - 8 - h >= margin) top = r.top - 8 - h;
        infoPop.style.left = left + "px";
        infoPop.style.top = Math.max(margin, top) + "px";
    }

    function openInfoPop(button) {
        closeInfoPop();
        var content = helpContent(button.dataset.helpKey);
        renderInfoPop(content);
        infoPop.setAttribute("aria-label", content.title + " 설명");
        infoPop.classList.add("on");
        button.setAttribute("aria-expanded", "true");
        infoOwner = button;
        positionInfoPop(button);
    }

    function closeInfoPop() {
        if (!infoOwner) return false;
        infoOwner.setAttribute("aria-expanded", "false");
        infoOwner = null;
        infoPop.classList.remove("on");
        return true;
    }

    function makeInfoButton(key, label) {
        var button = el("button", "info-btn", "!");
        button.type = "button";
        button.dataset.helpKey = key;
        button.setAttribute("aria-label", label + " 설명");
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-controls", "infoPop");
        // 행·카드 안에 있어도 클릭이 드릴다운으로 번지지 않게 한다(§2.7).
        button.addEventListener("click", function (e) {
            e.stopPropagation();
            if (infoOwner === button) closeInfoPop();
            else openInfoPop(button);
        });
        button.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") e.stopPropagation();
        });
        return button;
    }

    // HTML의 `data-help` 표지에 (!)를 붙인다 — 두 화면에 버튼 마크업을 복제하지 않는다.
    // 표지가 버튼(관리 탭)이면 버튼 안이 아니라 바로 뒤에 둔다(버튼 안 버튼 금지).
    function ownText(node) {
        var text = "";
        Array.prototype.forEach.call(node.childNodes, function (child) {
            if (child.nodeType === 3) text += child.nodeValue;
        });
        return text.trim();
    }

    function attachHelp(root) {
        Array.prototype.forEach.call(root.querySelectorAll("[data-help]"), function (node) {
            if (node.getAttribute("data-help-bound")) return;
            node.setAttribute("data-help-bound", "1");
            var button = makeInfoButton(node.getAttribute("data-help"), ownText(node) || "항목");
            if (node.tagName === "BUTTON") {
                node.parentNode.insertBefore(button, node.nextSibling);
                return;
            }
            // 제목 글자 바로 뒤(힌트 앞)에 둔다.
            var lastText = null;
            Array.prototype.forEach.call(node.childNodes, function (child) {
                if (child.nodeType === 3 && child.nodeValue.trim()) lastText = child;
            });
            node.insertBefore(button, lastText ? lastText.nextSibling : null);
        });
    }

    // 바깥 클릭으로 닫는다. 스크림을 눌렀으면 그 클릭은 팝오버만 닫고 멈춘다 —
    // "맨 위 층 하나만 닫는다"(팝오버 > 드로어 > 패널).
    document.addEventListener("click", function (e) {
        if (!infoOwner) return;
        if (infoPop.contains(e.target) || infoOwner.contains(e.target)) return;
        closeInfoPop();
        if (e.target.classList && e.target.classList.contains("scrim")) {
            e.stopPropagation();
            e.preventDefault();
        }
    }, true);
    window.addEventListener("resize", closeInfoPop);
    document.addEventListener("scroll", function (e) {
        if (infoOwner && !infoPop.contains(e.target)) closeInfoPop();
    }, true);

    // ── 세부 리스트 패널 (plans/112 §2.1~§2.3) ──────────────────────────
    // 모든 드릴다운이 **한 패널**을 쓴다 — 항목마다 다른 것은 필터와 열 프로파일뿐이다
    // (항목마다 화면을 만들면 한쪽만 고쳐지는 비대칭이 생긴다). 목록·총 건수·분포 칩은
    // `GET /decisions` 한 번의 응답(items·total·facets)에서 나온다 — 별도 집계 호출이 없다.
    // 두 화면의 열 프로파일은 같고, 동작 칸만 모드에 따라 다르다(침묵은 운영자 화면에만).

    var PANEL_SIZE = 50;

    // 칸 정의. render는 문자열·숫자·노드를 돌려주고, 빈 값은 "—"로 그린다.
    // when이 있는 칸은 **이 쪽의 행 중 하나라도 값이 있을 때만** 만든다 — 구 레코드만 있는 쪽에
    // 빈 칸 열을 두지 않는다(특히 `condition_log`는 사용자 경로 응답에 키 자체가 없다 — G-2 (c)).
    var COLS = {
        time: { head: "시각", cls: "k nowrap", render: function (it) { return fmtShortTime(it.ts); } },
        alarm: { head: "알람", render: function (it) { return it.alarm_name || "(알람명 미기록)"; } },
        server: { head: "서버", cls: "k", render: function (it) { return it.server_name; } },
        zone: {
            head: "존", cls: "k",
            when: function (it) { return !!it.db_id; },
            render: function (it) { return it.db_id; },
        },
        resource: {
            head: "자원", cls: "k",
            when: function (it) { return !!it.resource_name; },
            render: function (it) { return it.resource_name; },
        },
        tier: { head: "티어", render: function (it) { return tierBadge(it.tier); } },
        stage: { head: "결정 단계", render: function (it) { return it.stage_label || it.stage; } },
        reason: { head: "사유", cls: "wrap", render: function (it) { return it.reason; } },
        sev: { head: "실효 심각도", render: function (it) { return sig(it).effective_severity; } },
        sevChain: { head: "심각도", cls: "nowrap", render: sevChain },
        importance: { head: "중요도", render: function (it) { return sig(it).importance; } },
        promote: {
            head: "승격 근거", cls: "wrap",
            render: function (it) {
                if (it.stage !== "matrix") return "";
                var adjust = matrixAdjust(it);
                return adjust.indexOf("승격: ") === 0 ? adjust.slice(4) : "";
            },
        },
        recurrence: {
            head: "직전 창 재발",
            render: function (it) {
                return it.recurrence && it.recurrence.count ? fmtInt(it.recurrence.count) + "회" : "";
            },
        },
        conditionLog: {
            head: "울린 값", cls: "wrap k", admin: true,
            when: function (it) { return !!it.condition_log; },
            render: function (it) { return it.condition_log; },
        },
        matrix: { head: "매트릭스 산식", cls: "nowrap", render: matrixFormula },
        adjust: { head: "조정", cls: "wrap", render: matrixAdjust },
        notiPolicy: {
            head: "통보 정책",
            render: function (it) {
                var v = sig(it).noti_policy;
                return v === "notify" ? "통보(notify)" : v === "suppress" ? "비통보(suppress)" : v;
            },
        },
        routine: {
            head: "일상 패턴",
            render: function (it) {
                var s = sig(it);
                var routine = s.is_routine === true ? "일상 반복" : s.is_routine === false ? "비일상" : "";
                return [routine, s.pattern].filter(Boolean).join(" · ");
            },
        },
        basis: { head: "근거", cls: "wrap", render: stageBasis },

        // ── 퍼널 단계 전용 칸(§2.3) ──
        evMarkers: {
            head: "걸린 표지 단어",
            when: function (it) { return Array.isArray(evidenceOf(it).markers); },
            render: function (it) { return evidenceValue("markers", evidenceOf(it).markers); },
        },
        origin: {
            head: "원 발생 판단", cls: "wrap",
            render: function (it) { return relatedText(it, false); },
        },
        heal: {
            head: "발생→해소 소요",
            when: function (it) { return evidenceOf(it).heal_seconds !== undefined; },
            render: function (it) {
                var e = evidenceOf(it);
                if (e.heal_seconds === undefined) return "";
                return fmtDuration(e.heal_seconds)
                    + (e.fired_severity !== undefined ? " · 발생 심각도 " + e.fired_severity : "");
            },
        },
        resolvedHandling: {
            head: "처리", cls: "wrap",
            render: function (it) {
                return it.tier === "dashboard"
                    ? "화면 표시(DASHBOARD) — 설정 NOISE_RESOLVED_TO_DASHBOARD"
                    : "기록만(SUPPRESS)";
            },
        },
        missing: {
            head: "결측 처리", cls: "wrap",
            render: function () { return "부가 신호 미수집 → 중요도 '보통'으로 간주 · 유지보수 미확인"; },
        },
        maint: {
            head: "유지보수 신호",
            render: function (it) { return sig(it).maintenance ? "유지보수 중" : ""; },
        },
        ruleId: { head: "규칙 ID", cls: "k", render: silenceRuleId },
        ruleReason: { head: "규칙 사유", cls: "wrap", render: silenceRuleReason },
        ruleSnapshot: {
            head: "판단 시점 규칙", cls: "wrap",
            when: function (it) {
                var e = evidenceOf(it);
                return !!(e.matcher_summary || e.expires_at || e.created_by);
            },
            render: function (it) {
                var e = evidenceOf(it);
                return [e.matcher_summary, e.expires_at ? "만료 " + fmtShortTime(e.expires_at) : "",
                    e.created_by ? "생성 " + e.created_by : ""].filter(Boolean).join(" · ");
            },
        },
        // 운영자 화면에서만 — 침묵 저장소를 대조한다(사용자 경로에는 침묵 API가 없다).
        ruleStatus: {
            head: "현재 규칙 상태", admin: true,
            render: function (it, ps) {
                var id = silenceRuleId(it);
                if (!id) return "";
                if (!ps.silences) return "조회 실패";
                var rule = ps.silences[id];
                if (!rule) return "목록에 없음";
                if (rule.revoked_at) return "해제됨";
                return rule.active ? "활성 · " + fmtShortTime(rule.expires_at) + " 만료" : "만료됨";
            },
        },
        parentStatus: {
            head: "부모 가용 상태",
            render: function (it) {
                var v = sig(it).parent_avail_status;
                if (v === null || v === undefined) return "미수집";
                return v === 0 ? "정상(0)" : "비정상(" + v + ")";
            },
        },
        depMode: { head: "연쇄 판정", render: depMode },
        rootResource: { head: "근본원인 자원", cls: "k", render: depRoot },
        depResult: { head: "결과", cls: "wrap", render: depResult },
        evInhibition: {
            head: "누른 상위 알람", cls: "wrap",
            when: function (it) { return !!evidenceOf(it).inhibitor; },
            render: function (it) {
                var e = evidenceOf(it);
                if (!e.inhibitor) return "";
                return e.inhibitor + (e.inhibitor_severity !== undefined ? " (심각도 " + e.inhibitor_severity + ")" : "")
                    + " · " + fmtDuration(e.age_seconds) + " 전 발생 · 창 " + fmtDuration(e.window_seconds);
            },
        },
        evFlapping: {
            head: "진동 정도", cls: "wrap",
            when: function (it) { return evidenceOf(it).flap_percent !== undefined; },
            render: function (it) {
                var e = evidenceOf(it);
                if (e.flap_percent === undefined) return "";
                return "상태 변화율 " + e.flap_percent + "% (상한 " + e.high + "% · 하한 " + e.low + "%) · 표본 "
                    + e.samples;
            },
        },
        evStorm: {
            head: "몰린 정도", cls: "wrap",
            when: function (it) { return evidenceOf(it).window_count !== undefined; },
            render: function (it) {
                var e = evidenceOf(it);
                if (e.window_count === undefined) return "";
                return "창 안 " + e.window_count + "건 / 임계 " + e.threshold + "건 · 창 "
                    + fmtDuration(e.window_seconds);
            },
        },
        representative: {
            head: "대표 알람", cls: "wrap",
            render: function (it) { return relatedText(it, false); },
        },
        memberSeq: {
            head: "멤버 순번",
            render: function (it) {
                var meta = it.correlation_meta || {};
                return meta.member_seq !== undefined ? "#" + meta.member_seq : "";
            },
        },
        similarity: {
            head: "유사도",
            render: function (it) {
                var meta = it.correlation_meta || {};
                return typeof meta.similarity === "number" ? meta.similarity.toFixed(2) : "";
            },
        },
        evAnnotation: {
            head: "뒷받침한 근거",
            when: function (it) { return Array.isArray(evidenceOf(it).corroborated_by); },
            render: function (it) { return evidenceValue("corroborated_by", evidenceOf(it).corroborated_by); },
        },
        llm: {
            head: "LLM 액션가능성",
            render: function (it) {
                var v = sig(it).llm_actionability;
                return v === "actionable" ? "액션 필요" : v === "noise" ? "노이즈" : v;
            },
        },
    };

    // 퍼널 단계 프로파일(§2.3) — 공통 칸(시각·알람·서버) 뒤에 붙는다. 칩은 티어 분포다.
    var STAGE_PROFILES = {
        non_alarm: { question: "알람이 아니라고 본 근거는", cols: ["reason", "evMarkers"] },
        severity3: {
            question: "무엇이 억제 없이 곧바로 PAGE였나",
            cols: ["sevChain", "importance", "conditionLog"],
        },
        self_heal: {
            question: "무엇이 스스로 복구됐고 얼마나 걸렸나",
            cols: ["origin", "heal"], heads: { alarm: "해소 알람" },
        },
        resolved: { question: "짝 없는 해소를 어떻게 처리했나", cols: ["resolvedHandling"] },
        collection_failed: { question: "어떤 신호가 없어 보수적으로 불렀나", cols: ["sev", "missing"] },
        maintenance: { question: "유지보수 중이라 억제된 것은", cols: ["maint", "sev", "resource"] },
        silence: {
            question: "어느 규칙이 무엇을 조용히 시켰나",
            cols: ["ruleId", "ruleReason", "ruleSnapshot", "ruleStatus"],
        },
        dependency: {
            question: "어느 상위 장애의 연쇄로 봤나",
            cols: ["parentStatus", "depMode", "rootResource", "depResult"],
        },
        inhibition: { question: "어떤 상위 알람이 누르고 있었나", cols: ["reason", "evInhibition"] },
        flapping: { question: "얼마나 진동했나", cols: ["reason", "evFlapping"] },
        storm: { question: "얼마나 몰렸나", cols: ["reason", "evStorm"] },
        correlation: {
            question: "어느 대표 알람에 묶였나",
            cols: ["representative", "memberSeq", "similarity"],
        },
        annotation: { question: "무엇이 계획 작업임을 뒷받침했나", cols: ["reason", "evAnnotation"] },
        matrix: {
            question: "산식이 어떻게 흘렀나",
            cols: ["tier", "matrix", "adjust", "notiPolicy", "routine", "llm"],
        },
        unknown: { question: "어느 단계에서 확정됐는지 분류되지 않은 옛 판단", cols: ["tier", "reason"] },
    };

    function stageMeta(key) {
        return (state.stages || []).filter(function (s) { return s.stage === key; })[0] || null;
    }

    // 꺼진 단계 안내(§2.3 · S4) — 게이트 자체가 꺼지면 전 단계가 꺼진다(API `enabled`).
    function stageOffNote(meta) {
        if (!meta || meta.enabled !== false) return null;
        var keys = [];
        if (state.summary && state.summary.gate_enabled === false) keys.push("NOISE_ENABLE_NOISE_GATE");
        if (meta.enable_key) keys.push(meta.enable_key);
        return { text: "이 단계는 꺼져 있어 판단을 내리지 않습니다", keys: keys };
    }

    function openStage(key, trigger) {
        var meta = stageMeta(key);
        var prof = STAGE_PROFILES[key] || STAGE_PROFILES.unknown;
        openPanel({
            id: "stage:" + key,
            title: "퍼널 단계 · " + (meta ? meta.label : key),
            question: prof.question,
            // 단계 설명은 판정 코드 옆 도메인 정본을 API로 받는다(summary.stages[].description).
            lead: meta && meta.description ? meta.description : "",
            note: function () { return stageOffNote(stageMeta(key)); },
            help: "stage:" + key,
            chips: ["tier"],
            formula: "stage",
            cols: ["time", "alarm", "server"].concat(prof.cols),
            heads: prof.heads || {},
            base: { tier: [], stage: key, alarm_name: "", since: "", until: "" },
            silences: key === "silence" && IS_ADMIN,
            emptyText: function (ps) {
                if (ps.q || ps.chipTier) return "이 조건의 판단이 없습니다.";
                var m = stageMeta(key);
                return m && m.enabled === false
                    ? "이 단계는 꺼져 있어 이 기간에 여기서 확정된 판단이 없습니다."
                    : "이 기간에 이 단계에서 확정된 판단이 없습니다(해당 없음).";
            },
        }, trigger);
    }

    // (S7) 상위 억제 알람 유형 행 — 상위 목록은 캔슬 티어(SUPPRESS·DASHBOARD)만 세므로
    // 같은 티어 집합으로 거른다(그래야 총 건수가 행의 수와 같다).
    function openTopSuppressed(item, trigger) {
        var meta = stageMeta(item.stage);
        var prof = STAGE_PROFILES[item.stage] || STAGE_PROFILES.unknown;
        var cols = ["time", "alarm", "server"];
        if (prof.cols.indexOf("tier") < 0) cols.push("tier");
        openPanel({
            id: "top",
            title: item.alarm_name + " · " + item.label,
            question: "이 알람이 이 단계에서 왜 캔슬됐나",
            lead: meta && meta.description ? meta.description : "",
            help: "top",
            chips: ["tier"],
            cols: cols.concat(prof.cols),
            heads: prof.heads || {},
            base: {
                tier: ["dashboard", "suppress"], stage: item.stage, alarm_name: item.alarm_name,
                since: "", until: "",
            },
            silences: item.stage === "silence" && IS_ADMIN,
        }, trigger);
    }

    // (S7 · C-11) 추이 막대 — 그 구간(since ≤ ts < until)의 판단. 총 건수 = 막대 합계.
    function openBucket(point, bucketSec, trigger) {
        var start = new Date(point.bucket_ts);
        if (isNaN(start.getTime())) return;
        var until = new Date(start.getTime() + bucketSec * 1000).toISOString().replace(/\.\d{3}Z$/, "+00:00");
        openPanel({
            id: "bucket",
            title: "구간 " + bucketRangeText(point.bucket_ts, bucketSec) + " 판단",
            question: "이 구간에 무엇이 들어와 어디로 갔나",
            help: "panel.bucket",
            chips: ["tier", "stage"],
            cols: PROFILES.raw.cols,
            // 범례로 숨긴 티어가 있으면 보이는 티어로 거른다 — 패널 총 건수 = 막대 합계(C-6).
            base: {
                tier: visibleTiers().length < STACK.length ? visibleTiers() : [],
                stage: "", alarm_name: "", since: point.bucket_ts, until: until,
            },
        }, trigger);
    }

    // 운영자 화면 전용 — 침묵 단계 패널의 "현재 규칙 상태" 대조(만료·해제분 포함 목록).
    async function loadSilenceIndex() {
        var data = await getJSON(API + "/silences?include_inactive=true");
        var index = {};
        ((data && data.items) || []).forEach(function (rule) { index[rule.id] = rule; });
        return index;
    }

    // KPI·티어 카드 프로파일(§2.2) — 각 프로파일은 "이 목록이 답하는 질문" 하나에 맞춰 칸을 고른다.
    var PROFILES = {
        raw: {
            title: "수신 알람", question: "무엇이 들어와 어디로 갔나 — 게이트가 판단한 전체",
            help: "kpi.raw", tier: [], chips: ["tier", "stage"],
            cols: ["time", "alarm", "server", "zone", "resource", "sev", "importance", "tier", "stage", "reason"],
        },
        page: {
            title: "PAGE — 즉시 통보", question: "왜 즉시 불렀나",
            help: "kpi.page", tier: ["page"], chips: ["stage"],
            cols: ["time", "alarm", "server", "stage", "sevChain", "importance", "promote", "recurrence",
                "conditionLog"],
            heads: { stage: "결정 경로" },
        },
        ticket: {
            title: "TICKET — 대기열·기록", question: "어떤 산식으로 대기열에 갔나",
            help: "kpi.ticket", tier: ["ticket"], chips: ["stage"],
            cols: ["time", "alarm", "server", "matrix", "adjust", "notiPolicy", "routine"],
        },
        dashboard: {
            title: "DASHBOARD — 화면 표시만", question: "왜 통보 없이 화면에만 남았나",
            help: "kpi.dashboard", tier: ["dashboard"], chips: ["stage"],
            cols: ["time", "alarm", "server", "stage", "basis", "sev", "importance"],
            heads: { stage: "강등 경로", basis: "경로별 근거" },
        },
        suppress: {
            title: "SUPPRESS — 억제된 알람", question: "어떤 알람을 왜 억제했나",
            help: "kpi.suppress", tier: ["suppress"], chips: ["stage"],
            cols: ["time", "alarm", "server", "stage", "reason", "basis", "sev"],
            heads: { stage: "억제 단계", reason: "억제 사유", basis: "단계 근거" },
        },
        suppress_ratio: {
            like: "suppress", title: "억제율 — SUPPRESS ÷ 수신", help: "kpi.ratio", formula: "suppress",
        },
        actionable: {
            like: "page", title: "액션가능 — PAGE + TICKET", question: "사람이 움직여야 했던 것",
            help: "kpi.ratio", tier: ["page", "ticket"], chips: ["tier", "stage"], formula: "actionable",
            cols: ["time", "alarm", "server", "tier", "stage", "sevChain", "importance", "promote",
                "recurrence", "conditionLog"],
        },
    };

    function profileSpec(key) {
        var own = PROFILES[key];
        if (!own) return null;
        var base = own.like ? PROFILES[own.like] : {};
        var spec = {};
        [base, own].forEach(function (src) {
            Object.keys(src).forEach(function (k) { spec[k] = src[k]; });
        });
        spec.id = key;
        spec.base = { tier: spec.tier || [], stage: "", alarm_name: "", since: "", until: "" };
        return spec;
    }

    function openProfile(key, trigger) {
        var spec = profileSpec(key);
        if (spec) openPanel(spec, trigger);
    }

    // 패널 DOM — 두 화면 공통이라 HTML에 복제하지 않고 여기서 만든다.
    var P = (function buildPanel() {
        var root = el("aside", "detail-panel");
        root.id = "detailPanel";
        root.setAttribute("role", "dialog");
        root.setAttribute("aria-modal", "true");
        root.setAttribute("aria-labelledby", "panelTitle");
        root.setAttribute("aria-hidden", "true");

        var close = el("button", "x", "✕");
        close.type = "button";
        close.id = "panelClose";
        close.setAttribute("aria-label", "세부 리스트 닫기");
        root.appendChild(close);

        var head = el("div", "dp-head");
        var bar = el("div", "dp-titlebar");
        var title = el("h3");
        var titleText = el("span", null, "—");
        title.id = "panelTitle";
        title.appendChild(titleText);
        // 패널 헤더 (!) — 열린 항목의 설명(openPanel이 키를 바꾼다).
        var info = makeInfoButton("panel.bucket", "세부 리스트");
        title.appendChild(info);
        bar.appendChild(title);
        var scope = el("span", "dp-scope");
        scope.setAttribute("aria-live", "polite");
        bar.appendChild(scope);
        var range = el("div", "seg seg--sm");
        range.id = "panelRange";
        range.setAttribute("role", "group");
        range.setAttribute("aria-label", "조회 범위");
        ["1h", "24h", "7d", "30d"].forEach(function (value) {
            var button = el("button", value === state.range ? "on" : "", value.toUpperCase());
            button.type = "button";
            button.dataset.range = value;
            button.setAttribute("aria-pressed", value === state.range ? "true" : "false");
            range.appendChild(button);
        });
        bar.appendChild(range);
        head.appendChild(bar);
        var question = el("p", "dp-question");
        var lead = el("p", "dp-lead");
        var note = el("p", "dp-note");
        var formula = el("p", "dp-formula");
        var conds = el("p", "dp-conds");
        var chips = el("div", "dp-chips");
        head.appendChild(question);
        head.appendChild(lead);
        head.appendChild(note);
        head.appendChild(formula);
        head.appendChild(conds);
        head.appendChild(chips);

        var search = el("div", "dp-search");
        var input = el("input");
        input.type = "search";
        input.id = "panelSearch";
        input.placeholder = "알람명·서버명·사유 검색";
        input.setAttribute("aria-label", "세부 리스트 검색");
        var go = el("button", "btn btn-secondary", "조회");
        go.type = "button";
        search.appendChild(input);
        search.appendChild(go);
        head.appendChild(search);
        root.appendChild(head);

        var body = el("div", "dp-body");
        var table = el("table", "tbl dp-tbl");
        var thead = el("thead");
        var headRow = el("tr");
        thead.appendChild(headRow);
        var tbody = el("tbody");
        table.appendChild(thead);
        table.appendChild(tbody);
        var status = el("div", "empty");
        status.setAttribute("aria-live", "polite");
        body.appendChild(table);
        body.appendChild(status);
        root.appendChild(body);

        var pager = el("div", "dp-pager");
        var prev = el("button", "mini-btn", "‹ 이전");
        prev.type = "button";
        var pageInfo = el("span");
        var next = el("button", "mini-btn", "다음 ›");
        next.type = "button";
        pager.appendChild(prev);
        pager.appendChild(pageInfo);
        pager.appendChild(next);
        root.appendChild(pager);

        var scrimPanel = el("div", "scrim scrim--panel");
        scrimPanel.id = "panelScrim";
        document.body.appendChild(scrimPanel);
        document.body.appendChild(root);

        return {
            root: root, scrim: scrimPanel, close: close, title: titleText, info: info, bar: bar,
            scope: scope, range: range,
            question: question, lead: lead, note: note, formula: formula, conds: conds, chips: chips,
            input: input, go: go, table: table, headRow: headRow, tbody: tbody, status: status,
            pager: pager, prev: prev, next: next, pageInfo: pageInfo,
        };
    })();

    var panelState = null;     // {spec, chipTier, chipStage, q, page, data, trigger}
    var panelSeq = 0;          // 늦게 도착한 응답이 새 조회를 덮지 않게 하는 순번

    function openPanel(spec, trigger) {
        panelState = {
            spec: spec, chipTier: "", chipStage: "", q: "", page: 1, data: null,
            trigger: returnPoint(trigger || document.activeElement),
        };
        P.input.value = "";
        P.root.classList.add("on");
        P.root.setAttribute("aria-hidden", "false");
        P.scrim.classList.add("on");
        syncLayers();
        renderPanelHead();
        P.close.focus();
        loadPanel();
    }

    function closePanel() {
        if (!panelState) return;
        if (infoOwner && P.root.contains(infoOwner)) closeInfoPop();
        var trigger = panelState.trigger;
        panelState = null;
        panelSeq += 1;
        P.root.classList.remove("on");
        P.root.setAttribute("aria-hidden", "true");
        P.scrim.classList.remove("on");
        syncLayers();
        restoreFocus(trigger);
    }

    function panelFilters(ps) {
        var base = ps.spec.base;
        return {
            tier: ps.chipTier ? [ps.chipTier] : base.tier,
            stage: ps.chipStage || base.stage,
            alarm_name: base.alarm_name,
            since: base.since,
            until: base.until,
            q: ps.q,
        };
    }

    function panelParams(ps) {
        var f = panelFilters(ps);
        var params = ["range=" + state.range, "size=" + PANEL_SIZE, "page=" + ps.page, "related=true"];
        if (f.tier.length) params.push("tier=" + encodeURIComponent(f.tier.join(",")));
        if (f.stage) params.push("stage=" + encodeURIComponent(f.stage));
        if (f.alarm_name) params.push("alarm_name=" + encodeURIComponent(f.alarm_name));
        if (f.since) params.push("since=" + encodeURIComponent(f.since));
        if (f.until) params.push("until=" + encodeURIComponent(f.until));
        if (f.q) params.push("q=" + encodeURIComponent(f.q));
        return params;
    }

    function stageLabel(key) {
        var found = (state.stages || []).filter(function (s) { return s.stage === key; })[0];
        return found ? found.label : key;
    }

    // 현재 조건을 사람이 읽는 한 줄로(빈 결과 안내·헤더 조건 줄 공용).
    function describeFilters(ps, includeRange) {
        var f = panelFilters(ps);
        var parts = includeRange ? ["범위 " + state.range.toUpperCase()] : [];
        if (f.tier.length) {
            parts.push("티어 " + f.tier.map(function (t) { return TIER_NAMES[t] || t; }).join("·"));
        }
        if (f.stage) parts.push("단계 " + stageLabel(f.stage));
        if (f.alarm_name) parts.push("알람명 = " + f.alarm_name);
        if (f.since || f.until) parts.push("구간 " + fmtShortTime(f.since) + "–" + fmtShortTime(f.until));
        if (f.q) parts.push("검색 '" + f.q + "'");
        return parts.join(" · ");
    }

    async function loadPanel() {
        var ps = panelState;
        if (!ps) return;
        var seq = ++panelSeq;
        P.status.textContent = "불러오는 중…";
        P.status.style.display = "";
        try {
            // 침묵 단계(운영자 화면)는 규칙 저장소를 함께 읽는다 — 실패해도 목록은 그린다.
            var silences = ps.spec.silences
                ? loadSilenceIndex().catch(function () { return null; })
                : Promise.resolve(null);
            var data = await getJSON(API + "/decisions?" + panelParams(ps).join("&"));
            var index = await silences;
            if (seq !== panelSeq || ps !== panelState || !data) return;
            ps.data = data;
            ps.silences = index;
            renderPanel();
        } catch (err) {
            if (seq !== panelSeq || ps !== panelState) return;
            ps.data = null;
            renderPanel(err.message);
        }
    }

    function renderPanelHead() {
        var ps = panelState;
        var spec = ps.spec;
        P.title.textContent = spec.title;
        P.info.dataset.helpKey = spec.help || "panel.bucket";
        P.info.setAttribute("aria-label", spec.title + " 설명");
        P.question.textContent = spec.question ? "이 목록이 답하는 질문 — " + spec.question : "";
        P.question.style.display = spec.question ? "" : "none";
        var lead = spec.lead || "";
        P.lead.textContent = lead;
        P.lead.style.display = lead ? "" : "none";
        // 꺼진 단계 안내 — 켜는 설정 env 키는 글자로만 보인다(설정 화면 딥링크는 해시를 받는
        // 코드가 없어 링크를 걸어도 그 키로 가지 않는다 — plans/112 F-2).
        var note = spec.note ? spec.note() : null;
        P.note.innerHTML = "";
        if (note) {
            P.note.appendChild(document.createTextNode(note.text));
            if (note.keys.length) {
                P.note.appendChild(document.createTextNode(" · 켜는 설정 "));
                note.keys.forEach(function (key, i) {
                    if (i) P.note.appendChild(document.createTextNode(" + "));
                    P.note.appendChild(el("code", null, key));
                });
            }
        }
        P.note.style.display = note ? "" : "none";
    }

    function renderPanel(errorMessage) {
        var ps = panelState;
        var spec = ps.spec;
        var data = ps.data;
        renderPanelHead();

        var total = data ? data.total : 0;
        P.scope.textContent = data ? "총 " + fmtInt(total) + "건" : "—";

        var formula = data ? panelFormula(ps) : "";
        P.formula.textContent = formula;
        P.formula.style.display = formula ? "" : "none";
        var conds = describeFilters(ps, false);
        P.conds.textContent = conds ? "조건: " + conds : "";
        P.conds.style.display = conds ? "" : "none";

        renderChips(ps);
        renderPanelTable(ps, errorMessage);

        var pages = Math.max(1, Math.ceil(total / PANEL_SIZE));
        P.pageInfo.textContent = ps.page + " / " + pages + "쪽 · " + PANEL_SIZE + "건/쪽";
        P.prev.disabled = ps.page <= 1;
        P.next.disabled = ps.page >= pages;
        P.pager.style.display = data && total > PANEL_SIZE ? "" : "none";
    }

    // 비율 항목의 산식 줄 — 같은 응답의 facets.tiers(티어 필터만 뺀 집합)에서 센다.
    // 칩·검색 조건이 붙으면 그 조건 안의 비율이다.
    function panelFormula(ps) {
        var spec = ps.spec;
        var facets = ps.data && ps.data.facets && ps.data.facets.tiers;
        if (!spec.formula || !facets) return "";
        var raw = TIER_KEYS.reduce(function (sum, k) { return sum + (facets[k] || 0); }, 0);
        if (spec.formula === "stage") {
            // 퍼널 행의 "종결(캔슬)"과 같은 뜻 — 캔슬 = 통보하지 않은 결과(DASHBOARD·SUPPRESS).
            var cancelled = (facets.dashboard || 0) + (facets.suppress || 0);
            return "이 단계에서 확정 " + fmtInt(raw) + "건 · 그중 캔슬 " + fmtInt(cancelled)
                + "건(DASHBOARD " + fmtInt(facets.dashboard) + " + SUPPRESS " + fmtInt(facets.suppress) + ")"
                + (ps.q ? " · 현재 조건 기준" : "");
        }
        var scoped = ps.chipStage || ps.q ? " · 현재 조건 기준" : "";
        if (spec.formula === "suppress") {
            return "SUPPRESS " + fmtInt(facets.suppress) + " ÷ 수신 " + fmtInt(raw) + " = "
                + (raw ? fmtPct((facets.suppress || 0) / raw) : "—") + scoped;
        }
        var actionable = (facets.page || 0) + (facets.ticket || 0);
        return "(PAGE " + fmtInt(facets.page) + " + TICKET " + fmtInt(facets.ticket) + ") ÷ 수신 "
            + fmtInt(raw) + " = " + (raw ? fmtPct(actionable / raw) : "—") + scoped;
    }

    // 분포 칩 — 티어 항목이면 단계 분포, 단계 항목이면 티어 분포(§2.1). 칩 클릭 = 필터 추가(AND).
    // 같은 묶음 안의 칩은 하나만 고른다(티어·단계는 한 판단에 하나뿐이라 둘을 AND하면 0건이다).
    function renderChips(ps) {
        P.chips.innerHTML = "";
        var facets = ps.data && ps.data.facets;
        if (!facets) return;
        var spec = ps.spec;
        if (spec.chips.indexOf("tier") >= 0) {
            var tierKeys = spec.base.tier.length ? spec.base.tier : TIER_KEYS;
            P.chips.appendChild(chipRow("티어", tierKeys.map(function (t) {
                return { key: t, label: TIER_NAMES[t], count: (facets.tiers || {})[t] || 0, tier: t };
            }), ps.chipTier, function (key) {
                ps.chipTier = key;
                ps.page = 1;
                loadPanel();
            }));
        }
        if (spec.chips.indexOf("stage") >= 0) {
            var stageCounts = facets.stages || {};
            var ordered = (state.stages || []).map(function (s) { return s.stage; });
            Object.keys(stageCounts).forEach(function (k) { if (ordered.indexOf(k) < 0) ordered.push(k); });
            var items = ordered.filter(function (k) { return stageCounts[k]; }).map(function (k) {
                return { key: k, label: stageLabel(k), count: stageCounts[k] };
            });
            P.chips.appendChild(chipRow("단계", items, ps.chipStage, function (key) {
                ps.chipStage = key;
                ps.page = 1;
                loadPanel();
            }));
        }
    }

    function chipRow(label, items, active, onPick) {
        var row = el("div", "chip-row");
        row.setAttribute("role", "group");
        row.setAttribute("aria-label", label + " 분포");
        row.appendChild(el("span", "lab", label));
        var sum = items.reduce(function (acc, item) { return acc + item.count; }, 0);
        [{ key: "", label: "전체", count: sum }].concat(items).forEach(function (item) {
            var chip = el("button", "chip" + (item.key === active ? " on" : "")
                + (item.count ? "" : " is-zero"));
            chip.type = "button";
            chip.setAttribute("aria-pressed", item.key === active ? "true" : "false");
            if (item.tier) chip.appendChild(el("i", "sw t-" + item.tier));
            chip.appendChild(document.createTextNode(item.label));
            chip.appendChild(el("b", null, fmtInt(item.count)));
            chip.addEventListener("click", function () {
                // 켜진 칩을 다시 누르면 해제한다.
                onPick(item.key === active ? "" : item.key);
            });
            row.appendChild(chip);
        });
        return row;
    }

    function panelColumns(spec, items) {
        return spec.cols.filter(function (id) {
            var col = COLS[id];
            if (!col) return false;
            if (col.admin && !IS_ADMIN) return false;
            if (col.when) return items.some(col.when);
            return true;
        });
    }

    function cellNode(value) {
        var cell = el("td");
        if (value instanceof Node) cell.appendChild(value);
        else if (value === null || value === undefined || value === "") {
            cell.textContent = "—";
            cell.classList.add("cell-muted");
        } else cell.textContent = String(value);
        return cell;
    }

    function renderPanelTable(ps, errorMessage) {
        var spec = ps.spec;
        var items = ps.data ? ps.data.items : [];
        var cols = panelColumns(spec, items);
        var heads = spec.heads || {};

        P.headRow.innerHTML = "";
        cols.forEach(function (id) {
            var th = el("th", null, heads[id] || COLS[id].head);
            th.scope = "col";
            P.headRow.appendChild(th);
        });
        var actionHead = el("th", null, "동작");
        actionHead.scope = "col";
        P.headRow.appendChild(actionHead);

        P.tbody.innerHTML = "";
        if (errorMessage) {
            P.table.style.display = "none";
            P.status.style.display = "";
            P.status.textContent = "조회하지 못했습니다 — " + errorMessage;
            return;
        }
        if (!items.length) {
            P.table.style.display = "none";
            P.status.style.display = "";
            P.status.textContent = (spec.emptyText ? spec.emptyText(ps) : "이 조건의 판단이 없습니다.")
                + " (" + describeFilters(ps, true) + ")";
            return;
        }
        P.table.style.display = "";
        P.status.style.display = "none";
        items.forEach(function (item) {
            var row = el("tr", "row");
            cols.forEach(function (id) {
                var col = COLS[id];
                var cell = cellNode(col.render(item, ps));
                if (col.cls) col.cls.split(" ").forEach(function (c) { cell.classList.add(c); });
                row.appendChild(cell);
            });
            var act = actionCell(item, row);
            row.appendChild(act);
            // 행 클릭(마우스) = 그 행의 판단으로 결정 추적. 키보드는 동작 칸의 버튼으로 연다.
            // 포커스 복귀 대상은 행이 아니라 그 버튼이다(행은 포커스를 받지 않는다 — m-4).
            var traceBtn = act.querySelector("button");
            row.addEventListener("click", function () { openDrawerFromRecord(item, traceBtn || row); });
            P.tbody.appendChild(row);
        });
    }

    function actionCell(item, row) {
        var cell = el("td", "row-act");
        var name = item.alarm_name || "알람";
        var trace = el("button", "mini-btn", "결정 추적");
        trace.type = "button";
        trace.setAttribute("data-focus-key", "prow:" + String(item.alarm_id || "") + "|" + String(item.ts || ""));
        trace.setAttribute("aria-label", name + " 결정 추적");
        trace.addEventListener("click", function (e) {
            e.stopPropagation();
            openDrawerFromRecord(item, trace);
        });
        cell.appendChild(trace);
        // 침묵은 운영자 화면에서만 · 심각도3 판단에는 주지 않는다(§2.2).
        if (IS_ADMIN && !isSev3(item)) {
            var silence = el("button", "mini-btn", "이 패턴 침묵");
            silence.type = "button";
            silence.setAttribute("aria-label", name + " 패턴 침묵 초안 만들기");
            silence.addEventListener("click", function (e) {
                e.stopPropagation();
                prepareSilence(item);
            });
            cell.appendChild(silence);
        }
        return cell;
    }

    P.close.addEventListener("click", closePanel);
    P.scrim.addEventListener("click", closePanel);
    P.go.addEventListener("click", function () {
        if (!panelState) return;
        panelState.q = P.input.value.trim();
        panelState.page = 1;
        loadPanel();
    });
    P.input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") P.go.click();
    });
    P.prev.addEventListener("click", function () {
        if (!panelState || panelState.page <= 1) return;
        panelState.page -= 1;
        loadPanel();
    });
    P.next.addEventListener("click", function () {
        if (!panelState) return;
        panelState.page += 1;
        loadPanel();
    });

    // KPI 카드·퍼널 머리 수치(HTML의 `data-drill` 표지) — 억제율/액션가능 카드는 클릭 영역이
    // 둘이다(큰 숫자 → SUPPRESS, 부제 → PAGE+TICKET · §2.2).
    var DRILL_LABELS = {
        raw: "수신 알람", page: "PAGE", ticket: "TICKET", dashboard: "DASHBOARD",
        suppress: "SUPPRESS", suppress_ratio: "억제율(SUPPRESS)", actionable: "액션가능(PAGE+TICKET)",
    };
    document.querySelectorAll("[data-drill]").forEach(function (node) {
        var key = node.getAttribute("data-drill");
        var target = node.classList.contains("kpi") ? node.querySelector(".num") : node;
        target.setAttribute("data-focus-key", "drill:" + key);
        wireDrill(node, target, (DRILL_LABELS[key] || key) + " 세부 리스트 열기", function (trigger) {
            openProfile(key, trigger);
        });
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
            "related=true",
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
            // 그 행의 판단으로 그린다(plans/112 F-5) — 같은 alarm_id의 과거 행도 제 판단이 뜬다.
            row.tabIndex = 0;
            row.setAttribute("data-focus-key", "dec:" + String(item.alarm_id || "") + "|" + String(item.ts || ""));
            row.addEventListener("click", function () { openDrawerFromRecord(item, row); });
            row.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openDrawerFromRecord(item, row);
                }
            });
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
        // `[data-pane]` — 탭 사이에 (!) 설명 버튼이 끼어 있다(plans/112 S4).
        document.querySelectorAll(".mgmt-tabs button[data-pane]").forEach(function (button) {
            button.classList.toggle("on", button.dataset.pane === name);
        });
        document.querySelectorAll(".pane").forEach(function (pane) {
            pane.classList.toggle("on", pane.id === "pane-" + name);
        });
        if (name === "decisions") loadDecisions();
        if (name === "policy") loadPolicy();
        if (name === "silence") loadSilences();
    }

    // 관리 탭(침묵·결정 이력·정책)은 운영자 화면에만 있다 — 사용자 화면은 결정 이력을 바로 편다.
    document.querySelectorAll(".mgmt-tabs button[data-pane]").forEach(function (button) {
        button.addEventListener("click", function () { switchPane(button.dataset.pane); });
    });

    // 범위는 헤더와 세부 리스트 패널 헤더 두 곳에서 바꾼다 — 패널이 열리면 헤더가 스크림
    // 아래로 가려지므로, 패널 안에서도 같은 범위 상태를 바꿀 수 있어야 §2.1의 "범위 전환 시
    // 열린 패널 재조회"가 성립한다. 두 선택기는 같은 state.range 하나를 본다.
    function setRange(range) {
        // 이미 고른 범위를 다시 눌러도 종전처럼 새로 조회한다(수동 새로고침 동선).
        if (!range) return;
        state.range = range;
        document.querySelectorAll("#rangeSeg button, #panelRange button").forEach(function (b) {
            b.classList.toggle("on", b.dataset.range === range);
            b.setAttribute("aria-pressed", b.dataset.range === range ? "true" : "false");
        });
        refreshAggregates();
        // 열린 세부 리스트는 같은 필터로 새 범위를 다시 조회한다(§2.1).
        if (panelState) {
            panelState.page = 1;
            loadPanel();
        }
    }

    function onRangeClick(event) {
        var button = event.target.closest("button[data-range]");
        if (button) setRange(button.dataset.range);
    }

    document.getElementById("rangeSeg").addEventListener("click", onRangeClick);
    P.range.addEventListener("click", onRangeClick);

    if (IS_ADMIN) {
        document.getElementById("addSilenceBtn").addEventListener("click", addSilence);
        document.getElementById("showInactive").addEventListener("change", loadSilences);
    }
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
        // 운영자 화면은 SSE가 즉시성을 맡고 이 호출이 완전성을 맡는다.
        // 사용자 화면은 SSE가 없어 이 호출 하나가 피드 전부다 — 실패를 삼키지 않는다.
        resyncFeed().catch(function (err) {
            if (!IS_ADMIN) showError("결정 이력 조회 실패: " + err.message);
        });
        if (!IS_ADMIN) loadDecisions().catch(function () { /* 조회 버튼으로 재시도된다 */ });
    }

    // (!) 설명 버튼 — HTML `data-help` 표지(두 화면 각자 가진 카드만)와 드로어 추가 칸에 붙인다.
    attachHelp(document);

    refreshAggregates();
    if (IS_ADMIN) {
        loadHealth().catch(function (err) { showError("헬스 조회 실패: " + err.message); });
        loadSilences().catch(function () { /* 탭 진입 시 재시도된다 */ });
        connectStream();
    } else {
        // 사용자 화면은 스트림을 열지 않는다(D-196 ⑤ 유지) — 갱신 주기를 그대로 알린다.
        setStreamPill("ok", Math.round(RESYNC_MS / 1000) + "초마다 갱신");
    }

    setInterval(function () {
        if (IS_ADMIN) loadHealth().catch(function () {});
        loadSummary().catch(function () {});
    }, SUMMARY_MS);
    setInterval(function () {
        resyncFeed().catch(function () {});
    }, RESYNC_MS);
})();
