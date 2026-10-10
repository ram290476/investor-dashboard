import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const account = await readFile(new URL("./account-settings.js", import.meta.url), "utf8");

test("the desktop refresh table stays a five-column table", () => {
  const desktop = css.slice(css.indexOf(".refresh-table-wrap"), css.indexOf(".refresh-status.ok"));
  assert.match(desktop, /\.refresh-table\s*\{[^}]*min-width:\s*650px/);
  assert.match(account, /\["Job", "Name", "Last run", "Next run", "Status"\]/);
  assert.doesNotMatch(desktop, /grid-template-areas/);
});

test("a phone stacks each job and keeps the sheet above the visible viewport", () => {
  const phone = css.slice(css.indexOf("Data refresh rows stack"), css.length);
  assert.match(phone, /@media \(max-width:\s*640px\)/);
  assert.match(phone, /min-width:\s*0/);
  assert.match(phone, /grid-template-areas:\s*"status name job"/);
  assert.match(phone, /content:\s*attr\(data-label\)/);
  assert.equal(phone.includes("#"), false);
  for (const token of ["--line", "--muted", "--text"]) {
    assert.match(phone, new RegExp(`var\\(${token}\\)`));
  }
  assert.match(css, /max-height:\s*calc\(100vh - 24px\);/);
  assert.match(css, /max-height:\s*calc\(100dvh - 24px\);/);
  assert.match(css, /padding-bottom:\s*max\(16px,\s*env\(safe-area-inset-bottom\)\)/);
  assert.match(account, /dataset\.label = "Last"/);
  assert.match(account, /dataset\.label = "Next"/);
  assert.match(account, /lastRunLabel\(job\)/);
  assert.match(account, /nextRunLabel\(job\)/);
  assert.match(account, /refresh-event/);
  assert.equal(account.includes("not scheduled"), false);
  assert.match(css, /\.refresh-event\s*\{[^}]*var\(--muted\)/);
  assert.match(css, /\.refresh-event\s*\{[^}]*var\(--line\)/);
  assert.match(account, /function revealActiveTab\(/);
  assert.match(account, /tablist\.scrollLeft = right - tablist\.clientWidth/);
});
