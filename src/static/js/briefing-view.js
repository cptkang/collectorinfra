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
 *   - 지연 로드 — 화면이 펼칠 때만 부른다. 조사 중이면 15초 간격으로 다시 묻되 상한(기본 1200초 =
 *     `investigation_followup_timeout_seconds` 기본 · plans/187 W22)에서 멈춘다. 닫으면 타이머를 정리한다(G-2 (a)).
 *
 * 전역은 `window.BriefingView` 하나만 둔다. DOM 없이 도는 순수 함수(문자열 렌더·재조회 상한)는
 * node로 단위 테스트한다(`tests/test_api/test_plan153_w3_briefing_view.py`).
 */
(function (root) {
    "use strict";

    var POLL_MS = 15000;          // 조사 중 재조회 간격(G-2 (a))
    var DEFAULT_LIMIT_S = 1200;   // 재조회 상한 — capabilities가 값을 주지 않으면 이 값(W22 · 조사 전체 600s의 2배 · D-344 ④)

    // sre_agent briefing_builder.HUMAN_GATED_NOTE와 같은 문구(채팅·WorkB가 그대로 싣는 줄).
    var HUMAN_GATED_NOTE = "※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동 실행 경로 없음)";

    var TEXT = {
        title: "조사 브리핑",
        crossSourceLineLabel: "크로스소스",   // noise_gate render_briefing_lines 라벨과 같은 글자
        sourceBadge: "자동 조사 · 도구 출력 인용",
        manualSourceBadge: "요청 분석 · 도구 출력 인용",   // (plans/194 W5) 사용자 요청 조사(kind·origin manual)
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
        // (plans/194 W5 · D-347) 수동 조사 모드(attach `manualMode`) · 접수 거부의 다음 행동
        manualNoneChip: "조사 안 됨",
        manualNoneText: "아직 조사하지 않은 알람입니다. 자동 조사는 꺼져 있습니다 — 필요한 알람만 조사를 시작하세요.",
        manualStart: "조사 시작",
        manualAutoOff: "자동 조사는 꺼져 있습니다 — 필요한 알람만 조사를 시작하세요.",
        manualJoined: "다른 사용자가 먼저 요청한 조사에 합류했습니다.",
        manualOpen: "장애 진단에서 보기",
        retryAt: "{hm} 이후 다시 요청할 수 있습니다.",
        retryLater: "잠시 뒤 다시 눌러 주세요.",
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

    // ── plans/199 W5b · D-351 §3.9: 용어 정리(표시 문구만) ───────────────────────────────────
    // `extra.summaryView`가 참일 때만 쓴다(거짓이면 위 표 그대로 · 비트 동일). 내부 키·저장값은 바꾸지 않고, 모르는 열·값은
    // 원문 그대로 보인다(침묵 금지). 뜻·단위는 생산 코드 기준 — 단계 표는 sre_agent `playbook_steps.py`, 지표 이름은 같은
    // 파일의 지표 행과 apm_gateway 중립 지표 이름(`adapters/jennifer/fields.py`)이다.
    var TERM_TEXT = { stripHead: "단계별 판정", levelHead: "중요도" };
    // 단계 표 열(COLUMN_LABELS에 없던 열 — W0 실측) → 문구 · 단위는 괄호
    var TERM_COLUMNS = {
        host: "호스트", baseline_p95_ms: "기준선 p95(ms)", error_rate: "오류율(0~1)", verdict_source: "판정 출처",
        prev_day: "전일 같은 시간대", prev_week: "전주 같은 시간대",
        change_pct_prev_day: "전일 대비(%)", change_pct_prev_week: "전주 대비(%)",
        rss: "RSS(상주 메모리)", args: "실행 인자", elapsed_ms: "경과(ms)", running_mode: "실행 모드",
        running_text: "실행 중 내용", sql: "SQL 문", error_type: "오류 유형", count: "건수", status: "상태",
        alarm_time: "알람 시각", severity: "심각도", alarm_name: "알람 이름",
    };
    // 「지표」 열(metric) 값 — S1 골든 시그널 비교(apm.golden_signals) · S2 런타임·풀(apm.runtime_and_pool)
    var METRIC_LABELS = {
        p95_ms: "p95 응답(ms)", tps: "TPS", error_rate: "오류율(0~1)", calls: "호출 수", avg_response_ms: "평균 응답(ms)",
        heap_used_mb: "힙 사용량(MB)", heap_committed_mb: "힙 확보량(MB)", gc_time_usage_pct: "GC 시간 비율(%)",
        process_cpu_pct: "프로세스 CPU(%)", active_services: "실행 중 요청 수", thread_current: "현재 스레드 수",
        db_pool_active: "DB 풀 사용 중", db_pool_configured_avg: "DB 풀 설정 크기(평균)", db_pool_idle_avg: "DB 풀 유휴(평균)",
        db_pool_usage_ratio: "DB 풀 사용률(0~1)",
    };
    // 「종류」 열(kind) 값 — S4 호스트 자원 지표 종류(usage_trend) · S5 타임라인 항목 종류(correlation.TimelineItem)
    var KIND_LABELS = {
        cpu: "CPU 사용률(%)", memory: "메모리 사용률(%)", filesystem: "파일시스템 사용률(%)", disk_io: "디스크 I/O",
        alarm: "알람", apm_event: "APM 이벤트", metric: "지표", change: "변경",
    };
    // 열 → 값 문구 표(판정 출처는 단계 머리 배지와 같은 문구)
    var TERM_VALUES = {
        metric: METRIC_LABELS,
        kind: KIND_LABELS,
        anomaly_kind: { sustained: "지속", spike: "일시" },
        verdict_source: PB_TEXT.sourceBadges,
    };

    function ownLabel(map, key) {
        return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null;
    }

    // 열 머리 문구 — 종전 표 → 용어 정리 표 → 원문
    function termColumn(col) {
        return ownLabel(COLUMN_LABELS, col) || ownLabel(TERM_COLUMNS, col) || String(col);
    }

    // 셀 값 문구 — 그 열의 표에 있는 문자열 값만(없으면 null = 원문 그대로)
    function termValue(col, value) {
        var map = ownLabel(TERM_VALUES, col);
        return map && typeof value === "string" ? ownLabel(map, value) : null;
    }

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
        return chips.join("") + remainingChips(ctx, secs.length);
    }

    // 남은 단계(S9 종합 제외) 칩 — 진행 중 단계는 수집 중, 나머지는 대기(칩 줄 · plans/199 W3 단계 스트립 공용)
    function remainingChips(ctx, shown) {
        var prog = progressOf(ctx);
        var out = "";
        if (prog && prog.total > shown + 1) {
            var remain = prog.total - 1 - shown;
            for (var i = 0; i < remain; i++) {
                var label = i === 0 && prog.current ? String(prog.current) : "…";
                out += '<span class="bv-step-chip bv-step-chip--pending">' + escapeHtml(label) + ' ' +
                    escapeHtml(i === 0 ? PB_TEXT.stepPending : PB_TEXT.stepWaiting) + '</span>';
            }
        }
        return out;
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

    // ── plans/199 W1 · D-351: 「한눈에 보기」 카드(요약형·상세형·비플레이북 공통) ─────────────────
    // `summaryModel(view)`은 브리핑에 이미 있는 구조화 필드만 결정적으로 고른다(새 LLM 호출 0 · D-035).
    // 판정·가설 순위·원인 계층은 바꾸지 않는다 — 계층 판정 정본은 sre_agent `engine_agreement.py`이고 화면은
    // enum → 문구 표만 가진다(모르는 enum은 원문 그대로 · 침묵 금지). 카드는 `extra.summaryView`가 참일 때만
    // 그린다(기능 조회 `investigation_summary_view_enabled` · 끄면 종전 렌더 비트 동일 · D-162).

    // 원인 계층 enum → 화면 문구(정본 `engine_agreement.LAYERS` + unknown — W1 테스트가 대조한다)
    var LAYER_LABELS = {
        db: "DB(SQL·커넥션 풀)",
        external: "외부 연계(외부 호출)",
        cpu: "WAS 애플리케이션(CPU 처리)",
        gc: "WAS JVM(GC·힙)",
        host_resource: "서버 자원(OS·메모리·디스크)",
        unknown: "원인 영역 미확정",
    };

    // 확실성 배지 → 칩 종류(CSS 접미사). 「확정」 = 판독 규칙의 근거가 지지함(plans/199 §3.2)
    var CERTAINTY_KINDS = { "확정": "ok", "추정": "warn", "미확정": "none" };

    var SUM_TEXT = {
        title: "한눈에 보기",
        rows: { area: "원인 영역", conclusion: "결론", target: "대상", scope: "영향 범위", actions: "할 일", mustSee: "꼭 확인" },
        confirmed: "확정",
        presumed: "추정",
        undetermined: "미확정",
        altArea: "다른 분석은 {label} 영역",
        sources: { playbook: "플레이북", deep: "심층 조사" },
        noCause: "원인 미확정 — {reason}",
        soFar: "지금까지: {text}",
        soFarSteps: "끝난 단계 {n}개 · 이상 판정 {bad}개 — 종합 전",
        noResult: "아직 끝난 단계가 없습니다",
        symptom: "증상 {text}",
        at: "발생 {at}",
        episode: "사건 종합 조사",
        sameIncident: "같은 사건 알람 {n}건",
        badInstances: "이상 인스턴스 {bad}/{total}",
        mustRunning: "조사 진행 중 — {total}단계 중 {done}단계까지의 결과입니다",
        mustRunningNoProgress: "조사 진행 중 — 끝난 단계까지의 결과입니다",
        mustQueued: "조사 대기 중 — 시작되면 결과가 채워집니다",
        mustFailed: "{chip} — {reason}",
        mustEvidence: "(결론 근거 단계)",
        mustKe: "알려진 문제 {id} 「{title}」 {result} · 우회 {workaround}",
        workaround: { yes: "있음", no: "없음" },
        mustMore: "외 {n}건",
    };
    var MUST_SEE_MAX = 3;   // 관제용 꼭 확인 — 최대 3줄 + 「외 n건」(§3.3)
    var FAILED_STATUSES = { failed: true, timeout: true, rejected: true };
    var NO_SUMMARY_STATUSES = { none: true, episode_existing: true };   // 요약할 조사 결과가 없는 상태
    // 「권고 없음」 고정 표지 접두 — 할 일이 아니므로 카드에서 뺀다. 출처: sre_agent/sre_agent/application/
    // briefing_builder.py(심층 `recommendation.items` · 플레이북 최상위 `recommendation.items` 기본값)
    var NO_ACTION_PREFIX = "조치 권고 없음(";

    function isAction(text) {
        return !!text && String(text).indexOf(NO_ACTION_PREFIX) !== 0;
    }

    function layerLabel(layer) {
        return Object.prototype.hasOwnProperty.call(LAYER_LABELS, layer) ? LAYER_LABELS[layer] : String(layer);
    }

    // 타임존 없는 벽시계 시각(`2026-10-19T10:00:00`) → `2026-10-19 10:00` — Date로 해석하지 않는다(시간대 이동 금지)
    function fmtWall(value) {
        var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(String(value || ""));
        return m ? m[1] + " " + m[2] : String(value || "");
    }

    // 1순위 가설 「지지」 — 상태 코드·문구 중 실린 것이 모두 지지일 때만(하나라도 다르면 지지 아님 · 단정 금지)
    function isSupported(h) {
        var st = h.status ? String(h.status) : "";
        var lb = h.status_label ? String(h.status_label) : "";
        if (!st && !lb) return false;
        return (!st || st === "supported") && (!lb || lb === "지지");
    }

    // 단계 결손 어휘 — 화면 칩(STEP_CHIPS)과 같은 문구. 결손·미연동이 아니면 빈 문자열.
    function stepGap(sec) {
        if (sec.status === "missing") return STEP_CHIPS.missing[1];
        if (sec.status === "skipped" && /not_connected|미연동/.test(String(sec.reason || ""))) return STEP_CHIPS.not_connected[1];
        return "";
    }

    function stepGapText(sec, gap, inEvidence) {
        var reason = gap === STEP_CHIPS.missing[1] ? String(sec.reason || "").split(" — ")[0] : "";
        return [sec.step || "?", sec.title || sec.action || ""].filter(Boolean).join(" ") + " " + gap +
            (inEvidence ? SUM_TEXT.mustEvidence : "") + (reason ? " — " + reason : "");
    }

    // 원인 영역 — `engine_agreement.playbook.layer` → unknown이면 `holmesgpt.layers[0]` → 없으면 unknown(§2)
    function summaryArea(b) {
        var ea = isObj(b.engine_agreement) ? b.engine_agreement : {};
        var pbLayer = isObj(ea.playbook) && ea.playbook.layer ? String(ea.playbook.layer) : "unknown";
        var hgLayers = isObj(ea.holmesgpt) ? asList(ea.holmesgpt.layers).map(String) : [];
        var layer = pbLayer !== "unknown" ? pbLayer : (hgLayers[0] || "unknown");
        var area = { layer: layer, label: layerLabel(layer), certainty: "" };
        if (ea.result === "different_layer") {
            var other = hgLayers.filter(function (l) { return l !== layer; })[0];
            if (other) area.alt = { layer: other, label: layerLabel(other) };
        }
        return { area: area, fromPlaybook: pbLayer !== "unknown", differentLayer: ea.result === "different_layer" };
    }

    function summaryTarget(view, ctx) {
        var sc = ctx && isObj(ctx.pb.scope) ? ctx.pb.scope : {};
        var win = ctx && isObj(ctx.pb.windows) ? ctx.pb.windows : {};
        var alarm = isObj(view.alarm) ? view.alarm : {};
        var cs = isObj(view.cross_source) ? view.cross_source : {};
        var host = sc.host || alarm.server_name || alarm.hostname || cs.host_key || "";
        var names = [host];
        if (sc.alarm_instance && sc.alarm_instance !== host) names.push(sc.alarm_instance);
        var t = {
            name: names.filter(Boolean).map(String).join(" · "),
            symptom: String(sc.alarm_name || alarm.alarm_name || ""),
            at: fmtWall(win.alarm_time || alarm.alarm_time || ""),
        };
        return t.name || t.symptom || t.at ? t : null;
    }

    // 영향 범위 — 사건 종합 조사 · 같은 사건 알람(건수 · 있으면 이름) · 이상 판정 인스턴스 수. 해당 없으면 줄 없음.
    function summaryScope(view, ctx) {
        var out = [];
        if (view.kind === "episode") out.push(SUM_TEXT.episode + (view.episode_id ? " " + view.episode_id : ""));
        var cs = isObj(view.cross_source) ? view.cross_source : (ctx && isObj(ctx.b.cross_source) ? ctx.b.cross_source : null);
        var alarm = isObj(view.alarm) ? view.alarm : null;
        var related = alarm ? asList(alarm.related).filter(isObj) : [];
        var n = 0;
        var detail = [];
        var sources = cs && isObj(cs.sources) ? cs.sources : {};
        Object.keys(sources).forEach(function (key) {
            var c = num(sources[key]);
            if (!c) return;
            n += c;
            detail.push((TEXT.sourceNames[key] || key) + " " + c);
        });
        if (!n && related.length) n = num(alarm.alarm_count) || related.length;
        if (n) {
            var names = related.map(function (r) { return String(r.alarm_name || ""); }).filter(Boolean);
            out.push(fill(SUM_TEXT.sameIncident, { n: n }) + (detail.length ? "(" + detail.join(" · ") + ")" : "") +
                (names.length ? " — " + names.join(" · ") : ""));
        }
        var targets = ctx && isObj(ctx.pb.scope) ? asList(ctx.pb.scope.targets).filter(isObj) : [];
        if (targets.length) {
            var bad = targets.filter(function (t) { return t.verdict === "degraded"; }).length;
            out.push(fill(SUM_TEXT.badInstances, { bad: bad, total: targets.length }));
        }
        return out;
    }

    function knownLine(ke) {
        var hit = PB_TEXT.knownResult[ke.result];
        return fill(SUM_TEXT.mustKe, {
            id: ke.id || "", title: ke.title || "", result: hit ? hit[1] : String(ke.result || ""),
            workaround: asList(ke.workaround).length ? SUM_TEXT.workaround.yes : SUM_TEXT.workaround.no,
        });
    }

    // 관제용 꼭 확인(§3.3 왼쪽 열) — ① 진행 중·실패 ② 결론 근거 단계의 결손·미연동(1순위 가설이 없으면 모든 단계)
    // ③ 알려진 문제 일치·후보 한 줄씩. 최대 3줄 + 「외 n건」 · 없으면 빈 배열. 심층 조사 실패·생략은 넣지 않는다(담당자용).
    function summaryMustSee(view, ctx, chip, running, failed, top) {
        var items = [];
        if (running) {
            var p = isObj(view.progress) ? view.progress : null;
            items.push({ kind: "run", text: p && num(p.total)
                ? fill(SUM_TEXT.mustRunning, { done: num(p.done) || 0, total: num(p.total) }) : SUM_TEXT.mustRunningNoProgress });
        } else if (failed) {
            items.push({ kind: "warn", text: fill(SUM_TEXT.mustFailed, { chip: chip.text, reason: chip.reason }) });
        }
        if (ctx) {
            var evidence = top ? asList(top.evidence_steps).map(String) : null;
            stepSections(ctx.pb).forEach(function (sec) {
                var gap = stepGap(sec);
                var inEvidence = !!evidence && evidence.indexOf(String(sec.step)) >= 0;
                if (gap && (!evidence || inEvidence)) items.push({ kind: "warn", text: stepGapText(sec, gap, inEvidence) });
            });
        }
        var b = ctx ? ctx.b : (isObj(view.briefing) ? view.briefing : {});
        knownErrorsOf(b).forEach(function (ke) {
            if (ke.result === "match" || ke.result === "candidate") items.push({ kind: "info", text: knownLine(ke) });
        });
        if (items.length > MUST_SEE_MAX) {
            var more = items.length - MUST_SEE_MAX;
            items = items.slice(0, MUST_SEE_MAX);
            items.push({ kind: "more", text: fill(SUM_TEXT.mustMore, { n: more }) });
        }
        return items;
    }

    // 플레이북 갈래 — 결론·할 일·확실성(§2 · §3.2). briefing_lines(원 키 줄)는 재료로 쓰지 않는다(W0 실측 ③).
    function playbookSummary(view, ctx, model, chip, running, failed) {
        var syn = synthesisOf(ctx.pb);
        var hyps = asList(syn.hypotheses).filter(isObj);
        var top = hyps[0] || null;
        var dd = isObj(ctx.b.deep_dive) ? ctx.b.deep_dive : null;
        var deepCause = dd && dd.status === "done" ? (textOf(dd.cause) || textOf(asList(dd.root_cause_hypotheses)[0])) : "";
        var ar = summaryArea(ctx.b);
        var secs = stepSections(ctx.pb);
        var gapInEvidence = !!top && secs.some(function (sec) {
            return stepGap(sec) && asList(top.evidence_steps).map(String).indexOf(String(sec.step)) >= 0;
        });
        var text = top ? String(top.title || textOf(top)) : deepCause;
        var source = top ? "playbook" : (deepCause ? "deep" : null);
        if (running) {
            if (!text) {
                var bad = secs.filter(function (sec) { return sec.verdict === "degraded"; }).length;
                text = secs.length ? fill(SUM_TEXT.soFarSteps, { n: secs.length, bad: bad }) : SUM_TEXT.noResult;
            }
            text = fill(SUM_TEXT.soFar, { text: text });
        } else if (!text) {
            text = fill(SUM_TEXT.noCause, { reason: (failed && chip.reason) || PB_TEXT.noHypothesis });
        }
        var prog = progressOf(ctx);
        model.conclusion = { text: text, source: source,
            progress: running && prog ? fill(PB_TEXT.progress, { done: prog.done, total: prog.total }) : "" };
        // 위에서부터 먼저 맞는 것 — 미확정 → 추정 → 확정
        var certainty;
        if (failed || (!ar.fromPlaybook && !hyps.length)) certainty = SUM_TEXT.undetermined;
        else if (running || !top || !isSupported(top) || syn.top_inconclusive === true || ar.differentLayer ||
            gapInEvidence || !ar.fromPlaybook) certainty = SUM_TEXT.presumed;
        else certainty = SUM_TEXT.confirmed;
        ar.area.certainty = certainty;
        model.area = ar.area;
        var acts = asList(syn.actions).filter(isObj).map(function (a) { return String(a.text || textOf(a)); });
        if (!acts.length && dd && isObj(dd.recommendation)) acts = asList(dd.recommendation.items).map(textOf);
        model.actions = acts.filter(isAction).slice(0, 2);
        model.mustSee = summaryMustSee(view, ctx, chip, running, failed, top);
    }

    // 비플레이북 갈래(종전 HolmesGPT 브리핑) — 「원인 가설」 첫 줄([추정]) → 없으면 「요약」(원인 영역은 미확정)
    function plainSummary(view, model, chip, running, failed) {
        var lines = asList(view.briefing_lines).filter(isObj);
        function firstRow(label) {
            var hit = lines.filter(function (l) { return l.label === label; })[0];
            var rows = hit ? String(hit.text || "").split("\n").filter(function (r) {
                return r.trim() !== "" && r.trim() !== HUMAN_GATED_NOTE;
            }) : [];
            return rows.map(function (r) { return r.replace(BADGE_PREFIX, ""); });
        }
        var hypo = firstRow("원인 가설")[0] || "";
        var text = hypo || firstRow("요약")[0] || "";
        var source = text ? "deep" : null;
        if (running) text = fill(SUM_TEXT.soFar, { text: text || SUM_TEXT.noResult });
        else if (!text) text = fill(SUM_TEXT.noCause, { reason: (failed && chip.reason) || PB_TEXT.noHypothesis });
        model.conclusion = { text: text, source: source, progress: "" };
        model.area = { layer: "unknown", label: layerLabel("unknown"),
            certainty: !failed && hypo ? SUM_TEXT.presumed : SUM_TEXT.undetermined };
        model.actions = firstRow("권고").filter(isAction).slice(0, 2);
        model.mustSee = summaryMustSee(view, null, chip, running, failed, null);
    }

    /**
     * (plans/199 W1) 조사 응답 1건 → 「한눈에 보기」 모델(순수 함수 · 문자열은 이스케이프 전 원문).
     * `{status:{code,kind,text,at}, area:{layer,label,certainty,alt?:{layer,label}}, conclusion:{text,source,progress},
     *   target:{name,symptom,at}|null, scope:[문자열], actions:[문자열 ≤2], mustSee:[{kind,text}], owner:null}`
     * certainty = 「확정」·「추정」·「미확정」 · conclusion.source = "playbook"|"deep"|null ·
     * mustSee.kind = run|warn|info|more. `owner`(담당자)는 자리만 둔다(ITAM 등 별도 조회 — 범위 밖 · D-351 ⑧).
     */
    function summaryModel(view) {
        view = view || { status: "none" };
        var ctx = playbookContext(view);
        var chip = statusChip(view);
        var status = view.status || "none";
        var failed = !!FAILED_STATUSES[status];
        var running = !failed && (isActive(view) || !!(ctx && ctx.partial));
        var model = {
            status: { code: status, kind: chip.kind, text: chip.text, at: view.updated_at || "" },
            area: null, conclusion: null,
            target: summaryTarget(view, ctx), scope: summaryScope(view, ctx),
            actions: [], mustSee: [], owner: null,
        };
        if (ctx) playbookSummary(view, ctx, model, chip, running, failed);
        else plainSummary(view, model, chip, running, failed);
        return model;
    }

    function sumRow(label, html) {
        return '<div class="bv-sum-row"><div class="bv-label">' + escapeHtml(label) + '</div><div class="bv-text">' + html + '</div></div>';
    }

    // ── plans/199 W2 · D-351 ④: 전달문 — 고정 양식 미리보기 + [전달문 복사] ──────────────────────
    // 카드와 같은 `summaryModel`만 읽는다(새 계산·LLM 호출 0). 시스템이 메신저·문자로 직접 보내지 않는다.
    var HANDOFF_TEXT = {
        head: "[장애 전달]",
        area: "원인 영역: ",
        owner: "담당: ",
        conclusion: "결론: ",
        scope: "영향 범위: ",
        actions: "확인 요청: ",
        mustSee: "주의: ",
        investigation: "조사: ",
        // 여러 항목(영향 범위·확인 요청·주의) 사이 — 항목 문구 안에 이미 「 · 」가 있어 다른 기호로 끊는다
        sep: " / ",
        // 포털 `/?investigation=<id>`는 장애 진단이 켜져 있으면 그 조사 하위 탭, 꺼져 있으면 조사 대화 탭으로
        // 연다(app.js openInvestigationFromQuery · 미로그인이면 로그인 뒤 그 주소로 돌아온다) — 두 설정 모두 참인 안내
        link: "포털에서 근거 확인 {url}",
        copy: "전달문 복사",
        view: "전달문 보기",
        raw: "원문 포함",
        rawTitle: "운영자 화면 — 가리지 않은 원문이 그대로 복사됩니다",
    };
    // 사용자 뷰의 `briefing`에 실리는 키(noise_gate investigation_store.PLAYBOOK_VIEW_KEYS와 같은 집합)
    var USER_BRIEFING_KEYS = { partial: true, playbook: true, deep_dive: true, engine_agreement: true };

    /**
     * (plans/199 W2) 운영자 뷰(원문) 판정 — 서버가 정한 응답 모양으로만 본다(클라이언트 역할 값을 쓰지 않는다 · D-325 ③).
     * 운영자 뷰는 `briefing` 키를 늘 싣고(원 dict 또는 null), 사용자 뷰는 플레이북 표시용 부분
     * (`USER_BRIEFING_KEYS`)만 싣거나 키 자체가 없다(investigation_store.investigation_view). 원 브리핑은
     * sre_agent 빌더가 늘 `summary`를 싣는다 — 표시용 키만 남은 dict는 사용자 뷰다.
     */
    function isRawView(view) {
        if (!isObj(view) || !Object.prototype.hasOwnProperty.call(view, "briefing")) return false;
        var b = view.briefing;
        if (!isObj(b)) return true;
        return Object.keys(b).some(function (k) { return !Object.prototype.hasOwnProperty.call(USER_BRIEFING_KEYS, k); });
    }

    // 전달문 한 줄 — 항목 값 안의 줄바꿈(`\r`·`\n`·U+2028/2029)을 공백 하나로 접는다(LLM 유래 원인·사유가 여러 줄이어도
    // 「원인 영역:」 같은 머리말 줄이 새로 생기지 않게 — 고정 양식 유지). 카드 HTML에는 쓰지 않는다.
    function oneLine(text) {
        return String(text).replace(/\s*[\r\n\u2028\u2029]+\s*/g, " ").trim();
    }

    // 포털에서 이 조사를 여는 주소 — 브라우저 밖(테스트 등)이거나 id가 없으면 빈 문자열(그 부분은 뺀다)
    function handoffLink(id) {
        var origin = typeof location !== "undefined" && location ? String(location.origin || "") : "";
        return id && /^https?:\/\//.test(origin) ? origin + "/?investigation=" + encodeURIComponent(String(id)) : "";
    }

    // (plans/199 W2) 카드·전달문의 원인 영역 표시 `{label, certainty}` — 영역을 모르면 「원인 영역 미확정」 대신 「미확정」.
    // 확실성도 미확정이면 겹치지 않게 한 번만(label 빈 문자열), 추정이면 그 신호를 남긴다(「미확정 — 추정」 — 가설이 단정처럼
    // 읽히지 않게). 아는 영역은 모델 그대로. 모델의 label·certainty와 엔진 비교의 LAYER_LABELS 문구는 바꾸지 않는다.
    function areaShown(a) {
        if (a.layer !== "unknown") return { label: a.label, certainty: a.certainty };
        var u = SUM_TEXT.undetermined;
        return a.certainty === u ? { label: "", certainty: u } : { label: u, certainty: a.certainty };
    }

    // 전달문 「조사:」 시각 — 해석되면 브라우저 지역 시각 `YYYY-MM-DD HH:MM`(머리 줄 `fmtWall`과 같은 모양), 아니면 원문
    // (`updated_at`은 시간대가 붙어 와 fmtWall로 자르면 UTC 벽시계가 된다 — Date로 해석한다 · 공용 fmtTime은 그대로)
    function fmtMinute(value) {
        var d = new Date(value);
        if (!value || isNaN(d.getTime())) return String(value || "");
        function p(n) { return (n < 10 ? "0" : "") + n; }
        return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
    }

    /**
     * (plans/199 W2 · D-351 ④) 「한눈에 보기」 모델 → 담당자 전달문(고정 양식 평문 · 순수 함수). 없는 항목의 줄은 뺀다.
     * opts: `{id: 조사 id, link: 포털 주소}`(둘 다 선택). 문자열은 모델 값 그대로다(줄바꿈만 공백으로 접는다 — `oneLine`)
     * — 사용자 뷰는 서버가 가린 값이 그대로 실리고, 이 함수는 가리지도 풀지도 않는다. 담당자 줄은 `model.owner`가 있을 때만(지금은 자리만 · D-351 ⑧).
     */
    function handoffText(model, opts) {
        if (!isObj(model) || !isObj(model.area) || !isObj(model.conclusion)) return "";
        opts = opts || {};
        var T = HANDOFF_TEXT;
        var t = isObj(model.target) ? model.target : {};
        var head = [t.name, t.symptom, t.at ? fill(SUM_TEXT.at, { at: t.at }) : ""].filter(Boolean).join(" · ");
        var a = model.area;
        var s = areaShown(a);
        var lines = [T.head + (head ? " " + head : "")];
        lines.push(T.area + (s.label ? s.label + (s.certainty ? " — " + s.certainty : "") : s.certainty) +
            (isObj(a.alt) ? " · " + fill(SUM_TEXT.altArea, { label: a.alt.label }) : ""));
        if (model.owner) lines.push(T.owner + textOf(model.owner));
        if (model.conclusion.text) lines.push(T.conclusion + model.conclusion.text);
        var scope = asList(model.scope).filter(Boolean);
        if (scope.length) lines.push(T.scope + scope.join(T.sep));
        var acts = asList(model.actions).filter(Boolean);
        if (acts.length) lines.push(T.actions + acts.join(T.sep));
        var must = asList(model.mustSee).filter(isObj).map(function (m) { return String(m.text || ""); }).filter(Boolean);
        if (must.length) lines.push(T.mustSee + must.join(T.sep));
        var st = isObj(model.status) ? model.status : {};
        var inv = [opts.id ? String(opts.id) : "", st.at ? fmtMinute(st.at) : "",
            opts.link ? fill(T.link, { url: opts.link }) : ""].filter(Boolean);
        if (inv.length) lines.push(T.investigation + inv.join(" · "));
        return lines.map(oneLine).join("\n");
    }

    // 카드·[전달문 복사]가 함께 쓰는 전달문 재료 — 같은 뷰면 같은 글자(복사는 DOM을 읽지 않고 다시 만든다)
    function handoffOf(view, model) {
        var id = isObj(view) && view.investigation_id ? String(view.investigation_id) : "";
        return { text: handoffText(model, { id: id, link: handoffLink(id) }), raw: isRawView(view) };
    }

    // [전달문 복사] · 「원문 포함」(운영자 뷰만) · [전달문 보기](JS 없이 열리는 읽기 전용 미리보기 — 재조회에도
    // 열림 유지는 attach keepOpenState의 `data-bv-key`)
    function handoffButtonsHtml(handoff) {
        var T = HANDOFF_TEXT;
        return '<button type="button" class="bv-choice" data-bv-action="handoff-copy">' + escapeHtml(T.copy) + '</button>' +
            (handoff.raw ? '<span class="bv-handoff-raw" title="' + escapeHtml(T.rawTitle) + '">' + escapeHtml(T.raw) + '</span>' : "") +
            '<details class="bv-handoff" data-bv-key="handoff"><summary class="bv-choice">' + escapeHtml(T.view) + '</summary>' +
            '<pre class="bv-pre bv-handoff-text">' + escapeHtml(handoff.text) + '</pre></details>';
    }

    /**
     * (plans/199 W1) 「한눈에 보기」 카드 HTML(모든 문자열 이스케이프). 맨 아래 `.bv-sum-actions`
     * (`data-bv-summary-actions`)는 W2·W3 버튼([전달문 복사]·[전달문 보기]·[근거 보기]·[자세히]) 자리다.
     * (plans/199 W2) `handoff`(`{text, raw}` — `handoffOf`)가 있으면 그 자리 맨 앞에 전달문 버튼을 둔다 —
     * 클릭 처리기가 있는 attach 렌더만 넘긴다(없으면 종전처럼 빈 자리). 복사 결과 안내는 `extra.ui.handoff.msg`.
     * (plans/199 W3) `tiers`가 참이면 그 뒤에 [근거 보기]·[자세히](상세형 ②·③을 열고 스크롤) — 같은 조건(attach 상세형)만.
     */
    function summaryCardHtml(model, extra, handoff, tiers) {
        if (!isObj(model) || !isObj(model.area) || !isObj(model.conclusion)) return "";
        var R = SUM_TEXT.rows;
        var st = isObj(model.status) ? model.status : {};
        var head = '<div class="bv-sum-head"><span class="bv-sum-title">' + escapeHtml(SUM_TEXT.title) + '</span>' +
            (st.text ? chipHtml(st.kind || "none", st.text) : "") +
            (st.at ? '<span class="bv-meta">' + escapeHtml(fmtTime(st.at)) + '</span>' : "") + '</div>';
        var a = model.area;
        var s = areaShown(a);
        var rows = [sumRow(R.area, (s.label ? escapeHtml(s.label) + ' ' : "") + chipHtml(CERTAINTY_KINDS[s.certainty] || "none", s.certainty) +
            (isObj(a.alt) ? ' <span class="bv-sum-alt">' + escapeHtml(fill(SUM_TEXT.altArea, { label: a.alt.label })) + '</span>' : ""))];
        var c = model.conclusion;
        rows.push(sumRow(R.conclusion, escapeHtml(c.text) +
            (SUM_TEXT.sources[c.source] ? ' <span class="bv-src">' + escapeHtml(SUM_TEXT.sources[c.source]) + '</span>' : "") +
            (c.progress ? ' ' + chipHtml("running", c.progress) : "")));
        var t = model.target;
        if (isObj(t)) {
            var tp = [t.name];
            if (t.symptom) tp.push(fill(SUM_TEXT.symptom, { text: t.symptom }));
            if (t.at) tp.push(fill(SUM_TEXT.at, { at: t.at }));
            rows.push(sumRow(R.target, escapeHtml(tp.filter(Boolean).join(" · "))));
        }
        var scope = asList(model.scope);
        if (scope.length) rows.push(sumRow(R.scope, escapeHtml(scope.join(" · "))));
        var acts = asList(model.actions);
        if (acts.length) {
            rows.push(sumRow(R.actions, acts.map(function (x) { return '<div>' + escapeHtml(x) + '</div>'; }).join("") +
                '<div class="bv-gated">' + escapeHtml(HUMAN_GATED_NOTE) + '</div>'));
        }
        var must = asList(model.mustSee).filter(isObj);
        if (must.length) {
            rows.push(sumRow(R.mustSee, '<ul class="bv-sum-must">' + must.map(function (m) {
                return '<li class="bv-sum-must-item bv-sum-must-item--' + escapeHtml(m.kind || "warn") + '">' + escapeHtml(m.text) + '</li>';
            }).join("") + '</ul>'));
        }
        var hs = uiOf(extra).handoff;
        return '<section class="bv-sum" data-bv-summary="1">' + head + '<div class="bv-sum-rows">' + rows.join("") + '</div>' +
            '<div class="bv-sum-actions" data-bv-summary-actions>' + (isObj(handoff) ? handoffButtonsHtml(handoff) : "") +
            (tiers ? tierButtonsHtml() : "") + '</div>' +
            (isObj(hs) && hs.msg ? '<div class="bv-note">' + escapeHtml(hs.msg) + '</div>' : "") + '</section>';
    }

    // (plans/199 W1 교정) 대기열 — `extra.queue(view)`가 대기 칩을 주면(`queueOf`) 카드 상태 칩과 꼭 확인 진행 줄을
    // 머리 칩과 같은 말로 맞춘다. 대기 정보가 없으면 모델 그대로(종전).
    function queuedModel(model, qd) {
        if (!qd) return model;
        model.status.kind = "hold";
        model.status.text = qd.chip;
        model.mustSee = model.mustSee.map(function (m) {
            return m.kind === "run" ? { kind: "run", text: SUM_TEXT.mustQueued } : m;
        });
        return model;
    }

    // 머리 바로 아래 카드 — `extra.summaryView`가 참이고 요약할 결과가 있는 상태일 때만(아니면 빈 문자열 = 종전 그대로)
    // (plans/199 W3) `tiers` = 이 카드 아래에 상세형 ②·③이 있다(플레이북 상세형) — 단 이동 버튼도 처리기 있는 렌더에서만
    function summaryBlock(view, extra, tiers) {
        if (!extra || !extra.summaryView || NO_SUMMARY_STATUSES[(view && view.status) || "none"]) return "";
        var qd = queueOf(view, extra);
        var model = queuedModel(summaryModel(view), qd);
        // (plans/199 W2) 전달문 버튼은 클릭 처리기가 붙은 attach 렌더(`extra.handoff`)에서만 — 직접 렌더 경로는 빈 자리.
        // 대기 중(`qd`)에도 뺀다 — 아직 전달할 결과가 없다(대기가 아닌 진행 중은 그대로 · §3.4)
        return summaryCardHtml(model, extra, extra.handoff && !qd ? handoffOf(view, model) : null, !!tiers && !!extra.handoff);
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
        // (plans/199 W1) 「한눈에 보기」 카드가 켜져 있으면 머리 아래 카드가 이 「종합」 칸을 대신한다
        if (!extra.summaryView) {
            parts.push('<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.summaryHead) + '</div>' +
                (lines.length ? lines.join("")
                    : '<div class="bv-note">' + escapeHtml(ctx.partial ? PB_TEXT.synthesisPending : PB_TEXT.noHypothesis) + '</div>') +
                '</div>');
        }
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

    // 표 셀 1칸 — terms(plans/199 W5b · summaryView 켜짐)면 지표·종류 등 원문 값을 문구로
    function cellHtml(col, value, terms) {
        if (Array.isArray(value) && value.length > 1 && value.every(function (v) { return num(v) !== null; })) {
            return sparkline(value);
        }
        if (value === null || value === undefined || value === "") return "—";
        var term = terms ? termValue(col, value) : null;
        if (term) return escapeHtml(term);
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

    function renderPbTable(table, terms) {
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
                cols.map(function (c, i) { return '<td>' + cellHtml(c, row[i], terms) + '</td>'; }).join("") +
                (withBar ? '<td class="bv-bar-cell">' + stackedBar(cols, row) + '</td>' : "") + '</tr>';
        }).join("");
        return '<div class="bv-table-wrap"><table class="bv-cross-table bv-pb-table"><thead><tr>' +
            cols.map(function (c) { return '<th>' + escapeHtml(terms ? termColumn(c) : COLUMN_LABELS[c] || c) + '</th>'; }).join("") +
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

    function stepSectionHtml(sec, readings, terms) {
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
        body.push(renderPbTable(sec.table, terms));   // 근거(표)를 소견보다 먼저
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

    // (plans/199 W3) noHypo = 가설 표는 ② 근거 요약이 맡는다 — 대응·추가 확인·AI 소견만(없으면 블록 없음)
    function synthesisHtml(ctx, noHypo) {
        var syn = synthesisOf(ctx.pb);
        var hyps = asList(syn.hypotheses).filter(isObj);
        var parts = [];
        if (noHypo) {
            // 가설 표·「가설 없음」 안내는 ②의 가설 표 자리(hypoGridHtml)
        } else if (hyps.length) {
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
        if (noHypo && !parts.length) return "";
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
        var label = hasInv ? PB_TEXT.analyzeAgain : (extra.manualMode ? PB_TEXT.manualStart : PB_TEXT.analyzeNew);
        var locked = !!a.lockUntil && a.lockUntil > Date.now();   // (plans/194) 시간당 상한 — 다시 가능한 시각까지
        // (plans/194 W5) 대기·진행 중인 조사에는 [다시 분석]을 그리지 않는다 — 끝난 뒤에 다시 요청한다
        var parts = ['<div class="bv-engine-choice">' + (isActive(view) ? "" :
            '<button type="button" class="bv-open-conv" data-bv-action="analyze"' +
            (a.pending || a.off || locked ? " disabled" : "") + '>' + escapeHtml(label) + '</button>') +
            (a.openId ? '<button type="button" class="bv-choice" data-bv-action="open-conversation" data-investigation-id="' +
                escapeHtml(a.openId) + '">' + escapeHtml(PB_TEXT.analyzeOpenNew) + '</button>' : "") +
            '<div class="bv-meta">' + escapeHtml(PB_TEXT.analyzeNote) + '</div></div>'];
        if (a.off) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(a.off) + '</div>');
        if (a.msg) {
            parts.push('<div class="bv-note' + (a.warn ? " bv-note--warn" : "") + '">' + escapeHtml(a.msg) +
                (isObj(a.go) ? ' <button type="button" class="bv-choice" data-bv-action="open-investigation" data-investigation-id="' +
                    escapeHtml(a.go.id) + '">' + escapeHtml(a.go.label) + '</button>' : "") + '</div>');
        }
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
                        (p.investigation_id ? previousButton(p.investigation_id, extra) : "") + '</li>';
                }).join("") + '</ul></details>');
        }
        return '<div class="bv-pb-block bv-analyze">' + parts.join("") + '</div>';
    }

    // 「이전 분석」 행 버튼 — attach가 `onOpenPrevious`를 받았으면(extra.previousLabel) 모드와 무관하게
    // 그 버튼(plans/192 P8), 아니면 종전대로 요약형 「조사 대화로 열기」만.
    function previousButton(id, extra) {
        if (extra.previousLabel) {
            return ' <button type="button" class="bv-choice" data-bv-action="open-previous" data-investigation-id="' +
                escapeHtml(id) + '">' + escapeHtml(extra.previousLabel) + '</button>';
        }
        return extra.openConversation
            ? ' <button type="button" class="bv-choice" data-bv-action="open-conversation" data-investigation-id="' +
                escapeHtml(id) + '">' + escapeHtml(PB_TEXT.openConversation) + '</button>' : "";
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

    /**
     * (plans/194 W5 · U1 · D-347) 조사 기록이 없는 알람 카드의 「조사」 칸 본문 — 「조사 안 됨」 칩 + [조사 시작]
     * (`.inv-start-btn`) · (autoOff면) 자동 조사 꺼짐 안내 · 거부 사유 자리(`.inv-start-msg`, 처음엔 숨김).
     * 호출 없이 그리는 정적 마크업이다 — 접수는 누를 때 호출부가 한다.
     */
    function startBlockHtml(o) {
        o = o || {};
        return '<div class="bv-head">' + chipHtml("none", PB_TEXT.manualNoneChip) +
                '<button type="button" class="bv-open-conv inv-start-btn">' + escapeHtml(PB_TEXT.manualStart) + '</button></div>' +
            (o.autoOff ? '<div class="bv-meta">' + escapeHtml(PB_TEXT.manualAutoOff) + '</div>' : "") +
            '<div class="inv-start-msg" hidden></div>';
    }

    /** 접수 결과 한 줄 + (있으면) 다음 행동 버튼(`.inv-go` · `data-investigation-id`). warn이면 경고색. */
    function startMsgHtml(text, go, warn) {
        return '<div class="bv-note' + (warn ? " bv-note--warn" : "") + '"><span>' + escapeHtml(text) + '</span>' +
            (isObj(go) ? ' <button type="button" class="bv-choice inv-go" data-investigation-id="' + escapeHtml(go.id || "") + '">' +
                escapeHtml(go.label) + '</button>' : "") + '</div>';
    }

    /**
     * (plans/194 W5 · U2 · D-347) 조사 접수 응답(`{ok, status, body}`) → 화면 결과 — 알람 카드·관제 서랍·
     * 장애 진단 「이벤트 찾아 조사」 공용. `{kind, text, investigationId?, existingId?, retryAt?}`.
     * kind: accepted · joined · in_flight(같은 서버 조사 중) · busy(대기열·서비스 포화 — 문구에 「잠시 뒤
     * 다시」) · retry_later(`retry_after_seconds` — 다시 가능한 시각을 문구에) · rejected · failed.
     * 거부 문구는 서버 `reason_text`가 먼저다. texts = `{failed, rejected, joined}`(호출부 문구 · `{reason}`).
     * 사유는 반드시 text에 싣는다(침묵 금지).
     */
    function startOutcome(res, nowMs, texts) {
        var body = res && isObj(res.body) ? res.body : {};
        var detail = isObj(body.detail) ? body.detail : {};
        var raw = body.retry_after_seconds !== undefined ? body.retry_after_seconds : detail.retry_after_seconds;
        var retry = raw === null || raw === undefined ? NaN : Number(raw);
        var rejected = !!res && !!res.ok && body.status === "rejected";
        var code = String(body.reason || detail.code || "");
        var reason = String(body.reason_text || detail.reason_text || (rejected ? "" : reasonOf(res)));
        if ((!res || !res.ok || rejected) && isFinite(retry) && retry > 0) {
            var at = nowMs + retry * 1000;
            var d = new Date(at);
            var hm = (d.getHours() < 10 ? "0" : "") + d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
            return { kind: "retry_later", retryAt: at,
                text: (reason || code || "-").split(" — ")[0] + " — " + fill(PB_TEXT.retryAt, { hm: hm }) };
        }
        if (!res || !res.ok) return { kind: "failed", text: fill(texts.failed, { reason: reason }) };
        if (rejected) {
            var text = reason || fill(texts.rejected, { reason: code || "-" });
            if (code === "host_investigation_in_flight") {
                return { kind: "in_flight", text: text, existingId: String(body.existing_investigation_id || "") };
            }
            if (code === "queue_full" || code === "service_overloaded") {
                return { kind: "busy", text: text.indexOf("잠시 뒤") >= 0 ? text : text + " — " + PB_TEXT.retryLater };
            }
            return { kind: "rejected", text: text };
        }
        if (!body.investigation_id) return { kind: "failed", text: fill(texts.failed, { reason: "조사 id 없음" }) };
        var id = String(body.investigation_id);
        if (body.joined) return { kind: "joined", text: texts.joined, investigationId: id };
        return { kind: "accepted", text: "", investigationId: id };
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

    // ── plans/199 W3 · D-351 ①: 상세형 3단 — ① 한눈에 보기(카드) → ② 근거 요약(접힘) → ③ 상세(접힘) ────────────
    // `extra.summaryView`가 참일 때만 상세형을 이 배치로 그린다(거짓이면 종전 renderPlaybookDetail 그대로 · 비트 동일).
    // 블록은 지우지 않고 ②·③으로 내려 접는다 — 같은 결론은 카드 한 곳만 펼친 채 보인다(§3.1 끝). 펼침은 누구나 하고,
    // 관리자 추가 항목(가설 코드)은 서버가 운영자 뷰를 보냈을 때만 그린다(`isRawView` — 응답 모양 · D-325 ③ 개정 없음).
    // 펼침 기억은 attach가 맡는다(저장값 → `extra.expand` → 첫 `open`) — 렌더 함수는 순수하다(§3.7).
    var EV_TEXT = {
        evidenceHead: "근거 요약",
        detailHead: "상세",
        evidenceButton: "근거 보기 ▾",
        detailButton: "자세히 ▾",
        timelineHead: "타임라인 — 알람 시각 기준",
        timelineTitle: "알람 시각 기준 사건 {n}개",
        timelineAt: "알람 시각 {at}",
        lanes: { infra: "인프라", apm: "APM" },
        alarmMark: "알람",
        minutes: "{m}분",
        hypoColumns: [["지지", "ok"], ["판단 보류", "hold"], ["반박", "bad"]],
        mustHead: "꼭 확인 — 담당자용",
        mustEngine: "엔진 간 판단 불일치 — 플레이북 {pb} · 심층 조사 {hg}",
        mustDeep: "심층 조사 {status}",
        mustRunbook: "런북 검토 기한 경과",
        reviewed: " — 마지막 검토 {at}",
        limitsHead: "한계",
    };
    var EXPAND_KEYS = { evidence: true, detail: true };   // 펼침을 기억하는 단 — localStorage `bv.expand.<키>`
    var EXPAND_PREFIX = "bv.expand.";

    // 펼침 기억 읽기·쓰기 — 사람별 편의일 뿐 노출 범위와 무관하다. 사생활 모드·차단으로 던지면 기본 접힘(§3.7).
    function readExpand(key) {
        try {
            var s = root.localStorage;
            return !!s && s.getItem(EXPAND_PREFIX + key) === "1";
        } catch (_) { return false; }
    }

    function writeExpand(key, on) {
        try {
            var s = root.localStorage;
            if (s) s.setItem(EXPAND_PREFIX + key, on ? "1" : "0");
        } catch (_) { /* 저장 불가 — 이 패널 안에서만 유지 */ }
    }

    // ── plans/199 W4 · D-351 ⑤: [서술 보기] — 4줄 요약 먼저 · 원문은 다시 접어 안전 렌더 ─────────────────
    // 원문(HolmesGPT 마크다운)은 줄마다 **escapeHtml 먼저** 하고 그 결과에 화이트리스트 변환만 한다 — 제목(#~####) →
    // 굵은 소제목 · 목록(-·*·1.) · **굵게** · `인라인 코드` · 파이프 표(둘째 줄이 구분 행일 때). 링크·이미지·원시 HTML·
    // 자동 링크는 변환하지 않는다(escape된 글자 그대로). 만드는 태그·class는 고정 문자열이고 속성값에 원문을 넣지 않는다.
    // 답변 형식을 프롬프트로 고정하지 않으므로 형식이 어긋나도 글자는 그대로 남는다(생략 표지 줄 포함).
    var NARR_TEXT = {
        rows: { cause: "원인", basis: "근거", action: "대응", limits: "한계" },
        raw: "원문",
        toc: "목차",
        noRaw: "이 기록에는 답변 원문이 실려 있지 않습니다.",
        // (plans/199 W3) 종합 블록은 ③ 상세 안으로 옮겼다 — 단 이름을 함께 가리킨다
        seeSynthesis: "서술은 「" + EV_TEXT.detailHead + "」의 「{head}」 AI 소견을 보세요(같은 문장).",
    };
    var MD_TOC_MIN = 3;   // 제목이 이 수 이상이면 맨 위에 소제목 목차(텍스트만 — 한 화면 여러 패널이라 앵커 id 없음)
    var MD_HEAD = /^(#{1,4})\s+(.*)$/;
    var MD_ITEM = /^\s*([-*]|\d+\.)\s+(.*)$/;
    var MD_ROW = /^\s*\|/;
    var MD_SEP = /^\|?(\s*:?-{3,}:?\s*\|)*\s*:?-{3,}:?\s*\|?$/;

    // esc = escape가 끝난 한 줄. 백틱 짝 안은 코드(굵게 변환 안 함) · 짝 없는 백틱·별표는 글자 그대로.
    function mdInline(esc) {
        var segs = esc.split("`");
        return segs.map(function (seg, i) {
            if (i % 2 === 1 && i < segs.length - 1) return "<code>" + seg + "</code>";
            return (i % 2 === 1 ? "`" : "") + mdBold(seg);
        }).join("");
    }

    // `**` 짝을 앞에서부터 맞춘다(정규식 역추적 없이 선형). 안쪽 첫·끝 글자가 공백·별표면 짝으로 보지 않는다 —
    // 마스킹 표지(`password=***`)를 바꾸지 않는다.
    function mdBold(s) {
        var p = s.split("**"), out = p[0];
        for (var i = 1; i < p.length; i++) {
            if (i + 1 < p.length && /^[^\s*]/.test(p[i]) && /[^\s*]$/.test(p[i])) out += "<strong>" + p[i] + "</strong>" + p[++i];
            else out += "**" + p[i];
        }
        return out;
    }

    function mdRow(esc, tag) {
        return "<tr>" + esc.trim().replace(/^\||\|$/g, "").split("|").map(function (c) {
            return "<" + tag + ">" + mdInline(c.trim()) + "</" + tag + ">";
        }).join("") + "</tr>";
    }

    function renderMarkdownSafe(text) {
        var lines = String(text === null || text === undefined ? "" : text).split(/\r?\n/).map(escapeHtml);
        var out = [], heads = [], list = "", i = 0, m;
        function closeList() { if (list) { out.push("</" + list + ">"); list = ""; } }
        while (i < lines.length) {
            var ln = lines[i];
            if (MD_ROW.test(ln) && i + 1 < lines.length && MD_SEP.test(lines[i + 1].trim())) {
                closeList();
                var rows = [];
                for (i += 2; i < lines.length && MD_ROW.test(lines[i]); i++) rows.push(mdRow(lines[i], "td"));
                out.push('<div class="bv-md-tbl"><table><thead>' + mdRow(ln, "th") + "</thead><tbody>" + rows.join("") + "</tbody></table></div>");
                continue;
            }
            if ((m = MD_ITEM.exec(ln))) {
                var tag = /\d/.test(m[1]) ? "ol" : "ul";
                if (list !== tag) { closeList(); out.push("<" + tag + ">"); list = tag; }
                // 번호 목록은 원문 번호를 글자로 둔다(빈 줄·하위 목록으로 끊겨도 번호가 다시 1부터 시작하지 않게)
                out.push("<li>" + (tag === "ol" ? m[1] + " " : "") + mdInline(m[2]) + "</li>");
            } else {
                closeList();
                if ((m = MD_HEAD.exec(ln))) {
                    heads.push(mdInline(m[2]));
                    out.push('<div class="bv-md-h">' + heads[heads.length - 1] + "</div>");
                } else if (ln.trim()) {
                    out.push("<div>" + mdInline(ln) + "</div>");
                }
            }
            i++;
        }
        closeList();
        if (!out.length) return "";
        var toc = heads.length < MD_TOC_MIN ? "" : '<div class="bv-md-toc"><div class="bv-label">' + escapeHtml(NARR_TEXT.toc) +
            "</div><ul>" + heads.map(function (h) { return "<li>" + h + "</li>"; }).join("") + "</ul></div>";
        return '<div class="bv-md">' + toc + out.join("") + "</div>";
    }

    // HolmesGPT 칸 [서술 보기](summaryView 켜짐) — 원인·근거·대응·한계 4줄(없는 줄 생략 · 대응은 「권고 없음」 표지 제외)
    // 다음에 원문을 다시 접어 둔다. 원문이 없으면 첫 줄 summary + 중립 안내(이유는 단정하지 않는다 — 저장 상한·옛 레코드).
    function deepNarrativeHtml(dd, hgSide) {
        var R = NARR_TEXT.rows;
        var rec = isObj(dd.recommendation) ? dd.recommendation : {};
        var rows = [
            [R.cause, [textOf(dd.cause)]],
            [R.basis, asList(hgSide.basis).map(textOf)],
            [R.action, asList(rec.items).map(textOf).filter(isAction)],
            [R.limits, (Array.isArray(dd.limitations) ? dd.limitations : [dd.limitations]).map(textOf)],
        ].map(function (r) {
            var t = r[1].filter(Boolean).join(" · ");
            return t ? '<div class="bv-item"><div class="bv-label">' + escapeHtml(r[0]) + '</div><div class="bv-text">' + mdInline(escapeHtml(t)) + '</div></div>' : "";
        }).join("");
        var raw = textOf(dd.narrative);
        var short = textOf(dd.summary);
        var ai = '<span class="bv-ai">' + escapeHtml(PB_TEXT.aiBadge) + '</span>';
        var body = raw
            ? '<details class="bv-md-raw" data-bv-key="narr-hg-raw"><summary>' + escapeHtml(NARR_TEXT.raw) + ' ' + ai + '</summary>' +
                renderMarkdownSafe(raw) + '</details>'
            : (short ? '<div class="bv-md-short">' + ai + renderMarkdownSafe(short) + '</div>' : "") +
                '<div class="bv-meta">' + escapeHtml(NARR_TEXT.noRaw) + '</div>';
        if (!rows && !raw && !short) return "";
        return '<details class="bv-engine-narr" data-bv-key="narr-hg"><summary>' + escapeHtml(PB_TEXT.engineNarrative) + '</summary>' +
            (rows ? '<div class="bv-md-sum">' + rows + '</div>' : "") + body + '</details>';
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
        var terms = !!extra.summaryView;   // (plans/199 W5b) 켜짐이면 계층 enum → 원인 영역 문구(LAYER_LABELS)
        var pbCol = {
            cause: listCell(hyps.length ? [String(hyps[0].title || "") + (hyps[0].status_label ? " [" + hyps[0].status_label + "]" : "")] : []),
            basis: listCell(asList(pbSide.basis).map(textOf).concat(
                hyps.length && asList(hyps[0].evidence_steps).length ? [PB_TEXT.evidence + " " + asList(hyps[0].evidence_steps).join(", ")] : [])
                .concat(pbSide.layer ? [PB_TEXT.engineLayers + " " + (terms ? layerLabel(String(pbSide.layer)) : pbSide.layer)] : [])),
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
                    .concat(asList(hgSide.layers).length ? [PB_TEXT.engineLayers + " " + (terms ? asList(hgSide.layers).map(function (l) {
                        return layerLabel(String(l));
                    }) : asList(hgSide.layers)).join(", ")] : [])
                    .concat(tc ? [fill(PB_TEXT.engineToolCalls, { n: tc })] : [])),
                action: listCell(asList(rec.items).slice(0, 3).map(textOf)),
                open: listCell(Array.isArray(dd.limitations) ? dd.limitations.map(textOf) : [textOf(dd.limitations)]),
            };
            // narrative = 심층 답변 원문(D-325 부기) · 옛 레코드는 첫 줄 summary만 있다
            hgCol = engineColumn(dd.label || PB_TEXT.engineHolmes, hcol, textOf(dd.narrative) || dd.summary, true,
                extra.summaryView ? deepNarrativeHtml(dd, hgSide) : undefined);
        }
        var head = '<div class="bv-engine-head"><span class="bv-label">' + escapeHtml(PB_TEXT.engineHead) + '</span>';
        if (AGREEMENT[ea.result]) head += chipHtml(AGREEMENT[ea.result][0], ea.label || AGREEMENT[ea.result][1]);
        else if (running) head += chipHtml("running", "HolmesGPT: 진행 중");
        if (ea.result) head += '<span class="bv-meta">' + escapeHtml(PB_TEXT.engineApprox) + '</span>';
        if (ea.badge) head += chipHtml("warn", ea.badge);
        head += '</div>';
        var notes = [ea.note, ea.reason].filter(Boolean).map(function (n) { return '<div class="bv-note">' + escapeHtml(n) + '</div>'; }).join("");
        // (plans/199 W4) 플레이북 칸 서술은 종합 블록 AI 소견과 같은 문장이라 summaryView 켜짐이면 원문 대신 안내 1줄
        var pbNarr = !extra.summaryView ? undefined : syn.narrative
            ? '<div class="bv-meta">' + escapeHtml(fill(NARR_TEXT.seeSynthesis, { head: PB_TEXT.summaryHead })) + '</div>' : "";
        return '<div class="bv-pb-block bv-engine">' + head + notes +
            '<div class="bv-engine-grid">' + engineColumn(PB_TEXT.enginePlaybook, pbCol, syn.narrative, false, pbNarr) + hgCol + '</div>' +
            (running ? "" : engineChoiceHtml(view, extra)) + '</div>';
    }

    // narrHtml(plans/199 W4 · summaryView 켜짐에서만 문자열)이 오면 종전 [서술 보기] 대신 그것을 쓴다 — 없으면 종전 그대로.
    function engineColumn(title, col, narrative, isHypo, narrHtml) {
        var R = PB_TEXT.engineRows;
        return '<div class="bv-engine-col"><div class="bv-engine-title">' + escapeHtml(title) +
            (isHypo ? ' <span class="bv-badge bv-badge--hypo">가설</span>' : "") + '</div>' +
            ["cause", "basis", "action", "open"].map(function (k) {
                return '<div class="bv-item"><div class="bv-label">' + escapeHtml(R[k]) + '</div><div class="bv-text">' + col[k] + '</div></div>';
            }).join("") +
            (narrHtml !== undefined ? narrHtml : narrative ? '<details class="bv-engine-narr" data-bv-key="narr-' + (isHypo ? "hg" : "pb") + '"><summary>' +
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

    // 단계 섹션(아코디언 · 이상 단계 기본 펼침) + 진행 중 스켈레톤
    // terms = 용어 정리(plans/199 W5b · summaryView 켜짐의 ③ 상세에서만 참)
    function stepsDetailHtml(ctx, terms) {
        var readings = readingsOf(ctx.pb);
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.stepsDetailHead) + '</div>' +
            stepSections(ctx.pb).map(function (sec) { return stepSectionHtml(sec, readings, terms); }).join("") + skeletonSteps(ctx) + '</div>';
    }

    // 실행 메모(없으면 빈 문자열)
    function notesHtml(ctx) {
        var notes = asList(ctx.pb.notes);
        if (!notes.length) return "";
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(PB_TEXT.notesHead) + '</div>' +
            notes.map(function (n) { return '<div class="bv-meta">' + escapeHtml(textOf(n)) + '</div>'; }).join("") + '</div>';
    }

    // ── plans/199 W3: ② 근거 요약 · ③ 상세 ───────────────────────────────────────────────────

    // S번호 자연 정렬 — 숫자 부분 → 접미사(`S0` < `S0r` < `S1` < `S2` < `S2.pool` < `S10`). 번호 없는 단계는 뒤(원래 순서).
    var STEP_NO = /^S?(\d+)(.*)$/i;
    function stepOrder(a, b) {
        var ma = STEP_NO.exec(String(a.step || ""));
        var mb = STEP_NO.exec(String(b.step || ""));
        if (!ma || !mb) return (ma ? 0 : 1) - (mb ? 0 : 1);
        return Number(ma[1]) - Number(mb[1]) || (ma[2] < mb[2] ? -1 : ma[2] > mb[2] ? 1 : 0);
    }

    // (a) 단계 판정 스트립(머리 「단계별 판정」 · W5b) — S번호 순 · 사람 말 라벨(단계 이름 + 판정 칩 문구) · 번호는 꼬리 작은 글씨 ·
    // 누르면 ③을 열고 그 단계(`data-bv-step`)로 간다(attach `goto-step`).
    function stepStripHtml(ctx) {
        var readings = readingsOf(ctx.pb);
        var secs = stepSections(ctx.pb);
        var chips = secs.slice().sort(stepOrder).map(function (sec) {
            var c = stepChip(sec, readings);
            return '<button type="button" class="bv-step-chip bv-step-chip--' + c.kind + ' bv-strip-chip" data-bv-action="goto-step"' +
                ' data-step="' + escapeHtml(sec.step || "") + '">' + escapeHtml([sec.title || sec.action || "", c.text].filter(Boolean).join(" ")) +
                ' <span class="bv-strip-no">' + escapeHtml(sec.step || "?") + '</span></button>';
        });
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(TERM_TEXT.stripHead) + '</div>' +
            '<div class="bv-steps bv-strip">' + chips.join("") + remainingChips(ctx, secs.length) + '</div></div>';
    }

    // (b) 미니 타임라인 재료 — 단계 표의 알람 기준 분(`t_offset_min` · `minutes_before_alarm`)만 쓴다(구조 필드만 ·
    // 서술 줄은 해석하지 않는다). 레인은 행의 kind·source에 apm·jennifer가 있으면 APM, 그 열이 비면 단계가 `apm.*`인지로 정한다.
    var TL_TEXT_COLS = ["detail", "description", "alarm_name", "name"];
    function timelinePoints(ctx) {
        var pts = [];
        stepSections(ctx.pb).forEach(function (sec) {
            var t = sec.table;
            if (!isObj(t) || !Array.isArray(t.columns)) return;
            var cols = t.columns.map(String);
            var oi = cols.indexOf("t_offset_min");
            var bi = cols.indexOf("minutes_before_alarm");
            if (oi < 0 && bi < 0) return;
            var ti = TL_TEXT_COLS.map(function (c) { return cols.indexOf(c); }).filter(function (i) { return i >= 0; })[0];
            asList(t.rows).forEach(function (row) {
                if (isObj(row)) row = cols.map(function (c) { return row[c]; });
                if (!Array.isArray(row)) return;
                var m = oi >= 0 ? num(row[oi]) : null;
                if (m === null && bi >= 0 && num(row[bi]) !== null) m = -num(row[bi]);
                if (m === null) return;
                var tag = ["kind", "source"].map(function (c) {
                    var i = cols.indexOf(c);
                    return i >= 0 ? textOf(row[i]) : "";
                }).join(" ");
                var apm = tag.trim() ? /apm|jennifer/i.test(tag) : /^apm\./.test(String(sec.action || ""));
                pts.push({ lane: apm ? "apm" : "infra", m: m, step: String(sec.step || ""),
                    text: ti === undefined ? "" : textOf(row[ti]) });
            });
        });
        return pts;
    }

    function offsetText(m) {
        return m === 0 ? "알람 시각" : "알람 " + fmtNum(Math.abs(m)) + "분 " + (m < 0 ? "전" : "후");
    }

    // 인라인 SVG(외부 라이브러리 없음 · 색은 CSS 테마 토큰). 속성값은 숫자·고정 class만 — 사용자 텍스트는
    // 이스케이프한 `<text>`·`<title>` 안에만 둔다. 점이 2개 미만이면 그리지 않는다.
    var TL = { w: 600, h: 66, left: 52, right: 14, axis: 62, lanes: { infra: 20, apm: 42 } };
    function timelineHtml(ctx) {
        var pts = timelinePoints(ctx);
        if (pts.length < 2) return "";
        var lo = 0, hi = 0;
        pts.forEach(function (p) { lo = Math.min(lo, p.m); hi = Math.max(hi, p.m); });
        var dLo = lo === hi ? -1 : lo, dHi = lo === hi ? 1 : hi;
        function x(m) { return (TL.left + ((m - dLo) / (dHi - dLo)) * (TL.w - TL.left - TL.right)).toFixed(1); }
        var parts = ['<title>' + escapeHtml(fill(EV_TEXT.timelineTitle, { n: pts.length })) + '</title>'];
        Object.keys(TL.lanes).forEach(function (lane) {
            var y = TL.lanes[lane];
            parts.push('<line class="bv-tl-lane" x1="' + TL.left + '" y1="' + y + '" x2="' + (TL.w - TL.right) + '" y2="' + y + '"></line>' +
                '<text class="bv-tl-label" x="4" y="' + (y + 4) + '">' + escapeHtml(EV_TEXT.lanes[lane]) + '</text>');
        });
        var x0 = x(0);
        parts.push('<line class="bv-tl-zero" x1="' + x0 + '" y1="8" x2="' + x0 + '" y2="' + (TL.axis - 10) + '"></line>' +
            '<text class="bv-tl-tick" x="' + x0 + '" y="' + TL.axis + '" text-anchor="middle">' + escapeHtml(EV_TEXT.alarmMark) + '</text>');
        [[lo, "start"], [hi, "end"]].forEach(function (t) {
            if (t[0] === 0) return;
            parts.push('<text class="bv-tl-tick" x="' + x(t[0]) + '" y="' + TL.axis + '" text-anchor="' + t[1] + '">' +
                escapeHtml(fill(EV_TEXT.minutes, { m: (t[0] > 0 ? "+" : "-") + fmtNum(Math.abs(t[0])) })) + '</text>');
        });
        pts.forEach(function (p) {
            parts.push('<circle class="bv-tl-dot bv-tl-dot--' + p.lane + '" cx="' + x(p.m) + '" cy="' + TL.lanes[p.lane] + '" r="4"><title>' +
                escapeHtml([offsetText(p.m), p.step, p.text].filter(Boolean).join(" · ")) + '</title></circle>');
        });
        var win = isObj(ctx.pb.windows) ? ctx.pb.windows : {};
        return '<div class="bv-pb-block"><div class="bv-label">' + escapeHtml(EV_TEXT.timelineHead) + '</div>' +
            '<svg class="bv-tl" viewBox="0 0 ' + TL.w + ' ' + TL.h + '" role="img">' + parts.join("") + '</svg>' +
            (win.alarm_time ? '<div class="bv-meta">' + escapeHtml(fill(EV_TEXT.timelineAt, { at: fmtWall(win.alarm_time) })) + '</div>' : "") +
            '</div>';
    }

    // (c) 가설 표 — 지지·판단 보류·반박 3칸(칸 안은 순위 순). 상태를 모르는 가설은 「판단 보류」 칸에 원 상태 배지와 함께
    // (단정 금지 · 침묵 금지). 가설 코드(`rule_id`)는 운영자 뷰에만. 198 I-3(가설 신뢰도)이 오면 이 자리
    // (`data-bv-slot="hypothesis"`)를 그 카드로 바꾼다 — 점수·신뢰도는 여기서 만들지 않는다(D-351 ⑧).
    var HYPO_STATUS_LABELS = { supported: "지지", inconclusive: "판단 보류", refuted: "반박" };
    function hypoLabel(h) {
        return String(h.status_label || HYPO_STATUS_LABELS[h.status] || "");
    }

    function hypoItemHtml(h, raw) {
        var known = Object.prototype.hasOwnProperty.call(HYPO_KINDS, hypoLabel(h));
        var links = asList(h.evidence_steps).map(function (s) {
            return '<a href="#" class="bv-step-link" data-bv-action="goto-step" data-step="' + escapeHtml(s) + '">' + escapeHtml(s) + '</a>';
        }).join(" ");
        return '<li class="bv-ev-hitem"><span class="bv-mono">' + escapeHtml(h.rank || "") + '</span> ' + escapeHtml(h.title || "") +
            (known ? "" : ' ' + hypoBadge(h)) +
            (raw && h.rule_id ? ' <span class="bv-meta bv-mono bv-ev-code">' + escapeHtml(h.rule_id) + '</span>' : "") +
            (links ? '<div class="bv-meta">' + escapeHtml(PB_TEXT.evidence) + ' ' + links + '</div>' : "") + '</li>';
    }

    function hypoGridHtml(view, ctx) {
        var hyps = asList(synthesisOf(ctx.pb).hypotheses).filter(isObj);
        var raw = isRawView(view);
        var hold = EV_TEXT.hypoColumns[1][0];
        var body = !hyps.length
            ? '<div class="bv-note">' + escapeHtml(ctx.partial ? PB_TEXT.synthesisPending : PB_TEXT.noHypothesis) + '</div>'
            : '<div class="bv-ev-hypo">' + EV_TEXT.hypoColumns.map(function (col) {
                var items = hyps.filter(function (h) {
                    var label = hypoLabel(h);
                    return (Object.prototype.hasOwnProperty.call(HYPO_KINDS, label) ? label : hold) === col[0];
                });
                return '<div class="bv-ev-hcol"><div class="bv-ev-hcol-head"><span class="bv-hypo bv-hypo--' + col[1] + '">' +
                    escapeHtml(col[0]) + '</span> <span class="bv-meta">' + items.length + '</span></div>' +
                    (items.length ? '<ol class="bv-ev-hlist">' + items.map(function (h) { return hypoItemHtml(h, raw); }).join("") + '</ol>'
                        : '<div class="bv-meta">—</div>') + '</div>';
            }).join("") + '</div>';
        return '<div class="bv-pb-block" data-bv-slot="hypothesis"><div class="bv-label">' + escapeHtml(PB_TEXT.hypoHead) + '</div>' +
            body + '</div>';
    }

    // 한계 전부 — 브리핑 최상위(운영자 뷰에만 실린다) + 심층 조사. 같은 문장은 한 번만(순서 유지).
    function limitationsOf(b) {
        var dd = isObj(b.deep_dive) ? b.deep_dive : {};
        var seen = {};
        return [b.limitations, dd.limitations].reduce(function (acc, v) {
            return acc.concat(Array.isArray(v) ? v : [v]);
        }, []).map(textOf).filter(function (t) {
            if (!t || Object.prototype.hasOwnProperty.call(seen, t)) return false;
            seen[t] = true;
            return true;
        });
    }

    var DEEP_GAPS = { failed: true, timeout: true, skipped: true };   // 담당자용 꼭 확인에 사유를 올리는 심층 조사 상태

    // (e) 담당자용 꼭 확인(§3.3 오른쪽 열) — 엔진 불일치 요지 · 심층 조사 실패·생략 사유 · 검토 기한 지난 런북 · 한계 전부 ·
    // 알려진 문제 상세(피드백 3버튼 조건은 knownErrorsHtml 그대로). 해당 없으면 칸을 그리지 않는다(빈 경고 칸 금지).
    function ownerMustHtml(view, ctx, extra) {
        var b = ctx.b;
        var items = [];
        var ea = isObj(b.engine_agreement) ? b.engine_agreement : {};
        if (ea.result === "different_layer") {
            var pbLayer = isObj(ea.playbook) && ea.playbook.layer ? layerLabel(String(ea.playbook.layer)) : "";
            var hgLayers = isObj(ea.holmesgpt) ? asList(ea.holmesgpt.layers).map(function (l) { return layerLabel(String(l)); }) : [];
            items.push(fill(EV_TEXT.mustEngine, { pb: pbLayer || "—", hg: hgLayers.join(", ") || "—" }));
        }
        var dd = isObj(b.deep_dive) ? b.deep_dive : null;
        if (dd && DEEP_GAPS[dd.status]) {
            // 사유가 이미 「심층 조사 실패 — …」 꼴이면 머리를 겹쳐 쓰지 않는다
            var deepHead = fill(EV_TEXT.mustDeep, { status: DEEP_STATUS[dd.status][1] });
            var deepReason = textOf(dd.reason);
            items.push(deepReason.indexOf(deepHead) === 0 ? deepReason : deepHead + (deepReason ? " — " + deepReason : ""));
        }
        var rb = runbookOf(b);
        if (rb && rb.stale) {
            items.push(EV_TEXT.mustRunbook + (rb.last_reviewed ? fill(EV_TEXT.reviewed, { at: textOf(rb.last_reviewed) }) : ""));
        }
        var limits = limitationsOf(b);
        var kes = knownErrorsHtml(b, view, extra);
        if (!items.length && !limits.length && !kes) return "";
        return '<div class="bv-pb-block bv-ev-must"><div class="bv-label">' + escapeHtml(EV_TEXT.mustHead) + '</div>' +
            (items.length ? '<ul class="bv-sum-must">' + items.map(function (t) {
                return '<li class="bv-sum-must-item bv-sum-must-item--warn">' + escapeHtml(t) + '</li>';
            }).join("") + '</ul>' : "") +
            (limits.length ? '<div class="bv-label">' + escapeHtml(EV_TEXT.limitsHead) + '</div><ul class="bv-actions">' +
                limits.map(function (t) { return '<li>' + escapeHtml(t) + '</li>'; }).join("") + '</ul>' : "") +
            kes + '</div>';
    }

    // 접힌 단(②·③) — `data-bv-key`로 재조회 중 열림 유지(keepOpenState)와 펼침 기억(attach toggle)이 함께 잡는다
    function tierHtml(key, head, body, open) {
        return '<details class="bv-ev bv-ev--' + key + '" data-bv-key="' + key + '"' + (open ? " open" : "") + '>' +
            '<summary class="bv-ev-head">' + escapeHtml(head) + '</summary><div class="bv-ev-body">' + body + '</div></details>';
    }

    // 카드의 [근거 보기]·[자세히] — attach가 해당 단을 열고 그 자리로 스크롤한다(`data-bv-target`)
    function tierButtonsHtml() {
        return '<button type="button" class="bv-choice" data-bv-action="expand" data-bv-target="evidence">' +
            escapeHtml(EV_TEXT.evidenceButton) + '</button>' +
            '<button type="button" class="bv-choice" data-bv-action="expand" data-bv-target="detail">' +
            escapeHtml(EV_TEXT.detailButton) + '</button>';
    }

    // 상세형 3단(summaryView 켜짐) — 카드는 renderPlaybook이 머리 아래에 이미 그렸다. 진행 중(부분 브리핑)도 같은 구조.
    // ② 단계 스트립 · 타임라인 · 가설 표 · 엔진 비교(1회) · 담당자용 꼭 확인 / ③ 메타·진척·대상 · 단계별 근거 · 종합의
    // 대응·추가 확인·AI 소견 · 런북 · 참고 문서 · 메모 · KB 초안·포스트모템(종전 조건 그대로).
    function renderPlaybookTiers(view, ctx, extra) {
        var b = ctx.b;
        var open = isObj(extra.expand) ? extra.expand : {};
        var evidence = [
            stepStripHtml(ctx),
            timelineHtml(ctx),
            hypoGridHtml(view, ctx),
            engineCompareHtml(view, ctx, extra),
            ownerMustHtml(view, ctx, extra),
        ];
        var detail = [
            '<div class="bv-pb-meta">' + playbookMeta(ctx) + '</div>',
            progressBar(ctx),
            targetsHtml(ctx.pb),
            stepsDetailHtml(ctx, true),
            synthesisHtml(ctx, true),
            runbookHtml(b),
            referencesHtml(b),
            notesHtml(ctx),
            kbTailHtml(view, extra),
        ];
        return tierHtml("evidence", EV_TEXT.evidenceHead, evidence.join(""), open.evidence === true) +
            tierHtml("detail", EV_TEXT.detailHead, detail.join(""), open.detail === true);
    }

    // 상세형 — 조사 대화 첫 고정 카드 · break-glass 드로어(§5.6 ③ 배치 1~7 순서 고정)
    function renderPlaybookDetail(view, ctx, extra) {
        // (plans/199 W3) 「한눈에 보기」 켜짐이면 ②·③ 접힘 배치 — 아래 종전 배치는 꺼짐에서만(비트 동일)
        if (extra.summaryView) return renderPlaybookTiers(view, ctx, extra);
        var b = ctx.b;
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
        parts.push(stepsDetailHtml(ctx));
        // 6. 참고 문서
        parts.push(referencesHtml(b));
        // 7. 꼬리 — 실행 메모
        parts.push(notesHtml(ctx));
        // 7. 꼬리 — KB 초안 · 포스트모템(extra.kb일 때만 · W9)
        parts.push(kbTailHtml(view, extra));
        return parts.join("");
    }

    function renderPlaybook(view, ctx, extra) {
        var chip = statusChip(view);
        var qd = queueOf(view, extra);
        if (qd) { chip.kind = "hold"; chip.text = qd.chip; }
        var parts = [renderHead(view, chip, extra), summaryBlock(view, extra, extra.mode === "detail")];
        if (qd && qd.note) parts.push('<div class="bv-meta">' + escapeHtml(qd.note) + '</div>');
        if (chip.reason) {
            parts.push('<div class="bv-reason' + (chip.kind === "fail" ? " bv-reason--fail" : "") + '">' +
                '사유: ' + escapeHtml(chip.reason) + '</div>');
        }
        if (isActive(view)) parts.push(runningNoteHtml(qd && typeof qd.runningText === "string" ? qd : extra));
        parts.push(extra.mode === "detail" ? renderPlaybookDetail(view, ctx, extra) : renderPlaybookSummary(view, ctx, extra));
        parts.push(analyzeBarHtml(view, extra));
        parts.push(followupHtml(view, extra));
        if (extra.note) parts.push('<div class="bv-note bv-note--warn">' + escapeHtml(extra.note) + '</div>');
        return '<div class="bv bv--playbook">' + parts.join("") + '</div>';
    }

    // (plans/194 W5 · U3) 대기 표시 — `extra.queue(view)`가 `{chip, note?, runningText?}`를 주면 상태 칩을
    // 「대기 중 · 순번 N」(hold)으로 바꾸고 머리 아래에 안내 1줄을 단다. 키가 없거나 null이면 종전 그대로다.
    function queueOf(view, extra) {
        if (!extra || typeof extra.queue !== "function" || !isActive(view)) return null;
        var qd = extra.queue(view);
        return isObj(qd) && qd.chip ? qd : null;
    }

    // 진행 중 안내 1줄. `extra.runningText`가 문자열이면 그 문구로 바꾸고 빈 문자열이면 줄을 뺀다
    // (장애 진단 실시간 화면 — plans/187 W15). 미지정이면 종전 문구 그대로다(비트 동일).
    function runningNoteHtml(extra) {
        var text = typeof extra.runningText === "string" ? extra.runningText : TEXT.runningText;
        return text ? '<div class="bv-note">' + escapeHtml(text) + '</div>' : "";
    }

    // 출처 배지(D-035) — 사용자가 요청한 조사(kind·origin `manual`)는 「자동 조사」라 쓰지 않는다(plans/194 W5)
    function sourceBadgeHtml(view) {
        var manual = !!view && (view.kind === "manual" || view.origin === "manual");
        return '<span class="bv-src">' + escapeHtml(manual ? TEXT.manualSourceBadge : TEXT.sourceBadge) + '</span>';
    }

    // 머리 판정 등급 — `verdict_level`은 중요도(해소·주의·경고·심각 · sre_agent `severity_signatures.LEVEL_NAMES`)라
    // (plans/199 W5b) 켜짐이면 브리핑 줄(noise_gate `investigation_briefing`)과 같은 「중요도」, 꺼짐이면 종전 「평가」
    function verdictLevelHtml(view, extra) {
        return (extra && extra.summaryView ? TERM_TEXT.levelHead : "평가") + " " + escapeHtml(view.verdict_level);
    }

    function renderHead(view, chip, extra) {
        var head = '<div class="bv-head">' +
            '<span class="bv-chip bv-chip--' + chip.kind + '">' + escapeHtml(chip.text) + '</span>' +
            sourceBadgeHtml(view);
        if (view.verdict_level) head += '<span class="bv-meta">' + verdictLevelHtml(view, extra) + '</span>';
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
     *     `engineChoice`(상세형 4택 표시) · `ui`(4택 상태) · `runningText`(진행 중 안내 대체 — 빈 문자열이면 생략)
     *     (plans/199 W1) `summaryView`(참이면 머리 바로 아래 「한눈에 보기」 카드 — 플레이북·비플레이북 공통,
     *     요약형은 「종합(플레이북)」 칸을 카드로 대신한다. 없거나 거짓이면 종전 출력과 바이트 동일)
     *     (plans/199 W2) `handoff`(참이면 카드에 전달문 버튼 — 클릭 처리기가 있는 attach만 싣는다)
     *     (plans/199 W3) summaryView 켜짐의 상세형은 ② 근거 요약 · ③ 상세 접힘 배치 — `expand`(`{evidence, detail}`
     *     불리언 · 참이면 그 단을 펼친 채 그린다 · 없으면 접힘)는 attach가 펼침 기억에서 읽어 싣는다
     */
    function renderBody(view, extra) {
        view = view || { status: "none" };
        var pbCtx = playbookContext(view);
        if (pbCtx) return renderPlaybook(view, pbCtx, extra || {});
        var chip = statusChip(view);
        // (plans/194 W5 · D-347) 수동 조사 모드의 조사 없음 — 「조사 안 됨」 · 출처 배지 없음 · 모드 안내
        var manualNone = !!(extra && extra.manualMode) && (view.status || "none") === "none";
        if (manualNone) chip.text = PB_TEXT.manualNoneChip;
        var qd = queueOf(view, extra);
        if (qd) { chip.kind = "hold"; chip.text = qd.chip; }
        var head = '<div class="bv-head">' +
            '<span class="bv-chip bv-chip--' + chip.kind + '">' + escapeHtml(chip.text) + '</span>' +
            (manualNone ? "" : sourceBadgeHtml(view));
        if (view.verdict_level) head += '<span class="bv-meta">' + verdictLevelHtml(view, extra) + '</span>';
        if (view.kind === "episode") head += '<span class="bv-meta">사건 종합 조사</span>';
        if (view.updated_at) head += '<span class="bv-meta">' + escapeHtml(fmtTime(view.updated_at)) + '</span>';
        head += '</div>';

        var parts = [head, summaryBlock(view, extra)];
        if (qd && qd.note) parts.push('<div class="bv-meta">' + escapeHtml(qd.note) + '</div>');
        if (chip.reason) {
            parts.push('<div class="bv-reason' + (chip.kind === "fail" ? " bv-reason--fail" : "") + '">' +
                '사유: ' + escapeHtml(chip.reason) + '</div>');
        }
        var status = view.status || "none";
        if (status === "none") parts.push('<div class="bv-note">' + escapeHtml(manualNone ? PB_TEXT.manualNoneText : TEXT.noneText) + '</div>');
        if (isActive(view)) parts.push(runningNoteHtml(qd && typeof qd.runningText === "string" ? qd : (extra || {})));
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
     *   (plans/194 W5 · D-347 · 선택 키 — 없으면 종전과 같다)
     *   manualMode: true — 수동 조사 모드(자동 조사 꺼짐). 조사 없음이면 「조사 안 됨」 칩·모드 안내·[조사 시작],
     *     접수 결과는 `startOutcome`(서버 `reason_text` · 다음 행동 · 시간당 상한 잠금 · 합류 안내)로 보인다.
     *   onOpenInvestigation(id): 합류한 조사·같은 서버의 진행 중 조사로 가는 버튼(없으면 버튼 없음)
     *   openInvestigationLabel: 합류·진행 중 조사 버튼 문구(문자열 또는 함수 · 기본 「장애 진단에서 보기」)
     *   (plans/192 P8 · 선택 키 — 없으면 종전과 같다)
     *   onOpenPrevious(id): 「이전 분석」 각 행의 버튼 — 렌더 모드와 무관하게 그리고 행의 「조사 대화로 열기」를 대체
     *   openPreviousLabel: 그 버튼 문구(문자열 또는 함수 · 기본 「조사 대화로 열기」)
     *   (plans/199 W1 · 선택 키 — 없으면 종전과 같다)
     *   summaryView: 불리언 또는 함수 — 렌더할 때마다 평가해 참이면 `extra.summaryView`(「한눈에 보기」 카드).
     *     함수면 기능 조회가 늦게 와도 다음 렌더부터 맞는다. (plans/199 W2) 참이면 `extra.handoff`도 실어 카드에
     *     [전달문 복사]·[전달문 보기]를 그리고 이 패널이 복사를 처리한다(키가 있으면 클릭 처리기를 붙인다).
     *     (plans/199 W3) 상세형 ②·③의 펼침을 브라우저 저장소(`bv.expand.evidence`·`bv.expand.detail`)에 기억한다 —
     *     켜진 첫 렌더에 1회 읽어 `extra.expand`로 넘기고 toggle 때 쓴다. 저장소가 막히면 기본 접힘(이 패널 안에서만 유지).
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
        var fe2 = !!(opts.analyze || opts.followup || opts.keFeedback || opts.kb || opts.manualMode);
        // (plans/199 W3) ②·③ 펼침 — 이 패널이 아는 값. 켜진 첫 렌더에 저장소에서 1회 읽고 toggle로 갱신한다.
        var expand = null;

        function expandNow() {
            if (!expand) expand = { evidence: readExpand("evidence"), detail: readExpand("detail") };
            return { evidence: expand.evidence, detail: expand.detail };
        }

        function extraFor(info) {
            var extra = info && info.limited ? { note: fill(TEXT.limitReached, { s: limitS }) } : null;
            // (plans/199 W1) 카드 켜짐은 렌더 시점 값 — 거짓이면 키를 싣지 않는다(종전 extra 그대로)
            var summary = typeof opts.summaryView === "function" ? !!opts.summaryView() : !!opts.summaryView;
            // (plans/199 W2) 카드가 켜지면 전달문 버튼도 — 이 패널에는 그 클릭 처리기가 있다(`handoff`)
            if (!opts.render && !opts.engineChoice && !fe2) {
                if (summary) {
                    extra = extra || {};
                    extra.summaryView = true; extra.handoff = true; extra.ui = ui; extra.expand = expandNow();
                }
                return extra;
            }
            var merged = {};
            if (summary) { merged.summaryView = true; merged.handoff = true; merged.expand = expandNow(); }
            Object.keys(opts.render || {}).forEach(function (k) { merged[k] = opts.render[k]; });
            if (extra) merged.note = extra.note;
            merged.engineChoice = !!opts.engineChoice;
            merged.ui = ui;
            if (opts.analyze) merged.analyze = true;
            if (opts.followup) merged.followup = true;
            if (opts.keFeedback) merged.keFeedback = true;
            if (opts.kb) merged.kb = { operator: !!opts.kb.operator };
            if (opts.manualMode) merged.manualMode = true;
            if (opts.onOpenPrevious) {
                var pl = opts.openPreviousLabel;
                merged.previousLabel = String((typeof pl === "function" ? pl() : pl) || PB_TEXT.openConversation);
            }
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
        // (plans/199 W3) ②·③(`evidence`·`detail`)도 같다 — 우선순위는 지금 화면의 열림(이 함수) > 이 패널의 펼침 값
        // (`extra.expand` · 첫 렌더는 저장값) > 기본 접힘. 펼침 값은 toggle마다 따라가므로 둘은 보통 같다.
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
            if (opts.manualMode) a.go = null;
            repaint();
            call(opts.analyze.submit).then(function (res) {
                var body = res.body || {};
                if (isFlagOff(res)) { a.off = PB_TEXT.analyzeOff; return; }
                if (opts.manualMode && manualOutcome(a, res)) return;
                if (!res.ok) { a.msg = fill(PB_TEXT.analyzeFailed, { reason: reasonOf(res) }); a.warn = true; return; }
                var id = String(body.investigation_id || "");
                a.again = true;
                if (a.joinedMsg) {
                    a.msg = a.joinedMsg;
                    a.joinedMsg = "";
                } else if (body.status === "rejected") {
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
                ui.handoff = null;
                poller.start();
            }, function (err) {
                a.msg = fill(PB_TEXT.analyzeFailed, { reason: failText(err) });
                a.warn = true;
            }).then(function () { a.pending = false; repaint(); });
        }

        // (plans/194 W5 · U2) 수동 조사 모드의 접수 결과 — 거부·실패는 사유 한 줄 + 다음 행동을 남기고 끝낸다(true).
        // 거부는 거부 레코드로 갈아타지 않는다(「조사 안 됨」 + 버튼을 그대로 둔다). 접수·합류는 false(종전 흐름이
        // 그 조사로 갈아탄다) — 합류면 안내 문구와 그 조사로 가는 버튼을 미리 정해 둔다.
        function manualOutcome(a, res) {
            var r = startOutcome(res, Date.now(), {
                failed: PB_TEXT.analyzeFailed, rejected: PB_TEXT.analyzeRejected, joined: PB_TEXT.manualJoined,
            });
            if (r.kind === "accepted") return false;
            if (r.kind === "joined") {
                a.joinedMsg = r.text;
                if (opts.onOpenInvestigation) a.go = { id: r.investigationId, label: openLabel() };
                return false;
            }
            a.msg = r.text;
            a.warn = true;
            if (r.kind === "in_flight" && r.existingId && opts.onOpenInvestigation) {
                a.go = { id: r.existingId, label: openLabel() };   // (plans/192 W2) 합류와 같은 목적지 문구
            } else if (r.kind === "retry_later") {
                a.lockUntil = r.retryAt;
                setTimeout(repaint, Math.max(0, r.retryAt - Date.now()) + 50);
            }
            return true;
        }

        function openLabel() {
            var l = opts.openInvestigationLabel;
            return String((typeof l === "function" ? l() : l) || PB_TEXT.manualOpen);
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

        // (plans/199 W2) [전달문 복사] — 그린 카드와 같은 뷰·모델로 다시 만든다(DOM을 읽지 않는다). 실패·미지원이면
        // copyKb와 같은 안내를 보이고 [전달문 보기]를 펼쳐 둔다(직접 선택해 복사 — keepOpenState가 열림을 유지).
        // 대기 중 카드에는 이 버튼이 없다(summaryBlock) — 대기 문구를 입힐 일이 없다.
        function copyHandoff() {
            var h = ui.handoff = {};
            var text = handoffOf(last.view, summaryModel(last.view)).text;
            var clip = typeof navigator !== "undefined" && navigator.clipboard;
            Promise.resolve()
                .then(function () {
                    if (!clip || !clip.writeText) throw new Error("clipboard");
                    return clip.writeText(text);
                })
                .then(function () { h.msg = PB_TEXT.copied; }, function () {
                    h.msg = PB_TEXT.copyFailed;
                    var d = container.querySelector ? container.querySelector('details[data-bv-key="handoff"]') : null;
                    if (d) d.open = true;
                })
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

        // (plans/199 W3) ②·③ 펼침 기억 — <details>의 toggle은 거품이 오르지 않아 캡처로 받는다. 이 패널이 아는 값과
        // 다를 때만 쓴다(다시 그리기·열림 유지 복원이 일으키는 toggle은 같은 값이라 쓰지 않는다). 클릭 처리기보다 먼저 붙인다.
        if (opts.summaryView && container.addEventListener) {
            container.addEventListener("toggle", function (e) {
                var d = e && e.target;
                var key = d && d.getAttribute ? d.getAttribute("data-bv-key") : null;
                if (!expand || !Object.prototype.hasOwnProperty.call(EXPAND_KEYS, key) || expand[key] === !!d.open) return;
                expand[key] = !!d.open;
                writeExpand(key, expand[key]);
            }, true);
        }

        if ((opts.render || opts.engineChoice || opts.onOpenConversation || fe2 || opts.summaryView) && container.addEventListener) {
            container.addEventListener("click", function (e) {
                var el = e.target && e.target.closest ? e.target.closest("[data-bv-action]") : null;
                if (!el || !container.contains(el)) return;
                var action = el.getAttribute("data-bv-action");
                if (action === "goto-step") {
                    e.preventDefault();
                    var step = el.getAttribute("data-step");
                    // (plans/199 W3) 단계 근거가 ③ 상세 안에 있으면 그 단부터 연다(없으면 종전 그대로)
                    var tier = container.querySelector ? container.querySelector('details[data-bv-key="detail"]') : null;
                    if (tier) tier.open = true;
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
                } else if (action === "open-investigation" && opts.onOpenInvestigation) {
                    opts.onOpenInvestigation(el.getAttribute("data-investigation-id"));
                } else if (action === "open-previous" && opts.onOpenPrevious) {
                    opts.onOpenPrevious(el.getAttribute("data-investigation-id"));
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
                } else if (action === "handoff-copy" && last) {
                    copyHandoff();
                } else if (action === "expand") {
                    // (plans/199 W3) [근거 보기]·[자세히] — 그 단을 열고 그 자리로(열림은 toggle이 기억한다)
                    var key = el.getAttribute("data-bv-target");
                    var d = Object.prototype.hasOwnProperty.call(EXPAND_KEYS, key) && container.querySelector
                        ? container.querySelector('details[data-bv-key="' + key + '"]') : null;
                    if (d) {
                        d.open = true;
                        if (d.scrollIntoView) d.scrollIntoView({ block: "start", behavior: "smooth" });
                    }
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
        // (plans/199 W5b) 용어 정리 — 문구 표 · 열 머리·셀 값 문구(장애 진단 「조사 흐름」 카드 표도 쓴다)
        TERM_TEXT: TERM_TEXT,
        TERM_COLUMNS: TERM_COLUMNS,
        METRIC_LABELS: METRIC_LABELS,
        KIND_LABELS: KIND_LABELS,
        termColumn: termColumn,
        termValue: termValue,
        // (plans/199 W4) [서술 보기] 원문 안전 렌더 · 문구
        NARR_TEXT: NARR_TEXT,
        // (plans/199 W3) 상세형 ②·③ 문구
        EV_TEXT: EV_TEXT,
        renderMarkdownSafe: renderMarkdownSafe,
        // (plans/199 W1) 「한눈에 보기」 — 모델(순수) · 카드 HTML · 원인 영역 문구 표 · 문구
        SUM_TEXT: SUM_TEXT,
        NO_ACTION_PREFIX: NO_ACTION_PREFIX,
        LAYER_LABELS: LAYER_LABELS,
        summaryModel: summaryModel,
        summaryCardHtml: summaryCardHtml,
        // (plans/199 W2) 전달문 — 양식(순수) · 운영자 뷰(원문) 판정 · 문구
        HANDOFF_TEXT: HANDOFF_TEXT,
        handoffText: handoffText,
        isRawView: isRawView,
        hasPlaybook: function (view) { return !!playbookContext(view); },
        playbookScope: function (view) {
            var ctx = playbookContext(view);
            return ctx && isObj(ctx.pb.scope) ? ctx.pb.scope : null;
        },
        setBusy: setBusy,
        responseOf: responseOf,
        isFlagOff: isFlagOff,
        reasonOf: reasonOf,
        startOutcome: startOutcome,
        startBlockHtml: startBlockHtml,
        startMsgHtml: startMsgHtml,
        createPoller: createPoller,
        attach: attach,
    };
    root.BriefingView = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
