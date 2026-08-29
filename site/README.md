# site/ — rapierruntime.com

The public site for **rapierruntime.com**, the product front door for Rapier Runtime
(the engine that runs the SPARRING method). No build step — inline CSS/JS, system
fonts. `index.html` is **light-only** and serves the logo master
(`rapier-logo.png`) as a sibling asset in the header and the SPARRING/Rapier band;
`coming-soon.html` is self-contained with an emoji favicon.

- **`coming-soon.html`** — the interim **"in development"** page. Design-consistent with
  the full landing but safe to serve now: no placeholder pip command, no dead links.
  Kept as a rollback target; **not** what is deployed.
- **`index.html`** — the full MVP landing page. **This is what is live** at
  rapierruntime.com (swapped in at launch, 2026-07-16; verified byte-identical
  2026-08-28).

## Status: LAUNCHED 2026-07-16 — the full landing is live

Resolved for launch:

1. ✅ **`pip install rapier-runtime`** — package name confirmed (available on PyPI).
2. ✅ **Links wired** to real URLs: `#paper` → Zenodo concept DOI
   `10.5281/zenodo.21210264` (resolves to latest), `#pypi` → the PyPI project page, `#spec` → the **public**
   `muddyone/sparring-publicaccess` spec (`framework/sparring-specification.md`).
   (arXiv link deferred — endorsement pending; wire it in as a fast-follow.)
3. ✅ **Accessibility pass** (Zoe review, 2026-07-08): AA-compliant small-text accent
   token (`--accent-text`, light-only), real `<h2>` on the SPARRING↔Rapier band,
   `aria-live` copy-status region + clipboard `.catch()` fallback, skip-to-content
   link, and the mobile nav kept as a compact second row.

Standing note: **Evidence copy is intentionally number-free.** If you add figures
(catch-rate, grounding %), pull them **verbatim** from the final paper — do not paraphrase.

**Gate (satisfied):** the swap waited on the PyPI package being live, so the
`pip install` line and the `#pypi` link both resolve. The landing carries no version
string, so a new release needs no site change — re-deploy only when the copy itself
changes.

## Deploy

Target: the GoDaddy cPanel VPS at `160.153.180.205`, docroot
`~/public_html/rapierruntime.com/`. DNS + Let's Encrypt (AutoSSL) are already live;
HTTP→HTTPS redirect is set in that directory's `.htaccess`. Deploy is a file copy —
whichever page is current gets uploaded **as `index.html`**:

```bash
# now (interim):
scp site/coming-soon.html <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/index.html
# the live landing (index.html + its logo asset — upload BOTH):
scp site/index.html      <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/index.html
scp site/rapier-logo.png <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/rapier-logo.png
```

`index.html` references `rapier-logo.png` relatively, so the asset must be present
in the docroot or both logos 404. The previous page is kept server-side as
`index.html.prev` on each deploy.
