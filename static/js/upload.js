import { setPendingImage, clearPendingImage } from "./chat.js";

const uploadBtn = document.getElementById("upload-btn");
const fileInput = document.getElementById("file-input");
const previewWrap = document.getElementById("upload-preview");
const previewInner = document.getElementById("preview-inner");
const clearBtn = document.getElementById("clear-upload");

const MAX_DIM = 1024;

export function initUpload() {
  uploadBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file) handleFile(file);
    fileInput.value = "";
  });

  clearBtn.addEventListener("click", () => clearUpload());

  // Listen for programmatic clear
  document.addEventListener("imageClear", () => clearUpload(false));

  // Drag-drop on chat viewport
  const viewport = document.getElementById("chat-viewport");
  viewport.addEventListener("dragover", (e) => { e.preventDefault(); });
  viewport.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) handleFile(file);
  });
}

export function setVisionEnabled(enabled) {
  uploadBtn.classList.toggle("hidden", !enabled);
  if (!enabled) clearUpload();
}

async function handleFile(file) {
  if (!file.type.startsWith("image/")) return;
  const resized = await resizeImage(file, MAX_DIM);
  setPendingImage(resized);
  showPreview(resized);
}

function showPreview(file) {
  previewInner.innerHTML = "";
  const url = URL.createObjectURL(file);
  const img = document.createElement("img");
  img.src = url;
  img.className = "preview-thumb";
  img.alt = "image to upload";
  previewInner.appendChild(img);
  previewWrap.classList.remove("hidden");
}

function clearUpload(clearState = true) {
  if (clearState) clearPendingImage();
  previewInner.innerHTML = "";
  previewWrap.classList.add("hidden");
}

function resizeImage(file, maxDim) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width, height } = img;
      if (width <= maxDim && height <= maxDim) { resolve(file); return; }
      const ratio = Math.min(maxDim / width, maxDim / height);
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        resolve(new File([blob], file.name, { type: "image/jpeg" }));
      }, "image/jpeg", 0.88);
    };
    img.src = url;
  });
}
