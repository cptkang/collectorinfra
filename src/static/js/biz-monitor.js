/**
 * 업무 현황 — 포털 뷰 탭 모듈 (plans/215 §5.2 · §5.3 · D-368).
 *
 * `BizMonitor.mount(root, opts)` → `{pause, resume, unmount}`. 마크업은 `/static/biz-monitor.html` 조각
 * 한 벌을 app.js가 받아 `root`에 붙인 뒤 부른다(노이즈 관제 이식 방식과 같다 — D-245 · plans/193 K-1).
 *
 * 화면: ① 요약 띠 · ② 서비스 목록(타일 ↔ 표 · 필터) · ③ 상세 머리 + 보기 탭(`DETAIL_TABS`).
 * 「개요」 탭 = ④ 구성 맵(계층 레인 SVG — 코드가 그린다 · 외부 라이브러리 0) · ⑤ 구성원 상태 표 · ⑦ 추이.
 *
 * 원칙(§5.3):
 *   - 값·이름은 모두 `textContent`로 넣는다(HTML 문자열 삽입 0). 확인 대화상자 0.
 *   - 상태는 색 + 글자(색만으로 구분하지 않는다). 어휘는 위험·주의·이상 신호 없음·판정 불가.
 *   - 새 브라우저 탭은 [정의에서 편집](관리자 · P1 페이지) 하나뿐이다(`definitionLink`).
 *     질의응답·알람 조사 동선은 같은 창 탭 전환이다(부른 쪽 콜백).
 *   - 갱신은 30초 폴링 + heartbeat 30초. 탭이 숨거나(`pause`) 브라우저 탭이 숨으면(`document.hidden`)
 *     폴링·heartbeat를 멈추고 진행 중 요청을 끊는다(요청 0). 상세가 열려 있으면 상세만 갱신한다.
 *   - 업무 현황 탭에는 배지를 두지 않는다(알람 배지는 「이벤트 알람」 탭 하나).
 *
 * mount opts:
 *   token()                 요청마다 읽는 사용자 토큰(빈 값이면 헤더 없이)
 *   onAuthFail()            401 응답
 *   isAdmin()               관리자면 true — [정의에서 편집]을 그린다(서버가 다시 막는다)
 *   service, tab            처음 열 상세(주소 `?view=biz&service=&tab=` 복원)
 *   at                      (§5.2.1) `tab=events`일 때 그 시각(ISO 8601 UTC)으로 연다 — 형식 오류·미래·7일 넘은 과거는 무시
 *   onRoute({service, tab[, at]}) 상세·보기 탭이 바뀔 때(주소 동기화 · `at`은 이벤트 보기에 시각 지정이 있을 때만)
 *   fillQuestion(text)      질의응답 탭 입력창에 채우기만(전송 0) — 채웠으면 true
 *   onInvestigateMember(m)  [이 구성원 알람 조사] — 기존 알람 조사 동선(같은 창) · 없으면 버튼 없음
 *   onOpenAlarm(alarmId)    (W8b) 「이벤트」 알람 행 → 같은 창 노이즈 관제 탭 서랍 — 옮겼으면 true · 없으면 버튼 없음
 *   onOpenInvestigation(id) (W8b) 「이벤트」 조사 행 → 같은 창 장애 진단 탭 — 옮겼으면 true · 없으면 버튼 없음
 *   fetch                   (테스트) fetch 대체
 *
 * 상세 보기 탭 확장(W8b 「이벤트」 · W8c 「지표」 · W9 「진단」): `DETAIL_TABS`에 `{id, label, enabled(ctx),
 * refresh(ctx)}`를 더한다. 주소 `&tab=`은 `resolveTab`이 고르며 모르는 값·꺼진 탭은 `overview`다.
 * `refresh(ctx)`는 30초 상세 갱신마다 불리고 `ctx = {serviceId, detail, panel, request, focusMember,
 * flash, opts, tabState(id), active(id)}`를 받는다 — 화면은 `ctx.panel` 안에만 그린다. `tabState(id)`는 mount마다
 * 보기 탭 하나의 상태 그릇이고, `active(id)`는 지금도 그 서비스·그 보기 탭인지다(늦게 온 응답이 다른 화면을 덮지 않게).
 *
 * (W8b) 「이벤트」 탭 = ⑥ 통합 이벤트 타임라인(`/{id}/events`). 기간 1h/6h/24h(기본 1h) · 소스 칩 · 심각도·구성원
 * 필터 · 「억제 포함」(관리자만) · 수동 [새로고침] + 60초 자동(30초 상세 갱신 중 60초가 지난 때만 다시 읽는다 ·
 * pause면 요청 0). 응답은 `loadEvents(ctx, {force})`가 가져와 탭 상태에 60초 둔다 — 다른 보기 탭(W8c 지표 표지)도
 * 이 함수로 같은 결과를 추가 조회 없이 쓴다(`BizMonitor.events.load`). (§5.2.1) 지표 표지를 누르거나 주소 `&at=`으로 열면
 * 그 시각에 가장 가까운 행을 강조하고 처음 1회 스크롤한다 — 주소 복원은 그 시각을 담는 가장 작은 기간으로, 표지는 표지 기간 그대로.
 *
 * (W8c) 「지표」 탭 = ⑧ 구성원별 지표 추이(`/{id}/metrics`). 기능 조회(`/api/v1/alarm/capabilities`의
 * `biz_history_enabled`)가 true일 때만 탭이 보인다(mount마다 1회 · 꺼져 있으면 탭·요청 0). 기간 1h/6h/24h/7d(기본 24h)
 * · 구성원 고르기(상한 같음 · 「조회 N대 / 구성원 M대」) · 어제 비교(점선) · 계층 레인별 그래프(Chart.js — 없거나
 * 실패하면 표) · 공유 시간 커서 · 구성원 서랍 · 이벤트 표지(`loadEvents` 재사용 — 그 기간 타임라인을 이미 가졌으면
 * 추가 조회 0). 갱신은 1h만 60초 자동, 나머지는 수동이다. 진행 중 요청이 있으면 새로 보내지 않는다.
 * [넓게 보기]는 같은 창에서 목록 칸을 접는다(새 창 0). 값이 없는 칸은 선을 끊는다(보간 0) · APM 임계선 0.
 *
 * (W9 · W11) 「진단」 탭 = ⑧ 진단 패널. 기능 조회 `biz_diagnosis_enabled`가 true일 때만 탭이 보인다. D1 판독 1줄
 * (`verdict_text` + 「가설 · 참고용」) · 안내 · 상관≠인과 문구 · 이상 구성원(최초 이상 순 · 저장된 조사가 있으면 원인 영역 +
 * 같은 창 장애 진단 탭) · 결손(건수) · 「일부만」 사유 · [서비스 심층 조사](대표 서버가 있고 `investigation_enabled`일
 * 때만 — 아니면 사유) → 같은 탭 안 미리보기 + [접수](확인 대화상자 0 · 요청 본문 0) → 성공이면
 * `onOpenInvestigation(id)` · 거부면 사유 · 「진단 이력」 최근 10(행 → 같은 창 장애 진단 탭). 탭을 처음 열 때 1회 읽고
 * 그 뒤는 수동 [새로고침]만(30초 상세 갱신에 진단 요청 0 · pause면 요청 0).
 */
(function (root) {
    "use strict";

    var API = "/api/v1/biz-services/monitor";
    var POLL_MS = 30000;
    var HEARTBEAT_MS = 30000;
    var LAYOUT_KEY = "biz_monitor_layout";
    var FOCUS_MS = 2500;
    var ASK_MAX_NAMES = 20;
    var SVG_NS = "http://www.w3.org/2000/svg";

    // 상태 어휘(§4.4) — 순서는 요약 띠 표기 순
    var STATE_KEYS = ["risk", "caution", "no_signal", "unknown"];
    var STATE_LABELS = { risk: "위험", caution: "주의", no_signal: "이상 신호 없음", unknown: "판정 불가" };
    var STATE_RANK = { risk: 0, caution: 1, unknown: 2, no_signal: 3 };
    var RULE_LABELS = {
        incident_critical: "열린 사건(심각)", incident_open: "열린 사건", apm_signal: "APM 신호",
        sms_crit: "SMS 위험 임계", sms_warn: "SMS 주의 임계",
    };
    var METRIC_LABELS = {
        cpu: "CPU", memory: "메모리", filesystem: "파일시스템",
        response_time_avg_ms: "응답시간", tps: "TPS", error_rate: "에러율", active_services: "액티브",
    };
    var METRIC_UNITS = { cpu: "%", memory: "%", filesystem: "%", response_time_avg_ms: "ms", error_rate: "%" };
    var SPARKS = [
        { group: "apm_max", key: "response_time_avg_ms", label: "APM 응답시간" },
        { group: "apm_max", key: "error_rate", label: "APM 에러율" },
        { group: "sms_max", key: "cpu", label: "SMS CPU" },
    ];
    var SPARK_GAP_SECONDS = 900;   // 이보다 먼 두 점은 잇지 않는다(보간 0)
    // (W8b) 이벤트 타임라인
    var EVENT_WINDOWS = ["1h", "6h", "24h"];
    var EVENT_DEFAULT_WINDOW = "1h";
    var EVENTS_REFRESH_MS = 60000;
    // (§5.2.1) 주소 `&at=` — 이벤트 보기를 그 시각으로 연다. 형식 오류·미래·7일 넘은 과거는 무시한다(사유 문구 0)
    var EVENT_WINDOW_MS = { "1h": 3600000, "6h": 21600000, "24h": 86400000 };
    var AT_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?Z$/;
    var AT_MAX_AGE_MS = 7 * 86400000;
    var AT_FUTURE_SKEW_MS = 5 * 60000;      // 브라우저·서버 시계 차이 여유(이보다 앞선 시각은 미래로 본다)
    var EVENT_SEVERITIES = [
        { value: "critical", label: "심각" }, { value: "warning", label: "경고" },
        { value: "attention", label: "주의" }, { value: "clear", label: "해소" },
    ];
    var EVENT_SEVERITY_CLASS = { critical: "risk", warning: "caution", attention: "unknown", clear: "no_signal" };
    var CAUSALITY_NOTE = "시간이 가까울 뿐 원인이라는 뜻은 아닙니다(상관 ≠ 인과)";
    var EVENT_SEVERITY_RANK = { critical: 0, warning: 1, attention: 2, clear: 3 };
    // (W8c) 지표 보기
    var CAPS_URL = "/api/v1/alarm/capabilities";
    var METRIC_WINDOWS = ["1h", "6h", "24h", "7d"];
    var METRIC_DEFAULT_WINDOW = "24h";
    var METRICS_AUTO_WINDOW = "1h";          // 이 기간만 60초 자동 갱신
    var METRICS_AUTO_MS = 60000;
    var METRIC_EVENT_WINDOW = { "1h": "1h", "6h": "6h", "24h": "24h", "7d": "24h" };   // 표지에 쓸 타임라인 기간
    var CHART_MAX_LINES = 8;                 // 그래프 하나의 선(구성원) 상한 — 넘으면 「외 N대」
    var MARKER_BUCKETS = 120;                // 표지 칸 수(기간 / 120)
    var LANE_ORDER = ["web", "was", "db", "batch", "etc", "none"];
    var LANE_LABELS = { web: "웹", was: "WAS", db: "DB", batch: "배치", etc: "기타", none: "미지정" };
    var SOURCE_STATE_LABELS = {
        ok: "조회됨", partial: "일부만 조회", failed: "조회 실패", unset: "미설정", none: "조회 대상 없음",
        unsupported: "이 기간 미지원",
    };
    // (W9 · W11) 진단 보기 — 조사 상태 · 저장된 원인 영역(briefing-view.js `LAYER_LABELS`와 같은 enum · 모르는 값은 원문)
    var DIAG_STATUS_LABELS = {
        submitted: "접수", running: "진행 중", done: "완료", failed: "실패", timeout: "시간 초과", rejected: "거부",
    };
    var CAUSE_LAYER_LABELS = {
        db: "DB(SQL·커넥션 풀)", external: "외부 연계(외부 호출)", cpu: "WAS 애플리케이션(CPU 처리)",
        gc: "WAS JVM(GC·힙)", host_resource: "서버 자원(OS·메모리·디스크)",
    };

    // ─── 순수 함수(브라우저 없이 node로 검사한다) ───────────────────────────

    function text(v) { return v === null || v === undefined ? "" : String(v); }

    function stateRank(s) { return STATE_RANK[s] === undefined ? 9 : STATE_RANK[s]; }

    function stateLabel(s, given) { return given || STATE_LABELS[s] || "판정 불가"; }

    function stateClass(s) { return "bm-s--" + (STATE_LABELS[s] ? s : "unknown"); }

    function pad2(n) { return n < 10 ? "0" + n : String(n); }

    function hhmm(iso) {
        if (!iso) return "";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return "";
        return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    }

    // `YYYY-MM-DD HH:00`(SMS 최신 정각 통계) → 「HH시 통계」
    function hourLabel(statTime) {
        var m = /(\d{4})-(\d{2})-(\d{2})[ T](\d{2})/.exec(text(statTime));
        return m ? Number(m[4]) + "시 통계" : "";
    }

    function fmtNum(v, unit) {
        if (typeof v !== "number" || !isFinite(v)) return "-";
        var s = Math.abs(v) >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10);
        return s + (unit || "");
    }

    function metricText(name, v) { return fmtNum(v, METRIC_UNITS[name] || ""); }

    function filterOptions(services) {
        var zones = {}, tiers = {};
        (services || []).forEach(function (s) {
            (s.zones || []).forEach(function (z) { zones[z] = true; });
            if (typeof s.tier === "number") tiers[s.tier] = true;
        });
        return {
            zones: Object.keys(zones).sort(),
            tiers: Object.keys(tiers).map(Number).sort(function (a, b) { return a - b; }),
        };
    }

    // f = {state, zone, tier, monitoredOnly, q} — 서버 정렬(상태 나쁜 순 → 등급 → 이름)은 그대로 둔다
    function filterServices(services, f) {
        var q = text(f && f.q).trim().toLowerCase();
        return (services || []).filter(function (s) {
            if (f.state && s.state !== f.state) return false;
            if (f.zone && (s.zones || []).indexOf(f.zone) < 0) return false;
            if (f.tier !== "" && f.tier !== undefined && f.tier !== null && String(s.tier) !== String(f.tier)) return false;
            if (f.monitoredOnly && s.monitored !== true) return false;
            if (q && text(s.display_name).toLowerCase().indexOf(q) < 0) return false;
            return true;
        });
    }

    function sortMembers(members) {
        return (members || []).slice().sort(function (a, b) {
            var d = stateRank(a.state) - stateRank(b.state);
            if (d) return d;
            var na = text(a.name).toLowerCase(), nb = text(b.name).toLowerCase();
            if (na !== nb) return na < nb ? -1 : 1;
            return (a.id || 0) - (b.id || 0);
        });
    }

    // 판정 근거 1줄 — 근거가 있으면 「어느 구성원 · 어떤 값 · 어떤 규칙」, 없으면 사유(판정 불가 등)
    function evidenceLine(service) {
        var ev = (service && service.evidence) || [];
        if (ev.length) {
            var e = ev[0];
            var parts = [e.member_name || e.member_key || "서비스"];
            if (e.metric) {
                var v = metricText(e.metric, e.value);
                parts.push((METRIC_LABELS[e.metric] || e.metric) + " " + v +
                    (typeof e.threshold === "number" ? " (임계 " + metricText(e.metric, e.threshold) + ")" : ""));
            }
            parts.push(RULE_LABELS[e.rule] || e.rule || "");
            var line = parts.filter(Boolean).join(" · ");
            return ev.length > 1 ? line + " 외 " + (ev.length - 1) + "건" : line;
        }
        return ((service && service.reasons) || []).join(" · ");
    }

    // [질의응답에서 묻기] 질문 — 보이는 SMS 구성원 서버 이름(상태 나쁜 순 · ≤20)
    function askText(detail) {
        var svc = (detail && detail.service) || {};
        var names = [];
        sortMembers((detail && detail.members) || []).forEach(function (m) {
            if (m.kind !== "sms_server") return;
            var n = m.server_name || m.hostname || m.name;
            if (n && names.indexOf(n) < 0) names.push(n);
        });
        var title = "업무 서비스 「" + text(svc.display_name) + "」";
        if (!names.length) return title + "의 최근 1시간 알람 현황을 알려줘";
        var shown = names.slice(0, ASK_MAX_NAMES).join(", ");
        var rest = names.length > ASK_MAX_NAMES ? " 외 " + (names.length - ASK_MAX_NAMES) + "대" : "";
        return title + " 구성원 서버 " + shown + rest + "의 최근 1시간 CPU·메모리 사용률과 열린 알람을 알려줘";
    }

    // 기능 조회가 필요한 탭(`caps: true`)은 응답 전(`ctx.caps` 없음)에는 그대로 둔다 — 받은 뒤 다시 고른다
    function resolveTab(tabs, id, ctx) {
        ctx = ctx || {};
        var ok = (tabs || []).filter(function (t) {
            return !t.enabled || (t.caps && !ctx.caps) || t.enabled(ctx);
        });
        for (var i = 0; i < ok.length; i++) if (ok[i].id === id) return id;
        return "overview";
    }

    // (W8b) 이벤트 조회 주소 — 억제 포함은 관리자가 켰을 때만 싣는다(서버가 다시 막는다)
    function eventsUrl(serviceId, q) {
        var w = EVENT_WINDOWS.indexOf(q && q.window) >= 0 ? q.window : EVENT_DEFAULT_WINDOW;
        return API + "/" + encodeURIComponent(serviceId) + "/events?window=" + w +
            (q && q.includeSuppressed ? "&include_suppressed=true" : "");
    }

    // f = {off: {소스: true}, severity, member} — 서버 순서(고정 행 먼저 · 최신 순)는 그대로 둔다
    function filterEventRows(rows, f) {
        f = f || {};
        return (rows || []).filter(function (r) {
            if (f.off && f.off[r.source]) return false;
            if (f.severity && r.severity !== f.severity) return false;
            if (f.member && r.member_key !== f.member) return false;
            return true;
        });
    }

    // 구성원 필터 선택지(행에 나온 구성원만 · 이름 순)
    function eventMembers(rows) {
        var seen = {};
        (rows || []).forEach(function (r) {
            if (r.member_key && !seen[r.member_key]) seen[r.member_key] = text(r.member_name) || r.member_key;
        });
        return Object.keys(seen).map(function (k) { return { value: k, label: seen[k] }; })
            .sort(function (a, b) { return a.label < b.label ? -1 : a.label > b.label ? 1 : 0; });
    }

    // 행 시각 — 24시간 창은 날짜를 붙인다
    function eventTime(iso, win) {
        var t = hhmm(iso);
        if (!t || win !== "24h") return t;
        var d = new Date(iso);
        return pad2(d.getMonth() + 1) + "/" + pad2(d.getDate()) + " " + t;
    }

    // (§5.2.1) 주소 `at`(ISO 8601 UTC) → epoch ms · 형식 오류·없는 날짜·미래·7일 넘은 과거는 null
    function parseAt(value, now) {
        var s = text(value);
        if (!AT_PATTERN.test(s)) return null;
        var t = Date.parse(s);
        if (isNaN(t)) return null;
        var iso = new Date(t).toISOString();   // 2월 30일·24시 같은 넘김 날짜를 거른다
        if (iso.slice(0, 16) !== s.slice(0, 16) || (s.length > 17 && iso.slice(17, 19) !== s.slice(17, 19))) return null;
        if (t > now + AT_FUTURE_SKEW_MS || now - t > AT_MAX_AGE_MS) return null;
        return t;
    }

    // 그 시각을 담는 이벤트 기간 — 지금 기간이 담으면 그대로, 아니면 담는 가장 작은 기간(없으면 가장 긴 기간)
    function atWindow(atMs, now, current) {
        var age = Math.max(0, now - atMs);
        if (EVENT_WINDOW_MS[current] && age <= EVENT_WINDOW_MS[current]) return current;
        for (var i = 0; i < EVENT_WINDOWS.length; i++) if (age <= EVENT_WINDOW_MS[EVENT_WINDOWS[i]]) return EVENT_WINDOWS[i];
        return EVENT_WINDOWS[EVENT_WINDOWS.length - 1];
    }

    // 그 시각에 가장 가까운 행(같으면 앞 행) — 없으면 null
    function nearestEvent(rows, atMs) {
        var best = null, gap = Infinity;
        (rows || []).forEach(function (r) {
            var t = epochOf(r && r.at);
            if (t === null) return;
            var d = Math.abs(t * 1000 - atMs);
            if (d < gap) { gap = d; best = r; }
        });
        return best;
    }

    // epoch ms → 주소용 ISO 8601 UTC(밀리초가 0이면 뗀다)
    function atIso(ms) { return new Date(ms).toISOString().replace(/\.000Z$/, "Z"); }

    // 실행 여부 — 포털 탭이 보이고(active) 브라우저 탭이 숨지 않았을 때만
    function runnable(active, hidden) { return !!active && !hidden; }

    // 주기 작업 — start: 즉시 1회 + 주기 · stop: 예약 취소(진행 중 요청 끊기는 부른 쪽)
    function createPoller(task, ms, timers) {
        var t = timers || { setTimeout: setTimeout, clearTimeout: clearTimeout };
        var handle = null, on = false;
        function loop() {
            handle = null;
            if (!on) return;
            Promise.resolve().then(task).catch(function () {}).then(function () {
                if (on && handle === null) handle = t.setTimeout(loop, ms);
            });
        }
        return {
            start: function () { if (on) return; on = true; loop(); },
            stop: function () {
                on = false;
                if (handle !== null) t.clearTimeout(handle);
                handle = null;
            },
            running: function () { return on; },
        };
    }

    // 구성 맵 배치 — 레인(행) 안에 APM 칩 줄 → 서버 칩 줄 · 칸 수는 폭으로 정한다(같은 입력·폭 = 같은 배치)
    var TOPO = { labelW: 76, chipW: 160, chipH: 36, gapX: 8, gapY: 8, pad: 8 };

    function layoutTopology(topo, width) {
        var W = Math.max(480, Math.floor(width || 880));
        var perRow = Math.max(1, Math.floor((W - TOPO.labelW - TOPO.pad) / (TOPO.chipW + TOPO.gapX)));
        var y = 0;
        var out = { width: W, height: 0, perRow: perRow, lanes: [], nodes: [], folds: [], edges: [] };
        var pos = {};
        ((topo && topo.lanes) || []).forEach(function (lane) {
            var nodes = lane.nodes || [];
            var groups = [
                nodes.filter(function (n) { return n.kind !== "sms_server"; }),
                nodes.filter(function (n) { return n.kind === "sms_server"; }),
            ].filter(function (g) { return g.length; });
            if (lane.folded_count > 0) {
                if (!groups.length) groups.push([]);
                groups[groups.length - 1] = groups[groups.length - 1].concat([{ fold: lane.folded_count }]);
            }
            var row = 0;
            groups.forEach(function (g) {
                g.forEach(function (item, i) {
                    var x = TOPO.labelW + (i % perRow) * (TOPO.chipW + TOPO.gapX);
                    var cy = y + TOPO.gapY + (row + Math.floor(i / perRow)) * (TOPO.chipH + TOPO.gapY);
                    if (item.fold) {
                        out.folds.push({ layer: lane.layer, count: item.fold, x: x, y: cy });
                    } else {
                        var box = { node: item, x: x, y: cy, w: TOPO.chipW, h: TOPO.chipH };
                        out.nodes.push(box);
                        pos[item.id] = box;
                    }
                });
                row += Math.ceil(g.length / perRow);
            });
            var h = Math.max(1, row) * (TOPO.chipH + TOPO.gapY) + TOPO.gapY;
            out.lanes.push({ layer: lane.layer, label: lane.label, y: y, h: h, empty: !nodes.length && !lane.folded_count });
            y += h;
        });
        out.height = y;
        ((topo && topo.edges) || []).forEach(function (e) {
            var a = pos[e.from], b = pos[e.to];
            if (!a || !b) return;
            var seg;
            if (Math.abs(a.y - b.y) < 1) {
                var right = b.x > a.x;
                seg = { x1: right ? a.x + a.w : a.x, y1: a.y + a.h / 2, x2: right ? b.x : b.x + b.w, y2: b.y + b.h / 2 };
            } else {
                var down = b.y > a.y;
                seg = { x1: a.x + a.w / 2, y1: down ? a.y + a.h : a.y, x2: b.x + b.w / 2, y2: down ? b.y : b.y + b.h };
            }
            seg.edge = e;
            out.edges.push(seg);
        });
        return out;
    }

    function epochOf(iso) {
        var t = new Date(iso).getTime();
        return isNaN(t) ? null : t / 1000;
    }

    // 상태 띠 구간 → 막대 [{x, w, state, label, start, end}]
    function bandLayout(segments, since, now, width) {
        var s0 = epochOf(since), s1 = epochOf(now);
        if (s0 === null || s1 === null || s1 <= s0) return [];
        var span = s1 - s0;
        return (segments || []).map(function (seg) {
            var a = Math.max(s0, epochOf(seg.start) || s0), b = Math.min(s1, epochOf(seg.end) || s1);
            return {
                x: ((a - s0) / span) * width, w: Math.max(0, ((b - a) / span) * width),
                state: seg.state, label: seg.state_label, start: seg.start, end: seg.end,
            };
        }).filter(function (r) { return r.w > 0; });
    }

    // 스파크라인 — 값 있는 점만 · 없는 칸·먼 간격에서 선을 끊는다 → {lines: [[[x, y]…]…], last, min, max}
    function sparkSeries(points, spec, since, now, width, height) {
        var s0 = epochOf(since), s1 = epochOf(now);
        var vals = [];
        (points || []).forEach(function (p) {
            var g = p && p[spec.group];
            var v = g && g[spec.key];
            var t = epochOf(p && p.at);
            vals.push({ t: t, v: typeof v === "number" && isFinite(v) ? v : null });
        });
        var present = vals.filter(function (p) { return p.v !== null && p.t !== null; });
        if (!present.length || s0 === null || s1 === null || s1 <= s0) {
            return { lines: [], last: null, min: null, max: null };
        }
        var min = 0, max = Math.max.apply(null, present.map(function (p) { return p.v; }));
        if (max <= min) max = min + 1;
        var lines = [], cur = [], prevT = null;
        vals.forEach(function (p) {
            if (p.t === null || p.v === null) {
                if (cur.length) lines.push(cur);
                cur = []; prevT = null;
                return;
            }
            if (prevT !== null && p.t - prevT > SPARK_GAP_SECONDS && cur.length) { lines.push(cur); cur = []; }
            var x = ((p.t - s0) / (s1 - s0)) * width;
            var y = height - ((p.v - min) / (max - min)) * (height - 2) - 1;
            cur.push([Math.round(x * 10) / 10, Math.round(y * 10) / 10]);
            prevT = p.t;
        });
        if (cur.length) lines.push(cur);
        return { lines: lines, last: present[present.length - 1].v, min: min, max: max };
    }

    // (W8c) 지표 조회 주소 — 구성원은 고른 경우만 · 어제 비교는 7d 밖에서만
    function metricsUrl(serviceId, q) {
        var w = METRIC_WINDOWS.indexOf(q && q.window) >= 0 ? q.window : METRIC_DEFAULT_WINDOW;
        var ids = ((q && q.members) || []).filter(function (i) { return /^\d+$/.test(String(i)); });
        return API + "/" + encodeURIComponent(serviceId) + "/metrics?window=" + w +
            (ids.length ? "&members=" + ids.join(",") : "") +
            (q && q.compare && w !== "7d" ? "&compare=yesterday" : "");
    }

    // [[iso, 값]…] → [{x: ms, y}] — 값 없는 점은 버리고, 간격이 한 칸(step)의 1.5배를 넘으면 y=null 칸을 넣어
    // 선을 끊는다(보간 0 · 그래프 `spanGaps: false`).
    function gapPoints(points, stepSeconds) {
        var out = [], prev = null;
        var limit = stepSeconds > 0 ? stepSeconds * 1500 : Infinity;
        (points || []).forEach(function (p) {
            var t = epochOf(p && p[0]);
            var v = p && p[1];
            if (t === null || typeof v !== "number" || !isFinite(v)) return;
            t = t * 1000;
            if (prev !== null && t - prev > limit) out.push({ x: (prev + t) / 2, y: null });
            out.push({ x: t, y: v });
            prev = t;
        });
        return out;
    }

    function laneOf(layer) {
        var v = text(layer).toLowerCase();
        return LANE_ORDER.indexOf(v) >= 0 ? v : "none";
    }

    // 시계열 → 계층 레인별 그래프 [{lane, label, charts: [{key, source, metric, title, unit, members, lines, more}]}]
    // 그래프는 (소스, 지표)마다 하나 · 선은 구성원 `maxLines`까지(어제 선은 같은 구성원 몫) · 넘친 구성원 수는 `more`.
    function laneGroups(series, members, maxLines) {
        var cap = maxLines || CHART_MAX_LINES;
        var layerOf = {};
        (members || []).forEach(function (m) { layerOf[m.id] = m.layer; });
        var lanes = {};
        (series || []).forEach(function (s) {
            var lane = laneOf(layerOf[s.member_id]);
            var L = lanes[lane] || (lanes[lane] = { keys: [], charts: {} });
            var key = s.source + ":" + s.metric;
            var c = L.charts[key];
            if (!c) {
                c = L.charts[key] = { key: key, source: s.source, metric: s.metric, title: s.label || s.metric,
                    unit: s.unit || "", members: [], lines: [], extra: {} };
                L.keys.push(key);
            }
            if (c.members.indexOf(s.member_id) < 0) {
                if (c.members.length >= cap) { c.extra[s.member_id] = true; return; }
                c.members.push(s.member_id);
            }
            c.lines.push(s);
        });
        return LANE_ORDER.filter(function (l) { return lanes[l]; }).map(function (l) {
            var charts = lanes[l].keys.map(function (k) {
                var c = lanes[l].charts[k];
                c.more = Object.keys(c.extra).length;
                delete c.extra;
                return c;
            });
            charts.sort(function (a, b) { return (a.source === "sms" ? 0 : 1) - (b.source === "sms" ? 0 : 1); });
            return { lane: l, label: LANE_LABELS[l], charts: charts };
        });
    }

    // 타임라인 행 → 표지 [{from, to, pos, count, worst, last}] — 기간을 `buckets`칸으로 나눠 칸마다 1개(건수 합 = 기간 안 행 수)
    // `last`는 그 칸 마지막 행 시각(ms) — 표지를 누르면 이 시각이 주소 `&at=`이 된다
    function eventMarkers(rows, since, until, buckets) {
        var s0 = epochOf(since), s1 = epochOf(until);
        if (s0 === null || s1 === null || s1 <= s0) return [];
        var n = Math.max(1, buckets || MARKER_BUCKETS);
        var w = (s1 - s0) / n;
        var by = {};
        (rows || []).forEach(function (r) {
            var t = epochOf(r && r.at);
            if (t === null || t < s0 || t > s1) return;
            var i = Math.min(n - 1, Math.floor((t - s0) / w));
            var m = by[i] || (by[i] = { from: (s0 + i * w) * 1000, to: (s0 + (i + 1) * w) * 1000,
                pos: (i + 0.5) / n, count: 0, worst: "", last: 0 });
            m.count += 1;
            if (t * 1000 > m.last) m.last = t * 1000;
            var rank = EVENT_SEVERITY_RANK[r.severity];
            if (rank !== undefined && (!m.worst || rank < EVENT_SEVERITY_RANK[m.worst])) m.worst = r.severity;
        });
        return Object.keys(by).map(Number).sort(function (a, b) { return a - b; })
            .map(function (i) { return by[i]; });
    }

    // 커서 시각(ms)에 가장 가까운 점 — 반 칸 안에 점이 없으면 값 없음(보간 0)
    function cursorValues(lines, t) {
        return (lines || []).map(function (s) {
            var best = null, gap = Infinity, half = (s.step_seconds || 60) * 500;
            (s.points || []).forEach(function (p) {
                var x = epochOf(p && p[0]);
                if (x === null || typeof p[1] !== "number") return;
                var d = Math.abs(x * 1000 - t);
                if (d < gap) { gap = d; best = p; }
            });
            var hit = best && gap <= half;
            return { series: s, value: hit ? best[1] : null, at: hit ? best[0] : null };
        });
    }

    // 축·표 시각 — 7d는 날짜, 그 밖은 시:분(표는 날짜를 붙인다)
    function metricTime(ms, win, withDate) {
        var d = new Date(ms);
        if (isNaN(d.getTime())) return "";
        var day = pad2(d.getMonth() + 1) + "/" + pad2(d.getDate());
        if (win === "7d") return day;
        var t = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
        return withDate ? day + " " + t : t;
    }

    // SMS 점의 이름 — 시간 통계는 「HH시 통계」 · 일 통계는 「MM/DD 일 통계」
    function statLabel(iso, win) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) return "";
        if (win === "7d") return pad2(d.getMonth() + 1) + "/" + pad2(d.getDate()) + " 일 통계";
        return d.getHours() + "시 통계";
    }

    // ─── DOM 도우미(textContent만) ───────────────────────────────────────

    function el(tag, cls, txt) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (txt !== undefined && txt !== null) n.textContent = String(txt);
        return n;
    }

    function svgEl(tag, attrs) {
        var n = document.createElementNS(SVG_NS, tag);
        Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, String(attrs[k])); });
        return n;
    }

    function badge(state, label) {
        return el("span", "bm-badge " + stateClass(state), stateLabel(state, label));
    }

    function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

    function truncate(s, n) { s = text(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

    // P1 「업무 서비스 정의」로 가는 유일한 새 탭(§5.0 — P1은 넓은 편집 작업 화면 · D-248)
    function definitionLink(serviceId, label) {
        var a = el("a", "bm-btn bm-btn--link", label);
        a.href = "/biz-services" + (serviceId ? "?service=" + encodeURIComponent(serviceId) : "");
        a.target = "_blank";
        a.rel = "noopener";
        return a;
    }

    // ─── 상세 보기 탭(확장 지점) ─────────────────────────────────────────
    var DETAIL_TABS = [
        { id: "overview", label: "개요", enabled: function () { return true; }, refresh: refreshOverview },
        { id: "metrics", label: "지표", caps: true, refresh: refreshMetrics, leave: leaveMetrics,
          enabled: function (ctx) { return !!(ctx.caps && ctx.caps.biz_history_enabled === true); } },
        { id: "events", label: "이벤트", enabled: function () { return true; }, refresh: refreshEvents },
        { id: "diagnosis", label: "진단", caps: true, refresh: refreshDiagnosis,
          enabled: function (ctx) { return !!(ctx.caps && ctx.caps.biz_diagnosis_enabled === true); } },
    ];

    function refreshOverview(ctx) {
        var id = encodeURIComponent(ctx.serviceId);
        return Promise.all([
            ctx.request(API + "/" + id + "/topology"),
            ctx.request(API + "/" + id + "/trend"),
        ]).then(function (res) {
            renderOverview(ctx, res[0].ok ? res[0].body : null, res[1].ok ? res[1].body : null,
                [res[0], res[1]].filter(function (r) { return !r.ok; }).map(function (r) { return r.reason; }));
        });
    }

    function section(panel, title) {
        var box = el("section", "bm-section");
        box.appendChild(el("h3", "bm-section-title", title));
        panel.appendChild(box);
        return box;
    }

    function renderOverview(ctx, topo, trend, errors) {
        var panel = ctx.panel;
        clear(panel);
        errors.forEach(function (r) { panel.appendChild(el("p", "bm-note bm-note--warn", r)); });
        var mapBox = section(panel, "구성 맵");
        if (topo) renderTopology(mapBox, topo, ctx);
        var tableBox = section(panel, "구성원 상태");
        renderMemberTables(tableBox, ctx, topo);
        var trendBox = section(panel, "추이 (24시간)");
        if (trend) renderTrend(trendBox, trend);
    }

    function renderTopology(box, topo, ctx) {
        if (!topo.links_status || !topo.links_status.available) {
            box.appendChild(el("p", "bm-note", (topo.links_status && topo.links_status.reason) || "연계 선 없음"));
        }
        if (topo.folded) {
            box.appendChild(el("p", "bm-note", "구성원이 많아 레인마다 일부만 보입니다 — 전체는 아래 구성원 상태 표"));
        }
        var width = (box.clientWidth || 880) - 2;
        var lay = layoutTopology(topo, width);
        var svg = svgEl("svg", {
            class: "bm-topo", width: lay.width, height: lay.height, viewBox: "0 0 " + lay.width + " " + lay.height,
            role: "group", "aria-label": "구성 맵 — 계층 레인",
        });
        lay.lanes.forEach(function (lane, i) {
            svg.appendChild(svgEl("rect", { class: "bm-lane" + (i % 2 ? " bm-lane--alt" : ""), x: 0, y: lane.y, width: lay.width, height: lane.h }));
            var t = svgEl("text", { class: "bm-lane-label", x: 8, y: lane.y + 20 });
            t.textContent = lane.label;
            svg.appendChild(t);
            if (lane.empty) {
                var e = svgEl("text", { class: "bm-lane-empty", x: TOPO.labelW, y: lane.y + 20 });
                e.textContent = "구성원 없음";
                svg.appendChild(e);
            }
        });
        lay.edges.forEach(function (s) {
            var cls = "bm-edge" + (s.edge.estimated ? " bm-edge--estimated" : "") + (s.edge.broken ? " bm-edge--broken" : "");
            var line = svgEl("line", { class: cls, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2 });
            var tt = svgEl("title");
            tt.textContent = (s.edge.broken ? "끊김(대상 사라짐) · " : s.edge.estimated ? "추정 연계 · " : "확정 연계 · ") + text(s.edge.label);
            line.appendChild(tt);
            svg.appendChild(line);
        });
        lay.nodes.forEach(function (b) {
            var n = b.node;
            var g = svgEl("g", { class: "bm-chip " + stateClass(n.state), tabindex: 0, role: "button", "data-bm-node": n.id });
            g.appendChild(svgEl("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: 6 }));
            var name = svgEl("text", { class: "bm-chip-name", x: b.x + 8, y: b.y + 15 });
            name.textContent = truncate(n.name, 20);
            g.appendChild(name);
            var meta = svgEl("text", { class: "bm-chip-meta", x: b.x + 8, y: b.y + 29 });
            meta.textContent = stateLabel(n.state, n.state_label) + (n.kind === "sms_server" ? " · 서버" : " · APM") +
                (n.confidence === "inferred" ? " · 추정" : "");
            g.appendChild(meta);
            var tt = svgEl("title");
            tt.textContent = text(n.name) + " — " + stateLabel(n.state, n.state_label) + (n.link_note ? " · " + n.link_note : "");
            g.appendChild(tt);
            function go() { ctx.focusMember(n.id); }
            g.addEventListener("click", go);
            g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
            svg.appendChild(g);
        });
        lay.folds.forEach(function (f) {
            var t = svgEl("text", { class: "bm-fold", x: f.x + 8, y: f.y + 22 });
            t.textContent = "외 " + f.count + "대";
            svg.appendChild(t);
        });
        var wrap = el("div", "bm-topo-wrap");
        wrap.appendChild(svg);
        box.appendChild(wrap);
        box.appendChild(el("p", "bm-legend", "실선 = 확정 연계 · 점선 = 추정 연계(자동) · 붉은 점선 = 끊김(대상 사라짐) · 칩을 누르면 아래 표의 그 행으로 이동합니다"));
        if (topo.hidden_member_count) box.appendChild(el("p", "bm-note", "권한 밖 " + topo.hidden_member_count + "대"));
    }

    function linkedServers(topo, memberId) {
        var names = {};
        ((topo && topo.lanes) || []).forEach(function (l) { (l.nodes || []).forEach(function (n) { names[n.id] = n.name; }); });
        var node = null;
        ((topo && topo.lanes) || []).forEach(function (l) { (l.nodes || []).forEach(function (n) { if (n.id === memberId) node = n; }); });
        var parts = ((topo && topo.edges) || []).filter(function (e) { return e.from === memberId; }).map(function (e) {
            return text(names[e.to]) + (e.broken ? "(끊김)" : e.estimated ? "(추정)" : "");
        });
        if (node && node.outside_targets) parts.push("서비스 밖 " + node.outside_targets + "대");
        if (!parts.length && node && node.link_note) parts.push(node.link_note);
        return parts.join(", ") || "-";
    }

    function cell(tr, value, cls) { var td = el("td", cls || "", value); tr.appendChild(td); return td; }

    function memberTable(box, caption, heads, rows) {
        var wrap = el("div", "bm-table-wrap");
        var table = el("table", "bm-table");
        table.appendChild(el("caption", "", caption));
        var thead = el("thead"), hr = el("tr");
        heads.forEach(function (h) { hr.appendChild(el("th", "", h)); });
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = el("tbody");
        rows.forEach(function (r) { tbody.appendChild(r); });
        table.appendChild(tbody);
        wrap.appendChild(table);
        box.appendChild(wrap);
    }

    function memberRow(m) {
        var tr = el("tr", "bm-row");
        tr.setAttribute("data-bm-member", String(m.id));
        tr.tabIndex = -1;
        return tr;
    }

    function actionCell(tr, m, ctx) {
        var td = el("td", "bm-actions");
        if (ctx.opts.onInvestigateMember) {
            var btn = el("button", "bm-btn bm-btn--small", "이 구성원 알람 조사");
            btn.type = "button";
            btn.addEventListener("click", function () {
                var moved = ctx.opts.onInvestigateMember({
                    id: m.id, kind: m.kind, name: m.name, server_name: m.server_name,
                    hostname: m.hostname, instance_name: m.instance_name,
                });
                if (moved === false) ctx.flash("알람 조사로 옮기지 못했습니다 — 이벤트 알람 탭을 볼 수 없거나 답변 중입니다");
            });
            td.appendChild(btn);
        }
        tr.appendChild(td);
    }

    function stateCell(tr, m) {
        var td = el("td");
        td.appendChild(badge(m.state, m.state_label));
        if ((m.reasons || []).length && m.state === "unknown") td.appendChild(el("div", "bm-sub", m.reasons.join(" · ")));
        tr.appendChild(td);
    }

    function renderMemberTables(box, ctx, topo) {
        var detail = ctx.detail || {};
        var members = sortMembers(detail.members || []);
        var sms = members.filter(function (m) { return m.kind === "sms_server"; });
        var apm = members.filter(function (m) { return m.kind !== "sms_server"; });
        if (detail.hidden_member_count) box.appendChild(el("p", "bm-note", "권한 밖 " + detail.hidden_member_count + "대 — 이름·값은 보이지 않습니다"));
        if (!members.length) { box.appendChild(el("p", "bm-empty", "볼 수 있는 구성원이 없습니다")); return; }
        if (sms.length) {
            memberTable(box, "SMS 서버", ["서버", "존", "계층", "상태", "CPU", "메모리", "파일시스템", "열린 알람", "마지막 알람", ""],
                sms.map(function (m) {
                    var tr = memberRow(m);
                    cell(tr, m.name);
                    cell(tr, m.zone || "-");
                    cell(tr, m.layer || "미지정");
                    stateCell(tr, m);
                    ["cpu", "memory", "filesystem"].forEach(function (k) { cell(tr, metricText(k, (m.sms || {})[k]), "bm-num"); });
                    cell(tr, String((m.alarms || {}).open_incidents || 0), "bm-num");
                    cell(tr, hhmm((m.alarms || {}).last_alarm_at) || "-");
                    actionCell(tr, m, ctx);
                    return tr;
                }));
        }
        if (apm.length) {
            memberTable(box, "APM", ["인스턴스", "이어진 서버", "상태", "응답시간", "TPS", "에러율", "액티브", "신호", ""],
                apm.map(function (m) {
                    var tr = memberRow(m);
                    cell(tr, m.name);
                    cell(tr, m.kind === "apm_instance" ? linkedServers(topo, m.id) : "-");
                    stateCell(tr, m);
                    ["response_time_avg_ms", "tps", "error_rate", "active_services"].forEach(function (k) {
                        cell(tr, metricText(k, (m.apm || {})[k]), "bm-num");
                    });
                    cell(tr, (m.apm_signals || []).join(", ") || "-");
                    actionCell(tr, m, ctx);
                    return tr;
                }));
        }
    }

    function renderTrend(box, trend) {
        var width = Math.max(320, (box.clientWidth || 880) - 2);
        var band = trend.band || {};
        if (band.reason) box.appendChild(el("p", "bm-note", band.reason));
        else {
            var bars = bandLayout(band.segments, trend.since, trend.generated_at, width);
            var svg = svgEl("svg", { class: "bm-band", width: width, height: 20, viewBox: "0 0 " + width + " 20", role: "img", "aria-label": "24시간 상태 띠" });
            bars.forEach(function (b) {
                var r = svgEl("rect", { class: "bm-band-seg " + stateClass(b.state), x: b.x, y: 0, width: b.w, height: 20 });
                var tt = svgEl("title");
                tt.textContent = hhmm(b.start) + "–" + hhmm(b.end) + " " + text(b.label);
                r.appendChild(tt);
                svg.appendChild(r);
            });
            box.appendChild(svg);
            var axis = el("div", "bm-band-axis");
            axis.appendChild(el("span", "", hhmm(trend.since)));
            axis.appendChild(el("span", "", "지금 " + hhmm(trend.generated_at)));
            box.appendChild(axis);
            // 색만으로 구분하지 않는다 — 구간을 글자로도 적는다(최근 6개)
            var list = el("ol", "bm-band-list");
            (band.segments || []).slice(-6).forEach(function (s) {
                var li = el("li");
                li.appendChild(el("span", "bm-muted", hhmm(s.start) + "–" + hhmm(s.end) + " "));
                li.appendChild(badge(s.state, s.state_label));
                list.appendChild(li);
            });
            box.appendChild(list);
            if (band.truncated) box.appendChild(el("p", "bm-note", "상태 전이가 많아 최근 기록만 그렸습니다"));
        }
        var series = trend.series || {};
        if (series.reason) { box.appendChild(el("p", "bm-note", series.reason)); return; }
        var grid = el("div", "bm-sparks");
        SPARKS.forEach(function (spec) {
            var card = el("div", "bm-spark");
            var s = sparkSeries(series.points, spec, trend.since, trend.generated_at, 220, 40);
            card.appendChild(el("div", "bm-spark-label", spec.label));
            card.appendChild(el("div", "bm-spark-value", s.last === null ? "값 없음" : metricText(spec.key, s.last)));
            var svg = svgEl("svg", { class: "bm-spark-svg", width: 220, height: 40, viewBox: "0 0 220 40", role: "img", "aria-label": spec.label + " 추이" });
            s.lines.forEach(function (pts) {
                if (pts.length === 1) {
                    svg.appendChild(svgEl("circle", { class: "bm-spark-dot", cx: pts[0][0], cy: pts[0][1], r: 1.5 }));
                    return;
                }
                svg.appendChild(svgEl("polyline", { class: "bm-spark-line", points: pts.map(function (p) { return p.join(","); }).join(" ") }));
            });
            card.appendChild(svg);
            grid.appendChild(card);
        });
        box.appendChild(grid);
        box.appendChild(el("p", "bm-legend", "서비스 구성원 최댓값 · 값이 없는 칸은 선을 끊습니다"));
    }

    // ─── 「지표」 보기 탭(W8c · §5.2 ⑧) ─────────────────────────────────

    function metricsState(ctx) {
        var st = ctx.tabState("metrics");
        if (st.serviceId !== ctx.serviceId) {
            destroyCharts(st);
            st.serviceId = ctx.serviceId;
            st.body = null; st.key = ""; st.at = 0; st.error = ""; st.loading = false; st.queued = false;
            st.members = null; st.drawer = null; st.cursor = null; st.marks = null; st.pick = null;
        }
        if (!st.window) st.window = METRIC_DEFAULT_WINDOW;
        if (st.compare === undefined) st.compare = false;
        if (st.table === undefined) st.table = false;
        if (!st.charts) st.charts = [];
        return st;
    }

    function destroyCharts(st) {
        (st.charts || []).forEach(function (c) { try { c.destroy(); } catch (_) { /* 이미 정리됨 */ } });
        st.charts = [];
        st.readouts = [];
    }

    // 다른 보기 탭으로 옮길 때 — 그래프를 정리하고 넓게 보기를 끈다
    function leaveMetrics(ctx) {
        var st = ctx.tabState("metrics");
        destroyCharts(st);
        st.wide = false;
        if (ctx.setWide) ctx.setWide(false);
    }

    // 30초 상세 갱신마다 불린다 — 조건(기간·구성원·비교)이 같고 1h 60초 자동 갱신 때가 아니면 다시 읽지 않는다.
    // 진행 중 요청이 있으면 새로 보내지 않는다(사용자당 1건 — 서버도 429로 막는다).
    function refreshMetrics(ctx, o) {
        var st = metricsState(ctx);
        var url = metricsUrl(ctx.serviceId, { window: st.window, members: st.members, compare: st.compare });
        var due = st.window === METRICS_AUTO_WINDOW && Date.now() - st.at >= METRICS_AUTO_MS;
        if (st.key === url && !due && !(o && o.force)) {
            if (!ctx.panel.firstChild) renderMetrics(ctx);
            return Promise.resolve();
        }
        if (st.loading) { st.queued = true; return Promise.resolve(); }   // 끝나면 지금 조건으로 한 번 더
        var fresh = st.key !== url;
        st.loading = true;
        st.key = url;
        if (fresh || !ctx.panel.firstChild) renderMetrics(ctx);
        return ctx.request(url).then(function (r) {
            st.loading = false;
            var queued = st.queued;
            st.queued = false;
            if (r.aborted) { st.key = ""; return; }
            if (st.serviceId !== ctx.serviceId || st.key !== url) return;
            st.at = Date.now();
            if (r.ok) { st.body = r.body || {}; st.error = ""; st.marks = null; }
            else st.error = r.status === 429 ? (r.reason || "다른 지표 조회가 진행 중입니다") : "지표를 불러오지 못했습니다(" + r.reason + ")";
            if (ctx.active && !ctx.active("metrics")) return;
            if (queued) return refreshMetrics(ctx);
            renderMetrics(ctx);
            if (r.ok) return loadMarkers(ctx, st);
        });
    }

    // 이벤트 표지 — 「이벤트」 보기의 `loadEvents`를 그대로 쓴다(그 기간 결과를 60초 안에 가졌으면 추가 조회 0)
    function loadMarkers(ctx, st) {
        var win = METRIC_EVENT_WINDOW[st.window] || EVENT_DEFAULT_WINDOW;
        return loadEvents(ctx, { window: win }).then(function (r) {
            if (r.aborted || (ctx.active && !ctx.active("metrics"))) return;
            st.marks = r.ok ? { window: win, rows: (r.body && r.body.rows) || [] } : { window: win, error: r.reason };
            paintMarkers(ctx, st);
        });
    }

    function memberById(body, id) {
        return ((body && body.members) || []).filter(function (m) { return m.id === id; })[0] || null;
    }

    function memberLabel(body, id) {
        var m = memberById(body, id);
        return m ? text(m.name) || text(m.member_key) : "구성원 " + id;
    }

    function renderMetrics(ctx) {
        var st = metricsState(ctx);
        var body = st.body;
        var panel = ctx.panel;
        destroyCharts(st);
        clear(panel);
        panel.appendChild(metricsToolbar(ctx, st, body));
        if (st.loading) panel.appendChild(el("p", "bm-note", "지표를 불러오는 중입니다"));
        if (st.error) panel.appendChild(el("p", "bm-note bm-note--warn", st.error));
        if (!body) return;
        sourceLines(panel, body);
        (body.notices || []).forEach(function (n) { panel.appendChild(el("p", "bm-note", n)); });
        if (body.hidden_member_count) panel.appendChild(el("p", "bm-note", "권한 밖 구성원 " + body.hidden_member_count + "대 — 선 없이 건수만"));
        if (st.pick) panel.appendChild(memberPicker(ctx, st, body));
        st.markerBox = el("div", "bm-markers");
        st.markerBox.setAttribute("data-bm-markers", "");
        panel.appendChild(st.markerBox);
        paintMarkers(ctx, st);
        st.colors = {};
        ((body.members || []).filter(function (m) { return m.queried; })).forEach(function (m, i) { st.colors[m.id] = i; });
        var layout = el("div", "bm-metrics" + (st.drawer !== null ? " bm-metrics--drawer" : ""));
        var lanes = el("div", "bm-metrics-lanes");
        var groups = laneGroups(body.series, body.members);
        var useChart = !st.table && typeof root.Chart === "function";
        if (!st.table && !useChart) lanes.appendChild(el("p", "bm-note", "그래프 도구를 불러오지 못했습니다 — 표로 보여 줍니다"));
        if (!groups.length) lanes.appendChild(el("p", "bm-empty", "이 기간에 그릴 지표가 없습니다"));
        var pal = useChart ? palette(ctx) : null;
        groups.forEach(function (g) {
            var box = section(lanes, g.label + " 계층");
            g.charts.forEach(function (c) { box.appendChild(chartBlock(ctx, st, body, c, pal)); });
        });
        layout.appendChild(lanes);
        if (st.drawer !== null) layout.appendChild(memberDrawer(ctx, st, body));
        panel.appendChild(layout);
    }

    function metricsToolbar(ctx, st, body) {
        var bar = el("div", "bm-toolbar");
        var seg = el("div", "bm-seg");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "기간");
        METRIC_WINDOWS.forEach(function (w) {
            var b = el("button", w === st.window ? "on" : "", w);
            b.type = "button";
            b.setAttribute("data-bm-metrics-window", w);
            b.setAttribute("aria-pressed", w === st.window ? "true" : "false");
            b.addEventListener("click", function () {
                if (st.window === w) return;
                st.window = w;
                if (w === "7d") st.compare = false;
                st.cursor = null;
                refreshMetrics(ctx);
            });
            seg.appendChild(b);
        });
        bar.appendChild(seg);
        var label = el("label", "bm-check");
        var box = el("input");
        box.type = "checkbox";
        box.checked = !!st.compare;
        box.disabled = st.window === "7d";
        box.setAttribute("data-bm-compare", "");
        box.addEventListener("change", function () { st.compare = !!box.checked; refreshMetrics(ctx); });
        label.appendChild(box);
        label.appendChild(el("span", "", st.window === "7d" ? " 어제 비교(7d 제외)" : " 어제 같은 시각(점선)"));
        bar.appendChild(label);
        var sel = body && body.selection;
        var pick = el("button", "bm-btn bm-btn--small", "구성원 고르기");
        pick.type = "button";
        pick.setAttribute("data-bm-metrics-pick", "");
        pick.setAttribute("aria-expanded", st.pick ? "true" : "false");
        pick.disabled = !body;
        pick.addEventListener("click", function () { st.pick = st.pick ? null : {}; renderMetrics(ctx); });
        bar.appendChild(pick);
        bar.appendChild(el("span", "bm-stat", sel ? "조회 " + sel.queried + "대 / 구성원 " + sel.members + "대" : "조회 -"));
        var tbl = el("button", "bm-btn bm-btn--small bm-btn--ghost", st.table ? "그래프로 보기" : "표로 보기");
        tbl.type = "button";
        tbl.setAttribute("data-bm-metrics-table", "");
        tbl.addEventListener("click", function () { st.table = !st.table; renderMetrics(ctx); });
        bar.appendChild(tbl);
        var wide = el("button", "bm-btn bm-btn--small bm-btn--ghost", st.wide ? "좁게 보기" : "넓게 보기");
        wide.type = "button";
        wide.title = "같은 창에서 목록 칸을 접어 그래프를 넓힙니다";
        wide.setAttribute("data-bm-wide", "");
        wide.setAttribute("aria-pressed", st.wide ? "true" : "false");
        wide.addEventListener("click", function () {
            st.wide = !st.wide;
            if (ctx.setWide) ctx.setWide(st.wide);
            renderMetrics(ctx);
        });
        bar.appendChild(wide);
        var again = el("button", "bm-btn bm-btn--small", "새로고침");
        again.type = "button";
        again.setAttribute("data-bm-metrics-refresh", "");
        again.disabled = !!st.loading;
        again.addEventListener("click", function () { refreshMetrics(ctx, { force: true }); });
        bar.appendChild(again);
        bar.appendChild(el("span", "bm-stat", "마지막 갱신 " + (hhmm(body && body.generated_at) || "-") +
            (st.window === METRICS_AUTO_WINDOW ? " · 60초마다 자동" : " · 수동 갱신")));
        return bar;
    }

    // 소스별 상태 한 줄씩 — SMS는 「HH시 통계까지」를 함께 보인다
    function sourceLines(panel, body) {
        [["sms", "SMS"], ["apm", "APM"]].forEach(function (p) {
            var s = body[p[0]] || {};
            if (!s.state) return;
            var bad = s.state === "failed" || s.state === "partial" || s.state === "unset";
            var parts = [p[1] + " " + (SOURCE_STATE_LABELS[s.state] || s.state)];
            if (s.through) parts.push(s.through);
            if (s.note) parts.push(s.note);
            if (s.reason) parts.push(s.reason);
            var line = el("p", "bm-note" + (bad ? " bm-note--warn" : ""), parts.join(" · "));
            line.setAttribute("data-bm-source-state", p[0] + ":" + s.state);
            panel.appendChild(line);
        });
    }

    // 구성원 고르기 — 상한까지만 고를 수 있다(넘으면 나머지 칸을 잠근다)
    function memberPicker(ctx, st, body) {
        var max = (body.selection && body.selection.max) || 1;
        var box = el("div", "bm-picker");
        box.setAttribute("data-bm-picker", "");
        var chosen = {};
        (body.members || []).forEach(function (m) { if (m.queried) chosen[m.id] = true; });
        var boxes = [];
        function count() { return Object.keys(chosen).filter(function (k) { return chosen[k]; }).length; }
        function lock() { var n = count(); boxes.forEach(function (b) { b.input.disabled = !b.input.checked && n >= max; }); stat.textContent = "고름 " + n + " / 상한 " + max + "대"; }
        var stat = el("span", "bm-stat", "");
        box.appendChild(stat);
        var list = el("div", "bm-picker-list");
        (body.members || []).forEach(function (m) {
            var label = el("label", "bm-check");
            var input = el("input");
            input.type = "checkbox";
            input.checked = !!chosen[m.id];
            input.disabled = !m.supported;
            input.setAttribute("data-bm-pick", String(m.id));
            input.addEventListener("change", function () { chosen[m.id] = !!input.checked; lock(); });
            label.appendChild(input);
            label.appendChild(el("span", "", " " + text(m.name) + (m.supported ? "" : " (지표 보기 대상 아님)")));
            label.appendChild(badge(m.state, m.state_label));
            list.appendChild(label);
            if (m.supported) boxes.push({ input: input });
        });
        box.appendChild(list);
        var apply = el("button", "bm-btn bm-btn--small", "이 구성원으로 조회");
        apply.type = "button";
        apply.setAttribute("data-bm-pick-apply", "");
        apply.addEventListener("click", function () {
            var ids = Object.keys(chosen).filter(function (k) { return chosen[k]; }).map(Number)
                .sort(function (a, b) { return a - b; });
            if (!ids.length) { ctx.flash("구성원을 하나 이상 고르세요"); return; }
            st.members = ids; st.pick = null;
            refreshMetrics(ctx);
        });
        var auto = el("button", "bm-btn bm-btn--small bm-btn--ghost", "자동 선택으로");
        auto.type = "button";
        auto.addEventListener("click", function () { st.members = null; st.pick = null; refreshMetrics(ctx); });
        box.appendChild(apply);
        box.appendChild(auto);
        box.appendChild(el("p", "bm-legend", "자동 선택은 상태 나쁜 순 → 최근 1시간 알람 많은 순 → 이름 순입니다"));
        lock();
        return box;
    }

    function paintMarkers(ctx, st) {
        var box = st.markerBox;
        if (!box) return;
        clear(box);
        var range = (st.body && st.body.range) || {};
        var mk = st.marks;
        if (!mk) { box.appendChild(el("span", "bm-muted", "이벤트 표지 불러오는 중")); return; }
        if (mk.error) { box.appendChild(el("span", "bm-muted", "이벤트 표지 없음(" + mk.error + ")")); return; }
        var marks = eventMarkers(mk.rows, range.since, range.until, MARKER_BUCKETS);
        var total = marks.reduce(function (a, m) { return a + m.count; }, 0);
        box.appendChild(el("span", "bm-markers-label bm-muted", "이벤트 " + total + "건" +
            (st.window === "7d" ? " · 이벤트 표지는 최근 24시간만" : "") + " — 누르면 「이벤트」 보기"));
        var track = el("div", "bm-markers-track");
        marks.forEach(function (m) {
            var b = el("button", "bm-marker " + stateClass(EVENT_SEVERITY_CLASS[m.worst] || "unknown"), String(m.count));
            b.type = "button";
            b.setAttribute("data-bm-marker", String(m.count));
            b.style.left = (Math.round(m.pos * 10000) / 100) + "%";
            var tip = metricTime(m.from, st.window, true) + " 이벤트 " + m.count + "건";
            b.title = tip;
            b.setAttribute("aria-label", tip);
            b.addEventListener("click", function () {
                // 표지 기간의 타임라인을 그대로 쓴다(그 칸 행은 이 기간 안 — 추가 조회 0) · 주소에 `&tab=events&at=`
                var es = eventsState(ctx);
                es.window = mk.window;
                setFocusAt(es, m.last || m.from);
                if (ctx.openTab) ctx.openTab("events");
            });
            track.appendChild(b);
        });
        box.appendChild(track);
    }

    // 테마 토큰에서 선 색을 읽는다(라이트·다크 모두 · 고정 색 0)
    function palette(ctx) {
        var cs = typeof root.getComputedStyle === "function" ? root.getComputedStyle(ctx.panel) : null;
        function v(name) { return cs ? text(cs.getPropertyValue(name)).trim() : ""; }
        var lines = [];
        for (var i = 1; i <= CHART_MAX_LINES; i++) lines.push(v("--bm-line-" + i) || v("--text-secondary"));
        return { lines: lines, text: v("--text-secondary"), grid: v("--border"), cursor: v("--text"),
            warn: v("--warning"), crit: v("--error") };
    }

    function lineColor(st, pal, id) {
        var i = st.colors && st.colors[id] !== undefined ? st.colors[id] : 0;
        return pal ? pal.lines[i % pal.lines.length] : "";
    }

    function chartBlock(ctx, st, body, c, pal) {
        var box = el("div", "bm-chart");
        box.setAttribute("data-bm-chart", c.key);
        box.appendChild(el("h4", "bm-chart-title", c.title + (c.unit ? " (" + c.unit + ")" : "") +
            (c.source === "sms" ? " · SMS" : " · APM")));
        var legend = el("div", "bm-chart-legend");
        c.members.forEach(function (id) {
            var b = el("button", "bm-legend-item");
            b.type = "button";
            b.setAttribute("data-bm-line", String(id));
            var sw = el("span", "bm-swatch");
            if (pal) sw.style.background = lineColor(st, pal, id);
            b.appendChild(sw);
            b.appendChild(el("span", "", memberLabel(body, id)));
            b.title = "이 구성원 서랍 열기";
            b.addEventListener("click", function () { st.drawer = id; renderMetrics(ctx); });
            legend.appendChild(b);
        });
        box.appendChild(legend);
        var th = c.source === "sms" ? (body.thresholds || {})[c.metric] : null;
        var readout = el("p", "bm-cursor bm-muted", "그래프 위에 포인터를 올리면 그 시각 값을 보입니다");
        readout.setAttribute("data-bm-cursor", "");
        if (pal) {
            var wrap = el("div", "bm-chart-canvas");
            var canvas = el("canvas");
            canvas.setAttribute("role", "img");
            canvas.setAttribute("aria-label", c.title + " 추이 — 값은 [표로 보기]에서 읽습니다");
            wrap.appendChild(canvas);
            box.appendChild(wrap);
            try {
                st.charts.push(newChart(ctx, st, body, c, canvas, pal, th));
                st.readouts.push({ chart: c, node: readout });
            } catch (_) {
                box.removeChild(wrap);
                box.appendChild(el("p", "bm-note", "그래프를 그리지 못했습니다 — 표로 보여 줍니다"));
                box.appendChild(chartTable(st, body, c));
            }
        } else box.appendChild(chartTable(st, body, c));
        if (th) box.appendChild(el("p", "bm-legend", "점선 가로선 = 설정 임계(주의 " + fmtNum(th.warn, c.unit) + " · 위험 " + fmtNum(th.crit, c.unit) + ")"));
        if (st.compare) box.appendChild(el("p", "bm-legend", "점선 = 어제 같은 시각"));
        if (pal) box.appendChild(readout);
        if (c.more) box.appendChild(el("p", "bm-note", "외 " + c.more + "대 — [구성원 고르기]로 바꿔 볼 수 있습니다"));
        return box;
    }

    function newChart(ctx, st, body, c, canvas, pal, th) {
        var range = body.range || {};
        var x0 = (epochOf(range.since) || 0) * 1000, x1 = (epochOf(range.until) || 0) * 1000;
        var sets = c.lines.map(function (s) {
            var color = lineColor(st, pal, s.member_id);
            return {
                label: memberLabel(body, s.member_id) + (s.yesterday ? " (어제)" : ""),
                data: gapPoints(s.points, s.step_seconds), memberId: s.member_id,
                borderColor: color, backgroundColor: color, borderWidth: s.yesterday ? 1 : 1.6,
                borderDash: s.yesterday ? [4, 4] : [], pointRadius: (s.points || []).length <= 30 ? 2 : 0,
                spanGaps: false,
            };
        });
        // SMS 「설정 임계」만 가로 점선(APM 임계선은 긋지 않는다 — D-335 ①)
        [["warn", "주의", pal.warn], ["crit", "위험", pal.crit]].forEach(function (k) {
            if (!th || typeof th[k[0]] !== "number") return;
            sets.push({ label: "설정 임계 " + k[1], data: [{ x: x0, y: th[k[0]] }, { x: x1, y: th[k[0]] }],
                borderColor: k[2], borderDash: [6, 3], borderWidth: 1, pointRadius: 0, threshold: true });
        });
        var cursorPlugin = {
            id: "bmCursor",
            afterDatasetsDraw: function (chart) {
                if (st.cursor === null || st.cursor === undefined) return;
                var x = chart.scales.x.getPixelForValue(st.cursor), a = chart.chartArea;
                if (!(x >= a.left && x <= a.right)) return;
                var g = chart.ctx;
                g.save(); g.strokeStyle = pal.cursor; g.lineWidth = 1;
                g.beginPath(); g.moveTo(x, a.top); g.lineTo(x, a.bottom); g.stroke(); g.restore();
            },
        };
        return new root.Chart(canvas, {
            type: "line",
            data: { datasets: sets },
            options: {
                animation: false, responsive: true, maintainAspectRatio: false,
                interaction: { mode: "nearest", axis: "x", intersect: false },
                scales: {
                    x: { type: "linear", min: x0, max: x1, grid: { color: pal.grid },
                        ticks: { color: pal.text, maxTicksLimit: 8,
                            callback: function (v) { return metricTime(v, st.window, false); } } },
                    y: { beginAtZero: true, grid: { color: pal.grid }, ticks: { color: pal.text } },
                },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                onHover: function (evt, _hits, chart) { setCursor(st, body, chart.scales.x.getValueForPixel(evt.x)); },
                onClick: function (_evt, hits, chart) {
                    var ds = hits && hits.length ? chart.data.datasets[hits[0].datasetIndex] : null;
                    if (ds && ds.memberId !== undefined) { st.drawer = ds.memberId; renderMetrics(ctx); }
                },
            },
            plugins: [cursorPlugin],
        });
    }

    // 공유 시간 커서 — 모든 그래프에 같은 세로선 + 그래프마다 그 시각 값 한 줄
    function setCursor(st, body, t) {
        if (typeof t !== "number" || !isFinite(t)) return;
        st.cursor = t;
        st.charts.forEach(function (ch) { try { ch.draw(); } catch (_) { /* 정리 중 */ } });
        st.readouts.forEach(function (r) { r.node.textContent = cursorText(body, r.chart, t, st.window); });
    }

    function cursorText(body, c, t, win) {
        var vals = cursorValues(c.lines, t).filter(function (v) { return v.value !== null; });
        if (!vals.length) return metricTime(t, win, true) + " — 값 없음";
        var head = c.source === "sms" ? statLabel(vals[0].at, win) : metricTime(t, win, true);
        return head + " — " + vals.map(function (v) {
            return memberLabel(body, v.series.member_id) + (v.series.yesterday ? "(어제)" : "") + " " + fmtNum(v.value, c.unit);
        }).join(" · ");
    }

    // 그래프 대체 표 — 시각 × 선(값 없는 칸은 「-」)
    function chartTable(st, body, c) {
        var at = {};
        var maps = c.lines.map(function (s) {
            var m = {};
            (s.points || []).forEach(function (p) { m[p[0]] = p[1]; at[p[0]] = epochOf(p[0]); });
            return m;
        });
        var keys = Object.keys(at).sort(function (a, b) { return at[a] - at[b]; });
        var wrap = el("div", "bm-table-wrap");
        var table = el("table", "bm-table");
        table.setAttribute("data-bm-metrics-grid", c.key);
        var hr = el("tr");
        hr.appendChild(el("th", "", "시각"));
        c.lines.forEach(function (s) { hr.appendChild(el("th", "", memberLabel(body, s.member_id) + (s.yesterday ? " (어제)" : ""))); });
        var thead = el("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = el("tbody");
        keys.forEach(function (k) {
            var tr = el("tr", "bm-row");
            cell(tr, c.source === "sms" ? metricTime(at[k] * 1000, st.window, true).replace(/:00$/, "시 통계") : metricTime(at[k] * 1000, st.window, true));
            maps.forEach(function (m) { cell(tr, m[k] === undefined ? "-" : fmtNum(m[k], c.unit), "bm-num"); });
            tbody.appendChild(tr);
        });
        if (!keys.length) {
            var tr0 = el("tr");
            var td0 = el("td", "bm-muted", "값 없음");
            td0.setAttribute("colspan", String(c.lines.length + 1));
            tr0.appendChild(td0);
            tbody.appendChild(tr0);
        }
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    // 구성원 서랍 — 같은 패널 안(새 창 0) · 최근 값 · 연계 · 이 구성원만 다시 보기 · 알람 조사 · 질의응답 채우기
    function memberDrawer(ctx, st, body) {
        var id = st.drawer;
        var m = memberById(body, id) || { id: id, name: "구성원 " + id };
        var box = el("aside", "bm-drawer");
        box.setAttribute("data-bm-drawer", String(id));
        box.setAttribute("aria-label", "구성원 " + text(m.name));
        var head = el("div", "bm-drawer-head");
        head.appendChild(badge(m.state, m.state_label));
        head.appendChild(el("h4", "bm-drawer-name", m.name));
        var close = el("button", "bm-btn bm-btn--small bm-btn--ghost", "닫기");
        close.type = "button";
        close.addEventListener("click", function () { st.drawer = null; renderMetrics(ctx); });
        head.appendChild(close);
        box.appendChild(head);
        box.appendChild(el("p", "bm-muted", [LANE_LABELS[laneOf(m.layer)] + " 계층", m.zone ? "존 " + m.zone : "",
            "최근 1시간 알람 " + (m.recent_alarms || 0) + "건", "열린 사건 " + (m.open_incidents || 0) + "건",
            m.last_alarm_at ? "마지막 알람 " + hhmm(m.last_alarm_at) : ""].filter(Boolean).join(" · ")));
        var last = el("ul", "bm-drawer-list");
        (body.series || []).filter(function (s) { return s.member_id === id && !s.yesterday; }).forEach(function (s) {
            var p = (s.points || [])[(s.points || []).length - 1];
            var when = p ? (s.source === "sms" ? statLabel(p[0], st.window) : metricTime(epochOf(p[0]) * 1000, st.window, true)) : "";
            last.appendChild(el("li", "", s.label + " 마지막 " + (p ? fmtNum(p[1], s.unit) + " (" + when + ")" : "값 없음")));
        });
        if (!last.firstChild) last.appendChild(el("li", "bm-muted", "이 기간 값 없음"));
        box.appendChild(last);
        var links = (body.links || []).filter(function (e) { return e.from === id || e.to === id; });
        box.appendChild(el("p", "bm-muted", links.length ? "연계: " + links.map(function (e) {
            return e.from === id ? "→ " + memberLabel(body, e.to) : "← " + memberLabel(body, e.from);
        }).join(", ") : "연계 정보 없음"));
        var acts = el("div", "bm-drawer-actions");
        var only = el("button", "bm-btn bm-btn--small", "이 구성원만 보기");
        only.type = "button";
        only.setAttribute("data-bm-drawer-only", "");
        only.addEventListener("click", function () { st.members = [id]; refreshMetrics(ctx); });
        acts.appendChild(only);
        var dm = ((ctx.detail && ctx.detail.members) || []).filter(function (x) { return x.id === id; })[0];
        if (dm && ctx.opts.onInvestigateMember) {
            var inv = el("button", "bm-btn bm-btn--small", "이 구성원 알람 조사");
            inv.type = "button";
            inv.addEventListener("click", function () {
                var moved = ctx.opts.onInvestigateMember({
                    id: dm.id, kind: dm.kind, name: dm.name, server_name: dm.server_name,
                    hostname: dm.hostname, instance_name: dm.instance_name,
                });
                if (moved === false) ctx.flash("알람 조사로 옮기지 못했습니다 — 이벤트 알람 탭을 볼 수 없거나 답변 중입니다");
            });
            acts.appendChild(inv);
        }
        if (ctx.opts.fillQuestion) {
            var ask = el("button", "bm-btn bm-btn--small bm-btn--ghost", "질의응답에서 묻기");
            ask.type = "button";
            ask.title = "질의응답 탭 입력창에 질문을 채웁니다(보내지 않습니다)";
            ask.addEventListener("click", function () {
                var svc = ((ctx.detail && ctx.detail.service) || {}).display_name || "";
                var q = "업무 서비스 「" + text(svc) + "」 구성원 " + text(m.name) + "의 최근 " + st.window + " 지표 추이를 알려줘";
                if (ctx.opts.fillQuestion(q) === false) ctx.flash("질의응답 탭으로 옮기지 못했습니다 — 답변이 끝난 뒤 다시 누르세요");
            });
            acts.appendChild(ask);
        }
        box.appendChild(acts);
        return box;
    }

    // ─── 「이벤트」 보기 탭(W8b · §5.2 ⑥) ─────────────────────────────────

    function eventsState(ctx) {
        var st = ctx.tabState("events");
        if (st.serviceId !== ctx.serviceId) {
            st.serviceId = ctx.serviceId;
            st.body = null; st.key = ""; st.at = 0; st.member = ""; st.expanded = {}; st.cache = {}; st.focusAt = null;
        }
        if (!st.cache) st.cache = {};
        if (!st.window) st.window = EVENT_DEFAULT_WINDOW;
        if (!st.off) st.off = {};
        if (st.severity === undefined) st.severity = "";
        if (st.includeSuppressed === undefined) st.includeSuppressed = false;
        return st;
    }

    // (§5.2.1) 이벤트 보기를 그 시각(ms)으로 — 가장 가까운 행 강조 + 처음 그릴 때 1회 스크롤
    function setFocusAt(st, ms) { st.focusAt = ms; st.focusScrolled = false; }

    function canIncludeSuppressed(ctx) { return !!(ctx.opts.isAdmin && ctx.opts.isAdmin()); }

    // 타임라인 응답 — 같은 주소를 60초 안에 다시 부르면 가진 결과를 그대로 준다(force면 다시 읽는다).
    // 결과 {ok, status, body, reason, aborted?, cached?} — 다른 보기 탭도 이것으로 같은 행을 쓴다.
    // (W8c) `o.window`로 이 탭과 다른 기간을 부를 수 있다(지표 표지) — 결과는 주소별로 60초 두고,
    // 이 탭의 기간과 같을 때만 화면 결과(`st.body`)를 바꾼다.
    function loadEvents(ctx, o) {
        var st = eventsState(ctx);
        var win = (o && o.window) || st.window;
        var mine = win === st.window;
        var url = eventsUrl(ctx.serviceId, { window: win, includeSuppressed: canIncludeSuppressed(ctx) && st.includeSuppressed });
        var hit = st.cache[url];
        if (hit && !(o && o.force) && Date.now() - hit.at < EVENTS_REFRESH_MS) {
            if (mine) { st.body = hit.body; st.key = url; st.at = hit.at; }
            return Promise.resolve({ ok: true, status: 200, body: hit.body, reason: "", cached: true });
        }
        return ctx.request(url).then(function (r) {
            if (r.ok) {
                var at = Date.now();
                st.cache[url] = { body: r.body || {}, at: at };
                if (mine) { st.body = st.cache[url].body; st.key = url; st.at = at; }
            }
            return r;
        });
    }

    function refreshEvents(ctx, o) {
        return loadEvents(ctx, o).then(function (r) {
            if (r.aborted || (ctx.active && !ctx.active("events"))) return;
            // 30초 상세 갱신이 가진 결과를 다시 줄 때 이미 그린 화면이면 그대로 둔다(필터·포커스 유지)
            var st = eventsState(ctx);
            if (r.cached && st.paintedAt === st.at && ctx.panel.firstChild) return;
            st.paintedAt = st.at;
            renderEvents(ctx, r.ok ? null : r.reason);
        });
    }

    function eventsToolbar(ctx, st, body) {
        var bar = el("div", "bm-toolbar");
        var seg = el("div", "bm-seg");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "기간");
        EVENT_WINDOWS.forEach(function (w) {
            var b = el("button", w === st.window ? "on" : "", w);
            b.type = "button";
            b.setAttribute("data-bm-window", w);
            b.setAttribute("aria-pressed", w === st.window ? "true" : "false");
            b.addEventListener("click", function () {
                if (st.window === w) return;
                st.window = w;
                st.focusAt = null;
                if (ctx.syncRoute) ctx.syncRoute();
                refreshEvents(ctx, { force: true });
            });
            seg.appendChild(b);
        });
        bar.appendChild(seg);
        var chips = el("div", "bm-state-chips");
        chips.setAttribute("role", "group");
        chips.setAttribute("aria-label", "소스");
        var avail = (body && body.sources_available) || {};
        Object.keys(avail).forEach(function (src) {
            if (!avail[src] || !avail[src].available) return;   // 꺼진 소스(변경 이력 플래그 off 등)는 칩 없음
            var on = !st.off[src];
            var chip = el("button", "bm-state-chip bm-s--unknown", avail[src].label || src);
            chip.type = "button";
            chip.setAttribute("data-bm-source", src);
            chip.setAttribute("aria-pressed", on ? "true" : "false");
            chip.addEventListener("click", function () {
                st.off[src] = on;
                renderEvents(ctx, null);
            });
            chips.appendChild(chip);
        });
        bar.appendChild(chips);
        bar.appendChild(eventSelect("eventSeverity", "심각도 전체",
            EVENT_SEVERITIES, st.severity, function (v) { st.severity = v; renderEvents(ctx, null); }));
        bar.appendChild(eventSelect("eventMember", "구성원 전체",
            eventMembers(body && body.rows), st.member, function (v) { st.member = v; renderEvents(ctx, null); }));
        if (canIncludeSuppressed(ctx)) {
            var label = el("label", "bm-check");
            var box = el("input");
            box.type = "checkbox";
            box.checked = !!st.includeSuppressed;
            box.setAttribute("data-bm-include-suppressed", "");
            box.addEventListener("change", function () {
                st.includeSuppressed = !!box.checked;
                refreshEvents(ctx, { force: true });
            });
            label.appendChild(box);
            label.appendChild(el("span", "", " 억제 포함"));
            bar.appendChild(label);
        }
        var again = el("button", "bm-btn bm-btn--small", "새로고침");
        again.type = "button";
        again.setAttribute("data-bm-events-refresh", "");
        again.addEventListener("click", function () { refreshEvents(ctx, { force: true }); });
        bar.appendChild(again);
        bar.appendChild(el("span", "bm-stat", "마지막 갱신 " + (hhmm(body && body.generated_at) || "-") + " · 60초마다 자동"));
        return bar;
    }

    function eventSelect(name, allLabel, items, current, onChange) {
        var sel = el("select");
        sel.setAttribute("data-bm-filter", name);
        sel.setAttribute("aria-label", allLabel);
        var all = el("option", "", allLabel);
        all.value = "";
        sel.appendChild(all);
        items.forEach(function (it) {
            var o = el("option", "", it.label);
            o.value = String(it.value);
            sel.appendChild(o);
        });
        sel.value = String(current || "");
        sel.addEventListener("change", function () { onChange(sel.value); });
        return sel;
    }

    function renderEvents(ctx, error) {
        var st = eventsState(ctx);
        var body = st.body;
        var panel = ctx.panel;
        clear(panel);
        panel.appendChild(eventsToolbar(ctx, st, body));
        if (error) panel.appendChild(el("p", "bm-note bm-note--warn", "이벤트를 불러오지 못했습니다(" + error + ")"));
        if (!body) return;
        (body.notices || []).forEach(function (n) { panel.appendChild(el("p", "bm-note", n)); });
        if (body.hidden_row_count) panel.appendChild(el("p", "bm-note", "권한 밖 구성원 행 " + body.hidden_row_count + "건"));
        if (body.truncated) panel.appendChild(el("p", "bm-note", "최근 " + ((body.rows || []).length) + "건만 보입니다"));
        var rows = filterEventRows(body.rows, st);
        st.focusRow = typeof st.focusAt === "number" ? nearestEvent(rows, st.focusAt) : null;
        st.focusTr = null;
        var pinned = rows.filter(function (r) { return r.pinned; });
        var rest = rows.filter(function (r) { return !r.pinned; });
        if (pinned.length) {
            var head = section(panel, "장애 직전 변경");
            var start = body.anomaly_start ? eventTime(body.anomaly_start.at, st.window) : "";
            head.appendChild(el("p", "bm-note bm-note--warn", (start ? "이상 시작 " + start + " · " : "") +
                (body.pinned_note || CAUSALITY_NOTE)));
            head.appendChild(eventTable(ctx, st, pinned));
        }
        var box = section(panel, "이벤트 (" + st.window + ")");
        if (!rest.length) box.appendChild(el("p", "bm-note", (body.rows || []).length ? "조건에 맞는 이벤트가 없습니다" : "이 기간에 이벤트가 없습니다"));
        else box.appendChild(eventTable(ctx, st, rest));
        // 시각 지정(`&at=`)은 처음 그릴 때 1회만 스크롤한다(60초 갱신·필터 변경은 강조만 유지)
        if (st.focusTr && !st.focusScrolled) {
            st.focusScrolled = true;
            if (st.focusTr.scrollIntoView) st.focusTr.scrollIntoView({ block: "center" });
        }
    }

    function eventTable(ctx, st, rows) {
        var wrap = el("div", "bm-table-wrap");
        var table = el("table", "bm-table");
        var hr = el("tr");
        ["시각", "소스", "구성원", "요약", "심각도 · 판정", ""].forEach(function (h) { hr.appendChild(el("th", "", h)); });
        var thead = el("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = el("tbody");
        rows.forEach(function (r) {
            var tr = el("tr", "bm-row");
            tr.setAttribute("data-bm-event", r.source);
            // (W8c · §5.2.1) 지표 표지·주소 `&at=`으로 왔으면 그 시각에 가장 가까운 행을 강조한다
            if (st.focusRow && st.focusRow === r) { tr.classList.add("bm-row--focus"); st.focusTr = tr; }
            cell(tr, eventTime(r.at, st.window));
            cell(tr, r.source_label || r.source);
            cell(tr, r.member_name || (r.member_key ? r.member_key : "-"));
            cell(tr, text(r.summary) + (r.count > 1 ? " ×" + r.count : ""));
            var chips = el("td");
            if (r.severity) chips.appendChild(el("span", "bm-badge " + stateClass(EVENT_SEVERITY_CLASS[r.severity] || "unknown"), r.severity_label || r.severity));
            if (r.handling) {
                if (r.severity) chips.appendChild(el("span", "", " "));
                chips.appendChild(el("span", "bm-badge bm-s--unknown", r.handling_label || r.handling));
            }
            tr.appendChild(chips);
            var act = el("td");
            tr.appendChild(act);
            tbody.appendChild(tr);
            eventAction(ctx, st, r, act, tbody);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    // 행 동작 — 알람: 같은 창 노이즈 관제 탭 서랍 · 조사: 같은 창 장애 진단 탭 · 변경: 행 펼침(새 창 0)
    function eventAction(ctx, st, r, td, tbody) {
        var btn = null;
        if (r.alarm_id && ctx.opts.onOpenAlarm) {
            btn = el("button", "bm-btn bm-btn--small", "관제에서 보기");
            btn.title = "노이즈 관제 탭에서 이 알람의 판정 서랍을 엽니다";
            btn.addEventListener("click", function () {
                if (ctx.opts.onOpenAlarm(r.alarm_id) === false) ctx.flash("노이즈 관제 탭으로 옮기지 못했습니다 — 노이즈 관제 탭이 꺼져 있습니다");
            });
        } else if (r.investigation_id && ctx.opts.onOpenInvestigation) {
            btn = el("button", "bm-btn bm-btn--small", "장애 진단에서 보기");
            btn.addEventListener("click", function () {
                if (ctx.opts.onOpenInvestigation(r.investigation_id) === false) ctx.flash("장애 진단 탭으로 옮기지 못했습니다 — 답변이 끝난 뒤 다시 누르세요");
            });
        } else if (r.change) {
            var key = r.at + "|" + r.member_key + "|" + text(r.summary);
            var open = !!st.expanded[key];
            btn = el("button", "bm-btn bm-btn--small bm-btn--ghost", open ? "접기" : "펼치기");
            btn.setAttribute("aria-expanded", open ? "true" : "false");
            if (open) {
                var more = el("tr", "bm-row");
                var td2 = el("td", "bm-muted", [r.change.resource_type, r.change.change_type].filter(Boolean).join(" · ") +
                    (r.change.description ? " — " + r.change.description : ""));
                td2.setAttribute("colspan", "6");
                more.appendChild(td2);
                more.setAttribute("data-bm-event-detail", "");
                tbody.appendChild(more);
            }
            btn.addEventListener("click", function () {
                st.expanded[key] = !open;
                renderEvents(ctx, null);
            });
        }
        if (btn) { btn.type = "button"; td.appendChild(btn); }
    }

    // ─── 「진단」 보기 탭(W9 · W11 · §5.2 ⑧) ─────────────────────────────

    // 서비스 심층 조사 버튼 상태 — 조사 기능이 꺼졌거나 대표 서버가 없으면 비활성 + 사유(서버가 다시 막는다)
    function investigateState(d1, caps) {
        if (!caps || caps.investigation_enabled !== true) {
            return { enabled: false, reason: "조사 서비스가 꺼져 있어 접수할 수 없습니다" };
        }
        if (!d1 || !d1.representative) {
            return { enabled: false, reason: text(d1 && d1.representative_reason) || "대표 서버가 없어 접수할 수 없습니다" };
        }
        return { enabled: true, reason: "" };
    }

    // 거부 응답 → 화면 문구(`detail{code, reason[, reason_text, retry_after_seconds]}` 또는 문자열)
    function rejectText(r) {
        var d = r && r.body && r.body.detail;
        if (d && typeof d === "object") {
            var t = text(d.reason_text || d.reason) || ("HTTP " + r.status);
            if (typeof d.retry_after_seconds === "number" && d.retry_after_seconds > 0) {
                t += " — 약 " + Math.ceil(d.retry_after_seconds / 60) + "분 뒤 다시 시도하세요";
            }
            return t;
        }
        return text(r && r.reason) || "요청에 실패했습니다";
    }

    function diagState(ctx) {
        var st = ctx.tabState("diagnosis");
        if (st.serviceId !== ctx.serviceId) {
            st.serviceId = ctx.serviceId;
            st.loaded = false; st.loading = false; st.body = null; st.error = ""; st.history = null;
            st.historyError = ""; st.preview = false; st.submitting = false; st.result = null;
        }
        return st;
    }

    function diagUrl(ctx, part) { return API + "/" + encodeURIComponent(ctx.serviceId) + "/" + part; }

    // 처음 열 때 1회 + 수동 [새로고침](force) — 30초 상세 갱신은 가진 결과로 다시 그리기만 한다(요청 0)
    function refreshDiagnosis(ctx, o) {
        var st = diagState(ctx);
        if (!(o && o.force) && st.loaded) {
            if (!ctx.panel.firstChild) renderDiagnosis(ctx);
            return Promise.resolve();
        }
        if (st.loading) return Promise.resolve();
        st.loading = true;
        if (o && o.force) renderDiagnosis(ctx);
        return Promise.all([ctx.request(diagUrl(ctx, "diagnosis")), ctx.request(diagUrl(ctx, "investigations"))])
            .then(function (res) {
                st.loading = false;
                if (res[0].aborted || res[1].aborted) return;   // 끊긴 요청(pause) — 다음 갱신 때 다시 읽는다
                st.loaded = true;
                st.body = res[0].ok ? (res[0].body || {}) : null;
                st.error = res[0].ok ? "" : rejectText(res[0]);
                setHistory(st, res[1]);
                if (ctx.active && !ctx.active("diagnosis")) return;
                renderDiagnosis(ctx);
            });
    }

    function setHistory(st, r) {
        st.history = r.ok ? (r.body || {}) : null;
        st.historyError = r.ok ? "" : rejectText(r);
    }

    function openInvestigationFrom(ctx, id) {
        if (!ctx.opts.onOpenInvestigation) return false;
        if (ctx.opts.onOpenInvestigation(id) === false) {
            ctx.flash("장애 진단 탭으로 옮기지 못했습니다 — 답변이 끝난 뒤 다시 누르세요");
            return false;
        }
        return true;
    }

    function openInvestigationButton(ctx, id) {
        if (!id || !ctx.opts.onOpenInvestigation) return null;
        var b = el("button", "bm-btn bm-btn--small", "장애 진단에서 보기");
        b.type = "button";
        b.addEventListener("click", function () { openInvestigationFrom(ctx, id); });
        return b;
    }

    // [접수] — 요청 본문 0(질문은 서버의 코드 템플릿) · 확인 대화상자 0 · 이력을 다시 읽은 뒤 같은 창 장애 진단 탭
    function submitDiagnosis(ctx) {
        var st = diagState(ctx);
        if (st.submitting) return Promise.resolve();
        st.submitting = true; st.result = null;
        renderDiagnosis(ctx);
        return ctx.request(diagUrl(ctx, "investigations"), "POST").then(function (r) {
            st.submitting = false;
            if (r.aborted) return;
            var b = r.body || {};
            var openId = null;
            if (r.ok && b.investigation_id && b.status !== "rejected") {
                st.preview = false;
                openId = b.investigation_id;
                st.result = {
                    ok: true, notice: text(b.hypothesis_notice),
                    text: (b.reused ? "같은 서비스의 진행 중 조사가 있어 그 조사를 엽니다" : "서비스 심층 조사를 접수했습니다") +
                        (b.excluded_members ? " · 권한 밖 비교 구성원 " + b.excluded_members + "대 제외" : "") +
                        (b.truncated ? " · 비교 구성원 상한까지만 실음" : ""),
                };
            } else if (r.ok) {
                st.result = { ok: false, text: "조사 서비스가 접수를 거부했습니다 — " + (text(b.reason_text || b.reason) || "사유 없음") };
            } else {
                st.result = { ok: false, text: rejectText(r) };
            }
            return ctx.request(diagUrl(ctx, "investigations")).then(function (h) {
                if (!h.aborted) setHistory(st, h);
                if (ctx.active && !ctx.active("diagnosis")) return;
                if (openId && !ctx.opts.onOpenInvestigation) st.result.text += " (조사 id " + openId + ")";
                renderDiagnosis(ctx);
                if (openId) openInvestigationFrom(ctx, openId);
            });
        });
    }

    function renderDiagnosis(ctx) {
        var st = diagState(ctx);
        var panel = ctx.panel;
        clear(panel);
        var bar = el("div", "bm-toolbar");
        var again = el("button", "bm-btn bm-btn--small", st.loading ? "불러오는 중" : "새로고침");
        again.type = "button";
        again.disabled = !!st.loading;
        again.addEventListener("click", function () { refreshDiagnosis(ctx, { force: true }); });
        bar.appendChild(again);
        var d = st.body;
        if (d) {
            var srcLabel = { m3: "실시간 지표", adhoc: "즉석 조회", none: "지표 없음" }[(d.sources || {}).metrics] || "";
            bar.appendChild(el("span", "bm-muted", [
                d.computed_at ? hhmm(d.computed_at) + " 기준" : "",
                d.window_minutes ? "창 " + d.window_minutes + "분" : "",
                srcLabel ? "지표 " + srcLabel : "",
            ].filter(Boolean).join(" · ")));
        }
        panel.appendChild(bar);
        if (st.error) panel.appendChild(el("p", "bm-note bm-note--warn", "진단을 불러오지 못했습니다(" + st.error + ")"));
        if (d) renderVerdict(panel, ctx, d);
        if (d) renderInvestigate(panel, ctx, st, d);
        renderDiagHistory(panel, ctx, st);
    }

    function renderVerdict(panel, ctx, d) {
        var head = el("div", "bm-diag-head");
        var line = el("p", "bm-diag-verdict");
        line.appendChild(el("span", "", d.verdict_text || "판독 없음"));
        line.appendChild(el("span", "bm-diag-hypo", d.hypothesis_label || "가설 · 참고용"));
        head.appendChild(line);
        if (d.verdict_guidance) head.appendChild(el("p", "bm-note", d.verdict_guidance));
        if (d.correlation_note) head.appendChild(el("p", "bm-muted", d.correlation_note));
        if (d.partial) head.appendChild(el("p", "bm-note bm-note--warn", "일부만 조회했습니다 — 아래 사유를 확인하세요"));
        (d.notices || []).forEach(function (n) { head.appendChild(el("p", "bm-note", n)); });
        panel.appendChild(head);

        var box = section(panel, "이상 구성원 (최초 이상 순)");
        var rows = d.anomalous_members || [];
        if (!rows.length) box.appendChild(el("p", "bm-note", "이 창에서 이상 신호가 보인 구성원이 없습니다"));
        else {
            var wrap = el("div", "bm-table-wrap");
            var table = el("table", "bm-table");
            var hr = el("tr");
            ["최초 이상", "구성원", "계층", "신호", "이어진 서버", "최근 조사"].forEach(function (h) { hr.appendChild(el("th", "", h)); });
            var thead = el("thead");
            thead.appendChild(hr);
            table.appendChild(thead);
            var tbody = el("tbody");
            rows.forEach(function (m) {
                var tr = el("tr", "bm-row");
                tr.setAttribute("data-bm-diag-member", text(m.member_key));
                cell(tr, hhmm(m.first_at) || "-");
                cell(tr, (m.kind === "sms_server" ? "서버 " : "APM ") + text(m.name || m.member_key));
                cell(tr, LANE_LABELS[m.layer] || (m.layer ? m.layer : "미지정"));
                cell(tr, (m.signals || []).map(function (s) {
                    return text(s.label) + (s.time_basis ? " (" + s.time_basis + ")" : "");
                }).join(" · ") || "-");
                cell(tr, m.kind === "sms_server" ? "-" : ((m.linked_servers || []).length ? (m.linked_servers.length + "대") : "없음"));
                var inv = m.investigation;
                var td = cell(tr, inv ? ("원인 영역 " + (CAUSE_LAYER_LABELS[inv.cause_layer] || inv.cause_layer || "미확정") +
                    " · " + (DIAG_STATUS_LABELS[inv.status] || inv.status || "-")) : "-");
                var b = inv ? openInvestigationButton(ctx, inv.investigation_id) : null;
                if (b) { td.appendChild(el("span", "", " ")); td.appendChild(b); }
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            wrap.appendChild(table);
            box.appendChild(wrap);
        }
        var gaps = (d.gaps || []).filter(function (g) { return g && g.count; });
        if (gaps.length) {
            var gb = section(panel, "결손");
            gaps.forEach(function (g) { gb.appendChild(el("p", "bm-note", text(g.label || g.code) + " " + g.count + "건")); });
        }
    }

    function renderInvestigate(panel, ctx, st, d) {
        var box = section(panel, "서비스 심층 조사");
        var rep = d.representative;
        box.appendChild(el("p", "bm-note", rep
            ? "대표 서버: " + text(rep.name) + (rep.rule_label ? " (" + rep.rule_label + ")" : "")
            : "대표 서버 없음"));
        var s = investigateState(d, ctx.caps);
        var btn = el("button", "bm-btn", "서비스 심층 조사");
        btn.type = "button";
        btn.setAttribute("data-bm-diag-investigate", "");
        btn.disabled = !s.enabled || !!st.submitting;
        btn.setAttribute("aria-expanded", st.preview ? "true" : "false");
        btn.addEventListener("click", function () { st.preview = !st.preview; st.result = null; renderDiagnosis(ctx); });
        box.appendChild(btn);
        if (!s.enabled) box.appendChild(el("p", "bm-note bm-note--warn", s.reason));
        if (s.enabled && st.preview) {
            var pv = el("div", "bm-diag-preview");
            pv.setAttribute("data-bm-diag-preview", "");
            var compare = Math.max(0, ((ctx.detail && ctx.detail.members) || []).length - 1);
            pv.appendChild(el("p", "", "대표 서버 " + text(rep.name) + " 기준으로 최근 " + (d.window_minutes || "-") + "분을 조사합니다"));
            pv.appendChild(el("p", "bm-muted", "비교 구성원 후보 " + compare + "대 — 권한 안 구성원만 상한까지 싣습니다"));
            pv.appendChild(el("p", "bm-muted", d.hypothesis_label ? "결과는 " + d.hypothesis_label + "입니다" : "결과는 가설 · 참고용입니다"));
            var go = el("button", "bm-btn", st.submitting ? "접수 중" : "접수");
            go.type = "button";
            go.setAttribute("data-bm-diag-submit", "");
            go.disabled = !!st.submitting;
            go.addEventListener("click", function () { submitDiagnosis(ctx); });
            var cancel = el("button", "bm-btn bm-btn--ghost", "취소");
            cancel.type = "button";
            cancel.addEventListener("click", function () { st.preview = false; renderDiagnosis(ctx); });
            var acts = el("div", "bm-diag-actions");
            acts.appendChild(go);
            acts.appendChild(cancel);
            pv.appendChild(acts);
            box.appendChild(pv);
        }
        if (st.result) {
            var res = el("p", "bm-note" + (st.result.ok ? "" : " bm-note--warn"), st.result.text);
            res.setAttribute("role", "status");
            res.setAttribute("data-bm-diag-result", st.result.ok ? "ok" : "denied");
            box.appendChild(res);
            if (st.result.notice) box.appendChild(el("p", "bm-muted", st.result.notice));
        }
    }

    function renderDiagHistory(panel, ctx, st) {
        var box = section(panel, "진단 이력");
        if (st.historyError) { box.appendChild(el("p", "bm-note bm-note--warn", "진단 이력을 불러오지 못했습니다(" + st.historyError + ")")); return; }
        var h = st.history;
        if (!h) return;
        if (h.reason) box.appendChild(el("p", "bm-note bm-note--warn", h.reason));
        var items = h.items || [];
        if (!items.length) { box.appendChild(el("p", "bm-note", "이 서비스의 진단 이력이 없습니다")); return; }
        var wrap = el("div", "bm-table-wrap");
        var table = el("table", "bm-table");
        var hr = el("tr");
        ["접수", "대표 서버", "상태", ""].forEach(function (x) { hr.appendChild(el("th", "", x)); });
        var thead = el("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = el("tbody");
        items.forEach(function (it) {
            var tr = el("tr", "bm-row");
            tr.setAttribute("data-bm-diag-history", text(it.investigation_id));
            var sub = it.subject || {};
            cell(tr, eventTime(it.submitted_at, "24h") || "-");
            cell(tr, text(sub.server_name || sub.hostname) || "-");
            cell(tr, DIAG_STATUS_LABELS[it.status] || it.status || "-");
            var td = cell(tr, "");
            var b = openInvestigationButton(ctx, it.investigation_id);
            if (b) td.appendChild(b);
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        box.appendChild(wrap);
    }

    // ─── mount ──────────────────────────────────────────────────────────

    function mount(mountRoot, opts) {
        opts = opts || {};
        var rootEl = mountRoot.querySelector("[data-bm-root]");
        if (!rootEl) throw new Error("업무 현황 마크업이 없습니다");
        function q(name) { return rootEl.querySelector('[data-bm="' + name + '"]'); }
        var fetchFn = opts.fetch || (typeof fetch === "function" ? fetch.bind(root) : null);
        var store = null;
        try { store = root.localStorage || null; } catch (_) { store = null; }

        var state = {
            board: null, detail: null,
            serviceId: opts.service ? String(opts.service) : null,
            tab: opts.tab || "overview",
            filter: { state: "", zone: "", tier: "", monitoredOnly: false, q: "" },
            layout: (store && store.getItem(LAYOUT_KEY)) === "table" ? "table" : "tile",
            active: true, unmounted: false,
            caps: null,   // (W8c) 기능 조회 응답 — mount마다 1회(실패도 {}로 둔다 · 끊긴 요청만 다시)
        };
        var inflight = [];
        var noticeTimer = null;

        function request(path, method) {
            if (!fetchFn) return Promise.resolve({ ok: false, status: 0, body: null, reason: "fetch 없음" });
            var ctrl = typeof AbortController === "function" ? new AbortController() : null;
            if (ctrl) inflight.push(ctrl);
            var headers = {};
            var token = opts.token ? opts.token() : "";
            if (token) headers.Authorization = "Bearer " + token;
            return fetchFn(path, { method: method || "GET", headers: headers, signal: ctrl ? ctrl.signal : undefined })
                .then(function (res) {
                    return res.json().catch(function () { return null; }).then(function (body) {
                        if (res.status === 401 && opts.onAuthFail) opts.onAuthFail();
                        var reason = "";
                        if (!res.ok) reason = (body && typeof body.detail === "string" ? body.detail : "") || ("HTTP " + res.status);
                        return { ok: res.ok, status: res.status, body: body, reason: reason };
                    });
                }, function (err) {
                    var aborted = err && err.name === "AbortError";
                    return { ok: false, status: 0, body: null, aborted: aborted, reason: aborted ? "" : "연결 실패" };
                })
                .then(function (r) {
                    if (ctrl) inflight = inflight.filter(function (c) { return c !== ctrl; });
                    return r;
                });
        }

        function abortAll() {
            inflight.forEach(function (c) { try { c.abort(); } catch (_) { /* 이미 끝남 */ } });
            inflight = [];
        }

        function flash(msg) {
            var box = q("flash");
            if (!box) return;
            var p = el("p", "bm-note bm-note--warn bm-flash", msg);
            p.setAttribute("role", "alert");
            box.appendChild(p);
            if (noticeTimer) clearTimeout(noticeTimer);
            noticeTimer = setTimeout(function () { noticeTimer = null; p.remove(); }, 8000);
        }

        // ── 목록 ──
        var boardPoller = createPoller(loadBoard, POLL_MS);
        var detailPoller = createPoller(loadDetail, POLL_MS);
        var heartbeat = createPoller(function () { return request(API + "/heartbeat", "POST"); }, HEARTBEAT_MS);

        function loadBoard() {
            return request(API).then(function (r) {
                if (r.aborted || state.unmounted) return;
                if (!r.ok) {
                    renderBoardError(r.status === 404 ? "업무 현황이 꺼져 있습니다" : "업무 현황을 불러오지 못했습니다(" + r.reason + ")");
                    return;
                }
                state.board = r.body || {};
                renderBoard();
            });
        }

        function renderBoardError(msg) {
            var box = q("notices");
            clear(box);
            box.appendChild(el("p", "bm-note bm-note--warn", msg));
        }

        function renderBoard() {
            var b = state.board || {};
            renderSummary(b);
            renderNotices(b);
            renderFilters(b.services || []);
            renderServices();
        }

        function renderSummary(b) {
            var chips = q("stateChips");
            clear(chips);
            var counts = (b.summary && b.summary.state_counts) || {};
            STATE_KEYS.forEach(function (k) {
                var btn = el("button", "bm-state-chip " + stateClass(k));
                btn.type = "button";
                btn.setAttribute("aria-pressed", state.filter.state === k ? "true" : "false");
                btn.appendChild(el("span", "bm-state-chip-label", STATE_LABELS[k]));
                btn.appendChild(el("span", "bm-state-chip-count", String(counts[k] || 0)));
                btn.addEventListener("click", function () {
                    state.filter.state = state.filter.state === k ? "" : k;
                    renderSummary(state.board || {});
                    renderFilters((state.board && state.board.services) || []);
                    renderServices();
                });
                chips.appendChild(btn);
            });
            q("openIncidents").textContent = "열린 사건 " + ((b.summary && b.summary.open_incidents) || 0) + "건";
            var ms = b.metrics_status || {};
            var last = hhmm(ms.last_success_at);
            q("metricsStatus").textContent = "지표 수집: " + (ms.label || "알 수 없음") + (last ? " · 마지막 성공 " + last : "");
            q("metricsStatus").className = "bm-stat bm-metrics--" + (ms.state || "off");
            q("updatedAt").textContent = "마지막 갱신 " + (hhmm(b.generated_at) || "-");
        }

        function renderNotices(b) {
            var box = q("notices");
            clear(box);
            var ms = b.metrics_status || {};
            if (ms.state === "off") box.appendChild(el("p", "bm-note", "지표 수집 꺼짐 — 알람 현황만"));
            (ms.reasons || []).slice(0, 3).forEach(function (r) { box.appendChild(el("p", "bm-note bm-note--warn", r)); });
            (b.notices || []).forEach(function (n) { box.appendChild(el("p", "bm-note", n)); });
            var u = b.unattributed || {};
            var line = q("unattributed");
            clear(line);
            if (u.recent_alarms || u.open_incidents) {
                line.appendChild(el("span", "", "서비스 미상 알람 최근 " + (u.recent_alarms || 0) + "건 · 열린 사건 " + (u.open_incidents || 0) + "건"));
                (u.tree_estimates || []).forEach(function (t) {
                    line.appendChild(el("span", "bm-muted", " · 트리 그룹 추정 " + text(t.display_name) + " " + (t.recent_alarms || 0) + "건"));
                });
            }
        }

        function fillSelect(sel, items, current, allLabel) {
            clear(sel);
            var all = el("option", "", allLabel);
            all.value = "";
            sel.appendChild(all);
            items.forEach(function (it) {
                var o = el("option", "", it.label);
                o.value = String(it.value);
                sel.appendChild(o);
            });
            sel.value = String(current);
            if (sel.value !== String(current)) sel.value = "";
        }

        function renderFilters(services) {
            var o = filterOptions(services);
            fillSelect(q("zoneFilter"), o.zones.map(function (z) { return { value: z, label: z }; }), state.filter.zone, "존 전체");
            fillSelect(q("tierFilter"), o.tiers.map(function (t) { return { value: t, label: t + "등급" }; }), state.filter.tier, "등급 전체");
            fillSelect(q("stateFilter"), STATE_KEYS.map(function (k) { return { value: k, label: STATE_LABELS[k] }; }), state.filter.state, "상태 전체");
            q("monitoredOnly").checked = state.filter.monitoredOnly;
            rootEl.querySelectorAll("[data-bm-layout]").forEach(function (b) {
                var on = b.getAttribute("data-bm-layout") === state.layout;
                b.classList.toggle("on", on);
                b.setAttribute("aria-pressed", on ? "true" : "false");
            });
        }

        function tileMetrics(card, s) {
            var m = s.metrics;
            if (!s.monitored) { card.appendChild(el("span", "bm-tile-line bm-muted", "감시 꺼짐 — 알람 현황만")); return; }
            if (!m) { card.appendChild(el("span", "bm-tile-line bm-muted", (s.reasons || [])[0] || "지표 없음")); return; }
            var apm = m.apm_max || {}, sms = m.sms_max || {};
            card.appendChild(el("span", "bm-tile-line", "APM 응답시간 " + metricText("response_time_avg_ms", apm.response_time_avg_ms) +
                " · 에러율 " + metricText("error_rate", apm.error_rate)));
            var hour = hourLabel(m.sms_stat_time);
            card.appendChild(el("span", "bm-tile-line", "SMS CPU " + metricText("cpu", sms.cpu) + " · 메모리 " +
                metricText("memory", sms.memory) + (hour ? " (" + hour + ")" : "")));
        }

        function alarmLine(s) {
            var a = s.alarms || {}, h = a.recent_by_handling || {};
            return "열린 사건 " + (a.open_incidents || 0) + " · 최근 1시간 알람 전달 " + (h.delivered || 0) + " / 억제 " + (h.suppressed || 0);
        }

        function renderServices() {
            var box = q("services");
            var empty = q("empty");
            clear(box);
            var b = state.board;
            if (!b) return;
            var all = b.services || [];
            var list = filterServices(all, state.filter);
            box.className = "bm-services bm-services--" + state.layout;
            empty.hidden = true;
            clear(empty);
            if (!all.length) {
                empty.hidden = false;
                if (b.hidden_service_count) {
                    empty.appendChild(el("span", "", "볼 수 있는 업무 서비스가 없습니다 — 권한 밖 서비스 " + b.hidden_service_count + "개"));
                } else {
                    empty.appendChild(el("span", "", "자동 계산 또는 가져오기로 서비스를 만드세요"));
                    if (opts.isAdmin && opts.isAdmin()) empty.appendChild(definitionLink(null, "업무 서비스 정의 열기"));
                }
                return;
            }
            if (!list.length) { empty.hidden = false; empty.appendChild(el("span", "", "조건에 맞는 서비스가 없습니다")); return; }
            if (state.layout === "table") renderServiceTable(box, list);
            else list.forEach(function (s) { box.appendChild(serviceTile(s)); });
            if (b.hidden_service_count) box.appendChild(el("p", "bm-note", "권한 밖 서비스 " + b.hidden_service_count + "개"));
        }

        function serviceTile(s) {
            var card = el("button", "bm-tile " + stateClass(s.state));
            card.type = "button";
            card.setAttribute("data-bm-service", String(s.id));
            if (String(s.id) === state.serviceId) card.classList.add("is-selected");
            var head = el("span", "bm-tile-head");
            head.appendChild(badge(s.state, s.state_label));
            head.appendChild(el("span", "bm-tile-tier", typeof s.tier === "number" ? s.tier + "등급" : "등급 없음"));
            card.appendChild(head);
            card.appendChild(el("span", "bm-tile-name", s.display_name));
            card.appendChild(el("span", "bm-tile-line", alarmLine(s)));
            tileMetrics(card, s);
            var foot = "구성원 " + (s.member_count || 0) + "대" + (s.hidden_member_count ? " · 권한 밖 " + s.hidden_member_count + "대" : "");
            var at = s.metrics && hhmm(s.metrics.updated_at);
            card.appendChild(el("span", "bm-tile-foot bm-muted", foot + (at ? " · 갱신 " + at : "")));
            card.addEventListener("click", function () { openService(String(s.id)); });
            return card;
        }

        function renderServiceTable(box, list) {
            var wrap = el("div", "bm-table-wrap");
            var table = el("table", "bm-table");
            var hr = el("tr");
            ["상태", "서비스", "등급", "열린 사건", "최근 1시간 전달/억제", "APM 응답시간", "APM 에러율", "SMS CPU", "SMS 메모리", "갱신"].forEach(function (h) {
                hr.appendChild(el("th", "", h));
            });
            var thead = el("thead");
            thead.appendChild(hr);
            table.appendChild(thead);
            var tbody = el("tbody");
            list.forEach(function (s) {
                var tr = el("tr", "bm-row bm-row--link");
                tr.tabIndex = 0;
                var td = el("td");
                td.appendChild(badge(s.state, s.state_label));
                tr.appendChild(td);
                cell(tr, s.display_name);
                cell(tr, typeof s.tier === "number" ? s.tier + "등급" : "-");
                var a = s.alarms || {}, h = a.recent_by_handling || {};
                cell(tr, String(a.open_incidents || 0), "bm-num");
                cell(tr, (h.delivered || 0) + " / " + (h.suppressed || 0), "bm-num");
                var m = s.monitored ? s.metrics : null;
                var apm = (m && m.apm_max) || {}, sms = (m && m.sms_max) || {};
                cell(tr, m ? metricText("response_time_avg_ms", apm.response_time_avg_ms) : "-", "bm-num");
                cell(tr, m ? metricText("error_rate", apm.error_rate) : "-", "bm-num");
                cell(tr, m ? metricText("cpu", sms.cpu) : "-", "bm-num");
                cell(tr, m ? metricText("memory", sms.memory) + (hourLabel(m.sms_stat_time) ? " (" + hourLabel(m.sms_stat_time) + ")" : "") : (s.monitored ? "-" : "감시 꺼짐"), "bm-num");
                cell(tr, m ? (hhmm(m.updated_at) || "-") : "-");
                function go() { openService(String(s.id)); }
                tr.addEventListener("click", go);
                tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter") go(); });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            wrap.appendChild(table);
            box.appendChild(wrap);
        }

        // ── 상세 ──
        var tabStates = {};
        function tabState(id) { return tabStates[id] || (tabStates[id] = {}); }

        function tabCtx() {
            var sid = state.serviceId;
            return {
                serviceId: sid, detail: state.detail, panel: q("detailPanel"),
                request: request, focusMember: focusMember, flash: flash, opts: opts,
                tabState: tabState, caps: state.caps, openTab: openTab, setWide: setWide, syncRoute: notifyRoute,
                active: function (id) { return !state.unmounted && state.serviceId === sid && state.tab === id; },
            };
        }

        function currentTab() {
            return DETAIL_TABS.filter(function (t) { return t.id === state.tab; })[0] || DETAIL_TABS[0];
        }

        // 지금 보기 탭의 정리(그래프 해제 등)
        function leaveTab() {
            var t = currentTab();
            if (t.leave) t.leave(tabCtx());
        }

        // 보기 탭 전환(같은 창) — 탭 단추와 다른 탭의 이동(지표 표지 → 이벤트)이 함께 쓴다
        function openTab(id) {
            if (state.tab === id) return;
            if (id !== "events") dropAt();
            leaveTab();
            state.tab = resolveTab(DETAIL_TABS, id, tabCtx());
            notifyRoute();
            renderTabs();
            clear(q("detailPanel"));
            currentTab().refresh(tabCtx());
        }

        // [넓게 보기] — 같은 창에서 목록 칸을 접는다(새 창 0)
        function setWide(on) { rootEl.classList.toggle("bm--wide", !!on); }

        function loadCaps() {
            if (state.caps) return Promise.resolve(state.caps);
            return request(CAPS_URL).then(function (r) {
                if (!r.aborted) state.caps = (r.ok && r.body) || {};
                return state.caps;
            });
        }

        // 지금 상세 주소 — 이벤트 보기에 시각 지정이 있으면 `at`(ISO 8601 UTC)을 더한다
        function routeOf() {
            var r = { service: state.serviceId, tab: state.serviceId ? state.tab : null };
            var es = tabStates.events;
            if (r.service && r.tab === "events" && es && es.serviceId === r.service && typeof es.focusAt === "number") r.at = atIso(es.focusAt);
            return r;
        }

        function dropAt() { if (tabStates.events) tabStates.events.focusAt = null; }

        function notifyRoute() {
            if (opts.onRoute) opts.onRoute(routeOf());
        }

        function openService(id) {
            if (state.serviceId !== id) { leaveTab(); dropAt(); state.detail = null; clear(q("detailHead")); clear(q("detailPanel")); }
            state.serviceId = id;
            state.tab = resolveTab(DETAIL_TABS, state.tab, tabCtx());
            notifyRoute();
            showDetail(true);
            detailPoller.stop();
            apply();
            renderServices();
        }

        function closeDetail() {
            leaveTab();
            dropAt();
            state.serviceId = null;
            state.detail = null;
            notifyRoute();
            showDetail(false);
            detailPoller.stop();
            apply();
            renderServices();
        }

        function showDetail(on) {
            q("detailPane").hidden = !on;
            rootEl.classList.toggle("bm--detail", on);
        }

        function loadDetail() {
            var id = state.serviceId;
            if (!id) return Promise.resolve();
            return Promise.all([request(API + "/" + encodeURIComponent(id)), loadCaps()]).then(function (res) {
                var r = res[0];
                if (r.aborted || state.unmounted || state.serviceId !== id) return;
                if (r.status === 404) {
                    closeDetail();
                    flash("업무 서비스를 찾을 수 없습니다(없거나 권한 밖입니다)");
                    return;
                }
                if (!r.ok) { flash("상세를 불러오지 못했습니다(" + r.reason + ")"); return; }
                state.detail = r.body || {};
                // 기능 조회를 받은 뒤 보기 탭을 다시 고른다(꺼진 「지표」 → 개요 · `&tab=metrics` 복원)
                var tab = resolveTab(DETAIL_TABS, state.tab, tabCtx());
                if (tab !== state.tab) { state.tab = tab; notifyRoute(); }
                renderDetailHead();
                renderTabs();
                return currentTab().refresh(tabCtx());
            });
        }

        function renderDetailHead() {
            var d = state.detail || {}, s = d.service || {};
            var head = q("detailHead");
            clear(head);
            var top = el("div", "bm-detail-top");
            top.appendChild(badge(s.state, s.state_label));
            top.appendChild(el("h2", "bm-detail-name", s.display_name));
            var actions = el("div", "bm-detail-actions");
            if (opts.fillQuestion) {
                var ask = el("button", "bm-btn", "질의응답에서 묻기");
                ask.type = "button";
                ask.title = "질의응답 탭 입력창에 질문을 채웁니다(보내지 않습니다)";
                ask.addEventListener("click", function () {
                    if (opts.fillQuestion(askText(state.detail)) === false) flash("질의응답 탭으로 옮기지 못했습니다 — 답변이 끝난 뒤 다시 누르세요");
                });
                actions.appendChild(ask);
            }
            if (opts.isAdmin && opts.isAdmin()) actions.appendChild(definitionLink(s.id, "정의에서 편집"));
            var back = el("button", "bm-btn bm-btn--ghost", "목록으로");
            back.type = "button";
            back.addEventListener("click", closeDetail);
            actions.appendChild(back);
            top.appendChild(actions);
            head.appendChild(top);
            var why = evidenceLine(s);
            if (why) head.appendChild(el("p", "bm-detail-why", "판정 근거: " + why));
            var meta = [
                typeof s.tier === "number" ? s.tier + "등급" : "등급 없음",
                "담당 " + (s.owner_org || "-"),
                "구성원 " + (s.member_count || 0) + "대" + (d.hidden_member_count ? " (권한 밖 " + d.hidden_member_count + "대)" : ""),
            ];
            if (!s.monitored) meta.push("감시 꺼짐");
            else if (s.metrics) {
                var at = hhmm(s.metrics.updated_at), hour = hourLabel(s.metrics.sms_stat_time);
                meta.push("지표 " + (at || "-") + (hour ? " · SMS " + hour : ""));
            }
            head.appendChild(el("p", "bm-detail-meta bm-muted", meta.join(" · ")));
            if ((d.metrics_status || {}).state === "off") head.appendChild(el("p", "bm-note", "지표 수집 꺼짐 — 알람 현황만"));
            if (d.unattributed_service_alarms) head.appendChild(el("p", "bm-note", "구성원 미상 알람 " + d.unattributed_service_alarms + "건"));
        }

        function renderTabs() {
            var bar = q("detailTabs");
            clear(bar);
            var ctx = tabCtx();
            DETAIL_TABS.filter(function (t) { return !t.enabled || t.enabled(ctx); }).forEach(function (t) {
                var b = el("button", "bm-tab" + (t.id === state.tab ? " on" : ""), t.label);
                b.type = "button";
                b.setAttribute("role", "tab");
                b.setAttribute("aria-selected", t.id === state.tab ? "true" : "false");
                b.setAttribute("data-bm-tab", t.id);
                b.addEventListener("click", function () { openTab(t.id); });
                bar.appendChild(b);
            });
        }

        function focusMember(id) {
            var row = q("detailPanel").querySelector('[data-bm-member="' + String(id).replace(/"/g, "") + '"]');
            if (!row) return;
            row.classList.add("bm-row--focus");
            if (row.scrollIntoView) row.scrollIntoView({ block: "center", behavior: "smooth" });
            if (row.focus) row.focus({ preventScroll: true });
            setTimeout(function () { row.classList.remove("bm-row--focus"); }, FOCUS_MS);
        }

        // ── 실행 상태(폴링·heartbeat) ──
        function apply() {
            if (state.unmounted) return;
            var run = runnable(state.active, typeof document !== "undefined" && document.hidden);
            if (!run) {
                boardPoller.stop(); detailPoller.stop(); heartbeat.stop();
                abortAll();
                return;
            }
            heartbeat.start();
            if (state.serviceId) {
                boardPoller.stop();
                if (!state.board) loadBoard();
                detailPoller.start();
            } else {
                detailPoller.stop();
                boardPoller.start();
            }
        }

        // ── 고정 배선 ──
        function onVisibility() { apply(); }
        document.addEventListener("visibilitychange", onVisibility);

        rootEl.querySelectorAll("[data-bm-layout]").forEach(function (b) {
            b.addEventListener("click", function () {
                state.layout = b.getAttribute("data-bm-layout") === "table" ? "table" : "tile";
                try { if (store) store.setItem(LAYOUT_KEY, state.layout); } catch (_) { /* 저장 실패는 이번 화면만 */ }
                renderFilters((state.board && state.board.services) || []);
                renderServices();
            });
        });
        [["zoneFilter", "zone"], ["tierFilter", "tier"], ["stateFilter", "state"]].forEach(function (p) {
            q(p[0]).addEventListener("change", function () {
                state.filter[p[1]] = q(p[0]).value;
                renderSummary(state.board || {});
                renderServices();
            });
        });
        q("monitoredOnly").addEventListener("change", function () { state.filter.monitoredOnly = q("monitoredOnly").checked; renderServices(); });
        q("search").addEventListener("input", function () { state.filter.q = q("search").value; renderServices(); });

        var fsBtn = q("fullscreen");
        function syncFullscreen() { fsBtn.textContent = document.fullscreenElement === rootEl ? "전체 화면 끝내기" : "전체 화면"; }
        if (!rootEl.requestFullscreen) fsBtn.hidden = true;
        else {
            fsBtn.addEventListener("click", function () {
                if (document.fullscreenElement === rootEl) document.exitFullscreen();
                else rootEl.requestFullscreen().catch(function () { flash("전체 화면으로 바꾸지 못했습니다"); });
            });
            document.addEventListener("fullscreenchange", syncFullscreen);
        }

        if (state.serviceId) {
            state.tab = resolveTab(DETAIL_TABS, state.tab, tabCtx());
            // (§5.2.1) `&tab=events&at=` 복원 — 그 시각을 담는 가장 작은 기간으로 연다(무효면 무시 · 사유 문구 0)
            var atMs = state.tab === "events" ? parseAt(opts.at, Date.now()) : null;
            if (atMs !== null) {
                var es = eventsState(tabCtx());
                es.window = atWindow(atMs, Date.now(), es.window);
                setFocusAt(es, atMs);
            }
            showDetail(true);
            notifyRoute();
        }
        apply();

        return {
            pause: function () { state.active = false; apply(); },
            resume: function () { state.active = true; apply(); },
            unmount: function () {
                state.active = false;
                apply();
                if (state.serviceId) leaveTab();
                state.unmounted = true;
                document.removeEventListener("visibilitychange", onVisibility);
                document.removeEventListener("fullscreenchange", syncFullscreen);
            },
            // 지금 상세(주소 동기화용)
            route: routeOf,
        };
    }

    var api = {
        mount: mount,
        DETAIL_TABS: DETAIL_TABS,
        pure: {
            STATE_LABELS: STATE_LABELS, hourLabel: hourLabel, hhmm: hhmm, fmtNum: fmtNum,
            filterOptions: filterOptions, filterServices: filterServices, sortMembers: sortMembers,
            evidenceLine: evidenceLine, askText: askText, resolveTab: resolveTab, runnable: runnable,
            createPoller: createPoller, layoutTopology: layoutTopology, bandLayout: bandLayout,
            sparkSeries: sparkSeries, eventsUrl: eventsUrl, filterEventRows: filterEventRows,
            eventMembers: eventMembers, eventTime: eventTime,
            metricsUrl: metricsUrl, gapPoints: gapPoints, laneGroups: laneGroups, eventMarkers: eventMarkers,
            cursorValues: cursorValues, metricTime: metricTime, statLabel: statLabel,
            investigateState: investigateState, rejectText: rejectText,
            parseAt: parseAt, atWindow: atWindow, nearestEvent: nearestEvent, atIso: atIso,
        },
        // (W8b) 이벤트 타임라인 응답 공유 — 보기 탭 ctx로 부르면 60초 안 같은 결과를 추가 조회 없이 돌려준다
        events: { load: loadEvents },
    };
    root.BizMonitor = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
