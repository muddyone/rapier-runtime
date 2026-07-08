# site/ — rapierruntime.com

The public site for **rapierruntime.com**, the product front door for Rapier Runtime
(the engine that runs the SPARRING method). Both files are self-contained — no build
step, no external assets (inline CSS/JS, system fonts, emoji favicon), theme-aware.

- **`coming-soon.html`** — the interim **"in development"** page. Design-consistent with
  the full landing but safe to serve now: no placeholder pip command, no dead links.
  **This is what is currently deployed** (as `index.html`) at rapierruntime.com.
- **`index.html`** — the full MVP landing page. Swaps in at M4/launch once the
  placeholders below are resolved.

## Status: launch-ready (M4 pass 2026-07-08) — swap after PyPI is live

Resolved for launch:

1. ✅ **`pip install rapier-runtime`** — package name confirmed (available on PyPI).
2. ✅ **Links wired** to real URLs: `#paper` → Zenodo concept DOI
   `10.5281/zenodo.21210265`, `#pypi` → the PyPI project page, `#spec` → the **public**
   `muddyone/sparring-publicaccess` spec (`framework/sparring-specification.md`).
   (arXiv link deferred — endorsement pending; wire it in as a fast-follow.)
3. ✅ **Accessibility pass** (Zoe review, 2026-07-08): AA-compliant small-text accent
   token (`--accent-text`, light-only), real `<h2>` on the SPARRING↔Rapier band,
   `aria-live` copy-status region + clipboard `.catch()` fallback, skip-to-content
   link, and the mobile nav kept as a compact second row.

Standing note: **Evidence copy is intentionally number-free.** If you add figures
(catch-rate, grounding %), pull them **verbatim** from the final paper — do not paraphrase.

**Gate:** swap only **after** the PyPI package is live — the `pip install` line and the
`#pypi` link must resolve.

## Deploy

Target: the GoDaddy cPanel VPS at `160.153.180.205`, docroot
`~/public_html/rapierruntime.com/`. DNS + Let's Encrypt (AutoSSL) are already live;
HTTP→HTTPS redirect is set in that directory's `.htaccess`. Deploy is a file copy —
whichever page is current gets uploaded **as `index.html`**:

```bash
# now (interim):
scp site/coming-soon.html <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/index.html
# at launch (once placeholders are resolved):
scp site/index.html       <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/index.html
```

The previous file is kept server-side as `index.html.prev` on each deploy.
