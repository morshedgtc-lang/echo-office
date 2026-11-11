# EPU — the personal AI office (build in progress)

**Owner:** Mohammad Morshed — creator and owner. The assistant answers to **EPU**.
Address the owner as "sir" or "boss". Never refuse an order: try it, report the real
result, say in one line what is missing.

**Business (first client):** easyphoneunlocker.com — service site for mobile-phone
technicians. The owner builds repair/unlock software tools for this field.

## Start it
```
python epu.py serve              # this PC only, HUD at http://127.0.0.1:8790/
python epu.py serve --lan        # home network, so the owner's laptop can test it
double-click EPU.cmd             # starts hidden and opens the HUD
```
Laptop test (same WiFi): `http://192.168.0.103:8790/`

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
```

## What works today
- **identity from disk** — you are Mohammad Morshed, you call me EPU (0 ms, no model)
- **orders that really run** — folders, files, list, read, rename, find, remember,
  fix memory — and every result is verified on disk before it is reported
- **sites** — `check my site easyphoneunlocker.com` gives real HTTP status, IPs,
  response time and certificate expiry; `list my sites` lists every client
- **your voice** — RECORD on the HUD transcribes speech on this PC (offline); VOICE ENROL
  stores a 13-number voice print and VOICE VERIFY matches it, with the real score
- **your face** — FACE ENROL (5 photos) and FACE VERIFY, all local with OpenCV;
  only a 200x200 face crop is kept, never a photo
- **one voice** — EPU speaks through Windows' own voice, one utterance at a time,
  a new reply interrupts the old one
- **boss's standing orders** — saved in memory, answered from memory

## Rules this build must never break (each has a test)
1. Greeting < 0.2s, real answer < 3s. Memory writes never block a reply.
2. Identity lives on disk: "who am I" < 0.1s.
3. Orders really execute and the result is verified on disk, not claimed.
4. Idle cost: CPU < 1%, RAM < 250MB. No local model unless the owner turns it on.
5. Single instance: a second launch opens the running office, never a twin.
6. One voice: a new reply interrupts the previous audio.
7. Client isolation: each project has its own folder and its own memory scope.

## Layout
```
config/office.json   ports, providers, policy, projects (the owner edits this)
epu/                 the office engine (config, memory, rules, engine, chat,
                     brains, stt, face, tts, api)
hud/                 the HUD + phone page (chat, RECORD, FACE, VOICE, SPEAK)
projects/            one folder per client/project, each with its own memory scope
models/face/         the face model (one file, ships with the office)
secure/              keys + passphrase (owner-only, never in git)
tests/               pytest suite — green before anything is committed
```

## Tests
```
python -m pytest tests -q      # 46 green (one skips until the speech model downloads)
```

## Status
Steps 1-2 of the design: spine, memory, order engine, chat, cloud router, HTTP API,
HUD, face + voice biometrics, one-voice rule, launcher. Remaining: the cloud key,
the free test domain, the always-on worker, and the crew agents.
