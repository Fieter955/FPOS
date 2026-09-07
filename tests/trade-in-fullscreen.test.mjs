import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const tradeInPage = readFileSync(
  new URL("../frontend/trade_in.html", import.meta.url),
  "utf8",
);

test("popup transaksi tukar tambah memenuhi ruang halaman", () => {
  assert.match(
    tradeInPage,
    /class="modal-box wide trade-in-modal-box"/,
  );
  assert.match(tradeInPage, /width: calc\(100vw - 24px\)/);
  assert.match(tradeInPage, /height: calc\(100vh - 24px\)/);
  assert.match(tradeInPage, /class="trade-in-modal-actions"/);
});

test("header dan tombol tukar tambah tetap terjangkau saat isi digulir", () => {
  assert.match(tradeInPage, /#mBuat \.modal-hdr \{[\s\S]*?position: sticky/);
  assert.match(
    tradeInPage,
    /#mBuat \.trade-in-modal-actions \{[\s\S]*?position: sticky/,
  );
});

test("pembayaran tukar tambah mendukung saldo pelanggan", () => {
  assert.match(tradeInPage, /id="payDeposit"/);
  assert.match(tradeInPage, /id="customerBalanceHint"/);
  assert.match(tradeInPage, /deposit_amount: depositAmount/);
  assert.match(tradeInPage, /usedMethods\.length > 1 \? "mixed"/);
  assert.match(tradeInPage, /class="trade-in-payment-grid"/);
});
