// Chat UI for the AI booking assistant. Plain JavaScript, no build step.
(() => {
  const el = (id) => document.getElementById(id);
  const messages = el("messages");
  const form = el("chat-form");
  const input = el("chat-input");
  const sendButton = el("send");
  const pendingBox = el("pending");
  const banner = el("status-banner");
  const statusDot = el("status-dot");
  let busy = false;

  // Message text is always inserted with textContent, never as HTML.
  // Strip markdown bold that small models like to add; replies are shown as plain text.
  const clean = (text) => (text || "").replace(/\*\*(.+?)\*\*/g, "$1").trim();

  function avatar(role, error) {
    const span = document.createElement("span");
    const tone =
      role === "user"
        ? "bg-slate-700 text-white"
        : error
          ? "bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-600/15"
          : "bg-teal-50 text-teal-700 ring-1 ring-inset ring-teal-600/15";
    span.className = `inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${tone}`;
    span.append(el(role === "user" ? "icon-user" : "icon-assistant").content.cloneNode(true));
    return span;
  }

  function addRow(role, { error = false } = {}) {
    const item = document.createElement("li");
    item.className = role === "user" ? "flex flex-row-reverse items-end gap-2.5" : "flex items-end gap-2.5";
    const bubble = document.createElement("div");
    const base = "chat-in max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed";
    if (role === "user") bubble.className = `${base} rounded-br-md bg-teal-700 text-white shadow-xs`;
    else if (error) bubble.className = `${base} rounded-bl-md bg-rose-50 text-rose-800 ring-1 ring-rose-200`;
    else bubble.className = `${base} rounded-bl-md bg-white text-slate-700 shadow-xs ring-1 ring-slate-200`;
    item.append(avatar(role, error), bubble);
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return { item, bubble };
  }

  function addMessage(role, text, options) {
    addRow(role, options).bubble.textContent = clean(text);
  }

  // What the assistant is doing while a tool runs.
  const TOOL_LABELS = {
    list_doctors: "Looking up our doctors",
    find_available_slots: "Checking free slots",
    book_slot: "Preparing your booking",
    find_bookings: "Looking up your bookings",
    cancel_booking: "Preparing the cancellation",
  };

  function showTyping() {
    const { item, bubble } = addRow("assistant");
    bubble.classList.add("flex", "items-center", "gap-3");
    const dots = document.createElement("span");
    dots.className = "flex items-center gap-1";
    for (let i = 0; i < 3; i++) {
      const dot = document.createElement("span");
      dot.className = "typing-dot h-1.5 w-1.5 rounded-full bg-slate-400";
      dots.appendChild(dot);
    }
    const note = document.createElement("span");
    note.className = "text-xs text-slate-400";
    bubble.append(dots, note);
    const started = Date.now();
    let status = "Thinking";
    // Show what's happening and the elapsed time, so a slow local model doesn't look frozen.
    const render = () => {
      const seconds = Math.round((Date.now() - started) / 1000);
      note.textContent = seconds >= 3 ? `${status}… ${seconds}s` : `${status}…`;
    };
    render();
    const timer = setInterval(render, 1000);
    return {
      setStatus(text) {
        status = text;
        render();
      },
      stop() {
        clearInterval(timer);
        item.remove();
      },
    };
  }

  function renderPending(pending) {
    if (!pending) {
      pendingBox.classList.add("hidden");
      return;
    }
    el("pending-title").textContent =
      pending.action === "cancel" ? "Confirm cancellation" : "Confirm booking";
    const rows = [
      ["Doctor", `${pending.doctor} · ${pending.specialty}`],
      ["Date", pending.date_label || pending.date],
      ["Time", `${pending.start} – ${pending.end}`],
      ["Patient", pending.patient_name],
      ["Phone", pending.phone_number],
    ];
    const details = el("pending-details");
    details.replaceChildren();
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.className = "text-slate-500";
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.className = "font-medium text-slate-900";
      dd.textContent = value;
      details.append(dt, dd);
    }
    pendingBox.classList.remove("hidden");
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
    for (const button of pendingBox.querySelectorAll("button")) button.disabled = value;
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Something went wrong. Please try again.");
    return data;
  }

  async function run(request) {
    setBusy(true);
    const typing = showTyping();
    try {
      const data = await request();
      typing.stop();
      addMessage("assistant", data.reply, { error: data.error });
      renderPending(data.pending);
    } catch (err) {
      typing.stop();
      addMessage("assistant", err.message || "Network error. Please try again.", { error: true });
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  // Sends a chat message and shows the reply as it streams in (newline-delimited JSON events).
  async function streamChat(text) {
    setBusy(true);
    let typing = showTyping();
    let live = null; // the reply bubble being streamed into
    let streamed = "";
    let result = null;

    const dropLive = () => {
      if (live) live.item.remove();
      live = null;
      streamed = "";
    };
    const handle = (event) => {
      if (event.type === "delta") {
        if (typing) typing.stop();
        typing = null;
        if (!live) live = addRow("assistant");
        streamed += event.text;
        live.bubble.textContent = clean(streamed);
        messages.scrollTop = messages.scrollHeight;
      } else if (event.type === "reset" || event.type === "tool") {
        dropLive();
        if (!typing) typing = showTyping();
        if (event.type === "tool") typing.setStatus(TOOL_LABELS[event.name] || "Working on it");
      } else if (event.type === "done") {
        result = event;
      }
    };

    try {
      const response = await fetch("/api/assistant/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Something went wrong. Please try again.");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) handle(JSON.parse(line));
        }
      }
      if (!result) throw new Error("The connection was interrupted. Please try again.");

      if (typing) typing.stop();
      // The final text is authoritative (it may differ slightly from what was streamed).
      if (live && !result.error) live.bubble.textContent = clean(result.reply);
      else {
        dropLive();
        addMessage("assistant", result.reply, { error: result.error });
      }
      renderPending(result.pending);
    } catch (err) {
      if (typing) typing.stop();
      dropLive();
      addMessage("assistant", err.message || "Network error. Please try again.", { error: true });
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  function send(text) {
    text = text.trim();
    if (!text || busy) return;
    el("suggestions").classList.add("hidden");
    addMessage("user", text);
    input.value = "";
    streamChat(text);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });

  for (const button of document.querySelectorAll("[data-suggestion]")) {
    button.addEventListener("click", () => send(button.dataset.suggestion));
  }

  el("confirm-yes").addEventListener("click", () => {
    if (busy) return;
    addMessage("user", "Yes, confirm.");
    run(() => post("/api/assistant/confirm", { accept: true }));
  });
  el("confirm-no").addEventListener("click", () => {
    if (busy) return;
    addMessage("user", "No, don't do it.");
    run(() => post("/api/assistant/confirm", { accept: false }));
  });

  el("reset").addEventListener("click", async () => {
    if (busy) return;
    await post("/api/assistant/reset").catch(() => {});
    messages.replaceChildren(messages.firstElementChild);
    renderPending(null);
    el("suggestions").classList.remove("hidden");
    input.focus();
  });

  async function checkStatus() {
    try {
      const response = await fetch("/api/assistant/status");
      const status = await response.json();
      statusDot.classList.remove("bg-slate-300", "bg-emerald-400", "bg-amber-400");
      statusDot.classList.add(status.ready ? "bg-emerald-400" : "bg-amber-400");
      // Patients only need to know whether the assistant is available, not which model runs it.
      el("status-text").textContent = status.ready ? "Online" : "Temporarily unavailable";
      if (!status.ready && status.message) {
        el("status-message").textContent = status.message;
        banner.classList.remove("hidden");
        banner.classList.add("flex");
      }
    } catch {
      el("status-text").textContent = "Status unknown";
    }
  }

  checkStatus();
})();
