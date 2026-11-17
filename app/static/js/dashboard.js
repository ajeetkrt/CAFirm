/* ==========================================================================
   CAFirm — dashboard.js
   Renders Chart.js charts from JSON data blocks embedded in the page
   (type="application/json", never executable inline JavaScript).
   ========================================================================== */
(function () {
  "use strict";

  var STATUS_COLORS = {
    "New": "#0288d1",
    "Pending": "#f9a825",
    "Assigned": "#8e24aa",
    "In Progress": "#7cb342",
    "Resolved": "#2e7d32",
    "Closed": "#546e7a",
    "Rejected": "#c62828",
    "Reopened": "#ef6c00"
  };
  var PALETTE = ["#1b5e20", "#0288d1", "#f9a825", "#8e24aa", "#00897b", "#546e7a",
                 "#43a047", "#ef6c00", "#4fc3f7", "#c62828"];

  function readJson(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      return null;
    }
  }

  function colors(count) {
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push(PALETTE[i % PALETTE.length]);
    }
    return out;
  }

  /* Shared Chart.js look & feel */
  function applyDefaults() {
    Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
    Chart.defaults.font.size = 11;
    Chart.defaults.color = "#607d8b";
    Chart.defaults.borderColor = "rgba(0,0,0,.07)";
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.legend.labels.boxHeight = 8;
    Chart.defaults.plugins.tooltip.backgroundColor = "#1b5e20";
    Chart.defaults.plugins.tooltip.titleFont = { weight: "700" };
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.displayColors = true;
    Chart.defaults.plugins.tooltip.boxPadding = 4;
  }

  function donutOptions(legendPosition) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { position: legendPosition || "right" }
      }
    };
  }

  function donut(canvas, labels, counts, colorList) {
    new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [{
          data: counts,
          backgroundColor: colorList,
          borderWidth: 2,
          borderColor: "#fff",
          borderRadius: 5,
          spacing: 2,
          hoverOffset: 8
        }]
      },
      options: donutOptions("right")
    });
  }

  /* Vertical gradient fill under a line chart. Chart.js passes a scriptable
     context (NOT the raw canvas ctx) — read the real context via .chart. */
  function areaGradient(context) {
    var chart = context.chart;
    var area = chart.chartArea;
    if (!area) return "rgba(46, 125, 50, .16)"; // fallback while layout runs
    var g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, "rgba(46, 125, 50, .30)");
    g.addColorStop(1, "rgba(46, 125, 50, 0)");
    return g;
  }

  /* Render one chart in isolation — a failure must not cascade to later charts */
  function attempt(render) {
    try { render(); } catch (err) {
      if (window.console) console.error("Chart render failed:", err);
    }
  }

  /* Intentional empty state — an empty chart card says so instead of
     staying a blank box (canvas hidden, styled message overlaid). */
  function chartEmpty(canvas, message, icon) {
    if (!canvas || canvas.dataset.emptyApplied) return;
    canvas.style.display = "none";
    canvas.dataset.emptyApplied = "1";
    var wrap = canvas.parentElement;
    if (!wrap) return;
    var note = document.createElement("div");
    note.className = "chart-empty";
    var dot = document.createElement("i");
    dot.className = "bi " + (icon || "bi-inbox");
    var text = document.createElement("span");
    text.textContent = message;
    note.appendChild(dot);
    note.appendChild(text);
    wrap.appendChild(note);
  }

  function renderCharts() {
    if (typeof Chart === "undefined") return;
    applyDefaults();

    /* Status doughnut */
    attempt(function () {
      var canvas = document.getElementById("statusChart");
      var statusData = readJson("chartStatus");
      if (canvas && statusData && statusData.labels && statusData.labels.length) {
        var bg = statusData.labels.map(function (label) { return STATUS_COLORS[label] || PALETTE[0]; });
        donut(canvas, statusData.labels, statusData.counts, bg);
      } else if (canvas) {
        chartEmpty(canvas, "No enquiries yet", "bi-inbox");
      }
    });

    /* Category doughnut */
    attempt(function () {
      var canvas = document.getElementById("categoryChart");
      var categoryData = readJson("chartCategory");
      if (canvas && categoryData && categoryData[0] && categoryData[0].length) {
        donut(canvas, categoryData[0], categoryData[1], colors(categoryData[0].length));
      } else if (canvas) {
        chartEmpty(canvas, "No enquiries yet", "bi-inbox");
      }
    });

    /* Source doughnut */
    attempt(function () {
      var canvas = document.getElementById("sourceChart");
      var sourceData = readJson("chartSource");
      if (canvas && sourceData && sourceData[0] && sourceData[0].length) {
        donut(canvas, sourceData[0], sourceData[1], colors(sourceData[0].length));
      } else if (canvas) {
        chartEmpty(canvas, "No enquiries yet", "bi-inbox");
      }
    });

    /* Monthly trend line — an all-zero window is shown as an intentional
       empty state rather than a flat line hugging the bottom edge. */
    attempt(function () {
      var canvas = document.getElementById("trendChart");
      var trendData = readJson("chartTrend");
      if (!canvas) return;
      var hasData = !!(trendData && trendData.labels && trendData.labels.length &&
                       trendData.counts && trendData.counts.some(function (n) {
                         return Number(n) > 0;
                       }));
      if (!hasData) {
        chartEmpty(canvas, "No enquiries in the last 6 months", "bi-graph-up-arrow");
        return;
      }
      new Chart(canvas, {
        type: "line",
        data: {
          labels: trendData.labels,
          datasets: [{
            label: "Enquiries",
            data: trendData.counts,
            borderColor: "#1b5e20",
            borderWidth: 2.5,
            backgroundColor: areaGradient,
            fill: true,
            tension: 0.35,
            pointRadius: 3.5,
            pointHoverRadius: 6,
            pointBackgroundColor: "#f9a825",
            pointBorderColor: "#fff",
            pointBorderWidth: 1.5,
            pointHoverBorderWidth: 2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (c) {
                  return " " + c.parsed.y + (c.parsed.y === 1 ? " enquiry" : " enquiries");
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: false } },
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { borderDash: [4, 4] } }
          }
        }
      });
    });

  }

  /* Widget pagers: 4 rows per page, up to 4 pages (4×4) — the widgets stay
     one fixed height and staff browse through rows without a page reload. */
  function initWidgetPagers() {
    var PAGE_SIZE = 4, MAX_PAGES = 4;
    document.querySelectorAll("[data-wpager]").forEach(function (pager) {
      var card = pager.closest(".card");
      var body = card ? card.querySelector(".widget-body") : null;
      var rows = body ? Array.prototype.slice.call(body.querySelectorAll(".wrow")) : [];
      var pages = Math.min(Math.ceil(rows.length / PAGE_SIZE), MAX_PAGES);
      if (!rows.length || pages <= 1) { pager.classList.add("d-none"); return; }

      var ind = pager.querySelector(".wpager-ind b");
      var total = pager.querySelector(".wpager-total");
      var prev = pager.querySelector('[data-dir="-1"]');
      var next = pager.querySelector('[data-dir="1"]');
      if (total) total.textContent = pages;

      var page = 1;
      function render() {
        var visible = [];
        rows.forEach(function (row, i) {
          var show = i >= (page - 1) * PAGE_SIZE && i < page * PAGE_SIZE;
          row.style.display = show ? "" : "none";
          if (show) visible.push(i);
        });
        visible.forEach(function (idx, k) {
          // First row of every page has no divider line above it.
          rows[idx].style.borderTopWidth = k === 0 ? "0" : "";
        });
        if (ind) ind.textContent = page;
        if (prev) prev.disabled = page <= 1;
        if (next) next.disabled = page >= pages;
      }
      if (prev) prev.addEventListener("click", function () { if (page > 1) { page--; render(); } });
      if (next) next.addEventListener("click", function () { if (page < pages) { page++; render(); } });
      render();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      renderCharts();
      initWidgetPagers();
    });
  } else {
    renderCharts();
    initWidgetPagers();
  }
})();
