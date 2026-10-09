// Palette values mirror docs/design-roadmap/themes/themes.json; tests enforce that contract.
const TOKEN_NAMES = [
  "bg", "surface", "surface-2", "border", "border-strong", "grid", "text", "text-2",
  "text-muted", "text-faint", "accent", "accent-text", "up", "down", "neutral", "warning",
  "series-1", "series-2", "series-3", "series-4", "series-5", "series-6", "heat",
];
const definitions = [
  ["industrial-dark", "Industrial Dark", "dark",
    "0B0E13 12161D 1A2029 2A323E 4A5361 222A35 E6EAF0 C5CDD8 9BA6B5 8B96A6 6CA6FF 8EBBFF 3FB950 F85149 9BA6B5 E3B341 6CA6FF F2A33A EC7BA6 B392F0 39C5B0 E3C05C 5B6BD6"],
  ["terminal-amber", "Terminal Amber", "dark",
    "000000 080706 15110A 3D2E14 6E5222 261D0E FFA028 F2C46D CC8A2E B07A30 FFD23F FFE08A 22D65F FF4136 CC8A2E FFE600 FFD23F FF6F00 FF5FA2 B48CFF 00D8FF F5F5F5 7A4A00"],
  ["charting-navy", "Charting Navy", "dark",
    "0A1120 111A2E 18233A 27344F 3E4E70 1C2842 D9E1EE B6C1D4 93A0B8 8592AB 4C8DFF 8DB6FF 26A69A EF5350 93A0B8 F7B32B 4C8DFF FF9800 F06292 A98BF5 26C6DA FFD54F 3D6FD6"],
  ["clean-light", "Clean Light", "light",
    "F3F5F8 FFFFFF F1F3F6 D9DEE5 AAB3C0 E7EBF0 111827 2F3A4A 4B5563 5D6877 1A62D6 1554BD 0B7A3B C62828 5D6877 A15C00 1A62D6 E06C00 C2185B 7047D1 00897B A77B00 7FA6F0"],
  ["colorblind-hc", "Colorblind High Contrast", "dark",
    "000000 0B0D10 171B21 5A6370 9AA4B1 2C333D FFFFFF EEF1F5 D0D6DE BAC2CD FFFFFF FFFFFF 56B4E9 E69F00 BAC2CD F0E442 FFFFFF F0E442 CC79A7 B9A3FF 00B98A D55E00 2F6FA8"],
  ["midnight-slate", "Midnight Slate", "dark",
    "0F172A 162036 1E293B 334155 52617A 243049 F1F5F9 CBD5E1 A7B3C5 94A3B8 818CF8 A5B4FC 34D399 FB7185 A7B3C5 FBBF24 818CF8 F59E0B F472B6 C084FC 22D3EE FACC15 6366F1"],
];

export const DEFAULT_THEME = "industrial-dark";
export const THEMES = definitions.map(([id, label, scheme, colors]) => ({
  id, label, scheme,
  tokens: Object.fromEntries(colors.split(" ").map((color, index) => [TOKEN_NAMES[index], `#${color}`])),
}));

export function themeFor(id = DEFAULT_THEME) {
  const theme = THEMES.find((item) => item.id === id);
  if (!theme) throw new Error(`Unsupported theme: ${id}`);
  return theme;
}

function channels(hex) {
  return [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
}

function luminance(hex) {
  const rgb = channels(hex).map((value) => {
    const s = value / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
}

export function contrastRatio(a, b) {
  const values = [luminance(a), luminance(b)].sort((left, right) => right - left);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

export function mixColor(a, b, amount) {
  const target = channels(b);
  return `#${channels(a).map((value, index) =>
    Math.round(value + (target[index] - value) * amount).toString(16).padStart(2, "0"),
  ).join("")}`;
}

// Design references aren't sufficient: production text and chart colors must contrast on all surfaces.
function readable(color, backgrounds, scheme, minimum = 5) {
  const target = scheme === "light" ? "#000000" : "#ffffff";
  for (let step = 0; step <= 100; step += 1) {
    const candidate = mixColor(color, target, step / 100);
    if (backgrounds.every((bg) => contrastRatio(candidate, bg) >= minimum)) return candidate;
  }
  throw new Error(`Cannot achieve theme contrast for ${color}`);
}

export function themeProperties(id = DEFAULT_THEME, palette = "green-red") {
  const { tokens: t, scheme } = themeFor(id);
  if (!["theme", "green-red", "red-green", "blue-orange"].includes(palette)) {
    throw new Error(`Unsupported up/down palette: ${palette}`);
  }
  const selected = mixColor(t.surface, t.accent, 0.08);
  const hover = mixColor(t["surface-2"], t.text, 0.04);
  const backgrounds = [t.bg, t.surface, t["surface-2"], selected, hover];
  const text = (color) => readable(color, backgrounds, scheme);
  const green = text("#48c79b");
  const red = text("#f07a7a");
  const blue = text("#7db4ff");
  const orange = text("#f2a33a");
  const up = palette === "theme" ? text(t.up) : palette === "blue-orange" ? blue : palette === "red-green" ? red : green;
  const down = palette === "theme" ? text(t.down) : palette === "blue-orange" ? orange : palette === "red-green" ? green : red;
  return {
    "--page": t.bg, "--surface": t.surface, "--surface-raised": t["surface-2"],
    "--line": t.border, "--control-line": readable(t["border-strong"], backgrounds, scheme, 3),
    "--grid": t.grid, "--text": text(t.text), "--secondary": text(t["text-2"]),
    "--muted": text(t["text-muted"]), "--subtle": text(t["text-faint"]),
    "--blue": text(t["accent-text"]), "--price": readable(t["series-1"], backgrounds, scheme, 3),
    "--green": up, "--red": down, "--amber": text(t.warning), "--error": red,
    "--hover": hover, "--selected": selected,
    "--palette-green": green, "--palette-red": red, "--palette-blue": blue, "--palette-orange": orange,
    "--theme-up": text(t.up), "--theme-down": text(t.down),
    "--shadow-panel": scheme === "light" ? "rgba(22, 34, 50, 0.08)" : "rgba(0, 0, 0, 0.12)",
    "--shadow-menu": scheme === "light" ? "rgba(22, 34, 50, 0.18)" : "rgba(0, 0, 0, 0.45)",
    "--shadow-dialog": scheme === "light" ? "rgba(22, 34, 50, 0.22)" : "rgba(0, 0, 0, 0.55)",
    "--backdrop": scheme === "light" ? "rgba(22, 34, 50, 0.35)" : "rgba(5, 8, 12, 0.72)",
    ...Object.fromEntries(Array.from({ length: 6 }, (_, i) => [
      `--series-${i + 1}`, readable(t[`series-${i + 1}`], backgrounds, scheme, 3),
    ])),
  };
}

export function overlayColor(slot, id = DEFAULT_THEME) {
  const { tokens, scheme } = themeFor(id);
  const fill = mixColor(tokens.surface, themeProperties(id)["--price"], 0.08);
  const base = tokens[`series-${slot % 6 + 1}`];
  const color = slot < 6 ? base
    : mixColor(base, tokens[`series-${(slot + 1) % 6 + 1}`], Math.floor(slot / 6) * 0.13);
  return readable(color, [tokens.surface, tokens["surface-2"], fill], scheme, 3);
}

export function applyTheme(display = {}, doc = document) {
  const id = display.theme ?? DEFAULT_THEME;
  const theme = themeFor(id);
  const root = doc.documentElement;
  const properties = themeProperties(id, display.updown_palette ?? "green-red");
  root.dataset.theme = id;
  root.style.colorScheme = theme.scheme;
  for (const [name, value] of Object.entries(properties)) root.style.setProperty(name, value);
  doc.querySelector('meta[name="theme-color"]')?.setAttribute("content", properties["--page"]);
  doc.querySelector('meta[name="color-scheme"]')?.setAttribute("content", theme.scheme);
}
