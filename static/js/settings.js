import { updateConversation } from "./api.js";

const modal = document.getElementById("settings-modal");
const openBtn = document.getElementById("settings-btn");
const closeBtn = document.getElementById("settings-close");
const saveBtn = document.getElementById("settings-save");
const promptInput = document.getElementById("system-prompt-input");
const reasoningToggle = document.getElementById("reasoning-toggle");

const REASONING_KEY = "pai_reasoning";

let _currentCid = null;
let _onSave = null;

export function setCurrentCid(cid) { _currentCid = cid; }
export function setOnSave(fn) { _onSave = fn; }
export function setSystemPromptValue(value) { promptInput.value = value || ""; }
export function getSystemPromptValue() { return promptInput.value.trim(); }
export function getReasoningEnabled() {
  return localStorage.getItem(REASONING_KEY) === "true";
}

// Init toggle from localStorage
if (reasoningToggle) {
  reasoningToggle.checked = getReasoningEnabled();
  reasoningToggle.addEventListener("change", () => {
    localStorage.setItem(REASONING_KEY, reasoningToggle.checked ? "true" : "false");
  });
}

function openModal() {
  modal.classList.remove("hidden");
  if (reasoningToggle) reasoningToggle.checked = getReasoningEnabled();
  // Do NOT auto-focus — on mobile this would open the keyboard immediately
}
function closeModal() { modal.classList.add("hidden"); }

openBtn.addEventListener("click", openModal);
closeBtn.addEventListener("click", closeModal);
modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
});

saveBtn.addEventListener("click", async () => {
  const prompt = promptInput.value.trim();
  if (_currentCid) {
    try {
      await updateConversation(_currentCid, { system_prompt: prompt || null });
    } catch (err) {
      console.error("Failed to save system prompt:", err);
    }
  }
  if (reasoningToggle) {
    localStorage.setItem(REASONING_KEY, reasoningToggle.checked ? "true" : "false");
  }
  _onSave?.(prompt);
  closeModal();
});
