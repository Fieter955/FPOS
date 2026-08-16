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
    const firstResult = dropdown?.querySelector(
      ".item-dropdown-item.keyboard-highlight, .item-dropdown-item",
    );
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

  function getItemDropdownOptions(form) {
    return Array.from(
      form?.querySelectorAll(
        "#itemDropdown.item-dropdown.show .item-dropdown-item",
      ) || [],
    );
  }

  function highlightItemDropdownOption(input, options, nextIndex) {
    if (!options.length) {
      input.dataset.itemDropdownIndex = "-1";
      return;
    }

    const index = (nextIndex + options.length) % options.length;
    input.dataset.itemDropdownIndex = String(index);
    options.forEach((option, optionIndex) => {
      const active = optionIndex === index;
      option.classList.toggle("keyboard-highlight", active);
      option.classList.toggle("highlight", active);
      option.setAttribute("aria-selected", String(active));
    });
    options[index].scrollIntoView({ block: "nearest" });
  }

  function handleSupplierItemDropdownKeys(event, form, input) {
    const dropdown = form.querySelector("#itemDropdown");
    if (!dropdown) return false;

    const isNextKey = event.key === "ArrowDown" || event.key === "ArrowRight";
    const isPreviousKey =
      event.key === "ArrowUp" || event.key === "ArrowLeft";
    const isDropdownOpen = dropdown.classList.contains("show");

    if (isNextKey || isPreviousKey) {
      if (!isDropdownOpen && typeof window.renderItemDropdown === "function") {
        window.renderItemDropdown(input.value);
      }
      const options = getItemDropdownOptions(form);
      if (!options.length) return false;
      event.preventDefault();
      event.stopPropagation();
      const currentIndex = Number(input.dataset.itemDropdownIndex || -1);
      highlightItemDropdownOption(
        input,
        options,
        currentIndex + (isNextKey ? 1 : -1),
      );
      return true;
    }

    if (event.key === "Tab" && isDropdownOpen) {
      const options = getItemDropdownOptions(form);
      const currentIndex = Number(input.dataset.itemDropdownIndex || -1);
      const selected = options[currentIndex];
      if (selected) {
        const addButton = selected.querySelector(
          ".item-add-btn, button[type='button']",
        );
        (addButton || selected).click();
      } else {
        dropdown.classList.remove("show");
      }
      return false;
    }

    if (event.key === "Escape" && isDropdownOpen) {
      event.preventDefault();
      dropdown.classList.remove("show");
      input.dataset.itemDropdownIndex = "-1";
      return true;
    }

    return false;
  }

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (
      !(target instanceof HTMLElement) ||
      target.id !== "itemSearchInput"
    ) {
      return;
    }
    const form = target.closest(SUPPLIER_FORM_SELECTOR);
    if (form) handleSupplierItemDropdownKeys(event, form, target);
  });

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
