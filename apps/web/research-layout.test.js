import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");

test("research layout collapses for tablet and mobile widths", () => {
  assert.match(
    styles,
    /@media \(max-width: 930px\)[\s\S]*?\.research-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
  assert.match(
    styles,
    /@media \(max-width: 930px\)[\s\S]*?\.research-column:last-child\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/,
  );
  assert.match(
    styles,
    /@media \(max-width: 640px\)[\s\S]*?\.research-column:last-child\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
});
