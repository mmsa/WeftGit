const filter = document.getElementById("file-filter");
if (filter) {
  const queryParams = new URLSearchParams(location.search);
  const storageKey = "weftgit.file-filter." + queryParams.get("repo") + "." + queryParams.get("pr");
  try { filter.value = readWeftGitSetting(sessionStorage, storageKey) || ""; } catch { /* Storage is optional. */ }
  function applyFilter() {
    const query = filter.value.trim().toLowerCase();
    const items = document.querySelectorAll(".file-index li");
    let found = false;
    for (const item of items) {
      item.hidden = !item.querySelector("strong").textContent.toLowerCase().includes(query);
      if (!item.hidden) found = true;
    }
    document.getElementById("file-empty").hidden = found;
  }
  applyFilter();
  filter.addEventListener("input", () => {
    applyFilter();
    try { sessionStorage.setItem(storageKey, filter.value); } catch { /* Storage is optional. */ }
  });
}

const taskDetails = document.getElementById("file-task");
if (taskDetails) {
  const form = taskDetails.querySelector("form");
  const side = form.elements.side;
  const lineNumber = form.elements.line;
  function updateLocation() {
    lineNumber.disabled = side.value === "file";
    lineNumber.required = !lineNumber.disabled;
    lineNumber.closest("label").hidden = lineNumber.disabled;
  }
  side.addEventListener("change", updateLocation);
  updateLocation();
  const supported = new Set(Array.from(side.options, option => option.value));
  let before, after;
  for (const span of document.querySelectorAll("#file-patch > pre.patch > span")) {
    const text = span.textContent;
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {
      before = Number(hunk[1]);
      after = Number(hunk[2]);
      continue;
    }
    // Later patch sections may start mid-hunk; wait for a known line coordinate.
    if (before === undefined) continue;
    let location, number;
    if (text.startsWith("-")) {
      location = "before"; number = before++;
    } else if (text.startsWith("+")) {
      location = "after"; number = after++;
    } else if (text.startsWith(" ")) {
      before++; after++;
    }
    if (!location || !supported.has(location)) continue;
    const selectLine = document.createElement("button");
    selectLine.type = "button";
    selectLine.className = "patch-line-number";
    selectLine.textContent = String(number);
    selectLine.setAttribute("aria-label", "Add task on " + location + " line " + number);
    selectLine.onclick = () => {
      taskDetails.open = true;
      side.value = location;
      updateLocation();
      lineNumber.value = String(number);
      form.elements.note.focus();
    };
    span.prepend(selectLine);
  }
}
