# MCP Registry Distribution Runbook

Prep-only doc for the registry-blitz lane (2026-07-03). Covers steps 2-4 of the
submission checklist in the source brief:
[[2026-07-02 MCP Registry Distribution + Inbound (research-lite)]] (vault path:
`C:\Users\jaime\projects\the vault\research\2026-07-02 MCP Registry Distribution + Inbound (research-lite).md`).

Step 1 (server.json per repo) is done separately -- see `server.json` in this repo,
`desktop-mcp`, `github-mcp`, `bus-mcp` (all on branch `lane/registry-blitz`).

No account actions, no publish, no push were taken by this lane. Every command
block below is operator-executed.

## Finding that changes the plan: two PyPI names are already taken

Before publishing any `packages` entry, checked whether `mcp-factory` and
`github-mcp` are available on PyPI (`pypi.org/pypi/<name>/json`):

| Package name | PyPI status | Owner |
|---|---|---|
| `mcp-factory` | Taken (200) | ACNet-AI/mcp-factory (unrelated project) |
| `github-mcp` | Taken (200) | LWaetzig/github-mcp (unrelated project) |
| `desktop-mcp` | Free (404) | -- |
| `bus-mcp` | Free (404) | -- |

The official registry's `packages` array requires **verified ownership** of the
referenced package on the target registry (npm/PyPI/etc). We do not own the
`mcp-factory` or `github-mcp` PyPI names, so a `packages` block referencing
them would fail ownership verification even though the JSON would validate.

Consequence: the four `server.json` files in this blitz ship with only
`name` / `description` / `version` / `repository` (all the JSON schema strictly
requires is `name`, `description`, `version` -- confirmed against the live
schema at `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`,
`ServerDetail.required = [name, description, version]`). No `packages` or
`remotes` block is included yet.

**DECIDED 2026-07-03 12:15 MT (operator): `jaimenbell-mcp-factory` + `jaimenbell-github-mcp`** (both verified FREE on PyPI at decision time); desktop-mcp/bus-mcp keep their own free names. pyproject names, server.json packages blocks, and README mcp-name ownership markers all updated in-repo same pass. Original options preserved below.

~~**Operator decision needed**~~ before a real `mcp-publisher publish` will produce
a useful (installable) listing:
- Publish `mcp-factory` and `github-mcp` to PyPI under different available
  names (e.g. `mcp-factory-jaimenbell`, `jaimenbell-github-mcp`), or
- Publish `desktop-mcp` / `bus-mcp` to PyPI under their current (free) names
  and add a `packages` block, or
- Skip `packages` entirely for now -- the registry will still list the server
  (source-visible via `repository`), just without one-command install info.

## 1. mcp-publisher: install + validate (dry-run) -- Windows, PowerShell

`mcp-publisher` has no Windows package-manager install (Homebrew is
macOS/Linux only per the CLI reference doc). Use the release binary. Latest
verified: `v1.7.9` (github.com/modelcontextprotocol/registry/releases).

```powershell
# PowerShell -- download and extract mcp-publisher for Windows amd64
$version = "v1.7.9"
$dest = "$env:USERPROFILE\tools\mcp-publisher"
New-Item -ItemType Directory -Force -Path $dest
$url = "https://github.com/modelcontextprotocol/registry/releases/download/$version/mcp-publisher_windows_amd64.tar.gz"
Invoke-WebRequest -Uri $url -OutFile "$dest\mcp-publisher.tar.gz"
tar -xzf "$dest\mcp-publisher.tar.gz" -C $dest
& "$dest\mcp-publisher.exe" --help
```

```powershell
# PowerShell -- add to PATH for this session, then validate each server.json
$env:Path = "$env:USERPROFILE\tools\mcp-publisher;$env:Path"
mcp-publisher validate C:\Users\jaime\projects\mcp-factory\server.json
mcp-publisher validate C:\Users\jaime\projects\desktop-mcp\server.json
mcp-publisher validate C:\Users\jaime\projects\github-mcp\server.json
mcp-publisher validate C:\Users\jaime\projects\bus-mcp\server.json
```

`validate` only checks the file against the schema -- it does not publish and
does not require login. This is the safe dry-run step. Expected output on
success is `server.json is valid` per file.

### Login + publish (operator-only, NOT run by this lane)

```powershell
# PowerShell -- GitHub OAuth login (opens browser), grants io.github.jaimenbell/* namespace
mcp-publisher login github
```

```powershell
# PowerShell -- publish one server (repeat per repo, run from inside each repo dir)
mcp-publisher publish C:\Users\jaime\projects\mcp-factory\server.json
```

Publishing has no review queue -- it goes live same-day. Do this only after
the PyPI-name decision above is resolved and the operator has confirmed the
description text is factual (no marketing copy -- stuffed descriptions are an
explicit registry removal trigger per the source brief).

## 2. PulseMCP claim checklist

PulseMCP auto-ingests from the official registry on a recurring crawl (source
brief: weekly). Its per-server "Est Visitors (Week)" is the only outside
discovery metric available anywhere in this ecosystem.

| Step | Action | URL |
|---|---|---|
| 1 | Wait for the server to appear post-publish (poll ~weekly) | `https://www.pulsemcp.com/servers` |
| 2 | Search each server by name (`mcp-factory`, `desktop-mcp`, `github-mcp`, `bus-mcp`) | `https://www.pulsemcp.com/servers?q=<name>` |
| 3 | Open the listing, click "Claim this server" (or equivalent claim CTA on the listing page) | per-listing page |
| 4 | Verify ownership -- expect GitHub OAuth or a repo-file challenge, consistent with the registry's own namespace auth | in-flow |
| 5 | Once claimed, fill in any optional fields PulseMCP exposes beyond server.json (logo, extra links) | in-flow |
| 6 | Repeat for all 4 servers | -- |

Operator action: account creation/login on PulseMCP + the 4 claim flows.
Cannot be scripted -- it's a per-listing web claim gated on registry data that
doesn't exist until step 1 (publish) happens.

## 3. Glama claim checklist

Glama crawls the same upstream feed; claiming moves a listing from
crawled-tier to claimed-tier (this is also the gate `awesome-mcp-servers`
PRs require per the source brief -- a live Glama listing is a precondition
for that list now).

| Step | Action | URL |
|---|---|---|
| 1 | Create/log into a Glama account | `https://glama.ai/` |
| 2 | Search MCP servers directory for each repo name | `https://glama.ai/mcp/servers` |
| 3 | Open the listing (appears after registry publish + Glama's crawl cycle) | per-listing page |
| 4 | Claim via GitHub OAuth (same GitHub account as the repo owner, `jaimenbell`) | in-flow |
| 5 | Confirm claimed status shows on the public listing | in-flow |
| 6 | Repeat for all 4 servers | -- |

Operator action: account + 4 claim flows, same shape as PulseMCP. Do this
after PulseMCP so the operator can batch both claim sessions once listings
exist.

## 4. GitHub Sponsors prep

One account-level setup unlocks the Sponsor button on all 4 repos (they share
the `jaimenbell` GitHub account/namespace).

### What to enable (operator, account-level)

| Item | Where | Notes |
|---|---|---|
| Enable GitHub Sponsors | `https://github.com/sponsors/accept` or Settings > Sponsors | Requires bank account + tax info (US: W-9) + 2FA already on the account |
| Sponsor button visibility | Repo Settings > General > Social preview, or add `.github/FUNDING.yml` | `.github/FUNDING.yml` with `github: [jaimenbell]` turns on the Sponsor button per-repo -- additive file, safe to add once Sponsors is live |
| Sponsors bio/profile copy | `https://github.com/sponsors/jaimenbell/dashboard` | Put the jaimenbell.dev consulting link + a one-line pitch here (source brief: never put commercial language in server.json, sell here instead) |

### Suggested tiers (reference the live consulting offers)

Live offers as of 2026-06-22 (validated against market 2026-07-02, see
`the vault\research\2026-06-22 Automation Services — Offer & Positioning.md`):
MCP Integration Sprint $8k (anchor tier) / $5k lean, $12-25k multi-source,
$1.5k paid MCP Spike, $175/hr fallback, $1.5-4k/mo Reliability Retainer.

GitHub Sponsors is a small-dollar recurring-funding channel, not the
transactional sales surface for $1.5k+ project work -- keep tiers modest and
use them as an awareness/lead funnel into the real offers, which get sold
off-platform.

| Tier | Price | What it signals / includes |
|---|---|---|
| Supporter | $5/mo | Name in repo README sponsors section |
| Backer | $25/mo | Above + issue/PR priority triage across the 4 repos |
| Priority Support | $100/mo | Above + direct email/Slack line for setup help -- explicit lead qualifier: pitch the $1.5-4k/mo Reliability Retainer to anyone who sustains this tier 2+ months |
| One-time: Spike | $1.5k one-time (custom amount) | Mirrors the paid MCP Spike offer; bio copy should point serious inquiries to a direct consulting conversation rather than expecting this to close on Sponsors alone |

### README section (additive, operator or follow-up lane)

Not added by this lane (kept diff to server.json + this runbook only). When
Sponsors is live, a "Commercial support" README section per repo can link the
Sponsors profile + jaimenbell.dev -- purely additive, safe for a follow-up
pass once the account-level setup above is done.

## Out of scope for this lane

Source brief also lists `mcp.so` GitHub-issue submission, `awesome-mcp-servers`
PRs (gated on Glama claim above), Smithery (needs a hosted HTTP endpoint --
skip for these local/dev-tool servers), and the Anthropic Connectors
Directory (heavier review, only relevant if a hosted connector ships later).
Not actioned here; candidates for a follow-up lane once steps 1-4 land.
