import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const reports = ["sales", "profit-loss", "inventory", "purchases", "customers", "cash", "receivables"];

test("all report pages use the shared renderer and distinct report keys", () => {
  for (const report of reports) {
    const html = fs.readFileSync(path.join(root, "frontend", "reports", `${report}.html`), "utf8");
    assert.match(html, new RegExp(`data-report=\\"${report}\\"`));
    assert.match(html, /\/js\/report-page\.js/);
    assert.match(html, /id=\"export\"/);
    assert.match(html, /id=\"print\"/);
  }
});
