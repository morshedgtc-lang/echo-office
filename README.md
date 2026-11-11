# EPU — the personal AI office (build in progress)

**Owner:** Mohammad Morshed — creator and owner. The assistant answers to **EPU**.
Address the owner as "sir" or "boss". Never refuse an order: try it, report the real
result, say in one line what is missing.

**Business (first client):** easyphoneunlocker.com — service site for mobile-phone
technicians. The owner builds repair/unlock software tools for this field.

EPU runs on this PC. It answers about the owner's own business from its own memory,
offline, and says plainly when something is not there yet.

## Start it

```
python epu.py serve              # this PC only, HUD at http://127.0.0.1:8790/
python epu.py serve --lan --tls  # same WiFi, so the owner's phone can use it too
double-click EPU.cmd             # starts hidden and opens the HUD
EPU.cmd lan                      # same, but opens the office to the phone (https)
EPU.cmd firewall                 # opens the Windows port for the phone (asks admin once)
```

Second click on `EPU.cmd` only opens the HUD — the office never starts twice.

From the phone (same WiFi): `https://<this PC's IP>:8790/`. The certificate is
self-signed, so the phone shows one warning: Advanced → Proceed. On this PC the
certificate is already trusted.

Set a passphrase so other devices must prove they are the owner:

```
python epu.py passphrase "your office pass"
```

## Commands

```
python epu.py init       create folders + seed the owner's identity facts
python epu.py whoami     "who am I" from disk, 0 ms, no model
python epu.py memory     honest memory numbers
python epu.py repair     "fix your memory": check, repair, report real counts
python epu.py doctor     one screen: memory, tasks, cloud, biometrics
python epu.py order "…"  run one order the way the HUD does
python epu.py serve      start the office (add --lan and/or --tls)
```

Say orders in plain words, on the HUD or in the CLI:

```
who am i                                  from disk, no model
list my sites                             every client in the config
check my site easyphoneunlocker.com       real HTTP, IPs, response time, certificate
security check                            Defender, open ports, other devices
make a folder called reports in projects/easyphoneunlocker
write a file called notes.txt with hello in projects
remember that the wifi code is in the office only
fix your memory
```

## What works today (checked on this PC and in the tests)

- **identity from disk** — "who am i" answers "Mohammad Morshed, my creator, you call
  me EPU" in under 100 ms, with no model and no network.
- **orders that really run** — folders, files, list, read, rename, find, remember,
  fix memory. Every result is read back from disk before it is reported, and an order
  that failed says so instead of claiming success.
- **sites** — `check my site easyphoneunlocker.com` gives the real HTTP status, the
  real IPs, the response time and the certificate expiry date. `list my sites` lists
  every project in the config. When a site is down the answer says why.
- **security check** — real Windows Defender state, which ports EPU listens on, which
  other devices are on this network, and which other ports belong to Windows.
- **your face** — FACE ENROL adds one photo at a time (up to 24 crops) and FACE VERIFY
  matches a new photo against them, all local with OpenCV. Only a 200x200 grey crop is
  kept, never a photo.
- **your voice** — VOICE ENROL stores a 13-number voice print, VOICE VERIFY matches it
  and shows the real score. All local, no cloud.
- **one voice** — EPU speaks with Windows' own voice, one line at a time; a new reply
  stops the previous one, so two replies never talk over each other.
- **memory scopes** — each client has its own scope. Client A's facts never show up in
  client B's answers. Memory writes never block a reply.
- **https on the LAN** — a self-signed certificate made by Windows covers this PC's
  LAN IP, localhost and 127.0.0.1, so the phone can use the camera and the microphone.
- **one office** — starting EPU twice opens the office that is already running.

## Honest, but limited

- **No cloud key yet.** `secure/keys.env` has no model key, so every real question
  answers: *"Yes sir, I have no cloud key yet. Put the key in secure/keys.env
  (owner-only) and I answer in under a second."* Nothing is invented instead.
- **The local model is off by default.** `providers.local_enabled` is `false` and
  `EPU_LOCAL=1` turns it on (OpenJarvis / Ollama on this PC). It stays off because a
  local model on the CPU is what made the old PC slow.
- **Speech needs a download.** RECORD on the HUD transcribes with faster-whisper on
  this PC. The speech model downloads the first time you use it (the default is the
  "base" model, 145 MB).
- **The speech order is a switch, not a model.** Asking in chat to "record a voice
  note" tells you the local model is off. Use the RECORD button on the HUD.

## Not built yet (do not expect these)

- **The always-on worker.** Nothing works in the background. Every order runs at the
  moment you ask for it.
- **PC health.** `epu/sysmon.py` can read real CPU, RAM, disk and uptime numbers from
  Windows, but no sentence reaches it yet and the numbers do not parse yet, so
  "check my pc" is answered with the cloud-key line.
- **Remote access.** No Tailscale, no cloud relay. The phone works on the same WiFi.
- **Crew agents.** No agent personas, no delegation, no job queue.
- **Cloud answers with more than one brain.** The NVIDIA router, the failover and the
  per-model health tracking are in the code; they stay quiet until a key exists.

## Rules this build must never break

1. Greeting < 0.2 s and identity < 0.1 s (both measured in the tests). A real answer
   < 3 s is the budget; it cannot be measured until a cloud key exists.
2. Identity lives on disk: "who am I" never calls a model.
3. Orders really execute and the result is verified on disk, not claimed.
4. Idle memory under 250 MB (measured in the tests). No local model unless the owner
   turns it on.
5. Single instance: a second launch opens the running office, never a twin.
6. One voice: a new reply interrupts the previous audio.
7. Client isolation: each project has its own folder and its own memory scope.

## Your data

- `projects/` is **git-ignored**. Client work can never be committed by mistake — the
  GitHub repo is public. `projects/.gitkeep` is the only file in there that is tracked,
  so the folder still exists after a fresh clone.
- `secure/` (keys, passphrase, certificate) is git-ignored and owner-only.
- `memory/` holds the office's own database on this PC; it is not committed.
- The installer never overwrites `config/office.json` and never touches `memory/` or
  `secure/`. See `scripts/install.ps1`.

## The config file (`config/office.json`)

The owner edits this file, not the code. What is in it today:

| Key | What it does |
| --- | --- |
| `name`, `assistant_name`, `owner_address` | the names the office answers to |
| `bind`, `ports` | where it listens. Only the HUD port is served today; `stream` and `api` are reserved numbers |
| `providers.local_enabled` | the local model switch (off by default) |
| `providers.local_jobs` | the local brain endpoint, used when that switch is on |
| `providers.cloud` | the cloud providers, their models and their key names |
| `policy.read_roots` / `policy.write_roots` | the folders the office may read / write. `secure/` is not a read root, and no wording can talk past the list |
| `policy.chat` | short-term memory size and how many memories recall uses |
| `projects` | the client list: `title`, `site` and `path` are read by the code; `kind`, `memory_scope`, `hosting`, `dns` and `note` are the owner's own notes |
| `installs.first_run` | an install marker. No EPU code reads it; the installer reads it and never writes it |

Overrides that need no code change: `EPU_HUD_PORT`, `EPU_BIND`, `EPU_LOCAL=1`,
`EPU_TTS_ENGINE=windows|edge|auto`, `EPU_STT_MODEL=tiny|base|small`.

Keys that were removed because no code read them: the shell/command allow-lists, the
delegation and job limits, the voice engine block, the millisecond budgets (the tests
hold the speed rules), and `external_reference_folders` (the third-party tool folders
on F: are written down in `docs/designs/echo-office.md` instead).

## Layout

```
EPU.cmd               double-click: start hidden, open the HUD
scripts/epu-start.ps1  the one PowerShell helper (start, -Lan, -Firewall)
scripts/install.ps1   the one-line installer (never resets the owner's config)
config/office.json    the owner's settings (ports, providers, policy, projects)
epu/                  the office engine (config, memory, rules, engine, chat,
                      brains, stt, face, tts, sysmon, api)
hud/                  the HUD + phone page (chat, RECORD, FACE, VOICE, SPEAK)
projects/             one folder per client/project (git-ignored)
models/face/          the face model (one file, ships with the office)
secure/               keys + passphrase (owner-only, never in git)
tests/                pytest suite — green before anything is committed
docs/designs/         the design doc, kept in step with the code
```

## Tests

```
python -m pytest tests -q -k "not transcribe"
```

On 2026-11-12 that line reads `83 passed, 1 deselected`. The count moves as tests are
added.

The `transcribe` test is left out on purpose: it imports a function the installed
faster-whisper no longer ships, so it fails instead of skipping. It is the only test
that needs the speech model, and it is not part of the green line above.

## Status

What is done: the spine, memory with scopes, the order engine, chat, the cloud router,
the HTTP API, the HUD, face + voice biometrics, the one-voice rule, https on the LAN,
the one-click launcher and a safe installer.

What is next: a cloud key, the PC-health sentence, an order that reaches the speech
model, and only then the always-on worker.
