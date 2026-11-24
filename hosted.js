const review = document.querySelector(".review-detail");
if (review) {
  const panels = [...review.querySelectorAll("[data-pr-panel]")];
  const links = [...review.querySelectorAll(".pr-sections a")];
  function syncPanel() {
    const target = document.getElementById(location.hash.slice(1));
    const panel = target?.closest("[data-pr-panel]") ||
      panels.find(item => item.dataset.prPanel === document.body.dataset.defaultPanel) || panels[0];
    for (const item of panels) item.hidden = item !== panel;
    for (const link of links) {
      const anchor = document.getElementById(link.hash.slice(1));
      if (panel.contains(anchor)) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
  }
  syncPanel();
  for (const link of links) {
    link.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      event.preventDefault();
      if (link.hash !== location.hash) history.pushState(null, "", link.hash);
      syncPanel();
    });
  }
  window.addEventListener("hashchange", syncPanel);
  window.addEventListener("popstate", syncPanel);
}

const filterBar = document.getElementById("pr-filter");
const records = [...document.querySelectorAll("#pr-list [data-pr-state]")];
if (filterBar && records.length && !review) {
  filterBar.hidden = false;
  const empty = document.createElement("li");
  document.getElementById("pr-list").append(empty);
  function apply(state) {
    let count = 0;
    for (const record of records) {
      const open = record.dataset.prState === "open";
      record.hidden = state === "open" ? !open : state === "closed" ? open : false;
      if (!record.hidden) count++;
    }
    for (const button of filterBar.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.state === state));
    }
    empty.hidden = count > 0;
    empty.textContent = state === "closed" ? "No closed pull requests." : "No open pull requests.";
  }
  filterBar.addEventListener("click", event => {
    const button = event.target.closest("[data-state]");
    if (button) apply(button.dataset.state);
  });
  apply("open");
}

for (const button of document.querySelectorAll("[data-copy-field]")) {
  button.hidden = false;
  button.addEventListener("click", async () => {
    const field = document.getElementById(button.dataset.copyField);
    const status = document.getElementById("review-copy-status");
    try {
      await navigator.clipboard.writeText(field.value);
      status.textContent = field.id === "assistant-brief" ? "Review brief copied." : "Git commands copied.";
    } catch {
      document.getElementById("review-tools").open = true;
      field.focus();
      field.select();
      status.textContent = "Text selected. Use your browser’s Copy command.";
    }
  });
}
