/* ==========================================================================
   CAFirm — main.js
   Global behaviours: flash auto-dismiss, confirmation forms, password toggle,
   sidebar active state.
   ========================================================================== */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {

    /* Auto-dismiss flash messages after 6 seconds. */
    var alerts = document.querySelectorAll(".flash-area .alert");
    Array.prototype.forEach.call(alerts, function (alertEl) {
      setTimeout(function () {
        try {
          bootstrap.Alert.getOrCreateInstance(alertEl).close();
        } catch (e) {
          alertEl.classList.add("d-none");
        }
      }, 6000);
    });

    /* Confirmation prompts for destructive forms. */
    Array.prototype.forEach.call(document.querySelectorAll(".form-confirm"), function (form) {
      form.addEventListener("submit", function (event) {
        var message = form.getAttribute("data-confirm") || "Are you sure?";
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });

    /* Show/hide password on login. */
    var toggle = document.getElementById("togglePassword");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var target = document.getElementById(toggle.getAttribute("data-target"));
        if (!target) return;
        var isHidden = target.type === "password";
        target.type = isHidden ? "text" : "password";
        toggle.innerHTML = isHidden
          ? '<i class="bi bi-eye-slash"></i>'
          : '<i class="bi bi-eye"></i>';
      });
    }

    /* Highlight the active sidebar link. */
    var path = window.location.pathname;
    Array.prototype.forEach.call(document.querySelectorAll(".sidebar-link"), function (link) {
      var href = link.getAttribute("href");
      if (href && !href.startsWith("#")) {
        var url = new URL(href, window.location.origin);
        if (url.pathname === path) {
          link.classList.add("active");
        }
      }
    });

    /* Auto-apply filter panels: selects and date inputs refresh instantly;
       Enter inside the search box applies the text filter immediately. */
    Array.prototype.forEach.call(document.querySelectorAll("form.filter-auto"), function (form) {
      form.addEventListener("change", function (event) {
        var el = event.target;
        if (el.tagName === "SELECT" || el.type === "date") {
          form.submit();
        }
      });
      form.addEventListener("keydown", function (event) {
        var el = event.target;
        if (event.key === "Enter" && el.tagName === "INPUT" && el.type === "text") {
          event.preventDefault();
          form.submit();
        }
      });
    });

    /* Reset collapsed mobile sidebar when resizing to desktop. */
    window.addEventListener("resize", function () {
      if (window.innerWidth >= 992) {
        var mobileSidebar = document.getElementById("mobileSidebar");
        if (mobileSidebar && mobileSidebar.classList.contains("show")) {
          try { bootstrap.Collapse.getOrCreateInstance(mobileSidebar).hide(); } catch (e) { /* noop */ }
        }
      }
    });
  });
})();
