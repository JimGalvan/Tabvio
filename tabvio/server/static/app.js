const SCREEN_REFRESH_INTERVAL_MS = 750;
const PENDING_TASK_KEY = "tabvio.pending-task";

const terminalRunStatuses = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
]);

const taskForm = document.querySelector("#task-form");
const taskInput = document.querySelector("#task-input");
const startButton = document.querySelector("#start-button");
const formMessage = document.querySelector("#form-message");
const briefSection = document.querySelector("#brief-section");
const workspace = document.querySelector("#workspace");
const runState = document.querySelector("#run-state");
const statusDot = document.querySelector("#status-dot");
const statusLabel = document.querySelector("#status-label");
const runIdLabel = document.querySelector("#run-id");
const cancelButton = document.querySelector("#cancel-button");
const browserScreen = document.querySelector("#browser-screen");
const browserWaiting = document.querySelector("#browser-waiting");
const browserWaitingMessage = browserWaiting.querySelector("p");
const latestAction = document.querySelector("#latest-action");
const activityList = document.querySelector("#activity-list");
const activityEmpty = document.querySelector("#activity-empty");
const eventCount = document.querySelector("#event-count");
const answerPanel = document.querySelector("#answer-panel");
const answerQuestion = document.querySelector("#answer-question");
const answerForm = document.querySelector("#answer-form");
const answerInput = document.querySelector("#answer-input");
const resultPanel = document.querySelector("#result-panel");
const resultOutput = document.querySelector("#result-output");
const followUpPanel = document.querySelector("#follow-up-panel");
const followUpDeadline = document.querySelector("#follow-up-deadline");
const followUpForm = document.querySelector("#follow-up-form");
const followUpInput = document.querySelector("#follow-up-input");
const followUpMessage = document.querySelector("#follow-up-message");
const endSessionButton = document.querySelector("#end-session-button");
const rerunButton = document.querySelector("#rerun-button");
const terminalMessage = document.querySelector("#terminal-message");
const accountEmail = document.querySelector("#account-email");
const historyList = document.querySelector("#history-list");
const historyEmpty = document.querySelector("#history-empty");
const historyRefresh = document.querySelector("#history-refresh");
const credentialPicker = document.querySelector("#credential-picker");
const credentialManageButton = document.querySelector("#credential-manage-button");
const credentialManager = document.querySelector("#credential-manager");
const credentialManagerClose = document.querySelector("#credential-manager-close");
const credentialForm = document.querySelector("#credential-form");
const credentialName = document.querySelector("#credential-name");
const credentialLogin = document.querySelector("#credential-login");
const credentialPassword = document.querySelector("#credential-password");
const credentialDomains = document.querySelector("#credential-domains");
const credentialDefault = document.querySelector("#credential-default");
const credentialMessage = document.querySelector("#credential-message");
const credentialSaveButton = document.querySelector("#credential-save-button");
const credentialEditCancel = document.querySelector("#credential-edit-cancel");
const credentialList = document.querySelector("#credential-list");
const secureInputPanel = document.querySelector("#secure-input-panel");
const secureInputQuestion = document.querySelector("#secure-input-question");
const secureInputForm = document.querySelector("#secure-input-form");
const secureInputCode = document.querySelector("#secure-input-code");
const secureInputMessage = document.querySelector("#secure-input-message");

const eventTypes = [
  "run.created",
  "run.status",
  "browser.navigation.started",
  "browser.navigation.completed",
  "browser.observation",
  "browser.action.started",
  "browser.action.completed",
  "browser.action.failed",
  "browser.tab.changed",
  "browser.capture.failed",
  "browser.capture.recovered",
  "agent.message.delta",
  "input.required",
  "input.received",
  "sensitive_input.required",
  "sensitive_input.received",
  "follow_up.started",
  "follow_up.ended",
  "follow_up.expired",
  "run.completed",
  "run.failed",
  "run.cancelled",
];

const terminalStatuses = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
]);

const screenPausedStatuses = new Set([
  ...terminalStatuses,
  "ready_for_follow_up",
]);

let activeRunId = null;
let activeScreenUrl = null;
let currentScreenObjectUrl = null;
let eventSource = null;
let screenRefreshTimer = null;
let screenRequestInFlight = false;
let displayedEventCount = 0;
let streamedMessage = "";
let credentials = [];
let editingCredentialId = null;
let activeSensitiveRequestId = null;

const exampleChips = document.querySelectorAll(".example-chip");

exampleChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    taskInput.value = chip.dataset.example || "";
    taskInput.focus();
    taskInput.setSelectionRange(taskInput.value.length, taskInput.value.length);
  });
});

taskForm.addEventListener("submit", async (formEvent) => {
  formEvent.preventDefault();
  const task = taskInput.value.trim();
  if (!task) {
    return;
  }

  setFormBusy(true);
  formMessage.textContent = "";

  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        task,
        credential_ids: selectedCredentialIds(),
      }),
    });
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The run could not be started");
    }

    openRun(responseBody);
    void loadRunHistory();
  } catch (error) {
    formMessage.textContent = error.message;
    setFormBusy(false);
  }
});

credentialManageButton.addEventListener("click", () => {
  credentialManager.hidden = false;
  credentialName.focus();
});

credentialManagerClose.addEventListener("click", () => {
  credentialManager.hidden = true;
  resetCredentialForm();
});

credentialEditCancel.addEventListener("click", resetCredentialForm);

credentialForm.addEventListener("submit", async (formEvent) => {
  formEvent.preventDefault();
  const domains = credentialDomains.value
    .split(",")
    .map((domain) => domain.trim())
    .filter(Boolean);
  const body = {
    name: credentialName.value.trim(),
    allowed_domains: domains,
    is_default: credentialDefault.checked,
  };
  if (credentialLogin.value.trim()) {
    body.login = credentialLogin.value.trim();
  }
  if (credentialPassword.value) {
    body.password = credentialPassword.value;
  }
  if (!editingCredentialId && (!body.login || !body.password)) {
    credentialMessage.textContent = "Login and password are required for a new credential.";
    return;
  }

  credentialSaveButton.disabled = true;
  credentialMessage.textContent = "";
  try {
    const response = await fetch(
      editingCredentialId
        ? `/api/credentials/${editingCredentialId}`
        : "/api/credentials",
      {
        method: editingCredentialId ? "PATCH" : "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      },
    );
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The credential could not be saved");
    }
    resetCredentialForm();
    await loadCredentials(responseBody.id);
  } catch (error) {
    credentialMessage.textContent = error.message;
  } finally {
    credentialSaveButton.disabled = false;
  }
});

function selectedCredentialIds() {
  return [...credentialPicker.querySelectorAll("input:checked")]
    .map((input) => input.value);
}

async function loadCredentials(selectCredentialId = null, applyDefaults = false) {
  const selectedIds = new Set(selectedCredentialIds());
  if (selectCredentialId) {
    selectedIds.add(selectCredentialId);
  }
  try {
    const response = await fetch("/api/credentials");
    const body = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(body.detail || "Credentials could not be loaded");
    }
    credentials = body.credentials || [];
    if (applyDefaults) {
      for (const credential of credentials) {
        if (credential.is_default) {
          selectedIds.add(credential.id);
        }
      }
    }
    renderCredentialPicker(selectedIds);
    renderCredentialList();
  } catch (error) {
    credentialPicker.replaceChildren();
    const message = document.createElement("span");
    message.className = "credential-empty";
    message.textContent = error.message;
    credentialPicker.append(message);
  }
}

function renderCredentialPicker(selectedIds = new Set()) {
  credentialPicker.replaceChildren();
  if (!credentials.length) {
    const empty = document.createElement("span");
    empty.className = "credential-empty";
    empty.textContent = "No saved credentials.";
    credentialPicker.append(empty);
    return;
  }
  for (const credential of credentials) {
    const label = document.createElement("label");
    label.className = "credential-chip";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = credential.id;
    checkbox.checked = selectedIds.has(credential.id);
    const text = document.createElement("span");
    text.textContent = credential.name;
    label.append(checkbox, text);
    credentialPicker.append(label);
  }
}

function renderCredentialList() {
  credentialList.replaceChildren();
  for (const credential of credentials) {
    const item = document.createElement("li");
    const summary = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = credential.name;
    if (credential.is_default) {
      const badge = document.createElement("span");
      badge.className = "credential-default-badge";
      badge.textContent = "Default";
      name.append(" ", badge);
    }
    const detail = document.createElement("span");
    detail.textContent = `${credential.login_hint} · ${credential.allowed_domains.join(", ")}`;
    summary.append(name, detail);

    const actions = document.createElement("div");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "history-refresh";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => beginCredentialEdit(credential));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "history-refresh credential-delete";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => void deleteCredential(credential));
    actions.append(edit, remove);
    item.append(summary, actions);
    credentialList.append(item);
  }
}

function beginCredentialEdit(credential) {
  editingCredentialId = credential.id;
  credentialName.value = credential.name;
  credentialLogin.value = "";
  credentialLogin.placeholder = `Keep ${credential.login_hint}`;
  credentialPassword.value = "";
  credentialPassword.placeholder = "Leave blank to keep current password";
  credentialDomains.value = credential.allowed_domains.join(", ");
  credentialDefault.checked = Boolean(credential.is_default);
  credentialSaveButton.textContent = "Save changes";
  credentialEditCancel.hidden = false;
  credentialMessage.textContent = "";
  credentialName.focus();
}

function resetCredentialForm() {
  editingCredentialId = null;
  credentialForm.reset();
  credentialLogin.placeholder = "you@example.com";
  credentialPassword.placeholder = "Enter password";
  credentialSaveButton.textContent = "Save credential";
  credentialEditCancel.hidden = true;
  credentialMessage.textContent = "";
}

async function deleteCredential(credential) {
  if (!window.confirm(`Delete ${credential.name}? Existing runs will no longer be able to use it.`)) {
    return;
  }
  credentialMessage.textContent = "";
  try {
    const response = await fetch(`/api/credentials/${credential.id}`, {method: "DELETE"});
    if (!response.ok) {
      const body = await readResponseBody(response);
      throw new Error(body.detail || "The credential could not be deleted");
    }
    if (editingCredentialId === credential.id) {
      resetCredentialForm();
    }
    await loadCredentials();
  } catch (error) {
    credentialMessage.textContent = error.message;
  }
}

secureInputForm.addEventListener("submit", async (formEvent) => {
  formEvent.preventDefault();
  const code = secureInputCode.value.trim();
  if (!code || !activeRunId || !activeSensitiveRequestId) {
    return;
  }

  const requestId = activeSensitiveRequestId;
  const submitButton = secureInputForm.querySelector("button");
  submitButton.disabled = true;
  secureInputMessage.textContent = "";
  try {
    const response = await fetch(`/api/runs/${activeRunId}/sensitive-input`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({request_id: requestId, code}),
    });
    secureInputCode.value = "";
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The verification code could not be entered");
    }
    activeSensitiveRequestId = null;
    secureInputPanel.hidden = true;
  } catch (error) {
    secureInputCode.value = "";
    secureInputMessage.textContent = error.message;
    secureInputCode.focus();
  } finally {
    submitButton.disabled = false;
  }
});

answerForm.addEventListener("submit", async (formEvent) => {
  formEvent.preventDefault();
  const answer = answerInput.value.trim();
  if (!answer || !activeRunId) {
    return;
  }

  const submitButton = answerForm.querySelector("button");
  submitButton.disabled = true;

  try {
    const response = await fetch(`/api/runs/${activeRunId}/input`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({answer}),
    });
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The answer could not be sent");
    }

    answerInput.value = "";
    answerPanel.hidden = true;
  } catch (error) {
    answerQuestion.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

followUpForm.addEventListener("submit", async (formEvent) => {
  formEvent.preventDefault();
  const task = followUpInput.value.trim();
  if (!task || !activeRunId) {
    return;
  }

  const submitButton = followUpForm.querySelector(".primary-button");
  submitButton.disabled = true;
  endSessionButton.disabled = true;
  followUpMessage.textContent = "";

  try {
    const response = await fetch(`/api/runs/${activeRunId}/follow-ups`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({task}),
    });
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The follow-up could not be started");
    }

    followUpInput.value = "";
    followUpPanel.hidden = true;
    resultPanel.hidden = true;
    streamedMessage = "";
    cancelButton.disabled = false;
    updateStatus(responseBody.run.status);
    void refreshBrowserScreen();
  } catch (error) {
    followUpMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
    endSessionButton.disabled = false;
  }
});

endSessionButton.addEventListener("click", async () => {
  if (!activeRunId) {
    return;
  }

  const submitButton = followUpForm.querySelector(".primary-button");
  submitButton.disabled = true;
  endSessionButton.disabled = true;
  followUpMessage.textContent = "";

  try {
    const response = await fetch(`/api/runs/${activeRunId}/end`, {
      method: "POST",
    });
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The browser session could not be ended");
    }

    followUpPanel.hidden = true;
    updateStatus(responseBody.run.status);
  } catch (error) {
    followUpMessage.textContent = error.message;
    submitButton.disabled = false;
    endSessionButton.disabled = false;
  }
});

cancelButton.addEventListener("click", async () => {
  if (!activeRunId) {
    return;
  }

  cancelButton.disabled = true;
  try {
    const response = await fetch(`/api/runs/${activeRunId}/cancel`, {
      method: "POST",
    });
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The run could not be cancelled");
    }
  } catch (error) {
    addActivity("run.failed", {error: error.message}, new Date());
  }
});

rerunButton.addEventListener("click", async () => {
  if (!activeRunId) {
    return;
  }

  rerunButton.disabled = true;
  terminalMessage.textContent = "";
  try {
    await startRerun(activeRunId);
  } catch (error) {
    terminalMessage.textContent = error.message;
  } finally {
    rerunButton.disabled = false;
  }
});

/* Start a finished run again. The original stays in history untouched. */
async function startRerun(runId) {
  const response = await fetch(`/api/runs/${runId}/rerun`, {method: "POST"});
  const responseBody = await readResponseBody(response);
  if (!response.ok) {
    throw new Error(
      responseBody.detail || "The task could not be started again",
    );
  }

  openRun(responseBody);
  void loadRunHistory();
}

browserScreen.addEventListener("load", () => {
  browserScreen.hidden = false;
  browserWaiting.hidden = true;
});

function openRun(responseBody) {
  stopScreenRefresh();
  activeRunId = responseBody.run.id;
  activeScreenUrl = responseBody.screen_url;
  displayedEventCount = 0;
  streamedMessage = "";
  activityList.replaceChildren();
  activityEmpty.hidden = false;
  resultPanel.hidden = true;
  answerPanel.hidden = true;
  secureInputPanel.hidden = true;
  activeSensitiveRequestId = null;
  followUpPanel.hidden = true;
  followUpMessage.textContent = "";
  cancelButton.disabled = false;
  runIdLabel.textContent = activeRunId;
  latestAction.textContent = "The agent is preparing its browser.";
  briefSection.hidden = true;
  workspace.hidden = false;
  browserScreen.hidden = true;
  browserWaiting.hidden = false;
  browserWaitingMessage.textContent = "Waiting for the first browser frame";
  updateStatus(
    responseBody.run.status,
    responseBody.run.follow_up_expires_at,
  );
  void refreshBrowserScreen(
    !screenPausedStatuses.has(responseBody.run.status),
  );

  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource(responseBody.stream_url);
  for (const eventType of eventTypes) {
    eventSource.addEventListener(eventType, handleRunEvent);
  }

  eventSource.addEventListener("error", () => {
    if (!terminalStatuses.has(statusDot.dataset.status)) {
      statusLabel.textContent = "Reconnecting";
    }
  });
}

async function refreshBrowserScreen(scheduleNextRefresh = true) {
  const requestedRunId = activeRunId;
  const requestedScreenUrl = activeScreenUrl;
  if (!requestedRunId || !requestedScreenUrl || screenRequestInFlight) {
    return;
  }

  screenRequestInFlight = true;
  try {
    const response = await fetch(
      `${requestedScreenUrl}?captured=${Date.now()}`,
      {cache: "no-store"},
    );

    if (response.status === 204) {
      return;
    }
    if (response.status === 401) {
      returnToSignIn();
      return;
    }
    if (!response.ok) {
      throw new Error(`Live view returned ${response.status}`);
    }

    const frame = await response.blob();
    if (requestedRunId !== activeRunId) {
      return;
    }

    const nextScreenObjectUrl = URL.createObjectURL(frame);
    const previousScreenObjectUrl = currentScreenObjectUrl;
    currentScreenObjectUrl = nextScreenObjectUrl;
    browserScreen.src = nextScreenObjectUrl;
    if (previousScreenObjectUrl) {
      URL.revokeObjectURL(previousScreenObjectUrl);
    }
  } catch (error) {
    browserWaitingMessage.textContent = "Live view reconnecting";
  } finally {
    screenRequestInFlight = false;
    const runIsActive = requestedRunId === activeRunId;
    const screenRefreshIsPaused = screenPausedStatuses.has(
      statusDot.dataset.status,
    );
    if (scheduleNextRefresh && runIsActive && !screenRefreshIsPaused) {
      screenRefreshTimer = window.setTimeout(
        refreshBrowserScreen,
        SCREEN_REFRESH_INTERVAL_MS,
      );
    }
  }
}

function stopScreenRefresh() {
  if (screenRefreshTimer) {
    window.clearTimeout(screenRefreshTimer);
    screenRefreshTimer = null;
  }

  activeScreenUrl = null;
  screenRequestInFlight = false;
  if (currentScreenObjectUrl) {
    URL.revokeObjectURL(currentScreenObjectUrl);
    currentScreenObjectUrl = null;
  }
  browserScreen.removeAttribute("src");
}

function handleRunEvent(serverEvent) {
  const event = JSON.parse(serverEvent.data);
  const eventType = event.event_type;
  const payload = event.payload || {};

  if (eventType === "agent.message.delta") {
    streamedMessage += payload.text || "";
    resultOutput.textContent = streamedMessage;
    resultPanel.hidden = !streamedMessage;
    return;
  }

  addActivity(eventType, payload, new Date(event.created_at));

  if (eventType === "run.status") {
    updateStatus(payload.status, payload.follow_up_expires_at);
  } else if (eventType === "input.required") {
    answerQuestion.textContent = payload.question || "The agent needs more information.";
    answerPanel.hidden = false;
    answerInput.focus();
  } else if (eventType === "sensitive_input.required") {
    activeSensitiveRequestId = payload.request_id;
    secureInputQuestion.textContent = payload.prompt || "Enter your verification code.";
    secureInputMessage.textContent = "";
    secureInputPanel.hidden = false;
    answerPanel.hidden = true;
    secureInputCode.focus();
  } else if (eventType === "sensitive_input.received") {
    secureInputCode.value = "";
    secureInputPanel.hidden = true;
  } else if (eventType === "run.completed") {
    resultOutput.textContent = payload.output || streamedMessage;
    resultPanel.hidden = false;
    showFollowUpPanel(payload.follow_up_expires_at);
  } else if (eventType === "follow_up.started") {
    streamedMessage = "";
    resultOutput.textContent = "";
    resultPanel.hidden = true;
    followUpPanel.hidden = true;
    cancelButton.disabled = false;
    void refreshBrowserScreen();
  } else if (eventType === "run.failed") {
    resultOutput.textContent = payload.error || "The run failed.";
    resultPanel.hidden = false;
  } else if (eventType === "run.cancelled") {
    completeRun("cancelled");
  }
}

function addActivity(eventType, payload, createdAt) {
  const presentation = describeEvent(eventType, payload);
  if (!presentation) {
    return;
  }

  activityEmpty.hidden = true;
  displayedEventCount += 1;
  eventCount.textContent = `${displayedEventCount} ${displayedEventCount === 1 ? "event" : "events"}`;
  latestAction.textContent = presentation.detail
    ? `${presentation.title} · ${presentation.detail}`
    : presentation.title;

  const listItem = document.createElement("li");
  listItem.className = "activity-item";

  const timestamp = document.createElement("time");
  timestamp.dateTime = createdAt.toISOString();
  timestamp.textContent = createdAt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  const title = document.createElement("p");
  title.className = "activity-title";
  title.textContent = presentation.title;

  listItem.append(timestamp, title);
  if (presentation.detail) {
    const detail = document.createElement("p");
    detail.className = "activity-detail";
    detail.textContent = presentation.detail;
    listItem.append(detail);
  }

  activityList.prepend(listItem);
}

function describeEvent(eventType, payload) {
  const descriptions = {
    "run.created": {title: "Run created", detail: "The agent is preparing its browser."},
    "browser.navigation.started": {title: "Opening page", detail: payload.url},
    "browser.navigation.completed": {title: "Page loaded", detail: payload.url},
    "browser.observation": {title: "Page observed", detail: payload.message},
    "browser.action.started": {title: `${capitalize(payload.action)} started`, detail: payload.target},
    "browser.action.completed": {title: `${capitalize(payload.action)} completed`, detail: payload.target},
    "browser.action.failed": {title: `${capitalize(payload.action)} failed`, detail: payload.error},
    "browser.tab.changed": {title: "Browser tab changed", detail: payload.tab_id},
    "browser.capture.failed": {title: "Live view paused", detail: payload.message},
    "browser.capture.recovered": {title: "Live view resumed", detail: payload.message},
    "input.required": {title: "Waiting for your answer", detail: payload.question},
    "input.received": {title: "Answer received", detail: "The agent is continuing the task."},
    "sensitive_input.required": {title: "Verification required", detail: "Waiting for a secure code."},
    "sensitive_input.received": {title: "Verification code entered", detail: "The code was sent directly to the browser."},
    "follow_up.started": {title: "Follow-up started", detail: payload.task},
    "follow_up.ended": {title: "Browser session ended", detail: "The browser was closed."},
    "follow_up.expired": {title: "Browser session expired", detail: "The follow-up window ended."},
    "run.completed": {title: "Task completed", detail: "The agent verified its result."},
    "run.failed": {title: "Run failed", detail: payload.error},
    "run.cancelled": {title: "Run cancelled", detail: "The browser session was closed."},
  };

  return descriptions[eventType] || null;
}

function updateStatus(status, followUpExpiresAt = null) {
  const labels = {
    queued: "Queued",
    running: "Agent working",
    waiting_for_input: "Needs your input",
    ready_for_follow_up: "Ready for follow-up",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    timed_out: "Timed out",
  };

  runState.hidden = false;
  statusDot.className = `run-state-dot ${status}`;
  statusDot.dataset.status = status;
  statusLabel.textContent = labels[status] || status;

  if (status === "ready_for_follow_up") {
    showFollowUpPanel(followUpExpiresAt);
  } else {
    followUpPanel.hidden = true;
  }

  if (terminalStatuses.has(status)) {
    completeRun(status);
  }
}

function showFollowUpPanel(expiresAt) {
  cancelButton.disabled = true;
  answerPanel.hidden = true;
  secureInputPanel.hidden = true;
  activeSensitiveRequestId = null;
  followUpPanel.hidden = false;
  followUpMessage.textContent = "";
  followUpDeadline.textContent = formatFollowUpDeadline(expiresAt);

  if (screenRefreshTimer) {
    window.clearTimeout(screenRefreshTimer);
    screenRefreshTimer = null;
  }
  void refreshBrowserScreen(false);
}

function formatFollowUpDeadline(expiresAt) {
  if (!expiresAt) {
    return "Ask another task before this browser session closes.";
  }

  const expirationTime = new Date(expiresAt);
  return `Ask another task before the browser closes at ${expirationTime.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}.`;
}

function completeRun(status) {
  updateStatusWithoutCompletion(status);
  void loadRunHistory();
  cancelButton.disabled = true;
  answerPanel.hidden = true;
  followUpPanel.hidden = true;
  setFormBusy(false);

  if (screenRefreshTimer) {
    window.clearTimeout(screenRefreshTimer);
    screenRefreshTimer = null;
  }
  void refreshBrowserScreen(false);

  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function updateStatusWithoutCompletion(status) {
  const labels = {
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    timed_out: "Timed out",
  };
  runState.hidden = false;
  statusDot.className = `run-state-dot ${status}`;
  statusDot.dataset.status = status;
  statusLabel.textContent = labels[status] || status;
}

function setFormBusy(isBusy) {
  taskInput.disabled = isBusy;
  startButton.disabled = isBusy;
  for (const input of credentialPicker.querySelectorAll("input")) {
    input.disabled = isBusy;
  }
}

function capitalize(value) {
  if (!value) {
    return "Action";
  }

  return value.charAt(0).toUpperCase() + value.slice(1);
}

async function readResponseBody(response) {
  if (response.status === 401) {
    returnToSignIn();
    throw new Error("Your session expired. Redirecting to sign-in.");
  }

  try {
    return await response.json();
  } catch (error) {
    return {};
  }
}

function returnToSignIn() {
  window.location.href = "/login?next=%2Fapp";
}

async function loadSignedInAccount() {
  try {
    const response = await fetch("/api/auth/me");
    if (!response.ok) {
      return;
    }

    const account = await response.json();
    accountEmail.textContent = account.email;
  } catch (error) {
  }
}

const historyStatusLabels = {
  queued: "Queued",
  running: "Running",
  waiting_for_input: "Needs you",
  ready_for_follow_up: "Open",
  succeeded: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
};

async function loadRunHistory() {
  try {
    const response = await fetch("/api/runs");
    if (!response.ok) {
      if (response.status === 401) {
        returnToSignIn();
      }
      return;
    }

    const body = await response.json();
    renderRunHistory(body.runs || []);
  } catch (error) {
    // Leave whatever was already listed rather than blanking the panel.
  }
}

function renderRunHistory(runs) {
  historyEmpty.hidden = runs.length > 0;
  historyList.replaceChildren();

  for (const run of runs) {
    const item = document.createElement("li");
    item.className = "history-item";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-entry";
    button.addEventListener("click", () => {
      void openRunFromHistory(run.id);
    });

    const task = document.createElement("span");
    task.className = "history-task";
    task.textContent = run.task;

    const meta = document.createElement("span");
    meta.className = "history-meta";
    const status = document.createElement("span");
    status.className = `history-status ${run.status}`;
    status.textContent = historyStatusLabels[run.status] || run.status;
    const started = document.createElement("span");
    started.textContent = formatStartTime(run.created_at);
    meta.append(status, started);

    button.append(task, meta);
    item.append(button);

    if (terminalRunStatuses.has(run.status)) {
      const rerun = document.createElement("button");
      rerun.type = "button";
      rerun.className = "history-rerun";
      rerun.textContent = "Run again";
      rerun.title = "Start this task again";
      rerun.addEventListener("click", async (clickEvent) => {
        clickEvent.stopPropagation();
        rerun.disabled = true;
        try {
          await startRerun(run.id);
        } catch (error) {
          formMessage.textContent = error.message;
          rerun.disabled = false;
        }
      });
      item.append(rerun);
    }

    historyList.append(item);
  }
}

function formatStartTime(createdAt) {
  const startedAt = new Date(createdAt);
  const startedToday =
    startedAt.toDateString() === new Date().toDateString();

  return startedToday
    ? startedAt.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})
    : startedAt.toLocaleDateString([], {month: "short", day: "numeric"});
}

async function openRunFromHistory(runId) {
  try {
    const response = await fetch(`/api/runs/${runId}`);
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The run could not be opened");
    }

    openRun(responseBody);
    if (responseBody.run.final_output) {
      resultOutput.textContent = responseBody.run.final_output;
      resultPanel.hidden = false;
    }
  } catch (error) {
    formMessage.textContent = error.message;
  }
}

historyRefresh.addEventListener("click", () => {
  void loadRunHistory();
});

function restorePendingTask() {
  // Written by the landing page before it sent the visitor to sign up.
  try {
    const pendingTask = window.sessionStorage.getItem(PENDING_TASK_KEY);
    if (!pendingTask) {
      return;
    }

    window.sessionStorage.removeItem(PENDING_TASK_KEY);
    taskInput.value = pendingTask;
    taskInput.focus();
  } catch (error) {
  }
}

restorePendingTask();
void loadSignedInAccount();
void loadRunHistory();
void loadCredentials(null, true);
