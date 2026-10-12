/**
 * 자동 조사 브리핑 공용 렌더 (plans/150 W3 · R-1 · D-323).
 *
 * 알람 카드(`app.js renderAlarmMessage`)와 노이즈 관제 드로어(`noise.js` 「발생 원인과 처리」 ⓒ)가
 * **같은 함수(`renderBody`) 하나로** 그린다(D-247 「공용 패널」 — 두 벌이면 한쪽만 고쳐진다).
 * 입력은 조회 API(`/api/v1/alarm/investigations/*`)·관제 `decisions/{alarm_id}`의 `investigation`
 * 블록이다 — 둘은 같은 모양이다(`noise_gate.infrastructure.investigation_store.investigation_view`).
 *
 * 원칙:
 *   - 모든 텍스트는 `escapeHtml`을 거친다(브리핑은 LLM 서술 · 도구 출력 인용이라 외부 데이터다).
 *   - 출처를 표기한다(D-035) — 「자동 조사 · 도구 출력 인용」 배지, `[가설]`·`[미결]` 접두는 배지로 바꾼다.
 *   - 침묵 금지 — 실패·타임아웃·거부·APM 도구 미연결·재조회 상한 도달을 상태 칩·「한계」 칸으로 보인다.
 *   - 「한계」는 접지 않는다. 권고 아래 human-gated 문구는 항상 남긴다(sre_agent `HUMAN_GATED_NOTE`).
 *   - 지연 로드 — 화면이 펼칠 때만 부른다. 조사 중이면 15초 간격으로 다시 묻되 상한(기본 300초 =
 *     `investigation_followup_timeout_seconds` 기본)에서 멈춘다. 닫으면 타이머를 정리한다(G-2 (a)).
 *
 * 전역은 `window.BriefingView` 하나만 둔다. DOM 없이 도는 순수 함수(문자열 렌더·재조회 상한)는
 * node로 단위 테스트한다(`tests/test_api/test_plan150_w3_briefing_view.py`).
 */
(function (root) {
    "use strict";

    var POLL_MS = 15000;          // 조사 중 재조회 간격(G-2 (a))
    var DEFAULT_LIMIT_S = 300;    // 재조회 상한 — capabilities가 값을 주지 않으면 이 값

    // sre_agent briefing_builder.HUMAN_GATED_NOTE와 같은 문구(채팅·WorkB가 그대로 싣는 줄).
    var HUMAN_GATED_NOTE = "※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동 실행 경로 없음)";

    var TEXT = {
        title: "조사 브리핑",
        crossSourceLineLabel: "크로스소스",   // noise_gate render_briefing_lines 라벨과 같은 글자
        sourceBadge: "자동 조사 · 도구 출력 인용",
        loading: "조사 기록을 불러오는 중…",
        noneText: "이 알람에는 자동 조사 기록이 없습니다.",
        runningText: "조사가 진행 중입니다 — 15초마다 다시 확인합니다.",
        episodeText: "같은 사건의 종합 조사에 연결됐습니다 — 결과는 그 조사에서 확인하십시오.",
        emptyLines: "브리핑 본문이 없습니다.",
        limitReached: "재조회 상한({s}초)에 도달해 자동 확인을 멈췄습니다 — 다시 펼치면 이어서 확인합니다.",
        loadFailed: "조사 기록을 불러오지 못했습니다",
        crossTitle: "두 소스 타임라인",
        crossHead: { rule: "규칙", path: "원인 → 결과", lag: "지연", support: "도구 뒷받침" },
        apmConnected: "APM 도구 연결 · 호출 {n}회",
        apmDisconnected: "APM 도구 미연결 — 제니퍼 쪽 근거를 확인하지 못했습니다",
        crossNoLinks: "연결된 원인·결과 쌍이 없습니다.",
        support: { both: "양쪽 도구 확인", one: "한쪽만", none: "인용 없음" },
        sourceNames: { polestar: "폴스타", jennifer: "제니퍼" },
    };

    // status → [칩 종류, 칩 문구]. 종류는 CSS 클래스 접미사(bv-chip--*)다.
    var STATUS_CHIPS = {
        submitted: ["running", "조사 중"],
        running: ["running", "조사 중"],
        done: ["done", "완료"],
        failed: ["fail", "실패"],
        timeout: ["fail", "실패 · 시간 초과"],
        rejected: ["fail", "실패 · 제출 거부"],
        episode_existing: ["linked", "같은 사건 조사로 연결됨"],
        none: ["none", "없음"],
    };

    // 사유가 비어 있는 실패에 붙이는 기본 사유 — 사유 없는 실패 칩만 남기지 않는다(침묵 금지).
    var DEFAULT_REASONS = {
        failed: "조사 서비스가 실패를 보고했습니다",
        timeout: "조사 시간 제한을 넘겼습니다",
        rejected: "조사 서비스가 제출을 거부했습니다",
    };

    var BADGE_PREFIX = /^\s*\[(가설|미결)\]\s*/;

    function escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function fill(template, values) {
        return String(template).replace(/\{(\w+)\}/g, function (_m, key) {
            return values[key] === undefined ? "" : String(values[key]);
        });
    }

    function isActive(view) {
        var status = view && view.status;
        return status === "submitted" || status === "running";
    }

    function statusChip(view) {
        var status = (view && view.status) || "none";
        var hit = STATUS_CHIPS[status];
        var chip = hit ? { kind: hit[0], text: hit[1] } : { kind: "none", text: String(status) };
        chip.reason = "";
        if (chip.kind === "fail") {
            chip.reason = (view && view.reason) || DEFAULT_REASONS[status] || "";
        } else if (view && view.reason) {
            // 진행 중 재조회 실패 등 — 상태는 그대로 두고 사유를 함께 보인다
            chip.reason = view.reason;
        }
        return chip;
    }

    function fmtLag(seconds) {
        var n = Number(seconds);
        if (seconds === null || seconds === undefined || seconds === "" || !isFinite(n)) return "—";
        n = Math.round(n);
        if (Math.abs(n) < 60) return n + "초";
        var m = Math.floor(Math.abs(n) / 60);
        var s = Math.abs(n) % 60;
        return (n < 0 ? "-" : "") + m + "분" + (s ? " " + s + "초" : "");
    }

    // 갱신 시각 — 해석되면 브라우저 지역 시각, 아니면 원문 그대로.
    function fmtTime(value) {
        var d = new Date(value);
        if (!value || isNaN(d.getTime())) return String(value || "");
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    function fmtEnd(value) {
        if (value === null || value === undefined) return "—";
        if (typeof value !== "object") return String(value);
        var parts = [value.source, value.name || value.alarm_name || value.label, value.ts || value.at];
        var text = parts.filter(function (p) { return p !== null && p !== undefined && p !== ""; }).join(" · ");
        return text || JSON.stringify(value);
    }

    // 한 줄(문자열)을 렌더 — `[가설]`·`[미결]` 접두는 배지로 바꾸고 나머지는 이스케이프한다.
    function renderTextLine(line) {
        var match = BADGE_PREFIX.exec(line);
        if (!match) return '<div class="bv-line">' + escapeHtml(line) + '</div>';
        var kind = match[1] === "가설" ? "hypo" : "open";
        return '<div class="bv-line"><span class="bv-badge bv-badge--' + kind + '">' +
            escapeHtml(match[1]) + '</span> ' + escapeHtml(line.slice(match[0].length)) + '</div>';
    }

    function renderLine(item) {
        var label = String((item && item.label) || "");
        var text = String((item && item.text) || "");
        var rows = text.split("\n").filter(function (row) { return row.trim() !== ""; });
        var gated = false;
        if (label === "권고") {
            // 문구가 본문에 섞여 있으면 떼어 내 권고 아래 별도 줄로 — 없으면 덧붙인다(항상 남긴다)
            rows = rows.filter(function (row) { return row.trim() !== HUMAN_GATED_NOTE; });
            gated = true;
        }
        var cls = "bv-item" + (label === "한계" ? " bv-item--limit" : "");
        return '<div class="' + cls + '">' +
            '<div class="bv-label">' + escapeHtml(label) + '</div>' +
            '<div class="bv-text">' + rows.map(renderTextLine).join("") +
            (gated ? '<div class="bv-gated">' + escapeHtml(HUMAN_GATED_NOTE) + '</div>' : "") +
            '</div></div>';
    }

    function supportBadge(value) {
        if (value === true) return '<span class="bv-support bv-support--both">' + escapeHtml(TEXT.support.both) + '</span>';
        if (value === false) return '<span class="bv-support bv-support--one">' + escapeHtml(TEXT.support.one) + '</span>';
        return '<span class="bv-support bv-support--none">' + escapeHtml(TEXT.support.none) + '</span>';
    }

    // 브리핑 `cross_source`(§4.7) → 「두 소스 타임라인」 소표. 블록이 없으면 빈 문자열(그리지 않는다).
    function renderCrossSource(cs) {
        if (!cs || typeof cs !== "object") return "";
        var meta = [];
        if (cs.episode_id) meta.push("사건 " + escapeHtml(cs.episode_id));
        if (cs.host_key) meta.push("호스트 " + escapeHtml(cs.host_key));
        var sources = cs.sources && typeof cs.sources === "object" ? cs.sources : {};
        Object.keys(sources).forEach(function (key) {
            meta.push(escapeHtml(TEXT.sourceNames[key] || key) + " " + escapeHtml(sources[key]) + "건");
        });
        var apm = cs.apm_tools_connected === false
            ? '<div class="bv-cross-apm bv-cross-apm--off">' + escapeHtml(TEXT.apmDisconnected) + '</div>'
            : cs.apm_tools_connected === true
                ? '<div class="bv-cross-apm">' + escapeHtml(fill(TEXT.apmConnected, { n: Number(cs.apm_tools_called) || 0 })) + '</div>'
                : "";
        var links = Array.isArray(cs.links) ? cs.links : [];
        var body;
        if (!links.length) {
            body = '<div class="bv-cross-empty">' + escapeHtml(TEXT.crossNoLinks) + '</div>';
        } else {
            var H = TEXT.crossHead;
            body = '<table class="bv-cross-table"><thead><tr>' +
                '<th>' + escapeHtml(H.rule) + '</th><th>' + escapeHtml(H.path) + '</th>' +
                '<th>' + escapeHtml(H.lag) + '</th><th>' + escapeHtml(H.support) + '</th>' +
                '</tr></thead><tbody>' +
                links.map(function (link) {
                    link = link || {};
                    return '<tr><td class="bv-mono">' + escapeHtml(link.rule_id || "—") + '</td>' +
                        '<td>' + escapeHtml(fmtEnd(link.cause)) + ' → ' + escapeHtml(fmtEnd(link.effect)) + '</td>' +
                        '<td class="bv-mono bv-nowrap">' + escapeHtml(fmtLag(link.lag_seconds)) + '</td>' +
                        '<td class="bv-nowrap">' + supportBadge(link.supported_by_tools) + '</td></tr>';
                }).join("") +
                '</tbody></table>';
        }
        return '<div class="bv-cross">' +
            '<div class="bv-label">' + escapeHtml(TEXT.crossTitle) + '</div>' +
            (meta.length ? '<div class="bv-cross-meta">' + meta.join(" · ") + '</div>' : "") +
            apm + body + '</div>';
    }

    /**
     * 조사 요약 1건 → HTML 문자열(카드·드로어 공용 · 순수 함수).
     *
     * @param {object|null} view  조회 API 응답(`{status, briefing_lines, cross_source, reason, …}`)
     * @param {object} [extra]    `{note: 문자열}` — 재조회 상한 도달 등 화면 쪽 안내(이스케이프됨)
     */
    function renderBody(view, extra) {
        view = view || { status: "none" };
        var chip = statusChip(view);
        var head = '<div class="bv-head">' +
            '<span class="bv-chip bv-chip--' + chip.kind + '">' + escapeHtml(chip.text) + '</span>' +
            '<span class="bv-src">' + escapeHtml(TEXT.sourceBadge) + '</span>';
        if (view.verdict_level) head += '<span class="bv-meta">평가 ' + escapeHtml(view.verdict_level) + '</span>';
        if (view.kind === "episode") head += '<span class="bv-meta">사건 종합 조사</span>';
        if (view.updated_at) head += '<span class="bv-meta">' + escapeHtml(fmtTime(view.updated_at)) + '</span>';
        head += '</div>';

        var parts = [head];
        if (chip.reason) {
            parts.push('<div class="bv-reason' + (chip.kind === "fail" ? " bv-reason--fail" : "") + '">' +
                '사유: ' + escapeHtml(chip.reason) + '</div>');
        }
        var status = view.status || "none";
        if (status === "none") parts.push('<div class="bv-note">' + escapeHtml(TEXT.noneText) + '</div>');
        if (isActive(view)) parts.push('<div class="bv-note">' + escapeHtml(TEXT.runningText) + '</div>');
        if (status === "episode_existing") {
            parts.push('<div class="bv-note">' + escapeHtml(TEXT.episodeText) +
                (view.episode_id ? ' <span class="bv-mono">' + escapeHtml(view.episode_id) + '</span>' : "") +
                '</div>');
        }
        var lines = Array.isArray(view.briefing_lines) ? view.briefing_lines : [];
        // 「두 소스 타임라인」 소표를 그릴 때는 같은 내용의 「크로스소스」 평문 줄(채팅·workb용)을 뺀다.
        if (view.cross_source && typeof view.cross_source === "object") {
            lines = lines.filter(function (item) { return !(item && item.label === TEXT.crossSourceLineLabel); });
        }
        if (lines.length) {
            parts.push('<div class="bv-lines">' + lines.map(renderLine).join("") + '</div>');
        } else if (status === "done") {
            parts.push('<div class="bv-note">' + escapeHtml(TEXT.emptyLines) + '</div>');
        }
        parts.push(renderCrossSource(view.cross_source));
        if (extra && extra.note) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(extra.note) + '</div>');
        return '<div class="bv">' + parts.join("") + '</div>';
    }

    function renderError(message) {
        return '<div class="bv"><div class="bv-head">' +
            '<span class="bv-chip bv-chip--fail">' + escapeHtml(TEXT.loadFailed) + '</span>' +
            '<span class="bv-src">' + escapeHtml(TEXT.sourceBadge) + '</span></div>' +
            (message ? '<div class="bv-reason bv-reason--fail">사유: ' + escapeHtml(message) + '</div>' : "") +
            '</div>';
    }

    /**
     * 재조회 컨트롤러(DOM 무관 · 타이머 주입 가능). 조사 중이면 `intervalMs`마다 `load()`를 다시
     * 부르고, 시작부터 `limitMs`가 지나면 멈춘다(onView의 두 번째 인자 `{limited:true}`).
     *
     * opts: load() → Promise<view> · onView(view, info) · onError(err) · intervalMs · limitMs ·
     *       now() · setTimer(fn, ms) · clearTimer(id)
     */
    function createPoller(opts) {
        var interval = opts.intervalMs || POLL_MS;
        var limit = opts.limitMs || DEFAULT_LIMIT_S * 1000;
        var now = opts.now || function () { return Date.now(); };
        var setTimer = opts.setTimer || function (fn, ms) { return setTimeout(fn, ms); };
        var clearTimer = opts.clearTimer || function (id) { clearTimeout(id); };
        var timer = null;
        var gen = 0;
        var startedAt = 0;
        var calls = 0;

        function handle(g, view) {
            if (g !== gen) return;     // 닫혔거나 다시 시작됨 — 늦게 온 응답은 버린다
            var active = isActive(view);
            var limited = active && now() - startedAt >= limit;
            opts.onView(view, { limited: limited });
            if (active && !limited) {
                timer = setTimer(function () { timer = null; return tick(g); }, interval);
            }
        }

        function tick(g) {
            calls += 1;
            return Promise.resolve()
                .then(opts.load)
                .then(function (view) { handle(g, view); }, function (err) {
                    if (g !== gen) return;
                    if (opts.onError) opts.onError(err);
                });
        }

        function stop() {
            gen += 1;
            if (timer !== null) clearTimer(timer);
            timer = null;
        }

        return {
            // initial이 있으면 호출 없이 그것부터 그리고(관제 응답에 이미 실린 블록) 필요할 때만 재조회한다
            start: function (initial) {
                stop();
                startedAt = now();
                var g = gen;
                if (initial) { handle(g, initial); return Promise.resolve(); }
                return tick(g);
            },
            stop: stop,
            pending: function () { return timer !== null; },
            calls: function () { return calls; },
        };
    }

    /**
     * 컨테이너에 붙는 지연 로드 패널(카드·드로어 공용). open()에서만 호출이 나가고 close()가 타이머를
     * 정리한다. 컨테이너가 문서에서 빠지면(카드 목록 비우기 등) 다음 응답에서 스스로 멈춘다.
     *
     * opts: load() · initial(선택) · limitSeconds(선택)
     */
    function attach(container, opts) {
        var limitS = Number(opts.limitSeconds) > 0 ? Number(opts.limitSeconds) : DEFAULT_LIMIT_S;
        var poller = createPoller({
            load: opts.load,
            limitMs: limitS * 1000,
            onView: function (view, info) {
                if (container.isConnected === false) { poller.stop(); return; }
                container.innerHTML = renderBody(view,
                    info && info.limited ? { note: fill(TEXT.limitReached, { s: limitS }) } : null);
            },
            onError: function (err) {
                if (container.isConnected === false) return;
                container.innerHTML = renderError(err && err.message ? err.message : String(err || ""));
            },
        });
        return {
            open: function () {
                if (!opts.initial) {
                    container.innerHTML = '<div class="bv"><div class="bv-note">' + escapeHtml(TEXT.loading) + '</div></div>';
                }
                var initial = opts.initial;
                opts.initial = null;   // 다시 펼칠 때는 새로 묻는다(옛 블록을 되풀이하지 않는다)
                return poller.start(initial);
            },
            close: function () { poller.stop(); },
            poller: poller,
        };
    }

    var api = {
        POLL_MS: POLL_MS,
        DEFAULT_LIMIT_S: DEFAULT_LIMIT_S,
        HUMAN_GATED_NOTE: HUMAN_GATED_NOTE,
        TEXT: TEXT,
        escapeHtml: escapeHtml,
        isActive: isActive,
        statusChip: statusChip,
        fmtLag: fmtLag,
        renderBody: renderBody,
        renderError: renderError,
        renderCrossSource: renderCrossSource,
        createPoller: createPoller,
        attach: attach,
    };
    root.BriefingView = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
