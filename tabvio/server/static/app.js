const SCREEN_REFRESH_INTERVAL_MS = 750;
const PENDING_TASK_KEY = "tabvio.pending-task";

const taskForm = document.querySelector("#task-form");
const taskInput = document.querySelector("#task-input");
const startButton = document.querySelector("#start-button");
const formMessage = document.querySelector("#form-message");
const briefSection = document.querySelector("#brief-section");
const workspace = document.querySelector("#workspace");
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
const accountEmail = document.querySelector("#account-email");
const historyList = document.querySelector("#history-list");
const historyEmpty = document.querySelector("#history-empty");
const historyRefresh = document.querySelector("#history-refresh");

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
      body: JSON.stringify({task}),
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
  statusDot.className = `run-state-dot ${status}`;
  statusDot.dataset.status = status;
  statusLabel.textContent = labels[status] || status;
}

function setFormBusy(isBusy) {
  taskInput.disabled = isBusy;
  startButton.disabled = isBusy;
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
