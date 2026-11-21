// Small progressive enhancements shared by every page. Everything still works without JavaScript.
(() => {
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const storage = {
    get(key) {
      try { return sessionStorage.getItem(key); } catch { return null; }
    },
    set(key, value) {
      try { sessionStorage.setItem(key, value); } catch { /* private mode: ignore */ }
    },
    remove(key) {
      try { sessionStorage.removeItem(key); } catch { /* ignore */ }
    },
  };

  // --- Keep the scroll position across form posts (they redirect back to the same page) ---
  const SCROLL_KEY = "clinicbook:scroll";
  const saved = storage.get(SCROLL_KEY);
  if (saved) {
    storage.remove(SCROLL_KEY);
    const { path, y } = JSON.parse(saved);
    if (path === location.pathname) window.scrollTo({ top: y, behavior: "instant" });
  }

  // --- Forms: confirmation prompts, busy state, scroll memory ---
  for (const form of $$("form[method='post' i]")) {
    form.addEventListener("submit", (event) => {
      const question = form.dataset.confirm;
      if (question && !window.confirm(question)) {
        event.preventDefault();
        return;
      }
      if (form.dataset.busy) {
        event.preventDefault(); // already submitting: avoid double bookings
        return;
      }
      form.dataset.busy = "1";
      storage.set(SCROLL_KEY, JSON.stringify({ path: location.pathname, y: window.scrollY }));
      const button = form.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        const spinner = document.createElement("span");
        spinner.className = "btn-spinner";
        spinner.setAttribute("aria-hidden", "true");
        button.replaceChildren(spinner, document.createTextNode(form.dataset.loadingText || "Saving…"));
      }
    });
  }

  // Submit the date picker as soon as a date is chosen.
  for (const input of $$("[data-autosubmit]")) {
    input.addEventListener("change", () => input.form && input.form.submit());
  }

  // --- Slide-over dialogs ---
  for (const button of $$("[data-open-dialog]")) {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog) dialog.showModal();
    });
  }
  for (const dialog of $$("dialog")) {
    for (const close of $$("[data-close-dialog]", dialog)) close.addEventListener("click", () => dialog.close());
    // Close when clicking the dimmed backdrop.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
  }

  // --- New schedule form: length presets and a live summary ---
  const scheduleForm = document.querySelector("[data-schedule-form]");
  if (scheduleForm) {
    const duration = scheduleForm.querySelector("#gen-duration");
    const start = scheduleForm.querySelector("#gen-start");
    const end = scheduleForm.querySelector("#gen-end");
    const preview = scheduleForm.querySelector("[data-schedule-preview] span");
    const chips = $$("[data-duration]", scheduleForm);
    const minutes = (value) => {
      const [h, m] = (value || "").split(":").map(Number);
      return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : NaN;
    };
    const update = () => {
      for (const chip of chips) chip.setAttribute("aria-pressed", String(chip.dataset.duration === duration.value));
      const length = Number(duration.value);
      const span = minutes(end.value) - minutes(start.value);
      if (!preview) return;
      if (!(length >= 1 && length <= 240)) preview.textContent = "Appointment length must be between 1 and 240 minutes.";
      else if (!(span > 0)) preview.textContent = "The end time must be after the start time.";
      else {
        const count = Math.floor(span / length);
        preview.textContent = count
          ? `${count} ${count === 1 ? "slot" : "slots"} of ${length} minutes, ${start.value} to ${end.value}.`
          : "No appointments fit in these hours with this length.";
      }
    };
    for (const chip of chips) {
      chip.addEventListener("click", () => {
        duration.value = chip.dataset.duration;
        update();
      });
    }
    for (const input of [duration, start, end]) input.addEventListener("input", update);
    update();
  }

  // --- Toasts: dismiss on click, or automatically after a few seconds ---
  const dismiss = (toast) => {
    if (toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  for (const toast of $$("[data-toast]")) {
    toast.querySelector("[data-dismiss-toast]")?.addEventListener("click", () => dismiss(toast));
    const delay = toast.getAttribute("role") === "alert" ? 9000 : 5000;
    let timer = setTimeout(() => dismiss(toast), delay);
    toast.addEventListener("mouseenter", () => clearTimeout(timer));
    toast.addEventListener("mouseleave", () => { timer = setTimeout(() => dismiss(toast), 2500); });
  }
  // Remove the message from the URL so a refresh doesn't show it again.
  const url = new URL(location.href);
  if (["error", "success", "created", "skipped"].some((key) => url.searchParams.has(key))) {
    for (const key of ["error", "success", "created", "skipped"]) url.searchParams.delete(key);
    history.replaceState(null, "", url);
  }
})();
