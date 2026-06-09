# ReachyPad — native iPad controller for Reachy Mini  (resume keyword: REACHYPAD)

Pivot from the Windows web panel to a **native iPadOS app**. The Windows build
(this repo's `twin/` hub + panel) stays as-is — we come back to it later. iPad work
happens in **Xcode on the Mac** (`davids-macbook-pro`, reachable via Tailscale SSH,
key `id_ed25519_mac`).

## Target
- **iPad Pro 10.5" (2017, A10X), iPadOS 17** (its max). Lightning. SwiftUI.
- Frameworks: SwiftUI, **Vision** (face detection), URLSession/Combine. (Speech/AVSpeech NOT
  needed for v1 — voice stays on the robot, see below.)

## Architecture (decided)
The Lite is a USB robot → it **needs a host computer running the daemon** (motors/camera/
audio over USB). The iPad can't be that host. So:
- **Host (Windows laptop now, or vr-2/Pi later):** keeps the daemon **+ our Python `hub`**
  (`python -m twin.panel`, http://HOST:8500). The voice loop (robot mic → Whisper → brain →
  Kokoro TTS → robot speaker) and both brains stay HOST-side. Claude stays FREE via the
  host's Claude CLI (no API key). User chose **robot mic+speaker** for voice.
- **iPad app ("ReachyPad"):** native SwiftUI control surface that talks to the hub HTTP API
  over WiFi/Tailscale. "Thick" in UI + it adds **face-tracking via the iPad's own camera**
  (iOS Vision) → sends look-at commands to the robot.

## Why the iPad camera (not the robot's)
Robot camera frames come through the daemon's **GStreamer IPC, which is Linux-only** — dead
on a Windows host (confirmed: get_frame() returns None forever). iOS is Darwin, not Linux, and
doesn't change that (the daemon host is still Windows). So we use the **iPad's camera + Vision**
for face detection and map the face position to a robot head look-at. Sidesteps the whole issue.
(For the robot's OWN POV camera, the daemon would need a Linux host.)

## Hub API the app consumes (host:8500)
- `GET  /api/state`      → {active, listening, volume, brains, behaviors, log[]}
- `POST /api/chat`       {text, brain?}        → routes + speaks
- `POST /api/brain`      {brain}               → claude | marcus
- `POST /api/listen`     {on}                  → robot mic on/off
- `POST /api/volume`     {volume:0-100}
- `GET  /api/moves`      → {emotions[81], dances[20]}
- `POST /api/move`       {kind:"emotion"|"dance", name}
- `POST /api/behavior`   {name:"turn_to_sound"|"face_track"|"emotions_on_cue", on}
- `POST /api/jog`        {part:"pitch|roll|yaw|body|ant", delta_deg}
- `POST /api/center`
- `GET  /api/servos`     → {mode, body_yaw, antenna_left, antenna_right}
- **TODO add** `POST /api/look` {yaw_deg, pitch_deg}  for iPad-camera face-tracking

## v1 build order (on the Mac)
1. Xcode SwiftUI project "ReachyPad", target iPadOS 17, landscape, 10.5".
2. Settings: host base URL (default Tailscale name/IP of the Windows host) + reachability check.
3. Home screen: poll `/api/state` → status (active brain, listening, volume) + live transcript.
4. Chat: text field → `/api/chat`; brain toggle; volume slider; mic on/off.
5. Expressions: load `/api/moves`, grid of emotion + dance buttons → `/api/move`.
6. Behaviors: toggles → `/api/behavior`. Jog pad → `/api/jog` + center. Servo readout.
7. Face-tracking: AVCaptureSession + Vision face detection → map to look-at → new `/api/look`
   (add the endpoint to `twin/hub.py`: compute head pose yaw/pitch, goto_target).

## Status when paused (2026-06-09)
Windows side fully working except robot-camera/face-track (Windows GStreamer limitation) and
antenna-straight-up-on-center (pending PID damp). Hub HTTP API is the stable contract above.
See README.md + memory keyword REACHYTWIN for the host side.
