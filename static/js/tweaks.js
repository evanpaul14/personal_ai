const DEFAULTS = {
  crt: "medium",
  phos: "retro",
  scan: 3,
  tt: 0,
  flicker: true,
};

const STATE = Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem("pai_tweaks") || "{}"));

export function getTeletypeSpeed() { return STATE.tt; }

function save() {
  localStorage.setItem("pai_tweaks", JSON.stringify(STATE));
}

function applyTweaks() {
  const b = document.body;
  b.classList.remove("crt-subtle", "crt-medium", "crt-full");
  b.classList.add("crt-" + STATE.crt);
  b.classList.remove("phos-retro", "phos-amber", "phos-green");
  b.classList.add("phos-" + STATE.phos);
  document.documentElement.style.setProperty("--scanline-gap", STATE.scan + "px");
  b.classList.toggle("no-flicker", !STATE.flicker);

  const el = (id) => document.getElementById(id);
  if (el("v-crt"))  el("v-crt").textContent  = STATE.crt;
  if (el("v-phos")) el("v-phos").textContent = STATE.phos;
  if (el("v-scan")) el("v-scan").textContent = STATE.scan + "px";
  if (el("v-tt"))   el("v-tt").textContent   = STATE.tt === 0 ? "instant" : STATE.tt + " ms/ch";

  document.querySelectorAll("[data-tweak] button").forEach(btn => {
    const key = btn.parentElement.dataset.tweak;
    btn.classList.toggle("active", btn.dataset.val === String(STATE[key]));
  });

  if (el("r-scan")) el("r-scan").value = STATE.scan;
  if (el("r-tt"))   el("r-tt").value   = STATE.tt;
  if (el("c-flicker")) el("c-flicker").checked = STATE.flicker;
}

export function initTweaks() {
  applyTweaks();

  document.querySelectorAll("[data-tweak]").forEach(group => {
    const key = group.dataset.tweak;
    group.querySelectorAll("button").forEach(btn => {
      btn.addEventListener("click", () => {
        STATE[key] = btn.dataset.val;
        applyTweaks();
        save();
      });
    });
  });

  document.getElementById("r-scan")?.addEventListener("input", e => {
    STATE.scan = +e.target.value; applyTweaks(); save();
  });
  document.getElementById("r-tt")?.addEventListener("input", e => {
    STATE.tt = +e.target.value; applyTweaks(); save();
  });
  document.getElementById("c-flicker")?.addEventListener("change", e => {
    STATE.flicker = e.target.checked; applyTweaks(); save();
  });

  document.getElementById("tweaks-fab")?.addEventListener("click", () => {
    document.getElementById("tweaks")?.classList.add("open");
  });
  document.getElementById("tweaks-close")?.addEventListener("click", () => {
    document.getElementById("tweaks")?.classList.remove("open");
  });
}
