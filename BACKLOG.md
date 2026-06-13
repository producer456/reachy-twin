# Reachy — Backlog

Open work as of 2026-06-13. Done items live in git history / the project memory.

## Needs David (can't be done from code)
- **Canvas grades** — `canvas_grades` returns "session expired". Re-link the Canvas
  cookie session in the Marcus browser-extension settings to revive it.
- **Voice-gate calibration (ongoing)** — `only_david` verify gate is **0.82**.
  David's clean speech scores 0.89–0.94, casual 0.83–0.85, strangers/video ~0.79.
  If Reachy ever ignores David while the gate is on, read the logged
  `voice-id match=…` line and nudge `SPK_VERIFY_GATE` (web_ui.py) between the
  stranger band and his real score. The voiceprint keeps enriching as he talks.
- **Listening is OFF** by choice (2026-06-13). Flip it back on from the app/panel
  when wanted; it now persists as the durable preference.

## Deploy / push gaps
- **Marcus server (vr-2) push to GitHub** — today's Gmail/Calendar/iCloud + `/api/tool`
  + reminder-parse/lock commits are committed on vr-2 but NOT pushed (the sandbox
  classifier blocks a push from vr-2). Run:
  `ssh David@vr-2 "cd Heretic-Gemma3 && git push origin master"`
- **reachy-twin → `upstream` (producer456hub)** — only `origin` (producer456) is
  pushed; this Mac isn't authed for producer456hub. Needs that account's auth or
  producer456 added as a collaborator.
- **reachy-vision has NO git remote** — the threadpool/truncated-JPEG fix (commit
  b7fd6fa) is local-only. Add a remote + push if it should be backed up.

## Code follow-ups (nice-to-have, not bugs)
- **iOS view divergence** — ReachyPad `ControlsView` and MarcusPad
  `ReachyControlsView` are near-duplicate copies; same fix has now been applied to
  both twice. Extract the shared poll/toggle logic into one component to stop the
  drift (bug-hunt finding #5 flagged this).
- **MarcusBrain auto-memory** — `MarcusBrain._ask_marcus` does NOT send
  `auto_memory:false`, so every spoken Reachy chat runs server-side auto-memory and
  can write to David's real Marcus memory. May be intended (talking to Reachy =
  talking to Marcus). David's call: keep, or gate it.
- **Bug-hunt coverage gap** — two verified rounds covered hub/brains/voice/room_memory
  (r1) and Marcus-Reachy-endpoints/vision/panel/io/iOS/motion (r2). NOT deeply swept:
  the Marcus server's NON-Reachy surfaces (the 10k-line web_ui.py at large) and the
  daemon/supervisor reconnect edge cases. A future targeted hunt could go there.

## Feature ideas (not started — see project_reachypad memory for detail)
- Reachy speaks reminders/timers/alarms physically (voice + attention gesture when due).
- Reachy "likes" physical objects shown to its camera (VLM recognize → delight).
- Music groove-buddy for NoteLab / Legion Stage (DAW transport beat-sync + live onset).
- WSL2-on-vr-2 migration (BIOS-virtualization blocked) — now mostly moot since the
  camera works on the Mac host; revisit only if Reachy moves back to vr-2.
