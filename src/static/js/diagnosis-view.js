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
 *   - 실행 기록은 최신이 위, 탭당 300행 상한. AI 메모(`kind=ai_note`) 행은 종전대로 운영자 화면에만
 *     그린다(G-38 · D-035 — 결론처럼 보이지 않게 다른 배경 + 「검증 전」 배지).
 *   - (plans/187 W20 · §5.6.4 · G-47~G-50) 조사 하위 탭 안에 「조사 흐름」(처음)·「실행 기록」 전환 탭.
 *     실행 기록 = 위 종전 화면 그대로(숨김만 바꾼다) · 조사 흐름 = 단계 카드(왜 → 조회 → 결과 → 해석)와
 *     「지금까지」 줄 · HolmesGPT 회차 카드(AI 메모를 「AI 판단 — 검증 전」으로 — G-48 사용자·관리자 모두,
 *     서버가 두 겹 가림 후 보낸다). 같은 이벤트를 두 보기로 그린다 — 스트림 연결·커서는 하나다.
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
    // (plans/187 W18) 「이벤트 찾아 조사」 검색 — 지난 알람 검색(G-29)과 같은 판정 기록 사용자 경로
    var DECISIONS_API = "/api/v1/noise/decisions";
    var SEARCH_SIZE = 30;
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
        // (plans/187 W18) 이벤트 찾아 조사
        searchTitle: "이벤트 찾아 조사",
        searchSince: "시작",
        searchUntil: "끝",
        searchAlarm: "알람명",
        searchServer: "서버",
        searchBtn: "찾기",
        searchHint: "판정 시각 기준 · 시간을 비우면 최근 24시간(보존 30일) · 최근 30건까지",
        searchHead: ["판정 시각", "서버", "알람명", "판정", ""],
        searching: "찾는 중…",
        searchEmpty: "조건에 맞는 알람이 없습니다.",
        searchCount: "판정 기록 {total}건 중 최근 {n}건",
        searchFailed: "알람을 찾지 못했습니다 — {reason}",
        searchBadRange: "시작이 끝보다 늦습니다.",
        startBtn: "조사 시작",
        starting: "접수 중…",
        startJoined: "같은 알람의 진행 중인 조사에 합류했습니다.",
        startRejected: "조사 서비스가 거부했습니다 — {reason}",
        startFailed: "조사를 시작하지 못했습니다 — {reason}",
        noAlarmId: "알람 id가 없는 판정 기록이라 조사할 수 없습니다.",
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
        // (plans/187 W17) 머리 — 어떤 알람·대상을 왜 조사하는지
        alarmHead: "알람",
        targetHead: "대상",
        whyHead: "조사 이유",
        conditionHead: "조건",
        alarmUnknown: "알람 정보를 찾지 못했습니다(판정 기록 보존 범위 밖이거나 알람 없는 요청 조사).",
        // (W23) 조사 대화 카드 머리 — 무엇을 어떤 방식으로 조사하는지
        kindHead: "조사",
        decisionHead: "판정 사유",
        relatedHead: "함께 조사하는 알람",
        playbookHead: "플레이북",
        // (W24) 알람 발생 소스 · 조사 항목(플레이북 단계)
        sourceHead: "발생 소스",
        stepsHead: "조사 항목",
        stepsRunning: "진행 중 {step} · {done}/{total}단계 끝남",
        purposePrefix: "목적: ",
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
        // (plans/187 W20 · §5.6.4 · G-50) 「조사 흐름」 — 단계 카드(왜 → 조회 → 결과 → 해석)
        modeFlow: "조사 흐름",
        modeLog: "실행 기록",
        flowEmpty: "아직 조사 단계가 없습니다. 단계가 시작되면 카드가 쌓입니다.",
        sofarHead: "지금까지",
        sofarChecking: "{title} 확인 중 ⟳",
        sofarFinal: "종합 — {title}({status})",
        sofarNone: "아직 이상으로 본 단계가 없습니다.",
        cardWhy: "왜",
        cardQuery: "조회",
        cardResult: "결과",
        cardOpinion: "해석",
        cardRunning: "⟳ 진행 중",
        skipTitle: "건너뜀 {title}",
        skipNoReason: "생략",
        // 섹션 사유 원문(`when_false — <조건식>`·`budget — …`)은 카드 문장에 쓰지 않는다(원문은 실행 기록 탭)
        skipWhenFalse: "조건에 맞지 않아 생략",
        skipBudget: "시간 예산 소진으로 생략",
        tableOpen: "표 보기 ▸",
        tableClose: "표 접기 ▾",
        verdicts: { degraded: "이상", normal: "정상", no_was: "WAS 아님" },
        liveBadge: "현재값 · 조사 시각 {hm}",
        opinionMark: "AI · 이 단계까지의 근거",
        opinionPending: "AI 해석 작성 중 ⟳",
        opinionNone: "AI 해석 없음 — {reason}",
        opinionNoneReason: "사유 미상",
        conflictChip: "종합과 다름 — 종합 기준",
        cautionBtn: "ⓘ주의",
        holmesRound: "HolmesGPT {n}회차",
        holmesJudge: "AI 판단 — 검증 전",
        holmesNoQuery: "이 회차에는 조회가 없습니다.",
        engineGroups: { playbook: "플레이북", holmes: "HolmesGPT" },
        // ⓖ C1~C8 — 해당하는 문구만 카드 ⓘ에 펼친다
        caution: {
            c1: "뒤 단계 결과에 따라 달라질 수 있습니다. 최종 판단은 종합을 따르세요.",
            c2: "원인 확정이 아닌 단계 해석입니다.",
            c3: "종합(가설 순위)과 판정 방향이 다릅니다 — 종합을 기준으로 보세요.",
            c4: "현재값입니다 — 알람 시각이 아니라 조사 시각 기준입니다.",
            c4drift: "알람 시점과 다를 수 있음",
            c5: "가드 탈락·시간 초과 등으로 AI 해석이 없습니다 — 결과 한 줄(사실)은 그대로입니다.",
            c6: "수치는 표에 있는 값만 씁니다 — 「표 보기 ▸」로 원 표를 확인하세요.",
            c7: "사용자 화면에도 보이는 문장입니다 — 민감값은 두 번 가리지만 놓친 값이 있을 수 있습니다.",
            c8: "단계가 끝난 뒤 AI 응답 시간만큼 늦게 붙습니다.",
            holmes: "자동 검사를 거치지 않은 AI 중간 판단입니다.",
        },
    };

    var ENGINE_LABELS = { playbook: "플레이북", holmes: "HolmesGPT", system: "시스템" };
    // 폴스타 심각도 숫자 → 이름(판정 기록 `signals.severity` · 0 = 해소)
    var SEVERITY_LABELS = { 0: "해소", 1: "정보", 2: "경고", 3: "위험", 4: "치명" };
    var TIER_LABELS = { page: "즉시 통보", digest: "묶음 통보", suppress: "억제", drop: "버림" };
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
            // (plans/187 W17) 무엇 때문에 무엇을 보는지 — 플레이북 선택 근거 · 스텝 초점 · HolmesGPT 조사 이유
            purpose: String(e.purpose || ""),
        };
    }

    /**
     * (plans/187 W18) 검색 칸 값 → 판정 기록 조회 쿼리(순수). 시간을 하나라도 넣으면 30일 창 + 구간,
     * 비우면 최근 24시간. 시작이 끝보다 늦으면 null(화면이 사유를 보인다).
     * f = {since, until, alarm, server} — since/until은 datetime-local 값(브라우저 지역 시각).
     */
    function searchQuery(f) {
        f = isObj(f) ? f : {};
        var params = [];
        function add(k, v) { params.push(encodeURIComponent(k) + "=" + encodeURIComponent(v)); }
        function iso(v) {
            if (!v) return "";
            var d = new Date(String(v));
            return isNaN(d.getTime()) ? "" : d.toISOString();
        }
        var since = iso(f.since);
        var until = iso(f.until);
        if (since && until && since >= until) return null;
        add("range", since || until ? "30d" : "24h");
        if (since) add("since", since);
        if (until) add("until", until);
        var alarm = String(f.alarm || "").trim();
        var server = String(f.server || "").trim();
        if (alarm) add("alarm_q", alarm);
        if (server) add("server_q", server);
        add("size", String(SEARCH_SIZE));
        return params.join("&");
    }

    /** 실행 기록에서 조사 이유 줄을 모은다 — 플레이북 선택(`started`·playbook)과 HolmesGPT 시작(`deep_start`). */
    function whyLines(events) {
        var out = [];
        asList(events).forEach(function (e) {
            if (!isObj(e) || !e.purpose) return;
            var plan = (e.kind === "started" && e.engine === "playbook") || e.kind === "deep_start";
            if (plan) out.push({ seq: Number(e.seq) || 0, text: String(e.label || "") + " — " + String(e.purpose) });
        });
        return out;
    }

    /** 스텝 id → 그 스텝의 목적(`step_start` purpose) — 도구 행의 마우스 올림 설명에 쓴다. */
    function stepPurposes(events, into) {
        asList(events).forEach(function (e) {
            if (isObj(e) && e.kind === "step_start" && e.step && e.purpose) into[String(e.step)] = String(e.purpose);
        });
        return into;
    }

    function fmtStamp(value) {
        if (!value) return "";
        var d = new Date(value);
        if (isNaN(d.getTime())) return String(value);
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
            pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    }

    /**
     * 머리의 「어떤 알람·대상을 조사하는가」 줄 모델(순수 · 서버 텍스트는 화면이 textContent로 넣는다).
     * alarm = 조회 응답 `alarm`(판정 기록 요약), subject = 레코드 `subject`(채팅·요청 조사).
     */
    function targetLines(alarm, subject) {
        var lines = [];
        if (isObj(alarm)) {
            var sev = alarm.severity !== undefined ? SEVERITY_LABELS[alarm.severity] || ("심각도 " + alarm.severity) : "";
            var where = [alarm.server_name || alarm.hostname, alarm.resource_name].filter(Boolean).join(" · ");
            var when = fmtStamp(alarm.alarm_time || alarm.ts);
            var tier = alarm.tier ? (TIER_LABELS[alarm.tier] || String(alarm.tier)) : "";
            lines.push({ head: TEXT.alarmHead, text: [sev ? "[" + sev + "]" : "", alarm.alarm_name || alarm.alarm_id]
                .filter(Boolean).join(" ") + (where ? " — " + where : "") + (when ? " · 발생 " + when : "") +
                (tier ? " · 판정 " + tier : "") +
                (alarm.alarm_count > 1 ? " · 같은 사건 알람 " + alarm.alarm_count + "건 중 첫 알람" : "") });
            if (alarm.source || alarm.db_id) {
                lines.push({ head: TEXT.sourceHead, text: (alarm.source || alarm.db_id) +
                    (alarm.db_id && alarm.source && alarm.source !== alarm.db_id ? " (" + alarm.db_id + ")" : "") });
            }
            if (alarm.condition_log) lines.push({ head: TEXT.conditionHead, text: firstLine(alarm.condition_log, 200) });
        }
        if (isObj(subject)) {
            var who = subject.server_name || subject.hostname;
            var ref = fmtStamp(subject.reference_time);
            if (who || ref) {
                lines.push({ head: TEXT.targetHead, text: (who || "") + (ref ? (who ? " · " : "") + "기준 " + ref : "") });
            }
            if (subject.route_label || subject.route_reason) {
                lines.push({ head: TEXT.whyHead, text: [subject.route_label, subject.route_reason].filter(Boolean).join(" — ") });
            }
        }
        return lines;
    }

    // (W23) 조사 종류 문구 — 조사 기록 `kind`·`origin`·`focus`
    function kindText(view) {
        if (view.parent_investigation_id) return "추가 조사" + (view.focus ? " — 초점 " + view.focus : "");
        if (view.kind === "episode") {
            var n = asList(view.alarm_ids).length || (isObj(view.alarm) && view.alarm.alarm_count) || 0;
            return "사건 종합 조사" + (n > 1 ? " — 같은 사건 알람 " + n + "건을 함께 조사" : "");
        }
        if (view.kind === "manual") return "다시 분석(사용자 요청)";
        if (view.origin === "chat") return "채팅 증상 조사";
        if (view.kind === "diagnosis") return "요청 조사";
        return "알람 자동 조사";
    }

    // (W24) 조사 항목의 단계 상태 — 플레이북 섹션 `status`
    var STEP_STATUS_LABELS = { ok: "완료", skipped: "생략", missing: "자료 없음", error: "실패",
        failed: "실패", timeout: "시간 초과", incomplete: "일부만" };

    // (W24) 조사 항목 — 종결 브리핑의 단계(없으면 진행 중 부분 브리핑의 끝난 단계 + 진척)
    function stepsLine(view) {
        var pb = playbookOf(view);
        var sections = pb ? asList(pb.sections).filter(isObj) : [];
        var items = sections.map(function (sec, i) {
            var label = STEP_STATUS_LABELS[sec.status] || sec.status || "";
            return circled(i + 1) + " " + (sec.title || sec.step || "-") + (label ? " — " + label : "");
        });
        var prog = isObj(view.progress) ? view.progress : null;
        var finished = isObj(view.briefing) && isObj(view.briefing.playbook);
        if (!finished && prog && Number(prog.total)) {
            items.push(fill(TEXT.stepsRunning, { step: prog.current_step || "-",
                done: Number(prog.done) || 0, total: Number(prog.total) }));
        }
        return items.length ? { head: TEXT.stepsHead, text: items.join(" / "), items: items } : null;
    }

    // 종결 브리핑의 플레이북 — 없으면 진행 중 부분 브리핑의 플레이북
    function playbookOf(view) {
        if (isObj(view.briefing) && isObj(view.briefing.playbook)) return view.briefing.playbook;
        var part = view.partial_briefing;
        return isObj(part) && isObj(part.playbook) ? part.playbook : null;
    }

    // 플레이북 선택 근거(`matched_by`) — sre_agent `playbook_purpose`와 같은 말(MATCH_CONDITION_LABELS)
    var MATCH_CONDITION_LABELS = { resource_type: "자원 유형", alarm_kind: "알람 종류", any_kind: "알람 키워드" };
    function matchedByText(m) {
        m = String(m || "");
        if (m.indexOf("match:") === 0) {
            var names = m.slice(6).split("+").filter(Boolean).map(function (c) { return MATCH_CONDITION_LABELS[c] || c; });
            if (names.length) return "알람의 " + names.join("·") + "가 이 플레이북 조건과 일치";
        }
        if (m === "fallback") return "맞는 유형 플레이북이 없어 공통 점검";
        return m;
    }

    /**
     * 조사 대화 카드 머리 줄 모델(순수 · W23) — 조사 종류 · 알람 · 판정 사유 · 조건 · 함께 조사하는 알람 ·
     * 대상/조사 이유 · 플레이북. view = 조사 조회 응답(`with_alarm=true`면 `alarm`·`alarm.related`가 있다).
     */
    function conversationHeadLines(view) {
        if (!isObj(view)) return [];
        var lines = [{ head: TEXT.kindHead, text: kindText(view) }];
        var alarm = isObj(view.alarm) ? view.alarm : null;
        var target = targetLines(alarm, view.subject);
        if (!alarm && (asList(view.alarm_ids).length || view.parent_alarm_id || view.kind === "alarm")) {
            target.unshift({ head: TEXT.alarmHead, text: TEXT.alarmUnknown });
        }
        target.forEach(function (line, i) {
            lines.push(line);
            if (i === 0 && alarm && (alarm.reason || alarm.stage_label)) {
                lines.push({ head: TEXT.decisionHead,
                    text: [alarm.stage_label, firstLine(alarm.reason || "", 200)].filter(Boolean).join(" — ") });
            }
        });
        var related = alarm ? asList(alarm.related) : [];
        if (related.length) {
            var more = (alarm.alarm_count || 0) - 1 - related.length;
            lines.push({ head: TEXT.relatedHead, text: related.map(function (r) {
                var when = fmtStamp(r.alarm_time || r.ts);
                return (r.alarm_name || r.alarm_id || "-") + " — " + (r.server_name || r.hostname || "-") +
                    (r.source ? " · " + r.source : "") + (when ? " · " + when : "");
            }).join(" / ") + (more > 0 ? " 외 " + more + "건" : "") });
        }
        var pb = playbookOf(view);
        if (pb && pb.id) {
            var scope = isObj(pb.scope) ? (pb.scope.host || pb.scope.polestar_server || "") : "";
            lines.push({ head: TEXT.playbookHead, text: pb.id + (pb.version ? " v" + pb.version : "") +
                (pb.matched_by ? " — " + matchedByText(pb.matched_by) : "") + (scope ? " · 범위 " + scope : "") });
        }
        var steps = stepsLine(view);
        if (steps) lines.push(steps);
        return lines;
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

    // ─── (plans/187 W20) 조사 흐름 — 순수 함수 ─────────────────────────────

    // 시각 — 실행 기록 `at`은 epoch 초(실시간), 보관 요약은 ISO 문자열일 수 있다
    function atMs(at) {
        if (typeof at === "number" || (typeof at === "string" && /^\d+(\.\d+)?$/.test(at))) {
            var n = Number(at);
            return isFinite(n) && n > 0 ? n * 1000 : null;
        }
        if (!at) return null;
        var d = new Date(String(at));
        return isNaN(d.getTime()) ? null : d.getTime();
    }

    function clockOf(at, short) {
        var ms = atMs(at);
        if (ms === null) return "";
        var d = new Date(ms);
        return pad(d.getHours()) + ":" + pad(d.getMinutes()) + (short ? "" : ":" + pad(d.getSeconds()));
    }

    function circled(n) {
        return n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : "(" + n + ")";
    }

    // 「S3a 실행 중 요청」 → 「실행 중 요청」(단계 id 머리 제거)
    function stripStep(label, step) {
        var text = String(label || "");
        if (step && text.indexOf(step + " ") === 0) return text.slice(step.length + 1);
        return text;
    }

    /** 단계 해석 정규화 — 진행 중 `{status,text,reason}`(W20 계약) · 종결 문자열 + `opinion_dropped_reason`. */
    function opinionOf(sec) {
        if (!isObj(sec)) return null;
        var op = sec.opinion;
        if (isObj(op)) {
            var st = String(op.status || "");
            if (st === "ok" && op.text) return { status: "ok", text: String(op.text) };
            if (st === "pending") return { status: "pending" };
            if (st === "none" || st === "ok") return { status: "none", reason: String(op.reason || "") };
            return null;
        }
        if (typeof op === "string" && op) return { status: "ok", text: op };
        if (sec.opinion_dropped_reason) return { status: "none", reason: String(sec.opinion_dropped_reason) };
        return null;
    }

    // 「지금까지」 고리 한 구 — 결과 한 줄의 첫 마디(「 · 」·「 — 」 앞)
    function corePhrase(finding) {
        var text = String(finding || "").split("\n")[0];
        text = text.split(" · ")[0].split(" — ")[0];
        return firstLine(text, 60);
    }

    function firstSentence(text) {
        var line = String(text || "").split("\n")[0];
        var m = /^(.+?[.!?。])(\s|$)/.exec(line);
        return firstLine(m ? m[1] : line, 120);
    }

    function topHypothesis(synthesis) {
        var hyps = asList(isObj(synthesis) ? synthesis.hypotheses : null).filter(isObj).slice();
        hyps.sort(function (a, b) { return (Number(a.rank) || 99) - (Number(b.rank) || 99); });
        return hyps[0] || null;
    }

    /**
     * C3 「종합과 다름」(결정적 · LLM 0). 규칙:
     *   - 종결 뒤에만 · 해석(`opinion` ok)이 있는 단계만 · 종합 1위 가설(rank 최소)의 `evidence_steps`에 든 단계만.
     *   - 가설 극성: `supported`(지지) = 이상 · `refuted`(반박) = 정상 · `inconclusive`(판단 보류) = 극성 없음(칩 0).
     *   - 단계 극성: `verdict` `degraded` = 이상 · `normal` = 정상 · 그 밖(판정 없음·no_was) = 극성 없음(칩 0).
     *   - 두 극성이 모두 있고 서로 다르면 칩을 단다.
     */
    function conflictsWithSynthesis(sec, top) {
        if (!isObj(sec) || !isObj(top)) return false;
        if (asList(top.evidence_steps).map(String).indexOf(String(sec.step || "")) < 0) return false;
        var hyp = { supported: "bad", refuted: "good" }[top.status];
        var step = { degraded: "bad", normal: "good" }[sec.verdict];
        return !!(hyp && step && hyp !== step);
    }

    // 현재값 배지가 알람 시각과 10분 넘게 떨어졌는가 — 배지 「알람 후 N분」을 먼저, 없으면 시각 차이
    var DRIFT_MIN = 10;
    function liveDrift(badge, stepAt, alarmTime) {
        var m = /알람 후 (\d+)분/.exec(badge) || /알람 (\d+)분 전/.exec(badge);
        if (m) return Number(m[1]) > DRIFT_MIN;
        var a = atMs(stepAt);
        var b = atMs(alarmTime);
        return a !== null && b !== null && Math.abs(a - b) > DRIFT_MIN * 60000;
    }

    function queryOf(e) {
        return {
            text: String(e.phrase || e.label || e.tool || ""),
            tool: String(e.tool || ""),
            icon: rowIcon(e),
            warn: !!WARN_STATUS[String(e.status || "")],
            rows: e.rows !== undefined && e.rows !== null ? String(e.rows) + "행" : "",
            elapsed: e.elapsed_ms !== undefined && e.elapsed_ms !== null ? fmtMs(e.elapsed_ms) : "",
            excerpt: String(e.excerpt || ""),
            seq: Number(e.seq) || 0,
            open: e.kind === "tool_start",
            phrase: !!e.phrase,
        };
    }

    // 도구 시작·끝을 한 줄로 합친다 — 끝 행이 오면 같은 도구의 열린 줄을 채운다
    function addQuery(list, e) {
        if (e.kind === "tool_end") {
            for (var i = 0; i < list.length; i++) {
                if (list[i].open && list[i].tool === String(e.tool || "")) {
                    var q = queryOf(e);
                    if (!q.phrase && list[i].phrase) { q.text = list[i].text; q.phrase = true; }
                    list[i] = q;
                    return;
                }
            }
        }
        list.push(queryOf(e));
    }

    /**
     * 실행 기록 이벤트 + 단계 섹션(진행 중 `partial_briefing` 또는 종결 브리핑) → 조사 흐름 모델(순수).
     * o = {done, synthesis, alarmTime}. 새 키(`because`·`finding`·`phrase`·`step_skip`)가 없는 구버전
     * 이벤트도 한 줄(`label`)로 카드를 만든다(빈칸 0).
     */
    function flowModel(events, sections, o) {
        o = o || {};
        var evs = asList(events).filter(isObj).slice().sort(function (a, b) { return (a.seq || 0) - (b.seq || 0); });
        var secs = asList(sections).filter(function (s) {
            return isObj(s) && s.step && s.action !== "synthesize" && s.status !== "pending_synthesis";
        });
        var secBy = {};
        secs.forEach(function (s) { secBy[String(s.step)] = s; });
        var cards = [];
        var by = {};
        function card(step) {
            if (!by[step]) {
                by[step] = { step: step, title: "", at: null, started: false, ended: false, skipped: false,
                    status: "", elapsed: "", because: "", purpose: "", finding: "", queries: [] };
                cards.push(by[step]);
            }
            return by[step];
        }
        var rounds = [];
        var round = null;
        evs.forEach(function (e) {
            var kind = String(e.kind || "");
            if (e.engine === "holmes") {
                if (kind === "ai_note") {
                    round = { at: e.at, note: String(e.note || ""), label: String(e.label || ""), queries: [] };
                    rounds.push(round);
                } else if (kind === "tool_start" || kind === "tool_end") {
                    if (!round) { round = { at: e.at, note: "", label: "", queries: [] }; rounds.push(round); }
                    addQuery(round.queries, e);
                }
                return;
            }
            // 단계 카드는 단계·도구 이벤트로만 만든다(소견 LLM·초안 메모는 카드의 「해석」이 섹션에서 읽는다)
            if (e.engine !== "playbook" || !e.step || !/^(step_start|step_end|step_skip|tool_start|tool_end)$/.test(kind)) return;
            var c = card(String(e.step));
            if (kind === "step_start") {
                c.started = true;
                c.at = e.at;
                c.title = c.title || stripStep(e.label, c.step);
                if (e.because) c.because = String(e.because);
                if (e.purpose) c.purpose = String(e.purpose);
            } else if (kind === "step_end") {
                c.ended = true;
                c.endAt = e.at;
                if (c.at === null) c.at = e.at;
                c.title = c.title || stripStep(e.label, c.step);
                c.status = String(e.status || "");
                c.elapsed = e.elapsed_ms !== undefined && e.elapsed_ms !== null ? fmtMs(e.elapsed_ms) : "";
                if (e.finding) c.finding = String(e.finding);
                if (!c.purpose && e.purpose) c.purpose = String(e.purpose);
            } else if (kind === "step_skip") {
                c.skipped = true;
                c.at = c.at === null ? e.at : c.at;
                c.title = c.title || stripStep(e.label, c.step);
                if (e.because) c.because = String(e.because);
            } else if (kind === "tool_start" || kind === "tool_end") {
                if (c.at === null) c.at = e.at;
                addQuery(c.queries, e);
            }
        });
        // 카드 순서(결정적) — 섹션(플레이북 순서)에 있는 단계가 섹션 순서로 먼저, 섹션에 없는 단계(진행 중 ·
        // 구버전 이벤트만)는 그 뒤에 첫 이벤트 seq 순. 병렬 단계의 시작 순서가 바뀌어도 순서가 같다.
        var ordered = [];
        secs.forEach(function (s) {
            var c = card(String(s.step));
            if (ordered.indexOf(c) < 0) ordered.push(c);
        });
        cards.forEach(function (c) { if (!secBy[c.step]) ordered.push(c); });
        cards = ordered;
        var top = o.done ? topHypothesis(o.synthesis) : null;
        var models = cards.map(function (c, i) {
            var sec = secBy[c.step] || null;
            var skipped = c.skipped || (sec && sec.status === "skipped");
            var title = (sec && sec.title) || c.title || c.step;
            var m = { step: c.step, no: circled(i + 1), title: title, time: clockOf(c.at), skipped: !!skipped };
            if (skipped) {
                m.because = c.because || (sec && (sec.because || skipReason(sec.reason))) || "";
                return m;
            }
            var status = c.status || (sec ? String(sec.status || "") : "");
            m.running = !o.done && c.started && !c.ended && !sec;
            m.warn = !!WARN_STATUS[status];
            m.icon = m.running ? "" : (c.ended || sec ? (m.warn ? "⚠" : "✓") : "");
            m.elapsed = c.elapsed;
            m.verdict = sec && TEXT.verdicts[sec.verdict] ? String(sec.verdict) : "";
            m.verdictLabel = m.verdict ? TEXT.verdicts[m.verdict] : "";
            m.because = c.because || c.purpose;
            m.queries = c.queries;
            m.finding = c.finding || (sec && sec.finding ? String(sec.finding) : "");
            m.reason = !m.finding && sec && sec.reason ? String(sec.reason) : "";
            m.table = sec && isObj(sec.table) && Array.isArray(sec.table.columns) && asList(sec.table.rows).length
                ? sec.table : null;
            m.rows = rowCount(m.table, c.queries);
            m.opinion = opinionOf(sec);
            m.badges = [];
            m.live = "";
            m.drift = false;
            asList(sec && sec.badges).forEach(function (b) {
                b = String(b);
                if (/^현재값/.test(b)) {
                    var hm = clockOf(c.endAt || c.at, true);
                    m.live = hm ? fill(TEXT.liveBadge, { hm: hm }) : b;
                    m.drift = liveDrift(b, c.endAt || c.at, o.alarmTime);
                } else {
                    m.badges.push(b);
                }
            });
            m.conflict = !!(m.opinion && m.opinion.status === "ok" && conflictsWithSynthesis(sec, top));
            var notes = [];
            var C = TEXT.caution;
            if (m.opinion && m.opinion.status === "ok") notes.push(C.c1, C.c2);
            if (m.conflict) notes.push(C.c3);
            if (m.live) notes.push(C.c4 + (m.drift ? " " + C.c4drift : ""));
            if (m.opinion && m.opinion.status === "none") notes.push(C.c5);
            if (m.opinion && m.opinion.status === "ok") notes.push(C.c6, C.c7);
            if (m.opinion && m.opinion.status === "pending") notes.push(C.c8);
            m.notes = notes;
            return m;
        });
        var holmes = rounds.map(function (r, i) {
            return { no: i + 1, title: fill(TEXT.holmesRound, { n: i + 1 }), time: clockOf(r.at), note: r.note,
                queries: r.queries,
                notes: r.note ? [TEXT.caution.c1, TEXT.caution.c2, TEXT.caution.c7, TEXT.caution.holmes] : [] };
        });
        return { cards: models, holmes: holmes, sofar: sofarLine(models, o.done, top, o.synthesis) };
    }

    // 건너뜀 섹션 사유 원문 → 카드 문장(조건식·예산 원문은 감춘다)
    function skipReason(reason) {
        var text = String(reason || "");
        if (/^when_false\b/.test(text)) return TEXT.skipWhenFalse;
        if (/^budget\b/.test(text)) return TEXT.skipBudget;
        return text;
    }

    // 단계 결과 행 수 — 섹션 표 행 수, 없으면 도구 끝 행의 `rows` 합(모르면 0)
    function rowCount(table, queries) {
        if (table) return asList(table.rows).length;
        return asList(queries).reduce(function (n, q) { return n + (parseInt(q.rows, 10) || 0); }, 0);
    }

    /** 「지금까지」 줄(결정적) — 종결이면 종합 1위 가설 + 서술 첫 문장, 진행 중이면 결과 고리 최대 4 + 확인 중. */
    function sofarLine(cards, done, top, synthesis) {
        if (done && top) {
            var head = fill(TEXT.sofarFinal, { title: top.title || "", status: top.status_label || top.status || "" });
            var lead = isObj(synthesis) && synthesis.narrative ? firstSentence(synthesis.narrative) : "";
            return lead ? head + " · " + lead : head;
        }
        // 고리 규칙: ① 이상 판정(`degraded`) 단계 ② 판정이 없는 성공 단계 중 결과 행이 1개 이상인 것만.
        // 정상 판정·건너뜀·결손(경고 상태)·0행(「판정 없음 · 결과 0건」)은 뺀다. 걸러진 뒤 최근 4고리.
        var links = cards.filter(function (c) {
            if (c.skipped || c.running || c.warn || !c.finding) return false;
            if (c.verdict === "degraded") return true;
            return !c.verdict && c.rows > 0;
        }).map(function (c) { return corePhrase(c.finding); }).slice(-4);
        var run = cards.filter(function (c) { return c.running; })[0];
        if (run && !done) links.push(fill(TEXT.sofarChecking, { title: run.title }));
        return links.length ? links.join(" → ") : "";
    }

    // ─── 컨트롤러 ───────────────────────────────────────────────────────

    /**
     * opts:
     *   root(뷰 본문 요소) · tabButton(뷰 탭 버튼) · badge(뷰 탭 배지) · operator(관리자 화면 여부)
     *   headers() — fetch 인증 헤더(EventSource는 쿠키로 인증한다)
     *   onActivate() — 장애 진단 뷰 탭으로 전환
     *   onOpenConversation(id) — 종결 화면 [조사 대화로 열기](기존 조사 대화 진입 함수)
     *   analyzeFor(id, view) — 공용 렌더 analyze 옵션(기존 [다시 분석]) 또는 null
     *   startInvestigation(alarmId) — (W18) 「이벤트 찾아 조사」 접수 → Promise<{ok,status,body}>(없으면 칸 숨김)
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
        var searchOn = false;       // (W18) /active `event_search` — 알람 다시 분석 접수가 켜졌는지

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
            var search = buildSearch();
            listPanel.appendChild(search.box);
            listPanel.appendChild(listNote);
            listPanel.appendChild(table);
            panels.appendChild(listPanel);
            r.appendChild(panels);

            dom = { conn: connEl, strip: strip, listBtn: listBtn, menuBtn: menuBtn, menu: menu,
                panels: panels, listPanel: listPanel, listNote: listNote, tbody: tbody, search: search };
        }

        // ── (plans/187 W18) 이벤트 찾아 조사 ──
        // 검색은 판정 기록 사용자 경로(GET)만 묻는다. 조사 접수(쓰기)는 이 파일에 두지 않고 app.js가 넘긴
        // `startInvestigation(alarmId)`(기존 「분석 요청」과 같은 접수 라우트 — 존 검사·상한·합류)로 한다.
        function buildSearch() {
            var box = el("section", "dv-search");
            box.hidden = true;
            box.appendChild(el("h3", null, TEXT.searchTitle));
            var form = el("div", "dv-search-form");
            function field(label, type, cls) {
                var wrap = el("label", "dv-search-field");
                wrap.appendChild(el("span", null, label));
                var input = el("input", cls);
                input.type = type;
                if (type === "text") input.maxLength = 200;
                input.addEventListener("keydown", function (e) {
                    if (e && e.key === "Enter") { if (e.preventDefault) e.preventDefault(); runSearch(); }
                });
                wrap.appendChild(input);
                form.appendChild(wrap);
                return input;
            }
            var since = field(TEXT.searchSince, "datetime-local", "dv-search-since");
            var until = field(TEXT.searchUntil, "datetime-local", "dv-search-until");
            var alarm = field(TEXT.searchAlarm, "text", "dv-search-alarm");
            var server = field(TEXT.searchServer, "text", "dv-search-server");
            var btn = el("button", "dv-search-btn", TEXT.searchBtn);
            btn.type = "button";
            btn.addEventListener("click", function () { runSearch(); });
            form.appendChild(btn);
            box.appendChild(form);
            box.appendChild(el("div", "dv-note", TEXT.searchHint));
            var status = el("div", "dv-search-status");
            status.setAttribute("role", "status");
            box.appendChild(status);
            var table = el("table", "dv-search-list");
            var hr = el("tr");
            TEXT.searchHead.forEach(function (h) { hr.appendChild(el("th", null, h)); });
            var thead = el("thead");
            thead.appendChild(hr);
            table.appendChild(thead);
            var body = el("tbody");
            table.appendChild(body);
            box.appendChild(table);
            return { box: box, since: since, until: until, alarm: alarm, server: server, btn: btn,
                status: status, body: body, seq: 0 };
        }

        function showSearch(on) {
            if (dom) dom.search.box.hidden = !(on && opts.startInvestigation);
        }

        function runSearch() {
            var s = dom && dom.search;
            if (!s) return Promise.resolve();
            var qs = searchQuery({ since: s.since.value, until: s.until.value, alarm: s.alarm.value,
                server: s.server.value });
            s.body.textContent = "";
            if (qs === null) { s.status.textContent = TEXT.searchBadRange; return Promise.resolve(); }
            var mine = ++s.seq;   // 늦게 온 이전 응답이 새 결과를 덮지 않게
            s.status.textContent = TEXT.searching;
            return getJSON(DECISIONS_API + "?" + qs).then(function (res) {
                if (mine !== s.seq) return;
                if (!res.ok) { s.status.textContent = fill(TEXT.searchFailed, { reason: reasonOf(res) }); return; }
                var found = asList(res.body.items).filter(isObj);
                s.status.textContent = found.length
                    ? fill(TEXT.searchCount, { total: res.body.total || found.length, n: found.length })
                    : TEXT.searchEmpty;
                found.forEach(function (item) { s.body.appendChild(searchRow(item)); });
            }, function (err) {
                if (mine === s.seq) s.status.textContent = fill(TEXT.searchFailed, { reason: (err && err.message) || String(err) });
            });
        }

        function searchRow(item) {
            var tr = el("tr", "dv-search-row");
            var tier = item.tier ? (TIER_LABELS[item.tier] || String(item.tier)) : "";
            [fmtStamp(item.ts), item.server_name || item.hostname || "-", item.alarm_name || "-",
                [tier, item.stage_label].filter(Boolean).join(" · ")].forEach(function (c) {
                tr.appendChild(el("td", null, c));
            });
            var cell = el("td", "dv-search-act");
            var note = el("div", "dv-search-note");
            var alarmId = String(item.alarm_id || "");
            if (!alarmId) {
                note.textContent = TEXT.noAlarmId;
            } else {
                var btn = el("button", "dv-start-btn", TEXT.startBtn);
                btn.type = "button";
                btn.addEventListener("click", function () { startFor(alarmId, btn, note); });
                cell.appendChild(btn);
            }
            cell.appendChild(note);
            tr.appendChild(cell);
            return tr;
        }

        function startFor(alarmId, btn, note) {
            btn.disabled = true;
            btn.textContent = TEXT.starting;
            note.textContent = "";
            return Promise.resolve()
                .then(function () { return opts.startInvestigation(alarmId); })
                .then(function (res) {
                    var body = (res && res.body) || {};
                    btn.disabled = false;
                    btn.textContent = TEXT.startBtn;
                    if (!res || !res.ok) { note.textContent = fill(TEXT.startFailed, { reason: reasonOf(res) }); return; }
                    if (body.status === "rejected") {
                        note.textContent = fill(TEXT.startRejected, { reason: body.reason || "-" });
                        return;
                    }
                    if (!body.investigation_id) { note.textContent = fill(TEXT.startFailed, { reason: "조사 id 없음" }); return; }
                    if (body.joined) note.textContent = TEXT.startJoined;
                    open(String(body.investigation_id), { alarmId: alarmId });
                }, function (err) {
                    btn.disabled = false;
                    btn.textContent = TEXT.startBtn;
                    note.textContent = fill(TEXT.startFailed, { reason: (err && err.message) || String(err) });
                });
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
                alarm: null, why: [], stepPurpose: {},
                log: { rows: [], seen: {}, clipped: false, tools: 0 },
                filter: { playbook: true, holmes: true, system: true, ai: true },
                autoScroll: true, expanded: {}, finalPanel: null, dom: null,
                // (W20) 조사 흐름 — 같은 이벤트를 두 보기로 그린다(연결·커서는 하나)
                mode: "flow", flow: { events: [], seen: {} }, flowOpen: {},
            };
        }

        function tabLabel(tab) {
            var v = tab.view || {};
            // (plans/187 W17) 알람 조사는 「서버 · 알람명」, 채팅·요청 조사는 대상 서버
            var a = tab.alarm;
            if (isObj(a) && (a.alarm_name || a.server_name || a.hostname)) {
                return [a.server_name || a.hostname, a.alarm_name].filter(Boolean).join(" · ");
            }
            var sub = isObj(v.subject) ? (v.subject.server_name || v.subject.hostname) : "";
            return textOf(v.subject) || sub || tab.subject || tab.id;
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
            var target = el("dl", "dv-target");
            var meta = el("div", "dv-tab-meta");
            var warn = el("div", "dv-note dv-note--warn");
            warn.hidden = true;
            head.appendChild(title);
            head.appendChild(target);
            head.appendChild(meta);
            head.appendChild(warn);
            panel.appendChild(head);

            // (W20 · G-50) 「조사 흐름」·「실행 기록」 전환 — 실행 기록(dv-grid)은 종전 화면 그대로 숨김만 바꾼다
            var modes = el("div", "dv-modes");
            modes.setAttribute("role", "tablist");
            var modeBtns = {};
            [["flow", TEXT.modeFlow], ["log", TEXT.modeLog]].forEach(function (m) {
                var b = el("button", "dv-mode", m[1]);
                b.type = "button";
                b.setAttribute("role", "tab");
                b.addEventListener("click", function () { setMode(tab, m[0]); });
                modes.appendChild(b);
                modeBtns[m[0]] = b;
            });
            panel.appendChild(modes);
            var flowBox = el("section", "dv-flow");
            var sofar = el("div", "dv-sofar");
            sofar.appendChild(el("span", "dv-label", TEXT.sofarHead));
            var sofarText = el("span", "dv-sofar-text");
            sofar.appendChild(sofarText);
            flowBox.appendChild(sofar);
            var flowBody = el("div", "dv-flow-body");
            flowBox.appendChild(flowBody);
            panel.appendChild(flowBox);

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

            tab.dom = { wrap: wrap, btn: btn, label: label, state: state, panel: panel, title: title, target: target, meta: meta,
                warn: warn, filters: filters, logNote: logNote, rows: rows, partBox: partBox, partBody: partBody,
                finalBox: finalBox, finalBody: finalBody, grid: grid, modeBtns: modeBtns, flowBox: flowBox,
                sofar: sofar, sofarText: sofarText, flowBody: flowBody };
            setMode(tab, tab.mode);
            renderTabButton(tab);
        }

        // ── (W20) 조사 흐름 ──
        function setMode(tab, mode) {
            tab.mode = mode === "log" ? "log" : "flow";
            if (!tab.dom) return;
            tab.dom.grid.hidden = tab.mode !== "log";
            tab.dom.flowBox.hidden = tab.mode !== "flow";
            Object.keys(tab.dom.modeBtns).forEach(function (k) {
                var on = k === tab.mode;
                tab.dom.modeBtns[k].className = "dv-mode" + (on ? " active" : "");
                tab.dom.modeBtns[k].setAttribute("aria-selected", on ? "true" : "false");
            });
        }

        function keepFlowEvents(tab, events) {
            var f = tab.flow;
            asList(events).forEach(function (e) {
                if (!isObj(e)) return;
                var seq = Number(e.seq) || 0;
                if (seq && f.seen[seq]) return;
                if (seq) f.seen[seq] = true;
                f.events.push(e);
            });
            if (f.events.length > ROW_CAP) {
                f.events.sort(function (a, b) { return (a.seq || 0) - (b.seq || 0); });
                f.events.splice(0, f.events.length - ROW_CAP);
            }
        }

        // 단계 섹션 — 종결 브리핑이 있으면 그것(해석·판정 최종), 없으면 진행 중 partial_briefing
        function flowSource(tab) {
            var v = tab.view || {};
            var fin = isObj(v.briefing) && isObj(v.briefing.playbook) ? v.briefing.playbook : null;
            if (tab.done && fin) return { sections: fin.sections, synthesis: fin.synthesis };
            var part = isObj(tab.partial) && isObj(tab.partial.playbook) ? tab.partial.playbook : null;
            return { sections: part ? part.sections : [], synthesis: null };
        }

        function toggler(tab, key, closedText, openText, target) {
            var btn = el("button", "dv-flow-toggle", tab.flowOpen[key] ? openText : closedText);
            btn.type = "button";
            btn.setAttribute("aria-expanded", tab.flowOpen[key] ? "true" : "false");
            target.hidden = !tab.flowOpen[key];
            btn.addEventListener("click", function () {
                var on = !tab.flowOpen[key];
                tab.flowOpen[key] = on;
                target.hidden = !on;
                btn.textContent = on ? openText : closedText;
                btn.setAttribute("aria-expanded", on ? "true" : "false");
            });
            return btn;
        }

        function tableNode(table) {
            var t = el("table", "dv-card-table");
            var cols = table.columns.map(String);
            var hr = el("tr");
            cols.forEach(function (c) { hr.appendChild(el("th", null, c)); });
            var thead = el("thead");
            thead.appendChild(hr);
            t.appendChild(thead);
            var body = el("tbody");
            asList(table.rows).forEach(function (row) {
                if (isObj(row)) row = cols.map(function (c) { return row[c]; });
                if (!Array.isArray(row)) return;
                var tr = el("tr");
                row.forEach(function (v) {
                    var text = v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
                    tr.appendChild(el("td", null, text));
                });
                body.appendChild(tr);
            });
            t.appendChild(body);
            return t;
        }

        function cautionNode(tab, key, notes, parent) {
            if (!notes.length) return;
            var ul = el("ul", "dv-caution");
            notes.forEach(function (n) { ul.appendChild(el("li", null, n)); });
            parent.appendChild(toggler(tab, key, TEXT.cautionBtn, TEXT.cautionBtn, ul));
            parent.appendChild(ul);
        }

        function line(dl, head, node) {
            dl.appendChild(el("dt", null, head));
            var dd = el("dd");
            dd.appendChild(node);
            dl.appendChild(dd);
            return dd;
        }

        function queryNode(tab, q, key) {
            var div = el("div", "dv-q" + (q.warn ? " dv-q--warn" : ""));
            div.appendChild(el("span", "dv-q-text", q.text));
            var meta = [q.open ? "⟳" : q.icon, q.elapsed, q.rows].filter(Boolean).join(" ");
            if (meta) div.appendChild(el("span", "dv-meta", meta));
            if (q.excerpt) {
                var pre = el("pre", "dv-excerpt", q.excerpt);
                div.appendChild(toggler(tab, key, "▸", "▾", pre));
                div.appendChild(pre);
            }
            return div;
        }

        function cardNode(tab, m) {
            var li = el("li", "dv-card" + (m.skipped ? " dv-card--skip" : "") + (m.running ? " dv-card--running" : "") +
                (m.warn ? " dv-card--warn" : ""));
            li.setAttribute("data-step", m.step);
            var head = el("div", "dv-card-head");
            head.appendChild(el("span", "dv-card-no", m.no));
            if (m.time) head.appendChild(el("span", "dv-time", m.time));
            if (m.skipped) {
                head.appendChild(el("span", "dv-card-title", fill(TEXT.skipTitle, { title: m.title }) + " — " +
                    (m.because || TEXT.skipNoReason)));
                li.appendChild(head);
                return li;
            }
            head.appendChild(el("span", "dv-card-title", m.title));
            var state = m.running ? TEXT.cardRunning : [m.icon, m.elapsed].filter(Boolean).join(" ");
            if (state) head.appendChild(el("span", "dv-card-state", state));
            if (m.verdictLabel) head.appendChild(el("span", "dv-verdict dv-verdict--" + m.verdict, m.verdictLabel));
            if (m.live) head.appendChild(el("span", "dv-live", m.live));
            m.badges.forEach(function (b) { head.appendChild(el("span", "dv-badge", b)); });
            if (m.conflict) head.appendChild(el("span", "dv-conflict", TEXT.conflictChip));
            li.appendChild(head);
            var dl = el("dl", "dv-card-body");
            if (m.because) line(dl, TEXT.cardWhy, el("span", "dv-why", m.because));
            if (m.queries.length) {
                var qs = el("div", "dv-queries");
                m.queries.forEach(function (q, i) { qs.appendChild(queryNode(tab, q, "ex:" + m.step + ":" + i)); });
                line(dl, TEXT.cardQuery, qs);
            }
            if (m.finding || m.reason || m.table) {
                var res = el("div", "dv-result");
                if (m.finding) res.appendChild(el("span", "dv-finding", m.finding));
                else if (m.reason) res.appendChild(el("span", "dv-finding dv-finding--reason", m.reason));
                if (m.table) {
                    var wrap = el("div", "dv-card-table-wrap");
                    wrap.appendChild(tableNode(m.table));
                    res.appendChild(toggler(tab, "table:" + m.step, TEXT.tableOpen, TEXT.tableClose, wrap));
                    res.appendChild(wrap);
                }
                line(dl, TEXT.cardResult, res);
            }
            if (m.opinion) {
                var op = el("div", "dv-opinion dv-opinion--" + m.opinion.status);
                if (m.opinion.status === "ok") {
                    op.appendChild(el("span", "dv-ai-mark", TEXT.opinionMark));
                    op.appendChild(el("span", "dv-opinion-text", m.opinion.text));
                } else if (m.opinion.status === "pending") {
                    op.textContent = TEXT.opinionPending;
                } else {
                    op.textContent = fill(TEXT.opinionNone, { reason: m.opinion.reason || TEXT.opinionNoneReason });
                }
                line(dl, TEXT.cardOpinion, op);
            }
            if (dl.children.length) li.appendChild(dl);
            cautionNode(tab, "note:" + m.step, m.notes, li);
            return li;
        }

        function roundNode(tab, r) {
            var li = el("li", "dv-card dv-card--holmes");
            var head = el("div", "dv-card-head");
            head.appendChild(el("span", "dv-card-no", circled(r.no)));
            if (r.time) head.appendChild(el("span", "dv-time", r.time));
            head.appendChild(el("span", "dv-card-title", r.title));
            li.appendChild(head);
            var dl = el("dl", "dv-card-body");
            var qs = el("div", "dv-queries");
            r.queries.forEach(function (q, i) { qs.appendChild(queryNode(tab, q, "hx:" + r.no + ":" + i)); });
            if (!r.queries.length) qs.appendChild(el("span", "dv-note", TEXT.holmesNoQuery));
            line(dl, TEXT.cardQuery, qs);
            if (r.note) {
                var op = el("div", "dv-opinion dv-opinion--holmes");
                op.appendChild(el("span", "dv-ai-badge", TEXT.holmesJudge));
                op.appendChild(el("span", "dv-opinion-text", r.note));
                line(dl, TEXT.holmesJudge, op);
            }
            li.appendChild(dl);
            cautionNode(tab, "hnote:" + r.no, r.notes, li);
            return li;
        }

        function renderFlow(tab) {
            if (!tab.dom) return;
            var src = flowSource(tab);
            var model = flowModel(tab.flow.events, src.sections, {
                done: tab.done, synthesis: src.synthesis, alarmTime: isObj(tab.alarm) ? tab.alarm.alarm_time : null,
            });
            tab.dom.sofarText.textContent = model.sofar || TEXT.sofarNone;
            var body = tab.dom.flowBody;
            body.textContent = "";
            var groups = [];
            if (model.cards.length) groups.push(["playbook", model.cards.map(function (m) { return cardNode(tab, m); })]);
            if (model.holmes.length) groups.push(["holmes", model.holmes.map(function (r) { return roundNode(tab, r); })]);
            groups.forEach(function (g) {
                // 두 엔진이 함께 돌면 엔진별 줄을 나눈다(parallel)
                if (groups.length > 1) body.appendChild(el("div", "dv-label dv-flow-engine", TEXT.engineGroups[g[0]]));
                var ol = el("ol", "dv-cards");
                g[1].forEach(function (n) { ol.appendChild(n); });
                body.appendChild(ol);
            });
            if (!groups.length) body.appendChild(el("div", "dv-note", TEXT.flowEmpty));
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
            renderTarget(tab);
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

        // (plans/187 W17) 어떤 알람·대상을 왜 조사하는지 — 알람 요약 · 채팅 경로 · 플레이북 선택/HolmesGPT 이유
        function renderTarget(tab) {
            var box = tab.dom.target;
            var v = tab.view || {};
            var lines = targetLines(tab.alarm, v.subject);
            if (!lines.length && (asList(v.alarm_ids).length || v.parent_alarm_id || v.kind === "alarm")) {
                lines.push({ head: TEXT.alarmHead, text: TEXT.alarmUnknown });
            }
            tab.why.forEach(function (w) { lines.push({ head: TEXT.whyHead, text: w.text }); });
            box.textContent = "";
            lines.forEach(function (line) {
                box.appendChild(el("dt", null, line.head));
                box.appendChild(el("dd", null, line.text));
            });
            box.hidden = !lines.length;
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
            var text = el("span", "dv-text", row.text);
            // 도구 행은 그 스텝의 목적을 마우스 올림 설명으로 단다(행마다 되풀이하지 않는다)
            var stepWhy = row.step ? tab.stepPurpose[row.step] : "";
            if (!row.purpose && stepWhy) text.title = TEXT.purposePrefix + stepWhy;
            li.appendChild(text);
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
            if (row.purpose) li.appendChild(el("span", "dv-purpose", TEXT.purposePrefix + row.purpose));
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
            // 조사 이유·스텝 목적은 행 상한(300)으로 오래된 행이 빠져도 머리에 남긴다
            var seen = {};
            tab.why.forEach(function (w) { seen[w.seq] = true; });
            whyLines(act.events).forEach(function (w) { if (!seen[w.seq]) tab.why.push(w); });
            stepPurposes(act.events, tab.stepPurpose);
            keepFlowEvents(tab, act.events);
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
            if (isObj(view.alarm)) tab.alarm = view.alarm;
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
            renderFlow(tab);
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
            renderFlow(tab);
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
                renderFlow(tab);
            } else if (type === "partial") {
                if (isObj(data.partial_briefing)) tab.partial = data.partial_briefing;
                renderPartial(tab);
                renderFlow(tab);
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
                if (res.ok) { firstItems = res.body.items; searchOn = res.body.event_search === true; }
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
            showSearch(searchOn);
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
            search: runSearch,
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
        whyLines: whyLines,
        searchQuery: searchQuery,
        stepPurposes: stepPurposes,
        targetLines: targetLines,
        conversationHeadLines: conversationHeadLines,
        flowModel: flowModel,
        opinionOf: opinionOf,
        conflictsWithSynthesis: conflictsWithSynthesis,
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
