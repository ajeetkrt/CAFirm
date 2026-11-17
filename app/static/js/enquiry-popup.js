/* ==========================================================================
   CAFirm — enquiry popup for the dashboard
   Rows marked with data-enquiry-popup open their enquiry inside the shared
   #enquiryModal: full details + status first, with Update Status and Edit
   Details available right in the popup — no navigation needed. Forms post
   via fetch; validation failures stay in the popup with every typed value
   preserved. Delegated listeners keep working after every fragment swap.
   ========================================================================== */
(function () {
  "use strict";

  var modalEl = document.getElementById("enquiryModal");
  if (!modalEl) return;

  var instance = null;
  var needsRefresh = false;

  function modal() {
    if (!instance && window.bootstrap) {
      instance = bootstrap.Modal.getOrCreateInstance(modalEl);
    }
    return instance;
  }

  function content() {
    return modalEl.querySelector(".modal-content");
  }

  function ajaxHeaders() {
    return { "X-Requested-With": "XMLHttpRequest" };
  }

  function rememberEnquiry() {
    var node = content();
    if (node && node.dataset.enquiryId) {
      modalEl.dataset.enquiryId = node.dataset.enquiryId;
    }
  }

  function inject(html) {
    content().outerHTML = html;
    rememberEnquiry();
  }

  function loadingHTML() {
    return '<div class="modal-content"><div class="modal-body text-center py-5">' +
      '<div class="spinner-border text-success" role="status"></div>' +
      '<div class="small text-muted mt-2">Loading enquiry…</div></div></div>';
  }

  function errorHTML() {
    return '<div class="modal-content"><div class="modal-body text-center py-4">' +
      '<div class="alert alert-danger mb-3"><i class="bi bi-wifi-off"></i> ' +
      'Could not load this enquiry. Check your connection and try again.</div>' +
      '<button type="button" class="btn btn-sm btn-outline-secondary px-3" data-bs-dismiss="modal">Close</button>' +
      '</div></div>';
  }

  function request(url, options) {
    return fetch(url, options).then(function (res) {
      return res.text().then(function (html) {
        return { ok: res.ok, status: res.status, html: html };
      });
    });
  }

  function openPopup(enquiryId, view, withEdit) {
    modal().show();
    content().innerHTML = loadingHTML();
    modalEl.dataset.enquiryId = enquiryId;
    if (withEdit) modalEl.dataset.popupEdit = "1";
    else delete modalEl.dataset.popupEdit;
    var url = "/enquiries/" + enquiryId + "/popup?view=" + (view || "details") +
              (withEdit ? "&edit=1" : "");
    request(url, { headers: ajaxHeaders() })
      .then(function (res) {
        if (!res.ok) throw new Error("popup " + res.status);
        inject(res.html);
      })
      .catch(function () {
        content().outerHTML = errorHTML();
      });
  }

  /* --- Row triggers: plain left-click opens the popup; ctrl/middle-click
         and the real href keep working for the full detail page. A click on
         an [data-enquiry-popup-edit] element (the Enquiry No. link) opens the
         popup with the "Edit Enquiry" action in the footer. ---------------- */
  document.addEventListener("click", function (event) {
    if (event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey) return;
    var trigger = event.target.closest("[data-enquiry-popup]");
    if (!trigger) return;
    event.preventDefault();
    needsRefresh = false;
    var withEdit = !!event.target.closest("[data-enquiry-popup-edit]");
    openPopup(trigger.getAttribute("data-enquiry-popup"), "details", withEdit);
  });

  /* --- Footer swaps: details → status form / edit form / back. The edit
         flag travels with the popup so "Back" restores the same footer. -- */
  modalEl.addEventListener("click", function (event) {
    var swapper = event.target.closest("[data-popup-view]");
    if (!swapper) return;
    var enquiryId = modalEl.dataset.enquiryId;
    if (enquiryId) {
      openPopup(enquiryId, swapper.getAttribute("data-popup-view"),
                modalEl.dataset.popupEdit === "1");
    }
  });

  /* --- Popup forms (status / edit): AJAX posts that never leave the page.
         200 → refreshed details fragment; 400 → form fragment with errors. */
  modalEl.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-popup-form]");
    if (!form) return;
    event.preventDefault();

    var submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;

    request(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: ajaxHeaders()
    }).then(function (res) {
      inject(res.html);           /* both outcomes return a full modal-content */
      if (res.ok) needsRefresh = true;   /* rows/KPIs refresh when popup closes */
    }).catch(function () {
      if (submit) submit.disabled = false;
    });
  });

  /* --- After a successful update, refresh once so the dashboard rows,
         badges and KPI tiles reflect the change. -------------------------- */
  modalEl.addEventListener("hidden.bs.modal", function () {
    if (needsRefresh) {
      needsRefresh = false;
      window.location.reload();
    }
  });
})();
