/**
 * 운영자 대시보드 JavaScript
 *
 * 환경변수 설정, DB 연결 설정 관리를 담당한다.
 */

(function () {
    "use strict";

    var token = localStorage.getItem("admin_token");
    var alertError = document.getElementById("alertError");
    var alertSuccess = document.getElementById("alertSuccess");

    // --- 인증 확인 ---

    if (!token) {
        window.location.href = "/admin/login";
        return;
    }

    // 토큰 유효성 확인
    verifyToken();

    async function verifyToken() {
        try {
            var response = await apiRequest("GET", "/api/v1/admin/me");
            if (!response.ok) {
                localStorage.removeItem("admin_token");
                window.location.href = "/admin/login";
            }
        } catch (err) {
            localStorage.removeItem("admin_token");
            window.location.href = "/admin/login";
        }
    }

    // --- 헬스 체크 ---

    checkHealth();

    async function checkHealth() {
        try {
            var response = await fetch("/api/v1/health");
            var data = await response.json();
            var badge = document.getElementById("healthStatus");
            if (data.status === "healthy") {
                badge.textContent = "HEALTHY";
                badge.className = "status-badge status-badge--online";
            } else {
                badge.textContent = "DEGRADED";
                badge.className = "status-badge";
                badge.style.background = "var(--warning-bg)";
                badge.style.color = "var(--warning)";
                badge.style.border = "1px solid rgba(245, 158, 11, 0.15)";
            }
        } catch (err) {
            var badge = document.getElementById("healthStatus");
            badge.textContent = "OFFLINE";
            badge.className = "status-badge";
            badge.style.background = "var(--error-bg)";
            badge.style.color = "var(--error)";
            badge.style.border = "1px solid rgba(244, 63, 94, 0.15)";
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
        return fetch(url, options);
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
        });
    });

    // --- 로그아웃 ---

    document.getElementById("logoutBtn").addEventListener("click", function () {
        localStorage.removeItem("admin_token");
        window.location.href = "/admin/login";
    });

    // --- 환경변수 설정 ---

    var settingsData = [];

    loadSettings();

    async function loadSettings() {
        try {
            var response = await apiRequest("GET", "/api/v1/admin/settings");
            var data = await response.json();

            if (!response.ok) {
                showError(data.detail || "설정을 불러오는 데 실패했습니다.");
                return;
            }

            settingsData = data.settings;
            renderSettings(settingsData);
        } catch (err) {
            showError("서버와의 통신에 실패했습니다.");
        }
    }

    function renderSettings(settings) {
        var tbody = document.getElementById("settingsBody");
        tbody.innerHTML = "";

        settings.forEach(function (setting) {
            var tr = document.createElement("tr");

            var tdKey = document.createElement("td");
            tdKey.textContent = setting.key;
            tr.appendChild(tdKey);

            var tdValue = document.createElement("td");
            var input = document.createElement("input");
            input.type = setting.is_sensitive ? "password" : "text";
            input.className = "value-input";
            input.value = setting.is_sensitive ? "" : setting.value;
            input.placeholder = setting.is_sensitive ? "(변경하려면 새 값 입력)" : "";
            input.dataset.key = setting.key;
            input.dataset.sensitive = setting.is_sensitive;
            input.dataset.original = setting.value;
            tdValue.appendChild(input);
            tr.appendChild(tdValue);

            tbody.appendChild(tr);
        });

        document.getElementById("settingsLoading").classList.remove("active");
        document.getElementById("settingsTable").style.display = "table";
    }

    document.getElementById("saveSettingsBtn").addEventListener("click", async function () {
        var inputs = document.querySelectorAll(".value-input");
        var updates = {};

        inputs.forEach(function (input) {
            var key = input.dataset.key;
            var isSensitive = input.dataset.sensitive === "true";
            var value = input.value;

            // 민감 값: 비어있으면 변경하지 않음
            if (isSensitive && !value) return;

            // 비민감 값: 변경된 경우만
            if (!isSensitive && value === input.dataset.original) return;

            updates[key] = value;
        });

        if (Object.keys(updates).length === 0) {
            showError("변경된 설정이 없습니다.");
            return;
        }

        try {
            var response = await apiRequest("PUT", "/api/v1/admin/settings", {
                settings: updates,
            });
            var data = await response.json();

            if (!response.ok) {
                showError(data.detail || "저장에 실패했습니다.");
                return;
            }

            showSuccess(data.message);
            loadSettings(); // 새로고침
        } catch (err) {
            showError("서버와의 통신에 실패했습니다.");
        }
    });

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
                var tr = document.createElement("tr");
                tr.innerHTML =
                    "<td>" + escapeHtml(u.user_id) + "</td>" +
                    "<td>" + escapeHtml(u.username) + "</td>" +
                    "<td><select class='role-select' data-uid='" + escapeHtml(u.user_id) + "'>" +
                        "<option value='user'" + (u.role === "user" ? " selected" : "") + ">user</option>" +
                        "<option value='admin'" + (u.role === "admin" ? " selected" : "") + ">admin</option>" +
                    "</select></td>" +
                    "<td><select class='status-select' data-uid='" + escapeHtml(u.user_id) + "'>" +
                        "<option value='active'" + (u.status === "active" ? " selected" : "") + ">active</option>" +
                        "<option value='inactive'" + (u.status === "inactive" ? " selected" : "") + ">inactive</option>" +
                        "<option value='locked'" + (u.status === "locked" ? " selected" : "") + ">locked</option>" +
                    "</select></td>" +
                    "<td>" + escapeHtml(u.department || "-") + "</td>" +
                    "<td style='font-size:0.75rem'>" + (u.last_login_at ? u.last_login_at.substring(0, 19) : "-") + "</td>" +
                    "<td>" +
                        "<button class='btn btn-secondary btn-sm reset-pw-btn' data-uid='" + escapeHtml(u.user_id) + "' style='font-size:0.7rem;padding:3px 8px;margin-right:4px'>PW초기화</button>" +
                        "<button class='btn btn-secondary btn-sm delete-user-btn' data-uid='" + escapeHtml(u.user_id) + "' style='font-size:0.7rem;padding:3px 8px;color:#ef4444'>삭제</button>" +
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
})();
