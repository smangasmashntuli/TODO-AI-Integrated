"use strict";

/* ---------------------------------------------------------------------------
 * TODO AI frontend.
 * All AI calls go through the backend (no keys in the browser, Rule 15).
 * AI output is always shown as a suggestion the user must review and apply
 * (Rule 7 / Rule 16).
 * ------------------------------------------------------------------------- */

// If opened via PyCharm/LiveServer/file preview, target backend at http://127.0.0.1:8000;
// if served directly by FastAPI (e.g. at http://127.0.0.1:8000/ui/), use the same origin.
const API_BASE = (location.protocol.startsWith("http") && (location.port === "8000" || !location.port))
  ? location.origin
  : "http://127.0.0.1:8000";

const $ = (selector) => document.querySelector(selector);

/** Tiny DOM builder: h("div", { class: "x", onclick: fn }, child, ...). */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
    else if (key === "checked" || key === "disabled") node[key] = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorMessage(payload, status) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || "invalid input").join("; ");
  return `Request failed (${status})`;
}

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(API_BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorMessage(payload, response.status));
  return payload;
}

let toastTimer = null;
function toast(message, kind = "") {
  const box = $("#toast");
  box.textContent = message;
  box.className = "toast" + (kind ? ` toast--${kind}` : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), 6000);
}

function reportError(error) {
  if (error.status === 503) {
    toast("AI is not configured. Set GEMINI_API_KEY on the server to enable AI features.", "warn");
  } else if (error.status === 502) {
    toast(error.message || "The AI provider failed. Please try again.", "error");
  } else {
    toast(error.message || "Something went wrong.", "error");
  }
}

/** Disable a button while an async task runs; report failures once. */
async function withBusy(button, task) {
  const label = button ? button.textContent : null;
  if (button) {
    button.disabled = true;
    button.textContent = "Working…";
  }
  try {
    return await task();
  } catch (error) {
    reportError(error);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = label;
    }
  }
}

const taskApi = {
  list: () => api("/todos/"),
  create: (payload) => api("/todos/", { method: "POST", body: payload }),
  update: (id, payload) => api(`/todos/${id}`, { method: "PUT", body: payload }),
  remove: (id) => api(`/todos/${id}`, { method: "DELETE" }),
};

const aiApi = {
  parse: (text) => api("/api/tasks/parse", { method: "POST", body: { text } }),
  categorize: (taskId) => api("/api/tasks/categorize", { method: "POST", body: { task_id: taskId } }),
  prioritize: (body) => api("/api/tasks/prioritize", { method: "POST", body }),
  breakdown: (taskId) => api(`/api/tasks/${taskId}/breakdown`, { method: "POST" }),
  nextAction: (taskId) => api(`/api/tasks/${taskId}/next-action`, { method: "POST" }),
  reminders: () => api("/api/tasks/reminders"),
};

const state = {
  todos: [],
  filter: "open",
  selected: new Set(),
  panels: new Map(), // taskId -> { type: "categorize" | "breakdown" | "next-action", data }
  parse: null,
  prioritize: null,
};

async function boot() {
  bindEvents();
  await checkApi();
  await loadTasks(); // also refreshes reminders
}

async function checkApi() {
  const badge = $("#api-status");
  try {
    await api("/");
    badge.textContent = "API connected";
    badge.className = "status status--ok";
  } catch {
    badge.textContent = "API unreachable";
    badge.className = "status status--err";
  }
}

async function loadTasks() {
  try {
    state.todos = await taskApi.list();
    pruneSelection();
    renderTasks();
    await loadReminders(); // reminders depend on task state
  } catch (error) {
    reportError(error);
  }
}

async function loadReminders(button) {
  await withBusy(button, async () => {
    const reminders = await aiApi.reminders();
    const list = $("#reminder-list");
    list.replaceChildren(...reminders.map(reminderItem));
    $("#reminders-empty").classList.toggle("hidden", reminders.length > 0);
  });
}

function reminderItem(reminder) {
  return h("li", { class: `reminder reminder--${reminder.level}` },
    h("span", { class: "chip", text: reminder.level }),
    h("span", { class: "field-name", text: reminder.title }),
    h("div", { class: "field-why", text: reminder.message }),
  );
}

function pruneSelection() {
  const ids = new Set(state.todos.map((todo) => todo.id));
  for (const id of [...state.selected]) {
    if (!ids.has(id)) state.selected.delete(id);
  }
}

/* ---------------------------------------------------------------- rendering */

function visibleTodos() {
  if (state.filter === "open") return state.todos.filter((todo) => !todo.completed);
  if (state.filter === "done") return state.todos.filter((todo) => todo.completed);
  return state.todos;
}

function renderTasks() {
  const list = $("#task-list");
  list.replaceChildren(...visibleTodos().map(taskCard));
  $("#tasks-empty").classList.toggle("hidden", list.children.length > 0);
  $("#selection-hint").textContent = state.selected.size
    ? `${state.selected.size} task(s) selected — prioritization will use only these.`
    : "No tasks selected — prioritization will use all open tasks.";
  renderPrioritize();
}

function taskCard(todo) {
  const panel = state.panels.get(todo.id);
  return h("li", { class: `task${todo.completed ? " task--done" : ""}` },
    h("div", { class: "task-head" },
      h("input", {
        type: "checkbox",
        checked: state.selected.has(todo.id),
        title: "Include in prioritization",
        onchange: (event) => toggleSelection(todo.id, event.target.checked),
      }),
      h("input", {
        type: "checkbox",
        checked: todo.completed,
        title: "Mark complete",
        onchange: () => toggleComplete(todo),
      }),
      h("h3", { class: "task-title", text: todo.title }),
      h("div", { class: "task-actions" },
        h("button", { onclick: (event) => runCategorize(todo.id, event.target) }, "Categorize"),
        h("button", { onclick: (event) => runBreakdown(todo.id, event.target) }, "Break down"),
        h("button", { onclick: (event) => runNextAction(todo.id, event.target) }, "Next step"),
        h("button", { onclick: () => openEditor(todo) }, "Edit"),
        h("button", { class: "danger", onclick: () => removeTask(todo) }, "Delete"),
      ),
    ),
    todo.description ? h("p", { class: "task-desc", text: todo.description }) : null,
    chips(todo),
    subtaskList(todo),
    todo.context ? h("p", { class: "task-context", text: `Context: ${todo.context}` }) : null,
    panel ? panelView(todo, panel) : null,
  );
}

function chips(todo) {
  const items = [];
  const add = (text, cls = "") => items.push(h("span", { class: `chip ${cls}`, text }));

  if (todo.due_date) add(`due ${formatDate(todo.due_date)}`);
  if (todo.urgency) add(`urgency: ${todo.urgency}`, `chip--urgency-${todo.urgency}`);
  if (todo.category) add(todo.category);
  if (todo.project) add(`project: ${todo.project}`);
  if (todo.area_of_focus) add(`area: ${todo.area_of_focus}`);
  if (todo.effort_hours !== null && todo.effort_hours !== undefined) add(`${todo.effort_hours}h effort`);
  if (todo.assignees && todo.assignees.length) add(`with ${todo.assignees.join(", ")}`);
  if (todo.skills_required && todo.skills_required.length) add(`skills: ${todo.skills_required.join(", ")}`);
  if (todo.priority_score !== null && todo.priority_score !== undefined) add(`P ${fmt(todo.priority_score)}`, "chip--meta");
  if (todo.impact_score !== null && todo.impact_score !== undefined) add(`I ${fmt(todo.impact_score)}`, "chip--meta");
  if (todo.feasibility_score !== null && todo.feasibility_score !== undefined) add(`F ${fmt(todo.feasibility_score)}`, "chip--meta");
  if (todo.confidence_level !== null && todo.confidence_level !== undefined) add(`conf ${fmt(todo.confidence_level)}`, "chip--meta");
  if (todo.created_from === "natural_language") add("AI input", "chip--meta");

  return items.length ? h("div", { class: "chips" }, ...items) : null;
}

function subtaskList(todo) {
  if (!todo.subtasks || !todo.subtasks.length) return null;
  return h("ul", { class: "subtasks" }, ...todo.subtasks.map((step) => h("li", { text: step })));
}

function panelView(todo, panel) {
  if (panel.type === "categorize") return categorizeView(todo, panel.data);
  if (panel.type === "next-action") return nextActionView(todo, panel.data);
  return breakdownView(todo, panel.data);
}

/* ------------------------------------------------------- AI suggestion views */

const CATEGORY_FIELDS = ["category", "project", "area_of_focus", "urgency", "skills_required"];

function suggestRow(boxId, name, value, why) {
  return h("label", { class: "suggest" },
    h("input", { type: "checkbox", id: boxId, checked: true }),
    h("span", {},
      h("span", { class: "field-name", text: `${name}: ${value}` }),
      why ? h("div", { class: "field-why", text: why }) : null,
    ),
  );
}

function categorizeView(todo, data) {
  const rows = CATEGORY_FIELDS
    .filter((field) => present(data.suggested[field]))
    .map((field) => suggestRow(
      `cat-${todo.id}-${field}`,
      humanLabel(field),
      formatValue(field, data.suggested[field]),
      data.reasons ? data.reasons[field] : null,
    ));

  return h("div", { class: "result" },
    h("h3", { text: "AI categorization — suggestion" }),
    h("p", { class: "result-note", text: "Tick what you accept, then apply. Nothing is saved until you apply." }),
    ...(rows.length ? rows : [h("p", { class: "result-note", text: "The AI had no confident suggestions." })]),
    h("div", { class: "row row--end" },
      h("button", { onclick: () => dismissPanel(todo.id) }, "Dismiss"),
      h("button", { class: "primary", onclick: () => applyCategorization(todo, data) }, "Apply selected"),
    ),
  );
}

function breakdownView(todo, data) {
  const steps = data.subtasks.map((step) => h("label", { class: "suggest" },
    h("input", { type: "checkbox", class: "step-box", "data-step": step, checked: true }),
    h("span", { text: step }),
  ));

  return h("div", { class: "result", id: `panel-${todo.id}` },
    h("h3", { text: "AI breakdown — suggestion" }),
    h("p", { class: "result-note", text: "Untick steps you don't want, then apply." }),
    ...steps,
    h("div", { class: "row" },
      h("label", { class: "field field--inline" },
        h("span", { text: "Mode" }),
        h("select", { id: `step-mode-${todo.id}` },
          h("option", { value: "replace", text: "Replace subtasks" }),
          h("option", { value: "append", text: "Add to existing" }),
        ),
      ),
    ),
    h("div", { class: "row row--end" },
      h("button", { onclick: () => dismissPanel(todo.id) }, "Dismiss"),
      h("button", { class: "primary", onclick: () => applyBreakdown(todo) }, "Apply steps"),
    ),
  );
}

function nextActionView(todo, data) {
  const actions = data.actions.map((action) => h("label", { class: "suggest" },
    h("input", { type: "radio", name: `next-${todo.id}`, value: action, checked: true }),
    h("span", { text: action }),
  ));

  return h("div", { class: "result", id: `panel-${todo.id}` },
    h("h3", { text: "AI next action — suggestion" }),
    h("p", { class: "result-note", text: `confidence: ${fmt(data.confidence_level)} · pick one to use` }),
    ...actions,
    h("div", { class: "row row--end" },
      h("button", { onclick: () => dismissPanel(todo.id) }, "Dismiss"),
      h("button", { class: "primary", onclick: () => applyNextAction(todo) }, "Use as next subtask"),
    ),
  );
}

function renderPrioritize() {
  const box = $("#prioritize-result");
  const data = state.prioritize;
  if (!data) {
    box.classList.add("hidden");
    box.replaceChildren();
    return;
  }
  box.classList.remove("hidden");

  const workload = data.workload;
  const workloadClass = workload.fits === true
    ? "workload--ok"
    : workload.fits === false ? "workload--bad" : "workload--unknown";

  const ranks = data.items.map((item, index) => {
    const todo = state.todos.find((entry) => entry.id === item.task_id);
    return h("div", { class: "rank" },
      h("span", { text: `#${index + 1}` }),
      h("span", {},
        h("span", { class: "field-name", text: todo ? todo.title : `task ${item.task_id}` }),
        item.reason ? h("div", { class: "field-why", text: item.reason }) : null,
      ),
      h("span", { class: "rank-score", text: scoreText(item) }),
    );
  });

  box.replaceChildren(
    h("h3", { text: "Ranking — suggestion" }),
    h("p", { class: `workload ${workloadClass}`, text: workload.message }),
    ...(ranks.length ? ranks : [h("p", { class: "result-note", text: "No tasks to rank." })]),
    data.suggestions && data.suggestions.length
      ? h("div", {},
          h("p", { class: "field-name", text: "Suggested actions" }),
          h("ul", {}, ...data.suggestions.map((suggestion) => h("li", { text: suggestion }))),
        )
      : null,
    h("p", { class: "result-note", text: `confidence: ${fmt(data.confidence_level)}` }),
    h("div", { class: "row row--end" },
      h("button", { onclick: () => { state.prioritize = null; renderPrioritize(); } }, "Dismiss"),
      h("button", { class: "primary", onclick: () => applyScores(data) }, "Apply scores to tasks"),
    ),
  );
}

function scoreText(item) {
  const parts = [`P ${fmt(item.priority_score)}`];
  if (item.impact_score !== null && item.impact_score !== undefined) parts.push(`I ${fmt(item.impact_score)}`);
  if (item.feasibility_score !== null && item.feasibility_score !== undefined) parts.push(`F ${fmt(item.feasibility_score)}`);
  return parts.join(" · ");
}

function renderParseResult() {
  const box = $("#parse-result");
  const data = state.parse;
  if (!data) {
    box.classList.add("hidden");
    box.replaceChildren();
    return;
  }
  box.classList.remove("hidden");

  box.replaceChildren(
    h("h3", { text: "Parsed suggestion — review before saving" }),
    h("p", { class: "result-note", text: `confidence: ${fmt(data.confidence_level)}` }),
    data.clarification && data.clarification.required
      ? h("p", {
          class: "workload--unknown",
          text: `Clarification needed: ${data.clarification.question || "more detail required"}`,
        })
      : null,
    data.clarification && data.clarification.missing_fields && data.clarification.missing_fields.length
      ? h("p", { class: "result-note", text: `Missing: ${data.clarification.missing_fields.join(", ")}` })
      : null,
    h("p", { class: "field-name", text: "Extracted" }),
    fieldList(data.extracted),
    h("p", { class: "field-name", text: "Inferred by AI" }),
    fieldList(data.inferred),
    h("div", { class: "row row--end" },
      h("button", { onclick: clearParse }, "Dismiss"),
      h("button", { class: "primary", onclick: () => openEditor(null, parsePrefill(data)) }, "Review & add task"),
    ),
  );
}

function fieldList(source) {
  const rows = [];
  for (const [key, value] of Object.entries(source || {})) {
    const text = formatValue(key, value);
    if (text) rows.push(h("li", { text: `${humanLabel(key)}: ${text}` }));
  }
  return rows.length ? h("ul", {}, ...rows) : h("p", { class: "result-note", text: "none detected" });
}

/* ------------------------------------------------------------------ actions */

async function runParse(button) {
  const text = $("#nl-input").value.trim();
  if (!text) {
    toast("Enter a task description first.", "warn");
    return;
  }
  await withBusy(button, async () => {
    state.parse = await aiApi.parse(text);
    renderParseResult();
  });
}

function clearParse() {
  state.parse = null;
  $("#nl-input").value = "";
  renderParseResult();
}

function openEditor(todo, prefill) {
  const source = todo || prefill || {};
  const editing = Boolean(todo);
  $("#editor-title").textContent = editing ? "Edit task" : "New task";
  $("#task-form").dataset.id = editing ? String(todo.id) : "";
  setValue("#f-title", source.title || "");
  setValue("#f-description", source.description);
  $("#f-due-date").value = toLocalInput(source.due_date);
  setValue("#f-effort", source.effort_hours);
  setValue("#f-urgency", source.urgency || "");
  setValue("#f-category", source.category);
  setValue("#f-project", source.project);
  setValue("#f-area", source.area_of_focus);
  setValue("#f-priority", source.priority_score);
  setValue("#f-impact", source.impact_score);
  setValue("#f-feasibility", source.feasibility_score);
  setValue("#f-confidence", source.confidence_level);
  $("#f-assignees").value = (source.assignees || []).join(", ");
  $("#f-skills").value = (source.skills_required || []).join(", ");
  $("#f-subtasks").value = (source.subtasks || []).join("\n");
  setValue("#f-context", source.context);
  $("#f-completed").checked = Boolean(source.completed);
  setValue("#f-created-from", source.created_from || "ui");
  $("#editor-dialog").showModal();
}

async function saveTask(event) {
  event.preventDefault();
  const payload = formPayload();
  if (!payload.title) {
    toast("A title is required.", "warn");
    return;
  }
  const id = $("#task-form").dataset.id;
  await withBusy(null, async () => {
    if (id) await taskApi.update(Number(id), payload);
    else await taskApi.create(payload);
    $("#editor-dialog").close();
    clearParse();
    await loadTasks();
    toast(id ? "Task updated." : "Task created.", "ok");
  });
}

function formPayload() {
  return {
    title: $("#f-title").value.trim(),
    description: textOrNull("#f-description"),
    completed: $("#f-completed").checked,
    due_date: textOrNull("#f-due-date"),
    effort_hours: numberOrNull("#f-effort"),
    urgency: textOrNull("#f-urgency"),
    category: textOrNull("#f-category"),
    project: textOrNull("#f-project"),
    area_of_focus: textOrNull("#f-area"),
    priority_score: numberOrNull("#f-priority"),
    impact_score: numberOrNull("#f-impact"),
    feasibility_score: numberOrNull("#f-feasibility"),
    confidence_level: numberOrNull("#f-confidence"),
    assignees: commaList("#f-assignees"),
    skills_required: commaList("#f-skills"),
    subtasks: lineList("#f-subtasks"),
    context: textOrNull("#f-context"),
    created_from: $("#f-created-from").value,
  };
}

function toggleSelection(id, checked) {
  if (checked) state.selected.add(id);
  else state.selected.delete(id);
  renderTasks();
}

async function toggleComplete(todo) {
  await withBusy(null, async () => {
    await taskApi.update(todo.id, { completed: !todo.completed });
    await loadTasks();
  });
}

async function removeTask(todo) {
  if (!window.confirm(`Delete "${todo.title}"?`)) return;
  await withBusy(null, async () => {
    await taskApi.remove(todo.id);
    state.panels.delete(todo.id);
    state.selected.delete(todo.id);
    await loadTasks();
    toast("Task deleted.", "ok");
  });
}

function dismissPanel(id) {
  state.panels.delete(id);
  renderTasks();
}

async function runCategorize(taskId, button) {
  await withBusy(button, async () => {
    const data = await aiApi.categorize(taskId);
    state.panels.set(taskId, { type: "categorize", data });
    renderTasks();
  });
}

async function applyCategorization(todo, data) {
  const payload = {};
  for (const field of CATEGORY_FIELDS) {
    const box = $(`#cat-${todo.id}-${field}`);
    if (box && box.checked) payload[field] = data.suggested[field];
  }
  if (!Object.keys(payload).length) {
    toast("Nothing selected.", "warn");
    return;
  }
  await withBusy(null, async () => {
    await taskApi.update(todo.id, payload);
    dismissPanel(todo.id);
    await loadTasks();
    toast("Categorization applied.", "ok");
  });
}

async function runBreakdown(taskId, button) {
  await withBusy(button, async () => {
    const data = await aiApi.breakdown(taskId);
    state.panels.set(taskId, { type: "breakdown", data });
    renderTasks();
  });
}

async function runNextAction(taskId, button) {
  await withBusy(button, async () => {
    const data = await aiApi.nextAction(taskId);
    state.panels.set(taskId, { type: "next-action", data });
    renderTasks();
  });
}

async function applyNextAction(todo) {
  const panel = document.getElementById(`panel-${todo.id}`);
  const chosen = panel.querySelector(`input[name="next-${todo.id}"]:checked`);
  if (!chosen) return;
  await withBusy(null, async () => {
    await taskApi.update(todo.id, { subtasks: [...(todo.subtasks || []), chosen.value] });
    dismissPanel(todo.id);
    await loadTasks();
    toast("Next action added to subtasks.", "ok");
  });
}

async function applyBreakdown(todo) {
  const panel = document.getElementById(`panel-${todo.id}`);
  const selected = [...panel.querySelectorAll(".step-box")].filter((box) => box.checked);
  if (!selected.length) {
    toast("No steps selected.", "warn");
    return;
  }
  const mode = document.getElementById(`step-mode-${todo.id}`).value;
  const steps = selected.map((box) => box.dataset.step);
  const subtasks = mode === "append" ? [...(todo.subtasks || []), ...steps] : steps;
  await withBusy(null, async () => {
    await taskApi.update(todo.id, { subtasks });
    dismissPanel(todo.id);
    await loadTasks();
    toast("Subtasks applied.", "ok");
  });
}

async function runPrioritize(button) {
  const body = {};
  const hours = $("#available-hours").value.trim();
  if (hours !== "") body.available_hours = Number(hours);
  if (state.selected.size) body.task_ids = [...state.selected];
  await withBusy(button, async () => {
    state.prioritize = await aiApi.prioritize(body);
    renderPrioritize();
  });
}

async function applyScores(data) {
  await withBusy(null, async () => {
    for (const item of data.items) {
      await taskApi.update(item.task_id, {
        priority_score: item.priority_score,
        impact_score: item.impact_score,
        feasibility_score: item.feasibility_score,
      });
    }
    state.prioritize = null;
    await loadTasks();
    toast(`Scores applied to ${data.items.length} task(s).`, "ok");
  });
}

/* ---------------------------------------------------------------- utilities */

function present(value) {
  if (value === null || value === undefined || value === "") return false;
  return !Array.isArray(value) || value.length > 0;
}

function fmt(value) {
  return value === null || value === undefined ? "—" : Number(value).toFixed(2);
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function humanLabel(key) {
  return key.replace(/_/g, " ");
}

function formatValue(key, value) {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (key.includes("date")) return formatDate(value);
  if (typeof value === "number") return fmt(value);
  return String(value);
}

function textOrNull(selector) {
  const value = $(selector).value.trim();
  return value === "" ? null : value;
}

function numberOrNull(selector) {
  const value = $(selector).value.trim();
  return value === "" ? null : Number(value);
}

function commaList(selector) {
  return $(selector).value.split(",").map((item) => item.trim()).filter(Boolean);
}

function lineList(selector) {
  return $(selector).value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function setValue(selector, value) {
  $(selector).value = value === null || value === undefined ? "" : value;
}

function toLocalInput(value) {
  return value ? String(value).slice(0, 16) : "";
}

function parsePrefill(data) {
  const extracted = data.extracted || {};
  const inferred = data.inferred || {};
  return {
    title: extracted.title || "",
    due_date: extracted.due_date,
    assignees: extracted.assignees,
    subtasks: extracted.subtasks,
    effort_hours: extracted.effort_hours,
    context: extracted.context,
    category: inferred.category,
    priority_score: inferred.priority_score,
    created_from: "natural_language",
  };
}

function bindEvents() {
  $("#parse-btn").addEventListener("click", (event) => runParse(event.target));
  $("#prioritize-btn").addEventListener("click", (event) => runPrioritize(event.target));
  $("#reminders-btn").addEventListener("click", (event) => loadReminders(event.target));
  $("#new-task-btn").addEventListener("click", () => openEditor(null, null));
  $("#editor-cancel").addEventListener("click", () => $("#editor-dialog").close());
  $("#task-form").addEventListener("submit", saveTask);
  $("#filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    renderTasks();
  });
}

boot();


