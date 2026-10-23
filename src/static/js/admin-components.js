/**
 * 관리자 「환경변수 설정」 탭 — 분리 컴포넌트 대상 선택·카드·저장 배너 (plans/191 W2 · D-345).
 *
 * `COMPONENT_ADMIN_ENDPOINTS`에 등록된 컴포넌트(sre_agent·apm_gateway·mcp_server·alarm_server·
 * fabrix_proxy)가 있으면 설정 탭 위에 대상 선택을 그린다. 0개면 아무것도 그리지 않는다(현행 화면 동일).
 * 목록·위젯·diff 모달은 admin.js(window.AdminSettings)를 그대로 쓰고, 여기서는 대상 선택과 카드,
 * 저장 후 「재기동 대기」 배너만 맡는다. 컴포넌트가 준 문자열은 모두 textContent로만 넣는다(XSS).
 *
 * 순수 함수(restartBannerText·failText·ignoredReasonText·cardRows)는 node에서 require로 시험한다.
 */
(function (root) {
    "use strict";

    var API = "/api/v1/admin/components";

    var REASON_TEXTS = {
        connect_error: "연결 실패",
        timeout: "응답 시간 초과",
        unauthorized: "관리 토큰 불일치",
        not_enabled: "관리 API 꺼짐",
        bad_response: "응답 형식 오류",
        http_error: "오류 응답",
    };

    var IGNORED_TEXTS = {
        secret: "시크릿 — 서버 파일을 직접 편집하세요",
        masked: "마스킹 값이 그대로 전송됨(원값 보존)",
        readonly: "읽기 전용 항목",
        override_os: "OS 환경변수가 덮어써 저장해도 반영되지 않음",
        override_encenv: ".encenv가 덮어써 저장해도 반영되지 않음",
        override_dotenv: "우선하는 .env가 덮어써 저장해도 반영되지 않음",
    };

    var ROLE_TEXTS = { write_target: "쓰기 대상", override: "우선 적용", readonly: "읽기 전용", fallback: "보충(쓰기 대상이 우선)" };

    function restartBannerText(keys, hint) {
        if (!Array.isArray(keys) || !keys.length) return "";
        var command = hint ? String(hint) : "컴포넌트 기동 명령";
        return "재기동 대기: " + keys.length + "키 — " + command + "로 재기동하세요";
    }

    // 401(로그인 필요) · 403(권한 없음) · 그 외(컴포넌트 사유)를 나눠 말한다.
    function failText(status, body) {
        if (status === 401) return "로그인이 필요합니다(인증 토큰 없음 또는 만료).";
        if (status === 403) return "관리자 권한이 필요합니다.";
        body = body || {};
        if (status === 404) return "설정되지 않은 컴포넌트입니다.";
        if (status === 409 && body.error === "write_blocked") {
            return "이 컴포넌트는 쓰기가 막혀 있습니다" + (body.reason ? " — " + String(body.reason) : "") + ".";
        }
        if (body.reason && REASON_TEXTS[body.reason]) {
            return REASON_TEXTS[body.reason] + (body.message ? " — " + String(body.message) : "");
        }
        if (Array.isArray(body.errors) && body.errors.length) {
            return "저장하지 못했습니다 — 오류 " + body.errors.length + "건(항목 표시 참조).";
        }
        return "컴포넌트 요청에 실패했습니다(HTTP " + status + ").";
    }

    function ignoredReasonText(reason) {
        return IGNORED_TEXTS[reason] || String(reason || "");
    }

    // 컴포넌트 400의 errors[]({key, message} · key 없는 전역 오류 가능)를 배너 줄로 바꾼다.
    function errorLines(body) {
        var errors = body && Array.isArray(body.errors) ? body.errors : [];
        return errors.map(function (error) {
            error = error || {};
            var message = String(error.message || "알 수 없는 오류");
            return error.key ? String(error.key) + ": " + message : "전체: " + message;
        });
    }

    // 저장 성공 뒤 배너 줄 — 재기동 대기 · 저장하지 않은 항목 · 감사 기록 실패 경고.
    function savedLines(data, hint) {
        data = data || {};
        var lines = [];
        var restart = restartBannerText(data.restart_pending_keys, hint);
        if (restart) lines.push(restart);
        var ignored = Array.isArray(data.ignored_keys) ? data.ignored_keys : [];
        if (ignored.length) {
            lines.push("저장하지 않은 항목:");
            ignored.forEach(function (entry) {
                entry = entry || {};
                lines.push("· " + String(entry.key || "") + " — " + ignoredReasonText(entry.reason));
            });
        }
        if (data.audit_logged === false) {
            lines.push("⚠ 컴포넌트 감사 기록에 실패했습니다 — 저장은 유지되었습니다. 컴포넌트 로그를 확인하세요.");
        }
        if (data.proxy_audit_logged === false) {
            lines.push("⚠ 본체 감사 기록 불가 — 감사 저장소 미구성");
        }
        return lines;
    }

    function fmtTime(value) {
        if (!value) return "-";
        var d = new Date(value);
        if (isNaN(d.getTime())) return String(value);
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    // 카드 표의 [항목, 값] 문자열 쌍 — 상태(§2.3)·스키마(§2.1) 어느 응답이든 받는다.
    function cardRows(data) {
        data = data || {};
        var comp = data.component || {};
        var rows = [];
        rows.push(["버전", comp.version ? String(comp.version) : "-"]);
        rows.push(["기동 시각", fmtTime(comp.started_at) + (comp.pid ? " (PID " + comp.pid + ")" : "")]);
        if (Array.isArray(data.sources)) {
            rows.push(["읽는 파일", data.sources.map(function (src) {
                src = src || {};
                return String(src.path || "") + " (" + (ROLE_TEXTS[src.role] || String(src.role || ""))
                    + (src.exists === false ? " · 없음" : "") + ")";
            }).join(" · ") || "-"]);
        }
        rows.push(["쓰기", data.writable === false
            ? "불가" + (data.write_block_reason ? " — " + String(data.write_block_reason) : "")
            : "가능"]);
        var pending = Array.isArray(data.restart_pending_keys) ? data.restart_pending_keys : [];
        rows.push(["재기동 대기", pending.length ? pending.length + "키: " + pending.join(", ") : "없음"]);
        if (comp.restart_hint) rows.push(["재기동 명령", String(comp.restart_hint)]);
        return rows;
    }

    var api = {
        restartBannerText: restartBannerText,
        failText: failText,
        ignoredReasonText: ignoredReasonText,
        cardRows: cardRows,
        errorLines: errorLines,
        savedLines: savedLines,
    };
    if (typeof module === "object" && module.exports) {
        module.exports = api;
        return;
    }
    root.AdminComponents = api;

    // ── 화면 ─────────────────────────────────────────────

    var bar = document.getElementById("settingsTargetBar");
    var options = document.getElementById("settingsTargetOptions");
    var card = document.getElementById("componentCard");
    if (!bar || !options || !card || !root.AdminApi || !root.AdminSettings) return;

    var components = {};   // name -> 목록 행
    var current = null;    // null = 본체
    var restartHint = "";

    function node(tag, text, cls) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function renderCard(name, data, failure) {
        card.textContent = "";
        if (!name) {
            card.hidden = true;
            return;
        }
        card.hidden = false;
        var head = node("div", null, "component-card__head");
        head.appendChild(node("code", name, "component-card__name"));
        var ok = !failure;
        head.appendChild(node("span", ok ? "연결됨" : "연결 안 됨",
            "badge " + (ok ? "badge--reload" : "badge--override")));
        var pending = data && Array.isArray(data.restart_pending_keys) ? data.restart_pending_keys : [];
        if (ok && pending.length) {
            head.appendChild(node("span", "재기동 대기 " + pending.length + "키", "badge badge--restart"));
        }
        card.appendChild(head);
        if (failure) {
            card.appendChild(node("div", failure, "component-card__error"));
            return;
        }
        var table = node("dl", null, "component-card__rows");
        cardRows(data).forEach(function (row) {
            table.appendChild(node("dt", row[0]));
            table.appendChild(node("dd", row[1]));
        });
        card.appendChild(table);
    }

    function showBanner(lines) {
        var banner = document.getElementById("restartBanner");
        banner.textContent = "";
        lines.forEach(function (line) { banner.appendChild(node("div", line)); });
        banner.style.display = lines.length ? "block" : "none";
    }

    function renderSaved(data) {
        var saved = Array.isArray(data.saved_keys) ? data.saved_keys : [];
        showBanner(savedLines(data, restartHint));
        root.AdminApi.showSuccess(saved.length + "개 설정을 " + current + " 파일에 저장했습니다."
            + (data.audit_logged === false || data.proxy_audit_logged === false
                ? " (감사 기록 경고 — 배너 참조)" : ""));
    }

    function renderRejected(status, body) {
        showBanner([failText(status, body)].concat(errorLines(body)));
    }

    var hooks = {
        onSchema: function (data) {
            restartHint = data && data.component && data.component.restart_hint
                ? String(data.component.restart_hint) : "";
            renderCard(current, data, null);
        },
        onFailed: function (status, body) {
            var text = failText(status, body);
            renderCard(current, null, text);
            return text;
        },
        onSaved: renderSaved,
        onRejected: renderRejected,
        failText: failText,
    };

    function select(name) {
        if (name === current) return;
        if (root.AdminSettings.hasUnsavedEdits()
            && !window.confirm("저장하지 않은 변경이 있습니다. 대상을 바꾸면 사라집니다. 계속할까요?")) {
            return;
        }
        current = name;
        restartHint = "";
        options.querySelectorAll(".segment-option").forEach(function (option) {
            option.classList.toggle("selected", option.dataset.target === (name || ""));
        });
        var row = name ? components[name] : null;
        if (row && !row.reachable) renderCard(name, null, failText(502, row));
        else if (row) renderCard(name, row.status || {}, null);
        else renderCard(null);
        root.AdminSettings.setTarget(name, name ? hooks : null);
    }

    function addOption(label, name, reachable) {
        var option = node("button", label, "segment-option" + (name ? "" : " selected"));
        option.type = "button";
        option.dataset.target = name || "";
        if (name && !reachable) option.classList.add("segment-option--down");
        option.title = name ? (reachable ? "연결됨" : "연결 안 됨") : "본체(.env)";
        option.addEventListener("click", function () { select(name); });
        options.appendChild(option);
    }

    async function loadComponents() {
        try {
            var response = await root.AdminApi.request("GET", API);
            var data = await response.json().catch(function () { return {}; });
            if (!response.ok) {
                if (response.status !== 401) root.AdminApi.showError(failText(response.status, data));
                return;
            }
            var rows = Array.isArray(data.components) ? data.components : [];
            if (!rows.length) return;  // 기능 off — 현행 화면 그대로
            options.textContent = "";
            addOption("본체", null, true);
            rows.forEach(function (row) {
                components[row.name] = row;
                addOption(row.name + (row.reachable ? "" : " ⚠"), row.name, row.reachable);
            });
            bar.hidden = false;
        } catch (err) {
            root.AdminApi.showError("컴포넌트 목록을 불러오지 못했습니다.");
        }
    }

    loadComponents();
})(typeof window !== "undefined" ? window : this);
