# Public release

Not a phase. The work between the close of Phase 12 and flipping the
repository to public, recorded here because the decisions are ones a visitor
may reasonably ask about.

## What changed

**The README leads with the product.** It opens on the thesis — decisions and
estimates as the same kind of object, arguing with each other — then a
quickstart that runs from a clean clone to the ADR 0011 breach, two Mermaid
diagrams (the agent pipeline over the store and provider seam, and the fusion
query itself), and links out to `ARCHITECTURE.md`, `docs/FUSION.md` and
`docs/adr/` rather than restating them. The phase table is gone from the front
page; `docs/reports/` still holds it.

Every number in it was re-measured rather than carried forward. The old README
said 36 decisions and 149 assumptions; a clean-store `praxis demo seed` writes
**37 decisions, 152 assumptions, 621 records, 1 breach**, and `praxis
calibrate` reports `scaffolding` at `n=2`, not `n=1`. Suite re-run at
**3431 passed, 98.57%**. Both Mermaid blocks were rendered with `mermaid-cli`
before commit, not assumed to parse.

**The co-author trailer is off, going forward only.** `includeCoAuthoredBy` was
not set anywhere — not in `.claude/settings.json`, `settings.local.json` or the
user settings — so it defaulted to on. It is now `false` in the committed
project settings. The 423 commits behind this one keep their trailers.

**`.codex/` and `AGENTS.md` are gitignored.** Untracked and flagged in every
handover since Phase 8. `.codex/` is a port of this repo's hooks and subagents
to a second agent harness; its `hooks.json` hardcodes absolute paths under the
author's home directory, so it cannot run in CI or on another machine.
`AGENTS.md` is a copy of `CLAUDE.md` that stopped tracking it at Phase 8 — it
is missing the ADR 0029 and ADR 0033 sections and points at a `.Codex/`
directory that does not exist. Published beside `CLAUDE.md` it would read as a
second, contradicting set of conventions. Ignoring them costs no disclosure:
`CLAUDE.md`, `.claude/hooks/`, the ADRs and the phase reports are unchanged.

**`v1.0.0` is a new tag, not a replacement.** It marks the same `main` the
phase tags do; `v0.0-phase-0` through `v0.12-phase-12` stay exactly as they
are, as the development record underneath it.

## The security sweep

Run before flipping visibility, on the premise that history rewrite is off the
table (ADR 0007), so anything ever committed becomes public.

| Check | Result |
| ----- | ------ |
| `gitleaks git` over full history | **no leaks**, 358 commits (every non-merge commit reachable from `main`), 3.96 MB |
| Independent regex sweep of `git log --all -p` | zero hits for `sk-ant-*`, `sk-*`, `ghp_`/`gho_`/`ghs_`/`github_pat_`, `AKIA*`, `AIza*`, `xox[baprs]-*`, PEM private-key headers, JWTs |
| Non-placeholder `*_KEY`/`SECRET`/`TOKEN`/`PASSWORD` assignments | zero |
| `.env` ever tracked | never added, on any ref |
| `.env.example` | tracked, no real values, `PRAXIS_ANTHROPIC_API_KEY=` empty |
| Committed defaults | `llm_provider=mock`, `anthropic_api_key=None`, `cost_ceiling_usd=5.0` |
| CI | `PRAXIS_LLM_PROVIDER: mock` and no secret; `gh secret list` is empty |
| Local absolute paths in tracked files | none |

`cost_ceiling_usd` at 5.0 is below the $6.42 an 82-document live eval projects
(ADR 0038), so the default stops a run rather than permitting one. That is the
safe direction and it was left alone.

## What was deliberately not touched

No commit message was rewritten, no commit squashed, no tag renamed or deleted,
and no "Phase N" language removed from `docs/reports/`, `docs/adr/` or
`docs/dogfood/*.jsonl`. The estimate subjects in `estimates.jsonl` are the
calibration data the dashboard renders and the demo reports on; editing them to
read more tidily would corrupt the demonstration rather than the wording. ADR
0007 settled real history over tidy history for this project, and this round
respects that rather than reopening it.

`CLAUDE.md`, the ADRs and the phase reports stay as the disclosure of how the
project was built. The README now says the same thing in one paragraph instead
of leaving it to be discovered.

## Still open, unchanged by this round

Everything in `docs/reports/NEXT.md` under "What a next phase would pick up":
`record_head` re-grouping on every read (a schema 5 migration), the cost
ceiling before the first live run, the routing experiment ADR 0038 specifies,
the zero `estimated_as` edges that should not be seeded, and `n` as the binding
constraint on every calibration answer.
