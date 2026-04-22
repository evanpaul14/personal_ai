import { fetchModels } from "./api.js";

const STORAGE_KEY = "pai_model_id";
const DEFAULT_MODEL = "z-ai/glm-4.5-air:free";

const trigger = document.getElementById("model-trigger");
const triggerText = document.getElementById("model-trigger-text");
const dropdown = document.getElementById("model-dropdown");
const panel = document.getElementById("model-panel");
const modelList = document.getElementById("model-list");
const searchInput = document.getElementById("model-search");

let _models = [];
let _selectedId = "";

export function getCurrentModel() {
  return _models.find(m => m.id === _selectedId) || null;
}
export function currentModelSupportsVision() {
  return getCurrentModel()?.supports_vision ?? false;
}
export function currentModelSupportsTools() {
  return getCurrentModel()?.supports_tools ?? true;
}
export function getCurrentModelId() { return _selectedId; }

function setSelected(id) {
  _selectedId = id;
  const m = _models.find(m => m.id === id);
  triggerText.textContent = m ? (m.name || m.id) : id;
  localStorage.setItem(STORAGE_KEY, id);
  // Update active item in list
  modelList.querySelectorAll(".model-option").forEach(el => {
    el.classList.toggle("active", el.dataset.id === id);
  });
  document.dispatchEvent(new CustomEvent("modelChanged", { detail: { model: m } }));
}

function openPanel() {
  panel.classList.remove("hidden");
  dropdown.setAttribute("aria-expanded", "true");
  searchInput.value = "";
  renderList("");
  searchInput.focus();
  // Scroll active item into view
  const active = modelList.querySelector(".model-option.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function closePanel() {
  panel.classList.add("hidden");
  dropdown.setAttribute("aria-expanded", "false");
}

function renderList(filter) {
  const q = filter.toLowerCase().trim();
  modelList.innerHTML = "";

  const filtered = q
    ? _models.filter(m =>
        m.name.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q)
      )
    : _models;

  if (!filtered.length) {
    modelList.innerHTML = `<div class="model-empty">no matches</div>`;
    return;
  }

  // Group by provider
  const groups = {};
  for (const m of filtered) {
    const provider = m.id.split("/")[0];
    if (!groups[provider]) groups[provider] = [];
    groups[provider].push(m);
  }

  for (const [provider, models] of Object.entries(groups)) {
    const groupEl = document.createElement("div");
    groupEl.className = "model-group";

    const label = document.createElement("div");
    label.className = "model-group-label";
    label.textContent = provider;
    groupEl.appendChild(label);

    for (const m of models) {
      const opt = document.createElement("div");
      opt.className = "model-option" + (m.id === _selectedId ? " active" : "");
      opt.dataset.id = m.id;
      opt.setAttribute("role", "option");
      opt.setAttribute("aria-selected", m.id === _selectedId ? "true" : "false");

      const nameSpan = document.createElement("span");
      nameSpan.className = "model-option-name";
      nameSpan.textContent = m.name || m.id;

      const badges = document.createElement("span");
      badges.className = "model-option-badges";
      if (m.supports_vision) badges.innerHTML += `<span class="badge badge-vision">vision</span>`;
      if (m.supports_tools === false) badges.innerHTML += `<span class="badge badge-no-tools">no tools</span>`;
      if (m.id.includes(":free")) badges.innerHTML += `<span class="badge badge-free">free</span>`;

      opt.appendChild(nameSpan);
      opt.appendChild(badges);
      opt.addEventListener("click", () => {
        setSelected(m.id);
        closePanel();
      });
      groupEl.appendChild(opt);
    }
    modelList.appendChild(groupEl);
  }
}

// Toggle on trigger click
trigger.addEventListener("click", (e) => {
  e.stopPropagation();
  panel.classList.contains("hidden") ? openPanel() : closePanel();
});

// Filter on search input
searchInput.addEventListener("input", () => renderList(searchInput.value));
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closePanel(); trigger.focus(); }
});

// Close on outside click
document.addEventListener("click", (e) => {
  if (!dropdown.contains(e.target)) closePanel();
});

export async function initModels() {
  try {
    _models = await fetchModels();
    const saved = localStorage.getItem(STORAGE_KEY);
    const preferred = [saved, DEFAULT_MODEL].find(id => id && _models.find(m => m.id === id));
    setSelected(preferred || (_models[0]?.id ?? ""));
    triggerText.textContent = getCurrentModel()?.name || _selectedId || "select model";
  } catch (err) {
    triggerText.textContent = "error loading models";
    console.error("Models load failed:", err);
  }
}
