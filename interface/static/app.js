// Genesis Communication Interface — Frontend Controller (Vanilla JS)
document.addEventListener("DOMContentLoaded", () => {
  // State
  let currentTab = "individual"; // "individual" | "triad"
  let isLiveMode = false;
  let activePendingGate = null;
  let journalActiveStartTime = null;
  let journalTimerInterval = null;

  // DOM Elements
  const tabButtons = document.querySelectorAll(".nav-tab");
  const agentRadiosSection = document.getElementById("individualAgentSelectSection");
  const messageInput = document.getElementById("messageInput");
  const btnSend = document.getElementById("btnSend");
  const messagesContainer = document.getElementById("messagesContainer");
  const emptyState = document.getElementById("emptyState");

  // Approval Gate Elements
  const approvalCard = document.getElementById("approvalCard");
  const approvalActionName = document.getElementById("approvalActionName");
  const approvalRecipient = document.getElementById("approvalRecipient");
  const approvalPayloadEdit = document.getElementById("approvalPayloadEdit");
  const btnApproveGate = document.getElementById("btnApproveGate");
  const btnRejectGate = document.getElementById("btnRejectGate");

  // Kill Switch Elements
  const btnKillSwitch = document.getElementById("btnKillSwitch");
  const killSwitchBanner = document.getElementById("killSwitchBanner");
  const btnResetKillSwitch = document.getElementById("btnResetKillSwitch");

  // Journal Widget Elements
  const journalStatusText = document.getElementById("journalStatusText");
  const journalPulse = document.getElementById("journalPulse");
  const journalTimer = document.getElementById("journalTimer");
  const btnJournalBreak = document.getElementById("btnJournalBreak");
  const btnJournalResume = document.getElementById("btnJournalResume");
  const btnJournalStop = document.getElementById("btnJournalStop");
  const btnJournalReportModal = document.getElementById("btnJournalReportModal");
  const journalModal = document.getElementById("journalModal");
  const btnCloseJournalModal = document.getElementById("btnCloseJournalModal");
  const journalModalBody = document.getElementById("journalModalBody");

  // Governance Elements
  const liveModeToggle = document.getElementById("liveModeToggle");
  const modeBadge = document.getElementById("modeBadge");
  const modeText = document.getElementById("modeText");
  const cumulativeCostVal = document.getElementById("cumulativeCostVal");

  // Track rendered event IDs to ensure idempotent rendering across polls
  const renderedEventIds = new Set();

  // Helper: Format seconds as Xh Ym Zs
  function formatSeconds(sec) {
    if (isNaN(sec) || sec < 0) sec = 0;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
    if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
    return `${s}s`;
  }

  // ---------------------------------------------------------------------------
  // 1. Navigation & Tab Switching
  // ---------------------------------------------------------------------------
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      tabButtons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.dataset.tab;

      if (currentTab === "individual") {
        agentRadiosSection.style.display = "block";
        messageInput.placeholder = "Message Genesis, Antigravity, or OpenAI-Agent... (Press Cmd+Enter)";
        btnSend.querySelector("span:first-child").textContent = "Send";
      } else {
        agentRadiosSection.style.display = "none";
        messageInput.placeholder = "Enter initial probe to kick off the Supervised Triad round... (Press Cmd+Enter)";
        btnSend.querySelector("span:first-child").textContent = "Start Triad Round";
        checkTriadStatus();
      }
    });
  });

  // ---------------------------------------------------------------------------
  // 2. Execution Mode (Offline vs Live)
  // ---------------------------------------------------------------------------
  function setOfflineBadge() {
    modeBadge.querySelector(".dot").className = "dot offline";
    modeBadge.style.background = "rgba(16, 185, 129, 0.1)";
    modeBadge.style.borderColor = "rgba(16, 185, 129, 0.3)";
    modeBadge.style.color = "#34d399";
    modeText.textContent = "Offline Mode (Zero Cost)";
  }

  function setLiveBadge() {
    modeBadge.querySelector(".dot").className = "dot live";
    modeBadge.style.background = "rgba(239, 68, 68, 0.1)";
    modeBadge.style.borderColor = "rgba(239, 68, 68, 0.3)";
    modeBadge.style.color = "#f87171";
    modeText.textContent = "🔴 LIVE API MODE";
  }

  liveModeToggle.addEventListener("change", (e) => {
    isLiveMode = e.target.checked;
    if (isLiveMode) {
      const confirmLive = confirm(
        "⚠️ ENABLE LIVE EXECUTION?\n\n" +
        "Live mode invokes actual model APIs and headless CLI subprocesses.\n" +
        "All approval gates and spending ceilings will remain active.\n\n" +
        "Proceed with live calls?"
      );
      if (!confirmLive) {
        liveModeToggle.checked = false;
        isLiveMode = false;
        setOfflineBadge();
        return;
      }
      setLiveBadge();
    } else {
      setOfflineBadge();
    }
  });

  // ---------------------------------------------------------------------------
  // 3. Message Rendering
  // ---------------------------------------------------------------------------
  function appendMessage(sender, content, meta = {}, eventId = null) {
    if (eventId) {
      if (renderedEventIds.has(eventId)) {
        return; // Idempotent: event already rendered
      }
      if (document.querySelector(`[data-event-id="${eventId}"]`)) {
        renderedEventIds.add(eventId);
        return; // Already present in DOM
      }
      renderedEventIds.add(eventId);
    }
    if (emptyState) emptyState.style.display = "none";

    const card = document.createElement("div");
    card.className = `msg-card ${sender === "Fodoh" ? "user-msg" : "agent-msg"}`;
    if (eventId) {
      card.setAttribute("data-event-id", eventId);
    }

    const header = document.createElement("div");
    header.className = "msg-header";

    const author = document.createElement("div");
    author.className = "msg-author";

    let badgeClass = "user";
    let icon = "👤";
    if (sender === "Genesis") { badgeClass = "genesis"; icon = "🟣"; }
    else if (sender === "Antigravity") { badgeClass = "antigravity"; icon = "🔵"; }
    else if (sender === "OpenAI-Agent") { badgeClass = "openai"; icon = "🟢"; }

    author.innerHTML = `<span class="badge-speaker ${badgeClass}">${icon} ${sender}</span>`;

    const metaSpan = document.createElement("div");
    metaSpan.className = "msg-meta";

    const parts = [];
    if (meta.model) parts.push(meta.model);
    if (meta.latency_sec) parts.push(`${meta.latency_sec}s`);
    if (meta.cost_usd_est !== undefined && meta.cost_usd_est > 0) parts.push(`$${meta.cost_usd_est.toFixed(6)}`);
    metaSpan.textContent = parts.join(" • ");

    header.appendChild(author);
    header.appendChild(metaSpan);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = content; // Safely escape HTML

    card.appendChild(header);
    card.appendChild(body);
    messagesContainer.appendChild(card);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // 4. Send Message (Individual & Triad Initiate)
  // ---------------------------------------------------------------------------
  async function handleSend() {
    const text = messageInput.value.trim();
    if (!text) return;

    if (currentTab === "individual") {
      const selectedAgent = document.querySelector('input[name="agentSelect"]:checked').value;
      appendMessage("Fodoh", text);
      messageInput.value = "";
      btnSend.disabled = true;

      try {
        const res = await fetch("/api/chat/individual", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            recipient: selectedAgent,
            message: text,
            is_live: isLiveMode,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          alert(`Error: ${data.detail || "Agent communication failed"}`);
          return;
        }
        appendMessage(data.speaker, data.content, {
          model: data.model,
          latency_sec: data.latency_sec,
          cost_usd_est: data.cost_usd_est,
        });
        updateSystemStatus();
      } catch (err) {
        alert(`Network Error: ${err.message}`);
      } finally {
        btnSend.disabled = false;
      }
    } else {
      // Triad Initiate
      appendMessage("Fodoh", `Initiating Supervised Triad Round:\n${text}`);
      messageInput.value = "";
      btnSend.disabled = true;

      try {
        const res = await fetch("/api/chat/triad/initiate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            probe_prompt: text,
            is_live: isLiveMode,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          alert(`Error: ${data.detail || "Failed to initiate triad"}`);
          return;
        }
        renderTriadState(data);
      } catch (err) {
        alert(`Network Error: ${err.message}`);
      } finally {
        btnSend.disabled = false;
      }
    }
  }

  btnSend.addEventListener("click", handleSend);
  messageInput.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  });

  // ---------------------------------------------------------------------------
  // 5. Approval Gate Handler
  // ---------------------------------------------------------------------------
  function renderApprovalGate(gate) {
    if (!gate || gate.status !== "PENDING") {
      approvalCard.style.display = "none";
      activePendingGate = null;
      return;
    }
    activePendingGate = gate;
    approvalActionName.textContent = gate.action_name;
    approvalRecipient.textContent = `[${gate.recipient}]`;
    approvalPayloadEdit.value = gate.proposed_payload;
    approvalCard.style.display = "block";
    approvalCard.scrollIntoView({ behavior: "smooth" });
  }

  async function respondApprovalGate(action) {
    if (!activePendingGate) return;
    const payload = approvalPayloadEdit.value;
    btnApproveGate.disabled = true;
    btnRejectGate.disabled = true;

    try {
      const res = await fetch("/api/chat/triad/respond-gate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approval_id: activePendingGate.approval_id,
          action: action,
          edited_payload: payload,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(`Approval Error: ${data.detail || "Failed to process approval"}`);
        return;
      }
      renderTriadState(data);
    } catch (err) {
      alert(`Network Error: ${err.message}`);
    } finally {
      btnApproveGate.disabled = false;
      btnRejectGate.disabled = false;
    }
  }

  btnApproveGate.addEventListener("click", () => respondApprovalGate("approve"));
  btnRejectGate.addEventListener("click", () => respondApprovalGate("reject"));

  function renderTriadState(state) {
    if (!state) return;

    // 1. Render turns idempotently using stable event IDs
    if (state.turns && state.turns.length > 0) {
      state.turns.forEach((turn, idx) => {
        const turnId = turn.turn_id !== undefined ? turn.turn_id : idx + 1;
        const turnEventId = `triad_${state.session_id}_turn_${turnId}`;
        appendMessage(
          turn.speaker,
          turn.content,
          {
            model: turn.model,
            latency_sec: turn.latency_sec,
            cost_usd_est: turn.cost_usd_est,
          },
          turnEventId
        );
      });
    }

    // 2. Manage Approval Card Display
    if (state.pending_gate && state.status === "PENDING_APPROVAL") {
      renderApprovalGate(state.pending_gate);
    } else {
      renderApprovalGate(null);
    }

    // 3. Render Terminal Session States Idempotently (Once per session)
    const terminalStatuses = ["COMPLETED", "CANCELLED_BY_USER", "FAILED"];
    if (terminalStatuses.includes(state.status)) {
      const termEventId = `triad_${state.session_id}_terminal_${state.status}`;
      if (state.status === "COMPLETED") {
        appendMessage(
          "Genesis",
          "🎉 Supervised Triad Round COMPLETED. All agent turns and audit logs recorded.",
          {},
          termEventId
        );
      } else if (state.status === "CANCELLED_BY_USER") {
        appendMessage(
          "Genesis",
          `🛑 Triad round was cancelled: ${state.error_message || "Action rejected."}`,
          {},
          termEventId
        );
      } else if (state.status === "FAILED") {
        appendMessage(
          "Genesis",
          `⚠️ Triad round failed: ${state.error_message}`,
          {},
          termEventId
        );
      }
    }
    updateSystemStatus();
  }

  async function checkTriadStatus() {
    try {
      const res = await fetch("/api/chat/triad/status");
      const data = await res.json();
      if (data && data.status && data.status !== "IDLE") {
        renderTriadState(data);
      }
    } catch (e) {}
  }

  // ---------------------------------------------------------------------------
  // 6. Emergency Stop (Kill Switch)
  // ---------------------------------------------------------------------------
  btnKillSwitch.addEventListener("click", async () => {
    const confirmStop = confirm("🚨 ACTIVATE EMERGENCY STOP?\n\nThis will immediately halt all agent communication and block outgoing requests.");
    if (!confirmStop) return;

    try {
      const res = await fetch("/api/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "trigger" }),
      });
      const data = await res.json();
      updateSystemStatus();
      alert("🛑 Emergency stop activated.");
    } catch (err) {
      alert(`Error triggering stop: ${err.message}`);
    }
  });

  btnResetKillSwitch.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "clear" }),
      });
      const data = await res.json();
      updateSystemStatus();
      alert("✅ Emergency stop cleared. Interface operational.");
    } catch (err) {
      alert(`Error clearing stop: ${err.message}`);
    }
  });

  // ---------------------------------------------------------------------------
  // 7. Live Development Journal Integration
  // ---------------------------------------------------------------------------
  async function pollJournalStatus() {
    try {
      const res = await fetch("/api/journal/status");
      const data = await res.json();
      if (!res.ok) return;

      const st = data.status;
      if (st === "ACTIVE") {
        journalPulse.className = "status-pulse active";
        journalStatusText.textContent = `Journal: ACTIVE (${data.activity || "Working"})`;
        btnJournalBreak.style.display = "inline-flex";
        btnJournalResume.style.display = "none";
        btnJournalStop.style.display = "inline-flex";
        journalTimer.textContent = formatSeconds(data.active_work_sec || 0);
      } else if (st === "PAUSED") {
        journalPulse.className = "status-pulse paused";
        const reason = data.current_break?.reason ? ` (${data.current_break.reason})` : "";
        journalStatusText.textContent = `Journal: PAUSED${reason}`;
        btnJournalBreak.style.display = "none";
        btnJournalResume.style.display = "inline-flex";
        btnJournalStop.style.display = "inline-flex";
        journalTimer.textContent = `Break: ${formatSeconds(data.total_break_sec || 0)}`;
      } else {
        journalPulse.className = "status-pulse idle";
        journalStatusText.textContent = "Journal: IDLE";
        btnJournalBreak.style.display = "none";
        btnJournalResume.style.display = "none";
        btnJournalStop.style.display = "none";
        journalTimer.textContent = "--:--:--";
      }
    } catch (e) {}
  }

  btnJournalBreak.addEventListener("click", async () => {
    const reason = prompt("Enter break reason (e.g. Dinner, Coffee, Rest):", "Break");
    if (reason === null) return;
    await fetch("/api/journal/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "break", reason: reason }),
    });
    pollJournalStatus();
  });

  btnJournalResume.addEventListener("click", async () => {
    await fetch("/api/journal/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "resume" }),
    });
    pollJournalStatus();
  });

  btnJournalStop.addEventListener("click", async () => {
    const summary = prompt("Enter session summary of work completed:", "Finished session");
    if (summary === null) return;
    await fetch("/api/journal/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "stop", summary: summary }),
    });
    pollJournalStatus();
  });

  // Journal Report Modal
  btnJournalReportModal.addEventListener("click", async () => {
    journalModal.style.display = "flex";
    journalModalBody.textContent = "Loading report data...";
    try {
      const res = await fetch("/api/journal/report");
      const rep = await res.json();
      const today = rep.today;
      const week = rep.this_week;
      const cumul = rep.cumulative;

      let html = `
        <div style="display:flex; flex-direction:column; gap:14px;">
          <div style="background:#0f172a; padding:12px 16px; border-radius:8px; border:1px solid #334155;">
            <strong style="color:#38bdf8;">Today (${today.date}):</strong><br>
            • Active Work: <strong>${formatSeconds(today.active_work_sec)}</strong><br>
            • Breaks: ${formatSeconds(today.break_sec)} (${today.sessions_count} sessions)
          </div>
          <div style="background:#0f172a; padding:12px 16px; border-radius:8px; border:1px solid #334155;">
            <strong style="color:#34d399;">This Week (since ${week.week_start}):</strong><br>
            • Active Work: <strong>${formatSeconds(week.active_work_sec)}</strong><br>
            • Breaks: ${formatSeconds(week.break_sec)}
          </div>
          <div style="background:#0f172a; padding:12px 16px; border-radius:8px; border:1px solid #334155;">
            <strong style="color:#c084fc;">All-Time Cumulative:</strong><br>
            • Total Active Work: <strong>${formatSeconds(cumul.total_active_work_sec)}</strong><br>
            • Total Breaks: ${formatSeconds(cumul.total_break_sec)}<br>
            • Sessions: ${cumul.total_sessions_count}
          </div>
        </div>
      `;
      journalModalBody.innerHTML = html;
    } catch (e) {
      journalModalBody.textContent = `Error loading report: ${e.message}`;
    }
  });

  btnCloseJournalModal.addEventListener("click", () => {
    journalModal.style.display = "none";
  });

  // ---------------------------------------------------------------------------
  // 8. Polling Loop
  // ---------------------------------------------------------------------------
  async function updateSystemStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (!res.ok) return;

      // 1. Emergency Kill Switch Indicator & Fail-Closed Enforcement
      if (data.kill_switch_active) {
        killSwitchBanner.style.display = "flex";
        if (liveModeToggle.checked) {
          liveModeToggle.checked = false;
          isLiveMode = false;
          setOfflineBadge();
        }
        liveModeToggle.disabled = true;
      } else {
        killSwitchBanner.style.display = "none";
        liveModeToggle.disabled = false;
      }

      // 2. Cumulative Spending from backend governor
      if (data.cumulative_cost_usd !== undefined) {
        cumulativeCostVal.textContent = `$${data.cumulative_cost_usd.toFixed(6)}`;
      }

      // 3. Cost Cap Ceiling from backend governor
      if (data.max_cost_usd !== undefined) {
        const costCapEl = document.getElementById("costCapVal");
        if (costCapEl) {
          costCapEl.textContent = `$${data.max_cost_usd.toFixed(3)} USD`;
        }
      }

      // 4. Sandbox Profile Indicator from backend engine
      if (data.sandbox_status) {
        const sandboxEl = document.getElementById("sandboxProfileVal");
        if (sandboxEl) {
          sandboxEl.textContent = data.sandbox_status;
        }
      }
    } catch (e) {
      console.error("Failed to update system status:", e);
    }
  }

  // Poll intervals
  pollJournalStatus();
  updateSystemStatus();
  setInterval(pollJournalStatus, 2000);
  setInterval(updateSystemStatus, 3000);
  setInterval(checkTriadStatus, 3000);
});
