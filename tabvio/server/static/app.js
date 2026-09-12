const SCREEN_REFRESH_INTERVAL_MS = 750;
const TAKEOVER_REFRESH_INTERVAL_MS = 120;
const MAX_SCROLL_PIXELS = 2000;
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
const browserViewport = document.querySelector("#browser-viewport");
const browserScreen = document.querySelector("#browser-screen");
const takeoverBar = document.querySelector("#takeover-bar");
const takeoverNote = document.querySelector("#takeover-note");
const takeoverButton = document.querySelector("#takeover-button");
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
const answerMessage = document.querySelector("#answer-message");
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
const credentialVerification = document.querySelector("#credential-verification");
const credentialVerificationFallback = document.querySelector(
  "#credential-verification-fallback",
);

const VERIFICATION_LABELS = {
  sms: "Text message",
  email: "Email",
  authenticator_app: "Authenticator app",
  phone_call: "Phone call",
  push: "Push notification",
};
const credentialMessage = document.querySelector("#credential-message");
const credentialSaveButton = document.querySelector("#credential-save-button");
const credentialEditCancel = document.querySelector("#credential-edit-cancel");
const credentialList = document.querySelector("#credential-list");
const secureInputPanel = document.querySelector("#secure-input-panel");
const secureInputQuestion = document.querySelector("#secure-input-question");
const secureInputForm = document.querySelector("#secure-input-form");
const secureInputCode = document.querySelector("#secure-input-code");
const secureInputMessage = document.querySelector("#secure-input-message");
const secureInputDecline = document.querySelector("#secure-input-decline");
const secureInputDeadline = document.querySelector("#secure-input-deadline");

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
  "takeover.started",
  "takeover.ended",
  "sensitive_input.required",
  "sensitive_input.received",
  "sensitive_input.cancelled",
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
let activeControlUrl = null;
let secureInputCountdownTimer = null;
let controlSocket = null;
let takeoverIsActive = false;
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
    preferred_verification: chosenVerificationMethods(),
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

function chosenVerificationMethods() {
  const chosen = [
    credentialVerification.value,
    credentialVerificationFallback.value,
  ].filter(Boolean);
  return chosen.length ? chosen : ["ask"];
}

function describeVerification(methods) {
  const labelled = (methods || []).map((method) => VERIFICATION_LABELS[method] || method);
  return labelled.length ? ` · verify by ${labelled.join(", then ")}` : "";
}

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
    detail.textContent =
      `${credential.login_hint} · ${credential.allowed_domains.join(", ")}` +
      describeVerification(credential.preferred_verification);
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
  const [preferred = "", fallback = ""] = credential.preferred_verification || [];
  credentialVerification.value = preferred;
  credentialVerificationFallback.value = fallback;
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
  const submitButton = secureInputForm.querySelector("button[type='submit']");
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
    hideSecureInputPanel();
  } catch (error) {
    secureInputCode.value = "";
    secureInputMessage.textContent = error.message;
    secureInputCode.focus();
  } finally {
    submitButton.disabled = false;
  }
});

secureInputDecline.addEventListener("click", async () => {
  if (!activeRunId || !activeSensitiveRequestId) {
    return;
  }

  secureInputDecline.disabled = true;
  secureInputMessage.textContent = "";
  try {
    const response = await fetch(
      `/api/runs/${activeRunId}/sensitive-input/decline`,
      {method: "POST"},
    );
    if (!response.ok) {
      const responseBody = await readResponseBody(response);
      throw new Error(responseBody.detail || "The code request could not be dropped");
    }
    hideSecureInputPanel();
  } catch (error) {
    secureInputMessage.textContent = error.message;
  } finally {
    secureInputDecline.disabled = false;
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
    answerMessage.textContent = "";
    answerPanel.hidden = true;
  } catch (error) {
    answerMessage.textContent = error.message;
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

const forwardedKeys = new Set([
  "Enter",
  "Tab",
  "Backspace",
  "Delete",
  "Escape",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

const takeoverCloseReasons = {
  1008: "Sign in again to use this browser.",
  4404: "That run is no longer available.",
  4409: "The agent is no longer paused.",
};

function frameCoordinates(pointerEvent) {
  const box = browserScreen.getBoundingClientRect();
  if (!browserScreen.naturalWidth || !box.width || !box.height) {
    return null;
  }

  // object-fit: contain letterboxes the frame inside the element, so the drawn
  // image is not the element box and the offset has to come out first.
  const frameWidth = browserScreen.naturalWidth;
  const frameHeight = browserScreen.naturalHeight;
  const scale = Math.min(box.width / frameWidth, box.height / frameHeight);
  const left = box.left + (box.width - frameWidth * scale) / 2;
  const top = box.top + (box.height - frameHeight * scale) / 2;

  return {
    x: Math.min(Math.max((pointerEvent.clientX - left) / scale, 0), frameWidth),
    y: Math.min(Math.max((pointerEvent.clientY - top) / scale, 0), frameHeight),
  };
}

function sendControlEvent(controlEvent) {
  if (
    !takeoverIsActive ||
    !controlSocket ||
    controlSocket.readyState !== WebSocket.OPEN
  ) {
    return;
  }

  controlSocket.send(JSON.stringify(controlEvent));
}

function startTakeover() {
  if (takeoverIsActive || controlSocket || !activeControlUrl) {
    return;
  }

  const socketUrl = new URL(activeControlUrl, window.location.href);
  socketUrl.protocol = socketUrl.protocol === "https:" ? "wss:" : "ws:";
  controlSocket = new WebSocket(socketUrl.toString());

  controlSocket.addEventListener("open", () => {
    takeoverIsActive = true;
    browserViewport.classList.add("is-interactive");
    browserViewport.tabIndex = 0;
    browserViewport.focus();
    takeoverButton.textContent = "Give control back";
    takeoverNote.textContent =
      "You have the browser. Click and type in the frame; the agent waits.";
    requestImmediateFrame();
  });

  controlSocket.addEventListener("message", (socketEvent) => {
    const acknowledgement = JSON.parse(socketEvent.data);
    if (!acknowledgement.applied && acknowledgement.detail) {
      takeoverNote.textContent = acknowledgement.detail;
    }

    requestImmediateFrame();
  });

  controlSocket.addEventListener("close", (closeEvent) => {
    finishTakeover(takeoverCloseReasons[closeEvent.code]);
  });
}

function stopTakeover(message = null) {
  releaseHeldMouse();
  if (controlSocket) {
    controlSocket.close();
  }

  finishTakeover(message);
}

function finishTakeover(message = null) {
  releaseHeldMouse();
  controlSocket = null;
  takeoverIsActive = false;
  browserViewport.classList.remove("is-interactive");
  browserViewport.removeAttribute("tabindex");
  takeoverButton.textContent = "Take control";
  takeoverNote.textContent =
    message || "You can use this browser while the agent is waiting or the session is open for follow-up.";
}

function showTakeoverBar(status) {
  const canTakeControl = status === "waiting_for_input" || status === "ready_for_follow_up";
  takeoverBar.hidden = !canTakeControl;
  if (!canTakeControl && takeoverIsActive) {
    stopTakeover();
  }
}

takeoverButton.addEventListener("click", () => {
  if (takeoverIsActive || controlSocket) {
    stopTakeover();
  } else {
    startTakeover();
  }
});

let heldMousePointer = null;

function releaseHeldMouse() {
  if (heldMousePointer === null) {
    return;
  }
  const pointerId = heldMousePointer;
  heldMousePointer = null;
  sendControlEvent({type: "mouse_up"});
  if (browserViewport.hasPointerCapture(pointerId)) {
    browserViewport.releasePointerCapture(pointerId);
  }
}

browserViewport.addEventListener("pointerdown", (pointerEvent) => {
  if (!takeoverIsActive || pointerEvent.button !== 0 || heldMousePointer !== null) {
    return;
  }

  const coordinates = frameCoordinates(pointerEvent);
  if (coordinates) {
    pointerEvent.preventDefault();
    browserViewport.focus();
    browserViewport.setPointerCapture(pointerEvent.pointerId);
    heldMousePointer = pointerEvent.pointerId;
    sendControlEvent({type: "mouse_down", x: coordinates.x, y: coordinates.y});
  }
});

for (const eventType of ["pointerup", "pointercancel", "lostpointercapture"]) {
  browserViewport.addEventListener(eventType, (event) => {
    if (event.pointerId === heldMousePointer) {
      releaseHeldMouse();
    }
  });
}
browserViewport.addEventListener("dragstart", (event) => {
  if (takeoverIsActive) event.preventDefault();
});
window.addEventListener("blur", releaseHeldMouse);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) releaseHeldMouse();
});

browserViewport.addEventListener(
  "wheel",
  (wheelEvent) => {
    if (!takeoverIsActive) {
      return;
    }

    const coordinates = frameCoordinates(wheelEvent);
    if (!coordinates) {
      return;
    }

    wheelEvent.preventDefault();
    sendControlEvent({
      type: "scroll",
      x: coordinates.x,
      y: coordinates.y,
      delta_y: Math.min(
        Math.max(wheelEvent.deltaY, -MAX_SCROLL_PIXELS),
        MAX_SCROLL_PIXELS,
      ),
    });
  },
  {passive: false},
);

browserViewport.addEventListener("keydown", (keyEvent) => {
  if (!takeoverIsActive) {
    return;
  }

  if ((keyEvent.ctrlKey || keyEvent.metaKey) && keyEvent.key.toLowerCase() === "a") {
    keyEvent.preventDefault();
    sendControlEvent({type: "key", key: keyEvent.ctrlKey ? "Control+A" : "Meta+A"});
    return;
  }

  if (keyEvent.ctrlKey || keyEvent.metaKey || keyEvent.altKey) {
    return;
  }

  if (keyEvent.key.length === 1) {
    keyEvent.preventDefault();
    sendControlEvent({type: "text", text: keyEvent.key});
    return;
  }

  if (forwardedKeys.has(keyEvent.key)) {
    keyEvent.preventDefault();
    sendControlEvent({type: "key", key: keyEvent.key});
  }
});

function openRun(responseBody) {
  stopScreenRefresh();
  activeRunId = responseBody.run.id;
  activeScreenUrl = responseBody.screen_url;
  activeControlUrl = responseBody.control_url;
  displayedEventCount = 0;
  streamedMessage = "";
  activityList.replaceChildren();
  activityEmpty.hidden = false;
  resultPanel.hidden = true;
  answerPanel.hidden = true;
  hideSecureInputPanel();
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
    const screenRefreshIsPaused = !takeoverIsActive && screenPausedStatuses.has(
      statusDot.dataset.status,
    );
    if (scheduleNextRefresh && runIsActive && !screenRefreshIsPaused) {
      screenRefreshTimer = window.setTimeout(
        refreshBrowserScreen,
        takeoverIsActive
          ? TAKEOVER_REFRESH_INTERVAL_MS
          : SCREEN_REFRESH_INTERVAL_MS,
      );
    }
  }
}

function requestImmediateFrame() {
  if (screenRefreshTimer) {
    window.clearTimeout(screenRefreshTimer);
    screenRefreshTimer = null;
  }

  void refreshBrowserScreen();
}

function stopScreenRefresh() {
  if (screenRefreshTimer) {
    window.clearTimeout(screenRefreshTimer);
    screenRefreshTimer = null;
  }

  stopTakeover();
  activeScreenUrl = null;
  activeControlUrl = null;
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
    return;
  }

  addActivity(eventType, payload, new Date(event.created_at));

  if (eventType === "run.status") {
    updateStatus(payload.status, payload.follow_up_expires_at);
  } else if (eventType === "input.required") {
    answerQuestion.textContent = payload.question || "The agent needs more information.";
    answerMessage.textContent = "";
    answerPanel.hidden = false;
    if (activeSensitiveRequestId) {
      secureInputCode.focus();
    } else {
      hideSecureInputPanel();
      answerInput.focus();
    }
  } else if (eventType === "sensitive_input.required") {
    activeSensitiveRequestId = payload.request_id;
    secureInputQuestion.textContent = payload.prompt || "Enter your verification code.";
    secureInputMessage.textContent = "";
    secureInputPanel.hidden = false;
    answerPanel.hidden = true;
    startSecureInputCountdown(payload.expires_at);
    secureInputCode.focus();
  } else if (
    eventType === "sensitive_input.received" ||
    eventType === "sensitive_input.cancelled"
  ) {
    hideSecureInputPanel();
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
    "takeover.started": {title: "You took control", detail: "The browser is yours until you hand it back."},
    "takeover.ended": {title: "Control handed back", detail: "The agent has its browser again."},
    "sensitive_input.required": {title: "Verification required", detail: "Waiting for a secure code."},
    "sensitive_input.received": {title: "Verification code entered", detail: "The code was sent directly to the browser."},
    "sensitive_input.cancelled": {
      title: "Code request dropped",
      detail: payload.reason
        ? `The agent will carry on: ${payload.reason}.`
        : "The agent will carry on without a code.",
    },
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
  showTakeoverBar(status);

  if (status === "ready_for_follow_up") {
    showFollowUpPanel(followUpExpiresAt);
  } else {
    followUpPanel.hidden = true;
  }

  if (terminalStatuses.has(status)) {
    completeRun(status);
  }
}

function hideSecureInputPanel() {
  stopSecureInputCountdown();
  secureInputDeadline.textContent = "";
  secureInputCode.value = "";
  secureInputMessage.textContent = "";
  secureInputPanel.hidden = true;
  activeSensitiveRequestId = null;
}

function startSecureInputCountdown(expiresAt) {
  stopSecureInputCountdown();
  if (!expiresAt) {
    secureInputDeadline.textContent = "";
    return;
  }

  const deadline = new Date(expiresAt).getTime();
  const renderRemaining = () => {
    const secondsLeft = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    if (secondsLeft === 0) {
      stopSecureInputCountdown();
      secureInputDeadline.textContent =
        "Time is up. The agent is carrying on without a code.";
      return;
    }
    const minutes = Math.floor(secondsLeft / 60);
    const seconds = String(secondsLeft % 60).padStart(2, "0");
    secureInputDeadline.textContent =
      `${minutes}:${seconds} left before the agent carries on without a code.`;
  };

  renderRemaining();
  secureInputCountdownTimer = window.setInterval(renderRemaining, 1000);
}

function stopSecureInputCountdown() {
  if (secureInputCountdownTimer) {
    window.clearInterval(secureInputCountdownTimer);
    secureInputCountdownTimer = null;
  }
}

function showFollowUpPanel(expiresAt) {
  cancelButton.disabled = true;
  answerPanel.hidden = true;
  hideSecureInputPanel();
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
  takeoverBar.hidden = true;
  stopTakeover();
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
