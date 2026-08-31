/**
 * 운영자 대시보드 JavaScript
 *
 * 환경변수 설정, DB 연결 설정 관리를 담당한다.
 */

(function () {
    "use strict";

    // 통합 RBAC(D-069): 사용자 로그인 토큰(role==admin)으로도 어드민 진입을 허용한다.
    // break-glass 운영자 토큰(admin_token)이 있으면 우선, 없으면 사용자 토큰을 사용한다.
    var token = localStorage.getItem("admin_token") || localStorage.getItem("user_token");
    var alertError = document.getElementById("alertError");
    var alertSuccess = document.getElementById("alertSuccess");

    // --- 인증 확인 ---

    // Plan 59-a §3: 미인증 진입은 break-glass(/admin/login)가 아니라 정상 로그인(/login)으로 유도한다.
    // /admin/login은 DB 사용자를 인증하지 않으므로(운영자 env 계정 전용) 일반/관리자 계정이 로그인 실패한다.
    if (!token) {
        window.location.href = "/login?next=/admin";
        return;
    }

    // 토큰 유효성 확인
    verifyToken();

    async function verifyToken() {
        try {
            // require_admin_user로 보호된 엔드포인트로 검증(운영자 토큰·role==admin 사용자 모두 통과)
            var response = await apiRequest("GET", "/api/v1/admin/users");
            if (response.status === 403) {
                // 로그인은 됐으나 관리자 권한 없음(role==user) → 메인 화면으로
                window.location.href = "/";
                return;
            }
            if (!response.ok) {
                redirectUnauthenticated();
            }
        } catch (err) {
            redirectUnauthenticated();
        }
    }

    function redirectUnauthenticated() {
        // 미인증/만료 토큰 → 정상 로그인으로 유도(break-glass 아님)
        localStorage.removeItem("admin_token");
        window.location.href = "/login?next=/admin";
    }

    // --- 헬스 체크 ---

    checkHealth();

    function updateAdminTooltip(statusMap) {
        var tooltip = document.getElementById("adminStatusTooltip");
        if (!tooltip) return;
        var dbIds = Object.keys(statusMap);
        if (dbIds.length === 0) { tooltip.innerHTML = ""; return; }
        tooltip.innerHTML = dbIds.map(function (id) {
            var ok = statusMap[id];
            return '<div class="status-tooltip-item">' +
                '<span class="status-tooltip-dot status-tooltip-dot--' + (ok ? "online" : "offline") + '"></span>' +
                '<span>' + id + '</span>' +
                '</div>';
        }).join("");
    }

    async function checkHealth() {
        try {
            var response = await fetch("/api/v1/health");
            var data = await response.json();
            var badge = document.getElementById("healthStatus");
            var statusMap = data.db_status_map || {};
            var dbIds = Object.keys(statusMap);
            updateAdminTooltip(statusMap);

            if (dbIds.length > 0) {
                var onlineCount = dbIds.filter(function (id) { return statusMap[id]; }).length;
                if (onlineCount === dbIds.length) {
                    badge.textContent = "HEALTHY";
                    badge.className = "status-badge status-badge--online";
                } else if (onlineCount === 0) {
                    badge.textContent = "OFFLINE";
                    badge.className = "status-badge status-badge--offline";
                } else {
                    badge.textContent = "WARNING";
                    badge.className = "status-badge status-badge--warning";
                }
            } else {
                if (data.status === "healthy") {
                    badge.textContent = "HEALTHY";
                    badge.className = "status-badge status-badge--online";
                } else {
                    badge.textContent = "DEGRADED";
                    badge.className = "status-badge status-badge--warning";
                }
            }
        } catch (err) {
            var badge = document.getElementById("healthStatus");
            badge.textContent = "OFFLINE";
            badge.className = "status-badge status-badge--offline";
            updateAdminTooltip({});
        }
    }

    // --- API 헬퍼 ---

    function apiRequest(method, url, body) {
        var options = {
            method: method,
            headers: {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        // 토큰은 페이지 진입 시 1회만 캡처하므로, 만료 시 무안내 실패하지 않도록 401을 여기서 처리한다.
        return fetch(url, options).then(function (response) {
            if (response.status === 401) {
                redirectUnauthenticated();
            }
            return response;
        });
    }

    function showError(message) {
        alertError.textContent = message;
        alertError.classList.add("active");
        alertSuccess.classList.remove("active");
        setTimeout(function () { alertError.classList.remove("active"); }, 5000);
    }

    function showSuccess(message) {
        alertSuccess.textContent = message;
        alertSuccess.classList.add("active");
        alertError.classList.remove("active");
        setTimeout(function () { alertSuccess.classList.remove("active"); }, 5000);
    }

    // --- 탭 전환 ---

    var tabs = document.querySelectorAll(".tab");
    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            tabs.forEach(function (t) { t.classList.remove("active"); });
            tab.classList.add("active");

            document.querySelectorAll(".tab-content").forEach(function (c) {
                c.classList.remove("active");
            });
            document.getElementById("tab-" + tab.dataset.tab).classList.add("active");

            if (tab.dataset.tab === "settings") {
                ensureSettingsLoaded();  // 최초 1회만 로드 — 미저장 편집을 보존한다
            }
        });
    });

    // --- 로그아웃 ---

    // Plan 59-a 후속: 대시보드 로그아웃은 **계정 전체 로그아웃**이어야 한다.
    // - 일반(통합 RBAC) 세션: 서버 쿠키 정리(/auth/logout) + 로컬 토큰 제거 → 일반 로그인(/login).
    // - break-glass 운영자 세션(admin_token): 운영자 로그인(/admin/login)으로 복귀.
    document.getElementById("logoutBtn").addEventListener("click", async function () {
        if (localStorage.getItem("admin_token")) {
            localStorage.removeItem("admin_token");
            window.location.href = "/admin/login";
            return;
        }
        try {
            await apiRequest("POST", "/api/v1/auth/logout");  // HttpOnly 쿠키 정리 + 감사 로그
        } catch (e) { /* 무시 — 로컬 정리는 계속 진행 */ }
        localStorage.removeItem("user_token");
        localStorage.removeItem("user_info");
        window.location.href = "/login";
    });

    // --- 테마 (전역 기본값) ---

    // 운영자 화면의 토글은 자기 브라우저가 아니라 **전체 기본 테마**(UI_DEFAULT_THEME)를 바꾼다.
    // 즉시 반영 키라서 저장 후 새로 접속하는 화면부터 적용되며, 재시작·리로드가 필요 없다.
    // 저장 후 개인 선택을 지워, 운영자 자신의 화면도 방금 정한 기본값을 따르게 한다.
    var themeToggleBtn = document.getElementById("themeToggle");
    if (themeToggleBtn && window.AppTheme) {
        themeToggleBtn.addEventListener("click", async function () {
            var next = window.AppTheme.toggleValue();
            themeToggleBtn.disabled = true;
            try {
                var response = await apiRequest("PUT", "/api/v1/admin/settings", {
                    settings: { UI_DEFAULT_THEME: next },
                    reset_keys: [],
                });
                var data = await response.json();
                if (!response.ok) {
                    showError(errorMessage(data, "기본 테마 저장에 실패했습니다."));
                    return;
                }
                window.AppTheme.cacheGlobalDefault(next);
                window.AppTheme.clearPersonal();
                showSuccess("기본 테마를 " + (next === "dark" ? "어둡게" : "밝게") + "로 저장했습니다.");
                // 설정 목록이 옛 값을 보여주지 않도록 새로 읽는다 —
                // 단, 저장하지 않은 편집이 있으면 덮어쓰지 않는다.
                if (!Object.keys(settingsEdits).length) await loadSettings();
            } catch (err) {
                showError("서버와의 통신에 실패했습니다.");
            } finally {
                themeToggleBtn.disabled = false;
            }
        });
    }

    // --- 환경변수 설정 (Plan 68: 카탈로그 기반 아코디언) ---
    //
    // 모든 렌더링은 createElement/textContent로 수행한다(innerHTML 금지 —
    // escapeHtml이 작은따옴표를 이스케이프하지 않아 한국어 설명에서 위험하다).

    var settingsGroups = [];      // 서버 카탈로그(그룹 배열)
    var settingsItems = {};       // env_key -> 설정 항목
    var settingsEdits = {};       // env_key -> {reset: true} | {value: "..."}
    var settingsRows = {};        // env_key -> {row, state, error}
    var groupExpanded = {};       // group_key -> 펼침 여부
    var settingsLoaded = false;
    var settingsAllExpanded = false;

    ensureSettingsLoaded();  // 설정 탭이 기본 활성 상태다

    function ensureSettingsLoaded() {
        if (settingsLoaded) return;
        settingsLoaded = true;
        loadSettings();
    }

    async function loadSettings() {
        try {
            var response = await apiRequest("GET", "/api/v1/admin/settings/schema");
            var data = await response.json();

            if (!response.ok) {
                settingsLoaded = false;
                showError(errorMessage(data, "설정을 불러오는 데 실패했습니다."));
                return;
            }

            settingsGroups = data.groups || [];
            settingsItems = {};
            settingsEdits = {};
            settingsGroups.forEach(function (group) {
                (group.settings || []).forEach(function (item) {
                    settingsItems[item.env_key] = item;
                });
            });

            renderWarnings(data.warnings || [], data.env_file_path);
            renderSettings();
        } catch (err) {
            settingsLoaded = false;
            showError("서버와의 통신에 실패했습니다.");
        }
    }

    function errorMessage(data, fallback) {
        var detail = data && data.detail;
        if (!detail) return fallback;
        if (typeof detail === "string") return detail;
        if (detail.message) return detail.message;
        if (Array.isArray(detail)) {
            return detail.map(function (e) { return e.msg || e.message || ""; })
                .filter(Boolean).join(" / ") || fallback;
        }
        return fallback;
    }

    function fieldErrors(data) {
        var detail = data && data.detail;
        if (detail && Array.isArray(detail.errors)) return detail.errors;
        return [];
    }

    function renderWarnings(warnings, envFilePath) {
        var banner = document.getElementById("settingsWarnings");
        banner.textContent = "";
        if (!warnings.length) {
            banner.style.display = "none";
            return;
        }
        warnings.forEach(function (text) {
            var line = document.createElement("div");
            line.textContent = "⚠ " + text;
            banner.appendChild(line);
        });
        if (envFilePath) {
            var path = document.createElement("div");
            path.className = "settings-banner-path";
            path.textContent = "대상 파일: " + envFilePath;
            banner.appendChild(path);
        }
        banner.style.display = "block";
    }

    // --- 값 접근 ---

    function currentValue(item) {
        var edit = settingsEdits[item.env_key];
        if (edit) return edit.reset ? null : edit.value;
        return item.file_value === undefined ? null : item.file_value;
    }

    function isDirty(item) {
        return Object.prototype.hasOwnProperty.call(settingsEdits, item.env_key);
    }

    function setValue(key, value) {
        var item = settingsItems[key];
        if (!item) return;
        var original = item.file_value === undefined ? null : item.file_value;

        if (value === null) {
            // 기본값으로 되돌리기 = .env에서 줄 제거
            if (original === null) delete settingsEdits[key];
            else settingsEdits[key] = { reset: true };
        } else if (value === original) {
            delete settingsEdits[key];
        } else {
            settingsEdits[key] = { value: value };
        }
        refreshRow(key);
        refreshGroupCounts();
    }

    function dirtyCount() {
        return Object.keys(settingsEdits).length;
    }

    // --- 렌더링 ---

    function renderSettings() {
        var container = document.getElementById("settingsAccordion");
        container.textContent = "";
        settingsRows = {};

        var query = document.getElementById("settingsSearch").value.trim().toLowerCase();
        var filter = document.getElementById("settingsFilter").value;
        var showUnconsumed = document.getElementById("showUnconsumed").checked;
        var narrowed = query !== "" || filter !== "all";
        var shown = 0;

        settingsGroups.forEach(function (group) {
            var items = (group.settings || []).filter(function (item) {
                return matchesFilters(item, query, filter, showUnconsumed);
            });
            if (!items.length) return;
            shown += items.length;
            container.appendChild(buildGroup(group, items, narrowed));
        });

        if (!shown) {
            var empty = document.createElement("p");
            empty.className = "settings-empty";
            empty.textContent = "조건에 맞는 설정이 없습니다.";
            container.appendChild(empty);
        }

        updateCount(shown);
        refreshGroupCounts();
        document.getElementById("settingsLoading").classList.remove("active");
    }

    function matchesFilters(item, query, filter, showUnconsumed) {
        if (!item.consumed && !showUnconsumed && !isDirty(item)) return false;

        if (query) {
            var haystack = (item.env_key + " " + (item.description || "")).toLowerCase();
            if (haystack.indexOf(query) === -1) return false;
        }

        if (filter === "changed") return isDirty(item);
        if (filter === "restart") return applyMode(item) === "restart" && !item.is_secret;
        if (filter === "reload") return applyMode(item) === "reload" && !item.is_secret;
        if (filter === "non-default") {
            var value = currentValue(item);
            return value !== null && value !== undefined;
        }
        return true;
    }

    function updateCount(shown) {
        var total = Object.keys(settingsItems).length;
        var label = document.getElementById("settingsCount");
        var dirty = dirtyCount();
        label.textContent = shown + " / " + total + "개 표시"
            + (dirty ? " · 미저장 " + dirty + "건" : "");
    }

    function buildGroup(group, items, forceExpand) {
        var section = document.createElement("section");
        section.className = "settings-group";
        var expanded = forceExpand || groupExpanded[group.group_key] === true;
        if (expanded) section.classList.add("expanded");

        var header = document.createElement("button");
        header.type = "button";
        header.className = "settings-group-header";

        var title = document.createElement("span");
        title.className = "settings-group-title";
        title.textContent = group.title;
        header.appendChild(title);

        var count = document.createElement("span");
        count.className = "settings-group-count";
        count.textContent = items.length + "개";
        header.appendChild(count);

        var dirtyBadge = document.createElement("span");
        dirtyBadge.className = "badge badge--dirty";
        dirtyBadge.dataset.groupKey = group.group_key;
        header.appendChild(dirtyBadge);

        var chevron = document.createElement("span");
        chevron.className = "settings-group-chevron";
        chevron.textContent = "▾";
        header.appendChild(chevron);

        header.addEventListener("click", function () {
            var nowExpanded = !section.classList.contains("expanded");
            section.classList.toggle("expanded", nowExpanded);
            groupExpanded[group.group_key] = nowExpanded;
        });
        section.appendChild(header);

        var body = document.createElement("div");
        body.className = "settings-group-body";
        var lastSection = null;
        items.forEach(function (item) {
            if (item.section && item.section !== lastSection) {
                var subtitle = document.createElement("div");
                subtitle.className = "settings-subsection";
                subtitle.textContent = item.section;
                body.appendChild(subtitle);
                lastSection = item.section;
            }
            body.appendChild(buildRow(item));
        });
        section.appendChild(body);

        return section;
    }

    function buildRow(item) {
        var row = document.createElement("div");
        row.className = "setting-row";

        // 좌: 키 + 뱃지 + 설명
        var label = document.createElement("div");
        label.className = "setting-label";

        var keyLine = document.createElement("div");
        keyLine.className = "setting-key";
        var key = document.createElement("code");
        key.textContent = item.env_key;
        keyLine.appendChild(key);
        buildBadges(item).forEach(function (badge) { keyLine.appendChild(badge); });
        label.appendChild(keyLine);

        if (item.description) {
            var description = document.createElement("div");
            description.className = "setting-description";
            description.textContent = item.description;
            label.appendChild(description);
        }
        row.appendChild(label);

        // 중: 위젯
        var widget = document.createElement("div");
        widget.className = "setting-widget";
        widget.appendChild(buildWidget(item));
        row.appendChild(widget);

        // 우: 상태 + 기본값 복귀
        var state = document.createElement("div");
        state.className = "setting-state";
        row.appendChild(state);

        var error = document.createElement("div");
        error.className = "setting-error";
        row.appendChild(error);

        settingsRows[item.env_key] = { row: row, state: state, error: error };
        refreshRow(item.env_key);
        return row;
    }

    function applyMode(item) {
        // 서버가 apply_mode를 항상 내려주지만, 구버전 응답 캐시에 대비해 requires_restart로 폴백한다.
        return item.apply_mode || (item.requires_restart ? "restart" : "immediate");
    }

    function buildBadges(item) {
        var badges = [];
        var mode = applyMode(item);
        if (item.is_secret) {
            badges.push(makeBadge("🔒 .encenv 관리", "badge--secret",
                ".encenv에서 관리하는 시크릿입니다. .env 수정은 반영되지 않습니다."));
        } else if (mode === "restart") {
            badges.push(makeBadge("재시작", "badge--restart", "저장 후 서버 재시작이 필요합니다."));
        } else if (mode === "reload") {
            badges.push(makeBadge("리로드", "badge--reload",
                "저장 후 [설정 리로드] 버튼(또는 서버 재시작)으로 반영됩니다."));
        } else {
            badges.push(makeBadge("즉시 반영", "badge--immediate", "저장 후 다음 요청부터 반영됩니다."));
        }
        if (!item.consumed) {
            badges.push(makeBadge("미소비", "badge--unconsumed",
                "현재 코드가 이 설정을 읽지 않습니다."));
        }
        if (item.override === "os") {
            badges.push(makeBadge("OS 오버라이드", "badge--override",
                "OS 환경변수가 .env 값을 덮어씁니다 — 저장해도 반영되지 않습니다."));
        } else if (item.override === "encenv") {
            badges.push(makeBadge(".encenv 우선", "badge--override",
                ".encenv 값이 .env를 덮어씁니다 — 저장해도 반영되지 않습니다."));
        }
        return badges;
    }

    function makeBadge(text, className, title) {
        var badge = document.createElement("span");
        badge.className = "badge " + className;
        badge.textContent = text;
        if (title) badge.title = title;
        return badge;
    }

    function refreshRow(key) {
        var nodes = settingsRows[key];
        var item = settingsItems[key];
        if (!nodes || !item) return;

        var dirty = isDirty(item);
        nodes.row.classList.toggle("setting-row--dirty", dirty);
        nodes.error.textContent = "";
        nodes.row.classList.remove("setting-row--error");
        nodes.state.textContent = "";

        if (item.is_secret) {
            var secretState = document.createElement("span");
            secretState.className = "setting-state-text";
            secretState.textContent = item.is_set ? "설정됨" : "미설정";
            nodes.state.appendChild(secretState);
            return;
        }

        var value = currentValue(item);
        var stateText = document.createElement("span");
        stateText.className = "setting-state-text";
        if (dirty) {
            stateText.textContent = "변경됨(미저장)";
            stateText.classList.add("setting-state-text--dirty");
        } else if (value === null || value === undefined) {
            stateText.textContent = "기본값 사용 중";
        } else {
            stateText.textContent = "";
        }
        nodes.state.appendChild(stateText);

        if (value !== null && value !== undefined) {
            var resetBtn = document.createElement("button");
            resetBtn.type = "button";
            resetBtn.className = "setting-reset";
            resetBtn.textContent = "기본값으로";
            resetBtn.title = item.default === null
                ? "이 설정을 .env에서 제거합니다(미설정)."
                : "기본값(" + item.default + ")으로 되돌립니다.";
            resetBtn.addEventListener("click", function () {
                setValue(key, null);
                rerenderWidget(key);
            });
            nodes.state.appendChild(resetBtn);
        }
    }

    function rerenderWidget(key) {
        var nodes = settingsRows[key];
        var item = settingsItems[key];
        if (!nodes || !item) return;
        var widget = nodes.row.querySelector(".setting-widget");
        widget.textContent = "";
        widget.appendChild(buildWidget(item));
    }

    function refreshGroupCounts() {
        var perGroup = {};
        Object.keys(settingsEdits).forEach(function (key) {
            var item = settingsItems[key];
            if (!item) return;
            perGroup[item.group_key] = (perGroup[item.group_key] || 0) + 1;
        });
        document.querySelectorAll(".badge--dirty").forEach(function (badge) {
            var n = perGroup[badge.dataset.groupKey] || 0;
            badge.textContent = n ? "변경 " + n : "";
            badge.style.display = n ? "" : "none";
        });
        updateCount(document.querySelectorAll(".setting-row").length);
    }

    // --- 타입별 위젯 ---

    function buildWidget(item) {
        if (item.is_secret) return buildSecretWidget(item);
        if (item.type === "bool") return buildToggle(item);
        if (item.type === "tristate") return buildSegments(item, ["auto", "true", "false"]);
        if (item.type === "enum") {
            var choices = item.enum_choices || [];
            if (!item.optional && choices.length <= 4) return buildSegments(item, choices);
            return buildSelect(item, choices);
        }
        if (item.type === "int" || item.type === "float") return buildNumber(item);
        if (item.type === "json_list" || item.type === "csv") return buildTagEditor(item);
        return buildText(item);
    }

    function buildSecretWidget(item) {
        var wrap = document.createElement("div");
        wrap.className = "setting-secret";
        var text = document.createElement("span");
        text.textContent = item.is_set
            ? "설정됨 — .encenv에서 관리합니다."
            : "미설정 — .encenv에 값을 넣어야 합니다.";
        wrap.appendChild(text);
        return wrap;
    }

    function buildToggle(item) {
        var value = currentValue(item);
        var on = (value === null || value === undefined ? item.default : value) === "true";

        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "toggle-switch" + (on ? " on" : "");
        toggle.setAttribute("aria-pressed", on ? "true" : "false");
        var knob = document.createElement("span");
        knob.className = "toggle-knob";
        toggle.appendChild(knob);

        toggle.addEventListener("click", function () {
            var next = toggle.classList.contains("on") ? "false" : "true";
            toggle.classList.toggle("on", next === "true");
            toggle.setAttribute("aria-pressed", next);
            setValue(item.env_key, next);
        });
        return toggle;
    }

    function buildSegments(item, choices) {
        var value = currentValue(item);
        var selected = value === null || value === undefined
            ? (item.type === "tristate" ? "auto" : item.default)
            : value;

        var group = document.createElement("div");
        group.className = "segment-group";
        choices.forEach(function (choice) {
            var option = document.createElement("button");
            option.type = "button";
            option.className = "segment-option" + (choice === selected ? " selected" : "");
            option.textContent = choice === "auto" ? "auto(미설정)" : choice;
            option.addEventListener("click", function () {
                group.querySelectorAll(".segment-option").forEach(function (o) {
                    o.classList.remove("selected");
                });
                option.classList.add("selected");
                setValue(item.env_key, choice === "auto" ? null : choice);
            });
            group.appendChild(option);
        });
        return group;
    }

    function buildSelect(item, choices) {
        var value = currentValue(item);
        var select = document.createElement("select");
        select.className = "setting-input setting-select";

        if (item.optional) {
            var unset = document.createElement("option");
            unset.value = "";
            unset.textContent = "(미설정)";
            select.appendChild(unset);
        }
        choices.forEach(function (choice) {
            var option = document.createElement("option");
            option.value = choice;
            option.textContent = choice;
            select.appendChild(option);
        });
        select.value = value === null || value === undefined
            ? (item.optional ? "" : (item.default || "")) : value;

        select.addEventListener("change", function () {
            setValue(item.env_key, select.value === "" ? null : select.value);
        });
        return select;
    }

    function buildNumber(item) {
        var value = currentValue(item);
        var input = document.createElement("input");
        input.type = "number";
        input.className = "setting-input";
        if (item.type === "float") input.step = "any";
        input.value = value === null || value === undefined ? "" : value;
        input.placeholder = item.default === null ? "(미설정)" : item.default;
        input.addEventListener("input", function () {
            setValue(item.env_key, input.value === "" ? null : input.value.trim());
        });
        return input;
    }

    function buildText(item) {
        var value = currentValue(item);
        var input = document.createElement("input");
        input.type = "text";
        input.className = "setting-input";
        input.value = value === null || value === undefined ? "" : value;
        input.placeholder = item.default === null || item.default === ""
            ? "(미설정)" : item.default;
        input.autocomplete = "off";
        input.addEventListener("input", function () {
            setValue(item.env_key, input.value === "" && item.file_value === null
                ? null : input.value);
        });
        return input;
    }

    function parseListValue(item, value) {
        if (value === null || value === undefined || value === "") return [];
        if (item.type === "json_list") {
            try {
                var parsed = JSON.parse(value);
                return Array.isArray(parsed) ? parsed.map(String) : [];
            } catch (err) {
                return [];
            }
        }
        return value.split(",").map(function (part) { return part.trim(); })
            .filter(function (part) { return part !== ""; });
    }

    function serializeListValue(item, values) {
        if (item.type === "json_list") return JSON.stringify(values);
        return values.join(",");
    }

    function buildTagEditor(item) {
        var values = parseListValue(item, currentValue(item));

        var editor = document.createElement("div");
        editor.className = "tag-editor";

        function commit() {
            setValue(item.env_key, values.length || item.file_value !== null
                ? serializeListValue(item, values) : null);
        }

        function renderChips(focusInput) {
            editor.textContent = "";
            values.forEach(function (value, position) {
                var chip = document.createElement("span");
                chip.className = "tag-chip";
                var text = document.createElement("span");
                text.textContent = value;
                chip.appendChild(text);

                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "tag-chip-remove";
                remove.textContent = "×";
                remove.title = "제거";
                remove.addEventListener("click", function () {
                    values.splice(position, 1);
                    renderChips(true);
                    commit();
                });
                chip.appendChild(remove);
                editor.appendChild(chip);
            });
            editor.appendChild(input);
            if (focusInput) input.focus();
        }

        var input = document.createElement("input");
        input.type = "text";
        input.className = "tag-input";
        input.placeholder = values.length ? "추가…" : (item.default || "값 입력 후 Enter");
        input.autocomplete = "off";
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === ",") {
                event.preventDefault();
                var text = input.value.trim();
                if (!text) return;
                values.push(text);
                input.value = "";
                renderChips(true);
                commit();
            } else if (event.key === "Backspace" && input.value === "" && values.length) {
                values.pop();
                renderChips(true);
                commit();
            }
        });
        input.addEventListener("blur", function () {
            var text = input.value.trim();
            if (!text) return;
            values.push(text);
            input.value = "";
            renderChips(false);
            commit();
        });

        renderChips(false);
        return editor;
    }

    // --- 필터·툴바 ---

    document.getElementById("settingsSearch").addEventListener("input", renderSettings);
    document.getElementById("settingsFilter").addEventListener("change", renderSettings);
    document.getElementById("showUnconsumed").addEventListener("change", renderSettings);

    document.getElementById("toggleAllGroupsBtn").addEventListener("click", function () {
        settingsAllExpanded = !settingsAllExpanded;
        settingsGroups.forEach(function (group) {
            groupExpanded[group.group_key] = settingsAllExpanded;
        });
        this.textContent = settingsAllExpanded ? "모두 접기" : "모두 펼치기";
        renderSettings();
    });

    // --- 저장 (diff 확인 → PUT) ---

    function collectChanges() {
        var settings = {};
        var resetKeys = [];
        var rows = [];

        Object.keys(settingsEdits).forEach(function (key) {
            var item = settingsItems[key];
            if (!item) return;
            var edit = settingsEdits[key];
            var to = edit.reset ? null : edit.value;
            if (edit.reset) resetKeys.push(key);
            else settings[key] = edit.value;
            rows.push({
                key: key,
                from: item.file_value,
                to: to,
                mode: applyMode(item),
                fallback: item.default,
            });
        });

        return { settings: settings, reset_keys: resetKeys, rows: rows };
    }

    var pendingChanges = null;

    document.getElementById("saveSettingsBtn").addEventListener("click", function () {
        var changes = collectChanges();
        if (!changes.rows.length) {
            showError("변경된 설정이 없습니다.");
            return;
        }
        pendingChanges = changes;
        openDiffModal(changes);
    });

    function openDiffModal(changes) {
        var body = document.getElementById("settingsDiffBody");
        body.textContent = "";

        changes.rows.forEach(function (change) {
            var entry = document.createElement("div");
            entry.className = "diff-entry";

            var head = document.createElement("div");
            head.className = "diff-key";
            var code = document.createElement("code");
            code.textContent = change.key;
            head.appendChild(code);
            if (change.mode === "restart") {
                head.appendChild(makeBadge("재시작", "badge--restart"));
            } else if (change.mode === "reload") {
                head.appendChild(makeBadge("리로드", "badge--reload"));
            }
            entry.appendChild(head);

            var before = document.createElement("div");
            before.className = "diff-line diff-line--old";
            before.textContent = change.from === null || change.from === undefined
                ? "(기본값 사용 중)" : change.from;
            entry.appendChild(before);

            var after = document.createElement("div");
            after.className = "diff-line diff-line--new";
            after.textContent = change.to === null
                ? "(기본값으로 되돌림" + (change.fallback === null ? "" : ": " + change.fallback) + ")"
                : change.to;
            entry.appendChild(after);

            body.appendChild(entry);
        });

        document.getElementById("settingsDiffModal").style.display = "flex";
    }

    function closeDiffModal() {
        document.getElementById("settingsDiffModal").style.display = "none";
    }

    document.getElementById("diffCancelBtn").addEventListener("click", closeDiffModal);
    document.getElementById("settingsDiffModal").addEventListener("click", function (event) {
        if (event.target === this) closeDiffModal();
    });

    document.getElementById("diffConfirmBtn").addEventListener("click", async function () {
        if (!pendingChanges) return;
        var confirmBtn = this;
        confirmBtn.disabled = true;

        try {
            var response = await apiRequest("PUT", "/api/v1/admin/settings", {
                settings: pendingChanges.settings,
                reset_keys: pendingChanges.reset_keys,
            });
            var data = await response.json();

            if (!response.ok) {
                closeDiffModal();
                showValidationErrors(data);
                return;
            }

            closeDiffModal();
            showSuccess(data.message);
            showRestartBanner(data);
            pendingChanges = null;
            settingsEdits = {};
            await loadSettings();
        } catch (err) {
            showError("서버와의 통신에 실패했습니다.");
        } finally {
            confirmBtn.disabled = false;
        }
    });

    function showValidationErrors(data) {
        var errors = fieldErrors(data);
        showError(errorMessage(data, "저장에 실패했습니다."));
        if (!errors.length) return;

        // 오류 행이 필터·접힘으로 가려지지 않도록 조건을 초기화하고 해당 그룹을 펼친다
        document.getElementById("settingsSearch").value = "";
        document.getElementById("settingsFilter").value = "all";
        document.getElementById("showUnconsumed").checked = true;
        errors.forEach(function (error) {
            var item = settingsItems[error.key];
            if (item) groupExpanded[item.group_key] = true;
        });
        renderSettings();

        errors.forEach(function (error) {
            var nodes = settingsRows[error.key];
            if (!nodes) return;
            nodes.row.classList.add("setting-row--error");
            nodes.error.textContent = error.message;
        });
    }

    function showRestartBanner(data) {
        var banner = document.getElementById("restartBanner");
        banner.textContent = "";
        var restartKeys = data.requires_restart_keys || [];
        var reloadKeys = data.reload_keys || [];
        var immediateKeys = data.applied_immediately_keys || [];
        var ignoredKeys = data.ignored_keys || [];

        if (!restartKeys.length && !reloadKeys.length && !immediateKeys.length && !ignoredKeys.length) {
            banner.style.display = "none";
            return;
        }
        if (restartKeys.length) {
            var restartLine = document.createElement("div");
            restartLine.textContent = "다음 항목은 서버 재시작 후 반영됩니다: " + restartKeys.join(", ");
            banner.appendChild(restartLine);
        }
        if (reloadKeys.length) {
            var reloadLine = document.createElement("div");
            reloadLine.textContent = "다음 항목은 [설정 리로드] 버튼으로 반영할 수 있습니다: " + reloadKeys.join(", ");
            banner.appendChild(reloadLine);
        }
        if (immediateKeys.length) {
            var immediateLine = document.createElement("div");
            immediateLine.textContent = "다음 항목은 다음 요청부터 반영됩니다: " + immediateKeys.join(", ");
            banner.appendChild(immediateLine);
        }
        if (ignoredKeys.length) {
            var ignoredLine = document.createElement("div");
            ignoredLine.textContent = "마스킹 값이 그대로 전송되어 무시했습니다(원값 보존): " + ignoredKeys.join(", ");
            banner.appendChild(ignoredLine);
        }
        banner.style.display = "block";
    }

    // --- 설정 리로드 (Plan 68 §6 Phase 4) ---

    document.getElementById("reloadSettingsBtn").addEventListener("click", async function () {
        if (dirtyCount()) {
            showError("저장되지 않은 변경이 있습니다 — 먼저 저장한 뒤 리로드하세요.");
            return;
        }
        var reloadBtn = this;
        reloadBtn.disabled = true;
        reloadBtn.textContent = "리로드 중…";

        try {
            var response = await apiRequest("POST", "/api/v1/admin/settings/reload");
            var data = await response.json();
            if (!response.ok) {
                showError(errorMessage(data, "설정 리로드에 실패했습니다."));
                return;
            }
            showSuccess(data.message);
            showReloadResultBanner(data);
            await loadSettings();
        } catch (err) {
            showError("서버와의 통신에 실패했습니다.");
        } finally {
            reloadBtn.disabled = false;
            reloadBtn.textContent = "설정 리로드";
        }
    });

    function showReloadResultBanner(data) {
        var banner = document.getElementById("restartBanner");
        banner.textContent = "";
        var restartKeys = data.restart_only_keys || [];
        if (!restartKeys.length) {
            banner.style.display = "none";
            return;
        }
        var line = document.createElement("div");
        line.textContent = "다음 항목은 서버 재시작 후 반영됩니다: " + restartKeys.join(", ");
        banner.appendChild(line);
        banner.style.display = "block";
    }

    // --- DB 연결 설정 ---

    // DB Type 선택 UI
    var dbTypeSelector = document.getElementById("dbTypeSelector");
    var dbTypeInput = document.getElementById("dbType");
    var dbTypeOptions = dbTypeSelector.querySelectorAll(".db-type-option");

    dbTypeOptions.forEach(function (option) {
        option.addEventListener("click", function () {
            dbTypeOptions.forEach(function (o) { o.classList.remove("selected"); });
            option.classList.add("selected");
            dbTypeInput.value = option.dataset.value;

            // 포트 기본값 변경
            var portInput = document.getElementById("dbPort");
            if (!portInput.value || portInput.value === "5432" || portInput.value === "3306") {
                portInput.value = option.dataset.value === "postgresql" ? "5432" : "3306";
            }
        });
    });

    loadDbConfig();

    async function loadDbConfig() {
        try {
            var response = await apiRequest("GET", "/api/v1/admin/db-config");
            var data = await response.json();

            if (response.ok) {
                var dbType = data.db_type || "postgresql";
                dbTypeInput.value = dbType;

                // DB Type selector 업데이트
                dbTypeOptions.forEach(function (o) {
                    o.classList.remove("selected");
                    if (o.dataset.value === dbType) {
                        o.classList.add("selected");
                    }
                });

                document.getElementById("dbHost").value = data.host || "";
                document.getElementById("dbPort").value = data.port || 5432;
                document.getElementById("dbName").value = data.database || "";
                document.getElementById("dbUser").value = data.username || "";
                // 비밀번호는 표시하지 않음
            }
        } catch (err) {
            // 무시 (첫 설정일 수 있음)
        }
    }

    function getDbFormData() {
        return {
            db_type: dbTypeInput.value,
            host: document.getElementById("dbHost").value.trim(),
            port: parseInt(document.getElementById("dbPort").value) || 5432,
            database: document.getElementById("dbName").value.trim(),
            username: document.getElementById("dbUser").value.trim(),
            password: document.getElementById("dbPassword").value,
        };
    }

    function validateDbForm(data) {
        if (!data.host) return "호스트를 입력해주세요.";
        if (!data.database) return "데이터베이스명을 입력해주세요.";
        if (!data.username) return "사용자명을 입력해주세요.";
        if (!data.password) return "비밀번호를 입력해주세요.";
        return null;
    }

    // 연결 테스트
    document.getElementById("testDbBtn").addEventListener("click", async function () {
        var data = getDbFormData();
        var err = validateDbForm(data);
        if (err) {
            showError(err);
            return;
        }

        var testResult = document.getElementById("dbTestResult");
        testResult.className = "connection-status connection-status--testing";
        testResult.textContent = "연결 테스트 중...";
        testResult.style.display = "flex";

        try {
            var response = await apiRequest("POST", "/api/v1/admin/db-config/test", data);
            var result = await response.json();

            if (result.success) {
                testResult.className = "connection-status connection-status--success";
                testResult.textContent = result.message + (result.details ? " \u2014 " + result.details : "");
            } else {
                testResult.className = "connection-status connection-status--error";
                testResult.textContent = result.message + (result.details ? " \u2014 " + result.details : "");
            }
        } catch (err) {
            testResult.className = "connection-status connection-status--error";
            testResult.textContent = "서버와의 통신에 실패했습니다.";
        }
    });

    // 저장
    document.getElementById("saveDbBtn").addEventListener("click", async function () {
        var data = getDbFormData();
        var err = validateDbForm(data);
        if (err) {
            showError(err);
            return;
        }

        try {
            var response = await apiRequest("PUT", "/api/v1/admin/db-config", data);
            var result = await response.json();

            if (!response.ok) {
                showError(result.detail || "저장에 실패했습니다.");
                return;
            }

            showSuccess(result.message);
        } catch (err) {
            showError("서버와의 통신에 실패했습니다.");
        }
    });

    // --- 사용자 관리 ---

    var usersLoading = document.getElementById("usersLoading");
    var usersTable = document.getElementById("usersTable");
    var usersBody = document.getElementById("usersBody");
    var refreshUsersBtn = document.getElementById("refreshUsersBtn");

    if (refreshUsersBtn) {
        refreshUsersBtn.addEventListener("click", loadUsers);
    }

    // 사용자 관리 탭 클릭 시 로드
    document.querySelectorAll('.tab[data-tab="users"]').forEach(function(tab) {
        tab.addEventListener("click", loadUsers);
    });

    async function loadUsers() {
        if (!usersBody) return;
        if (usersLoading) usersLoading.classList.add("active");
        if (usersTable) usersTable.style.display = "none";

        try {
            var response = await apiRequest("GET", "/api/v1/admin/users");
            if (!response.ok) {
                showError("사용자 목록을 불러오지 못했습니다.");
                return;
            }
            var users = await response.json();
            usersBody.innerHTML = "";

            users.forEach(function(u) {
                var uid = escapeHtml(u.user_id);
                var prot = !!u.is_protected;                 // 보호 root 계정
                var protAttr = prot ? " disabled" : "";
                var isAdmin = u.role === "admin";
                var zones = u.alarm_zones || [];
                // 알림그룹: 관리자는 전 존 수신(체크 무의미), 보호 계정도 비활성
                var zDis = (isAdmin || prot) ? " disabled" : "";
                var zTitle = isAdmin ? ' title="관리자는 전 존 수신"' : (prot ? ' title="보호된 root 계정"' : "");

                var inputStyle = 'width:100px;font-size:0.75rem;padding:2px 4px;' +
                    'background:var(--bg-tertiary);color:var(--text-primary);' +
                    'border:1px solid var(--border);border-radius:4px';

                // 이름(표시명 username) 인라인 편집. 로그인 ID(user_id)는 불변이라 편집 대상 아님.
                var nameCell =
                    '<td><input type="text" class="name-input" data-uid="' + uid + '" value="' +
                    escapeHtml(u.username) + '" style="' + inputStyle + '"></td>';

                var deptCell =
                    '<td><input type="text" class="dept-input" data-uid="' + uid + '" value="' +
                    escapeHtml(u.department || "") + '" placeholder="-" style="' + inputStyle + '"></td>';

                // 수평 배치 + 체크박스·텍스트 수직 중앙 정렬은 .zone-chk-group(style.css)이 담당
                var zoneCell =
                    '<td' + zTitle + '><div class="zone-chk-group">' +
                        '<label><input type="checkbox" class="zone-chk" data-uid="' + uid +
                            '" data-zone="gongjon"' + (zones.indexOf("gongjon") >= 0 ? " checked" : "") + zDis + '> 공동존</label>' +
                        '<label><input type="checkbox" class="zone-chk" data-uid="' + uid +
                            '" data-zone="bankjon"' + (zones.indexOf("bankjon") >= 0 ? " checked" : "") + zDis + '> 은행존</label>' +
                    '</div></td>';

                var tr = document.createElement("tr");
                tr.innerHTML =
                    "<td>" + uid + (prot ? " 🔒" : "") + "</td>" +
                    nameCell +
                    '<td><select class="role-select" data-uid="' + uid + '"' + protAttr + ">" +
                        "<option value='user'" + (u.role === "user" ? " selected" : "") + ">user</option>" +
                        "<option value='admin'" + (u.role === "admin" ? " selected" : "") + ">admin</option>" +
                    "</select></td>" +
                    '<td><select class="status-select" data-uid="' + uid + '"' + protAttr + ">" +
                        "<option value='active'" + (u.status === "active" ? " selected" : "") + ">active</option>" +
                        "<option value='inactive'" + (u.status === "inactive" ? " selected" : "") + ">inactive</option>" +
                        "<option value='locked'" + (u.status === "locked" ? " selected" : "") + ">locked</option>" +
                    "</select></td>" +
                    deptCell +
                    zoneCell +
                    "<td style='font-size:0.75rem'>" + (u.last_login_at ? u.last_login_at.substring(0, 19) : "-") + "</td>" +
                    "<td>" +
                        '<button class="btn btn-secondary btn-sm reset-pw-btn" data-uid="' + uid + '" style="font-size:0.7rem;padding:3px 8px;margin-right:4px"' + protAttr + ">PW초기화</button>" +
                        '<button class="btn btn-secondary btn-sm delete-user-btn" data-uid="' + uid + '" style="font-size:0.7rem;padding:3px 8px;color:#ef4444"' + protAttr + ">삭제</button>" +
                    "</td>";
                usersBody.appendChild(tr);
            });

            // 이벤트 바인딩
            usersBody.querySelectorAll(".role-select").forEach(function(sel) {
                sel.addEventListener("change", function() { updateUser(sel.dataset.uid, {role: sel.value}); });
            });
            usersBody.querySelectorAll(".status-select").forEach(function(sel) {
                sel.addEventListener("change", function() { updateUser(sel.dataset.uid, {status: sel.value}); });
            });
            // 이름(username) 인라인 편집: 비우면(공백) 되돌림, 아니면 저장
            usersBody.querySelectorAll(".name-input").forEach(function(inp) {
                inp.addEventListener("change", function() {
                    var v = inp.value.trim();
                    if (!v) { loadUsers(); return; }   // username은 필수(min_length=1)
                    updateUser(inp.dataset.uid, {username: v});
                });
            });
            // 부서 인라인 편집(개선 4): 값 변경 시 저장
            usersBody.querySelectorAll(".dept-input").forEach(function(inp) {
                inp.addEventListener("change", function() { updateUser(inp.dataset.uid, {department: inp.value.trim()}); });
            });
            // 알림그룹 체크박스(개선 2): 해당 사용자의 체크 상태를 모아 alarm_zones로 저장(둘 다 해제=[])
            usersBody.querySelectorAll(".zone-chk").forEach(function(chk) {
                chk.addEventListener("change", function() {
                    var uid = chk.dataset.uid;
                    var sel = (window.CSS && CSS.escape) ? CSS.escape(uid) : uid.replace(/'/g, "\\'");
                    var checked = usersBody.querySelectorAll(".zone-chk[data-uid='" + sel + "']:checked");
                    var zones = Array.prototype.map.call(checked, function(c) { return c.dataset.zone; });
                    updateUser(uid, {alarm_zones: zones});
                });
            });
            usersBody.querySelectorAll(".reset-pw-btn").forEach(function(btn) {
                btn.addEventListener("click", function() { resetPassword(btn.dataset.uid); });
            });
            usersBody.querySelectorAll(".delete-user-btn").forEach(function(btn) {
                btn.addEventListener("click", function() { deleteUser(btn.dataset.uid); });
            });

            if (usersLoading) usersLoading.classList.remove("active");
            if (usersTable) usersTable.style.display = "table";
        } catch (err) {
            showError("사용자 목록 로드 실패");
            if (usersLoading) usersLoading.classList.remove("active");
        }
    }

    async function updateUser(uid, data) {
        try {
            var response = await apiRequest("PUT", "/api/v1/admin/users/" + uid, data);
            if (response.ok) {
                showSuccess("사용자 '" + uid + "' 수정 완료");
            } else {
                var err = await response.json();
                showError(err.detail || "수정 실패");
                loadUsers();
            }
        } catch (e) {
            showError("통신 실패");
        }
    }

    async function resetPassword(uid) {
        if (!confirm("'" + uid + "'의 비밀번호를 초기화하시겠습니까?")) return;
        try {
            var response = await apiRequest("POST", "/api/v1/admin/users/" + uid + "/reset-password");
            var result = await response.json();
            if (response.ok) {
                alert("임시 비밀번호: " + result.temp_password + "\n사용자에게 전달하세요.");
                showSuccess(result.message);
            } else {
                showError(result.detail || "초기화 실패");
            }
        } catch (e) {
            showError("통신 실패");
        }
    }

    async function deleteUser(uid) {
        if (!confirm("'" + uid + "' 사용자를 삭제하시겠습니까?")) return;
        try {
            var response = await apiRequest("DELETE", "/api/v1/admin/users/" + uid);
            if (response.ok) {
                showSuccess("사용자 '" + uid + "' 삭제 완료");
                loadUsers();
            } else {
                var err = await response.json();
                showError(err.detail || "삭제 실패");
            }
        } catch (e) {
            showError("통신 실패");
        }
    }

    function escapeHtml(str) {
        if (!str) return "";
        return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    // --- 감사 로그 ---

    var logsLoading = document.getElementById("logsLoading");
    var logsTable = document.getElementById("logsTable");
    var logsBody = document.getElementById("logsBody");
    var refreshLogsBtn = document.getElementById("refreshLogsBtn");
    var searchLogsBtn = document.getElementById("searchLogsBtn");
    var logsPrevBtn = document.getElementById("logsPrevBtn");
    var logsNextBtn = document.getElementById("logsNextBtn");
    var logsPageInfo = document.getElementById("logsPageInfo");
    var logsPagination = document.getElementById("logsPagination");
    var currentPage = 1;
    var currentPageSize = 50;
    var totalPages = 0;

    if (refreshLogsBtn) {
        refreshLogsBtn.addEventListener("click", function() { currentPage = 1; loadAuditLogs(); });
    }
    if (searchLogsBtn) {
        searchLogsBtn.addEventListener("click", function() { currentPage = 1; loadAuditLogs(); });
    }
    // 개선 5: 보관 기간 지난 감사 로그 수동 정리
    var cleanupLogsBtn = document.getElementById("cleanupLogsBtn");
    if (cleanupLogsBtn) {
        cleanupLogsBtn.addEventListener("click", async function() {
            if (!window.confirm("보관 기간이 지난 감사 로그를 삭제합니다. 계속할까요?")) return;
            cleanupLogsBtn.disabled = true;
            try {
                var res = await apiRequest("POST", "/api/v1/admin/audit/cleanup");
                if (res.ok) {
                    var d = await res.json();
                    showSuccess((d.deleted || 0) + "건 삭제 (보관 " + d.retention_days + "일)");
                    currentPage = 1; loadAuditLogs();
                } else {
                    var err = await res.json();
                    showError(err.detail || "로그 정리 실패");
                }
            } catch (e) {
                showError("로그 정리 중 오류");
            } finally {
                cleanupLogsBtn.disabled = false;
            }
        });
    }
    if (logsPrevBtn) {
        logsPrevBtn.addEventListener("click", function() {
            if (currentPage > 1) { currentPage--; loadAuditLogs(); }
        });
    }
    if (logsNextBtn) {
        logsNextBtn.addEventListener("click", function() {
            if (currentPage < totalPages) { currentPage++; loadAuditLogs(); }
        });
    }

    document.querySelectorAll('.tab[data-tab="auditlogs"]').forEach(function(tab) {
        tab.addEventListener("click", function() { loadAuditLogs(); loadAuditStats(); loadSecurityAlerts(); });
    });

    function buildAuditQuery() {
        var params = new URLSearchParams();
        var sd = document.getElementById("filterStartDate");
        var ed = document.getElementById("filterEndDate");
        var uid = document.getElementById("filterUserId");
        var et = document.getElementById("filterEventType");
        var kw = document.getElementById("filterKeyword");
        if (sd && sd.value) params.set("start_date", sd.value);
        if (ed && ed.value) params.set("end_date", ed.value);
        if (uid && uid.value) params.set("user_id", uid.value.trim());
        if (et && et.value) params.set("event_type", et.value);
        if (kw && kw.value) params.set("keyword", kw.value.trim());
        params.set("page", currentPage);
        params.set("page_size", currentPageSize);
        return params.toString();
    }

    async function loadAuditLogs() {
        if (!logsBody) return;
        if (logsLoading) logsLoading.classList.add("active");
        if (logsTable) logsTable.style.display = "none";
        if (logsPagination) logsPagination.style.display = "none";

        try {
            var qs = buildAuditQuery();
            var response = await apiRequest("GET", "/api/v1/admin/audit/logs?" + qs);
            if (!response.ok) {
                // 새 API 실패 시 기존 API 폴백
                response = await apiRequest("GET", "/api/v1/admin/audit-logs?limit=200");
                if (!response.ok) {
                    showError("감사 로그를 불러오지 못했습니다.");
                    if (logsLoading) logsLoading.classList.remove("active");
                    return;
                }
                var logs = await response.json();
                renderAuditLogs(logs);
                return;
            }

            var data = await response.json();
            totalPages = data.total_pages || 0;
            renderAuditLogs(data.logs || []);

            // 페이지네이션 업데이트
            if (logsPagination) {
                logsPagination.style.display = "flex";
                if (logsPageInfo) logsPageInfo.textContent = "페이지 " + data.page + " / " + totalPages + " (총 " + data.total + "건)";
                if (logsPrevBtn) logsPrevBtn.disabled = currentPage <= 1;
                if (logsNextBtn) logsNextBtn.disabled = currentPage >= totalPages;
            }
        } catch (err) {
            showError("감사 로그 로드 실패");
            if (logsLoading) logsLoading.classList.remove("active");
        }
    }

    function renderAuditLogs(logs) {
        logsBody.innerHTML = "";
        logs.forEach(function(log) {
            var tr = document.createElement("tr");
            var time = log.created_at ? log.created_at.substring(0, 19) : "-";
            var eventType = log.event_type || "-";
            var userId = log.user_id || "-";
            var ip = log.ip_address || "-";
            var detail = log.detail ? JSON.stringify(log.detail) : "{}";
            if (detail.length > 120) detail = detail.substring(0, 117) + "...";
            tr.innerHTML =
                "<td style='font-size:0.75rem;white-space:nowrap'>" + escapeHtml(time) + "</td>" +
                "<td><span style='font-size:0.75rem;padding:2px 6px;border-radius:3px;background:var(--bg-tertiary)'>" + escapeHtml(eventType) + "</span></td>" +
                "<td>" + escapeHtml(userId) + "</td>" +
                "<td style='font-size:0.75rem'>" + escapeHtml(ip) + "</td>" +
                "<td style='font-size:0.75rem;max-width:300px;overflow:hidden;text-overflow:ellipsis' title='" + escapeHtml(detail) + "'>" + escapeHtml(detail) + "</td>";
            logsBody.appendChild(tr);
        });
        if (logsLoading) logsLoading.classList.remove("active");
        if (logsTable) logsTable.style.display = "table";
        renderAuditAnonymousNotice(logs);
    }

    // 인증이 꺼져 있으면 모든 요청이 anonymous로 기록된다(D-183). 사용자 열이 전부 같아
    // 보이는 것이 버그로 읽히지 않도록, 실제로 그런 행이 있을 때만 사유를 밝힌다
    // — 인증을 켜면 안내가 저절로 사라진다(정적 문구를 박아두지 않는 이유).
    function renderAuditAnonymousNotice(logs) {
        var notice = document.getElementById("auditAnonymousNotice");
        if (!notice) return;
        var hasAnonymous = (logs || []).some(function(log) {
            return log.user_id === "anonymous";
        });
        notice.style.display = hasAnonymous ? "block" : "none";
    }

    // --- 감사 통계 ---

    async function loadAuditStats() {
        try {
            var response = await apiRequest("GET", "/api/v1/admin/audit/stats");
            if (!response.ok) return;
            var stats = await response.json();
            var el;
            el = document.getElementById("statTotalRequests");
            if (el) el.textContent = (stats.total_requests || 0).toLocaleString();
            el = document.getElementById("statUniqueUsers");
            if (el) el.textContent = stats.unique_users || 0;
            el = document.getElementById("statSuccessRate");
            if (el) el.textContent = stats.success_rate != null ? (stats.success_rate * 100).toFixed(1) + "%" : "-";
            el = document.getElementById("statAlerts");
            if (el) el.textContent = stats.security_alerts_count || 0;
        } catch (err) {
            // 통계 로드 실패는 무시
        }
    }

    // --- 보안 경고 ---

    var alertsBody = document.getElementById("alertsBody");
    var alertsTable = document.getElementById("alertsTable");
    var alertsLoading = document.getElementById("alertsLoading");
    var alertsEmpty = document.getElementById("alertsEmpty");
    var refreshAlertsBtn = document.getElementById("refreshAlertsBtn");

    if (refreshAlertsBtn) {
        refreshAlertsBtn.addEventListener("click", loadSecurityAlerts);
    }

    async function loadSecurityAlerts() {
        if (!alertsBody) return;
        if (alertsLoading) alertsLoading.classList.add("active");
        if (alertsTable) alertsTable.style.display = "none";
        if (alertsEmpty) alertsEmpty.style.display = "none";

        try {
            var response = await apiRequest("GET", "/api/v1/admin/audit/alerts?limit=50");
            if (!response.ok) {
                if (alertsLoading) alertsLoading.classList.remove("active");
                return;
            }
            var alerts = await response.json();
            alertsBody.innerHTML = "";

            if (alerts.length === 0) {
                if (alertsLoading) alertsLoading.classList.remove("active");
                if (alertsEmpty) alertsEmpty.style.display = "block";
                return;
            }

            alerts.forEach(function(a) {
                var tr = document.createElement("tr");
                var time = a.created_at ? a.created_at.substring(0, 19) : "-";
                var severity = (a.detail && a.detail.severity) || "warning";
                var sevColor = severity === "critical" ? "var(--error)" : severity === "warning" ? "#f59e0b" : "var(--text-muted)";
                var userId = a.user_id || "-";
                var ip = a.ip_address || "-";
                var detail = (a.detail && a.detail.detail) || JSON.stringify(a.detail || {});
                tr.innerHTML =
                    "<td style='font-size:0.75rem;white-space:nowrap'>" + escapeHtml(time) + "</td>" +
                    "<td><span style='font-size:0.7rem;font-weight:600;padding:2px 8px;border-radius:3px;color:" + sevColor + ";background:color-mix(in srgb," + sevColor + " 15%,transparent)'>" + escapeHtml(severity.toUpperCase()) + "</span></td>" +
                    "<td>" + escapeHtml(userId) + "</td>" +
                    "<td style='font-size:0.75rem'>" + escapeHtml(ip) + "</td>" +
                    "<td style='font-size:0.75rem'>" + escapeHtml(detail) + "</td>";
                alertsBody.appendChild(tr);
            });

            if (alertsLoading) alertsLoading.classList.remove("active");
            if (alertsTable) alertsTable.style.display = "table";
        } catch (err) {
            if (alertsLoading) alertsLoading.classList.remove("active");
        }
    }

    // --- 알람 피드백 집계 — Plan 83 T13 ---
    // 조회 전용이다. 상반된 라벨(같은 알람에 유효/노이즈가 함께 쌓인 경우)을 사람이 보고
    // 판단하게 할 뿐, 발송 판정에는 관여하지 않는다.

    var feedbackBody = document.getElementById("feedbackBody");
    var feedbackTable = document.getElementById("feedbackTable");
    var feedbackLoading = document.getElementById("feedbackLoading");
    var feedbackEmpty = document.getElementById("feedbackEmpty");
    var refreshFeedbackBtn = document.getElementById("refreshFeedbackBtn");

    if (refreshFeedbackBtn) {
        refreshFeedbackBtn.addEventListener("click", loadFeedbackSummary);
    }
    document.querySelectorAll('.tab[data-tab="feedback"]').forEach(function (tab) {
        tab.addEventListener("click", loadFeedbackSummary);
    });

    async function loadFeedbackSummary() {
        if (!feedbackBody) return;
        if (feedbackLoading) feedbackLoading.classList.add("active");
        if (feedbackTable) feedbackTable.style.display = "none";
        if (feedbackEmpty) feedbackEmpty.style.display = "none";
        try {
            var response = await apiRequest("GET", "/api/v1/alarm/feedback/summary?limit=200");
            if (!response.ok) {
                if (feedbackLoading) feedbackLoading.classList.remove("active");
                if (feedbackEmpty) feedbackEmpty.style.display = "block";
                return;
            }
            var data = await response.json();
            renderFeedbackSummary((data && data.items) || []);
        } catch (e) {
            if (feedbackLoading) feedbackLoading.classList.remove("active");
            if (feedbackEmpty) feedbackEmpty.style.display = "block";
        }
    }

    function renderFeedbackSummary(items) {
        feedbackBody.innerHTML = "";
        if (items.length === 0) {
            if (feedbackLoading) feedbackLoading.classList.remove("active");
            if (feedbackEmpty) feedbackEmpty.style.display = "block";
            return;
        }
        items.forEach(function (it) {
            var conflict = it.valid > 0 && it.noise > 0;   // 상충 표시 대상
            var tr = document.createElement("tr");
            tr.innerHTML =
                "<td>" + escapeHtml(it.alarm_name || "-") + "</td>" +
                "<td>" + escapeHtml(it.resource_name || "-") + "</td>" +
                "<td>" + it.valid + "</td>" +
                "<td>" + it.noise + "</td>" +
                "<td>" + (it.last_label === "valid" ? "유효" : "노이즈") +
                    (conflict ? " <span title='같은 알람에 상반된 라벨이 있습니다'>⚠</span>" : "") + "</td>" +
                "<td>" + escapeHtml(it.last_labeled_by || "-") + "</td>" +
                "<td>" + escapeHtml((it.last_ts || "").replace("T", " ").slice(0, 19)) + "</td>";
            feedbackBody.appendChild(tr);
        });
        if (feedbackLoading) feedbackLoading.classList.remove("active");
        if (feedbackTable) feedbackTable.style.display = "table";
    }

    // --- 열린 사건(incident) — D-049 ---

    var incidentsBody = document.getElementById("incidentsBody");
    var incidentsTable = document.getElementById("incidentsTable");
    var incidentsLoading = document.getElementById("incidentsLoading");
    var incidentsEmpty = document.getElementById("incidentsEmpty");
    var refreshIncidentsBtn = document.getElementById("refreshIncidentsBtn");

    var INCIDENT_SEVERITY_LABELS = { 0: "해소", 1: "주의", 2: "경고", 3: "심각" };

    if (refreshIncidentsBtn) {
        refreshIncidentsBtn.addEventListener("click", loadIncidents);
    }

    document.querySelectorAll('.tab[data-tab="incidents"]').forEach(function (tab) {
        tab.addEventListener("click", loadIncidents);
    });

    function formatElapsed(createdAt) {
        if (!createdAt) return "-";
        var start = new Date(createdAt).getTime();
        if (isNaN(start)) return "-";
        var sec = Math.floor((Date.now() - start) / 1000);
        if (sec < 0) sec = 0;
        if (sec < 60) return sec + "초";
        if (sec < 3600) return Math.floor(sec / 60) + "분";
        if (sec < 86400) return Math.floor(sec / 3600) + "시간";
        return Math.floor(sec / 86400) + "일";
    }

    async function loadIncidents() {
        if (!incidentsBody) return;
        if (incidentsLoading) incidentsLoading.classList.add("active");
        if (incidentsTable) incidentsTable.style.display = "none";
        if (incidentsEmpty) incidentsEmpty.style.display = "none";

        try {
            var response = await apiRequest("GET", "/api/v1/alarm/incidents?status=open&limit=100");
            if (!response.ok) {
                if (incidentsLoading) incidentsLoading.classList.remove("active");
                showError("열린 사건을 불러오지 못했습니다.");
                return;
            }
            var data = await response.json();
            renderIncidents((data && data.incidents) || []);
        } catch (err) {
            if (incidentsLoading) incidentsLoading.classList.remove("active");
            showError("열린 사건 로드 실패");
        }
    }

    function renderIncidents(incidents) {
        incidentsBody.innerHTML = "";
        if (incidents.length === 0) {
            if (incidentsLoading) incidentsLoading.classList.remove("active");
            if (incidentsEmpty) incidentsEmpty.style.display = "block";
            return;
        }
        incidents.forEach(function (inc) {
            var tr = document.createElement("tr");
            var time = inc.created_at ? inc.created_at.substring(0, 19) : "-";
            var sevLabel = INCIDENT_SEVERITY_LABELS[inc.severity] || String(inc.severity);
            var sevColor = inc.severity >= 3 ? "var(--error)" : inc.severity === 2 ? "#f59e0b" : "var(--text-muted)";
            tr.innerHTML =
                "<td style='font-size:0.75rem;white-space:nowrap'>" + escapeHtml(time) + "</td>" +
                "<td style='font-size:0.75rem'>" + escapeHtml(formatElapsed(inc.created_at)) + "</td>" +
                "<td style='font-size:0.8rem'>" + escapeHtml(inc.server_name || "-") +
                    "<span style='color:var(--text-muted);font-size:0.7rem'> (" + escapeHtml(inc.db_id || "-") + ")</span></td>" +
                "<td style='font-size:0.8rem'>" + escapeHtml(inc.alarm_name || "-") + "</td>" +
                "<td><span style='font-size:0.7rem;font-weight:600;padding:2px 8px;border-radius:3px;color:" + sevColor +
                    ";background:color-mix(in srgb," + sevColor + " 15%,transparent)'>" + escapeHtml(sevLabel) + "</span></td>" +
                "<td style='font-size:0.75rem'>" + escapeHtml(inc.tier || "-") + "</td>" +
                "<td><button class='btn btn-secondary btn-sm incident-ack-btn' data-iid='" + escapeHtml(String(inc.id)) +
                    "' style='font-size:0.7rem;padding:3px 10px'>확인</button></td>";
            incidentsBody.appendChild(tr);
        });
        incidentsBody.querySelectorAll(".incident-ack-btn").forEach(function (btn) {
            btn.addEventListener("click", function () { ackIncident(btn.dataset.iid, btn); });
        });
        if (incidentsLoading) incidentsLoading.classList.remove("active");
        if (incidentsTable) incidentsTable.style.display = "table";
    }

    async function ackIncident(incidentId, btn) {
        if (btn) btn.disabled = true;
        try {
            var response = await apiRequest("POST", "/api/v1/alarm/incidents/" + incidentId + "/ack");
            if (!response.ok) {
                showError("확인 처리 실패");
                if (btn) btn.disabled = false;
                return;
            }
            var res = await response.json();
            if (res && res.acked) {
                showSuccess("사건 #" + incidentId + " 확인됨");
            } else {
                showError("이미 확인/해소된 사건입니다.");
            }
            loadIncidents();
        } catch (err) {
            showError("확인 처리 실패");
            if (btn) btn.disabled = false;
        }
    }

    // --- DRM 연동 진단 (Plan 74 §4.2) ---
    //
    // 실기 환경이 운영계뿐이므로 셸 없이 연동 상태를 점검한다.
    // 진단 응답은 실패도 200 + 구조화된 결과이므로, 화면은 항상 결과를 렌더한다.

    var drmStatusBody = document.getElementById("drmStatusBody");
    var drmStatusTable = document.getElementById("drmStatusTable");
    var drmStatusLoading = document.getElementById("drmStatusLoading");
    var drmSummary = document.getElementById("drmSummary");
    var refreshDrmBtn = document.getElementById("refreshDrmBtn");
    var drmVerifyBtn = document.getElementById("drmVerifyBtn");
    var drmSampleInput = document.getElementById("drmSampleInput");
    var drmVerifyLoading = document.getElementById("drmVerifyLoading");
    var drmVerifyResult = document.getElementById("drmVerifyResult");

    if (refreshDrmBtn) refreshDrmBtn.addEventListener("click", loadDrmStatus);
    if (drmVerifyBtn) drmVerifyBtn.addEventListener("click", verifyDrmSample);

    document.querySelectorAll('.tab[data-tab="drm"]').forEach(function (tab) {
        tab.addEventListener("click", loadDrmStatus);
    });

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function drmBadge(ok) {
        var color = ok ? "var(--success, #2e7d32)" : "var(--danger, #c62828)";
        return '<span style="color: ' + color + '; font-weight: 600;">' +
            (ok ? "정상" : "확인 필요") + "</span>";
    }

    async function loadDrmStatus() {
        if (!drmStatusBody) return;
        if (drmStatusLoading) drmStatusLoading.classList.add("active");
        if (drmStatusTable) drmStatusTable.style.display = "none";
        if (drmSummary) drmSummary.style.display = "none";

        try {
            var response = await apiRequest("GET", "/api/v1/admin/drm/status");
            if (!response.ok) {
                if (drmStatusLoading) drmStatusLoading.classList.remove("active");
                showError("DRM 상태를 불러오지 못했습니다.");
                return;
            }
            renderDrmStatus(await response.json());
        } catch (err) {
            if (drmStatusLoading) drmStatusLoading.classList.remove("active");
            showError("DRM 상태를 불러오지 못했습니다.");
        }
    }

    function renderDrmStatus(data) {
        if (drmStatusLoading) drmStatusLoading.classList.remove("active");

        if (drmSummary) {
            var tone = data.enabled
                ? (data.ready ? "rgba(46,125,50,0.12)" : "rgba(198,40,40,0.12)")
                : "rgba(120,120,120,0.12)";
            drmSummary.style.background = tone;
            drmSummary.innerHTML =
                "<strong>DRM_ENABLED = " + (data.enabled ? "true" : "false") + "</strong>" +
                " &mdash; " + escapeHtml(data.summary);
            drmSummary.style.display = "block";
        }

        var rows = [];
        (data.checks || []).forEach(function (c) {
            var ok = c.exists && c.readable && !c.stale;
            var detail = escapeHtml(c.message || "");
            if (c.path) detail += '<br><span style="color: var(--text-muted); font-size: 0.75rem;">' + escapeHtml(c.path) + "</span>";
            rows.push([escapeHtml(c.label), drmBadge(ok), detail]);
        });
        if (data.java) {
            rows.push([
                escapeHtml(data.java.label || "Java 런타임"),
                drmBadge(!!data.java.available),
                escapeHtml(data.java.message || ""),
            ]);
        }
        if (data.temp_dir) {
            var t = data.temp_dir;
            var tDetail = escapeHtml(t.message || "");
            if (t.path) tDetail += '<br><span style="color: var(--text-muted); font-size: 0.75rem;">' + escapeHtml(t.path) + "</span>";
            if (t.leftover_files) tDetail += " · 잔여 파일 " + t.leftover_files + "개";
            rows.push([escapeHtml(t.label || "작업 디렉터리"), drmBadge(!t.exists || t.writable), tDetail]);
        }
        rows.push(["GroupID", "-", escapeHtml(data.group_id || "")]);
        rows.push(["복호화 타임아웃", "-", escapeHtml(data.timeout_sec) + "초"]);

        drmStatusBody.innerHTML = rows.map(function (r) {
            return "<tr><td>" + r[0] + "</td><td>" + r[1] + "</td><td>" + r[2] + "</td></tr>";
        }).join("");
        if (drmStatusTable) drmStatusTable.style.display = "table";
    }

    async function verifyDrmSample() {
        if (!drmSampleInput || !drmSampleInput.files || drmSampleInput.files.length === 0) {
            showError("진단할 샘플 파일을 선택하세요.");
            return;
        }
        var formData = new FormData();
        formData.append("file", drmSampleInput.files[0]);

        if (drmVerifyBtn) drmVerifyBtn.disabled = true;
        if (drmVerifyLoading) drmVerifyLoading.classList.add("active");
        if (drmVerifyResult) drmVerifyResult.style.display = "none";

        try {
            // FormData는 Content-Type을 브라우저가 boundary와 함께 설정해야 하므로
            // apiRequest(JSON 전용)를 쓰지 않고 직접 호출한다.
            var response = await fetch("/api/v1/admin/drm/verify", {
                method: "POST",
                headers: { "Authorization": "Bearer " + token },
                body: formData,
            });
            if (!response.ok) {
                showError("진단 요청에 실패했습니다 (HTTP " + response.status + ")");
                return;
            }
            renderDrmVerify(await response.json());
        } catch (err) {
            showError("진단 요청에 실패했습니다.");
        } finally {
            if (drmVerifyBtn) drmVerifyBtn.disabled = false;
            if (drmVerifyLoading) drmVerifyLoading.classList.remove("active");
        }
    }

    function renderDrmVerify(result) {
        if (!drmVerifyResult) return;

        var DETECT_LABELS = { drm: "DRM 암호문 (SCDS)", plain: "평문 문서 (ZIP)", unknown: "판별 불가" };
        var rows = [
            ["파일", escapeHtml(result.file_name) + " (" + (result.file_size_bytes || 0).toLocaleString() + " bytes)"],
            ["감지 결과", escapeHtml(DETECT_LABELS[result.detected] || result.detected || "-")],
        ];
        if (result.header_hex) rows.push(["선두 바이트", "<code>" + escapeHtml(result.header_hex) + "</code>"]);
        if (result.ret !== null && result.ret !== undefined) rows.push(["scsl 반환값 (ret)", "<code>" + escapeHtml(result.ret) + "</code>"]);
        if (result.elapsed_ms !== null && result.elapsed_ms !== undefined) rows.push(["소요 시간", escapeHtml(result.elapsed_ms) + " ms"]);

        var out = result.output;
        if (out) {
            rows.push(["산출물 크기", (out.size_bytes || 0).toLocaleString() + " bytes"]);
            rows.push(["ZIP 시그니처", out.is_zip ? "확인됨 (PK)" : "없음"]);
            if (out.parse_message) {
                rows.push(["문서 파싱", escapeHtml(out.parse_message)]);
            }
            if (out.sheet_names) rows.push(["시트", escapeHtml(out.sheet_names.join(", "))]);
            if (out.paragraph_count !== undefined) {
                rows.push(["문단/표", out.paragraph_count + "개 / " + (out.table_count || 0) + "개"]);
            }
        }
        if (result.detail) rows.push(["상세", "<code>" + escapeHtml(result.detail) + "</code>"]);

        var ok = !!result.success;
        var tone = ok ? "rgba(46,125,50,0.12)" : "rgba(198,40,40,0.12)";
        drmVerifyResult.innerHTML =
            '<div style="padding: 12px 14px; border-radius: 6px; background: ' + tone + '; margin-bottom: 12px; font-size: 0.85rem;">' +
            "<strong>" + (ok ? "성공" : "실패") + "</strong> &mdash; " + escapeHtml(result.message || "") +
            "</div>" +
            '<table class="settings-table"><tbody>' +
            rows.map(function (r) {
                return '<tr><td style="width: 22%;">' + r[0] + "</td><td>" + r[1] + "</td></tr>";
            }).join("") +
            "</tbody></table>";
        drmVerifyResult.style.display = "block";
    }
})();
