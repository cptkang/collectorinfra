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

    // ─── State ───

    var selectedFile = null;
    var isProcessing = false;
    var messages = []; // session message history
    var stageTimer = null;

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
    var nodeLabels = {
        input_parser: "입력 분석",
        semantic_router: "DB 라우팅",
        schema_analyzer: "스키마 탐색",
        query_generator: "SQL 생성",
        query_validator: "SQL 검증",
        query_executor: "쿼리 실행",
        result_organizer: "결과 정리",
        output_generator: "응답 생성",
        multi_db_executor: "멀티 DB 실행",
        result_merger: "결과 병합",
        error_response: "에러 처리",
    };

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
        });
    });

    // Panel toggle
    panelToggle.addEventListener("click", function () {
        document.querySelector(".chat-layout").classList.toggle("panel-collapsed");
    });

    // ─── Auto-resize Textarea ───

    function autoResizeTextarea() {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 160) + "px";
    }

    // ─── Keyboard Handling ───

    function handleKeydown(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            handleSend();
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
        scrollToBottom();
        startStageAnimation();
    }

    function removeProcessingMessage() {
        stopStageAnimation();
        var el = document.getElementById("processingMessage");
        if (el) el.remove();
    }

    // ─── Stage Animation ───

    function startStageAnimation() {
        var container = document.getElementById("processingStages");
        if (!container) return;

        var stageEls = container.querySelectorAll(".stage");
        var textEl = document.getElementById("processingText");
        var idx = 0;

        function advance() {
            if (idx > 0 && idx <= stageEls.length) {
                stageEls[idx - 1].classList.remove("active");
                stageEls[idx - 1].classList.add("done");
            }
            if (idx < stageEls.length) {
                stageEls[idx].classList.add("active");
                if (textEl) textEl.textContent = stageMessages[stages[idx]];
                idx++;
                stageTimer = setTimeout(advance, 2000 + Math.random() * 2000);
            }
        }
        advance();
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
                '<a class="message-download" href="/api/v1/query/' + data.query_id + '/download">' +
                    '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                    escapeHtml(data.file_name || "파일") + ' 다운로드' +
                '</a>';
        }

        el.innerHTML =
            avatarHtml +
            '<div class="message-content">' +
                '<div class="message-bubble">' +
                    '<div class="response-text">' + escapeHtml(responseText) + '</div>' +
                    metaHtml +
                    sqlHtml +
                    downloadHtml +
                '</div>' +
                '<div class="message-time">' + formatTime(new Date()) + '</div>' +
            '</div>';

        chatMessages.appendChild(el);
        scrollToBottom();
    }

    // ─── Create Streaming Agent Message ───

    function createStreamingMessage() {
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
        scrollToBottom();
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
            var response = await fetch("/api/v1/query/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: query }),
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
                                var textEl = document.getElementById("streamingText");
                                if (textEl) textEl.textContent = accumulatedText;
                                scrollToBottom();
                            } else if (event.type === "node_start") {
                                handleNodeStart(event);
                            } else if (event.type === "node_complete") {
                                handleNodeComplete(event);
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

            // Finalize streaming message
            finalizeStreamingMessage(accumulatedText, metaData);
            messages.push({
                role: "agent",
                data: {
                    response: accumulatedText,
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

        // Remove streaming IDs to prevent conflicts
        var streamingMsg = document.getElementById("streamingMessage");
        if (streamingMsg) streamingMsg.removeAttribute("id");
        ["streamingText", "streamingCursor", "streamingTime", "streamingMeta", "streamingSql"].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.removeAttribute("id");
        });

        scrollToBottom();
    }

    // ─── Fallback (non-streaming) Query ───

    async function executeFallbackQuery(query) {
        renderProcessingMessage();
        resetProgressPanel();

        try {
            var response = await fetch("/api/v1/query", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: query }),
            });

            var data = await response.json();

            removeProcessingMessage();

            if (!response.ok) {
                showError(data.detail || "처리 중 오류가 발생했습니다.");
                return;
            }

            renderAgentMessage(data);
            messages.push({ role: "agent", data: data, time: new Date() });

        } catch (err) {
            removeProcessingMessage();
            showError("서버와의 통신에 실패했습니다: " + err.message);
        }
    }

    // ─── File Query (no SSE needed) ───

    async function executeFileQuery(query, file) {
        isProcessing = true;
        sendBtn.disabled = true;

        renderProcessingMessage();
        resetProgressPanel();

        try {
            var formData = new FormData();
            formData.append("query", query);
            formData.append("file", file);

            var response = await fetch("/api/v1/query/file", {
                method: "POST",
                body: formData,
            });

            var data = await response.json();

            removeProcessingMessage();

            if (!response.ok) {
                showError(data.detail || "처리 중 오류가 발생했습니다.");
                return;
            }

            renderAgentMessage(data);
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

    function scrollToBottom() {
        requestAnimationFrame(function () {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        });
    }

    // ─── Global function for SQL toggle ───

    window.toggleSql = function (id, btn) {
        var codeEl = document.getElementById(id);
        if (!codeEl) return;
        btn.classList.toggle("open");
        codeEl.classList.toggle("open");
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

    function handleNodeStart(event) {
        var node = event.node;
        var label = nodeLabels[node] || node;

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

        stepEl.innerHTML =
            '<div class="pipeline-step-header" onclick="togglePipelineStep(this)">' +
                '<span class="pipeline-step-dot"></span>' +
                '<span class="pipeline-step-name">' + escapeHtml(label) + '</span>' +
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

        else if (node === "schema_analyzer") {
            if (data.relevant_tables && data.relevant_tables.length > 0) {
                var listHtml = '<ul class="step-data-list">';
                data.relevant_tables.forEach(function (t) {
                    listHtml += "<li>" + escapeHtml(t) + "</li>";
                });
                listHtml += "</ul>";
                html += renderSection("관련 테이블", listHtml);
            }
            if (data.schema_summary) {
                var schemaHtml = "";
                for (var tbl in data.schema_summary) {
                    var cols = data.schema_summary[tbl];
                    schemaHtml += '<div style="margin-bottom:6px"><strong style="color:var(--accent-dim);font-size:0.6875rem">' + escapeHtml(tbl) + '</strong>';
                    if (Array.isArray(cols)) {
                        schemaHtml += '<div class="step-data-value">' + cols.map(escapeHtml).join(", ") + "</div>";
                    } else {
                        schemaHtml += '<div class="step-data-value">' + escapeHtml(String(cols)) + "</div>";
                    }
                    schemaHtml += "</div>";
                }
                html += renderSection("스키마 요약", schemaHtml);
            }
        }

        else if (node === "query_generator") {
            if (data.generated_sql) {
                html += renderSection("생성된 SQL", '<pre class="step-data-code">' + escapeHtml(data.generated_sql) + "</pre>");
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

        else if (node === "output_generator") {
            html += renderSection("상태", '<span class="step-data-badge step-data-badge--success">' + escapeHtml(data.status || "완료") + "</span>");
        }

        else if (node === "error_response") {
            html += renderSection("에러", '<div class="step-data-value" style="color:var(--error)">' + escapeHtml(data.error || "") + "</div>");
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
                html += "<td title='" + escapeHtml(String(val)) + "'>" + escapeHtml(String(val)) + "</td>";
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

})();
