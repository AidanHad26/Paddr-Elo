/* Paddr Elo — app.js */

(function () {
  "use strict";

  // ── Player typeahead combos on the Record Game page ────────────────────
  async function initPlayerCombos() {
    const combos = document.querySelectorAll("[data-player-combo]");
    if (!combos.length) return;

    let players;
    try {
      const resp = await fetch("/api/players");
      players = await resp.json();
    } catch (e) {
      console.error("Failed to load players:", e);
      return;
    }

    combos.forEach((combo) => {
      const textInput = combo.querySelector(".player-search-input");
      const hiddenInput = combo.querySelector('input[type="hidden"]');
      const list = combo.querySelector(".player-dropdown");

      function renderList(filtered) {
        list.innerHTML = "";
        filtered.forEach((p) => {
          const li = document.createElement("li");
          li.textContent = `${p.name}  (${p.elo.toFixed(1)})`;
          li.dataset.id = p.id;
          li.dataset.name = p.name;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault(); // keep focus on textInput
            hiddenInput.value = p.id;
            textInput.value = p.name;
            list.hidden = true;
          });
          list.appendChild(li);
        });
        list.hidden = filtered.length === 0;
      }

      textInput.addEventListener("input", () => {
        hiddenInput.value = ""; // clear selection when typing
        const q = textInput.value.toLowerCase();
        renderList(q ? players.filter((p) => p.name.toLowerCase().includes(q)) : players);
      });

      textInput.addEventListener("focus", () => {
        const q = textInput.value.toLowerCase();
        renderList(q ? players.filter((p) => p.name.toLowerCase().includes(q)) : players);
      });

      textInput.addEventListener("blur", () => {
        // Small delay so mousedown on list item fires first
        setTimeout(() => {
          list.hidden = true;
          // If text doesn't match a confirmed selection, clear both
          if (!hiddenInput.value) textInput.value = "";
        }, 150);
      });
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
    initPlayerCombos();
    attachGameFormValidation();
    attachRadioToggle();
  });
})();
