/**
 * Local Design-canvas preview shim for design/ai-generated/*.dc.html.
 *
 * Not Claude's private host runtime — enough to run the artboard's
 * `class Component extends DCLogic` script, call renderVals(), and bind
 * {{holes}}, <sc-for>, <sc-if>, and onClick handlers so sample/demo series
 * paint for static screenshots and local preview.
 *
 * Usage: open Main.dc.html / Mobile.dc.html via a local static server
 * (file:// may block fonts; http://localhost is preferred).
 */
(function (global) {
  'use strict';

  var HOLE_RE = /\{\{\s*([^}]+?)\s*\}\}/g;
  var EVENT_ATTRS = {
    onclick: 'click',
    onchange: 'change',
    oninput: 'input',
    onkeydown: 'keydown',
    onkeyup: 'keyup',
    onkeypress: 'keypress',
    onfocus: 'focus',
    onblur: 'blur',
    onsubmit: 'submit',
    onmousedown: 'mousedown',
    onmouseup: 'mouseup',
    onmouseenter: 'mouseenter',
    onmouseleave: 'mouseleave',
    ontouchstart: 'touchstart',
    ontouchend: 'touchend'
  };

  function lookup(scope, path) {
    var p = String(path || '').trim();
    if (!p) return undefined;
    if (p === 'true') return true;
    if (p === 'false') return false;
    if (p === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(p)) return Number(p);
    var parts = p.split('.');
    var cur = scope;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  function formatText(val) {
    if (val == null) return '';
    if (typeof val === 'function') return '';
    if (typeof val === 'boolean') return val ? 'true' : 'false';
    if (typeof val === 'object') return '';
    return String(val);
  }

  function resolveAttr(raw, scope) {
    if (raw == null) return { kind: 'literal', value: raw };
    var s = String(raw);
    var m = /^\{\{\s*([^}]+?)\s*\}\}$/.exec(s);
    if (m) return { kind: 'raw', value: lookup(scope, m[1]) };
    if (!s.includes('{{')) return { kind: 'literal', value: s };
    var out = s.replace(HOLE_RE, function (_, path) {
      return formatText(lookup(scope, path));
    });
    return { kind: 'interp', value: out };
  }

  function truthy(v) {
    return !!v;
  }

  function promoteHelmet(root) {
    root.querySelectorAll('helmet').forEach(function (h) {
      Array.prototype.slice.call(h.childNodes).forEach(function (n) {
        if (n.nodeType === 1) document.head.appendChild(n.cloneNode(true));
      });
      h.remove();
    });
  }

  function ensureBaseStyles() {
    if (document.getElementById('dc-preview-shim-style')) return;
    var s = document.createElement('style');
    s.id = 'dc-preview-shim-style';
    s.textContent = [
      'x-dc { display: contents; }',
      'sc-if, sc-for { display: contents; }',
      'dc-import { display: none; }',
      'body { margin: 0; background: #0B0E13; }'
    ].join('\n');
    document.head.appendChild(s);
  }

  function defaultsFromPropsAttr(raw) {
    var props = {};
    if (!raw) return props;
    try {
      var cfg = JSON.parse(raw);
      Object.keys(cfg).forEach(function (k) {
        if (k === '$preview') return;
        var e = cfg[k];
        if (e && Object.prototype.hasOwnProperty.call(e, 'default')) props[k] = e.default;
      });
    } catch (err) {
      console.warn('[dc-preview] bad data-props', err);
    }
    return props;
  }

  function bindTextNode(node, scope) {
    var t = node.nodeValue;
    if (!t || t.indexOf('{{') < 0) return;
    node.nodeValue = t.replace(HOLE_RE, function (_, path) {
      return formatText(lookup(scope, path));
    });
  }

  function bindElement(el, scope) {
    if (el.tagName === 'SC-FOR') {
      bindScFor(el, scope);
      return;
    }
    if (el.tagName === 'SC-IF') {
      bindScIf(el, scope);
      return;
    }
    if (el.tagName === 'DC-IMPORT' || el.tagName === 'HELMET') {
      el.remove();
      return;
    }

    var attrs = Array.prototype.slice.call(el.attributes || []);
    attrs.forEach(function (attr) {
      var name = attr.name;
      var low = name.toLowerCase();
      var resolved = resolveAttr(attr.value, scope);

      if (EVENT_ATTRS[low]) {
        el.removeAttribute(name);
        if (typeof resolved.value === 'function') {
          el.addEventListener(EVENT_ATTRS[low], function (ev) {
            try {
              resolved.value.call(null, ev);
            } catch (err) {
              console.error('[dc-preview] handler error', err);
            }
          });
        }
        return;
      }

      if (resolved.kind === 'literal') return;

      if (resolved.kind === 'raw') {
        var v = resolved.value;
        if (typeof v === 'function') {
          el.removeAttribute(name);
          return;
        }
        if (v == null) {
          el.removeAttribute(name);
          return;
        }
        if (typeof v === 'boolean') {
          if (low === 'aria-pressed' || low === 'aria-expanded' || low === 'aria-selected' || low === 'aria-checked') {
            el.setAttribute(name, v ? 'true' : 'false');
          } else if (v) {
            el.setAttribute(name, '');
          } else {
            el.removeAttribute(name);
          }
          return;
        }
        el.setAttribute(name, String(v));
        return;
      }

      el.setAttribute(name, resolved.value);
    });

    Array.prototype.slice.call(el.childNodes).forEach(function (child) {
      if (child.nodeType === 3) bindTextNode(child, scope);
      else if (child.nodeType === 1) bindElement(child, scope);
    });
  }

  function bindScIf(el, scope) {
    var raw = el.getAttribute('value');
    var resolved = resolveAttr(raw, scope);
    var ok = truthy(resolved.value);
    if (!ok) {
      el.remove();
      return;
    }
    el.style.display = 'contents';
    Array.prototype.slice.call(el.childNodes).forEach(function (child) {
      if (child.nodeType === 3) bindTextNode(child, scope);
      else if (child.nodeType === 1) bindElement(child, scope);
    });
  }

  function bindScFor(el, scope) {
    var listAttr = el.getAttribute('list');
    var asName = el.getAttribute('as') || 'item';
    var listVal = resolveAttr(listAttr, scope).value;
    var list = Array.isArray(listVal) ? listVal : [];
    var templateNodes = Array.prototype.slice.call(el.childNodes).map(function (n) {
      return n.cloneNode(true);
    });
    while (el.firstChild) el.removeChild(el.firstChild);
    el.style.display = 'contents';
    list.forEach(function (item, index) {
      var childScope = Object.create(scope);
      childScope[asName] = item;
      childScope.$index = index;
      templateNodes.forEach(function (tmpl) {
        var node = tmpl.cloneNode(true);
        el.appendChild(node);
        if (node.nodeType === 3) bindTextNode(node, childScope);
        else if (node.nodeType === 1) bindElement(node, childScope);
      });
    });
  }

  function DCLogic() {
    this.props = {};
    this.state = {};
    this._d = null;
  }

  DCLogic.prototype.setState = function (partial) {
    var next = Object.assign({}, this.state || {}, partial || {});
    this.state = next;
    if (typeof this._rerender === 'function') this._rerender();
  };

  DCLogic.prototype.forceUpdate = function () {
    if (typeof this._rerender === 'function') this._rerender();
  };

  global.DCLogic = DCLogic;

  function loadComponent(scriptEl) {
    var src = scriptEl.textContent || '';
    try {
      return new Function('DCLogic', src + '\n; return Component;')(DCLogic);
    } catch (err) {
      console.error('[dc-preview] failed to evaluate Component', err);
      throw err;
    }
  }

  function mount() {
    ensureBaseStyles();
    var host = document.querySelector('x-dc');
    var scriptEl = document.querySelector('script[data-dc-script]');
    if (!host || !scriptEl) {
      console.warn('[dc-preview] missing x-dc or data-dc-script');
      return;
    }

    promoteHelmet(host);
    var templateHtml = host.innerHTML;
    var Component = loadComponent(scriptEl);
    var instance = new Component();
    instance.props = defaultsFromPropsAttr(scriptEl.getAttribute('data-props'));
    instance.state = {};

    function render() {
      var vals;
      try {
        vals = instance.renderVals() || {};
      } catch (err) {
        console.error('[dc-preview] renderVals threw', err);
        host.innerHTML = '<pre style="color:#F0A6A6;padding:24px;white-space:pre-wrap">renderVals error: ' +
          String(err && err.stack || err) + '</pre>';
        document.documentElement.setAttribute('data-dc-preview', 'error');
        return;
      }
      var wrap = document.createElement('div');
      wrap.innerHTML = templateHtml;
      // Re-promote any helmet left in the stored template (already stripped once)
      promoteHelmet(wrap);
      Array.prototype.slice.call(wrap.childNodes).forEach(function (child) {
        if (child.nodeType === 3) bindTextNode(child, vals);
        else if (child.nodeType === 1) bindElement(child, vals);
      });
      host.innerHTML = '';
      while (wrap.firstChild) host.appendChild(wrap.firstChild);
      document.documentElement.setAttribute('data-dc-preview', 'ready');
      document.documentElement.setAttribute('data-dc-sym', String(vals.sym || ''));
    }

    instance._rerender = render;
    render();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})(typeof window !== 'undefined' ? window : globalThis);
