/* Standby's visual kit, the script half (kit.css is the look). It runs before the fragment, in the visual's
   sandbox: no network, no storage, no Standby API. It gives the fragment sb.* helpers and <sb-math> and
   <sb-diagram>, and it is the visual's one way out, a message to its card: {sb: "size"} as its content
   changes, and {sb: "open", href} when an https link is clicked (the card opens it in the browser). */
(function () {
  "use strict";
  const send = m => {
    const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.card;
    if (h) h.postMessage(m);                        // the mini window's card (its own WKWebView)
    else if (window.parent !== window) window.parent.postMessage(m, "*");    // the main window (a sandboxed frame)
  };
  window.sb = {
    // a list whose items light one at a time when pressed (the first lit)
    steps(sel) {
      const l = [...document.querySelectorAll(sel + " li")];
      l.forEach(li => li.onclick = () => { l.forEach(o => o.classList.remove("on")); li.classList.add("on"); sb.size(); });
      if (l[0]) l[0].classList.add("on");
    },
    // one item lit by number (from 1), for a fragment that steps with the voice
    light(sel, n) {
      [...document.querySelectorAll(sel + " li")].forEach((li, i) => li.classList.toggle("on", i === n - 1));
    },
    // a number shown with its thousands, for live sums
    num(v, digits) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits || 0 }); },
    // what the card needs: the width the visual asks for (data-size on its root) and its content's height
    size() {
      const root = document.querySelector("#sb-root > *") || document.body;
      // a diagram asks for room unless the fragment says otherwise: its text never shrinks to fit
      const want = root.getAttribute && root.getAttribute("data-size") || (document.querySelector("sb-diagram") ? "large" : "medium");
      if (want === "line") document.documentElement.classList.add("sb-line");
      // the content's own size, never the view's (the view is the card's, and the card follows this)
      const r = document.body.getBoundingClientRect(), pad = 32;
      // a diagram drawn at its own size (never shrunk below readable text): the width the card should give it
      const need = Math.max(0, ...[...document.querySelectorAll("sb-diagram svg")].map(s => Math.ceil(s.getBoundingClientRect().width) + pad));
      send({ sb: "size", want, w: Math.ceil(r.width), h: Math.ceil(r.height), need });
    },
  };
  // links: only https, only on a click, and only by the card: the visual itself never navigates
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    e.preventDefault();
    if (/^https:\/\//i.test(a.href)) send({ sb: "open", href: a.href });
  }, true);
  document.addEventListener("submit", e => e.preventDefault(), true);
  const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;
  // a custom element is connected as its tag is read, before what's inside it: read it once the page is in
  const whenRead = fn => document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", fn, { once: true }) : fn();
  // <sb-math>x^2</sb-math>, <sb-math display>...</sb-math>: KaTeX when the visual has it, else the TeX as it is
  customElements.define("sb-math", class extends HTMLElement {
    connectedCallback() { if (!this._done) { this._done = true; whenRead(() => this.draw()) } }
    draw() {
      const tex = this.textContent;
      try { window.katex.render(tex, this, { displayMode: this.hasAttribute("display"), throwOnError: false }); }
      catch (e) { this.textContent = tex; this.classList.add("raw"); }
    }
  });
  // <sb-diagram>graph LR; A-->B</sb-diagram>: Mermaid when the visual has it, else the source as it is
  let n = 0;
  customElements.define("sb-diagram", class extends HTMLElement {
    connectedCallback() { if (!this._done) { this._done = true; whenRead(() => this.draw()) } }
    draw() {
      const src = this.textContent, el = this;
      if (!window.mermaid) { el.classList.add("raw"); return; }
      // its own size, its text at the kit's 13 px: the card grows for it, or it scrolls; it's never scaled down
      const own = { useMaxWidth: false };
      window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark() ? "dark" : "neutral",
        fontFamily: "-apple-system, system-ui, sans-serif", themeVariables: { fontSize: "13px" },
        flowchart: own, sequence: own, gantt: own, class: own, state: own, er: own, journey: own, pie: own, mindmap: own, timeline: own });
      window.mermaid.render("sb-d" + (++n), src).then(r => { el.innerHTML = r.svg; sb.size(); })
        .catch(() => { el.textContent = src; el.classList.add("raw"); sb.size(); });
    }
  });
  whenRead(() => new ResizeObserver(() => sb.size()).observe(document.body));
  addEventListener("load", () => sb.size());
})();
