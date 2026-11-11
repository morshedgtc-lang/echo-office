# EPU — build plan for the personal AI office (design doc copy)

Source: `~/.gstack/projects/echo-office/morshed-master-design-20261112-003442.md` (startup mode, DRAFT, awaiting owner approval)

Owner: **Mohammad Morshed** — creator and owner. The assistant answers to **EPU**.
Repo (to create): `morshedgtc-lang/echo-office`, root `F:\ECHO`.

Owner's business (his words, 2026-11-12): *"i am woring mobile phone repair software and
unlock and i am build tools for this fild used techician to reapir monile phone i have a
website and i am give many service to mobile phone techinan my site naem is
easyphoneunlocker.com"*

**Client 1 (self): easyphoneunlocker.com** — the service site for phone technicians.
**Own product lines:** repair/unlock software tools for technicians.
Third-party tool folders on F: (oppo, realme flash tools, iRemoveTools, UnlockTool, TSM-PRO)
are external references, never the office's own code.

## Wedge (week 1)
One local office, PC or phone: *"work on client X's site"* → reads that client's folder,
finds the bug, fixes in a branch, runs tests, reports "fixed, tests green, branch ready".

## Hard constraints (each has a test)
1. Reply speed: greeting < 0.2s, real question < 3s, **memory never blocks**.
2. Identity from disk: "who am I" < 0.1s → "Mohammad Morshed, my creator, you call me EPU."
3. Orders really execute: `make a folder called reports in client-acme` creates it, and the
   reply names the verified path.
4. Idle cost: CPU < 1%, RAM < 250 MB; no local LLM unless the owner turns it on.
5. Single instance: a second launch opens the running office, never a twin.
6. One voice: a new reply interrupts the old audio; only one player ever.
7. Client isolation: each client has its own folder + own memory scope.

## Build order (one green commit each)
1. `config` + `memory` (SQLite WAL + FTS, scoped, best-effort writes) + test spine.
2. `brains`: cloud router (NVIDIA NIM primary), health tracking, failover, latency budget.
3. `projects`: client folders, memory scopes, `worker` job queue + verification.
4. `tools`: files / shell / web / browser (Playwright), permission levels.
5. `api`: HTTP + WebSocket stream + phone page + HUD.
6. `voice`: TTS queue, one speaker, language follows the reply (en/bn/hi).
7. Always-on: watcher + worker while the owner is away; morning report.

## Owner decisions still open
- Remote access: Tailscale (free) vs LAN-only.
- Cloud keys: keep NVIDIA NIM only, or add more providers.
- The first real job for easyphoneunlocker.com (site content, service page, support reply?).

## The Assignment (from the design doc)
Send me one real job you want EPU to do this week, in your own words — spoken the way you
would say it out loud.

## Review process
gstack 1.99.1.0 installed (skills for Claude + OpenCode). Every build step passes
`/verify` (checks + tests) then `/review` before it lands. `/qa` opens the real HUD and
tests it. `/ship` pushes and opens the PR. `/cso` is unavailable here (needs the Visual
Studio C++ toolchain; optional, not required for the wedge).
