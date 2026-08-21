(function (global) {
  function normalizeBarcodeSearchValue(value) {
    return String(value ?? "").trim().toLocaleLowerCase();
  }

  function getItemBarcodeSearchValues(item) {
    return [
      item?.code,
      item?.barcode,
      ...(Array.isArray(item?.supplier_barcodes) ? item.supplier_barcodes : []),
      ...(Array.isArray(item?.supplier_details)
        ? item.supplier_details.map((detail) => detail?.barcode)
        : []),
    ]
      .map(normalizeBarcodeSearchValue)
      .filter(Boolean);
  }

  function findExactScannedItem(items, value) {
    const scanned = normalizeBarcodeSearchValue(value);
    if (!scanned) return null;
    return (
      (items || []).find((item) =>
        getItemBarcodeSearchValues(item).includes(scanned),
      ) || null
    );
  }

  // Scanner keyboard biasanya mengirim karakter cepat lalu Enter. Tangkap pada
  // fase capture supaya handler form/debounce tidak sempat memperlakukannya
  // sebagai submit biasa.
  function setupBarcodeScanner(onScan, config = {}) {
    const minLength = config.minLength ?? 2;
    const interval = config.interval ?? 60;
    let buffer = "";
    let lastKeyTime = 0;

    const reset = () => {
      buffer = "";
      lastKeyTime = 0;
    };

    const handleKeydown = (event) => {
      if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.defaultPrevented) {
        reset();
        return;
      }

      if (event.key === "Enter") {
        const scanned = buffer.trim();
        const completedQuickly =
          lastKeyTime > 0 && Date.now() - lastKeyTime <= interval * 4;
        reset();
        if (scanned.length < minLength || !completedQuickly) return;

        event.preventDefault();
        event.stopImmediatePropagation();
        const target = event.target;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
          target.value = "";
        }
        onScan?.(scanned, event);
        return;
      }

      if (typeof event.key !== "string" || event.key.length !== 1) return;
      const now = Date.now();
      if (!lastKeyTime || now - lastKeyTime > interval) buffer = "";
      buffer += event.key;
      lastKeyTime = now;
    };

    document.addEventListener("keydown", handleKeydown, true);
    return () => document.removeEventListener("keydown", handleKeydown, true);
  }

  global.normalizeBarcodeSearchValue = normalizeBarcodeSearchValue;
  global.getItemBarcodeSearchValues = getItemBarcodeSearchValues;
  global.findExactScannedItem = findExactScannedItem;
  global.setupBarcodeScanner = setupBarcodeScanner;
})(window);
