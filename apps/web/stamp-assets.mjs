// Deploy-only cache busting. Local dev and node tests keep the unstamped sources.
// The deploy workflow runs this against the checkout it is about to publish.

import { readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const RELATIVE_IMPORT = /(?:\bfrom\s+|\bimport\s*\(\s*|\bimport\s+)(['"])(\.\.?\/[^'"]+)\1/g;

export function normalizeVersion(version) {
  const value = String(version ?? "").trim().toLowerCase();
  if (!/^[0-9a-f]{7,40}$/.test(value)) {
    throw new Error("Asset version must be a 7 to 40 character hex commit SHA.");
  }
  return value;
}

function isLocalAsset(url) {
  if (!url || url.startsWith("#")) return false;
  if (url.startsWith("//")) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) return false;
  return true;
}

export function withVersion(url, version) {
  const hashAt = url.indexOf("#");
  const hash = hashAt === -1 ? "" : url.slice(hashAt);
  const beforeHash = hashAt === -1 ? url : url.slice(0, hashAt);
  const queryAt = beforeHash.indexOf("?");
  const assetPath = queryAt === -1 ? beforeHash : beforeHash.slice(0, queryAt);
  const query = queryAt === -1 ? "" : beforeHash.slice(queryAt + 1);
  const params = new URLSearchParams(query);
  params.set("v", version);
  return `${assetPath}?${params.toString()}${hash}`;
}

function stampAttribute(tag, name, version) {
  const matcher = new RegExp(String.raw`(\b${name}\s*=\s*)(['"])([^'"]*)\2`, "i");
  return tag.replace(matcher, (full, prefix, quote, url) => {
    if (!isLocalAsset(url)) return full;
    return `${prefix}${quote}${withVersion(url, version)}${quote}`;
  });
}

function linkIsStylesheet(tag) {
  const quoted = /\brel\s*=\s*(['"])([^'"]*)\1/i.exec(tag);
  const rel = quoted ? quoted[2] : (/\brel\s*=\s*([^\s>]+)/i.exec(tag)?.[1] ?? "");
  return rel.split(/\s+/).includes("stylesheet");
}

export function stampHtml(html, version) {
  const stampedVersion = normalizeVersion(version);
  return html.replace(/<link\b[^>]*>/gi, (tag) => {
    if (!linkIsStylesheet(tag)) return tag;
    return stampAttribute(tag, "href", stampedVersion);
  }).replace(/<script\b[^>]*>/gi, (tag) => {
    if (!/\bsrc\s*=/i.test(tag)) return tag;
    return stampAttribute(tag, "src", stampedVersion);
  });
}

export function stampModuleSource(source, version) {
  const stampedVersion = normalizeVersion(version);
  return source.replace(RELATIVE_IMPORT, (full, quote, specifier) => {
    const stamped = withVersion(specifier, stampedVersion);
    if (stamped === specifier) return full;
    return full.replace(`${quote}${specifier}${quote}`, `${quote}${stamped}${quote}`);
  });
}

export function findUnversionedImports(source, version) {
  const stampedVersion = normalizeVersion(version);
  const missing = [];
  for (const match of source.matchAll(RELATIVE_IMPORT)) {
    const specifier = match[2];
    const query = specifier.split("#")[0].split("?")[1] ?? "";
    if (new URLSearchParams(query).get("v") !== stampedVersion) missing.push(specifier);
  }
  return missing;
}

function localStylesheetAndScriptUrls(html) {
  const urls = [];
  for (const tag of html.match(/<link\b[^>]*>/gi) ?? []) {
    if (!linkIsStylesheet(tag)) continue;
    const href = /\bhref\s*=\s*(['"])([^'"]*)\1/i.exec(tag);
    if (href && isLocalAsset(href[2])) urls.push(href[2]);
  }
  for (const tag of html.match(/<script\b[^>]*>/gi) ?? []) {
    const src = /\bsrc\s*=\s*(['"])([^'"]*)\1/i.exec(tag);
    if (src && isLocalAsset(src[2])) urls.push(src[2]);
  }
  return urls;
}

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      files.push(...await walk(full));
    } else {
      files.push(full);
    }
  }
  return files;
}

function isModule(file) {
  const base = path.basename(file);
  if (base === "stamp-assets.mjs" || base.endsWith(".test.js")) return false;
  return base.endsWith(".js") || base.endsWith(".mjs");
}

async function rewrite(file, transform) {
  const original = await readFile(file, "utf8");
  const next = transform(original);
  if (next === original) return 0;
  await writeFile(file, next);
  return 1;
}

export async function stampWebAssets(rootDir, version) {
  const stampedVersion = normalizeVersion(version);
  const root = path.resolve(rootDir);
  const files = await walk(root);
  let updated = 0;
  let sawIndex = false;

  for (const file of files) {
    const base = path.basename(file);
    if (base === "index.html" && path.dirname(file) === root) {
      sawIndex = true;
      updated += await rewrite(file, (text) => stampHtml(text, stampedVersion));
      continue;
    }
    if (!isModule(file)) continue;
    updated += await rewrite(file, (text) => stampModuleSource(text, stampedVersion));
  }

  if (!sawIndex) throw new Error(`No index.html in ${root}`);

  const html = await readFile(path.join(root, "index.html"), "utf8");
  const assetUrls = localStylesheetAndScriptUrls(html);
  if (assetUrls.length === 0) {
    throw new Error("index.html has no local script or stylesheet URL to stamp.");
  }
  const bareHtml = assetUrls.filter((url) => new URLSearchParams(url.split("#")[0].split("?")[1] ?? "").get("v") !== stampedVersion);
  if (bareHtml.length > 0) {
    throw new Error(`index.html asset URLs were not stamped: ${bareHtml.join(", ")}`);
  }

  const bareImports = [];
  for (const file of files) {
    if (!isModule(file)) continue;
    const source = await readFile(file, "utf8");
    for (const specifier of findUnversionedImports(source, stampedVersion)) {
      bareImports.push(`${path.relative(root, file)}: ${specifier}`);
    }
  }
  if (bareImports.length > 0) {
    throw new Error(`Relative imports were not stamped:\n${bareImports.join("\n")}`);
  }

  return updated;
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const dir = process.argv[2];
  const version = process.argv[3];
  if (!dir || !version) {
    console.error("usage: node apps/web/stamp-assets.mjs <web-dir> <commit-sha>");
    process.exit(1);
  }
  stampWebAssets(dir, version).then((count) => {
    console.log(`Stamped ${count} file(s) with v=${normalizeVersion(version)}`);
  }).catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
