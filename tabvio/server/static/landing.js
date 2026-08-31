const PENDING_TASK_KEY = "tabvio.pending-task";

const taskForm = document.querySelector("#task-form");
const taskInput = document.querySelector("#task-input");
const exampleChips = document.querySelectorAll(".example-chip");

for (const chip of exampleChips) {
  chip.addEventListener("click", () => {
    taskInput.value = chip.dataset.example || "";
    taskInput.focus();
  });
}

taskForm.addEventListener("submit", (formEvent) => {
  formEvent.preventDefault();
  const task = taskInput.value.trim();
  if (!task) {
    return;
  }

  // Hand the task to the dashboard so signing up does not cost the visitor
  // the thing they came here to type.
  try {
    window.sessionStorage.setItem(PENDING_TASK_KEY, task);
  } catch (error) {
    // Private browsing can refuse storage; the task is simply not carried.
  }

  window.location.href = "/signup?next=%2Fapp";
});
