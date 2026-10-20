import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  findUnversionedImports,
  stampHtml,
  stampModuleSource,
  stampWebAssets,
} from "./stamp-assets.mjs";

const VERSION = "abc1234";

test("index.html script and stylesheet URLs gain the commit id", () => {
  const html = `<link rel="icon" href="/favicon.ico" />
<link href="/styles.css" rel="stylesheet" />
<link rel="stylesheet" href="https://cdn.example/site.css" />
<script type="module" src="/app.js"></script>
<script src="https://cdn.example/lib.js"></script>
<script>console.log("inline")</script>`;
  const stamped = stampHtml(html, VERSION);
  assert.match(stamped, /href="\/styles\.css\?v=abc1234"/);
  assert.match(stamped, /src="\/app\.js\?v=abc1234"/);
  assert.match(stamped, /href="\/favicon\.ico"/);
  assert.match(stamped, /href="https:\/\/cdn\.example\/site\.css"/);
  assert.match(stamped, /src="https:\/\/cdn\.example\/lib\.js"/);
  assert.match(stamped, /<script>console\.log\("inline"\)<\/script>/);
  assert.equal(stampHtml(stamped, VERSION), stamped);
});

test("relative module imports share one version and other specifiers stay put", () => {
  const source = `import { a } from "./chart-period.js";
import {
  b,
} from './deep-dive.js';
import "./side-effect.js";
export { c } from "../pkg/mod.js";
const loaded = import("./stock-page.js");
import nodeFs from "node:fs";
import bare from "bare-pkg";
const file = new URL("./app.js", import.meta.url);
`;
  const stamped = stampModuleSource(source, "ABC1234");
  assert.match(stamped, /from "\.\/chart-period\.js\?v=abc1234"/);
  assert.match(stamped, /from '\.\/deep-dive\.js\?v=abc1234'/);
  assert.match(stamped, /import "\.\/side-effect\.js\?v=abc1234"/);
  assert.match(stamped, /from "\.\.\/pkg\/mod\.js\?v=abc1234"/);
  assert.match(stamped, /import\("\.\/stock-page\.js\?v=abc1234"\)/);
  assert.match(stamped, /from "node:fs"/);
  assert.match(stamped, /from "bare-pkg"/);
  assert.match(stamped, /new URL\("\.\/app\.js", import\.meta\.url\)/);
  assert.deepEqual(findUnversionedImports(stamped, VERSION), []);
  assert.deepEqual(findUnversionedImports(source, VERSION), [
    "./chart-period.js",
    "./deep-dive.js",
    "./side-effect.js",
    "../pkg/mod.js",
    "./stock-page.js",
  ]);
  assert.equal(stampModuleSource(stamped, VERSION), stamped);
});

test("stamping a tree versions the module graph and leaves tests and dev sources alone", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "stamp-web-"));
  try {
    await cp(new URL(".", import.meta.url), dir, { recursive: true });
    const testBefore = await readFile(path.join(dir, "sparkline.test.js"), "utf8");
    const toolBefore = await readFile(path.join(dir, "stamp-assets.mjs"), "utf8");
    const repoHtml = await readFile(new URL("./index.html", import.meta.url), "utf8");
    const updated = await stampWebAssets(dir, VERSION);
    assert.ok(updated > 1);

    const html = await readFile(path.join(dir, "index.html"), "utf8");
    assert.match(html, /href="\/styles\.css\?v=abc1234"/);
    assert.match(html, /src="\/app\.js\?v=abc1234"/);

    const app = await readFile(path.join(dir, "app.js"), "utf8");
    assert.match(app, /from "\.\/deep-dive\.js\?v=abc1234"/);
    assert.match(app, /from "\.\/chart-overlays\.js\?v=abc1234"/);
    assert.equal(findUnversionedImports(app, VERSION).length, 0);
    const checked = spawnSync(process.execPath, ["--check", path.join(dir, "app.js")], { encoding: "utf8" });
    assert.equal(checked.status, 0, checked.stderr);

    const deepDive = await readFile(path.join(dir, "deep-dive.js"), "utf8");
    assert.match(deepDive, /from "\.\/geo\.js\?v=abc1234"/);
    assert.match(deepDive, /from "\.\/routes\.js\?v=abc1234"/);
    assert.match(deepDive, /from "\.\/stock-page\.js\?v=abc1234"/);

    assert.equal(await readFile(path.join(dir, "sparkline.test.js"), "utf8"), testBefore);
    assert.equal(await readFile(path.join(dir, "stamp-assets.mjs"), "utf8"), toolBefore);
    assert.equal(repoHtml, await readFile(new URL("./index.html", import.meta.url), "utf8"));
    assert.doesNotMatch(repoHtml, /\?v=/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("deploy publishes index.html and config.json uncached and stamps before sync", async () => {
  const workflow = await readFile(new URL("../../.github/workflows/deploy.yml", import.meta.url), "utf8");
  const publish = workflow.slice(workflow.indexOf("- name: Publish static dashboard"));
  assert.match(workflow, /node apps\/web\/stamp-assets\.mjs apps\/web "\$short"/);
  assert.ok(workflow.indexOf("stamp-assets.mjs") < workflow.indexOf("- name: Publish static dashboard"));
  assert.match(publish, /--exclude index\.html/);
  assert.match(publish, /--exclude config\.json/);
  assert.match(publish, /--cache-control "public, max-age=31536000, immutable"/);
  assert.match(publish, /aws s3 cp apps\/web\/index\.html[^\n]*\\\n\s+--cache-control "no-cache"/);
  assert.match(publish, /aws s3 cp apps\/web\/config\.json[^\n]*\\\n\s+--cache-control "no-cache"/);
});

test("rejects a non-hex version and stamps a small fixture", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "stamp-bad-"));
  try {
    await mkdir(dir, { recursive: true });
    await writeFile(path.join(dir, "index.html"), '<link rel="stylesheet" href="/styles.css" />\n<script type="module" src="/app.js"></script>\n');
    await writeFile(path.join(dir, "app.js"), 'import { a } from "./missing.js";\n');
    await assert.rejects(stampWebAssets(dir, "zzzzzzz"), /hex commit SHA/);
    await stampWebAssets(dir, VERSION);
    const stamped = await readFile(path.join(dir, "app.js"), "utf8");
    assert.match(stamped, /from "\.\/missing\.js\?v=abc1234"/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
