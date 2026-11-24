async function showLocalIdentity() {
  const container = document.getElementById("local-identity");
  if (!container) return;
  try {
    const response = await fetch("/identity");
    if (!response.ok) throw Error();
    const {person, managed, name: displayName} = await response.json();
    if (managed) {
      const back = location.pathname + location.search + location.hash;
      const link = document.createElement("a");
      link.href = "/users?" + new URLSearchParams({return:back});
      link.textContent = person ? displayName + " (@" + person + ")" : "Sign in";
      container.replaceChildren(link);
      return;
    }
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = person ? "Reviewing as @" + person : "Reviewer identity";
    const form = document.createElement("form");
    const label = document.createElement("label");
    label.textContent = "Local reviewer handle";
    const name = document.createElement("input");
    name.value = person;
    name.placeholder = "e.g. sam";
    name.maxLength = 48;
    name.pattern = "[a-zA-Z0-9][a-zA-Z0-9._\\-]{0,47}";
    name.autocapitalize = "none";
    label.append(name);
    const save = document.createElement("button");
    save.textContent = "Use identity";
    const clear = document.createElement("button");
    clear.textContent = "Browse anonymously";
    clear.hidden = !person;
    clear.formNoValidate = true;
    const hint = document.createElement("small");
    hint.textContent = "This name identifies your reviews in this browser. Anyone using this local tool can choose a name.";
    const message = document.createElement("p");
    message.setAttribute("role", "status");
    form.append(label, save, clear);
    form.onsubmit = async event => {
      event.preventDefault();
      const chosen = event.submitter === clear ? "" : name.value.trim();
      try {
        const result = await fetch("/identity", {method:"POST", body:new URLSearchParams({person:chosen})});
        if (!result.ok) throw Error();
        location.reload();
      } catch {
        message.textContent = "Could not update the local reviewer identity.";
      }
    };
    const setup = document.createElement("a");
    setup.href = "/users";
    setup.textContent = "Set up user accounts";
    details.append(summary, hint, form, setup, message);
    container.replaceChildren(details);
    if (location.hash === "#local-identity") details.open = true;
    window.addEventListener("hashchange", () => { if (location.hash === "#local-identity") details.open = true; });
  } catch {
    container.textContent = "Local reviewer identity unavailable.";
  }
}
showLocalIdentity();
// Restore the handle and server-rendered reviewer controls together on Back.
window.addEventListener("pageshow", event => {
  if (event.persisted) location.replace(location.href);
});
