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

  // ── Player modal (Elo history + Matchups tabs) ─────────────────────────
  function initPlayerModal() {
    const buttons = document.querySelectorAll("[data-player-id]");
    if (!buttons.length) return;

    const modal = document.getElementById("elo-modal");
    const title = document.getElementById("elo-modal-title");
    const closeBtn = document.getElementById("elo-modal-close");
    const canvas = document.getElementById("elo-chart");
    const emptyMsg = document.getElementById("elo-chart-empty");
    const tabElo = document.getElementById("tab-elo");
    const tabMatchups = document.getElementById("tab-matchups");
    const matchupLoading = document.getElementById("matchup-loading");
    const matchupContent = document.getElementById("matchup-content");
    const matchupError = document.getElementById("matchup-error");
    const h2hTbody = document.getElementById("h2h-tbody");
    const h2hEmpty = document.getElementById("h2h-empty");
    const h2hTable = document.getElementById("h2h-table");
    const partnerTbody = document.getElementById("partner-tbody");
    const partnerEmpty = document.getElementById("partner-empty");
    const partnerTable = document.getElementById("partner-table");
    const tabBtns = document.querySelectorAll(".modal-tab");

    let chartInstance = null;
    let currentPlayerId = null;
    let matchupFetched = false;

    function activateTab(name) {
      tabBtns.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
      tabElo.hidden = name !== "elo";
      tabMatchups.hidden = name !== "matchups";
      if (name === "matchups" && !matchupFetched) {
        loadMatchups(currentPlayerId);
      }
    }

    function renderMatchupTable(rows, tbody, table, emptyEl) {
      tbody.innerHTML = "";
      if (!rows.length) {
        table.hidden = true;
        emptyEl.hidden = false;
        return;
      }
      table.hidden = false;
      emptyEl.hidden = true;
      rows.forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          `<td>${r.name}</td><td class="win-stat">${r.wins}</td>` +
          `<td class="loss-stat">${r.losses}</td><td>${r.win_pct}%</td>`;
        tbody.appendChild(tr);
      });
    }

    function loadMatchups(playerId) {
      matchupFetched = true;
      matchupLoading.hidden = false;
      matchupContent.hidden = true;
      matchupError.hidden = true;

      fetch("/api/players/" + playerId + "/matchups")
        .then((r) => r.json())
        .then((data) => {
          matchupLoading.hidden = true;
          matchupContent.hidden = false;
          renderMatchupTable(data.h2h, h2hTbody, h2hTable, h2hEmpty);
          renderMatchupTable(data.partners, partnerTbody, partnerTable, partnerEmpty);
          const avgOpponent = data.avg_opponent_elo ?? "—";
          const avgTeammate = data.avg_teammate_elo ?? "—";
          const avgElosDiv = document.getElementById("matchup-avg-elos");
          avgElosDiv.innerHTML =
            `<div class="avg-elo-row"><span>Avg Opponent Elo</span><span>${avgOpponent}</span></div>` +
            `<div class="avg-elo-row"><span>Avg Teammate Elo</span><span>${avgTeammate}</span></div>`;
          avgElosDiv.hidden = false;
        })
        .catch(() => {
          matchupLoading.hidden = true;
          matchupError.hidden = false;
        });
    }

    function loadEloChart(playerId) {
      if (chartInstance) {
        chartInstance.destroy();
        chartInstance = null;
      }
      canvas.hidden = true;
      emptyMsg.hidden = true;

      fetch("/api/players/" + playerId + "/elo-history")
        .then((r) => r.json())
        .then((points) => {
          if (points.length === 0) {
            emptyMsg.hidden = false;
            return;
          }
          canvas.hidden = false;
          const labels = points.map((p, i) => (i === 0 ? "Start" : "Game " + i));
          const data = points.map((p) => Math.round(p.elo * 10) / 10);

          chartInstance = new Chart(canvas, {
            type: "line",
            data: {
              labels,
              datasets: [{
                label: "Elo",
                data,
                borderColor: "#e94560",
                backgroundColor: "rgba(233,69,96,0.08)",
                pointBackgroundColor: "#e94560",
                pointRadius: 4,
                tension: 0.3,
                fill: false,
              }],
            },
            options: {
              responsive: true,
              plugins: {
                legend: { display: false },
                tooltip: {
                  callbacks: {
                    label: (ctx) => " Elo: " + ctx.parsed.y.toFixed(1),
                  },
                },
              },
              scales: {
                x: {
                  ticks: { color: "#9090b0" },
                  grid: { color: "#2e2e50" },
                },
                y: {
                  ticks: { color: "#9090b0" },
                  grid: { color: "#2e2e50" },
                },
              },
            },
          });
        })
        .catch(() => {
          emptyMsg.textContent = "Failed to load data.";
          emptyMsg.hidden = false;
        });
    }

    function openModal(playerId, playerName) {
      currentPlayerId = playerId;
      matchupFetched = false;
      title.textContent = playerName;
      modal.hidden = false;
      activateTab("elo");
      loadEloChart(playerId);
    }

    function closeModal() {
      modal.hidden = true;
      if (chartInstance) {
        chartInstance.destroy();
        chartInstance = null;
      }
    }

    tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        openModal(btn.dataset.playerId, btn.dataset.playerName);
      });
    });

    closeBtn.addEventListener("click", closeModal);

    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  // ── Hamburger nav toggle ────────────────────────────────────────────────
  function initHamburger() {
    const hamburger = document.getElementById('nav-hamburger');
    const navLinks = document.getElementById('nav-links');
    if (!hamburger || !navLinks) return;
    hamburger.addEventListener('click', () => navLinks.classList.toggle('open'));
    navLinks.querySelectorAll('a').forEach(a => a.addEventListener('click', () => navLinks.classList.remove('open')));
  }

  // ── Init ───────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    initPlayerCombos();
    attachGameFormValidation();
    attachRadioToggle();
    initPlayerModal();
    initHamburger();
  });
})();
