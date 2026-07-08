# site/ — rapierruntime.com

The public site for **rapierruntime.com**, the product front door for Rapier Runtime
(the engine that runs the SPARRING method). Both files are self-contained — no build
step, no external assets (inline CSS/JS, system fonts, emoji favicon), theme-aware.

- **`coming-soon.html`** — the interim **"in development"** page. Design-consistent with
  the full landing but safe to serve now: no placeholder pip command, no dead links.
  **This is what is currently deployed** (as `index.html`) at rapierruntime.com.
- **`index.html`** — the full MVP landing page. Swaps in at M4/launch once the
  placeholders below are resolved.

## Status: MVP draft (approved 2026-07-07), pre-M4

Resolve these before it goes live:

1. **`pip install rapier-runtime`** — the PyPI package name is a placeholder; confirm it
   when the package is published (M4).
2. **Dead links** to wire to real URLs: `#paper` (arXiv/Zenodo), `#pypi`, and `#spec`
   (the SPARRING spec — must point at a **public** repo; `muddyone/sparring` is private,
   the public artifacts live in `muddyone/sparring-publicaccess`).
3. **Evidence copy is intentionally number-free.** If you add figures (catch-rate,
   grounding %), pull them **verbatim** from the final paper — do not paraphrase.

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
