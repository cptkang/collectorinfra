/**
 * 인프라 데이터 조회 에이전트 — Chat UI + SSE Streaming
 *
 * 채팅 인터페이스, 파일 첨부, SSE 스트리밍, 폴백 처리를 담당한다.
 */

(function () {
    "use strict";

    // ─── DOM Elements ───

    var chatMessages = document.getElementById("chatMessages");
    var chatWelcome = document.getElementById("chatWelcome");
    var chatError = document.getElementById("chatError");
    var chatErrorText = document.getElementById("chatErrorText");
    var promptEl = document.getElementById("prompt");
    var fileInput = document.getElementById("fileInput");
    var filePreview = document.getElementById("filePreview");
    var fileNameEl = document.getElementById("fileName");
    var fileSizeEl = document.getElementById("fileSize");
    var removeFileBtn = document.getElementById("removeFile");
    var sendBtn = document.getElementById("sendBtn");
    var hintButtons = document.querySelectorAll(".chat-welcome-hint");
    var progressPanel = document.getElementById("progressPanel");
    var progressPipeline = document.getElementById("progressPipeline");
    var progressEmpty = document.getElementById("progressEmpty");
    var panelToggle = document.getElementById("panelToggle");
    var scrollToBottomBtn = document.getElementById("scrollToBottomBtn");

    // ─── Auth Helpers ───

    function getAuthHeaders() {
        var headers = {};
        var token = localStorage.getItem("user_token");
        if (token) {
            headers["Authorization"] = "Bearer " + token;
        }
        return headers;
    }

    function redirectToLogin() {
        // 만료/무효 토큰을 정리하여 로그인 후 동일 증상 재발을 방지한다.
        localStorage.removeItem("user_token");
        localStorage.removeItem("user_info");
        window.location.href = "/login";
    }

    function checkAuthOnLoad() {
        fetch("/api/v1/auth/status", { headers: getAuthHeaders() })
            .then(function(res) {
                // 토큰이 만료/무효(예: 서버 재시작로 JWT 시크릿 회전)이면 status가 401을 반환한다.
                // 본문에 auth_enabled가 없어 아래 분기로 잡히지 않으므로 여기서 먼저 처리한다.
                if (res.status === 401) {
                    redirectToLogin();
                    return null;
                }
                return res.json();
            })
            .then(function(data) {
                if (!data) return;  // 401 처리 후 리다이렉트된 경우
                // 인증이 켜져 있는데 유효 사용자가 없으면(토큰 부재 또는 무효) 로그인으로 유도한다.
                if (data.auth_enabled && !data.user) {
                    redirectToLogin();
                    return;
                }
                // 사용자 정보 표시
                var userInfo = data.user;
                var userArea = document.getElementById("userInfoArea");
                if (userArea && userInfo) {
                    userArea.style.display = "inline-flex";
                    var nameEl = document.getElementById("userDisplayName");
                    if (nameEl) nameEl.textContent = userInfo.username || userInfo.user_id;
                }
                // 로그아웃 버튼
                var logoutBtn = document.getElementById("userLogoutBtn");
                if (logoutBtn && data.auth_enabled) {
                    logoutBtn.style.display = "inline-block";
                    logoutBtn.addEventListener("click", function() {
                        fetch("/api/v1/auth/logout", {
                            method: "POST",
                            headers: getAuthHeaders()
                        }).finally(function() {
                            localStorage.removeItem("user_token");
                            localStorage.removeItem("user_info");
                            window.location.href = "/login";
                        });
                    });
                }
            })
            .catch(function() {
                // 인증 상태 확인 실패 시 무시 (AUTH_ENABLED=false 기본)
            });
    }

    checkAuthOnLoad();

    // ─── Health Check ───

    var statusBadge = document.getElementById("statusBadge");
    var dbWarningBanner = document.getElementById("dbWarningBanner");
    var dbWarningText = document.getElementById("dbWarningText");
    var _healthOk = false;

    function updateStatusTooltip(statusMap) {
        var tooltip = document.getElementById("statusTooltip");
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

    function checkHealth() {
        fetch("/api/v1/health", { headers: getAuthHeaders() })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                var statusMap = data.db_status_map || {};
                var dbIds = Object.keys(statusMap);
                updateStatusTooltip(statusMap);

                if (dbIds.length > 0) {
                    var onlineCount = dbIds.filter(function (id) { return statusMap[id]; }).length;
                    var allOnline = onlineCount === dbIds.length;
                    var allOffline = onlineCount === 0;

                    if (allOnline) {
                        _healthOk = true;
                        statusBadge.className = "status-badge status-badge--online";
                        statusBadge.textContent = "ONLINE";
                        if (dbWarningBanner) dbWarningBanner.classList.remove("active");
                    } else if (allOffline) {
                        _healthOk = false;
                        statusBadge.className = "status-badge status-badge--offline";
                        statusBadge.textContent = "OFFLINE";
                        if (dbWarningBanner) {
                            dbWarningText.textContent =
                                "모든 DB 서버에 연결할 수 없습니다. MCP 서버 상태를 확인하세요.";
                            dbWarningBanner.classList.add("active");
                        }
                    } else {
                        _healthOk = true;
                        statusBadge.className = "status-badge status-badge--warning";
                        statusBadge.textContent = "WARNING";
                        if (dbWarningBanner) {
                            var offlineIds = dbIds.filter(function (id) { return !statusMap[id]; });
                            dbWarningText.textContent =
                                "일부 DB 연결 불가: " + offlineIds.join(", ") + ". 쿼리 결과가 불완전할 수 있습니다.";
                            dbWarningBanner.classList.add("active");
                        }
                    }
                } else {
                    // 단일 DB 모드 (레거시)
                    if (data.db_connected) {
                        _healthOk = true;
                        statusBadge.className = "status-badge status-badge--online";
                        statusBadge.textContent = "ONLINE";
                        if (dbWarningBanner) dbWarningBanner.classList.remove("active");
                    } else {
                        _healthOk = false;
                        statusBadge.className = "status-badge status-badge--offline";
                        statusBadge.textContent = "DB OFFLINE";
                        if (dbWarningBanner) {
                            dbWarningText.textContent =
                                "DB 서버(MCP Server)에 연결할 수 없습니다. 쿼리 실행이 불가능합니다. MCP 서버 상태를 확인하세요.";
                            dbWarningBanner.classList.add("active");
                        }
                    }
                }
            })
            .catch(function () {
                _healthOk = false;
                statusBadge.className = "status-badge status-badge--offline";
                statusBadge.textContent = "OFFLINE";
                updateStatusTooltip({});
                if (dbWarningBanner) {
                    dbWarningText.textContent =
                        "API 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.";
                    dbWarningBanner.classList.add("active");
                }
            });
    }

    // 즉시 + 30초마다 헬스체크
    checkHealth();
    setInterval(checkHealth, 30000);

    // ─── State ───

    var selectedFile = null;
    var isProcessing = false;
    var messages = []; // session message history
    var stageTimer = null;
    var currentThreadId = null;

    // ─── Scroll (stick-to-bottom) State ───
    var stickToBottom = true;          // 맨 아래 고정 여부
    var hasNewContent = false;         // 고정 해제 상태에서 미확인 신규 출력 존재 여부(버튼 강조용)
    var BOTTOM_THRESHOLD_PX = 24;      // 이 거리 이내면 "맨 아래"로 간주(테스트 후 조정 가능)

    // ─── Streaming Render State (비파괴 렌더 + rAF 코얼레싱) ───
    var _streamAccumulated = "";       // 현재 스트리밍 메시지의 누적 마크다운(렌더 입력)
    var _streamRafQueued = false;      // 이번 프레임 렌더 예약 여부(토큰 버스트 코얼레싱)

    // ─── Prompt History ───
    var promptHistory = [];           // 전송된 프롬프트 히스토리 (오래된 순)
    var historyIndex = -1;            // 현재 탐색 위치 (-1 = 탐색 안 함)
    var savedCurrentInput = "";       // 히스토리 진입 전 입력 중이던 텍스트 보존

    // Stage definitions
    var stages = ["parse", "schema", "sql", "exec", "result"];
    var stageLabels = {
        parse: "입력 분석",
        schema: "스키마 탐색",
        sql: "SQL 생성",
        exec: "쿼리 실행",
        result: "결과 정리",
    };
    var stageMessages = {
        parse: "입력 분석 중...",
        schema: "데이터베이스 스키마 탐색 중...",
        sql: "SQL 쿼리 생성 중...",
        exec: "쿼리 실행 중...",
        result: "결과 정리 중...",
    };

    // Node → Pipeline display mapping
    var nodeTooltips = {
        context_resolver:  "이전 대화 맥락을 분석하여 멀티턴 질의를 지원합니다",
        input_parser:      "자연어 질의를 구조화된 요구사항으로 파싱합니다",
        field_mapper:      "양식 파일의 필드명을 DB 컬럼에 매핑합니다 (파일 없으면 스킵)",
        semantic_router:   "질의 의도를 분류하고 대상 DB를 선택합니다",
        schema_analyzer:   "DB 스키마를 조회하고 관련 테이블을 탐색합니다",
        query_generator:   "요구사항에 맞는 SQL 쿼리를 생성합니다",
        query_validator:   "생성된 SQL의 안전성과 정확성을 검증합니다",
        query_executor:    "검증된 SQL을 DB에서 실행합니다",
        result_organizer:  "조회 결과의 충분성을 검토하고 정리합니다",
        output_generator:  "조회 결과를 바탕으로 자연어 응답을 생성합니다",
        general_inference: "DB 조회 없이 LLM이 직접 응답을 생성합니다",
        multi_db_executor: "여러 DB에서 동시에 쿼리를 실행합니다",
        result_merger:     "다중 DB 결과를 통합합니다",
        synonym_registrar: "새로운 유사어를 등록합니다",
        error_response:    "처리 중 오류가 발생했습니다",
        intent_planner:    "사용자 질의를 처리할 작업(의도) 단위로 분해합니다",
        agent_orchestrator:"분해된 작업들을 순서/병렬로 실행합니다",
        replanner:         "1차 결과를 평가하여 후속 작업이 필요한지 판단합니다",
        result_aggregator: "여러 작업 결과를 하나의 응답으로 통합합니다",
    };

    var nodeLabels = {
        input_parser: "입력 분석",
        field_mapper: "필드 매핑",
        semantic_router: "DB 라우팅",
        schema_analyzer: "스키마 탐색",
        query_generator: "SQL 생성",
        query_validator: "SQL 검증",
        query_executor: "쿼리 실행",
        result_organizer: "결과 정리",
        output_generator: "응답 생성",
        multi_db_executor: "멀티 DB 실행",
        result_merger: "결과 병합",
        general_inference: "일반 추론",
        error_response: "에러 처리",
        intent_planner: "의도 분석",
        agent_orchestrator: "작업 실행",
        replanner: "재계획",
        result_aggregator: "결과 통합",
    };

    // task agent 식별자 → 사용자용 라벨 (처리 현황 작업 목록 표시)
    var agentLabels = {
        data_query: "DB 조회",
        alarm_query: "알람 조회",
        cache_management: "캐시 관리",
        synonym_registration: "유사어 등록",
        general_inference: "일반 안내",
    };

    // ─── Tooltip ───

    var _tooltip = document.createElement("div");
    _tooltip.id = "appTooltip";
    document.body.appendChild(_tooltip);

    document.addEventListener("mouseover", function (e) {
        var el = e.target.closest("[data-tooltip]");
        if (!el) return;
        var text = el.getAttribute("data-tooltip");
        if (!text) return;
        _tooltip.textContent = text;
        _tooltip.style.opacity = "1";
    });
    document.addEventListener("mousemove", function (e) {
        if (_tooltip.style.opacity !== "1") return;
        var x = e.clientX + 12;
        var y = e.clientY + 18;
        var tw = _tooltip.offsetWidth;
        var th = _tooltip.offsetHeight;
        if (x + tw > window.innerWidth - 8) x = e.clientX - tw - 12;
        if (y + th > window.innerHeight - 8) y = e.clientY - th - 8;
        _tooltip.style.left = x + "px";
        _tooltip.style.top  = y + "px";
    });
    document.addEventListener("mouseout", function (e) {
        var el = e.target.closest("[data-tooltip]");
        if (!el) return;
        _tooltip.style.opacity = "0";
    });

    // ─── Initialization ───

    promptEl.addEventListener("input", autoResizeTextarea);
    promptEl.addEventListener("keydown", handleKeydown);
    sendBtn.addEventListener("click", handleSend);
    fileInput.addEventListener("change", handleFileChange);
    removeFileBtn.addEventListener("click", clearFile);

    hintButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
            promptEl.value = btn.dataset.query;
            autoResizeTextarea.call(promptEl);
            promptEl.focus();
            // 도움말 버튼은 예시와 달리 클릭 즉시 실행하여 채팅으로 안내를 전달한다.
            if (btn.dataset.help) {
                handleSend();
            }
        });
    });

    // Panel toggle
    panelToggle.addEventListener("click", function () {
        document.querySelector(".chat-layout").classList.toggle("panel-collapsed");
    });

    // 채팅 스크롤: 맨 아래 고정(stick-to-bottom) 상태 추적 + 플로팅 버튼 토글
    chatMessages.addEventListener("scroll", function () {
        stickToBottom = isNearBottom();
        if (stickToBottom) hasNewContent = false;   // 맨 아래 복귀 → 신규 강조 해제
        updateScrollToBottomBtn();
    }, { passive: true });

    if (scrollToBottomBtn) {
        scrollToBottomBtn.addEventListener("click", function () {
            scrollToBottom(true);   // smooth 이동 + stickToBottom=true 복귀
        });
    }

    // ─── Auto-resize Textarea ───

    function autoResizeTextarea() {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 160) + "px";
    }

    // ─── Keyboard Handling ───

    function handleKeydown(e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
            return;
        }

        // 방향키 히스토리 탐색 (히스토리가 없으면 무시)
        if (promptHistory.length === 0) return;

        if (e.key === "ArrowUp") {
            // textarea가 여러 줄이면 커서가 첫 줄에 있을 때만 히스토리 탐색
            var cursorAtTop = (promptEl.selectionStart === 0) ||
                (promptEl.value.substring(0, promptEl.selectionStart).indexOf("\n") === -1);
            if (!cursorAtTop) return;

            e.preventDefault();

            if (historyIndex === -1) {
                // 히스토리 탐색 시작 — 현재 입력 텍스트를 보존
                savedCurrentInput = promptEl.value;
                historyIndex = promptHistory.length - 1;
            } else if (historyIndex > 0) {
                historyIndex--;
            }

            promptEl.value = promptHistory[historyIndex];
            autoResizeTextarea.call(promptEl);
            // 커서를 맨 끝으로 이동
            promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
            return;
        }

        if (e.key === "ArrowDown") {
            if (historyIndex === -1) return; // 탐색 중이 아니면 무시

            // textarea가 여러 줄이면 커서가 마지막 줄에 있을 때만 히스토리 탐색
            var cursorAtBottom = (promptEl.selectionStart === promptEl.value.length) ||
                (promptEl.value.substring(promptEl.selectionStart).indexOf("\n") === -1);
            if (!cursorAtBottom) return;

            e.preventDefault();

            if (historyIndex < promptHistory.length - 1) {
                historyIndex++;
                promptEl.value = promptHistory[historyIndex];
            } else {
                // 가장 최근 항목을 지나면 원래 입력 텍스트 복원
                historyIndex = -1;
                promptEl.value = savedCurrentInput;
            }

            autoResizeTextarea.call(promptEl);
            promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
            return;
        }
    }

    // ─── File Handling ───

    function handleFileChange(e) {
        var file = e.target.files[0];
        if (!file) return;

        var ext = file.name.split(".").pop().toLowerCase();
        if (ext !== "xlsx" && ext !== "docx") {
            showError("지원하지 않는 파일 형식입니다. .xlsx 또는 .docx 파일만 첨부할 수 있습니다.");
            fileInput.value = "";
            return;
        }

        if (file.size > 10 * 1024 * 1024) {
            showError("파일 크기가 10MB를 초과합니다.");
            fileInput.value = "";
            return;
        }

        selectedFile = file;
        fileNameEl.textContent = file.name;
        fileSizeEl.textContent = "(" + formatFileSize(file.size) + ")";
        filePreview.classList.add("active");
        hideError();
    }

    function clearFile() {
        selectedFile = null;
        fileInput.value = "";
        filePreview.classList.remove("active");
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    // ─── Error Handling ───

    function showError(message) {
        chatErrorText.textContent = message;
        chatError.classList.add("active");
        setTimeout(function () {
            chatError.classList.remove("active");
        }, 8000);
    }

    function hideError() {
        chatError.classList.remove("active");
    }

    // ─── Time Formatting ───

    function formatTime(date) {
        var h = String(date.getHours()).padStart(2, "0");
        var m = String(date.getMinutes()).padStart(2, "0");
        return h + ":" + m;
    }

    // ─── Send Message ───

    function handleSend() {
        if (isProcessing) return;

        var query = promptEl.value.trim();
        if (!query) {
            showError("질의를 입력해주세요.");
            return;
        }

        // 프롬프트 히스토리에 저장 (중복 연속 방지)
        if (promptHistory.length === 0 || promptHistory[promptHistory.length - 1] !== query) {
            promptHistory.push(query);
        }
        historyIndex = -1;
        savedCurrentInput = "";

        hideError();

        // Hide welcome
        if (chatWelcome && !chatWelcome.classList.contains("hidden")) {
            chatWelcome.classList.add("hidden");
        }

        // Add user message
        var userMsg = {
            role: "user",
            content: query,
            time: new Date(),
            file: selectedFile ? { name: selectedFile.name, size: selectedFile.size } : null,
        };
        messages.push(userMsg);
        renderUserMessage(userMsg);

        // Clear input
        promptEl.value = "";
        promptEl.style.height = "auto";

        // Execute
        if (selectedFile) {
            executeFileQuery(query, selectedFile);
            clearFile();
        } else {
            executeStreamingQuery(query);
        }
    }

    // ─── Render User Message ───

    function renderUserMessage(msg) {
        var el = document.createElement("div");
        el.className = "message message--user";

        var avatarHtml = '<div class="message-avatar"><svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>';

        var fileHtml = "";
        if (msg.file) {
            fileHtml = '<div class="message-file-badge"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' + escapeHtml(msg.file.name) + '</div>';
        }

        el.innerHTML =
            avatarHtml +
            '<div class="message-content">' +
                '<div class="message-bubble">' + escapeHtml(msg.content) + fileHtml + '</div>' +
                '<div class="message-time">' + formatTime(msg.time) + '</div>' +
            '</div>';

        chatMessages.appendChild(el);
        scrollToBottom();
    }

    // ─── Render Processing Indicator ───

    function renderProcessingMessage() {
        var el = document.createElement("div");
        el.className = "message message--agent message--processing";
        el.id = "processingMessage";

        var avatarHtml = '<div class="message-avatar"><svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div>';

        var stagesHtml = '<div class="processing-stages" id="processingStages">';
        stages.forEach(function (s) {
            stagesHtml += '<div class="stage" data-stage="' + s + '"><span class="stage-dot"></span>' + stageLabels[s] + '</div>';
        });
        stagesHtml += '</div>';

        el.innerHTML =
            avatarHtml +
            '<div class="message-content">' +
                '<div class="message-bubble">' +
                    '<div class="processing-indicator">' +
                        '<div class="processing-dots"><span></span><span></span><span></span></div>' +
                        '<span class="processing-text" id="processingText">처리 중...</span>' +
                    '</div>' +
                    stagesHtml +
                '</div>' +
            '</div>';

        chatMessages.appendChild(el);
        scrollToBottomIfSticky();
        startStageAnimation();
    }

    function removeProcessingMessage() {
        stopStageAnimation();
        var el = document.getElementById("processingMessage");
        if (el) el.remove();
    }

    // ─── Stage Animation ───

    // Node name → chat indicator stage mapping
    var nodeToStage = {
        input_parser: "parse", context_resolver: "parse",
        semantic_router: "schema", schema_analyzer: "schema",
        query_generator: "sql", query_validator: "sql",
        query_executor: "exec", multi_db_executor: "exec",
        result_organizer: "result", result_merger: "result",
        output_generator: "result",
    };

    function startStageAnimation() {
        // 초기 상태만 설정하고, SSE 이벤트를 대기한다.
        // 타이머 기반 자동 진행은 사용하지 않는다.
        var textEl = document.getElementById("processingText");
        if (textEl) textEl.textContent = "처리 대기 중...";
    }

    function updateProcessingStage(node, status) {
        var stage = nodeToStage[node];
        if (!stage) return;

        var stageEl = document.querySelector('.stage[data-stage="' + stage + '"]');
        if (!stageEl) return;

        var textEl = document.getElementById("processingText");

        if (status === "start") {
            stageEl.classList.add("active");
            if (textEl) textEl.textContent = stageMessages[stage] || "처리 중...";
        } else if (status === "complete") {
            stageEl.classList.remove("active");
            stageEl.classList.add("done");
        }
    }

    function stopStageAnimation() {
        if (stageTimer) {
            clearTimeout(stageTimer);
            stageTimer = null;
        }
    }

    // ─── Render Agent Response Message ───

    function renderAgentMessage(data) {
        var el = document.createElement("div");
        el.className = "message message--agent";

        var avatarHtml = '<div class="message-avatar"><svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div>';

        // Response text
        var responseText = data.response || "(응답 없음)";

        // Meta items
        var metaHtml = "";
        var metaItems = [];
        if (data.row_count != null) {
            metaItems.push('<div class="meta-item"><span class="meta-label">ROWS</span><span class="meta-value">' + data.row_count + '건</span></div>');
        }
        if (data.processing_time_ms != null) {
            metaItems.push('<div class="meta-item"><span class="meta-label">TIME</span><span class="meta-value">' + (data.processing_time_ms / 1000).toFixed(1) + 's</span></div>');
        }
        if (data.query_id) {
            metaItems.push('<div class="meta-item"><span class="meta-label">ID</span><span class="meta-value">' + data.query_id.substring(0, 8) + '</span></div>');
        }
        if (metaItems.length > 0) {
            metaHtml = '<div class="message-meta">' + metaItems.join("") + '</div>';
        }

        // SQL block
        var sqlHtml = "";
        if (data.executed_sql) {
            var sqlId = "sql-" + Date.now();
            sqlHtml =
                '<div class="message-sql">' +
                    '<button class="message-sql-toggle" onclick="toggleSql(\'' + sqlId + '\', this)">' +
                        '<span class="arrow">&#9654;</span> 실행된 SQL 보기' +
                    '</button>' +
                    '<pre class="message-sql-code" id="' + sqlId + '">' + escapeHtml(data.executed_sql) + '</pre>' +
                '</div>';
        }

        // Download button
        var downloadHtml = "";
        if (data.has_file && data.query_id) {
            downloadHtml =
                '<a class="message-download" href="/api/v1/query/' + encodeURIComponent(data.query_id) + '/download">' +
                    '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                    escapeHtml(data.file_name || "파일") + ' 다운로드' +
                '</a>';
        }

        // CSV download button
        var csvHtml = "";
        if (data.row_count > 0 && data.query_id) {
            csvHtml =
                '<a class="message-download message-download--csv" href="/api/v1/query/' + encodeURIComponent(data.query_id) + '/download-csv">' +
                    '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>' +
                    'CSV 다운로드 (' + data.row_count + '건)' +
                '</a>';
        }

        // Mapping report buttons
        var reportHtml = "";
        if (data.has_mapping_report && data.query_id) {
            reportHtml =
                '<div class="mapping-report-actions">' +
                    '<a class="message-download message-download--report" href="/api/v1/query/' + encodeURIComponent(data.query_id) + '/mapping-report">' +
                        '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>' +
                        '매핑 보고서 다운로드' +
                    '</a>' +
                    '<label class="message-download message-download--upload" data-query-id="' + data.query_id + '">' +
                        '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>' +
                        '수정된 보고서 업로드' +
                        '<input type="file" accept=".md" style="display:none" onchange="handleMappingFeedbackUpload(this)">' +
                    '</label>' +
                '</div>';
        }

        el.innerHTML =
            avatarHtml +
            '<div class="message-content">' +
                '<div class="message-bubble">' +
                    '<div class="response-text">' + renderMarkdown(responseText) + '</div>' +
                    metaHtml +
                    sqlHtml +
                    downloadHtml +
                    csvHtml +
                    reportHtml +
                '</div>' +
                '<div class="message-time">' + formatTime(new Date()) + '</div>' +
            '</div>';

        chatMessages.appendChild(el);
        scrollToBottomIfSticky();
    }

    // ─── Create Streaming Agent Message ───

    function createStreamingMessage() {
        // 새 스트리밍 시작 — 이전 스트림의 잔여 rAF가 새 버블에 옛 텍스트를 렌더하지 않도록 초기화
        _streamAccumulated = "";
        var el = document.createElement("div");
        el.className = "message message--agent";
        el.id = "streamingMessage";

        var avatarHtml = '<div class="message-avatar"><svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div>';

        el.innerHTML =
            avatarHtml +
            '<div class="message-content">' +
                '<div class="message-bubble">' +
                    '<div class="response-text" id="streamingText"></div>' +
                    '<span class="typing-cursor" id="streamingCursor"></span>' +
                    '<div id="streamingMeta"></div>' +
                    '<div id="streamingSql"></div>' +
                '</div>' +
                '<div class="message-time" id="streamingTime"></div>' +
            '</div>';

        chatMessages.appendChild(el);
        scrollToBottomIfSticky();
        return el;
    }

    // ─── SSE Streaming Query ───

    async function executeStreamingQuery(query) {
        isProcessing = true;
        sendBtn.disabled = true;

        // Show processing first
        renderProcessingMessage();
        resetProgressPanel();

        try {
            // Try SSE streaming first
            // 멀티턴: 진행 중 세션이 있으면 thread_id를 함께 전송해야 백엔드가
            // 체크포인트(이전 대화 맥락)를 복원한다. 누락 시 매 턴 새 세션(1턴)으로 처리됨.
            var streamBody = { query: query };
            if (currentThreadId) {
                streamBody.thread_id = currentThreadId;
            }
            var response = await fetch("/api/v1/query/stream", {
                method: "POST",
                headers: Object.assign({ "Content-Type": "application/json" }, getAuthHeaders()),
                body: JSON.stringify(streamBody),
            });

            if (response.status === 404 || response.status === 405) {
                // SSE endpoint not available, fallback to regular POST
                removeProcessingMessage();
                await executeFallbackQuery(query);
                return;
            }

            if (!response.ok) {
                var errData;
                try {
                    errData = await response.json();
                } catch (_e) {
                    errData = { detail: "처리 중 오류가 발생했습니다." };
                }
                removeProcessingMessage();
                showError(errData.detail || "처리 중 오류가 발생했습니다.");
                return;
            }

            // Check content type
            var contentType = response.headers.get("content-type") || "";
            if (!contentType.includes("text/event-stream")) {
                // Not SSE, treat as JSON fallback
                removeProcessingMessage();
                var jsonData = await response.json();
                renderAgentMessage(jsonData);
                messages.push({ role: "agent", data: jsonData, time: new Date() });
                return;
            }

            // Process SSE stream
            removeProcessingMessage();
            createStreamingMessage();

            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = "";
            var accumulatedText = "";
            var metaData = {};
            var done = false;

            while (!done) {
                var chunk = await reader.read();
                if (chunk.done) break;

                buffer += decoder.decode(chunk.value, { stream: true });

                // Parse SSE events from buffer
                var lines = buffer.split("\n");
                buffer = lines.pop() || ""; // Keep incomplete line in buffer

                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (line.startsWith("data: ")) {
                        var dataStr = line.substring(6);
                        try {
                            var event = JSON.parse(dataStr);
                            if (event.type === "token") {
                                accumulatedText += event.content;
                                _streamAccumulated = accumulatedText;
                                scheduleStreamingRender();   // 비파괴 렌더 + 스크롤(rAF 코얼레싱)
                            } else if (event.type === "node_start") {
                                handleNodeStart(event);
                                updateProcessingStage(event.node, "start");
                            } else if (event.type === "node_complete") {
                                handleNodeComplete(event);
                                updateProcessingStage(event.node, "complete");
                            } else if (event.type === "meta") {
                                metaData = event;
                            } else if (event.type === "done") {
                                done = true;
                                metaData = Object.assign(metaData, event);
                            } else if (event.type === "error") {
                                showError(event.message || "처리 중 오류가 발생했습니다.");
                                done = true;
                            }
                        } catch (_parseErr) {
                            // Skip malformed JSON
                        }
                    }
                }
            }

            // 권위 있는 최종 응답(서버 final_response)이 있으면 누적 토큰 대신 사용한다.
            var finalText = (typeof metaData.response === "string" && metaData.response.length > 0)
                ? metaData.response : accumulatedText;

            // Finalize streaming message
            finalizeStreamingMessage(finalText, metaData);
            currentThreadId = metaData.thread_id || currentThreadId;
            messages.push({
                role: "agent",
                data: {
                    response: finalText,
                    query_id: metaData.query_id,
                    executed_sql: metaData.executed_sql,
                    row_count: metaData.row_count,
                    processing_time_ms: metaData.processing_time_ms,
                    has_file: metaData.has_file,
                    file_name: metaData.file_name,
                },
                time: new Date(),
            });

        } catch (err) {
            removeProcessingMessage();
            // Network error - fallback to regular query
            if (err.name === "TypeError" || err.message.includes("fetch")) {
                await executeFallbackQuery(query);
            } else {
                showError("서버와의 통신에 실패했습니다: " + err.message);
            }
        } finally {
            isProcessing = false;
            sendBtn.disabled = false;
        }
    }

    function finalizeStreamingMessage(text, meta) {
        // 최종 텍스트를 렌더링한다. 스트리밍 중 누적된 토큰과 다를 수 있으므로
        // (복합 질의의 병렬 토큰 순서/인터리빙) 권위 있는 최종 응답으로 보정한다.
        var finalTextEl = document.getElementById("streamingText");
        if (finalTextEl) finalTextEl.innerHTML = renderMarkdown(text);

        // Remove cursor
        var cursor = document.getElementById("streamingCursor");
        if (cursor) cursor.remove();

        // Set time
        var timeEl = document.getElementById("streamingTime");
        if (timeEl) timeEl.textContent = formatTime(new Date());

        // Add meta
        var metaContainer = document.getElementById("streamingMeta");
        if (metaContainer) {
            var metaItems = [];
            if (meta.row_count != null) {
                metaItems.push('<div class="meta-item"><span class="meta-label">ROWS</span><span class="meta-value">' + meta.row_count + '건</span></div>');
            }
            if (meta.processing_time_ms != null) {
                metaItems.push('<div class="meta-item"><span class="meta-label">TIME</span><span class="meta-value">' + (meta.processing_time_ms / 1000).toFixed(1) + 's</span></div>');
            }
            if (meta.query_id) {
                metaItems.push('<div class="meta-item"><span class="meta-label">ID</span><span class="meta-value">' + meta.query_id.substring(0, 8) + '</span></div>');
            }
            if (metaItems.length > 0) {
                metaContainer.innerHTML = '<div class="message-meta">' + metaItems.join("") + '</div>';
            }
        }

        // Add SQL
        var sqlContainer = document.getElementById("streamingSql");
        if (sqlContainer && meta.executed_sql) {
            var sqlId = "sql-" + Date.now();
            sqlContainer.innerHTML =
                '<div class="message-sql">' +
                    '<button class="message-sql-toggle" onclick="toggleSql(\'' + sqlId + '\', this)">' +
                        '<span class="arrow">&#9654;</span> 실행된 SQL 보기' +
                    '</button>' +
                    '<pre class="message-sql-code" id="' + sqlId + '">' + escapeHtml(meta.executed_sql) + '</pre>' +
                '</div>';
        }

        // Add download button
        if (meta.has_file && meta.query_id) {
            var streamingMsg = document.getElementById("streamingMessage");
            var bubble = streamingMsg ? streamingMsg.querySelector(".message-bubble") : null;
            if (bubble) {
                var downloadHtml =
                    '<a class="message-download" href="/api/v1/query/' + encodeURIComponent(meta.query_id) + '/download">' +
                        '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                        escapeHtml(meta.file_name || "파일") + ' 다운로드' +
                    '</a>';
                bubble.insertAdjacentHTML("beforeend", downloadHtml);
            }
        }

        // Add CSV download button
        if (meta.row_count > 0 && meta.query_id) {
            var streamingMsgCsv = document.getElementById("streamingMessage");
            var bubbleCsv = streamingMsgCsv ? streamingMsgCsv.querySelector(".message-bubble") : null;
            if (bubbleCsv) {
                var csvHtml =
                    '<a class="message-download message-download--csv" href="/api/v1/query/' + encodeURIComponent(meta.query_id) + '/download-csv">' +
                        '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>' +
                        'CSV 다운로드 (' + meta.row_count + '건)' +
                    '</a>';
                bubbleCsv.insertAdjacentHTML("beforeend", csvHtml);
            }
        }

        // Add mapping report download + upload buttons
        if (meta.has_mapping_report && meta.query_id) {
            var streamingMsg2 = document.getElementById("streamingMessage");
            var bubble2 = streamingMsg2 ? streamingMsg2.querySelector(".message-bubble") : null;
            if (bubble2) {
                var reportHtml =
                    '<div class="mapping-report-actions">' +
                        '<a class="message-download message-download--report" href="/api/v1/query/' + encodeURIComponent(meta.query_id) + '/mapping-report">' +
                            '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>' +
                            '매핑 보고서 다운로드' +
                        '</a>' +
                        '<label class="message-download message-download--upload" data-query-id="' + meta.query_id + '">' +
                            '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>' +
                            '수정된 보고서 업로드' +
                            '<input type="file" accept=".md" style="display:none" onchange="handleMappingFeedbackUpload(this)">' +
                        '</label>' +
                    '</div>';
                bubble2.insertAdjacentHTML("beforeend", reportHtml);
            }
        }

        // Remove streaming IDs to prevent conflicts
        var streamingMsg = document.getElementById("streamingMessage");
        if (streamingMsg) streamingMsg.removeAttribute("id");
        ["streamingText", "streamingCursor", "streamingTime", "streamingMeta", "streamingSql"].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.removeAttribute("id");
        });

        scrollToBottomIfSticky();
    }

    // ─── Fallback (non-streaming) Query ───

    async function executeFallbackQuery(query) {
        renderProcessingMessage();
        resetProgressPanel();

        try {
            // 멀티턴: 진행 중 세션이 있으면 thread_id를 함께 전송 (체크포인트 복원).
            var queryBody = { query: query };
            if (currentThreadId) {
                queryBody.thread_id = currentThreadId;
            }
            var response = await fetch("/api/v1/query", {
                method: "POST",
                headers: Object.assign({ "Content-Type": "application/json" }, getAuthHeaders()),
                body: JSON.stringify(queryBody),
            });

            var data = await response.json();

            removeProcessingMessage();

            if (!response.ok) {
                showError(data.detail || "처리 중 오류가 발생했습니다.");
                return;
            }

            renderAgentMessage(data);
            showPostHocProgress(data);
            currentThreadId = data.thread_id || currentThreadId;
            messages.push({ role: "agent", data: data, time: new Date() });

        } catch (err) {
            removeProcessingMessage();
            showError("서버와의 통신에 실패했습니다: " + err.message);
        }
    }

    // ─── File Query (SSE streaming) ───

    async function executeFileQuery(query, file) {
        isProcessing = true;
        sendBtn.disabled = true;

        renderProcessingMessage();
        resetProgressPanel();

        var formData = new FormData();
        formData.append("query", query);
        formData.append("file", file);
        if (currentThreadId) {
            formData.append("thread_id", currentThreadId);
        }

        try {
            // SSE 스트리밍 시도
            var response = await fetch("/api/v1/query/file/stream", {
                method: "POST",
                headers: getAuthHeaders(),
                body: formData,
            });

            if (response.status === 404 || response.status === 405) {
                // 스트리밍 엔드포인트 없으면 폴백
                removeProcessingMessage();
                await _executeFileQueryFallback(formData);
                return;
            }

            if (!response.ok) {
                var errData;
                try { errData = await response.json(); } catch (_e) { errData = { detail: "처리 중 오류가 발생했습니다." }; }
                removeProcessingMessage();
                showError(errData.detail || "처리 중 오류가 발생했습니다.");
                return;
            }

            var contentType = response.headers.get("content-type") || "";
            if (!contentType.includes("text/event-stream")) {
                removeProcessingMessage();
                var jsonData = await response.json();
                renderAgentMessage(jsonData);
                showPostHocProgress(jsonData);
                currentThreadId = jsonData.thread_id || currentThreadId;
                messages.push({ role: "agent", data: jsonData, time: new Date() });
                return;
            }

            // SSE 스트림 처리 (executeStreamingQuery와 동일한 로직)
            removeProcessingMessage();
            createStreamingMessage();

            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = "";
            var accumulatedText = "";
            var metaData = {};
            var done = false;

            while (!done) {
                var chunk = await reader.read();
                if (chunk.done) break;

                buffer += decoder.decode(chunk.value, { stream: true });
                var lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (line.startsWith("data: ")) {
                        var dataStr = line.substring(6);
                        try {
                            var event = JSON.parse(dataStr);
                            if (event.type === "token") {
                                accumulatedText += event.content;
                                _streamAccumulated = accumulatedText;
                                scheduleStreamingRender();   // 비파괴 렌더 + 스크롤(rAF 코얼레싱)
                            } else if (event.type === "node_start") {
                                handleNodeStart(event);
                                updateProcessingStage(event.node, "start");
                            } else if (event.type === "node_complete") {
                                handleNodeComplete(event);
                                updateProcessingStage(event.node, "complete");
                            } else if (event.type === "meta") {
                                metaData = event;
                            } else if (event.type === "done") {
                                done = true;
                                metaData = Object.assign(metaData, event);
                            } else if (event.type === "error") {
                                showError(event.message || "처리 중 오류가 발생했습니다.");
                                done = true;
                            }
                        } catch (_parseErr) {}
                    }
                }
            }

            var finalText = (typeof metaData.response === "string" && metaData.response.length > 0)
                ? metaData.response : accumulatedText;
            finalizeStreamingMessage(finalText, metaData);
            currentThreadId = metaData.thread_id || currentThreadId;
            messages.push({
                role: "agent",
                data: {
                    response: finalText,
                    query_id: metaData.query_id,
                    executed_sql: metaData.executed_sql,
                    row_count: metaData.row_count,
                    processing_time_ms: metaData.processing_time_ms,
                    has_file: metaData.has_file,
                    file_name: metaData.file_name,
                },
                time: new Date(),
            });

        } catch (err) {
            removeProcessingMessage();
            showError("서버와의 통신에 실패했습니다: " + err.message);
        } finally {
            isProcessing = false;
            sendBtn.disabled = false;
        }
    }

    // ─── File Query Fallback (non-streaming) ───

    async function _executeFileQueryFallback(formData) {
        isProcessing = true;
        sendBtn.disabled = true;
        renderProcessingMessage();
        try {
            var response = await fetch("/api/v1/query/file", {
                method: "POST",
                headers: getAuthHeaders(),
                body: formData,
            });
            var data = await response.json();
            removeProcessingMessage();
            if (!response.ok) {
                showError(data.detail || "처리 중 오류가 발생했습니다.");
                return;
            }
            renderAgentMessage(data);
            showPostHocProgress(data);
            currentThreadId = data.thread_id || currentThreadId;
            messages.push({ role: "agent", data: data, time: new Date() });
        } catch (err) {
            removeProcessingMessage();
            showError("서버와의 통신에 실패했습니다: " + err.message);
        } finally {
            isProcessing = false;
            sendBtn.disabled = false;
        }
    }

    // ─── Utilities ───

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // marked.js 초기화 (로드된 경우에만)
    var _markedReady = false;
    if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
        try {
            marked.use({ breaks: true, gfm: true });
            _markedReady = true;
        } catch (_e) {
            console.warn('[renderMarkdown] marked.use() 실패:', _e);
        }
    } else {
        console.warn('[renderMarkdown] marked.js 미로드 — 폴백 렌더링 사용');
    }

    function renderMarkdown(text) {
        if (!text) return '';
        if (_markedReady) {
            try {
                var result = marked.parse(text);
                if (typeof result === 'string') return result;
            } catch (_e) {
                console.warn('[renderMarkdown] marked.parse 실패:', _e);
            }
        }
        return escapeHtml(text).replace(/\n/g, '<br>');
    }

    // ─── Streaming Non-destructive Render (DOM 모핑) ───
    //
    // 토큰마다 innerHTML 전체 교체 대신, 새 파싱 결과를 기존 DOM에 diff 적용한다.
    // 동일 위치·태그의 요소를 "재사용"하므로 표(table)의 가로 스크롤 위치(scrollLeft)와
    // 텍스트 드래그 선택이 보존된다. 출력 HTML은 full-parse 결과와 동일.
    // 폐쇄망 제약으로 외부 morphdom 의존성 대신 동등 동작의 경량 자체 구현 사용.

    function syncAttributes(fromEl, toEl) {
        var toAttrs = toEl.attributes;
        for (var i = 0; i < toAttrs.length; i++) {
            var a = toAttrs[i];
            if (fromEl.getAttribute(a.name) !== a.value) fromEl.setAttribute(a.name, a.value);
        }
        var fromAttrs = fromEl.attributes;
        for (var j = fromAttrs.length - 1; j >= 0; j--) {
            var name = fromAttrs[j].name;
            if (!toEl.hasAttribute(name)) fromEl.removeAttribute(name);
        }
    }

    // 두 부모의 자식들을 인덱스 기준으로 reconcile(기존 노드 재사용 → scrollLeft/선택 보존)
    function morphChildren(fromParent, toParent) {
        var toNodes = toParent.childNodes;
        var i = 0;
        while (i < toNodes.length) {
            var toNode = toNodes[i];
            var fromNode = fromParent.childNodes[i];
            if (!fromNode) {
                // 새 노드 추가(끝에 append) — toNode를 옮기지 않도록 clone
                fromParent.appendChild(toNode.cloneNode(true));
            } else if (fromNode.nodeType !== toNode.nodeType ||
                       (fromNode.nodeType === 1 && fromNode.nodeName !== toNode.nodeName)) {
                // 타입/태그 불일치 → 해당 노드만 교체
                fromParent.replaceChild(toNode.cloneNode(true), fromNode);
            } else if (fromNode.nodeType === 3 || fromNode.nodeType === 8) {
                // 텍스트/주석 → 값만 갱신
                if (fromNode.nodeValue !== toNode.nodeValue) fromNode.nodeValue = toNode.nodeValue;
            } else if (fromNode.nodeType === 1 && !fromNode.isEqualNode(toNode)) {
                // 동일 태그 요소이며 내용이 다를 때만 어트리뷰트 동기화 + 재귀
                syncAttributes(fromNode, toNode);
                morphChildren(fromNode, toNode);
            }
            // isEqualNode가 true면 변화 없음 → 기존 노드 유지(불필요 reflow 방지)
            i++;
        }
        // 남는 기존 노드 제거
        while (fromParent.childNodes.length > toNodes.length) {
            fromParent.removeChild(fromParent.lastChild);
        }
    }

    // 스트리밍 마크다운을 기존 DOM 보존하며 갱신. 실패 시 폴백(가로 스크롤 위치만 보존).
    function renderStreamingMarkdown(el, md) {
        var html = renderMarkdown(md);
        try {
            var tpl = document.createElement("div");
            tpl.innerHTML = html;
            morphChildren(el, tpl);
        } catch (_e) {
            console.warn('[renderStreamingMarkdown] morph 실패 — 폴백 사용:', _e);
            var prev = el.querySelectorAll("table");
            var sc = [];
            for (var i = 0; i < prev.length; i++) sc[i] = prev[i].scrollLeft;
            el.innerHTML = html;
            var next = el.querySelectorAll("table");
            for (var j = 0; j < next.length && j < sc.length; j++) {
                if (sc[j]) next[j].scrollLeft = sc[j];
            }
        }
    }

    // 토큰 버스트를 프레임당 1회 렌더로 코얼레싱(전체 재파싱 O(L²) 상수 절감)
    function scheduleStreamingRender() {
        if (_streamRafQueued) return;
        _streamRafQueued = true;
        requestAnimationFrame(function () {
            _streamRafQueued = false;
            var el = document.getElementById("streamingText");
            if (el) renderStreamingMarkdown(el, _streamAccumulated);
            scrollToBottomIfSticky();   // 렌더 후 높이 갱신된 상태에서 추종
        });
    }

    function isNearBottom() {
        var gap = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
        return gap <= BOTTOM_THRESHOLD_PX;
    }

    function updateScrollToBottomBtn() {
        if (!scrollToBottomBtn) return;
        var show = !isNearBottom();
        scrollToBottomBtn.classList.toggle("is-visible", show);
        // 버튼이 보이고 미확인 신규 출력이 있을 때만 강조
        scrollToBottomBtn.classList.toggle("has-new", show && hasNewContent);
    }

    // 무조건 맨 아래로 (사용자 본인 질의 등 명시적 의도)
    function scrollToBottom(smooth) {
        requestAnimationFrame(function () {
            if (smooth) {
                chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: "smooth" });
            } else {
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
            stickToBottom = true;
            hasNewContent = false;       // 맨 아래로 강제 이동 → 신규 강조 해제
            updateScrollToBottomBtn();
        });
    }

    // 고정 상태일 때만 따라 내려감 (토큰 스트리밍 / 에이전트 출력 전용)
    function scrollToBottomIfSticky() {
        if (!stickToBottom) {
            hasNewContent = true;        // 미확인 신규 출력 → 버튼 강조 대상
            updateScrollToBottomBtn();
            return;
        }
        requestAnimationFrame(function () {
            chatMessages.scrollTop = chatMessages.scrollHeight;  // 즉시(비smooth)
        });
    }

    // ─── Global function for SQL toggle ───

    window.toggleSql = function (id, btn) {
        var codeEl = document.getElementById(id);
        if (!codeEl) return;
        btn.classList.toggle("open");
        codeEl.classList.toggle("open");
    };

    // ─── Mapping Feedback Upload Handler ───

    window.handleMappingFeedbackUpload = async function (inputEl) {
        var file = inputEl.files[0];
        if (!file) return;

        var queryId = inputEl.closest("[data-query-id]").getAttribute("data-query-id");
        if (!queryId) {
            showError("query_id를 찾을 수 없습니다.");
            return;
        }

        var label = inputEl.closest("label");
        var origText = label ? label.textContent.trim() : "";
        if (label) label.style.opacity = "0.6";

        try {
            var formData = new FormData();
            formData.append("file", file);
            formData.append("query_id", queryId);

            var response = await fetch("/api/v1/query/mapping-feedback", {
                method: "POST",
                headers: getAuthHeaders(),
                body: formData,
            });

            var result = await response.json();

            if (!response.ok) {
                showError(result.detail || "피드백 처리 중 오류가 발생했습니다.");
                return;
            }

            // Show result as a chat message
            var summary = "";
            if (result.status === "no_changes") {
                summary = result.summary || "변경사항이 없습니다.";
            } else if (result.status === "applied") {
                var d = result.diff || {};
                var parts = [];
                if (d.added) parts.push(d.added + "건 추가");
                if (d.modified) parts.push(d.modified + "건 수정");
                if (d.deleted) parts.push(d.deleted + "건 삭제");
                summary = "매핑 피드백이 Redis에 반영되었습니다: " + parts.join(", ");
            }

            // Add feedback result as agent message
            var el = document.createElement("div");
            el.className = "message message--agent";
            el.innerHTML =
                '<div class="message-avatar"><svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div>' +
                '<div class="message-content">' +
                    '<div class="message-bubble">' +
                        '<div class="response-text">' + escapeHtml(summary) + '</div>' +
                    '</div>' +
                    '<div class="message-time">' + formatTime(new Date()) + '</div>' +
                '</div>';
            chatMessages.appendChild(el);
            scrollToBottomIfSticky();

        } catch (err) {
            showError("피드백 업로드 실패: " + err.message);
        } finally {
            if (label) label.style.opacity = "1";
            inputEl.value = "";
        }
    };

    // ─── Progress Panel ───

    function resetProgressPanel() {
        progressPipeline.innerHTML = "";
        progressEmpty.style.display = "none";
    }

    function showProgressEmpty() {
        progressPipeline.innerHTML = "";
        progressEmpty.style.display = "flex";
    }

    function showPostHocProgress(data) {
        resetProgressPanel();
        progressEmpty.style.display = "none";

        // 쿼리 처리 완료 단계들을 순서대로 표시
        var steps = [];

        steps.push({ node: "input_parser", data: null });

        if (data.executed_sql) {
            steps.push({ node: "schema_analyzer", data: null });
            steps.push({ node: "query_generator", data: { generated_sql: data.executed_sql } });
            steps.push({ node: "query_validator", data: { passed: true, reason: "" } });
        }

        if (data.row_count != null) {
            steps.push({ node: "query_executor", data: { row_count: data.row_count, preview_rows: [] } });
        }

        steps.push({ node: "output_generator", data: { status: "완료" } });

        steps.forEach(function (step) {
            handleNodeStart({ node: step.node, timestamp_ms: 0 });
            handleNodeComplete({
                node: step.node,
                data: step.data || {},
                timestamp_ms: 0,
            });
        });
    }

    function handleNodeStart(event) {
        var node = event.node;
        var label = nodeLabels[node] || node;
        var tooltip = nodeTooltips[node] || "";

        progressEmpty.style.display = "none";

        // Mark any previously active step as done (if no explicit complete came)
        var activeSteps = progressPipeline.querySelectorAll(".pipeline-step.active");
        activeSteps.forEach(function (el) {
            el.classList.remove("active");
            el.classList.add("done");
        });

        // Create new step
        var stepEl = document.createElement("div");
        stepEl.className = "pipeline-step active";
        stepEl.id = "step-" + node;
        stepEl.setAttribute("data-node", node);

        var tooltipAttr = tooltip ? ' data-tooltip="' + escapeHtml(tooltip) + '"' : "";
        stepEl.innerHTML =
            '<div class="pipeline-step-header" onclick="togglePipelineStep(this)">' +
                '<span class="pipeline-step-dot"></span>' +
                '<span class="pipeline-step-name"' + tooltipAttr + '>' + escapeHtml(label) + '</span>' +
                '<span class="pipeline-step-time" data-start="' + (event.timestamp_ms || 0) + '"></span>' +
                '<span class="pipeline-step-arrow">&#9654;</span>' +
            '</div>' +
            '<div class="pipeline-step-body"></div>';

        progressPipeline.appendChild(stepEl);
        progressPipeline.scrollTop = progressPipeline.scrollHeight;
    }

    function handleNodeComplete(event) {
        var node = event.node;
        var data = event.data || {};
        var stepEl = document.getElementById("step-" + node);

        if (!stepEl) return;

        // Update status
        stepEl.classList.remove("active");
        if (node === "error_response") {
            stepEl.classList.add("error");
        } else {
            stepEl.classList.add("done");
        }

        // Show elapsed time
        var timeEl = stepEl.querySelector(".pipeline-step-time");
        if (timeEl) {
            var startMs = parseFloat(timeEl.getAttribute("data-start") || "0");
            var elapsed = ((event.timestamp_ms || 0) - startMs) / 1000;
            if (elapsed > 0) {
                timeEl.textContent = elapsed.toFixed(1) + "s";
            }
        }

        // Fill body with data
        var bodyEl = stepEl.querySelector(".pipeline-step-body");
        if (bodyEl && data && Object.keys(data).length > 0) {
            bodyEl.innerHTML = renderNodeData(node, data);
            // Auto-expand step
            stepEl.classList.add("expanded");
        }
    }

    function renderNodeData(node, data) {
        var html = "";

        if (node === "input_parser") {
            if (data.parsed_requirements) {
                html += renderSection("파싱된 요구사항", renderJsonPreview(data.parsed_requirements));
            }
            if (data.template_structure) {
                html += renderSection("템플릿 구조", renderJsonPreview(data.template_structure));
            }
        }

        else if (node === "field_mapper") {
            if (data.mapped_count != null && data.total_count != null) {
                var pct = data.total_count > 0 ? Math.round(data.mapped_count / data.total_count * 100) : 0;
                html += renderSection("매핑 결과", '<span class="step-data-badge step-data-badge--info">' + data.mapped_count + '/' + data.total_count + ' (' + pct + '%)</span>');
            }
            if (data.sources) {
                var srcParts = [];
                if (data.sources.hint) srcParts.push("힌트: " + data.sources.hint);
                if (data.sources.synonym) srcParts.push("유사어: " + data.sources.synonym);
                if (data.sources.eav_synonym) srcParts.push("EAV: " + data.sources.eav_synonym);
                if (data.sources.llm_inferred) srcParts.push("LLM: " + data.sources.llm_inferred);
                if (srcParts.length > 0) {
                    html += renderSection("매핑 출처", '<div class="step-data-value">' + escapeHtml(srcParts.join(", ")) + '</div>');
                }
            }
            if (data.has_mapping_report) {
                html += renderSection("보고서", '<span class="step-data-badge step-data-badge--success">생성됨</span>');
            }
        }

        else if (node === "context_resolver") {
            var turnLabel = data.turn === 1 ? "신규 대화 (1턴)" : data.turn + "번째 턴";
            html += renderSection("대화 상태", '<span class="step-data-badge step-data-badge--info">' + escapeHtml(turnLabel) + '</span>');
        }

        else if (node === "semantic_router") {
            var intentMap = {
                data_query: "DB 조회",
                general_inference: "일반 추론",
                cache_management: "캐시 관리",
                synonym_registration: "유사어 등록",
            };
            var intentLabel = intentMap[data.routing_intent] || data.routing_intent || "알 수 없음";
            var intentBadgeClass = data.routing_intent === "data_query" ? "step-data-badge--success" : "step-data-badge--info";
            html += renderSection("분류된 의도", '<span class="step-data-badge ' + intentBadgeClass + '">' + escapeHtml(intentLabel) + '</span>');
            if (data.active_db_id) {
                html += renderSection("선택된 DB", '<span class="step-data-value">' + escapeHtml(data.active_db_id) + (data.is_multi_db ? " (멀티 DB)" : "") + '</span>');
            }
            if (data.targets && data.targets.length > 0) {
                var targetHtml = '<ul class="step-data-list">';
                data.targets.forEach(function (t) {
                    targetHtml += "<li><strong>" + escapeHtml(t.db_id) + "</strong>";
                    if (t.reason) targetHtml += ": " + escapeHtml(t.reason);
                    targetHtml += "</li>";
                });
                targetHtml += "</ul>";
                html += renderSection("라우팅 근거", targetHtml);
            }
        }

        else if (node === "field_mapper") {
            if (data.skipped) {
                html += renderSection("매핑 상태", '<span class="step-data-badge step-data-badge--info">자연어 질의 — 필드 매핑 불필요</span>');
            } else {
                if (data.mapped_count != null && data.total_count != null) {
                    var pct = data.total_count > 0 ? Math.round(data.mapped_count / data.total_count * 100) : 0;
                    html += renderSection("매핑 결과", '<span class="step-data-badge step-data-badge--info">' + data.mapped_count + '/' + data.total_count + ' (' + pct + '%)</span>');
                }
                if (data.sources) {
                    var srcParts = [];
                    if (data.sources.hint) srcParts.push("힌트: " + data.sources.hint);
                    if (data.sources.synonym) srcParts.push("유사어: " + data.sources.synonym);
                    if (data.sources.eav_synonym) srcParts.push("EAV: " + data.sources.eav_synonym);
                    if (data.sources.llm_inferred) srcParts.push("LLM: " + data.sources.llm_inferred);
                    if (srcParts.length > 0) {
                        html += renderSection("매핑 출처", '<div class="step-data-value">' + escapeHtml(srcParts.join(", ")) + '</div>');
                    }
                }
                if (data.has_mapping_report) {
                    html += renderSection("보고서", '<span class="step-data-badge step-data-badge--success">생성됨</span>');
                }
            }
        }

        else if (node === "schema_analyzer") {
            if (data.cache_source) {
                var cacheBadgeClass = data.cache_source === "DB 직접 조회" ? "step-data-badge--warning" : "step-data-badge--success";
                html += renderSection("스키마 캐시", '<span class="step-data-badge ' + cacheBadgeClass + '">' + escapeHtml(data.cache_source) + '</span>');
            }
            if (data.relevant_tables && data.relevant_tables.length > 0) {
                var listHtml = '<ul class="step-data-list">';
                data.relevant_tables.forEach(function (t) {
                    listHtml += "<li>" + escapeHtml(t) + "</li>";
                });
                listHtml += "</ul>";
                html += renderSection("관련 테이블", listHtml);
            }
        }

        else if (node === "query_generator") {
            if (data.generated_sql) {
                html += renderSection("생성된 SQL", '<pre class="step-data-code">' + escapeHtml(data.generated_sql) + "</pre>");
            }
            if (data.synonym_usage) {
                var su = data.synonym_usage;
                var suTypeLabel = { eav_name: "EAV 속성", resource_type: "리소스 타입", column: "컬럼" };
                if (su.mappings && su.mappings.length > 0) {
                    var suHtml = '<ul class="step-data-list">';
                    su.mappings.forEach(function (m) {
                        suHtml += "<li><strong>" + escapeHtml(m.key) + "</strong>";
                        suHtml += ' <span class="step-data-badge step-data-badge--info">' + escapeHtml(suTypeLabel[m.type] || m.type) + "</span>";
                        if (m.matched_user_terms && m.matched_user_terms.length > 0) {
                            suHtml += ' <span class="step-data-badge step-data-badge--success">사용자 용어: ' + m.matched_user_terms.map(escapeHtml).join(", ") + "</span>";
                        }
                        if (m.synonyms && m.synonyms.length > 0) {
                            suHtml += '<div class="step-data-value">유사어: ' + m.synonyms.map(escapeHtml).join(", ") + "</div>";
                        }
                        suHtml += "</li>";
                    });
                    suHtml += "</ul>";
                    html += renderSection("유사어 매핑 (생성된 SQL 기준)", suHtml);
                }
                if (su.unregistered && su.unregistered.length > 0) {
                    var unregHtml = '<ul class="step-data-list">';
                    su.unregistered.forEach(function (u) {
                        unregHtml += "<li><strong>" + escapeHtml(u.literal) + "</strong>";
                        unregHtml += ' <span class="step-data-badge step-data-badge--info">' + escapeHtml(suTypeLabel[u.type] || u.type) + "</span>";
                        unregHtml += ' <span class="step-data-badge step-data-badge--warning">사전 미등록 (LLM 직접 추론)</span></li>';
                    });
                    unregHtml += "</ul>";
                    html += renderSection("사전 미등록 항목", unregHtml);
                }
            }
        }

        else if (node === "query_validator") {
            var badge = data.passed
                ? '<span class="step-data-badge step-data-badge--success">PASS</span>'
                : '<span class="step-data-badge step-data-badge--error">FAIL</span>';
            html += renderSection("검증 결과", badge);
            if (data.reason) {
                html += renderSection("사유", '<div class="step-data-value">' + escapeHtml(data.reason) + "</div>");
            }
        }

        else if (node === "query_executor") {
            if (data.error) {
                html += renderSection("에러", '<span class="step-data-badge step-data-badge--error">' + escapeHtml(data.error) + "</span>");
            } else {
                html += renderSection("조회 건수", '<span class="step-data-badge step-data-badge--info">' + (data.row_count || 0) + "건</span>");
            }
            if (data.preview_rows && data.preview_rows.length > 0) {
                html += renderSection("미리보기 (최대 10행)", renderDataTable(data.preview_rows));
            }
        }

        else if (node === "result_organizer") {
            if (data.summary) {
                html += renderSection("요약", '<div class="step-data-value">' + escapeHtml(data.summary) + "</div>");
            }
            var suffBadge = data.is_sufficient
                ? '<span class="step-data-badge step-data-badge--success">충분</span>'
                : '<span class="step-data-badge step-data-badge--error">부족</span>';
            html += renderSection("데이터 충분성", suffBadge);
            if (data.row_count != null) {
                html += renderSection("정리된 행 수", '<span class="step-data-badge step-data-badge--info">' + data.row_count + "건</span>");
            }
            if (data.column_mapping) {
                html += renderSection("컬럼 매핑", renderJsonPreview(data.column_mapping));
            }
        }

        else if (node === "output_generator" || node === "general_inference") {
            html += renderSection("상태", '<span class="step-data-badge step-data-badge--success">' + escapeHtml(data.status || "완료") + "</span>");
        }

        else if (node === "error_response") {
            html += renderSection("에러", '<div class="step-data-value" style="color:var(--error)">' + escapeHtml(data.error || "") + "</div>");
        }

        else if (node === "intent_planner") {
            var cnt = data.task_count != null ? data.task_count : (data.tasks ? data.tasks.length : 0);
            html += renderSection("의도 분석", '<span class="step-data-badge step-data-badge--info">' + escapeHtml(cnt + "개 작업으로 분석됨") + "</span>");
            if (data.tasks && data.tasks.length > 0) {
                html += renderSection("작업 목록", renderTaskList(data.tasks));
            }
        }

        else if (node === "agent_orchestrator") {
            if (data.tasks && data.tasks.length > 0) {
                html += renderSection("작업 진행", renderTaskList(data.tasks));
            }
        }

        else if (node === "replanner") {
            if (data.replan_history && data.replan_history.length > 0) {
                var histHtml = '<ul class="step-data-list">';
                data.replan_history.forEach(function (h) {
                    histHtml += "<li><strong>재계획 " + (h.count || "?") + "회</strong>";
                    histHtml += ' <span class="step-data-badge step-data-badge--warning">작업 ' + (h.added || 0) + "개 추가</span>";
                    if (h.reason) histHtml += '<div class="step-data-value">' + escapeHtml(h.reason) + "</div>";
                    histHtml += "</li>";
                });
                histHtml += "</ul>";
                html += renderSection("재계획 이력", histHtml);
            }
            if (!data.needs_replan) {
                html += renderSection("재계획 상태", '<span class="step-data-badge step-data-badge--success">추가 작업 없음 (완료)</span>');
            }
        }

        else if (node === "result_aggregator") {
            html += renderSection("상태", '<span class="step-data-badge step-data-badge--success">' + escapeHtml(data.status || "통합 완료") + "</span>");
        }

        else {
            // Generic fallback
            html += renderSection("데이터", renderJsonPreview(data));
        }

        return html;
    }

    function renderSection(label, contentHtml) {
        return '<div class="step-data-section"><div class="step-data-label">' + escapeHtml(label) + "</div>" + contentHtml + "</div>";
    }

    // 다중 의도 task 목록 렌더링 (의도 분석 / 작업 실행 단계 공용)
    function renderTaskList(tasks) {
        var html = '<ul class="step-data-list">';
        tasks.forEach(function (t, idx) {
            var label = agentLabels[t.agent] || t.agent || "작업";
            var ordinal = t.order != null ? t.order : (idx + 1);
            var statusBadge = "";
            if (t.status === "completed") {
                statusBadge = ' <span class="step-data-badge step-data-badge--success">완료</span>';
            } else if (t.status === "failed") {
                statusBadge = ' <span class="step-data-badge step-data-badge--error">실패</span>';
            } else if (t.status === "in_progress") {
                statusBadge = ' <span class="step-data-badge step-data-badge--info">진행 중</span>';
            } else if (t.status) {
                statusBadge = ' <span class="step-data-badge step-data-badge--info">대기</span>';
            }
            html += "<li><strong>" + ordinal + ". " + escapeHtml(label) + "</strong>" + statusBadge;
            if (t.sub_query) {
                html += '<div class="step-data-value">' + escapeHtml(t.sub_query) + "</div>";
            }
            // 대상 DB (b0 등 오선택을 즉시 확인하기 위해 노출)
            if (t.target_db_ids && t.target_db_ids.length > 0) {
                html += '<div class="step-data-value">대상 DB: ' + escapeHtml(t.target_db_ids.join(", ")) + "</div>";
            }
            // 행 수
            if (t.row_count != null) {
                html += ' <span class="step-data-badge step-data-badge--info">' + t.row_count + "건</span>";
            }
            // 생성된 SQL (orchestration에서 어떤 쿼리가 만들어졌는지 확인)
            if (t.generated_sql) {
                html += '<pre class="step-data-code">' + escapeHtml(t.generated_sql) + "</pre>";
            }
            // DB별 실행 에러 (예: polestar_b0 SQL0204N)
            if (t.db_errors) {
                Object.keys(t.db_errors).forEach(function (dbId) {
                    html += '<div class="step-data-value" style="color:var(--error)">' + escapeHtml(dbId + ": " + t.db_errors[dbId]) + "</div>";
                });
            } else if (t.error) {
                html += '<div class="step-data-value" style="color:var(--error)">' + escapeHtml(t.error) + "</div>";
            }
            html += "</li>";
        });
        html += "</ul>";
        return html;
    }

    function renderJsonPreview(obj) {
        var str = JSON.stringify(obj, null, 2);
        if (str.length > 500) str = str.substring(0, 500) + "\n...";
        return '<pre class="step-data-code">' + escapeHtml(str) + "</pre>";
    }

    function renderDataTable(rows) {
        if (!rows || rows.length === 0) return '<div class="step-data-value">데이터 없음</div>';

        var keys = Object.keys(rows[0]);
        var html = '<div style="overflow-x:auto"><table class="step-data-table"><thead><tr>';
        keys.forEach(function (k) {
            html += "<th>" + escapeHtml(k) + "</th>";
        });
        html += "</tr></thead><tbody>";

        rows.forEach(function (row) {
            html += "<tr>";
            keys.forEach(function (k) {
                var val = row[k];
                if (val == null) val = "";
                html += '<td title="' + escapeHtml(String(val)).replace(/"/g, "&quot;") + '">' + escapeHtml(String(val)) + "</td>";
            });
            html += "</tr>";
        });

        html += "</tbody></table></div>";
        return html;
    }

    // Global function for toggling pipeline steps
    window.togglePipelineStep = function (headerEl) {
        var step = headerEl.parentElement;
        step.classList.toggle("expanded");
    };

    // ─── Alarm Notification SSE ───

    var ALARM_SEVERITY_COLORS = {
        "심각": "#dc3545",
        "경고": "#fd7e14",
        "주의": "#ffc107",
        "해소": "#28a745"
    };

    // Plan 47: 패턴 근거 표 렌더 헬퍼 — history_stats(결정적 통계)를 그대로 표시한다.

    function fmtHistTs(iso) {
        // "2026-06-11T14:35:00" → "06-11 14:35" (타임존 변환 없이 문자열 슬라이스)
        if (!iso || typeof iso !== "string" || iso.length < 16) return "-";
        return iso.slice(5, 16).replace("T", " ");
    }

    function fmtInterval(mins) {
        if (mins === null || mins === undefined) return "";
        return mins >= 60 ? (mins / 60).toFixed(1) + "시간" : Math.round(mins) + "분";
    }

    var HIST_SOURCE_LABEL = {
        "polestar_db": "폴스타 DB",
        "cache": "폴스타 DB (캐시)",
        "simulated": "시뮬레이션"
    };

    function renderHistoryEvidence(hs, alarmTimeIso) {
        if (!hs) return "";  // 이력 없으면 표 생략 — 문장만 표시 (graceful degradation)

        // 1) 근거 요약표
        var rows = [];
        rows.push(["발생 빈도",
            "총 " + hs.total_count + "건 (24h " + hs.count_24h +
            " / 7일 " + hs.count_7d + " / 30일 " + hs.count_30d + ")"]);
        if (hs.median_interval_minutes !== null && hs.median_interval_minutes !== undefined) {
            var iv = "중앙값 " + fmtInterval(hs.median_interval_minutes);
            if (hs.interval_cv !== null && hs.interval_cv !== undefined) {
                iv += " · 변동 " + hs.interval_cv.toFixed(2);
            }
            if (hs.period_label) iv += " → " + hs.period_label;
            rows.push(["발생 간격", iv]);
        }
        if (hs.first_seen || hs.last_seen) {
            rows.push(["최초/직전", fmtHistTs(hs.first_seen) + " / " + fmtHistTs(hs.last_seen)]);
        }
        if (alarmTimeIso) rows.push(["이번 발생", fmtHistTs(alarmTimeIso)]);
        if (hs.truncated) rows.push(["참고", "이력 일부만 반영 (상한 도달)"]);

        var summaryRows = rows.map(function (r) {
            return '<tr><th>' + escapeHtml(r[0]) + '</th><td>' + escapeHtml(r[1]) + '</td></tr>';
        }).join("");
        var srcLabel = HIST_SOURCE_LABEL[hs.source] || hs.source || "이력";
        var summary =
            '<table class="alarm-evidence">' +
                '<caption>근거 · ' + escapeHtml(srcLabel) + '</caption>' +
                summaryRows +
            '</table>';

        // 2) 시간대 분포 막대 (최근 30일) — 현재 발생 시각 강조
        var hist = hs.hour_histogram || {};
        var hours = Object.keys(hist).map(function (k) { return parseInt(k, 10); });
        var curHour = (alarmTimeIso && alarmTimeIso.length >= 13)
            ? parseInt(alarmTimeIso.slice(11, 13), 10) : -1;
        // 현재 시각대가 이력에 없으면 0건 행으로 추가하여 "시간대 차이"를 드러낸다
        if (curHour >= 0 && hours.indexOf(curHour) === -1) hours.push(curHour);
        hours.sort(function (a, b) { return a - b; });

        var histHtml = "";
        if (hours.length) {
            var maxc = 1;
            hours.forEach(function (h) {
                var c = hist[String(h)] || 0;
                if (c > maxc) maxc = c;
            });
            var barRows = hours.map(function (h) {
                var c = hist[String(h)] || 0;
                var pct = Math.round(c / maxc * 100);
                var isCur = (h === curHour);
                var hh = (h < 10 ? "0" + h : "" + h) + "시";
                return '<div class="alarm-hist-row' + (isCur ? ' is-current' : '') + '">' +
                    '<span class="alarm-hist-hour">' + hh + '</span>' +
                    '<span class="alarm-hist-track">' +
                        '<span class="alarm-hist-bar" style="width:' + pct + '%"></span>' +
                    '</span>' +
                    '<span class="alarm-hist-cnt">' + c + (isCur ? ' ← 이번' : '') + '</span>' +
                    '</div>';
            }).join("");
            histHtml =
                '<div class="alarm-hist">' +
                    '<div class="alarm-hist-title">시간대 분포 (최근 30일)</div>' +
                    barRows +
                '</div>';
        }

        return summary + histHtml;
    }

    function fmtPctCell(v) {
        return (v === null || v === undefined) ? "-" : Number(v).toFixed(1) + "%";
    }

    // Plan 47-1: 영향 프로세스 표 — CPU/메모리 알람의 상위 N 점유 프로세스.
    // args는 백엔드에서 이미 마스킹된 값만 전달되므로 그대로 표시한다.
    function renderProcessEvidence(ps) {
        if (!ps || !ps.top || !ps.top.length) return "";
        var isMem = ps.alarm_kind === "memory";
        var metricLabel = isMem ? "메모리" : "CPU";
        var caption = "영향 프로세스 · " + metricLabel + " 상위";
        var meta = [];
        if (ps.captured_at) meta.push(fmtHistTs(ps.captured_at) + " 기준");
        if (ps.total_count !== null && ps.total_count !== undefined) {
            meta.push("전체 " + ps.total_count + "개");
        }
        if (meta.length) caption += " (" + meta.join(", ") + ")";

        var head =
            '<tr><th>프로세스</th><th>PID</th>' +
            '<th class="alarm-proc-num' + (isMem ? '' : ' is-primary') + '">CPU</th>' +
            '<th class="alarm-proc-num' + (isMem ? ' is-primary' : '') + '">MEM</th>' +
            '<th>사용자</th></tr>';
        var body = ps.top.map(function (p) {
            var mainRow = '<tr>' +
                '<td>' + escapeHtml(p.name || "-") + '</td>' +
                '<td class="alarm-proc-num">' + escapeHtml(String(p.pid)) + '</td>' +
                '<td class="alarm-proc-num' + (isMem ? '' : ' is-primary') + '">' +
                    fmtPctCell(p.p100cpu) + '</td>' +
                '<td class="alarm-proc-num' + (isMem ? ' is-primary' : '') + '">' +
                    fmtPctCell(p.pmem) + '</td>' +
                '<td>' + escapeHtml(p.user || "-") + '</td>' +
                '</tr>';
            // 실행 파라미터(args, 마스킹됨)를 행 아래 전체폭 보조 줄로 표시 — 서비스 추적용
            var argsRow = p.args
                ? '<tr class="alarm-proc-args"><td colspan="5">' +
                    escapeHtml(p.args) + '</td></tr>'
                : "";
            return mainRow + argsRow;
        }).join("");
        return '<table class="alarm-evidence alarm-proc-table">' +
            '<caption>' + escapeHtml(caption) + '</caption>' +
            head + body +
            '</table>';
    }

    function renderAlarmMessage(data) {
        var el = document.createElement("div");
        el.className = "message message--alarm";

        var severityColor = ALARM_SEVERITY_COLORS[data.severity_label] || "#fd7e14";
        var alarmSvg = '<svg viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';

        // Plan 47: 패턴 분석 배지 — is_routine=true는 회색(일상), false는 강조색(확인 필요)
        var patternHtml = "";
        if (data.pattern_type) {
            var badgeColor = data.is_routine === true ? "#6c757d" : "#dc3545";
            var badgeText = data.pattern_type;
            if (data.is_routine === true) {
                badgeText += " · 일상 알람";
            } else if (data.is_routine === false) {
                badgeText += " · 확인 필요";
            }
            patternHtml =
                '<div class="alarm-section">' +
                    '<span class="alarm-section-label">패턴 분석</span>' +
                    '<p><span style="color:' + badgeColor + ';font-weight:bold">[' +
                    escapeHtml(badgeText) + ']</span> ' +
                    escapeHtml(data.pattern_analysis || "") + '</p>' +
                    renderHistoryEvidence(data.history_stats, data.alarm_time) +
                '</div>';
        }

        // Plan 47-1: 영향 프로세스 표 (CPU/메모리 알람만 — 스냅샷 없으면 생략)
        var processHtml = "";
        var procTable = renderProcessEvidence(data.process_snapshot);
        if (procTable) {
            processHtml =
                '<div class="alarm-section">' +
                    '<span class="alarm-section-label">영향 프로세스</span>' +
                    procTable +
                '</div>';
        }

        // D-049: incident 확인(ack) 버튼 — incident_id가 있을 때만 표시(비-incident 알람 불변)
        var ackHtml = "";
        if (data.incident_id) {
            ackHtml =
                '<div class="alarm-section alarm-ack-section">' +
                    '<button type="button" class="btn-alarm-ack">확인</button>' +
                    '<span class="alarm-ack-msg"></span>' +
                '</div>';
        }

        el.innerHTML =
            '<div class="message-avatar">' + alarmSvg + '</div>' +
            '<div class="message-content">' +
                '<div class="message-bubble">' +
                    '<div class="alarm-header">' +
                        '<span style="color:' + severityColor + '">[' + escapeHtml(data.severity_label) + ']</span> ' +
                        escapeHtml(data.resource_name) +
                        '<span class="alarm-host"> (' + escapeHtml(data.hostname) + ')</span>' +
                    '</div>' +
                    '<div class="alarm-name">' + escapeHtml(data.alarm_name) + '</div>' +
                    '<div class="alarm-section">' +
                        '<span class="alarm-section-label">요약</span>' +
                        '<p>' + escapeHtml(data.summary) + '</p>' +
                    '</div>' +
                    '<div class="alarm-section">' +
                        '<span class="alarm-section-label">추정 원인</span>' +
                        '<p>' + escapeHtml(data.probable_cause) + '</p>' +
                    '</div>' +
                    '<div class="alarm-section">' +
                        '<span class="alarm-section-label">권고 조치</span>' +
                        '<p>' + escapeHtml(data.recommended_action) + '</p>' +
                    '</div>' +
                    processHtml +
                    patternHtml +
                    ackHtml +
                '</div>' +
            '</div>';

        if (chatWelcome && !chatWelcome.classList.contains("hidden")) {
            chatWelcome.classList.add("hidden");
        }
        chatMessages.appendChild(el);

        // D-049: ack 버튼 이벤트 바인딩(closure로 incident_id 캡처 — 인라인 onclick 미사용)
        if (data.incident_id) {
            var ackBtn = el.querySelector(".btn-alarm-ack");
            var ackMsg = el.querySelector(".alarm-ack-msg");
            if (ackBtn) {
                bindIncidentAck(ackBtn, ackMsg, data.incident_id);
            }
        }

        scrollToBottomIfSticky();
    }

    // D-049: incident 확인(ack) 버튼 핸들러를 바인딩한다.
    // POST /api/v1/alarm/incidents/{id}/ack → {acked, incident_id}.
    // 성공(acked) 시 "확인됨 · HH:MM:SS" 비활성, 이미 처리됨(acked=false)이면 "이미 확인됨",
    // 실패(네트워크/503)면 옆 에러 텍스트 + 재시도 가능(카드 자체는 유지 — graceful).
    function bindIncidentAck(btn, msgEl, incidentId) {
        btn.addEventListener("click", function () {
            btn.disabled = true;
            if (msgEl) msgEl.textContent = "";
            fetch("/api/v1/alarm/incidents/" + encodeURIComponent(incidentId) + "/ack", {
                method: "POST",
                headers: getAuthHeaders()
            })
                .then(function (resp) {
                    if (!resp.ok) throw new Error("HTTP " + resp.status);
                    return resp.json();
                })
                .then(function (result) {
                    btn.classList.add("btn-alarm-ack--done");
                    btn.disabled = true;
                    if (result && result.acked) {
                        var now = new Date();
                        var hh = String(now.getHours()).padStart(2, "0");
                        var mm = String(now.getMinutes()).padStart(2, "0");
                        var ss = String(now.getSeconds()).padStart(2, "0");
                        btn.textContent = "확인됨 · " + hh + ":" + mm + ":" + ss;
                    } else {
                        btn.textContent = "이미 확인됨";
                    }
                })
                .catch(function () {
                    btn.disabled = false;
                    if (msgEl) msgEl.textContent = "확인 실패 · 다시 시도";
                });
        });
    }

    function connectAlarmStream() {
        var es = new EventSource("/api/v1/alarm/notifications/stream");
        es.onmessage = function (e) {
            try {
                var data = JSON.parse(e.data);
                if (data.type === "alarm_notification") {
                    renderAlarmMessage(data);
                }
            } catch (_) {}
        };
        es.onerror = function () {
            es.close();
            setTimeout(connectAlarmStream, 5000);
        };
    }

    connectAlarmStream();

})();
