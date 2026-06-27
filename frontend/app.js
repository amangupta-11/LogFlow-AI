// Global state
let currentLogs = []; // Stores the currently visible logs for exporting

document.addEventListener("DOMContentLoaded", () => {
    // Initialize icons
    lucide.createIcons();

    // Tab Navigation Setup
    const navButtons = document.querySelectorAll(".nav-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");
    const pageTitle = document.getElementById("page-title");
    const pageDesc = document.getElementById("page-desc");

    const tabMeta = {
        "collect": {
            title: "Automatic Log Collector",
            desc: "Automate searching, scraping, and structuring platform logs from web forums."
        },
        "generate": {
            title: "Synthetic Log Generator",
            desc: "Create realistic simulated log events and mock errors for specific software versions."
        },
        "search": {
            title: "Semantic Vector Search",
            desc: "Leverage AI embeddings to query collected logs conceptually rather than by exact keywords."
        },
        "settings": {
            title: "API Settings",
            desc: "Configure your LLM provider credentials to unlock the full potential of parsing and generation."
        }
    };

    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.getAttribute("data-tab");
            
            // Toggle active states
            navButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(t => t.classList.remove("active"));
            
            btn.classList.add("active");
            document.getElementById(`tab-${targetTab}`).classList.add("active");
            
            // Update Header texts
            pageTitle.innerText = tabMeta[targetTab].title;
            pageDesc.innerText = tabMeta[targetTab].desc;
        });
    });

    // Check Settings Status on load
    checkSettings();

    // Set default start/end dates (last 7 days to now)
    const now = new Date();
    const tzOffset = now.getTimezoneOffset();
    
    const endLocal = new Date(now.getTime() - tzOffset * 60000);
    const endISOTime = endLocal.toISOString().slice(0, 16);
    
    const startLocal = new Date(now.getTime() - (7 * 24 * 60 * 60 * 1000) - tzOffset * 60000);
    const startISOTime = startLocal.toISOString().slice(0, 16);
    
    document.getElementById("collect-start-date").value = startISOTime;
    document.getElementById("collect-end-date").value = endISOTime;
    document.getElementById("gen-start-date").value = startISOTime;
    document.getElementById("gen-end-date").value = endISOTime;
    
    // Setup forms and click actions
    setupCollector();
    setupGenerator();
    setupSemanticSearch();
    setupSettings();
});

// Toast Notifications helper
function showToast(message, type = "info") {
    const toast = document.getElementById("toast-msg");
    const text = toast.querySelector(".toast-text");
    const icon = toast.querySelector(".toast-icon");

    text.innerText = message;
    toast.className = "toast show";

    if (type === "success") {
        toast.style.borderColor = "#10b981";
        icon.setAttribute("data-lucide", "check-circle");
        icon.style.color = "#10b981";
    } else if (type === "error") {
        toast.style.borderColor = "#ef4444";
        icon.setAttribute("data-lucide", "alert-triangle");
        icon.style.color = "#ef4444";
    } else {
        toast.style.borderColor = "#00f2fe";
        icon.setAttribute("data-lucide", "info");
        icon.style.color = "#00f2fe";
    }

    lucide.createIcons();

    setTimeout(() => {
        toast.classList.remove("show");
    }, 4000);
}

// Check if settings credentials are set
async function checkSettings() {
    try {
        const res = await fetch("/api/settings");
        if (res.ok) {
            const data = await res.json();
            const geminiStatus = document.getElementById("gemini-status");
            const openaiStatus = document.getElementById("openai-status");
            const anthropicStatus = document.getElementById("anthropic-status");

            if (data.gemini_key_configured) {
                geminiStatus.innerText = "Configured";
                geminiStatus.className = "key-status active";
            } else {
                geminiStatus.innerText = "Not Set";
                geminiStatus.className = "key-status";
            }

            if (data.openai_key_configured) {
                openaiStatus.innerText = "Configured";
                openaiStatus.className = "key-status active";
            } else {
                openaiStatus.innerText = "Not Set";
                openaiStatus.className = "key-status";
            }

            if (anthropicStatus) {
                if (data.anthropic_key_configured) {
                    anthropicStatus.innerText = "Configured";
                    anthropicStatus.className = "key-status active";
                } else {
                    anthropicStatus.innerText = "Not Set";
                    anthropicStatus.className = "key-status";
                }
            }
        }
    } catch (e) {
        console.error("Error connecting to backend settings API:", e);
    }
}

// TAB 1: Log Collector Manager
function setupCollector() {
    const form = document.getElementById("collect-form");
    const loader = document.getElementById("collect-loader");
    const emptyState = document.getElementById("collect-empty");
    const viewerWrapper = document.getElementById("collect-viewer-wrapper");
    const consoleLogs = document.getElementById("collect-console");
    const exportControls = document.getElementById("export-controls");

    // Toggle between Raw Console and Source Verification table
    const viewButtons = document.querySelectorAll(".console-tab-btn");
    const panelConsole = document.getElementById("panel-console");
    const panelSources = document.getElementById("panel-sources");

    console.log("setupCollector initialized. console-tab-btn count:", viewButtons.length);
    viewButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const view = btn.getAttribute("data-view");
            console.log("Console tab clicked:", view);
            viewButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            
            if (panelConsole && panelSources) {
                if (view === "console") {
                    panelConsole.style.display = "block";
                    panelSources.style.display = "none";
                } else {
                    panelConsole.style.display = "none";
                    panelSources.style.display = "block";
                }
            } else {
                console.error("panelConsole or panelSources element is missing in the DOM.", { panelConsole, panelSources });
            }
        });
    });

    // Mode Switching Logic
    const btnModeManual = document.getElementById("btn-mode-manual");
    const btnModeBatch = document.getElementById("btn-mode-batch");
    const batchContainer = document.getElementById("batch-container");

    if (btnModeManual && btnModeBatch && batchContainer) {
        btnModeManual.addEventListener("click", () => {
            btnModeManual.classList.add("active");
            btnModeBatch.classList.remove("active");
            form.style.display = "flex";
            batchContainer.style.display = "none";
        });

        btnModeBatch.addEventListener("click", () => {
            btnModeBatch.classList.add("active");
            btnModeManual.classList.remove("active");
            form.style.display = "none";
            batchContainer.style.display = "block";
        });
    }

    // Drag and Drop Zone
    const dropZone = document.getElementById("batch-drop-zone");
    const fileInput = document.getElementById("batch-file-input");
    const fileInfo = document.getElementById("batch-file-info");
    const fileNameText = document.getElementById("batch-file-name");
    const btnBatchCollect = document.getElementById("btn-batch-collect");
    const batchForm = document.getElementById("batch-form");

    let selectedFile = null;

    if (dropZone && fileInput) {
        // Drag over
        dropZone.addEventListener("dragover", (e) => {
            e.preventDefault();
            dropZone.classList.add("dragover");
        });

        // Drag leave
        dropZone.addEventListener("dragleave", () => {
            dropZone.classList.remove("dragover");
        });

        // Drop
        dropZone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropZone.classList.remove("dragover");
            if (e.dataTransfer.files.length > 0) {
                handleFileSelect(e.dataTransfer.files[0]);
            }
        });

        // Click to select
        dropZone.addEventListener("click", () => {
            fileInput.click();
        });

        fileInput.addEventListener("change", (e) => {
            if (e.target.files.length > 0) {
                handleFileSelect(e.target.files[0]);
            }
        });
    }

    function handleFileSelect(file) {
        const ext = file.name.split('.').pop().toLowerCase();
        if (ext !== 'csv' && ext !== 'xlsx') {
            showToast("Unsupported file type. Please select a .csv or .xlsx file.", "error");
            selectedFile = null;
            if (fileInfo) fileInfo.style.display = "none";
            if (btnBatchCollect) btnBatchCollect.disabled = true;
            return;
        }

        selectedFile = file;
        if (fileNameText) fileNameText.innerText = file.name;
        if (fileInfo) fileInfo.style.display = "flex";
        if (btnBatchCollect) btnBatchCollect.disabled = false;
        
        // Refresh icons inside fileInfo
        if (window.lucide) {
            window.lucide.createIcons();
        }
    }

    // Batch Form Submit & Polling
    let pollInterval = null;

    if (batchForm) {
        batchForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!selectedFile) return;

            // UI Reset
            if (btnBatchCollect) btnBatchCollect.disabled = true;
            const progressWrapper = document.getElementById("batch-progress-wrapper");
            const btnDownloadZip = document.getElementById("btn-download-zip");
            const statusText = document.getElementById("batch-status-text");
            const progressBar = document.getElementById("batch-progress-bar");
            
            if (progressWrapper) progressWrapper.style.display = "block";
            if (btnDownloadZip) btnDownloadZip.style.display = "none";
            if (statusText) statusText.innerText = "Uploading file and starting batch...";
            if (progressBar) progressBar.style.width = "0%";

            // Reset results view to empty
            emptyState.style.display = "flex";
            viewerWrapper.style.display = "none";
            exportControls.style.display = "none";

            const formData = new FormData();
            formData.append("file", selectedFile);

            try {
                const response = await fetch("/api/upload-batch", {
                    method: "POST",
                    body: formData
                });

                if (!response.ok) {
                    const errData = await response.json();
                    throw new Error(errData.detail || "Upload failed");
                }

                const data = await response.json();
                const jobId = data.job_id;
                showToast(`Batch Job ${jobId} initialized successfully!`, "success");

                // Start polling
                if (pollInterval) clearInterval(pollInterval);
                pollInterval = setInterval(() => pollBatchStatus(jobId), 1500);

            } catch (err) {
                showToast(`Failed to start batch: ${err.message}`, "error");
                if (statusText) statusText.innerText = `Error: ${err.message}`;
                if (btnBatchCollect) btnBatchCollect.disabled = false;
            }
        });
    }

    async function pollBatchStatus(jobId) {
        const progressWrapper = document.getElementById("batch-progress-wrapper");
        const btnDownloadZip = document.getElementById("btn-download-zip");
        const statusText = document.getElementById("batch-status-text");
        const progressBar = document.getElementById("batch-progress-bar");
        const statTotal = document.getElementById("stat-total");
        const statCompleted = document.getElementById("stat-completed");
        const statFailed = document.getElementById("stat-failed");
        const statSkipped = document.getElementById("stat-skipped");
        const statRemaining = document.getElementById("stat-remaining");

        try {
            const response = await fetch(`/api/batch-status/${jobId}`);
            if (!response.ok) throw new Error("Status check failed");

            const data = await response.json();
            const job = data.job;

            // Update stats
            if (statTotal) statTotal.innerText = job.total_rows;
            if (statCompleted) statCompleted.innerText = job.completed_rows;
            if (statFailed) statFailed.innerText = job.failed_rows;
            if (statSkipped) statSkipped.innerText = job.skipped_rows;
            if (statRemaining) statRemaining.innerText = job.remaining_rows;

            // Calculate progress percent
            const processedCount = job.completed_rows + job.failed_rows + job.skipped_rows;
            const pct = job.total_rows > 0 ? Math.round((processedCount / job.total_rows) * 100) : 0;
            if (progressBar) progressBar.style.width = `${pct}%`;

            // Find current active task text
            let currentTask = "Processing batch...";
            const activeRow = data.rows.find(r => r.status === "processing");
            if (activeRow) {
                currentTask = `Processing: ${activeRow.platform} (${activeRow.version || 'any'})`;
            } else if (job.status === "completed") {
                currentTask = "Completed! Download your ZIP archive below.";
            } else if (job.status === "failed") {
                currentTask = "Job failed.";
            } else if (job.status === "pending") {
                currentTask = "Queued for processing...";
            }

            if (statusText) statusText.innerText = currentTask;

            if (job.status === "completed") {
                clearInterval(pollInterval);
                pollInterval = null;
                if (btnDownloadZip) {
                    btnDownloadZip.href = `/api/download-zip/${jobId}`;
                    btnDownloadZip.style.display = "block";
                }
                if (btnBatchCollect) btnBatchCollect.disabled = false;
                showToast("Batch job finished!", "success");
            } else if (job.status === "failed") {
                clearInterval(pollInterval);
                pollInterval = null;
                if (btnBatchCollect) btnBatchCollect.disabled = false;
                showToast("Batch job failed", "error");
            }

        } catch (err) {
            console.error("Polling error:", err);
        }
    }

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const platform = document.getElementById("collect-platform").value;
        const version = document.getElementById("collect-version").value;
        const service = document.getElementById("collect-service").value;
        const count = parseInt(document.getElementById("collect-count").value);
        const startDate = document.getElementById("collect-start-date").value;
        const endDate = document.getElementById("collect-end-date").value;

        // UI state: Loading & Reset sub-tab views
        emptyState.style.display = "none";
        viewerWrapper.style.display = "none";
        exportControls.style.display = "none";
        loader.style.display = "flex";
        
        if (panelConsole && panelSources) {
            viewButtons.forEach(b => b.classList.remove("active"));
            const consoleBtn = document.querySelector('.console-tab-btn[data-view="console"]');
            if (consoleBtn) consoleBtn.classList.add("active");
            panelConsole.style.display = "block";
            panelSources.style.display = "none";
        }
        
        // Progress status steps simulation for realistic feel
        const loaderMsg = loader.querySelector(".loader-message");
        const loaderSub = loader.querySelector(".loader-sub");
        
        loaderMsg.innerText = "Searching DDG & Bing sources...";
        loaderSub.innerText = "Finding forum threads, StackOverflow, and GitHub posts...";

        const statusIntervals = [
            setTimeout(() => {
                loaderMsg.innerText = "Downloading source documents...";
                loaderSub.innerText = "Cleaning HTML pages and extracting logs sections...";
            }, 2500),
            setTimeout(() => {
                loaderMsg.innerText = "Running AI NLP extraction pipeline...";
                loaderSub.innerText = "Applying structuring, tagging, and severity classifications...";
            }, 5500)
        ];

        try {
            const response = await fetch("/api/collect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ platform, version, service, count, start_date: startDate, end_date: endDate })
            });

            // Clear timers
            statusIntervals.forEach(t => clearTimeout(t));

            if (response.ok) {
                const data = await response.json();
                currentLogs = data.logs;
                
                if (currentLogs && currentLogs.length > 0) {
                    renderLogs(consoleLogs, currentLogs);
                    renderSourceVerification(currentLogs);
                    loader.style.display = "none";
                    viewerWrapper.style.display = "flex";
                    exportControls.style.display = "flex";
                    
                    const sourceText = data.source === "scraped" ? "scraped from the web" : "synthetically generated as fallback";
                    showToast(`Successfully loaded ${currentLogs.length} logs (${sourceText})`, "success");
                } else {
                    loader.style.display = "none";
                    emptyState.style.display = "flex";
                    showToast("No logs found for the given criteria", "error");
                }
            } else {
                throw new Error("HTTP connection error");
            }
        } catch (err) {
            statusIntervals.forEach(t => clearTimeout(t));
            loader.style.display = "none";
            emptyState.style.display = "flex";
            showToast(`Collection failed: ${err.message}`, "error");
        }
    });

    // Severity Filters configuration
    setupLogFilters(viewerWrapper, consoleLogs);
}

// TAB 2: Log Generator Manager
function setupGenerator() {
    const form = document.getElementById("generate-form");
    const loader = document.getElementById("gen-loader");
    const emptyState = document.getElementById("gen-empty");
    const viewerWrapper = document.getElementById("gen-viewer-wrapper");
    const consoleLogs = document.getElementById("gen-console");
    const exportControls = document.getElementById("gen-export-controls");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const platform = document.getElementById("gen-platform").value;
        const version = document.getElementById("gen-version").value;
        const service = document.getElementById("gen-service").value;
        const severity = document.getElementById("gen-severity").value;
        const count = parseInt(document.getElementById("gen-count").value);
        const scenario = document.getElementById("gen-scenario").value;
        const startDate = document.getElementById("gen-start-date").value;
        const endDate = document.getElementById("gen-end-date").value;

        // UI state: Loading
        emptyState.style.display = "none";
        viewerWrapper.style.display = "none";
        exportControls.style.display = "none";
        loader.style.display = "flex";

        try {
            const response = await fetch("/api/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ platform, version, service, severity, count, scenario, start_date: startDate, end_date: endDate })
            });

            if (response.ok) {
                const data = await response.json();
                currentLogs = data.logs;

                if (currentLogs && currentLogs.length > 0) {
                    renderLogs(consoleLogs, currentLogs);
                    loader.style.display = "none";
                    viewerWrapper.style.display = "flex";
                    exportControls.style.display = "flex";
                    showToast(`Generated ${currentLogs.length} synthetic logs`, "success");
                } else {
                    loader.style.display = "none";
                    emptyState.style.display = "flex";
                    showToast("Failed to generate logs", "error");
                }
            } else {
                throw new Error("HTTP error");
            }
        } catch (err) {
            loader.style.display = "none";
            emptyState.style.display = "flex";
            showToast(`Generation failed: ${err.message}`, "error");
        }
    });

    // Severity Filters configuration
    setupLogFilters(viewerWrapper, consoleLogs);
}

// Log line rendering logic
function renderLogs(container, logs) {
    container.innerHTML = "";
    if (!logs) return;
    try {
        logs.forEach(log => {
            const severity = (log.severity || "INFO").toUpperCase();
            const line = document.createElement("div");
            line.className = `log-line ${severity}`;
            
            const textSpan = document.createElement("span");
            let logText = log.original_log || "";
            
            // Check if logText already starts with a timestamp or date pattern
            const hasTimestamp = /^(?:\[?\d{4}[-/]\d{2}[-/]\d{2}|\[?[A-Za-z]{3}\s+\d{1,2}|\b\d{6}\b)/.test(logText.trim());
            if (!hasTimestamp && log.timestamp) {
                // Standardize timestamp display to match console log feel
                let displayTs = log.timestamp;
                if (displayTs.includes("T")) {
                    displayTs = displayTs.replace("T", " ").replace("Z", "");
                }
                logText = `${displayTs} ${logText}`;
            }
            
            textSpan.innerText = logText + " ";
            line.appendChild(textSpan);
            
            if (log.source_url && typeof log.source_url === "string" && log.source_url.startsWith("http")) {
                const link = document.createElement("a");
                link.href = log.source_url;
                link.target = "_blank";
                link.className = "log-source-link";
                link.title = `Source: ${log.source_url}`;
                link.innerHTML = `<i data-lucide="external-link" class="inline-icon"></i>`;
                line.appendChild(link);
            } else if (log.source_url === "synthetic") {
                const span = document.createElement("span");
                span.className = "log-source-synthetic";
                span.innerText = " [synthetic]";
                line.appendChild(span);
            }
            
            container.appendChild(line);
        });
    } catch (e) {
        console.error("Error in renderLogs:", e);
    }
    if (window.lucide) {
        try {
            window.lucide.createIcons();
        } catch (e) {
            console.error("Error creating icons in renderLogs:", e);
        }
    }
}

// Source Verification table rendering logic
function renderSourceVerification(logs) {
    const validatedBody = document.getElementById("verify-sources-validated-body");
    const nonValidatedBody = document.getElementById("verify-sources-non-validated-body");
    
    if (!validatedBody || !nonValidatedBody) return;
    
    validatedBody.innerHTML = "";
    nonValidatedBody.innerHTML = "";
    
    if (!logs || logs.length === 0) return;
    
    let validatedCount = 0;
    let nonValidatedCount = 0;
    
    try {
        const discoveredEl = document.getElementById("stats-discovered");
        const validatedEl = document.getElementById("stats-validated");
        const nonValidatedEl = document.getElementById("stats-non-validated");
        
        if (discoveredEl) discoveredEl.innerText = logs.length;
        
        logs.forEach((log) => {
            const isValid = log.validation && log.validation.valid === true;
            
            let sourceHTML = `<span class="log-source-synthetic" style="margin-left:0">Synthetic Fallback</span>`;
            if (log.source_url && typeof log.source_url === "string" && log.source_url.startsWith("http")) {
                let displayUrl = log.source_url;
                if (displayUrl.length > 55) {
                    displayUrl = displayUrl.substring(0, 52) + "...";
                }
                sourceHTML = `<a href="${log.source_url}" target="_blank" class="log-source-link" style="margin-left:0; opacity:0.9" title="Verify source: ${log.source_url}"><i data-lucide="external-link" class="inline-icon" style="margin-right:6px"></i> ${displayUrl}</a>`;
            }
            
            const severity = (log.severity || "INFO").toLowerCase();
            const severityDisplay = (log.severity || "INFO").toUpperCase();
            
            if (isValid) {
                validatedCount++;
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td style="font-family:'JetBrains Mono', monospace; font-size:0.85rem; opacity:0.7">${validatedCount}</td>
                    <td><span class="badge ${severity}">${severityDisplay}</span></td>
                    <td style="font-family:'JetBrains Mono', monospace; font-size:0.8rem">${log.message || ""}</td>
                    <td>${sourceHTML}</td>
                `;
                validatedBody.appendChild(tr);
            } else {
                nonValidatedCount++;
                const tr = document.createElement("tr");
                
                // Fetch short reason
                let reason = "No reason provided";
                if (log.validation && log.validation.reason) {
                    reason = log.validation.reason;
                    // Simplify/shorten prefix for clean UI
                    reason = reason.replace("Claude Verification (OpenRouter): ", "")
                                   .replace("Claude Verification: ", "")
                                   .replace("OpenAI Fallback Verification (OpenRouter): ", "")
                                   .replace("OpenAI Fallback Verification: ", "")
                                   .replace("OpenAI Validation: ", "")
                                   .replace("Local Verification Fallback: ", "")
                                   .replace("Local Verification Fallback ", "");
                }
                
                tr.innerHTML = `
                    <td style="font-family:'JetBrains Mono', monospace; font-size:0.85rem; opacity:0.7">${nonValidatedCount}</td>
                    <td><span class="badge ${severity}">${severityDisplay}</span></td>
                    <td style="font-family:'JetBrains Mono', monospace; font-size:0.8rem">${log.message || ""}</td>
                    <td>${sourceHTML}</td>
                    <td style="font-size:0.8rem; color:#ef4444; font-weight:500">${reason}</td>
                `;
                nonValidatedBody.appendChild(tr);
            }
        });
        if (validatedEl) validatedEl.innerText = validatedCount;
        if (nonValidatedEl) nonValidatedEl.innerText = nonValidatedCount;

        // If one of the bodies is empty, insert placeholder row
        if (validatedCount === 0) {
            validatedBody.innerHTML = `<tr><td colspan="4" style="text-align:center; opacity:0.5; padding: 15px;">No validated logs found.</td></tr>`;
        }
        if (nonValidatedCount === 0) {
            nonValidatedBody.innerHTML = `<tr><td colspan="5" style="text-align:center; opacity:0.5; padding: 15px; color:#10b981;">All logs successfully validated!</td></tr>`;
        }
        
    } catch (e) {
        console.error("Error in renderSourceVerification:", e);
    }
    
    if (window.lucide) {
        try {
            window.lucide.createIcons();
        } catch (e) {
            console.error("Error creating icons in renderSourceVerification:", e);
        }
    }
}

// Log filtering logic
function setupLogFilters(wrapper, consoleLogs) {
    const filterBadges = wrapper.querySelectorAll(".filter-badge");
    
    filterBadges.forEach(badge => {
        badge.addEventListener("click", () => {
            filterBadges.forEach(b => b.classList.remove("active"));
            badge.classList.add("active");
            
            const filterVal = badge.getAttribute("data-filter").toUpperCase();
            
            const lines = consoleLogs.querySelectorAll(".log-line");
            lines.forEach(line => {
                if (filterVal === "ALL") {
                    line.style.display = "block";
                } else if (line.classList.contains(filterVal)) {
                    line.style.display = "block";
                } else {
                    line.style.display = "none";
                }
            });
        });
    });
}

// TAB 3: Semantic Search Manager
function setupSemanticSearch() {
    const input = document.getElementById("search-query");
    const btn = document.getElementById("btn-search");
    const loader = document.getElementById("search-loader");
    const emptyState = document.getElementById("search-empty");
    const table = document.getElementById("search-results-table");
    const tbody = document.getElementById("search-results-body");

    const runSearch = async () => {
        const query = input.value.trim();
        if (!query) return;

        emptyState.style.display = "none";
        table.style.display = "none";
        loader.style.display = "flex";

        try {
            const res = await fetch(`/api/search?query=${encodeURIComponent(query)}&limit=15`);
            if (res.ok) {
                const data = await res.json();
                const results = data.results;

                tbody.innerHTML = "";
                if (results && results.length > 0) {
                    results.forEach(item => {
                        const tr = document.createElement("tr");
                        
                        const scorePct = Math.round(item.score * 100);
                        const displayScore = item.score !== undefined ? `${scorePct}%` : "N/A";
                        
                        let sourceHTML = `<span class="log-source-synthetic" style="margin-left:0">Synthetic</span>`;
                        if (item.source_url && item.source_url.startsWith("http")) {
                            sourceHTML = `<a href="${item.source_url}" target="_blank" class="log-source-link" style="margin-left:0; opacity:0.85" title="Source: ${item.source_url}"><i data-lucide="external-link" class="inline-icon" style="margin-right:4px"></i> Link</a>`;
                        }
                        
                        tr.innerHTML = `
                            <td><strong>${item.platform}</strong> ${item.version ? `<span style="font-size:0.8rem; opacity:0.7">v${item.version}</span>` : ""}</td>
                            <td style="font-family:'JetBrains Mono', monospace; font-size:0.8rem">${item.timestamp}</td>
                            <td><span class="badge ${item.severity.toLowerCase()}">${item.severity}</span></td>
                            <td style="font-family:'JetBrains Mono', monospace; font-size:0.85rem">${item.message}</td>
                            <td>${sourceHTML}</td>
                            <td><span class="score-text">${displayScore}</span></td>
                        `;
                        tbody.appendChild(tr);
                    });
                    loader.style.display = "none";
                    table.style.display = "table";
                    if (window.lucide) {
                        window.lucide.createIcons();
                    }
                } else {
                    loader.style.display = "none";
                    emptyState.style.display = "flex";
                    emptyState.querySelector("h3").innerText = "No matches found";
                    emptyState.querySelector("p").innerText = "Try searching for a different concept or check if you have indexed any logs.";
                }
            } else {
                throw new Error("HTTP Connection Error");
            }
        } catch (err) {
            loader.style.display = "none";
            emptyState.style.display = "flex";
            showToast(`Search failed: ${err.message}`, "error");
        }
    };

    btn.addEventListener("click", runSearch);
    input.addEventListener("keypress", (e) => {
        if (e.key === "Enter") runSearch();
    });
}

// TAB 4: API Settings Manager
function setupSettings() {
    const form = document.getElementById("settings-form");
    const geminiInput = document.getElementById("setting-gemini");
    const openaiInput = document.getElementById("setting-openai");
    const anthropicInput = document.getElementById("setting-anthropic");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const gemini_key = geminiInput.value.trim() || null;
        const openai_key = openaiInput.value.trim() || null;
        const anthropic_key = anthropicInput && anthropicInput.value.trim() ? anthropicInput.value.trim() : null;

        if (!gemini_key && !openai_key && !anthropic_key) {
            showToast("Please enter at least one API key", "error");
            return;
        }

        try {
            const res = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ gemini_key, openai_key, anthropic_key })
            });

            if (res.ok) {
                showToast("API Credentials saved successfully!", "success");
                geminiInput.value = "";
                openaiInput.value = "";
                if (anthropicInput) anthropicInput.value = "";
                await checkSettings();
            } else {
                throw new Error("Failed to save keys");
            }
        } catch (err) {
            showToast(`Saving settings failed: ${err.message}`, "error");
        }
    });
}

// Browser-based client-side log exports (TXT, JSON, CSV)
function exportLogs(format) {
    if (!currentLogs || currentLogs.length === 0) {
        showToast("No logs available to export", "error");
        return;
    }

    let fileContent = "";
    let mimeType = "text/plain";
    let filename = `pulselog-export.${format}`;

    // Get active filter if any
    const activePane = document.querySelector(".tab-pane.active");
    const filterBadge = activePane.querySelector(".filter-badge.active");
    const filterVal = filterBadge ? filterBadge.getAttribute("data-filter").toUpperCase() : "ALL";
    
    // Filter the logs in memory to match visual display
    const filteredLogs = currentLogs.filter(log => {
        return filterVal === "ALL" || log.severity.toUpperCase() === filterVal;
    });

    if (filteredLogs.length === 0) {
        showToast("No logs matching current filter", "error");
        return;
    }

    if (format === "txt") {
        fileContent = filteredLogs.map(log => log.original_log).join("\n");
        mimeType = "text/plain";
    } else if (format === "json") {
        fileContent = JSON.stringify(filteredLogs, null, 2);
        mimeType = "application/json";
    } else if (format === "csv") {
        // Headers
        fileContent = "timestamp,severity,message,original_log\n";
        filteredLogs.forEach(log => {
            // Escape double quotes inside values
            const cleanMsg = log.message.replace(/"/g, '""');
            const cleanOrig = log.original_log.replace(/"/g, '""');
            fileContent += `"${log.timestamp}","${log.severity}","${cleanMsg}","${cleanOrig}"\n`;
        });
        mimeType = "text/csv";
    }

    // Trigger download
    const blob = new Blob([fileContent], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    
    // Cleanup
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    
    showToast(`Successfully downloaded ${filteredLogs.length} logs in ${format.toUpperCase()} format`, "success");
}
