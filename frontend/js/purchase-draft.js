(function (global) {
  "use strict";

  const VERSION = 1;
  const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000;
  const KEY_PREFIX = "fpos_purchase_draft";

  function bagianKunci(value) {
    return encodeURIComponent(String(value ?? "unknown"));
  }

  function buatKunci({ userId, branchId, mode }) {
    return [
      KEY_PREFIX,
      `v${VERSION}`,
      bagianKunci(userId),
      bagianKunci(branchId),
      bagianKunci(mode),
    ].join(":");
  }

  function buatPenyimpanan(options = {}) {
    const {
      storage = global.localStorage,
      userId = "unknown",
      branchId = "unknown",
      mode = "purchase",
      ttlMs = DEFAULT_TTL_MS,
      now = () => Date.now(),
    } = options;
    const key = buatKunci({ userId, branchId, mode });
    const scope = {
      userId: String(userId),
      branchId: String(branchId),
      mode: String(mode),
    };

    function hapus() {
      try {
        storage.removeItem(key);
        return true;
      } catch (error) {
        console.error("Gagal menghapus draft pembelian", error);
        return false;
      }
    }

    function baca() {
      let raw;
      try {
        raw = storage.getItem(key);
      } catch (error) {
        console.error("Gagal membaca draft pembelian", error);
        return null;
      }
      if (!raw) return null;

      try {
        const envelope = JSON.parse(raw);
        const savedAt = Number(envelope?.savedAt);
        const scopeCocok =
          envelope?.version === VERSION &&
          String(envelope?.userId) === scope.userId &&
          String(envelope?.branchId) === scope.branchId &&
          String(envelope?.mode) === scope.mode;
        const waktuValid =
          Number.isFinite(savedAt) &&
          savedAt <= now() &&
          now() - savedAt < ttlMs;
        const dataValid =
          envelope?.data &&
          typeof envelope.data === "object" &&
          !Array.isArray(envelope.data);

        if (!scopeCocok || !waktuValid || !dataValid) {
          hapus();
          return null;
        }
        return envelope.data;
      } catch (error) {
        hapus();
        return null;
      }
    }

    function simpan(data) {
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        return false;
      }
      try {
        storage.setItem(
          key,
          JSON.stringify({
            version: VERSION,
            savedAt: now(),
            ...scope,
            data,
          }),
        );
        return true;
      } catch (error) {
        console.error("Gagal menyimpan draft pembelian", error);
        return false;
      }
    }

    return { key, read: baca, save: simpan, remove: hapus };
  }

  global.PurchaseDraftStore = Object.freeze({
    VERSION,
    DEFAULT_TTL_MS,
    buildKey: buatKunci,
    create: buatPenyimpanan,
  });
})(globalThis);
