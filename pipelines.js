const byId = (id) => document.getElementById(id);
const types = ["Build", "Test", "Deploy"];
const storageKey = "freethegit.pipelines.v1";
const example = [{
  name: "Billing exports",
  repos: [{
    name: "exports/api",
    url: "",
    stages: defaultStages(),
  }],
}];

function defaultStages() {
  return ["Staging", "Pre-prod", "Prod"].map((environment) => ({
    name: environment,
    type: "Deploy",
    command: "",
    environment,
  }));
}

// Upgrade the original demo only when all of its stages match the shipped data.
const oldDemoStages = [
  { name: "Build export service", type: "Build", command: "python -m build", environment: "" },
  { name: "Test CSV export", type: "Test", command: "python -m pytest", environment: "" },
  { name: "Deploy to staging", type: "Deploy", command: "bash scripts/deploy-staging.sh", environment: "Staging" },
];

let projects = example;
let savedWorkspace = null;
let recoveryCopy = null;
let projectIndex = 0;
let repoIndex = 0;
let editing = false;

function readProjects(raw) {
  if (raw === null) return structuredClone(example);
  const records = JSON.parse(raw);
  if (!Array.isArray(records)) throw new Error("Invalid workspace");
  const validName = record => typeof record?.name === "string" && record.name.trim();
  const recovered = records.filter(validName).map(project => ({
    ...project,
    repos: (Array.isArray(project.repos) ? project.repos : []).filter(validName).map(repo => ({
      ...repo,
      stages: Array.isArray(repo.stages) ? repo.stages.filter(validName) : defaultStages(),
    })),
  }));
  if (JSON.stringify(recovered) !== JSON.stringify(records)) {
    recoveryCopy = raw;
    byId("notice").textContent = "Recovered the readable pipeline settings. The original is backed up when you save.";
  }
  const demo = recovered[0]?.name === "Billing exports" ? recovered[0].repos[0] : null;
  if (demo?.name === "exports/api" && demo.url === "" &&
      JSON.stringify(demo.stages) === JSON.stringify(oldDemoStages)) demo.stages = defaultStages();
  return recovered;
}

function loadWorkspace(raw) {
  savedWorkspace = raw;
  recoveryCopy = null;
  try {
    projects = readProjects(raw);
  } catch {
    recoveryCopy = raw;
    projects = structuredClone(example);
    byId("notice").textContent = "The saved pipeline configuration could not be read. It will be backed up before your next save.";
  }
}

function rememberSelection() {
  try { sessionStorage.setItem("freethegit.selection", JSON.stringify([projectIndex, repoIndex])); }
  catch { /* Selection is optional when browser storage is unavailable. */ }
}

try {
  loadWorkspace(localStorage.getItem(storageKey));
  const selected = JSON.parse(sessionStorage.getItem("freethegit.selection"));
  if (Array.isArray(selected) && Number.isInteger(selected[0]) && projects[selected[0]]) {
    projectIndex = selected[0];
    if (Number.isInteger(selected[1]) && projects[projectIndex].repos[selected[1]]) repoIndex = selected[1];
  }
} catch { /* In-memory editing remains available. */ }
const dialog = byId("dialog");

function button(label, action, disabled = false) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.disabled = disabled;
  element.addEventListener("click", action);
  return element;
}

async function save() {
  render();
  function write() {
    const latest = localStorage.getItem(storageKey);
    if (latest !== savedWorkspace) {
      loadWorkspace(latest);
      projectIndex = projects[projectIndex] ? projectIndex : 0;
      repoIndex = projects[projectIndex]?.repos[repoIndex] ? repoIndex : 0;
      render();
      byId("notice").textContent = "Another tab changed this workspace. Its changes are loaded. Apply your edit again.";
      return;
    }
    if (recoveryCopy !== null) {
      localStorage.setItem(storageKey + ".recovery." + crypto.randomUUID(), recoveryCopy);
      recoveryCopy = null;
    }
    const snapshot = JSON.stringify(projects);
    localStorage.setItem(storageKey, snapshot);
    savedWorkspace = snapshot;
    byId("notice").textContent = "Saved in this browser.";
  }
  try {
    if (navigator.locks) await navigator.locks.request(storageKey, write);
    else write();
  } catch {
    byId("notice").textContent = "Browser storage is unavailable. Changes last until this page closes.";
  }
  rememberSelection();
}

function edit(title, fields, apply, submitLabel = "Save") {
  byId("edit-form").querySelector('[type="submit"]').textContent = submitLabel;
  byId("dialog-title").textContent = title;
  byId("fields").replaceChildren();
  byId("edit-error").textContent = "";
  for (const [name, label, value, kind, required] of fields) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement(kind === "select" ? "select" : kind === "textarea" ? "textarea" : "input");
    if (kind === "select") {
      for (const type of types) input.add(new Option(type));
    } else if (kind !== "textarea") {
      input.type = kind;
    }
    input.name = name;
    input.value = value;
    input.required = required;
    wrapper.append(input);
    byId("fields").append(wrapper);
  }
  byId("edit-form").onsubmit = async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    for (const field of Object.keys(values)) values[field] = values[field].trim();
    if (!values.name) {
      byId("edit-error").textContent = "Enter a name to save.";
      return;
    }
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await apply(values);
    } catch (error) {
      byId("edit-error").textContent = error.message;
      return;
    } finally {
      submit.disabled = false;
    }
    dialog.close();
    save();
  };
  dialog.showModal();
}

function editStage(stages, index = stages.length) {
  const stage = stages[index] ?? { name: "", type: "Build", environment: "", command: "" };
  edit(index === stages.length ? "Add stage" : `Configure ${stage.name}`, [
    ["name", "Stage name", stage.name, "text", true],
    ["type", "Stage type", stage.type, "select", true],
    ["environment", "Deployment environment", stage.environment, "text", false],
    ["sourceRef", "Git ref (defaults to the repository ref)", stage.sourceRef ?? "", "text", false],
    ["configPath", "Config file path (optional, relative to repository)", stage.configPath ?? "", "text", false],
    ["target", "Deployment target (optional)", stage.target ?? "", "text", false],
    ["command", "Command (optional)", stage.command, "textarea", false],
  ], (values) => { stages[index] = values; });
}

async function configureHostedPipeline(slug) {
  const url = "http://127.0.0.1:" + location.port + "/git/" + slug + ".git";
  for (const [p, project] of projects.entries()) {
    const r = project.repos.findIndex(repo => repo.url === url);
    if (r !== -1) {
      projectIndex = p;
      repoIndex = r;
      editing = false;
      rememberSelection();
      render();
      location.hash = "pipelines";
      return;
    }
  }
  const [group, name] = slug.split("/");
  projectIndex = projects.findIndex(project => project.name === group);
  if (projectIndex === -1) projectIndex = projects.push({name: group, repos: []}) - 1;
  const repos = projects[projectIndex].repos;
  repoIndex = repos.push({name, url, stages: defaultStages()}) - 1;
  editing = false;
  await save();
  location.hash = "pipelines";
}

function render() {
  const navigation = byId("projects");
  navigation.replaceChildren();
  projects.forEach((project, p) => {
    const section = document.createElement("section");
    section.className = "project";
    const title = document.createElement("strong");
    title.textContent = project.name;
    const repos = document.createElement("ul");
    project.repos.forEach((repo, r) => {
      const row = document.createElement("li");
      const item = button(repo.name, () => { projectIndex = p; repoIndex = r; editing = false; rememberSelection(); render(); });
      item.className = "repo";
      if (p === projectIndex && r === repoIndex) item.setAttribute("aria-current", "true");
      row.append(item);
      repos.append(row);
    });
    section.append(title, repos, button("Add repository", () => edit("Add repository", [
      ["name", "Repository name", "", "text", true],
      ["url", "Repository URL (optional)", "", "url", false],
    ], (values) => {
      projectIndex = p;
      repoIndex = project.repos.length;
      project.repos.push({ ...values, stages: defaultStages() });
    })));
    section.append(button("Host repository", () => edit("Host repository", [
      ["project", "Project slug", project.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), "text", true],
      ["name", "Repository slug", "", "text", true],
    ], async ({ project: slug, name }) => {
      const response = await fetch("/repositories", {
        method: "POST",
        body: new URLSearchParams({ project: slug, name }),
      });
      if (!response.ok) throw new Error(response.status === 409
        ? "That repository already exists."
        : "Could not create the repository. Use letters, numbers, hyphens or underscores.");
      const { url } = await response.json();
      projectIndex = p;
      repoIndex = project.repos.length;
      project.repos.push({ name, url, ref: "main", stages: defaultStages() });
      void loadDirectory();
    })));
    navigation.append(section);
  });

  const project = projects[projectIndex];
  const repo = project?.repos[repoIndex];
  byId("project-name").textContent = project?.name ?? "";
  byId("repo-name").textContent = repo?.name ?? "Pipelines";
  byId("repo-url").textContent = repo?.url ?? "";
  byId("editor").hidden = !repo;
  byId("repo-settings").hidden = !repo;
  const hosted = /^http:\/\/127\.0\.0\.1:8091\/git\/([\w-]+\/[\w-]+)\.git$/.exec(repo?.url ?? "");
  const tools = byId("hosted-tools");
  tools.hidden = !hosted;
  if (hosted) tools.href = "/hosted?" + new URLSearchParams({ repo: hosted[1] });
  byId("review-description").textContent = hosted ? "Compare branches and track review tasks." : "Host a repository to open pull requests and track review tasks.";
  byId("deploy-description").textContent = hosted ? "Publish snapshots and inspect their history." : "Hosted repositories can publish local snapshots.";
  for (const [id, view] of [["review-link", "pulls"], ["deploy-link", "deploys"]]) {
    const link = byId(id);
    link.hidden = !hosted;
    if (hosted) link.href = "/hosted?" + new URLSearchParams({ repo: hosted[1], view });
  }
  const tryReview = byId("try-review");

  tryReview.onclick = async () => {
    tryReview.disabled = true;
    const name = "billing-" + crypto.randomUUID().slice(0, 8);
    try {
      const response = await fetch("/repositories", { method: "POST",
        body: new URLSearchParams({ project: "examples", name, example: "review" }) });
      if (!response.ok) throw new Error("Could not create the example review.");
      const { url } = await response.json();
      if (project) {
        repoIndex = project.repos.push({ name, url, ref: "main", stages: defaultStages() }) - 1;
      }
      await save();
      location.assign("/hosted?" + new URLSearchParams({ repo: "examples/" + name, pr: "1" }));
    } catch (error) {
      byId("notice").textContent = error.message;
    } finally {
      tryReview.disabled = false;
    }
  };
  byId("empty").hidden = Boolean(repo);
  if (!repo) return;
  byId("repo-settings").onclick = () => edit("Repository settings", [
    ["name", "Repository name", repo.name, "text", true],
    ["url", "Repository URL", repo.url, "url", false],
    ["ref", "Default branch or ref", repo.ref ?? "main", "text", true],
  ], (values) => Object.assign(repo, values));
  byId("toggle-edit").textContent = editing ? "Done editing" : "Edit pipeline";
  byId("toggle-edit").setAttribute("aria-expanded", String(editing));
  byId("editor-tools").hidden = !editing;
  byId("stages").classList.toggle("overview", !editing);
  byId("add-stage").onclick = () => editStage(repo.stages);
  const list = byId("stages");
  list.replaceChildren();
  repo.stages.forEach((stage, index) => {
    const row = document.createElement("li");
    row.className = "stage";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    const type = document.createElement("small");
    title.textContent = stage.name;
    type.textContent = stage.type === "Deploy"
      ? (stage.environment && stage.environment !== stage.name ? stage.environment : "Deployment")
      : stage.type;
    details.append(title, type);
    if (editing && stage.command) {
      const command = document.createElement("code");
      command.textContent = stage.command;
      details.append(command);
    }
    list.append(row);
    if (!editing) {
      const configure = button("", () => editStage(repo.stages, index));
      configure.className = "stage-configure";
      configure.setAttribute("aria-label", `Configure ${stage.name}`);
      configure.append(details);
      row.append(configure);
      const environment = typeof stage.environment === "string" ? stage.environment.toLowerCase() : "";
      if (hosted && stage.type === "Deploy" && ["staging", "pre-prod", "prod"].includes(environment)
          && !stage.target && !stage.configPath && !stage.command) {
        const deploy = document.createElement("a");
        const params = new URLSearchParams({repo: hosted[1], view: "deploys", environment});
        const ref = stage.sourceRef || repo.ref;
        if (ref) params.set("ref", ref);
        deploy.href = "/hosted?" + params;
        deploy.className = "stage-deploy";
        deploy.textContent = "Publish to " + environment;
        row.append(deploy);
      }
      return;
    }
    row.append(details);
    const move = (offset) => {
      repo.stages.splice(index, 1);
      repo.stages.splice(index + offset, 0, stage);
      save();
    };
    const actions = document.createElement("div");
    actions.className = "stage-actions";
    actions.append(
      button("Up", () => move(-1), index === 0),
      button("Down", () => move(1), index === repo.stages.length - 1),
      button("Edit", () => editStage(repo.stages, index)),
      button("Remove", () => {
        if (window.confirm(`Remove "${stage.name}"?`)) {
          repo.stages.splice(index, 1);
          save();
        }
      }),
    );
    row.append(actions);
  });
}

byId("add-project").onclick = () => edit("New project", [
  ["name", "Project name", "", "text", true],
], ({ name }) => {
  projectIndex = projects.length;
  repoIndex = 0;
  projects.push({ name, repos: [] });
});
byId("toggle-edit").onclick = () => { editing = !editing; render(); };
byId("cancel").onclick = () => dialog.close();
render();

function showDeployments(repositories) {
  const rows = byId("release-list");
  const picker = byId("release-repo");
  const selected = picker.value;
  picker.replaceChildren(...repositories.map(repo => new Option(repo.name, repo.name)));
  if (repositories.some(repo => repo.name === selected)) picker.value = selected;
  byId("publish-repository").hidden = !repositories.length;

  const releases = repositories.flatMap(repo => (repo.deployments || []).map(release => ({...release, name:repo.name})));
  releases.sort((a,b) => b.created.localeCompare(a.created) || a.name.localeCompare(b.name));
  rows.replaceChildren();
  for (const release of releases) {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "release-environment";
    label.textContent = release.environment;
    const info = document.createElement("div");
    const repo = document.createElement("a");
    repo.href = "/hosted?" + new URLSearchParams({repo:release.name,view:"deploys",environment:release.environment});
    repo.textContent = release.name;
    const meta = document.createElement("small");
    const kind = release.site ? "Static site" : release.available ? "ZIP artifact" : "Artifact unavailable";
    meta.textContent = kind + " · " + release.commit.slice(0,8) + " · " + release.created + " UTC";
    info.append(repo, meta);
    item.append(label, info);
    const actions = document.createElement("div");
    actions.className = "release-actions";
    const environmentUrl = release.site && "/environments/" + release.name + "/" + release.environment + "/";
    for (const [title, href] of [["Open site", environmentUrl], ["Download ZIP", release.archive]]) {
      if (!href) continue;
      const link = document.createElement("a");
      link.href = href;
      link.textContent = title;
      link.setAttribute("aria-label", title + " for " + release.name + " " + release.environment);
      actions.append(link);
    }
    item.append(actions);
    rows.append(item);
  }
  byId("release-status").textContent = releases.length
    ? "Latest published revision in each local environment."
    : "No local deployments yet. Publish a hosted repository to see it here.";
}
byId("publish-repository").onsubmit = event => {
  event.preventDefault();
  location.assign("/hosted?" + new URLSearchParams({repo:byId("release-repo").value,view:"deploys"}));
};
byId("refresh-deployments").onclick = () => loadDirectory();
let directoryRequest = 0;
async function loadDirectory() {
  const requestId = ++directoryRequest;
  const section = byId("host-directory");
  try {
    const [response, identity] = await Promise.all([
      fetch("/repositories"),
      fetch("/identity").then(response => response.ok ? response.json() : null).catch(() => null),
    ]);
    if (!response.ok) throw new Error("Repository list unavailable");
    const repositories = await response.json();
    if (requestId !== directoryRequest) return;
    showDeployments(repositories);
    byId("review-repo").replaceChildren(...repositories.map(({name}) => new Option(name, name)));
    const search = byId("directory-search");
    const personal = byId("personal-filter");
    const person = identity?.person || "";
    const preferenceKey = "freethegit_inbox_" + location.port + "_" + person;
    let preference = "all";
    if (person) {
      const cookie = document.cookie.split("; ").find(value => value.startsWith(preferenceKey + "="));
      if (cookie) preference = cookie.slice(preferenceKey.length + 1);
      else {
        try { preference = localStorage.getItem(preferenceKey) || "all"; }
        catch { /* Storage can be disabled in a private window. */ }
      }
    }
    personal.value = Array.from(personal.options).some(option => option.value === preference) ? preference : "all";
    function draw() {
      const showReviews = currentPage() === "reviews";
      byId("start-review").hidden = !showReviews || !repositories.length;
      byId("personal-view").hidden = !person || !showReviews;
      const query = search.value.trim().toLowerCase();
      const list = byId("directory-items");
      list.replaceChildren();
      function add(name, detail, params, pipeline = false) {
        if (!(name + " " + detail).toLowerCase().includes(query)) return;
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = "/hosted?" + new URLSearchParams(params);
        const title = document.createElement("strong");
        title.textContent = name;
        const secondary = document.createElement("small");
        secondary.textContent = detail;
        link.append(title, secondary);
        item.append(link);
        if (pipeline) {
          item.className = "directory-repo";
          const configure = button("Configure pipeline", () => configureHostedPipeline(name));
          configure.setAttribute("aria-label", "Configure pipeline for " + name);
          item.append(configure);
        }
        list.append(item);
      }
      for (const repo of repositories) {
        if (!showReviews || repo.unavailable) {
          const n = repo.reviews.length;
          add(repo.name, repo.unavailable ? "Review status unavailable" : n + (n === 1 ? " open review" : " open reviews"), {repo:repo.name}, !showReviews);
        } else {
          const filter = personal.value;
          const records = filter === "authored" || filter === "reviewed" ? [...repo.reviews, ...(repo.history || [])] : repo.reviews;
          for (const pr of records) {
            if (filter !== "all" && !pr[filter]) continue;
            const state = pr.superseded ? " · Earlier snapshot" : pr.state !== "open" ? " · " + pr.state : "";
            add(pr.title, repo.name + " #" + pr.id + " · " + pr.source + " → " + pr.target + state, {repo:repo.name,pr:pr.id});
          }
        }
      }
      byId("review-empty-actions").hidden = !showReviews || !!query || !!list.childElementCount;
      if (!list.childElementCount) {
        const empty = document.createElement("li");
        empty.className = "directory-empty";
        empty.textContent = query ? "No matches. Try another title or repository." : personal.value === "needsReview" ? "No reviews are waiting for you." : !showReviews ? "No repositories yet. Create your first repository above." : "No pull requests in this view.";
        list.append(empty);
      }
    }
    drawDirectory = draw;
    personal.onchange = () => {
      if (person) {
        document.cookie = preferenceKey + "=" + personal.value + "; Path=/; SameSite=Strict; Max-Age=31536000";
        try { localStorage.setItem(preferenceKey, personal.value); }
        catch { /* The browser cookie preserves the preference. */ }
      }
      draw();
    };
    search.oninput = draw;
    draw();
  } catch {
    if (requestId !== directoryRequest) return;
    byId("release-status").textContent = "Could not refresh deployment status.";
    if (!drawDirectory) byId("directory-items").replaceChildren();
    byId("notice").textContent = "Could not load hosted repositories. Reload to retry.";
  }
}
let drawDirectory;
function currentPage() {
  const route = location.hash.slice(1);
  return ["repositories", "local-deployments", "pipelines"].includes(route) ? route : "reviews";
}
function showPage() {
  const page = currentPage();
  const reviews = page === "reviews";
  byId("host-directory").hidden = !reviews && page !== "repositories";
  byId("local-deployments").hidden = page !== "local-deployments";
  byId("pipeline-projects").hidden = byId("pipeline-page").hidden = page !== "pipelines";
  byId("workspace-layout").classList.toggle("pipeline-open", page === "pipelines");
  byId("local-identity").hidden = !reviews;
  byId("try-review").hidden = !reviews;
  byId("create-hosted").hidden = reviews;
  byId("directory-title").textContent = reviews ? "Pull requests" : "Repositories";
  const label = reviews ? "Find a pull request" : "Find a repository";
  const search = byId("directory-search");
  search.placeholder = label;
  search.setAttribute("aria-label", label);
  search.value = "";
  document.querySelectorAll("[data-page]").forEach(link => {
    if (link.dataset.page === page) {
      link.setAttribute("aria-current", "page");
      document.title = link.textContent + " · FreeTheGit";
    } else link.removeAttribute("aria-current");
  });
  drawDirectory?.();
}
byId("start-review").onclick = () => {
  const picker = byId("review-repo");
  if (picker.options.length === 1) picker.form.requestSubmit();
  else byId("choose-repo").showModal();
};
byId("cancel-review").onclick = () => byId("choose-repo").close();
byId("create-hosted").onclick = () => edit("New repository", [
  ["project", "Owner or project", "", "text", true],
  ["name", "Repository name", "", "text", true],
], async ({project, name}) => {
  const response = await fetch("/repositories", {method:"POST", body:new URLSearchParams({project, name})});
  if (!response.ok) throw Error(response.status === 409 ? "That repository already exists." : "Use letters, numbers, hyphens or underscores for both names.");
  const {url} = await response.json();
  const slug = new URL(url).pathname.slice(5, -4);
  location.assign("/hosted?" + new URLSearchParams({repo:slug}));
}, "Create repository");
window.addEventListener("hashchange", showPage);
showPage();
window.addEventListener("pageshow", loadDirectory);
