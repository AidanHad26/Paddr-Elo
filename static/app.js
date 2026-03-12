/* Paddr Elo — app.js */

(function () {
  "use strict";

  // ── Populate player dropdowns on the Record Game page ──────────────────
  async function loadPlayerDropdowns() {
    const selects = document.querySelectorAll("select[data-player-select]");
    if (!selects.length) return;

    let players;
    try {
      const resp = await fetch("/api/players");
      players = await resp.json();
    } catch (e) {
      console.error("Failed to load players:", e);
      return;
    }

    selects.forEach((sel) => {
      const currentVal = sel.value;
      // keep the placeholder option
      while (sel.options.length > 1) sel.remove(1);

      players.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.name}  (${p.elo.toFixed(1)})`;
        sel.appendChild(opt);
      });

      if (currentVal) sel.value = currentVal;
    });
  }

  // ── Duplicate-player validation on form submit ──────────────────────────
  function attachGameFormValidation() {
    const form = document.querySelector("form[data-game-form]");
    if (!form) return;

    form.addEventListener("submit", function (e) {
      const names = ["t1p1", "t1p2", "t2p1", "t2p2"];
      const ids = names.map((n) => form.querySelector(`[name="${n}"]`).value);

      // check all selected
      if (ids.some((id) => !id)) {
        e.preventDefault();
        alert("Please select a player for every slot.");
        return;
      }

      // check all distinct
      if (new Set(ids).size !== 4) {
        e.preventDefault();
        alert("Each player can only appear once. Please select four different players.");
      }
    });
  }

  // ── Radio button visual toggle ──────────────────────────────────────────
  function attachRadioToggle() {
    const radios = document.querySelectorAll(".radio-option input[type='radio']");
    radios.forEach((radio) => {
      radio.addEventListener("change", () => {
        // Force repaint for :has() polyfill environments
        document.querySelectorAll(".radio-option").forEach((el) => {
          el.classList.remove("checked");
        });
        if (radio.checked) {
          radio.closest(".radio-option").classList.add("checked");
        }
      });
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    loadPlayerDropdowns();
    attachGameFormValidation();
    attachRadioToggle();
  });
})();
