/**
 * 자동 조사 브리핑 공용 렌더 (plans/153 W3 · R-1 · D-325).
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
 * node로 단위 테스트한다(`tests/test_api/test_plan153_w3_briefing_view.py`).
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

    // ── plans/187 W5b: 플레이북 브리핑(요약형·상세형 · 엔진 비교) ─────────────────────────
    // `briefing.playbook`(종결 뒤) 또는 `partial_briefing.playbook`(종결 전 · W5a)이 있을 때만 이 갈래로
    // 그린다. 없으면 renderBody는 종전 경로 그대로다(비트 동일). 원칙(§5.6 · §5.0.1 6):
    //   - 판정 칩(게이트웨이·판독 규칙)은 공통 칸에 한 번만 — 엔진 선택과 무관하다(D-035).
    //   - 근거(도구·수치·근거 단계)를 서술보다 먼저 보이고, LLM 서술에는 「AI 소견」 배지를 단다.
    //   - 가드가 버린 소견·서술은 사유를 보인다(침묵 금지). 외부 차트 라이브러리 없이 인라인 SVG만 쓴다.

    var PB_TEXT = {
        playbook: "플레이북",
        summaryHead: "종합(플레이북)",
        topHead: "1순위",
        actionHead: "대응(제안)",
        followupHead: "추가 확인",
        hypoHead: "원인 가설 순위",
        knownHead: "알려진 문제",
        runbookHead: "런북",
        stepsHead: "단계",
        stepsCommon: "단계 판정 — 공통(엔진 선택과 무관)",
        stepsDetailHead: "단계별 근거",
        refsHead: "참고 · 판정 무관",
        notesHead: "메모",
        targetHead: "대상",
        progress: "진행 중 S{done}/{total}",
        synthesisPending: "종합은 모든 단계가 끝난 뒤 나옵니다.",
        noHypothesis: "가설을 세우지 못했습니다.",
        openConversation: "조사 대화로 열기",
        openBusy: "답변이 끝난 뒤 열 수 있습니다",
        aiBadge: "AI 소견",
        opinionDropped: "AI 소견을 싣지 않았습니다 — {reason}",
        narrativeDropped: "AI 서술을 싣지 않았습니다 — {reason}",
        tools: "사용 도구",
        reason: "사유",
        partialReason: "일부 결손",
        stepPending: "수집 중",
        stepWaiting: "대기",
        stale: "검토 기한 경과",
        runbookFields: { meaning: "의미", impact: "영향", mitigation: "완화", escalation: "에스컬레이션", owner: "담당", last_reviewed: "마지막 검토", references: "참고" },
        knownResult: { match: ["ok", "일치"], candidate: ["warn", "후보"], inconclusive: ["hold", "판단 보류"] },
        knownFields: { satisfied: "충족 조건", missing_steps: "확인 못 한 단계", workaround: "우회" },
        actionMeta: { risk: "위험도", verification: "검증", rollback: "롤백" },
        evidence: "근거 단계",
        sourceBadges: { apm_gateway: "게이트웨이 판정", playbook_rule: "판독 규칙" },
        engineHead: "엔진 비교",
        engineApprox: "대략적 대조",
        enginePlaybook: "플레이북(결정적 판정)",
        engineHolmes: "심층 조사(HolmesGPT · 가설)",
        engineRows: { cause: "1순위 원인", basis: "근거", action: "대응", open: "미결" },
        engineNarrative: "서술 보기",
        engineToolCalls: "도구 호출 {n}회",
        engineLayers: "계층",
        engineRunning: "심층 조사가 진행 중입니다 — 끝나면 이 칸이 채워집니다.",
        choiceHead: "이 결론이 맞다",
        choiceNote: "선택은 판정·티어·통보를 바꾸지 않습니다 — 조사 지식 통계와 KB 초안의 기본값에만 쓰입니다.",
        choiceOptions: [["playbook", "플레이북"], ["holmesgpt", "HolmesGPT"], ["both", "둘 다"], ["neither", "둘 다 아님"]],
        choiceDone: "기록했습니다 — {label}",
        choiceOff: "엔진 선택 기록이 꺼져 있어 고를 수 없습니다(관리자 설정).",
        choiceFailed: "선택을 기록하지 못했습니다 — {reason}",
        // plans/187 FE-2 — W8 알려진 문제 3버튼 · W9 꼬리 · W10 추가 조사 · W11 이 알람 분석
        keExtra: { issue: "증상", cause: "원인", unsatisfied: "미충족 조건", unknown: "확인 못 한 조건", permanent_fix: "영구 해결", owner: "담당", last_reviewed: "마지막 검토" },
        kePinned: "가설 1순위 고정",
        keRecurrence: "최근 30일 일치 {n}회",
        keReviewOnly: "검토 전용",
        keFeedbackHead: "이 알려진 문제가 맞나요?",
        keFeedbackOptions: [["correct", "맞음"], ["incorrect", "아님"], ["workaround_ineffective", "우회 효과 없음"]],
        keFeedbackNote: "피드백은 판정·가설 순위를 바꾸지 않습니다 — 「아님」이 이어지면 관리자 재검토 대상에 오릅니다.",
        keFeedbackDone: "기록했습니다 — {label}",
        keFeedbackFailed: "피드백을 기록하지 못했습니다 — {reason}",
        kbOff: "조사 지식(KB) 기능이 꺼져 있어 쓸 수 없습니다(관리자 설정).",
        tailHead: "지식 남기기",
        kbDraft: "KB 초안 만들기",
        kbDraftNote: "초안은 검토 전(wip) 상태로만 남습니다 — 저장소 반영은 관리자가 검토해 PR로 합니다.",
        kbDraftDone: "KB 초안을 만들었습니다 — {id}",
        kbDraftFailed: "KB 초안을 만들지 못했습니다 — {reason}",
        kbYaml: "초안 YAML",
        postmortem: "포스트모템 내보내기",
        pmDone: "포스트모템 Markdown을 만들었습니다 — 내려받거나 복사하세요.",
        pmFailed: "포스트모템을 내보내지 못했습니다 — {reason}",
        pmMarkdown: "포스트모템 Markdown",
        pmDownload: "내려받기(.md)",
        copy: "복사",
        copied: "복사했습니다.",
        copyFailed: "복사하지 못했습니다 — 내용을 직접 선택해 복사하세요.",
        analyzeNew: "분석 요청",
        analyzeAgain: "다시 분석",
        analyzeNote: "사용자 요청 조사는 통보(메신저·문자)로 나가지 않습니다 — 결과는 이 화면과 조사 대화에서만 봅니다.",
        analyzePrevious: "이전 조사 {n}건",
        analyzeSubmitted: "분석을 요청했습니다 — {id}. 끝난 단계부터 채웁니다.",
        analyzeJoined: "같은 알람의 진행 중(또는 최근) 요청 조사에 합류했습니다 — {id}",
        analyzeRefFallback: "원 알람 발생 시각이 기록되지 않아 판정 시각을 기준으로 조사합니다.",
        analyzeRejected: "조사 서비스가 요청을 거부했습니다 — {reason}",
        analyzeFailed: "분석을 요청하지 못했습니다 — {reason}",
        analyzeOff: "알람 다시 분석 기능이 꺼져 있습니다(관리자 설정).",
        analyzeOpenNew: "새 조사로 조사 대화 열기",
        kindLabels: { alarm: "자동 조사", manual: "요청 분석", episode: "사건 종합 조사", diagnosis: "추가 조사" },
        followupHead: "추가 조사 요청",
        followupFocus: "조사 초점",
        followupNote: "보충 한 줄(선택)",
        followupPlaceholder: "예) 배치 작업과 겹치는지 — 계정·키 등 민감정보 입력 금지",
        followupSubmit: "추가 조사 요청",
        followupNotice: "결과는 이 카드 아래에만 쌓이고 통보되지 않습니다. 권한·상한(진행 중 1건 · 시간당 횟수)을 넘으면 사유와 함께 거절됩니다.",
        followupNeedFocus: "조사 초점을 고르세요.",
        followupSubmitted: "추가 조사를 접수했습니다 — {id}",
        followupReused: "같은 초점의 추가 조사가 이미 있어 그 조사를 보여 줍니다 — {id}",
        followupFailed: "추가 조사를 요청하지 못했습니다 — {reason}",
        followupListFailed: "추가 조사 목록을 불러오지 못했습니다 — {reason}",
        followupEmpty: "아직 추가 조사가 없습니다.",
        followupItemHead: "초점",
    };

    // 단계 칩 — [종류(CSS 접미사), 문구]
    var STEP_CHIPS = {
        degraded: ["bad", "이상"],
        normal: ["ok", "정상"],
        hold: ["hold", "판단 보류"],
        missing: ["missing", "결손"],
        skipped: ["skip", "생략"],
        not_connected: ["skip", "미연동"],
        no_was: ["skip", "WAS 아님"],
        info: ["none", "참고"],
        pending: ["pending", "수집 중"],
    };

    // R-C 지배 계층(readings.dominant) → S3 칩 문구
    var DOMINANT_LABELS = { sql: "SQL", fetch: "Fetch", external: "외부", cpu: "CPU", network: "네트워크", other: "혼재" };

    var HYPO_KINDS = { "지지": "ok", "판단 보류": "hold", "반박": "bad" };

    var DEEP_STATUS = {
        running: ["running", "진행 중"],
        pending: ["running", "진행 중"],
        done: ["done", "완료"],
        skipped: ["none", "생략"],
        timeout: ["fail", "시간 초과"],
        failed: ["fail", "실패"],
    };

    var AGREEMENT = {
        same_layer: ["ok", "같은 계층"],
        different_layer: ["warn", "다른 계층"],
        not_comparable: ["none", "비교 불가"],
    };

    // 표 열 이름 → 화면 문구(모르는 열은 원문)
    var COLUMN_LABELS = {
        instance: "인스턴스", tps: "TPS", p95_ms: "p95(ms)", verdict: "판정", application: "서비스",
        response_time_ms: "응답(ms)", sql_ms: "SQL(ms)", fetch_ms: "Fetch(ms)", external_ms: "외부(ms)",
        cpu_ms: "CPU(ms)", network_ms: "네트워크(ms)", txid: "txid", kind: "종류", peak: "최고",
        baseline_mean: "기준선 평균", z_score: "z", is_anomalous: "이상", anomaly_kind: "이상 유형",
        t_offset_min: "알람 기준", detail: "내용", metric: "지표", incident: "사건창", min: "최소", max: "최대",
        last: "마지막", note: "비고", source: "출처", at: "시각", minutes_before_alarm: "알람 기준",
        description: "내용", name: "프로세스", pid: "PID", user: "사용자", pcpu: "CPU%", pmem: "MEM%",
    };
    var VERDICT_CELLS = { degraded: "이상", normal: "정상", no_was: "WAS 아님" };

    // S3 가로 누적 막대 — 응답시간을 SQL(+Fetch) · 외부 호출 · CPU · 기타로 나눈다
    var BAR_PARTS = [
        ["sql", ["sql_ms", "fetch_ms"], "SQL"],
        ["ext", ["external_ms"], "외부 호출"],
        ["cpu", ["cpu_ms"], "CPU"],
    ];

    function isObj(value) { return !!value && typeof value === "object" && !Array.isArray(value); }
    function asList(value) { return Array.isArray(value) ? value : []; }
    function num(value) {
        if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
        var n = Number(value);
        return isFinite(n) ? n : null;
    }
    function fmtNum(n) {
        return Math.abs(n) >= 100 || n === Math.round(n) ? String(Math.round(n)) : String(Math.round(n * 100) / 100);
    }
    // 문자열·{title|text|label} 객체 → 한 줄 문자열
    function textOf(value) {
        if (value === null || value === undefined) return "";
        if (typeof value !== "object") return String(value);
        return String(value.title || value.text || value.action || value.label || value.summary || value.cause || "");
    }

    // 조사 응답에서 플레이북 브리핑을 찾는다 — 없으면 null(종전 렌더).
    function playbookContext(view) {
        var b = view && view.briefing;
        var partial = false;
        if (!(isObj(b) && isObj(b.playbook))) {
            b = view && view.partial_briefing;
            if (!(isObj(b) && isObj(b.playbook))) return null;
            partial = b.partial === true;
        }
        return { b: b, pb: b.playbook, partial: partial, progress: isObj(view.progress) ? view.progress : null };
    }

    // 표시 단계 — 종합(S9 synthesize)은 「종합」 칸이 맡는다
    function stepSections(pb) {
        return asList(pb.sections).filter(function (s) {
            return isObj(s) && s.action !== "synthesize" && s.status !== "pending_synthesis";
        });
    }

    function stepChip(sec, readings) {
        var key;
        var reason = String(sec.reason || "");
        if (sec.status === "missing") key = "missing";
        else if (sec.status === "skipped") key = /not_connected|미연동/.test(reason) ? "not_connected" : "skipped";
        else if (sec.verdict === "degraded") key = "degraded";
        else if (sec.verdict === "normal") key = "normal";
        else if (sec.verdict === "no_was") key = "no_was";
        else if (!sec.verdict_source) key = "info";   // 판정을 내지 않는 단계(타임라인·실시간 단면·변경)
        else key = "hold";
        var hit = STEP_CHIPS[key];
        var text = hit[1];
        var dom = readings && readings.dominant;
        if (sec.action === "apm.slow_tx_breakdown" && sec.status === "ok" && DOMINANT_LABELS[dom]) {
            text = DOMINANT_LABELS[dom];
        }
        return { kind: hit[0], text: text };
    }

    function readingsOf(pb) {
        var r = pb.readings;
        if (!isObj(r)) return {};
        return isObj(r.values) ? r.values : r;   // 평탄 `{이름: enum}`(dispatcher) · 원 `{values}` 둘 다 받는다
    }

    function progressOf(ctx) {
        var p = ctx.progress;
        if (p && num(p.total)) return { done: num(p.done) || 0, total: num(p.total), current: p.current_step || "" };
        return null;
    }

    function chipHtml(kind, text, title) {
        return '<span class="bv-chip bv-chip--' + kind + '"' + (title ? ' title="' + escapeHtml(title) + '"' : "") + '>' +
            escapeHtml(text) + '</span>';
    }

    function stepChipsLine(ctx) {
        var readings = readingsOf(ctx.pb);
        var secs = stepSections(ctx.pb);
        var chips = secs.map(function (sec) {
            var c = stepChip(sec, readings);
            return '<span class="bv-step-chip bv-step-chip--' + c.kind + '" title="' +
                escapeHtml((sec.title || sec.action || "") + " · " + c.text) + '">' +
                escapeHtml(sec.step || "?") + ' ' + escapeHtml(c.text) + '</span>';
        });
        var prog = progressOf(ctx);
        if (prog && prog.total > secs.length + 1) {
            // 남은 단계(S9 종합 제외) — 진행 중 단계는 수집 중, 나머지는 대기
            var remain = prog.total - 1 - secs.length;
            for (var i = 0; i < remain; i++) {
                var label = i === 0 && prog.current ? String(prog.current) : "…";
                chips.push('<span class="bv-step-chip bv-step-chip--pending">' + escapeHtml(label) + ' ' +
                    escapeHtml(i === 0 ? PB_TEXT.stepPending : PB_TEXT.stepWaiting) + '</span>');
            }
        }
        return chips.join("");
    }

    function engineChip(b) {
        var dd = isObj(b.deep_dive) ? b.deep_dive : null;
        var ea = isObj(b.engine_agreement) ? b.engine_agreement : null;
        if (!dd && !ea) return null;
        if (dd && (dd.status === "running" || dd.status === "pending")) return { kind: "running", text: "HolmesGPT: 진행 중" };
        if (ea && AGREEMENT[ea.result]) return { kind: AGREEMENT[ea.result][0], text: "HolmesGPT: " + AGREEMENT[ea.result][1] };
        var hit = dd && DEEP_STATUS[dd.status];
        if (hit) return { kind: hit[0], text: "HolmesGPT: " + hit[1] };
        return null;
    }

    function hypoBadge(h) {
        var label = String(h.status_label || h.status || "");
        if (!label) return "";
        return '<span class="bv-hypo bv-hypo--' + (HYPO_KINDS[label] || "hold") + '">' + escapeHtml(label) + '</span>';
    }

    function synthesisOf(pb) { return isObj(pb.synthesis) ? pb.synthesis : {}; }

    function playbookMeta(ctx) {
        var pb = ctx.pb;
        var out = '<span class="bv-src">' + escapeHtml(PB_TEXT.playbook + " " + (pb.id || "")) +
            (pb.version ? " v" + escapeHtml(pb.version) : "") + '</span>';
        var prog = progressOf(ctx);
        if (prog && (ctx.partial || prog.done < prog.total)) {
            out += chipHtml("running", fill(PB_TEXT.progress, { done: prog.done, total: prog.total }));
        }
        var ec = engineChip(ctx.b);
        if (ec) out += chipHtml(ec.kind, ec.text);
        return out;
    }

    function knownErrorsOf(b) {
        var list = asList(b.known_errors);
        if (!list.length && isObj(b.playbook)) list = asList(b.playbook.known_errors);
        return list.filter(isObj);
    }
    function runbookOf(b) {
        if (isObj(b.runbook)) return b.runbook;
        return isObj(b.playbook) && isObj(b.playbook.runbook) ? b.playbook.runbook : null;
    }
    function referencesOf(b) {
        var refs = asList(b.references);
        if (!refs.length && isObj(b.playbook)) refs = asList(b.playbook.references);
        return refs.filter(isObj);
    }

    function knownBadge(ke) {
        var hit = PB_TEXT.knownResult[ke.result] || ["hold", String(ke.result || "")];
        return chipHtml(hit[0], hit[1]);
    }

    // 요약형 — 알람 카드·관제 드로어(§5.6 ①)
    function renderPlaybookSummary(view, ctx, extra) {
        var syn = synthesisOf(ctx.pb);
        var parts = ['<div class="bv-pb-meta">' + playbookMeta(ctx) + '</div>'];
        var hyps = asList(syn.hypotheses).filter(isObj);
        var acts = asList(syn.actions).filter(isObj);
        var lines = [];
        if (hyps.length) {
            lines.push('<div class="bv-pb-line"><span class="bv-pb-key">' + escapeHtml(PB_TEXT.topHead) + '</span> ' +
                escapeHtml(hyps[0].title || "") + ' ' + hypoBadge(hyps[0]) + '</div>');
        }
        if (acts.length) {
            lines.push('<div class="bv-pb-line"><span class="bv-pb-key">' + escapeHtml(PB_TEXT.actionHead) + '</span> ' +
                acts.slice(0, 2).map(function (a) { return escapeHtml(a.text || ""); }).join(" → ") + '</div>');
        }
        var fups = asList(syn.followups);
        if (fups.length) {
            lines.push('<div class="bv-pb-line"><span class="bv-pb-key">' + escapeHtml(PB_TEXT.followupHead) + '</span> ' +
                escapeHtml(textOf(fups[0])) + '</div>');
        }
        parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.summaryHead) + '</div>' +
            (lines.length ? lines.join("")
                : '<div class="bv-note">' + escapeHtml(ctx.partial ? PB_TEXT.synthesisPending : PB_TEXT.noHypothesis) + '</div>') +
            '</div>');
        var kes = knownErrorsOf(ctx.b).filter(function (ke) { return ke.result === "match" || ke.result === "candidate"; });
        if (kes.length) {
            parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.knownHead) + '</div>' +
                kes.slice(0, 2).map(function (ke) {
                    return '<div class="bv-pb-line">' + knownBadge(ke) + ' <span class="bv-mono">' + escapeHtml(ke.id || "") +
                        '</span> ' + escapeHtml(ke.title || "") + '</div>';
                }).join("") + '</div>');
        }
        parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.stepsHead) + '</div>' +
            '<div class="bv-steps">' + stepChipsLine(ctx) + '</div></div>');
        if (extra.openConversation && view.investigation_id) {
            var busy = typeof extra.busy === "function" ? !!extra.busy() : !!extra.busy;
            parts.push('<div class="bv-pb-actions"><button type="button" class="bv-open-conv" data-bv-action="open-conversation"' +
                ' data-investigation-id="' + escapeHtml(view.investigation_id) + '"' +
                (busy ? ' disabled title="' + escapeHtml(PB_TEXT.openBusy) + '"' : "") + '>' +
                escapeHtml(PB_TEXT.openConversation) + '</button></div>');
        }
        return parts.join("");
    }

    // 표 셀 1칸
    function cellHtml(col, value) {
        if (Array.isArray(value) && value.length > 1 && value.every(function (v) { return num(v) !== null; })) {
            return sparkline(value);
        }
        if (value === null || value === undefined || value === "") return "—";
        if (typeof value === "boolean") return value ? "예" : "아니오";
        var n = num(value);
        if ((col === "t_offset_min" || col === "minutes_before_alarm") && n !== null) {
            var m = col === "minutes_before_alarm" ? -n : n;
            if (m === 0) return "알람 시각";
            return escapeHtml("알람 " + fmtNum(Math.abs(m)) + "분 " + (m < 0 ? "전" : "후"));
        }
        if (col === "verdict" && VERDICT_CELLS[value]) return escapeHtml(VERDICT_CELLS[value]);
        if (typeof value === "number" && n !== null) return escapeHtml(fmtNum(n));
        if (typeof value === "object") return escapeHtml(JSON.stringify(value));
        return escapeHtml(value);
    }

    // 인라인 SVG 스파크라인(외부 차트 라이브러리 없음 · 색은 currentColor = 테마 토큰)
    function sparkline(values) {
        var nums = values.map(Number);
        var lo = Math.min.apply(null, nums);
        var hi = Math.max.apply(null, nums);
        var span = hi - lo || 1;
        var w = 80, h = 20;
        var pts = nums.map(function (v, i) {
            var x = (i / (nums.length - 1)) * (w - 2) + 1;
            var y = h - 1 - ((v - lo) / span) * (h - 2);
            return x.toFixed(1) + "," + y.toFixed(1);
        }).join(" ");
        return '<svg class="bv-spark" viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" role="img" aria-label="' +
            escapeHtml("추이 " + nums.length + "점 · 최소 " + fmtNum(lo) + " · 최대 " + fmtNum(hi)) + '">' +
            '<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="' + pts + '"></polyline></svg>';
    }

    function stackedBar(cols, row) {
        var rt = num(row[cols.indexOf("response_time_ms")]);
        if (!rt || rt <= 0) return "—";
        var used = 0;
        var segs = [];
        var title = [];
        BAR_PARTS.forEach(function (part) {
            var v = 0;
            var seen = false;
            part[1].forEach(function (key) {
                var i = cols.indexOf(key);
                var x = i >= 0 ? num(row[i]) : null;
                if (x !== null) { v += x; seen = true; }
            });
            if (!seen) return;
            used += v;
            segs.push([part[0], v]);
            title.push(part[2] + " " + fmtNum(v) + "ms");
        });
        segs.push(["other", Math.max(0, rt - used)]);
        title.push("기타 " + fmtNum(Math.max(0, rt - used)) + "ms");
        var total = Math.max(rt, used);
        return '<div class="bv-bar" title="' + escapeHtml(title.join(" · ")) + '">' +
            segs.map(function (s) {
                var pct = Math.max(0, Math.min(100, (s[1] / total) * 100));
                return '<span class="bv-bar-seg bv-bar-seg--' + s[0] + '" style="width:' + pct.toFixed(1) + '%"></span>';
            }).join("") + '</div>';
    }

    function barLegend() {
        return '<div class="bv-bar-legend">' + BAR_PARTS.concat([["other", [], "기타"]]).map(function (p) {
            return '<span><i class="bv-bar-seg bv-bar-seg--' + p[0] + '"></i>' + escapeHtml(p[2]) + '</span>';
        }).join("") + '</div>';
    }

    function renderPbTable(table) {
        if (!isObj(table) || !Array.isArray(table.columns) || !asList(table.rows).length) return "";
        var cols = table.columns.map(String);
        var withBar = cols.indexOf("response_time_ms") >= 0 && BAR_PARTS.some(function (p) {
            return p[1].some(function (k) { return cols.indexOf(k) >= 0; });
        });
        var vIdx = cols.indexOf("verdict");
        var aIdx = cols.indexOf("is_anomalous");
        var body = asList(table.rows).map(function (row) {
            if (isObj(row)) row = cols.map(function (c) { return row[c]; });
            if (!Array.isArray(row)) return "";
            var bad = (vIdx >= 0 && row[vIdx] === "degraded") || (aIdx >= 0 && row[aIdx] === true);
            return '<tr' + (bad ? ' class="bv-row--bad"' : "") + '>' +
                cols.map(function (c, i) { return '<td>' + cellHtml(c, row[i]) + '</td>'; }).join("") +
                (withBar ? '<td class="bv-bar-cell">' + stackedBar(cols, row) + '</td>' : "") + '</tr>';
        }).join("");
        return '<div class="bv-table-wrap"><table class="bv-cross-table bv-pb-table"><thead><tr>' +
            cols.map(function (c) { return '<th>' + escapeHtml(COLUMN_LABELS[c] || c) + '</th>'; }).join("") +
            (withBar ? '<th>구성</th>' : "") + '</tr></thead><tbody>' + body + '</tbody></table></div>' +
            (withBar ? barLegend() : "");
    }

    function opinionHtml(sec) {
        if (sec.opinion) {
            return '<div class="bv-opinion"><span class="bv-ai">' + escapeHtml(PB_TEXT.aiBadge) + '</span> ' +
                escapeHtml(sec.opinion) + '</div>';
        }
        if (sec.opinion_dropped_reason) {
            return '<div class="bv-note bv-note--warn">' +
                escapeHtml(fill(PB_TEXT.opinionDropped, { reason: sec.opinion_dropped_reason })) + '</div>';
        }
        return "";
    }

    function stepSectionHtml(sec, readings) {
        var c = stepChip(sec, readings);
        var open = c.kind === "bad" || c.kind === "missing";
        var head = '<span class="bv-mono">' + escapeHtml(sec.step || "?") + '</span> ' +
            '<span class="bv-step-title">' + escapeHtml(sec.title || sec.action || "") + '</span> ' +
            chipHtml(c.kind === "bad" ? "fail" : c.kind === "ok" ? "done" : "none", c.text);
        if (PB_TEXT.sourceBadges[sec.verdict_source]) {
            head += ' <span class="bv-src">' + escapeHtml(PB_TEXT.sourceBadges[sec.verdict_source]) + '</span>';
        }
        asList(sec.badges).forEach(function (badge) {
            var live = /^현재값/.test(String(badge));
            head += ' <span class="bv-badge' + (live ? " bv-badge--live" : "") + '">' + escapeHtml(badge) + '</span>';
        });
        var body = [];
        if (sec.reason) {
            // 결손·생략 사유 · 성공 단계의 일부 결손(침묵 금지)
            body.push('<div class="bv-reason' + (sec.status === "missing" ? " bv-reason--fail" : "") + '">' +
                escapeHtml(sec.status === "ok" ? PB_TEXT.partialReason : PB_TEXT.reason) + ': ' + escapeHtml(sec.reason) + '</div>');
        }
        body.push(renderPbTable(sec.table));   // 근거(표)를 소견보다 먼저
        body.push(opinionHtml(sec));
        var tools = asList(sec.tools);
        if (tools.length) {
            body.push('<div class="bv-meta">' + escapeHtml(PB_TEXT.tools) + ': <span class="bv-mono">' +
                tools.map(escapeHtml).join(", ") + '</span></div>');
        }
        return '<details class="bv-step" data-bv-step="' + escapeHtml(sec.step || "") + '" data-bv-key="step-' +
            escapeHtml(sec.step || "") + '"' + (open ? " open" : "") + '><summary>' + head + '</summary>' +
            '<div class="bv-step-body">' + body.join("") + '</div></details>';
    }

    function skeletonSteps(ctx) {
        var prog = progressOf(ctx);
        if (!prog) return "";
        var remain = prog.total - 1 - stepSections(ctx.pb).length;
        var out = "";
        for (var i = 0; i < remain; i++) {
            out += '<div class="bv-step bv-step--skeleton"><span class="bv-mono">' +
                escapeHtml(i === 0 && prog.current ? prog.current : "…") + '</span> ' +
                '<span class="bv-skel"></span> ' + chipHtml("running", i === 0 ? PB_TEXT.stepPending : PB_TEXT.stepWaiting) + '</div>';
        }
        return out;
    }

    function targetsHtml(pb) {
        var sc = isObj(pb.scope) ? pb.scope : {};
        var rows = [];
        if (sc.alarm_instance) rows.push("알람 대상 " + sc.alarm_instance);
        if (sc.host) rows.push("호스트 " + sc.host);
        if (sc.polestar_server) rows.push("폴스타 서버 " + sc.polestar_server + (sc.polestar_source ? " (" + sc.polestar_source + ")" : ""));
        var win = isObj(sc.incident_window) ? sc.incident_window : null;
        if (win && win.start) rows.push("사건창 " + win.start + " ~ " + (win.end || "") + (win.minutes ? " (" + win.minutes + "분)" : ""));
        var out = rows.map(function (r) { return '<span class="bv-meta">' + escapeHtml(r) + '</span>'; }).join(" · ");
        var targets = asList(sc.targets).filter(isObj);
        if (targets.length) {
            out += '<div class="bv-pb-line">' + targets.map(function (t) {
                var v = VERDICT_CELLS[t.verdict] || t.verdict || "";
                return '<span class="bv-step-chip bv-step-chip--' + (t.verdict === "degraded" ? "bad" : "none") + '">' +
                    escapeHtml(t.instance || "?") + (v ? " " + escapeHtml(v) : "") + '</span>';
            }).join("") + '</div>';
        }
        return out ? '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.targetHead) + '</div>' + out + '</div>' : "";
    }

    function progressBar(ctx) {
        var prog = progressOf(ctx);
        if (!prog || prog.done >= prog.total) return "";
        var pct = Math.max(0, Math.min(100, (prog.done / prog.total) * 100));
        return '<div class="bv-progress" role="progressbar" aria-valuemin="0" aria-valuemax="' + prog.total +
            '" aria-valuenow="' + prog.done + '"><span style="width:' + pct.toFixed(1) + '%"></span></div>';
    }

    function synthesisHtml(ctx) {
        var syn = synthesisOf(ctx.pb);
        var hyps = asList(syn.hypotheses).filter(isObj);
        var parts = [];
        if (hyps.length) {
            parts.push('<table class="bv-cross-table bv-hypo-table"><thead><tr><th>순위</th><th>가설</th><th>상태</th><th>' +
                escapeHtml(PB_TEXT.evidence) + '</th></tr></thead><tbody>' + hyps.map(function (h) {
                    var links = asList(h.evidence_steps).map(function (s) {
                        return '<a href="#" class="bv-step-link" data-bv-action="goto-step" data-step="' + escapeHtml(s) + '">' +
                            escapeHtml(s) + '</a>';
                    }).join(" ");
                    return '<tr><td class="bv-mono">' + escapeHtml(h.rank || "") + '</td><td>' + escapeHtml(h.title || "") +
                        (h.rule_id ? ' <span class="bv-meta bv-mono">' + escapeHtml(h.rule_id) + '</span>' : "") + '</td><td>' +
                        hypoBadge(h) + '</td><td>' + (links || "—") + '</td></tr>';
                }).join("") + '</tbody></table>');
        } else {
            parts.push('<div class="bv-note">' + escapeHtml(ctx.partial ? PB_TEXT.synthesisPending : PB_TEXT.noHypothesis) + '</div>');
        }
        var acts = asList(syn.actions).filter(isObj);
        if (acts.length) {
            parts.push('<div class="bv-label">' + escapeHtml(PB_TEXT.actionHead) + '</div><ol class="bv-actions">' +
                acts.map(function (a) {
                    var meta = ["risk", "verification", "rollback"].filter(function (k) { return a[k]; }).map(function (k) {
                        return escapeHtml(PB_TEXT.actionMeta[k]) + ' ' + escapeHtml(a[k]);
                    });
                    return '<li>' + escapeHtml(a.text || "") + (a.source ? ' <span class="bv-src">' + escapeHtml(a.source) + '</span>' : "") +
                        (meta.length ? '<div class="bv-meta">' + meta.join(" · ") + '</div>' : "") + '</li>';
                }).join("") + '</ol><div class="bv-gated">' + escapeHtml(HUMAN_GATED_NOTE) + '</div>');
        }
        var fups = asList(syn.followups);
        if (fups.length) {
            parts.push('<div class="bv-label">' + escapeHtml(PB_TEXT.followupHead) + '</div><ul class="bv-actions">' +
                fups.map(function (f) { return '<li>' + escapeHtml(textOf(f)) + '</li>'; }).join("") + '</ul>');
        }
        // 서술은 근거(가설·대응) 다음 — 「AI 소견」 배지 · 가드가 버렸으면 사유
        if (syn.narrative) {
            parts.push('<div class="bv-opinion"><span class="bv-ai">' + escapeHtml(PB_TEXT.aiBadge) + '</span> ' + escapeHtml(syn.narrative) + '</div>');
        } else if (syn.narrative_dropped_reason) {
            parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(fill(PB_TEXT.narrativeDropped, { reason: syn.narrative_dropped_reason })) + '</div>');
        }
        return '<div class="bv-pb-block bv-pb-synthesis"><div class="bv-label">' + escapeHtml(PB_TEXT.summaryHead) + '</div>' + parts.join("") + '</div>';
    }

    function uiOf(extra) { return extra && isObj(extra.ui) ? extra.ui : {}; }

    // 알려진 문제 카드의 W8 계약 칸(§5.7.3) — 키가 있을 때만 줄을 만든다(없으면 종전 카드 그대로).
    function keExtraRows(ke) {
        var X = PB_TEXT.keExtra;
        var rows = [];
        ["issue", "cause"].forEach(function (k) {
            if (ke[k]) rows.push('<div class="bv-meta">' + escapeHtml(X[k]) + ': ' + escapeHtml(textOf(ke[k])) + '</div>');
        });
        ["unsatisfied", "unknown"].forEach(function (k) {
            var items = asList(ke[k]);
            if (items.length) {
                rows.push('<div class="bv-meta">' + escapeHtml(X[k]) + ': ' +
                    items.map(function (x) { return escapeHtml(textOf(x)); }).join(" · ") + '</div>');
            }
        });
        var pf = ke.permanent_fix;
        if (pf) {
            var pfText = isObj(pf) ? [pf.status, pf.problem_ref].filter(Boolean).join(" · ") : textOf(pf);
            if (pfText) rows.push('<div class="bv-meta">' + escapeHtml(X.permanent_fix) + ': ' + escapeHtml(pfText) + '</div>');
        }
        var who = ["owner", "last_reviewed"].filter(function (k) { return ke[k]; }).map(function (k) {
            return escapeHtml(X[k]) + ' ' + escapeHtml(ke[k]);
        });
        if (who.length) rows.push('<div class="bv-meta">' + who.join(" · ") + '</div>');
        var rec = isObj(ke.recurrence) ? ke.recurrence : null;
        if (rec && num(rec.count_30d) !== null) {
            rows.push('<div class="bv-meta">' + escapeHtml(fill(PB_TEXT.keRecurrence, { n: num(rec.count_30d) })) +
                (rec.last ? ' · ' + escapeHtml(fmtTime(rec.last)) : "") + '</div>');
        }
        return rows.join("");
    }

    // 우회 항목이 구조(`{action, risk, verification, rollback, review_only}`)면 항목별로 — 문자열이면 종전 한 줄.
    function workaroundRows(items) {
        if (!items.some(isObj)) {
            return '<div class="bv-meta">' + escapeHtml(PB_TEXT.knownFields.workaround) + ': ' +
                items.map(function (x) { return escapeHtml(textOf(x)); }).join(" · ") + '</div>';
        }
        return '<div class="bv-meta">' + escapeHtml(PB_TEXT.knownFields.workaround) + '</div><ol class="bv-actions">' +
            items.map(function (w) {
                if (!isObj(w)) return '<li>' + escapeHtml(textOf(w)) + '</li>';
                var meta = ["risk", "verification", "rollback"].filter(function (k) { return w[k]; }).map(function (k) {
                    return escapeHtml(PB_TEXT.actionMeta[k]) + ' ' + escapeHtml(w[k]);
                });
                return '<li>' + escapeHtml(w.action || textOf(w)) +
                    (w.review_only ? ' <span class="bv-src">' + escapeHtml(PB_TEXT.keReviewOnly) + '</span>' : "") +
                    (meta.length ? '<div class="bv-meta">' + meta.join(" · ") + '</div>' : "") + '</li>';
            }).join("") + '</ol>';
    }

    // 「맞음/아님/우회 효과 없음」 3버튼(§5.7.5 3) — 값 어휘는 feedback 라우트의 verdict 그대로.
    function keFeedbackHtml(ke, view, extra) {
        if (!extra || !extra.keFeedback || !view || !view.investigation_id || !ke.id) return "";
        var ui = uiOf(extra);
        var st = isObj(ui.ke) && isObj(ui.ke[ke.id]) ? ui.ke[ke.id] : {};
        var buttons = PB_TEXT.keFeedbackOptions.map(function (o) {
            var on = st.verdict === o[0];
            return '<button type="button" class="bv-choice' + (on ? " bv-choice--on" : "") + '" data-bv-action="ke-feedback" data-ke-id="' +
                escapeHtml(ke.id) + '" data-verdict="' + o[0] + '" aria-pressed="' + (on ? "true" : "false") + '"' +
                (ui.kbOff || st.pending ? " disabled" : "") + '>' + escapeHtml(o[1]) + '</button>';
        }).join("");
        var msg = ui.kbOff ? '<div class="bv-note bv-note--warn">' + escapeHtml(ui.kbOff) + '</div>'
            : st.msg ? '<div class="bv-note' + (st.warn ? " bv-note--warn" : "") + '">' + escapeHtml(st.msg) + '</div>' : "";
        return '<div class="bv-engine-choice bv-ke-feedback"><span class="bv-label">' + escapeHtml(PB_TEXT.keFeedbackHead) + '</span>' +
            buttons + '<div class="bv-meta">' + escapeHtml(PB_TEXT.keFeedbackNote) + '</div>' + msg + '</div>';
    }

    function knownErrorsHtml(b, view, extra) {
        var kes = knownErrorsOf(b);
        if (!kes.length) return "";
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.knownHead) + '</div>' +
            kes.map(function (ke) {
                var rows = ["satisfied", "missing_steps", "workaround"].map(function (k) {
                    var items = asList(ke[k]);
                    if (!items.length) return "";
                    if (k === "workaround") return workaroundRows(items);
                    return '<div class="bv-meta">' + escapeHtml(PB_TEXT.knownFields[k]) + ': ' +
                        items.map(function (x) { return escapeHtml(textOf(x)); }).join(" · ") + '</div>';
                }).join("");
                var chips = (ke.badge ? ' ' + chipHtml("warn", ke.badge) : "") +
                    (ke.pinned === true ? ' ' + chipHtml("ok", PB_TEXT.kePinned) : "") +
                    (ke.stale === true ? ' ' + chipHtml("warn", PB_TEXT.stale) : "");
                return '<div class="bv-ke">' + knownBadge(ke) + ' <span class="bv-mono">' + escapeHtml(ke.id || "") + '</span> ' +
                    escapeHtml(ke.title || "") + (ke.state ? ' <span class="bv-meta">' + escapeHtml(ke.state) + '</span>' : "") + chips +
                    rows + keExtraRows(ke) + keFeedbackHtml(ke, view, extra) + '</div>';
            }).join("") + '</div>';
    }

    function copyButton(key) {
        return '<button type="button" class="bv-choice" data-bv-action="copy" data-bv-copy="' + key + '">' + escapeHtml(PB_TEXT.copy) + '</button>';
    }

    // 꼬리(§5.6 ③ 7 · §5.7.5) — 「KB 초안 만들기」(운영자만 · 서버도 403) · 「포스트모템 내보내기」. 완료된 조사만.
    function kbTailHtml(view, extra) {
        if (!extra || !isObj(extra.kb) || !view || view.status !== "done" || !view.investigation_id) return "";
        var ui = uiOf(extra);
        var k = isObj(ui.kb) ? ui.kb : {};
        var lock = ui.kbOff || k.pending ? " disabled" : "";
        var buttons = (extra.kb.operator
            ? '<button type="button" class="bv-choice" data-bv-action="kb-draft"' + lock + '>' + escapeHtml(PB_TEXT.kbDraft) + '</button>' : "") +
            '<button type="button" class="bv-choice" data-bv-action="postmortem"' + lock + '>' + escapeHtml(PB_TEXT.postmortem) + '</button>';
        var parts = ['<div class="bv-engine-choice">' + buttons +
            (extra.kb.operator ? '<div class="bv-meta">' + escapeHtml(PB_TEXT.kbDraftNote) + '</div>' : "") + '</div>'];
        if (ui.kbOff) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(ui.kbOff) + '</div>');
        if (k.draftMsg) parts.push('<div class="bv-note' + (k.draftWarn ? " bv-note--warn" : "") + '">' + escapeHtml(k.draftMsg) + '</div>');
        if (k.draftYaml) {
            parts.push('<details class="bv-step" data-bv-key="kb-yaml" open><summary>' + escapeHtml(PB_TEXT.kbYaml) + '</summary>' +
                '<pre class="bv-pre">' + escapeHtml(k.draftYaml) + '</pre><div class="bv-engine-choice">' + copyButton("draft") + '</div></details>');
        }
        if (k.pmMsg) parts.push('<div class="bv-note' + (k.pmWarn ? " bv-note--warn" : "") + '">' + escapeHtml(k.pmMsg) + '</div>');
        var crit = asList(k.pmCriteria).filter(isObj);
        if (crit.length) {
            parts.push('<ul class="bv-actions">' + crit.map(function (c) {
                return '<li>' + chipHtml(c.met ? "ok" : "warn", c.met ? "충족" : "미충족") + ' ' + escapeHtml(c.label || c.id || "") +
                    (c.detail ? ' <span class="bv-meta">' + escapeHtml(c.detail) + '</span>' : "") + '</li>';
            }).join("") + '</ul>');
        }
        if (k.pmMarkdown) {
            parts.push('<details class="bv-step" data-bv-key="pm-md"><summary>' + escapeHtml(PB_TEXT.pmMarkdown) + '</summary>' +
                '<pre class="bv-pre">' + escapeHtml(k.pmMarkdown) + '</pre></details>' +
                '<div class="bv-engine-choice"><button type="button" class="bv-choice" data-bv-action="download" data-bv-copy="postmortem">' +
                escapeHtml(PB_TEXT.pmDownload) + '</button>' + copyButton("postmortem") + '</div>');
        }
        if (k.copyMsg) parts.push('<div class="bv-note">' + escapeHtml(k.copyMsg) + '</div>');
        return '<div class="bv-pb-block bv-tail"><div class="bv-label">' + escapeHtml(PB_TEXT.tailHead) + '</div>' + parts.join("") + '</div>';
    }

    // 「추가 조사 요청」(§5.6.1 G-21 · W10) — 목록 조회가 404(기능 꺼짐)면 ui.followup이 없어 그리지 않는다.
    function followupHtml(view, extra) {
        if (!extra || !extra.followup) return "";
        var f = uiOf(extra).followup;
        if (!isObj(f)) return "";
        var head = '<div class="bv-label">' + escapeHtml(PB_TEXT.followupHead) + '</div>';
        if (f.error) {
            return '<div class="bv-pb-block bv-followup">' + head +
                '<div class="bv-note bv-note--warn">' + escapeHtml(fill(PB_TEXT.followupListFailed, { reason: f.error })) + '</div></div>';
        }
        var focuses = asList(f.focuses).filter(isObj);
        var chosen = f.focus || (focuses.filter(function (o) { return o.active; })[0] || {}).id || "";
        var options = focuses.map(function (o) {
            var label = String(o.label || o.id || "") + (o.active ? "" : " — " + String(o.reason || ""));
            return '<option value="' + escapeHtml(o.id) + '"' + (o.active ? "" : " disabled") +
                (o.id === chosen && o.active ? " selected" : "") + '>' + escapeHtml(label) + '</option>';
        }).join("");
        var form = '<div class="bv-fu-form">' +
            '<label class="bv-meta">' + escapeHtml(PB_TEXT.followupFocus) +
            ' <select class="bv-input" data-bv-input="fu-focus" data-bv-key="fu-focus">' + options + '</select></label>' +
            '<label class="bv-meta">' + escapeHtml(PB_TEXT.followupNote) +
            ' <input type="text" class="bv-input" data-bv-input="fu-note" data-bv-key="fu-note" maxlength="2000" placeholder="' +
            escapeHtml(PB_TEXT.followupPlaceholder) + '"></label>' +
            '<button type="button" class="bv-open-conv" data-bv-action="followup-submit"' + (f.pending ? " disabled" : "") + '>' +
            escapeHtml(PB_TEXT.followupSubmit) + '</button></div>';
        var notes = (f.notice ? '<div class="bv-note"><span class="bv-badge bv-badge--hypo">가설</span> ' + escapeHtml(f.notice) + '</div>' : "") +
            '<div class="bv-meta">' + escapeHtml(PB_TEXT.followupNotice) + '</div>';
        var msg = f.msg ? '<div class="bv-note' + (f.warn ? " bv-note--warn" : "") + '">' + escapeHtml(f.msg) + '</div>' : "";
        var labels = {};
        focuses.forEach(function (o) { labels[o.id] = o.label; });
        var items = asList(f.items).filter(isObj);
        var list = items.length ? items.map(function (item) {
            return '<div class="bv-fu-item" data-investigation-id="' + escapeHtml(item.investigation_id || "") + '">' +
                '<div class="bv-pb-line"><span class="bv-pb-key">' + escapeHtml(PB_TEXT.followupItemHead) + '</span> ' +
                escapeHtml(labels[item.focus] || item.focus || "") +
                (item.submitted_at ? ' <span class="bv-meta">' + escapeHtml(fmtTime(item.submitted_at)) + '</span>' : "") + '</div>' +
                renderBody(item, {}) + '</div>';
        }).join("") : '<div class="bv-note">' + escapeHtml(PB_TEXT.followupEmpty) + '</div>';
        return '<div class="bv-pb-block bv-followup">' + head + notes + form + msg + list + '</div>';
    }

    // 「이 알람 분석」(§5.6.2 ① · W11) — by-alarm 응답에 `previous`가 실렸을 때(= 기능 켜짐)만 그린다.
    function analyzeBarHtml(view, extra) {
        if (!extra || !extra.analyze) return "";
        var a = uiOf(extra).analyze;
        if (!isObj(a) || (!a.enabled && !a.off)) return "";
        var hasInv = a.again || (view && view.status && view.status !== "none");
        var label = hasInv ? PB_TEXT.analyzeAgain : PB_TEXT.analyzeNew;
        var parts = ['<div class="bv-engine-choice"><button type="button" class="bv-open-conv" data-bv-action="analyze"' +
            (a.pending || a.off ? " disabled" : "") + '>' + escapeHtml(label) + '</button>' +
            (a.openId ? '<button type="button" class="bv-choice" data-bv-action="open-conversation" data-investigation-id="' +
                escapeHtml(a.openId) + '">' + escapeHtml(PB_TEXT.analyzeOpenNew) + '</button>' : "") +
            '<div class="bv-meta">' + escapeHtml(PB_TEXT.analyzeNote) + '</div></div>'];
        if (a.off) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(a.off) + '</div>');
        if (a.msg) parts.push('<div class="bv-note' + (a.warn ? " bv-note--warn" : "") + '">' + escapeHtml(a.msg) + '</div>');
        if (a.refNote) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(a.refNote) + '</div>');
        var prev = asList(a.previous).filter(isObj);
        if (prev.length) {
            parts.push('<details class="bv-step" data-bv-key="analyze-prev"><summary>' +
                escapeHtml(fill(PB_TEXT.analyzePrevious, { n: prev.length })) + '</summary><ul class="bv-actions">' +
                prev.map(function (p) {
                    var chip = statusChip(p);
                    return '<li><span class="bv-mono">' + escapeHtml(p.investigation_id || "") + '</span> ' +
                        '<span class="bv-meta">' + escapeHtml(PB_TEXT.kindLabels[p.kind] || p.kind || "") + '</span> ' +
                        chipHtml(chip.kind, chip.text) +
                        (p.submitted_at ? ' <span class="bv-meta">' + escapeHtml(fmtTime(p.submitted_at)) + '</span>' : "") +
                        (extra.openConversation && p.investigation_id
                            ? ' <button type="button" class="bv-choice" data-bv-action="open-conversation" data-investigation-id="' +
                                escapeHtml(p.investigation_id) + '">' + escapeHtml(PB_TEXT.openConversation) + '</button>' : "") + '</li>';
                }).join("") + '</ul></details>');
        }
        return '<div class="bv-pb-block bv-analyze">' + parts.join("") + '</div>';
    }

    // fetch Response → `{ok, status, body}`(본문이 JSON이 아니면 body = {}) — 호출부 공용.
    function responseOf(resp) {
        return Promise.resolve(resp && resp.json ? resp.json() : {}).catch(function () { return {}; }).then(function (body) {
            return { ok: !!(resp && resp.ok), status: resp ? resp.status : 0, body: body || {} };
        });
    }

    // 기능 꺼짐 404 — 라우트 미등록·플래그 off는 `detail`이 문자열이다(사유 코드가 있는 404는 꺼짐이 아니다).
    function isFlagOff(res) {
        var detail = res && res.body && res.body.detail;
        return !!res && res.status === 404 && !(isObj(detail) && detail.code);
    }

    function reasonOf(res) {
        var detail = res && res.body && res.body.detail;
        if (isObj(detail)) return String(detail.reason || detail.message || detail.code || "");
        if (detail) return String(detail);
        return "HTTP " + ((res && res.status) || "?");
    }

    function runbookHtml(b) {
        var rb = runbookOf(b);
        if (!rb) return "";
        var F = PB_TEXT.runbookFields;
        var rows = ["meaning", "impact", "mitigation", "escalation", "owner", "last_reviewed", "references"].map(function (k) {
            var v = rb[k];
            if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return "";
            var text = Array.isArray(v) ? v.map(function (x) { return escapeHtml(textOf(x)); }).join(" · ") : escapeHtml(textOf(v));
            return '<div class="bv-item"><div class="bv-label">' + escapeHtml(F[k]) + '</div><div class="bv-text">' + text + '</div></div>';
        }).join("");
        return '<details class="bv-step bv-runbook" data-bv-key="runbook"><summary>' + escapeHtml(PB_TEXT.runbookHead) +
            (rb.stale ? ' ' + chipHtml("warn", PB_TEXT.stale) : "") + '</summary><div class="bv-step-body">' + rows + '</div></details>';
    }

    function safeHref(href) {
        var s = String(href || "");
        return /^(https?:\/\/|\/(?!\/))/i.test(s) ? s : "";
    }

    function referencesHtml(b) {
        var refs = referencesOf(b);
        var st = isObj(b.references_status) ? b.references_status : null;
        if (!refs.length && !(st && st.notice)) return "";
        var items = refs.slice(0, 3).map(function (r) {
            var href = safeHref(r.href);
            var title = escapeHtml(r.title || "") + (r.section ? " · " + escapeHtml(r.section) : "");
            return '<li>' + (href ? '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>' : title) +
                (r.excerpt ? '<div class="bv-meta">' + escapeHtml(r.excerpt) + '</div>' : "") + '</li>';
        }).join("");
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.refsHead) + '</div>' +
            (st && st.notice ? '<div class="bv-note">' + escapeHtml(st.notice) + '</div>' : "") +
            (items ? '<ul class="bv-actions">' + items + '</ul>' : "") + '</div>';
    }

    function listCell(items) {
        items = items.filter(function (x) { return x !== ""; });
        if (!items.length) return "—";
        return items.map(function (x) { return '<div>' + escapeHtml(x) + '</div>'; }).join("");
    }

    // 엔진 비교(§5.0.1 6) — 같은 틀(1순위 원인 · 근거 · 대응 · 미결)을 나란히. 서술은 그 아래 접힘.
    function engineCompareHtml(view, ctx, extra) {
        var b = ctx.b;
        var dd = isObj(b.deep_dive) ? b.deep_dive : null;
        if (!dd) return "";
        var ea = isObj(b.engine_agreement) ? b.engine_agreement : {};
        var syn = synthesisOf(ctx.pb);
        var hyps = asList(syn.hypotheses).filter(isObj);
        var pbSide = isObj(ea.playbook) ? ea.playbook : {};
        var hgSide = isObj(ea.holmesgpt) ? ea.holmesgpt : {};
        var pbCol = {
            cause: listCell(hyps.length ? [String(hyps[0].title || "") + (hyps[0].status_label ? " [" + hyps[0].status_label + "]" : "")] : []),
            basis: listCell(asList(pbSide.basis).map(textOf).concat(
                hyps.length && asList(hyps[0].evidence_steps).length ? [PB_TEXT.evidence + " " + asList(hyps[0].evidence_steps).join(", ")] : [])
                .concat(pbSide.layer ? [PB_TEXT.engineLayers + " " + pbSide.layer] : [])),
            action: listCell(asList(syn.actions).filter(isObj).slice(0, 3).map(function (a) { return String(a.text || ""); })),
            open: listCell((syn.top_inconclusive ? ["1순위 가설 판단 보류"] : []).concat(asList(syn.followups).map(textOf))),
        };
        var running = dd.status === "running" || dd.status === "pending";
        var hgCol;
        if (dd.status !== "done") {
            var st = DEEP_STATUS[dd.status] || ["none", String(dd.status || "")];
            var msg = running ? PB_TEXT.engineRunning : st[1] + (dd.reason ? " — " + dd.reason : "");
            hgCol = '<div class="bv-engine-col"><div class="bv-engine-title">' + escapeHtml(dd.label || PB_TEXT.engineHolmes) + ' ' +
                chipHtml(st[0], st[1]) + '</div><div class="bv-note' + (running ? "" : " bv-note--warn") + '">' + escapeHtml(msg) + '</div>' +
                (running ? '<div class="bv-skel"></div><div class="bv-skel"></div>' : "") + '</div>';
        } else {
            var rec = isObj(dd.recommendation) ? dd.recommendation : {};
            var tc = Array.isArray(dd.tool_calls) ? dd.tool_calls.length : num(dd.tool_calls);
            var hcol = {
                cause: listCell([textOf(dd.cause) || textOf(asList(dd.root_cause_hypotheses)[0])]),
                basis: listCell(asList(hgSide.basis).map(textOf)
                    .concat(asList(hgSide.layers).length ? [PB_TEXT.engineLayers + " " + asList(hgSide.layers).join(", ")] : [])
                    .concat(tc ? [fill(PB_TEXT.engineToolCalls, { n: tc })] : [])),
                action: listCell(asList(rec.items).slice(0, 3).map(textOf)),
                open: listCell(Array.isArray(dd.limitations) ? dd.limitations.map(textOf) : [textOf(dd.limitations)]),
            };
            hgCol = engineColumn(dd.label || PB_TEXT.engineHolmes, hcol, dd.summary, true);
        }
        var head = '<div class="bv-engine-head"><span class="bv-label">' + escapeHtml(PB_TEXT.engineHead) + '</span>';
        if (AGREEMENT[ea.result]) head += chipHtml(AGREEMENT[ea.result][0], ea.label || AGREEMENT[ea.result][1]);
        else if (running) head += chipHtml("running", "HolmesGPT: 진행 중");
        if (ea.result) head += '<span class="bv-meta">' + escapeHtml(PB_TEXT.engineApprox) + '</span>';
        if (ea.badge) head += chipHtml("warn", ea.badge);
        head += '</div>';
        var notes = [ea.note, ea.reason].filter(Boolean).map(function (n) { return '<div class="bv-note">' + escapeHtml(n) + '</div>'; }).join("");
        return '<div class="bv-pb-block bv-engine">' + head + notes +
            '<div class="bv-engine-grid">' + engineColumn(PB_TEXT.enginePlaybook, pbCol, syn.narrative, false) + hgCol + '</div>' +
            (running ? "" : engineChoiceHtml(view, extra)) + '</div>';
    }

    function engineColumn(title, col, narrative, isHypo) {
        var R = PB_TEXT.engineRows;
        return '<div class="bv-engine-col"><div class="bv-engine-title">' + escapeHtml(title) +
            (isHypo ? ' <span class="bv-badge bv-badge--hypo">가설</span>' : "") + '</div>' +
            ["cause", "basis", "action", "open"].map(function (k) {
                return '<div class="bv-item"><div class="bv-label">' + escapeHtml(R[k]) + '</div><div class="bv-text">' + col[k] + '</div></div>';
            }).join("") +
            (narrative ? '<details class="bv-engine-narr" data-bv-key="narr-' + (isHypo ? "hg" : "pb") + '"><summary>' +
                escapeHtml(PB_TEXT.engineNarrative) + '</summary><div class="bv-opinion"><span class="bv-ai">' + escapeHtml(PB_TEXT.aiBadge) +
                '</span> ' + escapeHtml(narrative) + '</div></details>' : "") + '</div>';
    }

    function engineChoiceHtml(view, extra) {
        if (!extra.engineChoice || !view.investigation_id) return "";
        var ui = isObj(extra.ui) ? extra.ui : {};
        var msg = "";
        if (ui.choiceOff) msg = '<div class="bv-note bv-note--warn">' + escapeHtml(ui.choiceOff) + '</div>';
        else if (ui.choiceMsg) msg = '<div class="bv-note">' + escapeHtml(ui.choiceMsg) + '</div>';
        var buttons = PB_TEXT.choiceOptions.map(function (o) {
            var on = ui.choice === o[0];
            return '<button type="button" class="bv-choice' + (on ? " bv-choice--on" : "") + '" data-bv-action="engine-choice" data-choice="' +
                o[0] + '" aria-pressed="' + (on ? "true" : "false") + '"' + (ui.choiceOff || ui.pending ? " disabled" : "") + '>' +
                escapeHtml(o[1]) + '</button>';
        }).join("");
        return '<div class="bv-engine-choice"><span class="bv-label">' + escapeHtml(PB_TEXT.choiceHead) + '</span>' + buttons +
            '<div class="bv-meta">' + escapeHtml(PB_TEXT.choiceNote) + '</div>' + msg + '</div>';
    }

    // 상세형 — 조사 대화 첫 고정 카드 · break-glass 드로어(§5.6 ③ 배치 1~7 순서 고정)
    function renderPlaybookDetail(view, ctx, extra) {
        var b = ctx.b;
        var readings = readingsOf(ctx.pb);
        var parts = [];
        // 1. 머리 — 대상 · 진척 · 단계 판정(공통 칸 1회)
        parts.push('<div class="bv-pb-meta">' + playbookMeta(ctx) + '</div>');
        parts.push(progressBar(ctx));
        parts.push(targetsHtml(ctx.pb));
        parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.stepsCommon) + '</div>' +
            '<div class="bv-steps">' + stepChipsLine(ctx) + '</div></div>');
        // 엔진 비교(조사 대화 첫 카드 맨 위 · 심층 조사가 붙은 경우만)
        parts.push(engineCompareHtml(view, ctx, extra));
        // 2. 종합
        parts.push(synthesisHtml(ctx));
        // 3. 알려진 문제(피드백 3버튼은 extra.keFeedback일 때만 · W8)
        parts.push(knownErrorsHtml(b, view, extra));
        // 4. 런북(접힘)
        parts.push(runbookHtml(b));
        // 5. 단계 섹션(아코디언 · 이상 단계 기본 펼침) + 진행 중 스켈레톤
        parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.stepsDetailHead) + '</div>' +
            stepSections(ctx.pb).map(function (sec) { return stepSectionHtml(sec, readings); }).join("") + skeletonSteps(ctx) + '</div>');
        // 6. 참고 문서
        parts.push(referencesHtml(b));
        // 7. 꼬리 — 실행 메모
        var notes = asList(ctx.pb.notes);
        if (notes.length) {
            parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.notesHead) + '</div>' +
                notes.map(function (n) { return '<div class="bv-meta">' + escapeHtml(textOf(n)) + '</div>'; }).join("") + '</div>');
        }
        // 7. 꼬리 — KB 초안 · 포스트모템(extra.kb일 때만 · W9)
        parts.push(kbTailHtml(view, extra));
        return parts.join("");
    }

    function renderPlaybook(view, ctx, extra) {
        var chip = statusChip(view);
        var parts = [renderHead(view, chip)];
        if (chip.reason) {
            parts.push('<div class="bv-reason' + (chip.kind === "fail" ? " bv-reason--fail" : "") + '">' +
                '사유: ' + escapeHtml(chip.reason) + '</div>');
        }
        if (isActive(view)) parts.push('<div class="bv-note">' + escapeHtml(TEXT.runningText) + '</div>');
        parts.push(extra.mode === "detail" ? renderPlaybookDetail(view, ctx, extra) : renderPlaybookSummary(view, ctx, extra));
        parts.push(analyzeBarHtml(view, extra));
        parts.push(followupHtml(view, extra));
        if (extra.note) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(extra.note) + '</div>');
        return '<div class="bv bv--playbook">' + parts.join("") + '</div>';
    }

    function renderHead(view, chip) {
        var head = '<div class="bv-head">' +
            '<span class="bv-chip bv-chip--' + chip.kind + '">' + escapeHtml(chip.text) + '</span>' +
            '<span class="bv-src">' + escapeHtml(TEXT.sourceBadge) + '</span>';
        if (view.verdict_level) head += '<span class="bv-meta">평가 ' + escapeHtml(view.verdict_level) + '</span>';
        if (view.kind === "episode") head += '<span class="bv-meta">사건 종합 조사</span>';
        if (view.updated_at) head += '<span class="bv-meta">' + escapeHtml(fmtTime(view.updated_at)) + '</span>';
        head += '</div>';
        return head;
    }

    /**
     * 조사 요약 1건 → HTML 문자열(카드·드로어 공용 · 순수 함수).
     *
     * @param {object|null} view  조회 API 응답(`{status, briefing_lines, cross_source, reason, …}`)
     * @param {object} [extra]    `{note: 문자열}` — 재조회 상한 도달 등 화면 쪽 안내(이스케이프됨).
     *     (plans/187 W5b) 플레이북 브리핑일 때만 읽는 키: `mode`("summary"|"detail") ·
     *     `openConversation`(요약형 「조사 대화로 열기」 버튼) · `busy`(bool|함수 — 버튼 비활성) ·
     *     `engineChoice`(상세형 4택 표시) · `ui`(4택 상태)
     */
    function renderBody(view, extra) {
        view = view || { status: "none" };
        var pbCtx = playbookContext(view);
        if (pbCtx) return renderPlaybook(view, pbCtx, extra || {});
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
        if (extra && extra.mode === "detail") parts.push(kbTailHtml(view, extra));
        parts.push(analyzeBarHtml(view, extra));
        parts.push(followupHtml(view, extra));
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
     *   (plans/187 W5b · 플레이북 브리핑에서만 쓰는 선택 키 — 없으면 종전과 같다)
     *   render: renderBody의 extra 기본값(`{mode, openConversation, busy}`)
     *   onOpenConversation(investigationId): 요약형 「조사 대화로 열기」
     *   engineChoice(choice) → Promise<{ok, status, detail}>: 상세형 「이 결론이 맞다」 4택 기록
     *   onView(view): 그린 뒤 알림(조사 대화의 범위 칩 설정 등)
     *   (plans/187 FE-2 · 선택 키 — 없으면 종전과 같다. 호출 함수는 모두 Promise<{ok, status, body}>)
     *   analyze: `{submit(), loadById(id), probe?(), follow?}` — 「이 알람 분석」(W11). by-alarm 응답의
     *     `previous`(또는 probe 응답)가 있을 때만 버튼을 그린다. follow(기본 true)면 새 조사로 갈아탄다.
     *   followup: `{list(), submit(focus, note), loadById(id)}` — 「추가 조사 요청」(W10 · 상세형)
     *   keFeedback(keId, verdict) — 알려진 문제 3버튼(W8)
     *   kb: `{operator, draft(), postmortem()}` — 꼬리 「KB 초안 만들기」·「포스트모템 내보내기」(W9)
     */
    function attach(container, opts) {
        var limitS = Number(opts.limitSeconds) > 0 ? Number(opts.limitSeconds) : DEFAULT_LIMIT_S;
        var ui = { choice: null, choiceMsg: "", choiceOff: "", pending: false };
        var last = null;
        var currentLoad = opts.load;
        var probed = false;
        var fuListed = false;
        var fuTimer = null;
        var fuStartedAt = 0;
        var fe2 = !!(opts.analyze || opts.followup || opts.keFeedback || opts.kb);

        function extraFor(info) {
            var extra = info && info.limited ? { note: fill(TEXT.limitReached, { s: limitS }) } : null;
            if (!opts.render && !opts.engineChoice && !fe2) return extra;
            var merged = {};
            Object.keys(opts.render || {}).forEach(function (k) { merged[k] = opts.render[k]; });
            if (extra) merged.note = extra.note;
            merged.engineChoice = !!opts.engineChoice;
            merged.ui = ui;
            if (opts.analyze) merged.analyze = true;
            if (opts.followup) merged.followup = true;
            if (opts.keFeedback) merged.keFeedback = true;
            if (opts.kb) merged.kb = { operator: !!opts.kb.operator };
            return merged;
        }

        function failText(err) { return (err && err.message) || String(err || ""); }

        // 호출 함수 결과를 `{ok, status, body}`로 맞춘다(fetch Response를 그대로 돌려줘도 된다).
        function call(fn, args) {
            return Promise.resolve()
                .then(function () { return fn.apply(null, args || []); })
                .then(function (res) { return res && typeof res.json === "function" ? responseOf(res) : (res || {}); });
        }

        // 다시 그려도 사용자가 펼친·접은 아코디언(<details data-bv-key>)은 그대로 둔다(15초 재조회)
        function keepOpenState(paint) {
            var q = container.querySelectorAll ? container : null;
            var state = {};
            if (q) Array.prototype.forEach.call(q.querySelectorAll("details[data-bv-key]"), function (d) {
                state[d.getAttribute("data-bv-key")] = d.open;
            });
            var inputs = {};
            if (q && fe2) Array.prototype.forEach.call(q.querySelectorAll("[data-bv-input]"), function (el) {
                inputs[el.getAttribute("data-bv-input")] = el.value;
            });
            paint();
            if (q) Array.prototype.forEach.call(q.querySelectorAll("details[data-bv-key]"), function (d) {
                var key = d.getAttribute("data-bv-key");
                if (Object.prototype.hasOwnProperty.call(state, key)) d.open = state[key];
            });
            // 입력 중인 보충 문구·고른 초점은 15초 재조회 뒤에도 남긴다
            if (q && fe2) Array.prototype.forEach.call(q.querySelectorAll("[data-bv-input]"), function (el) {
                var key = el.getAttribute("data-bv-input");
                if (Object.prototype.hasOwnProperty.call(inputs, key) && inputs[key] && !el.disabled) el.value = inputs[key];
            });
        }

        function inputValue(name) {
            var el = container.querySelector ? container.querySelector('[data-bv-input="' + name + '"]') : null;
            return el && el.value ? String(el.value).trim() : "";
        }

        // 「이 알람 분석」 — by-alarm 응답의 `previous`가 곧 기능 켜짐 신호다(capabilities에 없음).
        function noteAnalyze(view) {
            if (!opts.analyze) return;
            var a = ui.analyze || (ui.analyze = {});
            if (view && Array.isArray(view.previous)) {
                a.enabled = true;
                a.previous = view.previous;
                return;
            }
            if (probed || !opts.analyze.probe || a.enabled) return;
            probed = true;
            call(opts.analyze.probe).then(function (res) {
                if (res.ok && res.body && Array.isArray(res.body.previous)) {
                    a.enabled = true;
                    a.previous = res.body.previous;
                    repaint();
                }
            }, function () { /* 조사(probe) 실패는 버튼을 숨긴 채로 둔다 — 본 카드는 그대로 */ });
        }

        function submitAnalyze() {
            var a = ui.analyze || (ui.analyze = {});
            a.pending = true;
            a.msg = "";
            a.warn = false;
            a.refNote = "";
            repaint();
            call(opts.analyze.submit).then(function (res) {
                var body = res.body || {};
                if (isFlagOff(res)) { a.off = PB_TEXT.analyzeOff; return; }
                if (!res.ok) { a.msg = fill(PB_TEXT.analyzeFailed, { reason: reasonOf(res) }); a.warn = true; return; }
                var id = String(body.investigation_id || "");
                a.again = true;
                if (body.status === "rejected") {
                    a.msg = fill(PB_TEXT.analyzeRejected, { reason: body.reason || DEFAULT_REASONS.rejected });
                    a.warn = true;
                } else {
                    a.msg = fill(body.joined ? PB_TEXT.analyzeJoined : PB_TEXT.analyzeSubmitted, { id: id });
                }
                if (body.reference_time_source === "decision_ts") a.refNote = PB_TEXT.analyzeRefFallback;
                if (!id) return;
                if (opts.analyze.follow === false || !opts.analyze.loadById) {
                    a.openId = id;
                    return;
                }
                // POST 응답 id가 우선 — by-alarm 최신 조회 대신 이 조사를 재조회한다
                currentLoad = function () { return opts.analyze.loadById(id); };
                ui.ke = {};
                ui.kb = {};
                poller.start();
            }, function (err) {
                a.msg = fill(PB_TEXT.analyzeFailed, { reason: failText(err) });
                a.warn = true;
            }).then(function () { a.pending = false; repaint(); });
        }

        function stopFollowups() {
            if (fuTimer !== null) clearTimeout(fuTimer);
            fuTimer = null;
        }

        // 목록은 저장소만 읽는다 — 진행 중 항목은 조사 조회(GET …/{id})로 따로 갱신한다.
        function scheduleFollowups() {
            stopFollowups();
            var f = ui.followup;
            if (!f || !opts.followup.loadById) return;
            var active = asList(f.items).filter(function (it) { return isObj(it) && isActive(it); });
            if (!active.length || Date.now() - fuStartedAt >= limitS * 1000) return;
            fuTimer = setTimeout(function () {
                fuTimer = null;
                if (container.isConnected === false) return;
                Promise.all(active.map(function (it) {
                    return Promise.resolve()
                        .then(function () { return opts.followup.loadById(it.investigation_id); })
                        .then(function (v) { return v; }, function () { return null; });
                })).then(function (views) {
                    views.forEach(function (v, i) {
                        if (!isObj(v)) return;
                        var at = f.items.indexOf(active[i]);
                        if (at < 0) return;
                        v.submitted_at = v.submitted_at || active[i].submitted_at;
                        v.focus = v.focus || active[i].focus;
                        f.items[at] = v;
                    });
                    repaint();
                    scheduleFollowups();
                });
            }, POLL_MS);
        }

        function loadFollowups() {
            fuListed = true;
            return call(opts.followup.list).then(function (res) {
                var prev = ui.followup || {};
                if (isFlagOff(res)) { ui.followup = null; return; }
                if (!res.ok) { ui.followup = { error: reasonOf(res) }; return; }
                var body = res.body || {};
                ui.followup = {
                    focuses: asList(body.focuses), items: asList(body.items), notice: body.hypothesis_notice || "",
                    focus: prev.focus || "", msg: prev.msg || "", warn: !!prev.warn,
                };
                fuStartedAt = Date.now();
            }, function (err) {
                ui.followup = { error: failText(err) };
            }).then(function () { repaint(); scheduleFollowups(); });
        }

        function submitFollowup() {
            var f = ui.followup;
            if (!f || f.pending) return;
            var focus = inputValue("fu-focus");
            var note = inputValue("fu-note");
            f.focus = focus;
            if (!focus) { f.msg = PB_TEXT.followupNeedFocus; f.warn = true; repaint(); return; }
            f.pending = true;
            f.msg = "";
            repaint();
            call(opts.followup.submit, [focus, note]).then(function (res) {
                var body = res.body || {};
                if (isFlagOff(res)) { ui.followup = null; return false; }
                if (!res.ok) { f.msg = fill(PB_TEXT.followupFailed, { reason: reasonOf(res) }); f.warn = true; return false; }
                f.msg = fill(body.reused ? PB_TEXT.followupReused : PB_TEXT.followupSubmitted, { id: body.investigation_id || "" });
                f.warn = body.status === "rejected";
                if (f.warn) f.msg += " — " + (body.reason || DEFAULT_REASONS.rejected);
                return true;
            }, function (err) {
                f.msg = fill(PB_TEXT.followupFailed, { reason: failText(err) });
                f.warn = true;
                return false;
            }).then(function (reload) {
                f.pending = false;
                if (reload && ui.followup) {
                    var input = container.querySelector ? container.querySelector('[data-bv-input="fu-note"]') : null;
                    if (input) input.value = "";
                    return loadFollowups();
                }
                repaint();
            });
        }

        function sendKeFeedback(keId, verdict) {
            ui.ke = ui.ke || {};
            var st = ui.ke[keId] || (ui.ke[keId] = {});
            if (st.pending || ui.kbOff) return;
            var label = PB_TEXT.keFeedbackOptions.filter(function (o) { return o[0] === verdict; }).map(function (o) { return o[1]; })[0] || verdict;
            st.pending = true;
            repaint();
            call(opts.keFeedback, [keId, verdict]).then(function (res) {
                if (isFlagOff(res)) { ui.kbOff = PB_TEXT.kbOff; return; }
                if (res.ok) { st.verdict = verdict; st.msg = fill(PB_TEXT.keFeedbackDone, { label: label }); st.warn = false; }
                else { st.msg = fill(PB_TEXT.keFeedbackFailed, { reason: reasonOf(res) }); st.warn = true; }
            }, function (err) {
                st.msg = fill(PB_TEXT.keFeedbackFailed, { reason: failText(err) });
                st.warn = true;
            }).then(function () { st.pending = false; repaint(); });
        }

        function runKb(kind) {
            var k = ui.kb || (ui.kb = {});
            if (k.pending || ui.kbOff) return;
            k.pending = true;
            k.copyMsg = "";
            repaint();
            var fn = kind === "draft" ? opts.kb.draft : opts.kb.postmortem;
            call(fn).then(function (res) {
                var body = res.body || {};
                var detail = body.detail;
                if (isFlagOff(res)) { ui.kbOff = PB_TEXT.kbOff; return; }
                if (kind === "draft") {
                    if (res.ok) { k.draftMsg = fill(PB_TEXT.kbDraftDone, { id: body.draft_id || "" }); k.draftWarn = false; k.draftYaml = String(body.yaml || ""); }
                    else { k.draftMsg = fill(PB_TEXT.kbDraftFailed, { reason: reasonOf(res) }); k.draftWarn = true; }
                    return;
                }
                if (res.ok) {
                    k.pmMsg = PB_TEXT.pmDone;
                    k.pmWarn = false;
                    k.pmMarkdown = String(body.markdown || "");
                    k.pmCriteria = asList(body.criteria);
                } else {
                    k.pmMsg = fill(PB_TEXT.pmFailed, { reason: reasonOf(res) });
                    k.pmWarn = true;
                    k.pmMarkdown = "";
                    k.pmCriteria = isObj(detail) ? asList(detail.criteria) : [];
                }
            }, function (err) {
                if (kind === "draft") { k.draftMsg = fill(PB_TEXT.kbDraftFailed, { reason: failText(err) }); k.draftWarn = true; }
                else { k.pmMsg = fill(PB_TEXT.pmFailed, { reason: failText(err) }); k.pmWarn = true; }
            }).then(function () { k.pending = false; repaint(); });
        }

        function kbText(key) {
            var k = ui.kb || {};
            return key === "draft" ? String(k.draftYaml || "") : String(k.pmMarkdown || "");
        }

        function copyKb(key) {
            var k = ui.kb || (ui.kb = {});
            var clip = typeof navigator !== "undefined" && navigator.clipboard;
            Promise.resolve()
                .then(function () {
                    if (!clip || !clip.writeText) throw new Error("clipboard");
                    return clip.writeText(kbText(key));
                })
                .then(function () { k.copyMsg = PB_TEXT.copied; }, function () { k.copyMsg = PB_TEXT.copyFailed; })
                .then(repaint);
        }

        function downloadKb(key) {
            if (typeof Blob === "undefined" || typeof document === "undefined" || typeof URL === "undefined" || !URL.createObjectURL) return;
            var id = last && last.view ? String(last.view.investigation_id || "") : "";
            var url = URL.createObjectURL(new Blob([kbText(key)], { type: "text/markdown;charset=utf-8" }));
            var a = document.createElement("a");
            a.href = url;
            a.download = "postmortem-" + (id.replace(/[^\w.-]/g, "_") || "investigation") + ".md";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 0);
        }

        function repaint() {
            if (!last || container.isConnected === false) return;
            keepOpenState(function () { container.innerHTML = renderBody(last.view, extraFor(last.info)); });
        }

        if ((opts.render || opts.engineChoice || opts.onOpenConversation || fe2) && container.addEventListener) {
            container.addEventListener("click", function (e) {
                var el = e.target && e.target.closest ? e.target.closest("[data-bv-action]") : null;
                if (!el || !container.contains(el)) return;
                var action = el.getAttribute("data-bv-action");
                if (action === "goto-step") {
                    e.preventDefault();
                    var step = el.getAttribute("data-step");
                    Array.prototype.forEach.call(container.querySelectorAll("[data-bv-step]"), function (sec) {
                        if (sec.getAttribute("data-bv-step") !== step) return;
                        sec.open = true;
                        if (sec.scrollIntoView) sec.scrollIntoView({ block: "start", behavior: "smooth" });
                    });
                } else if (action === "open-conversation" && opts.onOpenConversation) {
                    if (el.disabled) return;
                    opts.onOpenConversation(el.getAttribute("data-investigation-id"));
                } else if (action === "engine-choice" && opts.engineChoice && !ui.pending && !ui.choiceOff) {
                    var choice = el.getAttribute("data-choice");
                    var label = PB_TEXT.choiceOptions.filter(function (o) { return o[0] === choice; }).map(function (o) { return o[1]; })[0] || choice;
                    ui.pending = true;
                    repaint();
                    Promise.resolve()
                        .then(function () { return opts.engineChoice(choice); })
                        .then(function (res) {
                            res = res || {};
                            if (res.status === 404) ui.choiceOff = PB_TEXT.choiceOff;
                            else if (res.ok) { ui.choice = choice; ui.choiceMsg = fill(PB_TEXT.choiceDone, { label: label }); }
                            else ui.choiceMsg = fill(PB_TEXT.choiceFailed, { reason: res.detail || ("HTTP " + (res.status || "?")) });
                        }, function (err) {
                            ui.choiceMsg = fill(PB_TEXT.choiceFailed, { reason: (err && err.message) || String(err || "") });
                        })
                        .then(function () { ui.pending = false; repaint(); });
                } else if (action === "analyze" && opts.analyze) {
                    if (el.disabled || (ui.analyze && (ui.analyze.pending || ui.analyze.off))) return;
                    submitAnalyze();
                } else if (action === "followup-submit" && opts.followup) {
                    if (el.disabled) return;
                    submitFollowup();
                } else if (action === "ke-feedback" && opts.keFeedback) {
                    if (el.disabled) return;
                    sendKeFeedback(el.getAttribute("data-ke-id"), el.getAttribute("data-verdict"));
                } else if ((action === "kb-draft" || action === "postmortem") && opts.kb) {
                    if (el.disabled) return;
                    runKb(action === "kb-draft" ? "draft" : "postmortem");
                } else if (action === "copy" && opts.kb) {
                    copyKb(el.getAttribute("data-bv-copy"));
                } else if (action === "download" && opts.kb) {
                    downloadKb(el.getAttribute("data-bv-copy"));
                }
            });
        }

        var poller = createPoller({
            load: function () { return currentLoad(); },
            limitMs: limitS * 1000,
            onView: function (view, info) {
                if (container.isConnected === false) { poller.stop(); return; }
                last = { view: view, info: info };
                noteAnalyze(view);
                keepOpenState(function () {
                    container.innerHTML = renderBody(view, extraFor(info));
                });
                if (opts.followup && !fuListed) loadFollowups();
                if (opts.onView) opts.onView(view);
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
            close: function () { poller.stop(); stopFollowups(); },
            poller: poller,
        };
    }

    // 답변 스트리밍 중에는 「조사 대화로 열기」를 막는다(진행 중 대화 보호 · §5.6.1 1). 다음 재조회 렌더는
    // attach의 `render.busy`가 같은 값을 다시 반영한다.
    function setBusy(rootEl, busy) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        Array.prototype.forEach.call(rootEl.querySelectorAll('[data-bv-action="open-conversation"]'), function (btn) {
            btn.disabled = !!busy;
            if (busy) btn.title = PB_TEXT.openBusy;
            else btn.removeAttribute("title");
        });
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
        PB_TEXT: PB_TEXT,
        hasPlaybook: function (view) { return !!playbookContext(view); },
        playbookScope: function (view) {
            var ctx = playbookContext(view);
            return ctx && isObj(ctx.pb.scope) ? ctx.pb.scope : null;
        },
        setBusy: setBusy,
        responseOf: responseOf,
        isFlagOff: isFlagOff,
        reasonOf: reasonOf,
        createPoller: createPoller,
        attach: attach,
    };
    root.BriefingView = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
