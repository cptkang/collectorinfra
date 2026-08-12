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

    // --- DRM 연동 진단 (Plan 69 §4.2) ---
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
