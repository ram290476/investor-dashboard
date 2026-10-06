# AI-generated design references

These files are canvas exports, mockups, and design guidance. They are reference material only;
the deployable, zero-build dashboard lives in [`../../apps/web/`](../../apps/web/). Do not publish
this directory as the production site or treat its sample values as live market data.

## Local preview (`support.js`)

`support.js` in this folder is a **local Design-canvas preview shim** (not Claude’s private host
runtime). It defines `DCLogic`, evaluates each artboard’s `class Component extends DCLogic`
script, calls `renderVals()`, and binds `{{holes}}`, `<sc-for>`, `<sc-if>`, and click handlers so
the demo series paint in a normal browser.

Serve this directory over HTTP and open `Main.dc.html` or `Mobile.dc.html`:

```sh
python3 -m http.server 8765
# → http://127.0.0.1:8765/Main.dc.html
```

Screenshots under [`../../docs/design-roadmap/`](../../docs/design-roadmap/) were captured this way.
