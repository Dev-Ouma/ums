/**
 * Unsaved Changes Guard
 * Opt-in: add data-unsaved-guard to any <form> to warn the user before
 * navigating away with unsaved edits. Dirty state clears on submit.
 */
(function () {
  function watchForm(form) {
    let dirty = false;
    const markDirty = () => { dirty = true; };

    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);

    form.addEventListener("submit", () => { dirty = false; });

    window.addEventListener("beforeunload", (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });

    // Cancel/back links inside the same page should also confirm.
    document.querySelectorAll("a[data-unsaved-exit]").forEach((link) => {
      link.addEventListener("click", (event) => {
        if (!dirty) return;
        const proceed = confirm("You have unsaved changes. Leave this page without saving?");
        if (!proceed) event.preventDefault();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-unsaved-guard]").forEach(watchForm);
  });
})();
