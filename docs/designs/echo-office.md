# EPU — build plan for the personal AI office (design doc copy)

Source: `~/.gstack/projects/echo-office/morshed-master-design-20261112-003442.md`
(startup mode, DRAFT, awaiting owner approval). This copy is kept in step with the
code: anything below marked **done** exists and is tested today.

Owner: **Mohammad Morshed** — creator and owner. The assistant answers to **EPU**.
Repo: `morshedgtc-lang/echo-office`, root `F:\ECHO`.

Owner's business (his words, 2026-11-12): *"i am woring mobile phone repair software and
unlock and i am build tools for this fild used techician to reapir monile phone i have a
website and i am give many service to mobile phone techinan my site naem is
easyphoneunlocker.com"*

**Client 1 (self): easyphoneunlocker.com** — the service site for phone technicians.
**Own product lines:** repair/unlock software tools for technicians.
Third-party tool folders on F: (oppo, realme flash file, iRemoveTools_A12_9.5_win,
UnlockTool-2026-11-05-0, UnlockTool_2026-10-31-0, TSM-PRO Edition) are external
references, never the office's own code. (This list used to sit in
`config/office.json` under `external_reference_folders`, which no code read; it lives
here now, so the config only claims what the code uses.)

## Wedge (changed after the CEO review)

**EPU answers about MY business, from memory, offline, in under 3 s, on the PC and on
the phone.**

The owner asks who he is, what his standing orders are, what a client's site is doing,
or says "make a folder for this client's reports" — and the answer comes from this
machine's own memory and its own tools. No cloud key is needed for the paths that
matter, and when a key is missing the office says so in one line instead of guessing.

The old wedge — the site/coding worker ("work on client X's site" → fix in a branch,
run the tests) — is **demoted to a later phase**. It needs the always-on worker, the
shell tools and the job queue that do not exist yet, and it was pulling the build
towards machinery the owner did not ask for first.

## What exists today

1. `config` + `memory` (SQLite, scoped, best-effort writes, repair + index rebuild).
2. `brains`: cloud router (NVIDIA NIM), health tracking, failover, and a plain
   "no cloud key yet" answer when no key is set.
3. `engine`: the order engine — folders, files, list, read, rename, find, remember,
   memory repair, site check, security check — every result verified on disk.
4. `api` + `hud`: chat, face and voice biometrics, one-voice speech, https.
5. Launcher (`EPU.cmd` + `scripts/epu-start.ps1`) and a one-line installer
   (`scripts/install.ps1`) that never resets the owner's config.

## CEO review — 2026-11-12 (adversarial, four critical findings)

The review ran against the build as it stood. All four were real:

1. **File-read leak on `secure/`.** The office must never read its own secrets: an
   order had to be stopped in code, not by wording. Fixed: `secure/` is not in
   `policy.read_roots`, and the guard refuses any order naming a protected folder
   before a tool runs. Test: *an order outside the write folders is refused by code*.
2. **Unscoped chat history.** One client's words could travel into another client's
   answer. Fixed: chat history is scope-scoped, so a scope is a wall.
3. **Single-file memory.** One SQLite file holds facts, chat history and the index,
   with no story for growth beyond "back it up". Still open by design for now: the
   file is small, the repair command works, and the next step is a written backup
   policy — not a second database.
4. **Installer config reset.** `scripts/install.ps1` copied repo files over the live
   office with `-Force`, which silently overwrote the owner's client list and DNS
   notes. Fixed: the installer never overwrites `config/office.json` (a changed file
   is written beside it as `office.json.new` with a one-line summary), never touches
   `memory/` or `secure/`, and is safe to re-run.

Also cut in the same pass, because it was distribution machinery for one user: the
four one-click launchers (only `EPU.cmd` and one PowerShell helper remain), the laptop
installer and the laptop launcher (two offices = a split memory brain; the PC is the
office), every config key no code reads, and `projects/` was added to `.gitignore` so
client work can never be committed to a public repo by mistake.

## Hard constraints (each has a test, except where noted)

1. Reply speed: greeting < 0.2 s, identity < 0.1 s, memory never blocks a reply.
   (A real answer < 3 s is the budget; it cannot be measured until a cloud key exists.)
2. Identity from disk: "who am I" → "Mohammad Morshed, my creator, you call me EPU."
3. Orders really execute and the result is verified on disk, never claimed.
4. Idle cost: RSS under 250 MB; no local model unless the owner turns it on.
5. Single instance: a second launch opens the running office, never a twin.
6. One voice: a new reply interrupts the old audio; only one player ever.
7. Client isolation: each client has its own folder + own memory scope.

## Build order (one green commit each)

1. `config` + `memory` + test spine. **done**
2. `brains`: cloud router, health, failover, honest "no key". **done**
3. `engine`: the order engine with verification. **done**
4. `api` + `hud` + biometrics + one voice + https. **done**
5. Launcher + a safe installer + the public-repo safety rules. **done**
6. Next: a cloud key, the PC-health sentence, an order that reaches the speech model.
7. Later: the always-on worker and the site/coding worker (the old wedge).

## Owner decisions still open

- Remote access: Tailscale (free) vs LAN-only. Today it is LAN-only.
- Cloud keys: NVIDIA NIM only, or more providers.
- The first real job for easyphoneunlocker.com (site content, service page, support
  reply?).

## Review process

gstack 1.99.1.0 installed (skills for Claude + OpenCode). Every build step passes
`/verify` (checks + tests) then `/review` before it lands. `/qa` opens the real HUD and
tests it. `/ship` pushes and opens the PR. `/cso` is unavailable here (needs the Visual
Studio C++ toolchain; optional, not required for the wedge).
