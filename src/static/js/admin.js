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
})();
