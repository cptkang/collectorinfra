/**
 * 조회 결과 보조 표시 — plans/160 D(표 넓게 보기) · A(그래프 보기) · B(용어 사전) · C(값 해석).
 *
 * 공개 객체는 `window.ResultViz` 하나다.
 *
 *   ResultViz.enhanceTables(rootEl)
 *     답 말풍선 안 표(마크다운 표 · 데이터 격자)를 래퍼로 감싸고, 표가 말풍선보다 넓을 때만
 *     「넓게 보기」 버튼을 단다. 같은 요소에 여러 번 불러도 래퍼·버튼이 늘지 않는다(멱등).
 *     렌더 완료 지점(renderAgentMessage · finalizeStreamingMessage)에서만 부른다 —
 *     스트리밍 토큰 렌더는 매 프레임 innerHTML을 갈아엎으므로 그 중간에는 붙이지 않는다.
 *     서버를 부르지 않는다(D-327 ⑦⑧ · 플래그 없음).
 *
 *   ResultViz.attach(bubbleEl, data, opts)
 *     응답에 `chart` 신호가 있고 결과 id(`data.query_id || data.result_query_id`)가 있으면 버튼 줄에
 *     「그래프로 보기」를 단다. 신호가 null이면(플래그 off 포함) 아무것도 만들지 않는다 = 현행 화면.
 *     opts.headers: 인증 헤더 객체를 돌려주는 함수(app.js getAuthHeaders) — 서버 호출에 싣는다.
 *     패널은 처음 펼칠 때 `GET /api/v1/query/{id}/chart`를 1회 부른다. 데이터 격자의 머리글·셀은
 *     `explain_enabled`일 때만 링크가 되고, 누르면 용어 사전(glossary)·값 해석(interpret) 팝오버를 연다.
 *     서버·LLM이 준 문자열은 모두 textContent로만 넣는다(XSS — 값·칼럼명·LLM 문장 모두 비신뢰).
 */
(function () {
    "use strict";

    // 마크다운 표 + 앞으로 생길 데이터 격자(plans/160 A)를 같은 함수로 처리한다.
    var TABLE_SELECTOR = ".response-text table, table.result-viz-grid";
    var WRAP_CLASS = "result-table-wrap";
    var BTN_CLASS = "table-expand-btn";
    // `:has()` 미지원 브라우저 대비 — 표가 든 답 말풍선에 다는 표지(CSS가 두 선택자를 함께 쓴다)
    var WIDE_CLASS = "has-wide-content";

    var resizeObserver = null;   // 모든 래퍼가 공유하는 관찰자 1개
    var pending = [];            // 다음 프레임에 다시 판정할 래퍼
    var rafScheduled = false;
    var openOverlay = null;      // { root, trigger, onKey }

    // ─── 넘침 판정 ───

    function isOverflowing(wrap, table) {
        // 마크다운 표는 표 자신이 가로 스크롤 상자(display:block)다. 격자는 래퍼 밖으로 넘칠 수 있다.
        return table.scrollWidth > table.clientWidth + 1 || wrap.scrollWidth > wrap.clientWidth + 1;
    }

    function updateButton(wrap) {
        var table = wrap.querySelector(":scope > table");
        var btn = wrap.querySelector(":scope > ." + BTN_CLASS);
        if (!table || !btn) return;
        // 화면에 없는 동안(대화 복원 직후 숨은 뷰 등)은 폭이 0이다 — 보일 때 관찰자가 다시 부른다.
        btn.hidden = !(wrap.clientWidth > 0 && isOverflowing(wrap, table));
    }

    function flushPending() {
        rafScheduled = false;
        var list = pending;
        pending = [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].isConnected) updateButton(list[i]);
        }
    }

    function getObserver() {
        if (resizeObserver || typeof ResizeObserver === "undefined") return resizeObserver;
        // 버튼 표시 전환은 래퍼 높이를 바꾼다 — 콜백 안에서 바로 바꾸면 관찰 루프 경고가 나므로
        // 다음 프레임으로 미룬다.
        resizeObserver = new ResizeObserver(function (entries) {
            for (var i = 0; i < entries.length; i++) {
                if (pending.indexOf(entries[i].target) === -1) pending.push(entries[i].target);
            }
            if (!rafScheduled) {
                rafScheduled = true;
                window.requestAnimationFrame(flushPending);
            }
        });
        return resizeObserver;
    }

    // ─── 래퍼·버튼 ───

    function ensureWrap(table) {
        var wrap = table.parentElement;
        if (wrap && wrap.classList.contains(WRAP_CLASS)) return wrap;
        wrap = document.createElement("div");
        wrap.className = WRAP_CLASS;
        table.parentNode.insertBefore(wrap, table);
        var btn = document.createElement("button");
        btn.type = "button";               // id는 달지 않는다 — 매뉴얼 역방향 가드가 `<button id>`를 잡는다
        btn.className = BTN_CLASS;
        btn.textContent = "넓게 보기";
        btn.title = "표를 화면 전체 폭으로 보기";
        btn.hidden = true;
        btn.addEventListener("click", function () { openTableOverlay(table, btn); });
        wrap.appendChild(btn);
        wrap.appendChild(table);
        var ro = getObserver();
        if (ro) ro.observe(wrap);
        return wrap;
    }

    function enhanceTables(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        var tables = rootEl.querySelectorAll(TABLE_SELECTOR);
        for (var i = 0; i < tables.length; i++) {
            var wrap = ensureWrap(tables[i]);
            updateButton(wrap);
            var content = wrap.closest(".message-content");
            if (content) content.classList.add(WIDE_CLASS);
        }
    }

    // ─── 오버레이 ───

    function closeTableOverlay() {
        if (!openOverlay) return;
        var o = openOverlay;
        openOverlay = null;
        // 복제 격자에서 연 팝오버는 오버레이와 함께 닫는다 — 남으면 트리거가 사라져 위치가 튄다(plans/160 B·C)
        closePopoverWithin(o.root);
        document.removeEventListener("keydown", o.onKey, true);
        if (o.root.parentNode) o.root.parentNode.removeChild(o.root);
        // 대화 스크롤 위치가 움직이지 않도록 스크롤 없이 포커스만 되돌린다.
        if (o.trigger && o.trigger.isConnected) o.trigger.focus({ preventScroll: true });
    }

    function openTableOverlay(table, trigger) {
        closeTableOverlay();

        var root = document.createElement("div");
        root.className = "table-expand-overlay";
        root.setAttribute("role", "dialog");
        root.setAttribute("aria-modal", "true");
        root.setAttribute("aria-label", "표 넓게 보기");
        // 다음 Wave의 위임 리스너(머리글·셀 클릭)가 원 결과를 알아볼 수 있게 원 표의 data-*를 옮긴다.
        for (var key in table.dataset) {
            if (Object.prototype.hasOwnProperty.call(table.dataset, key)) root.dataset[key] = table.dataset[key];
        }

        var dialog = document.createElement("div");
        dialog.className = "table-expand-dialog";

        var head = document.createElement("div");
        head.className = "table-expand-head";
        var title = document.createElement("span");
        title.className = "table-expand-title";
        title.textContent = "표 넓게 보기";
        var closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "table-expand-close";
        closeBtn.setAttribute("aria-label", "닫기 (Esc)");
        closeBtn.textContent = "닫기";
        closeBtn.addEventListener("click", closeTableOverlay);
        head.appendChild(title);
        head.appendChild(closeBtn);

        // 원본 DOM은 옮기지 않는다 — 복제본만 띄운다(대화 스크롤 위치 보존).
        var body = document.createElement("div");
        body.className = "table-expand-body response-text";
        body.appendChild(table.cloneNode(true));

        dialog.appendChild(head);
        dialog.appendChild(body);
        root.appendChild(dialog);

        // 바깥(배경) 클릭으로 닫는다 — 다이얼로그 안 클릭은 그대로 둔다.
        root.addEventListener("click", function (e) {
            if (e.target === root) closeTableOverlay();
        });

        var onKey = function (e) {
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                closeTableOverlay();
            }
        };
        document.addEventListener("keydown", onKey, true);

        document.body.appendChild(root);
        openOverlay = { root: root, trigger: trigger, onKey: onKey };
        closeBtn.focus({ preventScroll: true });
    }

    // ═══ plans/160 A·B·C — 그래프 패널 · 용어 사전 · 값 해석 ═══

    var API_BASE = "/api/v1/query/";
    var GRID_CLASS = "result-viz-grid";
    var LINK_CLASS = "result-viz-link";
    var OPEN_CLASS = "result-viz-open";     // 패널을 펼친 답의 .message-content 표지
    var CHART_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/></svg>';
    // 시리즈 색 — 테마 토큰을 그릴 때마다 읽는다(라이트/다크가 각자 대비를 맞춘 값)
    var SERIES_TOKENS = ["--accent", "--secondary", "--tone-amber", "--tone-rose", "--tone-sky",
        "--tone-violet", "--tone-lime", "--tone-orange", "--info", "--warning"];

    var SEC = 1000, MIN = 60 * SEC, HOUR = 60 * MIN, DAY = 24 * HOUR;
    var TIME_STEPS = [SEC, 5 * SEC, 10 * SEC, 15 * SEC, 30 * SEC, MIN, 2 * MIN, 5 * MIN, 10 * MIN,
        15 * MIN, 30 * MIN, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 30 * DAY];

    var headerFn = null;         // 인증 헤더 공급자(app.js getAuthHeaders)
    var livePanels = [];         // 그래프가 그려진 패널 — 테마가 바뀌면 다시 그린다
    var themeObserver = null;
    var requestCache = {};       // 요청 키 → Promise — 같은 용어·해석 요청은 다시 부르지 않는다
    var openPop = null;          // { el, trigger, body, onKey, onDown, onMove }
    var delegated = false;

    // ─── 서버 호출 ───

    function requestJson(url, method, payload) {
        var headers = {};
        var base = headerFn ? headerFn() : null;
        if (base) {
            for (var k in base) {
                if (Object.prototype.hasOwnProperty.call(base, k)) headers[k] = base[k];
            }
        }
        var init = { method: method || "GET", headers: headers };
        if (payload !== undefined) {
            headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(payload);
        }
        return fetch(url, init).then(function (res) {
            return res.json().catch(function () { return null; }).then(function (body) {
                if (res.ok && body) return body;
                // 서버 detail을 그대로 보인다(만료·권한 등) — 문자열이 아니면(검증 오류 목록 등) 상태 코드로 대신한다
                var detail = body && typeof body.detail === "string" && body.detail
                    ? body.detail : "요청을 처리하지 못했습니다 (HTTP " + res.status + ")";
                throw new Error(detail);
            });
        }, function (e) {
            throw new Error("서버와 통신하지 못했습니다 — " + (e && e.message ? e.message : "연결 실패"));
        });
    }

    function cachedRequest(key, make) {
        if (!requestCache[key]) {
            // 실패는 캐시하지 않는다 — 다시 누르면 재시도한다
            requestCache[key] = make().catch(function (e) {
                delete requestCache[key];
                throw e;
            });
        }
        return requestCache[key];
    }

    // ─── 작은 DOM 도우미 ───

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function cellText(v) {
        if (v == null) return "";
        if (typeof v === "object") {
            try { return JSON.stringify(v); } catch (e) { return String(v); }
        }
        return String(v);
    }

    // ─── 시간 서식 — 서버가 벽시계 시각을 그대로 epoch ms로 주므로 UTC 필드로 읽어야 원 값이다 ───

    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function fmtDate(d) { return pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); }

    function fmtClock(d, withSec) {
        return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + (withSec ? ":" + pad(d.getUTCSeconds()) : "");
    }

    function formatFullTime(ms) {
        var d = new Date(ms);
        if (isNaN(d.getTime())) return String(ms);
        return d.getUTCFullYear() + "-" + fmtDate(d) + " " + fmtClock(d, d.getUTCSeconds() !== 0);
    }

    function formatTick(ms, step, span) {
        var d = new Date(ms);
        if (step >= DAY) return fmtDate(d);
        if (span >= DAY) return fmtDate(d) + " " + fmtClock(d, false);
        return fmtClock(d, step < MIN);
    }

    function pickStep(span) {
        for (var i = 0; i < TIME_STEPS.length; i++) {
            if (span / TIME_STEPS[i] <= 8) return TIME_STEPS[i];
        }
        return TIME_STEPS[TIME_STEPS.length - 1];
    }

    // ─── 차트 구성 ───

    function readPalette() {
        var cs = window.getComputedStyle(document.documentElement);
        var v = function (name, fallback) { return cs.getPropertyValue(name).trim() || fallback; };
        var text = v("--text", "gray");
        return {
            series: SERIES_TOKENS.map(function (t) { return v(t, text); }),
            text: v("--text-secondary", text),
            muted: v("--text-muted", text),
            grid: v("--table-cell-border", v("--border", text)),
            border: v("--border", text),
            tipBg: v("--tooltip-bg", v("--surface", text)),
            tipText: v("--tooltip-text", text),
            font: v("--font-display", "sans-serif")
        };
    }

    function seriesLabel(s, i) {
        return cellText(s.name) || cellText(s.column) || "시리즈 " + (i + 1);
    }

    function buildChartConfig(d, pal) {
        var series = Array.isArray(d.series) ? d.series : [];
        var timeX = d.x_type === "time";
        var font = { family: pal.font, size: 11 };
        var tickBase = { color: pal.muted, font: font };
        var tooltip = {
            backgroundColor: pal.tipBg, titleColor: pal.tipText, bodyColor: pal.tipText,
            borderColor: pal.border, borderWidth: 1, titleFont: font, bodyFont: font
        };
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: "nearest", axis: "x", intersect: false },
            plugins: {
                legend: { labels: { color: pal.text, font: font, boxWidth: 12 } },   // 범례 클릭 = 시리즈 토글(기본)
                tooltip: tooltip
            },
            scales: {
                y: { beginAtZero: d.kind === "bar", ticks: tickBase, grid: { color: pal.grid } }
            }
        };

        if (d.kind !== "bar" && timeX) {
            // 날짜 어댑터 없이 선형 축 — 눈금은 UTC 기준 반듯한 간격으로 직접 만든다
            var lo = Infinity, hi = -Infinity, maxPts = 0;
            var datasets = series.map(function (s, i) {
                var pts = [];
                (s.points || []).forEach(function (p) {
                    var x = Number(p[0]);
                    if (!isFinite(x)) return;
                    if (x < lo) lo = x;
                    if (x > hi) hi = x;
                    pts.push({ x: x, y: p[1] == null ? null : Number(p[1]) });
                });
                if (pts.length > maxPts) maxPts = pts.length;
                var c = pal.series[i % pal.series.length];
                return {
                    label: seriesLabel(s, i), data: pts, borderColor: c, backgroundColor: c,
                    borderWidth: 2, pointRadius: pts.length > 60 ? 0 : 2, pointHoverRadius: 4, tension: 0
                };
            });
            if (!isFinite(lo)) { lo = 0; hi = 1; }
            if (hi === lo) { lo -= 30 * MIN; hi += 30 * MIN; }
            var span = hi - lo;
            var step = pickStep(span);
            options.scales.x = {
                type: "linear", min: lo, max: hi,
                grid: { color: pal.grid },
                ticks: {
                    color: pal.muted, font: font, maxRotation: 0, autoSkip: true,
                    callback: function (value) { return formatTick(value, step, span); }
                },
                afterBuildTicks: function (axis) {
                    var ticks = [];
                    for (var t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push({ value: t });
                    if (ticks.length) axis.ticks = ticks;
                }
            };
            tooltip.callbacks = {
                title: function (items) { return items.length ? formatFullTime(items[0].parsed.x) : ""; }
            };
            return { type: "line", data: { datasets: datasets }, options: options };
        }

        // 범주 x — 행 순서(처음 나온 순서)를 그대로 둔다
        var labels = [], pos = {};
        series.forEach(function (s) {
            (s.points || []).forEach(function (p) {
                var key = timeX ? formatFullTime(Number(p[0])) : cellText(p[0]) || "(없음)";
                if (!Object.prototype.hasOwnProperty.call(pos, key)) {
                    pos[key] = labels.length;
                    labels.push(key);
                }
            });
        });
        var catSets = series.map(function (s, i) {
            var arr = labels.map(function () { return null; });
            (s.points || []).forEach(function (p) {
                var key = timeX ? formatFullTime(Number(p[0])) : cellText(p[0]) || "(없음)";
                arr[pos[key]] = p[1] == null ? null : Number(p[1]);
            });
            var c = pal.series[i % pal.series.length];
            return { label: seriesLabel(s, i), data: arr, borderColor: c, backgroundColor: c, borderWidth: d.kind === "bar" ? 0 : 2 };
        });
        options.scales.x = {
            type: "category", grid: { display: false },
            ticks: {
                color: pal.muted, font: font, autoSkip: true, maxRotation: 45,
                callback: function (value) {
                    var s = this.getLabelForValue(value);
                    return s.length > 18 ? s.slice(0, 17) + "…" : s;
                }
            }
        };
        return { type: d.kind === "bar" ? "bar" : "line", data: { labels: labels, datasets: catSets }, options: options };
    }

    function destroyChart(state) {
        if (state.chart) {
            try { state.chart.destroy(); } catch (e) { /* 이미 떨어진 캔버스 */ }
            state.chart = null;
        }
    }

    function drawChart(state) {
        destroyChart(state);
        var box = state.chartBox;
        if (!box) return;
        var t = state.payload.tables[state.active];
        if (typeof window.Chart !== "function") {
            box.textContent = "";
            box.appendChild(el("p", "result-viz-error", "그래프 라이브러리를 불러오지 못했습니다 — 아래 데이터 표를 보세요."));
            return;
        }
        var canvas = box.querySelector("canvas");
        if (!canvas) {
            canvas = el("canvas");
            canvas.setAttribute("role", "img");
            canvas.setAttribute("aria-label", (cellText(t.label) || "표 " + (state.active + 1)) + " 그래프");
            box.appendChild(canvas);
        }
        try {
            state.chart = new window.Chart(canvas, buildChartConfig(t.data || {}, readPalette()));
        } catch (e) {
            box.textContent = "";
            box.appendChild(el("p", "result-viz-error", "그래프를 그리지 못했습니다 — " + (e && e.message ? e.message : "알 수 없는 오류")));
        }
    }

    function watchTheme() {
        if (themeObserver || typeof MutationObserver === "undefined") return;
        // theme.js는 이벤트 없이 <html data-theme>만 바꾼다 — 그 속성만 본다
        themeObserver = new MutationObserver(function () {
            livePanels = livePanels.filter(function (s) {
                if (s.panel.isConnected) return true;
                destroyChart(s);   // 대화 전환 등으로 떨어진 패널
                return false;
            });
            livePanels.forEach(function (s) { if (s.chart) drawChart(s); });
        });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }

    // ─── 패널 ───

    function noticeText(d) {
        var parts = [];
        var hidden = Number(d && d.hidden_series) || 0;
        if (hidden > 0) {
            parts.push("시리즈가 많아 " + ((d.series || []).length) + "개만 그렸습니다 — " + hidden + "개는 숨겼습니다");
        }
        if (d && d.downsampled) parts.push("점이 많아 간격을 두고 줄여 그렸습니다");
        return parts.join(" · ");
    }

    function buildGrid(queryId, t, explain) {
        var rows = Array.isArray(t.rows) ? t.rows : [];
        var columns = Array.isArray(t.columns) && t.columns.length ? t.columns : (rows[0] ? Object.keys(rows[0]) : []);
        var table = el("table", GRID_CLASS);
        // 위임 리스너가 원 결과를 찾는 표지 — 넓게 보기 복제본에도 그대로 따라간다
        table.dataset.rvQuery = queryId;
        table.dataset.rvTable = String(t.index);
        if (explain) table.dataset.rvExplain = "1";

        var headRow = el("tr");
        columns.forEach(function (c) {
            var th = el("th");
            th.setAttribute("scope", "col");
            var name = cellText(c);
            if (explain) {
                var b = el("button", LINK_CLASS, name);
                b.type = "button";
                b.title = "칼럼 설명 보기";
                b.dataset.rvCol = name;
                th.appendChild(b);
            } else {
                th.textContent = name;
            }
            headRow.appendChild(th);
        });
        var thead = el("thead");
        thead.appendChild(headRow);
        table.appendChild(thead);

        var tbody = el("tbody");
        rows.forEach(function (r, ri) {
            var tr = el("tr");
            columns.forEach(function (c) {
                var td = el("td");
                var text = cellText(r ? r[c] : null);
                if (explain && text !== "") {
                    var b = el("button", LINK_CLASS, text);
                    b.type = "button";
                    b.title = "값 해석 보기";
                    b.dataset.rvCol = cellText(c);
                    b.dataset.rvRow = String(ri);
                    td.appendChild(b);
                } else {
                    td.textContent = text;
                }
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        return table;
    }

    function showTable(state, i) {
        closePopoverWithin(state.panel);
        destroyChart(state);
        state.active = i;
        if (state.tabs) {
            state.tabs.forEach(function (tab, ti) { tab.setAttribute("aria-selected", ti === i ? "true" : "false"); });
        }
        var t = state.payload.tables[i];
        var body = state.body;
        body.textContent = "";

        var notice = noticeText(t.data);
        if (notice) body.appendChild(el("p", "result-viz-notice", notice));

        state.chartBox = el("div", "result-viz-chart");
        body.appendChild(state.chartBox);
        drawChart(state);

        var explain = state.payload.explain_enabled === true;
        var title = el("div", "result-viz-grid-title", "데이터");
        if (explain) title.appendChild(el("span", "result-viz-grid-hint", "칼럼명·값을 누르면 설명을 봅니다"));
        body.appendChild(title);
        // 서버는 격자 행을 앞부분만 보낸다(rows_total = 전체 행 수 · 그래프 data는 전체) — 없으면 고지 없음
        var shown = Array.isArray(t.rows) ? t.rows.length : 0;
        if (typeof t.rows_total === "number" && t.rows_total > shown) {
            body.appendChild(el("p", "result-viz-notice", "데이터 격자는 처음 " + shown.toLocaleString("ko-KR") +
                "행만 보입니다(전체 " + t.rows_total.toLocaleString("ko-KR") + "행 · 그래프는 전체 행)"));
        }
        body.appendChild(buildGrid(state.queryId, t, explain));
        enhanceTables(state.panel);   // 격자에도 「넓게 보기」
    }

    function renderPanel(state) {
        var panel = state.panel;
        panel.textContent = "";
        var tables = state.payload.tables;
        state.tabs = null;
        if (tables.length > 1) {
            var list = el("div", "result-viz-tabs");
            list.setAttribute("role", "tablist");
            list.setAttribute("aria-label", "표 선택");
            state.tabs = tables.map(function (t, i) {
                var tab = el("button", "result-viz-tab", cellText(t.label) || "표 " + (i + 1));
                tab.type = "button";
                tab.setAttribute("role", "tab");
                tab.addEventListener("click", function () { if (state.active !== i) showTable(state, i); });
                list.appendChild(tab);
                return tab;
            });
            panel.appendChild(list);
        }
        state.body = el("div", "result-viz-body");
        panel.appendChild(state.body);
        if (livePanels.indexOf(state) === -1) livePanels.push(state);
        watchTheme();
        showTable(state, 0);
    }

    function showPanelMessage(state, text, isError) {
        state.panel.textContent = "";
        var p = el("p", isError ? "result-viz-error" : "result-viz-status", text);
        p.setAttribute("role", isError ? "alert" : "status");
        state.panel.appendChild(p);
    }

    function loadPanel(state) {
        state.requested = true;
        showPanelMessage(state, "그래프 데이터를 불러오는 중…", false);
        requestJson(API_BASE + encodeURIComponent(state.queryId) + "/chart").then(function (body) {
            if (!Array.isArray(body.tables) || !body.tables.length) throw new Error("그래프로 그릴 수 있는 표가 없습니다");
            state.payload = body;
            renderPanel(state);
        }).catch(function (e) {
            state.requested = false;   // 접었다 다시 펼치면 재시도
            showPanelMessage(state, e && e.message ? e.message : "그래프를 불러오지 못했습니다", true);
        });
    }

    function togglePanel(state, btn) {
        var open = btn.getAttribute("aria-expanded") !== "true";
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        btn.querySelector("span").textContent = open ? "그래프 접기" : "그래프로 보기";
        state.panel.hidden = !open;
        // 말풍선은 내용 폭(inline-block)이라 캔버스가 쪼그라든다 — 펼친 동안만 말풍선을 가용 폭으로 편다
        var content = state.panel.closest(".message-content");
        if (content) content.classList.toggle(OPEN_CLASS, open);
        if (!open) {
            closePopoverWithin(state.panel);
            return;
        }
        if (!state.requested) loadPanel(state);
    }

    function attach(bubbleEl, data, opts) {
        if (opts && typeof opts.headers === "function") headerFn = opts.headers;
        if (!bubbleEl || !data || !data.chart) return;
        var queryId = data.query_id || data.result_query_id;
        if (!queryId || bubbleEl.querySelector(".result-viz-toggle")) return;

        // 대화 복원 턴은 다운로드 줄이 없다 — 없으면 만든다
        var actions = bubbleEl.querySelector(":scope > .message-download-actions");
        if (!actions) {
            actions = el("div", "message-download-actions");
            bubbleEl.appendChild(actions);
        }
        var btn = el("button", "message-download result-viz-toggle");
        btn.type = "button";
        btn.innerHTML = CHART_ICON;   // 고정 아이콘만 — 서버 문자열은 아래 textContent
        btn.appendChild(el("span", null, "그래프로 보기"));
        btn.setAttribute("aria-expanded", "false");
        actions.appendChild(btn);

        var panel = el("div", "result-viz-panel");
        panel.hidden = true;
        panel.setAttribute("role", "region");
        panel.setAttribute("aria-label", "그래프로 보기");
        actions.parentNode.insertBefore(panel, actions.nextSibling);

        var state = { queryId: String(queryId), panel: panel, requested: false, payload: null,
            active: 0, chart: null, chartBox: null, body: null, tabs: null };
        btn.addEventListener("click", function () { togglePanel(state, btn); });
        ensureDelegation();
    }

    // ─── 팝오버(용어 사전 · 값 해석) — 한 번에 하나만 ───

    function positionPopover() {
        if (!openPop) return;
        var pop = openPop.el;
        var r = openPop.trigger.getBoundingClientRect();
        var vw = document.documentElement.clientWidth;
        var vh = window.innerHeight;
        var w = pop.offsetWidth, h = pop.offsetHeight;
        var left = Math.max(8, Math.min(r.left, vw - w - 8));
        var top = r.bottom + 6;
        if (top + h > vh - 8 && r.top - h - 6 >= 8) top = r.top - h - 6;
        pop.style.left = left + "px";
        pop.style.top = Math.max(8, top) + "px";
    }

    function closePopover(returnFocus) {
        if (!openPop) return;
        var o = openPop;
        openPop = null;
        window.removeEventListener("keydown", o.onKey, true);
        document.removeEventListener("mousedown", o.onDown, true);
        window.removeEventListener("scroll", o.onMove, true);
        window.removeEventListener("resize", o.onMove);
        if (o.el.parentNode) o.el.parentNode.removeChild(o.el);
        o.trigger.setAttribute("aria-expanded", "false");
        if (returnFocus && o.trigger.isConnected) o.trigger.focus({ preventScroll: true });
    }

    function closePopoverWithin(root) {
        if (openPop && root.contains(openPop.trigger)) closePopover(false);
    }

    function openPopover(trigger, title) {
        closePopover(false);
        var pop = el("div", "result-viz-pop");
        pop.setAttribute("role", "dialog");
        pop.setAttribute("aria-label", title);
        pop.tabIndex = -1;

        var head = el("div", "result-viz-pop-head");
        head.appendChild(el("span", "result-viz-pop-title", title));
        var close = el("button", "result-viz-pop-close", "닫기");
        close.type = "button";
        close.setAttribute("aria-label", "닫기 (Esc)");
        close.addEventListener("click", function () { closePopover(true); });
        head.appendChild(close);

        var body = el("div", "result-viz-pop-body");
        var loading = el("div", "result-viz-pop-loading");
        loading.setAttribute("role", "status");
        loading.appendChild(el("span", "result-viz-spinner"));
        loading.appendChild(el("span", null, "불러오는 중…"));
        body.appendChild(loading);

        pop.appendChild(head);
        pop.appendChild(body);
        document.body.appendChild(pop);

        // Esc는 창 단계 캡처에서 먼저 받는다 — 넓게 보기 오버레이(문서 캡처)보다 앞서 팝오버만 닫는다
        var onKey = function (e) {
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                closePopover(true);
            }
        };
        var onDown = function (e) {
            if (!pop.contains(e.target) && !trigger.contains(e.target)) closePopover(false);
        };
        var onMove = function (e) {
            if (e && e.type === "scroll" && pop.contains(e.target)) return;
            positionPopover();
        };
        window.addEventListener("keydown", onKey, true);
        document.addEventListener("mousedown", onDown, true);
        window.addEventListener("scroll", onMove, true);
        window.addEventListener("resize", onMove);

        openPop = { el: pop, trigger: trigger, body: body, onKey: onKey, onDown: onDown, onMove: onMove };
        trigger.setAttribute("aria-expanded", "true");
        positionPopover();
        pop.focus({ preventScroll: true });
        return openPop;
    }

    function badge(kind, text) {
        return el("span", "result-viz-badge result-viz-badge--" + kind, text);
    }

    function fillGlossary(body, res) {
        body.textContent = "";
        var src = res.source;
        var label = src === "glossary" ? "용어 사전" : src === "llm_generated" ? "사전 미등재 · LLM 생성" : "설명 없음";
        var kind = src === "glossary" ? "glossary" : src === "llm_generated" ? "llm" : "none";
        body.appendChild(badge(kind, label));
        var text = cellText(res.text);
        if (text && src !== "unavailable") {
            body.appendChild(el("p", "result-viz-pop-text", text));
        } else {
            body.appendChild(el("p", "result-viz-pop-reason", cellText(res.reason) || "설명을 찾지 못했습니다."));
        }
        var meta = [];
        if (res.unit) meta.push("단위: " + cellText(res.unit));
        if (res.origin) meta.push("출처: " + cellText(res.origin));
        if (meta.length) body.appendChild(el("p", "result-viz-pop-meta", meta.join(" · ")));
    }

    function fillInterpret(body, res) {
        body.textContent = "";
        var value = el("p", "result-viz-pop-value");
        value.appendChild(el("span", "result-viz-pop-key", "값"));
        value.appendChild(el("span", null, cellText(res.value)));
        body.appendChild(value);

        // 코드가 계산한 사실을 먼저 — LLM 문장은 그 아래 참고용
        var facts = Array.isArray(res.facts) ? res.facts : [];
        if (facts.length) {
            var dl = el("dl", "result-viz-facts");
            facts.forEach(function (f) {
                dl.appendChild(el("dt", null, cellText(f && f.label) || cellText(f && f.key)));
                dl.appendChild(el("dd", null, cellText(f && f.value)));
            });
            body.appendChild(dl);
        } else {
            body.appendChild(el("p", "result-viz-pop-reason", "계산된 위치 정보가 없습니다."));
        }

        var llm = el("div", "result-viz-pop-llm");
        llm.appendChild(badge("llm", "LLM 해석 · 참고용"));
        var text = cellText(res.text);
        if (text) {
            llm.appendChild(el("p", "result-viz-pop-text", text));
        } else {
            llm.appendChild(el("p", "result-viz-pop-reason", "해석 문장 없음 — " + (cellText(res.reason) || "사유 미상")));
        }
        body.appendChild(llm);
    }

    function openExplain(link, table) {
        if (openPop && openPop.trigger === link) {   // 같은 링크 재클릭 = 닫기
            closePopover(true);
            return;
        }
        var queryId = table.dataset.rvQuery;
        var tableIndex = Number(table.dataset.rvTable);
        var column = link.dataset.rvCol || "";
        var isCell = link.dataset.rvRow !== undefined;
        var row = Number(link.dataset.rvRow);
        var title = isCell ? column + " · " + (row + 1) + "행" : column;
        var pop = openPopover(link, title);
        var base = API_BASE + encodeURIComponent(queryId);
        var promise = isCell
            ? cachedRequest(["i", queryId, tableIndex, column, row].join("\u0000"), function () {
                return requestJson(base + "/interpret", "POST", { table: tableIndex, column: column, row: row });
            })
            : cachedRequest(["g", queryId, tableIndex, column].join("\u0000"), function () {
                return requestJson(base + "/glossary?table=" + encodeURIComponent(tableIndex) +
                    "&column=" + encodeURIComponent(column));
            });
        promise.then(function (res) {
            if (openPop !== pop) return;   // 그 사이 닫혔거나 다른 팝오버로 바뀜
            if (isCell) fillInterpret(pop.body, res); else fillGlossary(pop.body, res);
            positionPopover();
        }, function (e) {
            if (openPop !== pop) return;
            pop.body.textContent = "";
            var p = el("p", "result-viz-pop-error", e && e.message ? e.message : "설명을 불러오지 못했습니다");
            p.setAttribute("role", "alert");
            pop.body.appendChild(p);
            positionPopover();
        });
    }

    function ensureDelegation() {
        if (delegated) return;
        delegated = true;
        // 위임 1개 — 패널 격자와 「넓게 보기」 복제본 격자를 같이 받는다(입력창에는 아무것도 넣지 않는다)
        document.addEventListener("click", function (e) {
            var link = e.target && e.target.closest ? e.target.closest("." + LINK_CLASS) : null;
            if (!link) return;
            var table = link.closest("table." + GRID_CLASS);
            if (!table || table.dataset.rvExplain !== "1") return;
            e.preventDefault();
            openExplain(link, table);
        });
    }

    window.ResultViz = {
        enhanceTables: enhanceTables,
        attach: attach
    };
})();
