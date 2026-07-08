# site/ — rapierruntime.com landing page

The public landing page for **rapierruntime.com**, the product front door for Rapier
Runtime (the engine that runs the SPARRING method).

- **`index.html`** — self-contained, no build step, no external assets (inline CSS/JS,
  system fonts, emoji favicon). Theme-aware (light/dark). Just upload it.

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
HTTP→HTTPS redirect is set in that directory's `.htaccess`. Deploy is a file copy:

```bash
scp site/index.html <cpuser>@160.153.180.205:~/public_html/rapierruntime.com/index.html
```

(A temporary "coming soon" placeholder is currently served there; this replaces it.)
