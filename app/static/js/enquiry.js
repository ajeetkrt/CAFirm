/* ==========================================================================
   CAFirm — enquiry.js
   Enquiry form helpers: live character counter, copy tracking details.
   ========================================================================== */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {

    /* Live character counter for description textareas. */
    var counters = document.querySelectorAll("[data-charcount]");
    Array.prototype.forEach.call(counters, function (field) {
      var target = document.getElementById(field.getAttribute("data-charcount"));
      if (!target) return;
      var update = function () {
        target.textContent = String(field.value.length);
      };
      field.addEventListener("input", update);
      update();
    });

    /* Copy the Enquiry Number to the clipboard. */
    var copyButton = document.getElementById("copyTracking");
    if (copyButton) {
      copyButton.addEventListener("click", function () {
        var numberEl = document.querySelector(".enquiry-number");
        if (!numberEl) return;
        var text = "Enquiry Number: " + numberEl.textContent.trim();
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            copyButton.innerHTML = '<i class="bi bi-check2"></i> Copied';
          }).catch(function () {
            window.prompt("Copy this value:", text);
          });
        } else {
          window.prompt("Copy this value:", text);
        }
      });
    }
  });
})();
