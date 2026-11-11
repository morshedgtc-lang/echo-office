# EPU — the personal AI office (build in progress)

**Owner:** Mohammad Morshed — creator and owner. The assistant answers to **EPU**.
Address the owner as "sir" or "boss". Never refuse an order: try it, report the real
result, say in one line what is missing.

**Business (first client):** easyphoneunlocker.com — service site for mobile-phone
technicians. The owner builds repair/unlock software tools for this field.

## Rules this build must never break (each has a test)
1. Greeting < 0.2 s, real answer < 3 s. Memory writes never block a reply.
2. Identity lives on disk: "who am I" answers from facts, not from a model.
3. Orders really execute and the result is verified on disk, not claimed.
4. Idle cost: CPU < 1 %, RAM < 250 MB. No local model unless the owner turns it on.
5. Single instance: a second launch opens the running office, never a twin.
6. One voice: a new reply interrupts the previous audio.
7. Client isolation: each project has its own folder and its own memory scope.

## Layout
```
config/     office.json (ports, providers, limits, policy)
epu/        the office engine (config, memory, rules, brains, tools, crew, worker, api, voice)
projects/   one folder per client/project, each with its own memory scope
hud/        the HUD + phone page (static, no build step)
tests/      pytest suite — green before anything is committed
secure/     keys only — gitignored, owner-only
```

## Status
Design approved 2026-11-12. Build step 1 of 6: config + memory + test spine.
