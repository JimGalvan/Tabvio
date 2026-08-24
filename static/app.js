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
const activityList = document.querySelector("#activity-list");
const activityEmpty = document.querySelector("#activity-empty");
const eventCount = document.querySelector("#event-count");
const answerPanel = document.querySelector("#answer-panel");
const answerQuestion = document.querySelector("#answer-question");
const answerForm = document.querySelector("#answer-form");
const answerInput = document.querySelector("#answer-input");
const resultPanel = document.querySelector("#result-panel");
const resultOutput = document.querySelector("#result-output");

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
  "agent.message.delta",
  "input.required",
  "input.received",
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

let activeRunId = null;
let eventSource = null;
let displayedEventCount = 0;
let streamedMessage = "";

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
  activeRunId = responseBody.run.id;
  displayedEventCount = 0;
  streamedMessage = "";
  activityList.replaceChildren();
  activityEmpty.hidden = false;
  resultPanel.hidden = true;
  answerPanel.hidden = true;
  cancelButton.disabled = false;
  runIdLabel.textContent = activeRunId;
  briefSection.hidden = true;
  workspace.hidden = false;
  updateStatus(responseBody.run.status);

  browserScreen.hidden = true;
  browserWaiting.hidden = false;
  browserScreen.src = `${responseBody.screen_url}?started=${Date.now()}`;

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
    updateStatus(payload.status);
  } else if (eventType === "input.required") {
    answerQuestion.textContent = payload.question || "The agent needs more information.";
    answerPanel.hidden = false;
    answerInput.focus();
  } else if (eventType === "run.completed") {
    resultOutput.textContent = payload.output || streamedMessage;
    resultPanel.hidden = false;
    completeRun("succeeded");
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
    "input.required": {title: "Waiting for your answer", detail: payload.question},
    "input.received": {title: "Answer received", detail: "The agent is continuing the task."},
    "run.completed": {title: "Run completed", detail: "The agent verified its final result."},
    "run.failed": {title: "Run failed", detail: payload.error},
    "run.cancelled": {title: "Run cancelled", detail: "The browser session was closed."},
  };

  return descriptions[eventType] || null;
}

function updateStatus(status) {
  const labels = {
    queued: "Queued",
    running: "Agent working",
    waiting_for_input: "Needs your input",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    timed_out: "Timed out",
  };

  statusDot.className = `run-state-dot ${status}`;
  statusDot.dataset.status = status;
  statusLabel.textContent = labels[status] || status;

  if (terminalStatuses.has(status)) {
    completeRun(status);
  }
}

function completeRun(status) {
  updateStatusWithoutCompletion(status);
  cancelButton.disabled = true;
  answerPanel.hidden = true;
  setFormBusy(false);

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
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
}
