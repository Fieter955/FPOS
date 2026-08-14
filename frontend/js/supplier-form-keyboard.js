(function setupSupplierFormKeyboardNavigation() {
  "use strict";

  const SUPPLIER_FORM_SELECTOR = "form#supForm";
  const FORM_CONTROL_SELECTOR = [
    "input:not([type='hidden']):not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "button[type='submit']:not([disabled])",
  ].join(",");

  function isVisible(element) {
    if (!(element instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(element);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      element.getClientRects().length > 0
    );
  }

  function getFormControls(form) {
    return Array.from(form.querySelectorAll(FORM_CONTROL_SELECTOR)).filter(
      (control) => control.getAttribute("tabindex") !== "-1" && isVisible(control),
    );
  }

  function focusControl(control) {
    if (!control) return false;
    control.focus();
    if (
      control instanceof HTMLInputElement &&
      ["", "text", "number", "search", "tel", "url", "email"].includes(
        control.type,
      )
    ) {
      try {
        control.select();
      } catch (_) {}
    }
    return true;
  }

  function validateControl(control) {
    if (typeof control.checkValidity !== "function" || control.checkValidity()) {
      return true;
    }
    control.reportValidity?.();
    return false;
  }

  function focusSubmitButton(form) {
    const submitButton = form.querySelector("button[type='submit']:not([disabled])");
    return isVisible(submitButton) && focusControl(submitButton);
  }

  function chooseFirstItemResult(form, input) {
    // Kedua halaman supplier memiliki renderer masing-masing. Panggil langsung agar
    // Enter yang ditekan sebelum debounce selesai tetap memakai hasil terbaru.
    if (typeof window.renderItemDropdown === "function") {
      window.renderItemDropdown(input.value);
    }

    const dropdown = form.querySelector("#itemDropdown");
    const firstResult = dropdown?.querySelector(".item-dropdown-item");
    if (!dropdown?.classList.contains("show") || !firstResult) return false;

    const addButton = firstResult.querySelector(
      ".item-add-btn, button[type='button']",
    );
    (addButton || firstResult).click();

    // Timer pencarian lama mungkin masih berjalan dan membuka dropdown kembali.
    setTimeout(() => dropdown.classList.remove("show"), 350);
    focusSubmitButton(form);
    return true;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.defaultPrevented) {
      return;
    }

    const target = event.target;
    if (
      !(target instanceof HTMLElement) ||
      !target.matches("input, select, textarea")
    ) {
      return;
    }

    const form = target.closest(SUPPLIER_FORM_SELECTOR);
    if (!form) return;

    // Enter tetap berfungsi sebagai baris baru pada Alamat dan Catatan.
    if (target instanceof HTMLTextAreaElement) return;

    event.preventDefault();
    event.stopPropagation();

    // Menahan tombol tidak boleh melewati beberapa field hingga menyimpan tanpa sengaja.
    if (event.repeat || !validateControl(target)) return;

    if (target.id === "itemSearchInput" && !event.shiftKey) {
      if (!chooseFirstItemResult(form, target)) focusSubmitButton(form);
      return;
    }

    const controls = getFormControls(form);
    const currentIndex = controls.indexOf(target);
    if (currentIndex < 0) return;
    focusControl(controls[currentIndex + (event.shiftKey ? -1 : 1)]);
  });
})();
