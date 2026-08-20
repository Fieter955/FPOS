(function () {
  "use strict";

  const MAX_TABS = 10;
  const DASHBOARD_ID = "dashboard";
  const DASHBOARD_URL = "/dashboard";
  const tabsElement = document.getElementById("workspaceTabs");
  const framesElement = document.getElementById("workspaceFrames");
  const tabCountElement = document.getElementById("workspaceTabCount");
  const bootElement = document.getElementById("workspaceBoot");
  const accountElement = document.getElementById("workspaceAccount");
  const accountButton = document.getElementById("workspaceAccountButton");
  const accountMenu = document.getElementById("workspaceAccountMenu");
  const accountAvatar = document.getElementById("workspaceAccountAvatar");
  const accountName = document.getElementById("workspaceAccountName");
  const accountMenuAvatar = document.getElementById(
    "workspaceAccountMenuAvatar",
  );
  const accountMenuName = document.getElementById("workspaceAccountMenuName");
  const accountUsername = document.getElementById("workspaceAccountUsername");
  const logoutButton = document.getElementById("workspaceLogoutButton");

  const allowedPaths = new Set(
    [
      "/dashboard",
      "/pos",
      "/pos_2",
      "/sales",
      "/returns",
      "/customers",
      "/inventory",
      "/reports",
      "/accounting",
      "/shifts",
      "/konsinyasi",
      "/ai_advisor",
      "/settings",
      "/warehouse",
      "/assembly",
      "/discounts",
      "/onboarding",
      "/unit_conversion",
      "/delivery",
      "/trade_in",
      "/ai_bangunan",
      "/branches",
      "/users",
      "/barcode",
      "/po",
      "/setor",
      "/setoran",
      "/purchases",
      "/catat-pembelian",
      "/detail_item",
      "/purchase/purchases",
      "/purchase/catat-pembelian",
      "/purchase/detail_item",
      "/item/dashboard",
      "/item/satuan",
      "/item/levelharga",
      "/item/leveljumlah",
      "/item/items",
      "/item/popup",
      "/item/kategori",
      "/item/units",
      "/item/merek",
      "/item/potonganhargajual",
      "/supplier/dashboard",
      "/supplier/tambahsuplier",
    ].map((path) => path.toLowerCase()),
  );

  const pageTitles = {
    "/dashboard": "Dashboard",
    "/pos": "Kasir",
    "/pos_2": "Kasir",
    "/sales": "Riwayat Jual",
    "/returns": "Retur",
    "/customers": "Pelanggan",
    "/inventory": "Mutasi Stok",
    "/reports": "Laporan",
    "/accounting": "Akuntansi",
    "/shifts": "Shift Kasir",
    "/konsinyasi": "Konsinyasi",
    "/ai_advisor": "AI Advisor",
    "/settings": "Pengaturan",
    "/warehouse": "Multi Gudang",
    "/assembly": "Perakitan",
    "/discounts": "Diskon & Promo",
    "/onboarding": "Onboarding",
    "/unit_conversion": "Konversi Satuan",
    "/delivery": "Surat Jalan",
    "/trade_in": "Tukar Tambah",
    "/ai_bangunan": "AI Bangunan",
    "/branches": "Manajemen Cabang",
    "/users": "Manajemen User",
    "/barcode": "Barcode",
    "/po": "Pre-Order",
    "/setor": "Permintaan Barang",
    "/setoran": "Setoran",
    "/purchases": "Pembelian",
    "/catat-pembelian": "Catat Pembelian",
    "/detail_item": "Detail Pembelian",
    "/purchase/purchases": "Pembelian",
    "/purchase/catat-pembelian": "Catat Pembelian",
    "/purchase/detail_item": "Detail Pembelian",
    "/item/dashboard": "Pengaturan Harga",
    "/item/satuan": "Harga per Satuan",
    "/item/levelharga": "Level Harga",
    "/item/leveljumlah": "Level Jumlah",
    "/item/items": "Data Barang",
    "/item/popup": "Form Barang",
    "/item/kategori": "Kategori",
    "/item/units": "Satuan",
    "/item/merek": "Merek",
    "/item/potonganhargajual": "Potongan Harga",
    "/supplier/dashboard": "Supplier",
    "/supplier/tambahsuplier": "Tambah Supplier",
  };

  const routedTabPaths = new Set([
    "/item/items",
    "/purchases",
    "/purchase/purchases",
    "/inventory",
    "/warehouse",
    "/assembly",
    "/konsinyasi",
    "/reports",
    "/accounting",
    "/settings",
    "/users",
  ]);

  let tabs = [];
  let activeTabId = DASHBOARD_ID;
  let nextTabNumber = Date.now();
  let nextUnsavedRequestNumber = Date.now();
  const pendingUnsavedRequests = new Map();

  function cleanPath(pathname) {
    let path = pathname || "/";
    if (path.length > 1) path = path.replace(/\/$/, "");
    return path.replace(/\.html$/i, "");
  }

  function normalizeTabUrl(rawUrl) {
    try {
      const parsed = new URL(rawUrl, window.location.origin);
      if (parsed.origin !== window.location.origin) return null;
      const path = cleanPath(parsed.pathname);
      if (!allowedPaths.has(path.toLowerCase())) return null;
      return `${path}${parsed.search}${parsed.hash}`;
    } catch {
      return null;
    }
  }

  function titleForUrl(url) {
    const path = cleanPath(new URL(url, location.origin).pathname).toLowerCase();
    return pageTitles[path] || "FPOS";
  }

  function cleanDocumentTitle(title, url) {
    const cleaned = String(title || "")
      .replace(/\s+-\s+(FPOS|iPos\s*5\.0)$/i, "")
      .trim();
    return cleaned || titleForUrl(url);
  }

  function storageKey() {
    const user = getUser();
    const identity = user.id || user.username || "anonymous";
    return `fpos_workspace_tabs:${identity}`;
  }

  function saveState() {
    if (!getToken()) return;
    const serializableTabs = tabs.map(({ id, url, title, isDashboard }) => ({
      id,
      url,
      title,
      isDashboard: Boolean(isDashboard),
    }));
    try {
      localStorage.setItem(
        storageKey(),
        JSON.stringify({ tabs: serializableTabs, activeTabId }),
      );
    } catch {}
  }

  function makeDashboardTab() {
    return {
      id: DASHBOARD_ID,
      url: DASHBOARD_URL,
      title: "Dashboard",
      isDashboard: true,
      loaded: false,
      frame: null,
      element: null,
      hasUnsavedChanges: false,
      unsavedStateKnown: false,
    };
  }

  function restoreState() {
    const dashboard = makeDashboardTab();
    let restored = null;
    let restoredActiveTabId = DASHBOARD_ID;
    try {
      restored = JSON.parse(localStorage.getItem(storageKey()) || "null");
    } catch {}

    const restoredTabs = Array.isArray(restored?.tabs) ? restored.tabs : [];
    const restoredActiveId = restored?.activeTabId;
    const restoredByMenu = new Map();
    tabs = [dashboard];
    for (const saved of restoredTabs) {
      if (saved?.isDashboard || saved?.id === DASHBOARD_ID) {
        continue;
      }
      const url = normalizeTabUrl(saved?.url);
      if (!url) {
        continue;
      }
      const path = cleanPath(new URL(url, location.origin).pathname);
      if (path === DASHBOARD_URL) continue;

      // Tab lama mungkin sudah menyimpan duplikat sebelum aturan satu-menu
      // diterapkan. Pertahankan tab pertama dan arahkan state aktif ke sana.
      const menuKey = path.toLowerCase();
      const existing = restoredByMenu.get(menuKey);
      if (existing) {
        if (saved?.id === restoredActiveId) restoredActiveTabId = existing.id;
        continue;
      }
      if (tabs.length >= MAX_TABS) continue;

      const restoredTab = {
        id: `tab-${nextTabNumber++}`,
        url,
        title: cleanDocumentTitle(saved?.title, url),
        isDashboard: false,
        loaded: false,
        frame: null,
        element: null,
        hasUnsavedChanges: false,
        unsavedStateKnown: false,
      };
      tabs.push(restoredTab);
      restoredByMenu.set(menuKey, restoredTab);
      if (saved?.id === restoredActiveId) {
        restoredActiveTabId = restoredTab.id;
      }
    }
    activeTabId = restoredActiveTabId;
  }

  function createTabElement(tab) {
    const element = document.createElement("div");
    element.className = "workspace-tab";
    element.dataset.tabId = tab.id;
    element.setAttribute("role", "tab");
    element.setAttribute("tabindex", "0");

    const icon = document.createElement("span");
    icon.className = "workspace-tab-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = tab.isDashboard ? "⌂" : "●";

    const label = document.createElement("span");
    label.className = "workspace-tab-label";
    label.textContent = tab.title;

    element.append(icon, label);
    if (!tab.isDashboard) {
      const close = document.createElement("button");
      close.type = "button";
      close.className = "workspace-tab-close";
      close.setAttribute("aria-label", `Tutup tab ${tab.title}`);
      close.title = `Tutup ${tab.title}`;
      close.textContent = "×";
      close.addEventListener("click", (event) => {
        event.stopPropagation();
        requestCloseTab(tab.id);
      });
      element.appendChild(close);
    }

    element.addEventListener("click", () => activateTab(tab.id));
    element.addEventListener("keydown", (event) => {
      if (event.target !== element) return;

      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateTab(tab.id);
        return;
      }

      if (
        !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
          event.key,
        )
      ) {
        return;
      }

      const currentIndex = tabs.indexOf(tab);
      if (currentIndex < 0) return;
      const direction = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
      const nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
      const nextTab = tabs[nextIndex];
      if (!nextTab?.element) return;
      event.preventDefault();
      activateTab(nextTab.id);
      nextTab.element.focus({ preventScroll: true });
    });
    tabsElement.appendChild(element);
    tab.element = element;
  }

  function createFrame(tab) {
    const frame = document.createElement("iframe");
    frame.className = "workspace-frame";
    frame.dataset.tabId = tab.id;
    frame.title = tab.title;
    frame.setAttribute("loading", "eager");
    frame.setAttribute("allow", "clipboard-read; clipboard-write");
    frame.addEventListener("load", () => handleFrameLoad(tab));
    framesElement.appendChild(frame);
    tab.frame = frame;
  }

  function loadTab(tab) {
    if (!tab || tab.loaded) return;
    tab.loaded = true;
    tab.frame.src = tab.url;
  }

  function updateTabPresentation(tab) {
    if (!tab?.element) return;
    const label = tab.element.querySelector(".workspace-tab-label");
    const close = tab.element.querySelector(".workspace-tab-close");
    if (label) label.textContent = tab.title;
    if (close) {
      close.title = `Tutup ${tab.title}`;
      close.setAttribute("aria-label", `Tutup tab ${tab.title}`);
    }
    tab.element.title = tab.title;
    if (tab.frame) tab.frame.title = tab.title;
  }

  function renderActiveState() {
    for (const tab of tabs) {
      const active = tab.id === activeTabId;
      tab.element?.classList.toggle("is-active", active);
      tab.element?.setAttribute("aria-selected", String(active));
      tab.element?.setAttribute("tabindex", active ? "0" : "-1");
      tab.frame?.classList.toggle("is-active", active);
    }
    tabCountElement.textContent = `${tabs.length}/${MAX_TABS}`;
  }

  function activateTab(tabId) {
    const tab = tabs.find((item) => item.id === tabId);
    if (!tab) return;
    activeTabId = tab.id;
    loadTab(tab);
    renderActiveState();
    updateTabPresentation(tab);
    tab.element?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    document.title = `${tab.title} - FPOS`;
    saveState();
  }

  function openTab(rawUrl, requestedTitle = "") {
    const url = normalizeTabUrl(rawUrl);
    if (!url) {
      showToast("Halaman tersebut tidak dapat dibuka di tab FPOS.", "error");
      return null;
    }
    if (cleanPath(new URL(url, location.origin).pathname) === DASHBOARD_URL) {
      activateTab(DASHBOARD_ID);
      return tabs[0];
    }
    const requestedPath = cleanPath(
      new URL(url, location.origin).pathname,
    ).toLowerCase();
    const existing = tabs.find((tab) => {
      if (tab.url === url) return true;
      if (tab.isDashboard) return false;
      return (
        cleanPath(new URL(tab.url, location.origin).pathname).toLowerCase() ===
        requestedPath
      );
    });
    if (existing) {
      const routeChanged =
        existing.url !== url && routedTabPaths.has(requestedPath);
      if (routeChanged) {
        existing.url = url;
        existing.title = cleanDocumentTitle(requestedTitle, url);
        updateTabPresentation(existing);
      }
      activateTab(existing.id);
      if (routeChanged && existing.loaded && existing.frame?.contentWindow) {
        existing.frame.contentWindow.postMessage(
          { type: "fpos-route-state", url },
          location.origin,
        );
        saveState();
      }
      return existing;
    }
    if (tabs.length >= MAX_TABS) {
      showToast(
        `Maksimal ${MAX_TABS} tab termasuk Dashboard. Tutup salah satu tab terlebih dahulu.`,
        "warning",
      );
      return null;
    }

    const tab = {
      id: `tab-${nextTabNumber++}`,
      url,
      title: cleanDocumentTitle(requestedTitle, url),
      isDashboard: false,
      loaded: false,
      frame: null,
      element: null,
      hasUnsavedChanges: false,
      unsavedStateKnown: false,
    };
    tabs.push(tab);
    createTabElement(tab);
    createFrame(tab);
    activateTab(tab.id);
    return tab;
  }

  function requestUnsavedAction(tab, type, timeoutMs = 15000) {
    if (!tab?.loaded || !tab.frame?.contentWindow) {
      return Promise.resolve({ success: true, dirty: false });
    }
    const requestId = `unsaved-${nextUnsavedRequestNumber++}`;
    return new Promise((resolve) => {
      const timeout = window.setTimeout(() => {
        pendingUnsavedRequests.delete(requestId);
        resolve({
          success: false,
          dirty: Boolean(tab.hasUnsavedChanges),
          timedOut: true,
        });
      }, timeoutMs);
      pendingUnsavedRequests.set(requestId, {
        source: tab.frame.contentWindow,
        resolve: (response) => {
          window.clearTimeout(timeout);
          resolve(response);
        },
      });
      tab.frame.contentWindow.postMessage({ type, requestId }, location.origin);
    });
  }

  async function refreshTabUnsavedState(tab) {
    const response = await requestUnsavedAction(
      tab,
      "fpos-unsaved-status-request",
      2000,
    );
    if (!response.timedOut) {
      tab.hasUnsavedChanges = Boolean(response.dirty);
      tab.unsavedStateKnown = true;
    }
    return Boolean(tab.hasUnsavedChanges);
  }

  async function saveTabBeforeExit(tab) {
    activateTab(tab.id);
    const response = await requestUnsavedAction(
      tab,
      "fpos-unsaved-save-request",
      60000,
    );
    if (!response.success) {
      showToast(
        `Perubahan pada tab ${tab.title} belum berhasil disimpan.`,
        "error",
      );
      return false;
    }
    tab.hasUnsavedChanges = Boolean(response.dirty);
    return !tab.hasUnsavedChanges;
  }

  async function discardTabChanges(tab) {
    await requestUnsavedAction(
      tab,
      "fpos-unsaved-discard-request",
      5000,
    );
    tab.hasUnsavedChanges = false;
  }

  async function requestCloseTab(tabId) {
    const tab = tabs.find((item) => item.id === tabId);
    if (!tab || tab.isDashboard) return;
    const dirty = await refreshTabUnsavedState(tab);
    if (dirty) {
      const action = await showUnsavedChangesDialog(
        `Perubahan pada tab “${tab.title}” belum disimpan. Apa yang ingin dilakukan?`,
        {
          saveText: "Simpan & Tutup",
          discardText: "Tutup Tanpa Simpan",
        },
      );
      if (action === "cancel") return;
      if (action === "save") {
        if (!(await saveTabBeforeExit(tab))) return;
      } else {
        await discardTabChanges(tab);
      }
      removeTab(tabId);
      return;
    }
    removeTab(tabId);
  }

  function removeTab(tabId, { activateDashboard = false } = {}) {
    const index = tabs.findIndex((item) => item.id === tabId);
    if (index <= 0) return;
    const [tab] = tabs.splice(index, 1);
    tab.element?.remove();
    tab.frame?.remove();

    if (activeTabId === tabId) {
      const replacement = activateDashboard
        ? tabs[0]
        : tabs[Math.max(0, index - 1)] || tabs[0];
      activeTabId = replacement.id;
      activateTab(replacement.id);
    } else {
      renderActiveState();
      saveState();
    }
  }

  function updateTabFromLocation(tab, rawUrl, rawTitle) {
    const url = normalizeTabUrl(rawUrl);
    if (!url) return;
    const path = cleanPath(new URL(url, location.origin).pathname);
    if (!tab.isDashboard && path === DASHBOARD_URL) {
      removeTab(tab.id, { activateDashboard: true });
      activateTab(DASHBOARD_ID);
      return;
    }
    tab.url = tab.isDashboard ? DASHBOARD_URL : url;
    tab.title = tab.isDashboard
      ? "Dashboard"
      : cleanDocumentTitle(rawTitle, url);
    updateTabPresentation(tab);
    if (tab.id === activeTabId) document.title = `${tab.title} - FPOS`;
    saveState();
  }

  function handleFrameLoad(tab) {
    bootElement?.remove();
    const frame = tab?.frame;
    // Iframe baru dapat memancarkan event load sementara untuk about:blank, atau
    // event terlambat setelah tab ditutup. Keduanya bukan navigasi keluar FPOS.
    if (!frame?.isConnected || !frame.contentWindow) return;
    tab.hasUnsavedChanges = false;
    tab.unsavedStateKnown = false;

    // Jangan membaca contentWindow.location di event load. Edge/Chrome dapat
    // melempar SecurityError sementara walaupun halaman masih same-origin, yang
    // sebelumnya memicu toast palsu dan loop reload. Halaman FPOS menyinkronkan
    // URL/judulnya sendiri lewat postMessage `fpos-frame-location`; pesan tersebut
    // tetap diperiksa origin dan allowlist sebelum state tab diperbarui.
  }

  function isKnownFrameSource(source) {
    return tabs.some((tab) => tab.frame?.contentWindow === source);
  }

  function tabFromSource(source) {
    return tabs.find((tab) => tab.frame?.contentWindow === source) || null;
  }

  function handleWorkspaceMessage(event) {
    if (event.origin !== window.location.origin || !isKnownFrameSource(event.source)) {
      return;
    }
    const message = event.data || {};
    if (message.type === "fpos-unsaved-response") {
      const pending = pendingUnsavedRequests.get(message.requestId);
      if (!pending || pending.source !== event.source) return;
      pendingUnsavedRequests.delete(message.requestId);
      pending.resolve(message);
    } else if (message.type === "fpos-unsaved-state") {
      const tab = tabFromSource(event.source);
      if (tab) {
        tab.hasUnsavedChanges = Boolean(message.dirty);
        tab.unsavedStateKnown = true;
      }
    } else if (message.type === "fpos-open-tab") {
      openTab(message.url, message.title);
    } else if (message.type === "fpos-focus-dashboard") {
      activateTab(DASHBOARD_ID);
    } else if (message.type === "fpos-frame-location") {
      const tab = tabFromSource(event.source);
      if (tab) updateTabFromLocation(tab, message.url, message.title);
    } else if (message.type === "fpos-branches-changed") {
      refreshBranchSwitcher({ force: true });
    } else if (message.type === "fpos-auth-expired") {
      if (message.sessionExpired) markSessionExpired();
      window.location.replace("/login");
    } else if (message.type === "fpos-request-logout") {
      logoutCurrentUser();
    }
  }

  async function prepareWorkspaceLogout() {
    const loadedTabs = tabs.filter((tab) => !tab.isDashboard && tab.loaded);
    const dirtyStates = await Promise.all(
      loadedTabs.map(async (tab) => ({
        tab,
        dirty: await refreshTabUnsavedState(tab),
      })),
    );
    const dirtyTabs = dirtyStates
      .filter((entry) => entry.dirty)
      .map((entry) => entry.tab);

    if (!dirtyTabs.length) {
      return showConfirm("Yakin ingin keluar dari akun saat ini?");
    }

    const tabNames = dirtyTabs.map((tab) => `• ${tab.title}`).join("\n");
    const action = await showUnsavedChangesDialog(
      `Ada perubahan yang belum disimpan:\n${tabNames}\n\nApa yang ingin dilakukan sebelum keluar dari akun?`,
      {
        saveText: "Simpan Semua & Keluar",
        discardText: "Keluar Tanpa Simpan",
      },
    );
    if (action === "cancel") return false;
    if (action === "discard") {
      for (const tab of dirtyTabs) await discardTabChanges(tab);
      return true;
    }
    for (const tab of dirtyTabs) {
      if (!(await saveTabBeforeExit(tab))) return false;
    }
    return true;
  }

  window.fposPrepareForLogout = prepareWorkspaceLogout;

  function applyStoredTheme() {
    const isDark = localStorage.getItem("ipos_theme") === "dark";
    document.body.classList.toggle("dark-mode", isDark);
  }

  function initialsForAccount(value) {
    const parts = String(value || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return "A";
    const first = parts[0].charAt(0);
    const last = parts.length > 1 ? parts[parts.length - 1].charAt(0) : "";
    return `${first}${last}`.toLocaleUpperCase("id-ID");
  }

  function populateAccountMenu() {
    const user = getUser();
    const username = String(user.username || "").trim();
    const displayName = String(user.full_name || username || "Pengguna").trim();
    const initials = initialsForAccount(displayName);

    accountAvatar.textContent = initials;
    accountMenuAvatar.textContent = initials;
    accountName.textContent = displayName;
    accountMenuName.textContent = displayName;
    accountUsername.textContent = username ? `@${username}` : "Akun aktif";
    accountButton.title = `Akun: ${displayName}`;
    accountButton.setAttribute("aria-label", `Buka menu akun ${displayName}`);
  }

  function setAccountMenuOpen(open, { returnFocus = false } = {}) {
    const shouldOpen = Boolean(open);
    accountMenu.hidden = !shouldOpen;
    accountButton.setAttribute("aria-expanded", String(shouldOpen));
    accountElement.classList.toggle("is-open", shouldOpen);
    if (returnFocus) accountButton.focus();
  }

  function getAccountMenuControls() {
    return Array.from(
      accountMenu.querySelectorAll(
        "select:not([disabled]), button:not([disabled]), input:not([disabled])",
      ),
    ).filter((control) => {
      const style = window.getComputedStyle(control);
      return (
        !control.hidden &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        control.getClientRects().length > 0
      );
    });
  }

  function focusAccountMenuControl(direction = 1) {
    const controls = getAccountMenuControls();
    if (!controls.length) return false;
    const activeIndex = controls.indexOf(document.activeElement);
    const startIndex =
      activeIndex < 0
        ? direction > 0
          ? 0
          : controls.length - 1
        : activeIndex + direction;
    const nextIndex = (startIndex + controls.length) % controls.length;
    controls[nextIndex].focus({ preventScroll: true });
    return true;
  }

  function initializeAccountMenu() {
    populateAccountMenu();

    accountButton.addEventListener("click", () => {
      const shouldOpen = accountButton.getAttribute("aria-expanded") !== "true";
      setAccountMenuOpen(shouldOpen);
      if (shouldOpen) focusAccountMenuControl(1);
    });

    accountButton.addEventListener("keydown", (event) => {
      const isNextKey =
        event.key === "ArrowDown" || event.key === "ArrowRight";
      const isPreviousKey =
        event.key === "ArrowUp" || event.key === "ArrowLeft";
      if (!isNextKey && !isPreviousKey) return;

      event.preventDefault();
      event.stopPropagation();
      setAccountMenuOpen(true);
      focusAccountMenuControl(isNextKey ? 1 : -1);
    });

    logoutButton.addEventListener("click", async () => {
      setAccountMenuOpen(false);
      await logoutCurrentUser();
    });

    document.addEventListener("click", (event) => {
      if (!accountElement.contains(event.target)) setAccountMenuOpen(false);
    });

    document.addEventListener("keydown", (event) => {
      if (
        event.key === "Escape" &&
        accountButton.getAttribute("aria-expanded") === "true"
      ) {
        event.preventDefault();
        setAccountMenuOpen(false, { returnFocus: true });
        return;
      }

      if (
        accountButton.getAttribute("aria-expanded") !== "true" ||
        !accountElement.contains(event.target) ||
        event.target === accountButton ||
        event.target instanceof HTMLSelectElement
      ) {
        return;
      }

      const isNextKey =
        event.key === "ArrowDown" || event.key === "ArrowRight";
      const isPreviousKey =
        event.key === "ArrowUp" || event.key === "ArrowLeft";
      if (isNextKey || isPreviousKey) {
        event.preventDefault();
        focusAccountMenuControl(isNextKey ? 1 : -1);
      } else if (event.key === "Tab") {
        setTimeout(() => {
          if (!accountElement.contains(document.activeElement))
            setAccountMenuOpen(false);
        }, 0);
      }
    });
  }

  function initialize() {
    if (!getToken()) {
      window.location.replace("/login");
      return;
    }

    initializeAccountMenu();
    restoreState();
    const requestedStart = normalizeTabUrl(
      new URLSearchParams(location.search).get("start") || "",
    );
    if (requestedStart && requestedStart !== DASHBOARD_URL) {
      const requestedPath = cleanPath(
        new URL(requestedStart, location.origin).pathname,
      ).toLowerCase();
      const existing = tabs.find(
        (tab) =>
          !tab.isDashboard &&
          cleanPath(new URL(tab.url, location.origin).pathname).toLowerCase() ===
            requestedPath,
      );
      if (existing) activeTabId = existing.id;
      else if (tabs.length < MAX_TABS) {
        tabs.push({
          id: `tab-${nextTabNumber++}`,
          url: requestedStart,
          title: titleForUrl(requestedStart),
          isDashboard: false,
          loaded: false,
          frame: null,
          element: null,
          hasUnsavedChanges: false,
          unsavedStateKnown: false,
        });
        activeTabId = tabs[tabs.length - 1].id;
      }
    }
    history.replaceState(null, "", "/workspace");

    for (const tab of tabs) {
      createTabElement(tab);
      createFrame(tab);
    }
    activateTab(activeTabId);

    const backgroundTabs = tabs.filter((tab) => !tab.loaded);
    backgroundTabs.forEach((tab, index) => {
      window.setTimeout(() => {
        if (tabs.includes(tab)) loadTab(tab);
      }, 300 * (index + 1));
    });

    window.addEventListener("message", handleWorkspaceMessage);
    window.addEventListener("beforeunload", saveState);
    window.addEventListener("storage", (event) => {
      if (event.key === "ipos_theme") applyStoredTheme();
    });
    saveState();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
