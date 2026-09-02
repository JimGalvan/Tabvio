const RUN_FRAGMENT_PREFIX = "#run=";
const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const observedRunId = document.querySelector("#run-id");
const observedRunStatus = document.querySelector("#status-dot");
const terminalActions = document.querySelector("#terminal-actions");
const newRunButton = document.querySelector("#new-run-button");
const terminalRunStatuses = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
]);

const runIdObserver = new MutationObserver(() => {
  const runId = observedRunId.textContent.trim();
  if (!RUN_ID_PATTERN.test(runId)) {
    return;
  }

  const updatedUrl = `${window.location.pathname}${window.location.search}${RUN_FRAGMENT_PREFIX}${runId}`;
  window.history.replaceState(null, "", updatedUrl);
});
runIdObserver.observe(observedRunId, {childList: true});

const runStatusObserver = new MutationObserver(() => {
  terminalActions.hidden = !terminalRunStatuses.has(
    observedRunStatus.dataset.status,
  );
});
runStatusObserver.observe(observedRunStatus, {attributes: true});

newRunButton.addEventListener("click", () => {
  resetRunViewer();
});

function resetRunViewer() {
  stopScreenRefresh();
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  activeRunId = null;
  displayedEventCount = 0;
  streamedMessage = "";
  runIdLabel.textContent = "";
  activityList.replaceChildren();
  activityEmpty.hidden = false;
  eventCount.textContent = "0 events";
  resultOutput.textContent = "";
  resultPanel.hidden = true;
  terminalActions.hidden = true;
  answerPanel.hidden = true;
  secureInputPanel.hidden = true;
  secureInputCode.value = "";
  activeSensitiveRequestId = null;
  followUpPanel.hidden = true;
  followUpInput.value = "";
  followUpMessage.textContent = "";
  workspace.hidden = true;
  briefSection.hidden = false;
  formMessage.textContent = "";
  taskInput.value = "";
  setFormBusy(false);

  statusDot.className = "run-state-dot";
  delete statusDot.dataset.status;
  statusLabel.textContent = "Ready";
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
  taskInput.focus();
}

async function restoreRunFromFragment() {
  if (!window.location.hash.startsWith(RUN_FRAGMENT_PREFIX)) {
    return;
  }

  const runId = window.location.hash.slice(RUN_FRAGMENT_PREFIX.length);
  if (!RUN_ID_PATTERN.test(runId)) {
    formMessage.textContent = "The run link is invalid.";
    return;
  }

  setFormBusy(true);
  try {
    const response = await fetch(`/api/runs/${runId}`);
    const responseBody = await readResponseBody(response);
    if (!response.ok) {
      throw new Error(responseBody.detail || "The run could not be restored");
    }

    openRun(responseBody);
    if (responseBody.run.final_output) {
      resultOutput.textContent = responseBody.run.final_output;
      resultPanel.hidden = false;
    }
  } catch (error) {
    formMessage.textContent = error.message;
    setFormBusy(false);
  }
}

void restoreRunFromFragment();
