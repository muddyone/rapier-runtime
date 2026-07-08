# Rapier Runtime

**A code-orchestrated engine that runs the SPARRING method** — grounded,
cross-vendor adversarial review for AI-in-the-loop decisions.

Most AI tooling helps you *build* agent workflows. Rapier is different: it
*governs* them. It runs a structured, adversarial, grounded review over an AI's
proposed answer — cross-vendor by construction, with mechanical grounding
checks and a correctness gate — so that confident wrongness is structurally
hard to ship. The method is [SPARRING](https://github.com/muddyone/sparring-publicaccess);
Rapier is the runtime that executes it.

> **Method vs. runtime.** *SPARRING* is the method (the concept, described in
> its own papers and framework). *Rapier* is the runtime that executes a
> SPARRING method declared in a manifest. Upgrade the runtime, or swap a model
> in a manifest, without changing the method.

Free and open source (Apache-2.0). Not monetized.

---

## Status: pre-alpha — the full ceremony runs end-to-end (M0–M3 complete; M4 in progress)

This is early software, pinned pre-1.0 — but the whole method runs. What works
today:

- **The full SPARRING ceremony, end-to-end.** The **Proposer** (divergent
  generation → false-novelty filter → the Cut — a cross-vendor convergence loop)
  hands a committed option to the **Resolver** (author → cross-vendor review →
  anchored correction → a correctness *definitiveness* gate → an external-canon
  citation gate → a two-part report). Run the Resolver alone (`spar`) or the
  whole loop (`sparring`).
- **Cross-vendor by construction.** Author and reviewer are always distinct
  vendors when two keys are present, and it degrades *honestly* to single-vendor
  (and says so) when only one is. Any role can be Anthropic, OpenAI, Gemini,
  Grok, or any OpenAI-compatible endpoint — vendor and model names live only in
  a manifest.
- **A manifest *is* the method.** Reorder stages, swap a model, or point two
  roles at two vendors — with no engine-code change. Built-in presets (`spar`,
  `sparring`, `proposer`) cover the common cases; `--settle` and `--verify` tune
  the resolver.
- **Grounding + a correctness gate.** The definitiveness gate checks that every
  hard specific in the answer is traceable to the given facts or explicitly
  flagged as an estimate; the citation gate resolves cited external canon
  (CWE / RFC / DOI / …). Both wrap one shared, battle-tested verification
  service — no duplicate copies.
- **Auditable and safe by design.** Opt-in, redacted, owner-only run
  persistence; a verbatim model-call transcript; env-only secrets + redaction;
  `yaml.safe_load`; a [threat model](docs/threat-model.md) and a
  [security policy](SECURITY.md).

116 tests pass. Cross-vendor runs are live-proven (Anthropic×OpenAI, Gemini×Grok).

**Honest boundary.** The definitiveness gate, anchored correction, and the
two-part trust rider are *exploratory* governance instruments — useful, but not
yet validated by a pre-registered study. Rapier makes confident wrongness
structurally *harder to ship*; it does not make an answer correct.

## Quickstart

```bash
pip install .          # or: pip install git+https://github.com/muddyone/rapier-runtime.git

export ANTHROPIC_API_KEY=...    # author + gate
export OPENAI_API_KEY=...       # a distinct cross-vendor reviewer
                                # (or GEMINI_API_KEY / XAI_API_KEY)

rapier spar     --request "Should we adopt Kubernetes for one flat-traffic web app?"
rapier sparring --request "Monorepo or separate repos for our three services?"
```

`spar` runs the Resolver on a chosen option; `sparring` runs the full four-phase
ceremony. Add `--settle N` for extra decision-stability rounds, or
`--verify off|gate|round` to tune the citation gate. Point `--ledger-dir` at a
directory to persist the run's transcript, report, and records.

No keys? The `mock` vendor needs none:

```bash
rapier run --manifest manifests/echo.yaml --request "should we ship X?"
# -> [mock:rapier-echo-1] should we ship X?
```

Runtime dependencies are just `requests` and `pyyaml` — every vendor is called
over the wire; no provider SDKs.

## Configure your keys

Rapier reads vendor keys from the **environment only** — never from a file it
reads itself. Scaffold and check your setup:

```bash
rapier init      # writes .env.example (key names, no values)
# fill in .env, then load it into your shell:
set -a; source .env; set +a
rapier doctor    # shows which vendors are configured (names only — never values)
```

`doctor` reports each vendor's key env var as set/unset and whether cross-vendor
review is available (two or more keys). A ceremony launched with **no** keys
fails loudly with an actionable message instead of producing empty output.

### Keep your keys loaded

Environment variables last only for the current shell, so `source .env` sets
them up for **this session** — a new terminal starts empty. That's the same as
`aws`, `gh`, and most API-key CLIs, and it's deliberate here: Rapier reads keys
only from the environment and never persists a secret itself. To avoid re-running
the command each time, set it up once:

- **Load in every shell** — add the source line to your shell profile (e.g.
  `~/.bashrc` or `~/.zshrc`), pointing at a keys file you keep outside any repo:

  ```bash
  # add once to ~/.bashrc
  set -a; source ~/.config/rapier/keys.env; set +a
  ```

  Simplest for a personal machine. (Trade-off: the keys then load into *every*
  shell, and live in a file on disk — fine for your own box, less so on a shared
  one.)

- **Load per directory — [`direnv`](https://direnv.net/)** — auto-loads a
  project's `.env` when you `cd` in and unloads it when you leave. Cleaner
  isolation; a one-time `direnv allow` per directory.

- **Per session** — just run `set -a; source .env; set +a` when you sit down to
  use it. Fine for occasional use.

One gotcha: `source` runs the file as shell, so every value with spaces must be
**quoted** (`FOO="a b c"`, not `FOO=a b c`) or `source` will try to run the extra
words as a command. If sourcing errors, that's usually the cause.

## Use it from an MCP client (optional)

Expose `spar` / `sparring` (and a `rapier_doctor` check) as tools to any MCP
client — Claude Desktop, editors, agents:

```bash
pip install "rapier-runtime[mcp]"
```

Point the client at the stdio server. Keys travel in the server's `env` block —
the client launches the process with them, and the engine reads them from the
environment (it still reads no secret from a file):

```jsonc
{
  "mcpServers": {
    "rapier": {
      "command": "rapier",
      "args": ["mcp"],
      "env": { "ANTHROPIC_API_KEY": "…", "OPENAI_API_KEY": "…" }
    }
  }
}
```

## Updating & staying current

Check your version, and update to the latest release:

```bash
rapier --version                    # what you have (also shown by `rapier doctor`)
pip install -U rapier-runtime       # update to the latest ( add [mcp] if you use the MCP server )
```

Because Rapier can read files, fetch URLs, and call model vendors, **staying
current matters for security.** How to hear about issues:

- **Security fixes** are published as **GitHub Security Advisories**, which flow
  into the Python vulnerability databases — so `pip-audit` and GitHub Dependabot
  will flag an affected version automatically if you use them.
- **Watch [Releases](https://github.com/muddyone/rapier-runtime/releases)** on
  GitHub (Watch → Custom → Releases) to be notified of new versions.
- **Found a vulnerability?** Report it privately — see
  [`SECURITY.md`](SECURITY.md). Please don't open a public issue.

Rapier does **not** phone home to check for updates — nothing about your usage
leaves your machine except the model calls you configure.

## A manifest is the method

```yaml
name: echo
pipeline:
  - stage: echo
    config: { note: "hello from Rapier" }
    roles:
      author: { vendor: mock, model: rapier-echo-1 }
```

Editing the manifest changes the method — reorder stages, swap a model, point
two roles at two different vendors — without touching engine code.

## Roadmap

| Milestone | What | |
|---|---|---|
| M0 | Skeleton + threat model + security baseline | ✅ |
| M1 | Resolver ported (one shared grounding/verification service) | ✅ |
| M2 | The Proposer (convergence primitive; SPARK / Pattern Lock / the Cut; cross-vendor roles) | ✅ |
| M3 | Full controller + the `spar` / `sparring` adapters (the whole ceremony end-to-end) | ✅ |
| M4 | Hardening + packaging + first public release | in progress |

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

## License

Apache-2.0. See [LICENSE](LICENSE).
