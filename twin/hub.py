"""RobotHub: one ReachyMini connection shared by the voice loop and the web panel.

Thread-safety: all robot media access (speak + mic capture) is serialized by a single
lock. The mic capture loop grabs the lock per ~10ms chunk, so a panel-triggered say()
can slip in between chunks, hold the lock for its playback, and the capture loop simply
pauses until he's done talking. Brain calls (Claude CLI / Marcus HTTP) are not locked.
"""
import json
import re
import threading
import time
import urllib.request
from collections import deque

import numpy as np
from reachy_mini import ReachyMini

from twin.stt import STT
from twin.tts import KokoroTTS
from twin.app import (
    build_brains, detect_switch, strip_wake, calibrate_floor,
    _rms, _mono, SR, EXIT_WORDS, SPEECH_START, SILENCE_HANG, MIN_CHUNKS,
)
from twin.brains import strip_mood, MOOD_TO_EMOTION

DAEMON = "http://localhost:8000"


class RobotHub:
    def __init__(self):
        self.stt = STT()
        self.tts = KokoroTTS()
        self.brains, self.voices = build_brains()
        self.active = "claude"
        self.mini = None
        self._lock = threading.Lock()
        self._listening = False
        self._stop = threading.Event()
        self._thread = None
        self._thresh = 0.02
        self.log = deque(maxlen=100)
        self.emotions = None       # RecordedMoves (lazy)
        self._dances = None        # list[str]
        # autonomous behaviors
        self.behaviors = {"turn_to_sound": False, "face_track": False, "emotions_on_cue": False}
        self._behavior_stop = threading.Event()
        self._behavior_thread = None
        self._body_yaw = 0.0
        self._last_doa_turn = 0.0
        self.doa_sign = -1.0       # flip if he turns the wrong way (tuned live)
        self._last_face = 0.0
        self._cached_mode = "?"    # /api/motors/status is slow (~2s) -> cache it
        self._mode_ts = 0.0
        self._cascade = None       # opencv face detector (lazy)
        self._pose = {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "body": 0.0, "ant": 0.0}  # degrees

    # ---------- lifecycle ----------
    def start(self):
        self.mini = ReachyMini(media_backend="default")
        self.mini.__enter__()
        self.mini.media.start_recording()
        self.mini.media.start_playing()
        self._thresh = calibrate_floor(self.mini)
        self._load_moves()
        self._log("system", f"online - brains: {', '.join(self.brains)} | gate {self._thresh:.4f}")

    def shutdown(self):
        self.set_listening(False)
        try:
            self.mini.media.stop_recording()
            self.mini.media.stop_playing()
            self.mini.__exit__(None, None, None)
        except Exception:
            pass

    # ---------- logging ----------
    def _log(self, who, text):
        self.log.append({"who": who, "text": text, "t": time.time()})

    # ---------- speech ----------
    def say(self, text, voice=None):
        self._say_with_motion(text, None, voice)

    def _say_with_motion(self, text, move, voice=None):
        """Speak while an (optional) emotion move plays concurrently -- like real body language.

        push_audio_sample is non-blocking and the daemon composes its audio-reactive wobble
        on top of the move's pose, so motion + speech layer instead of fighting.
        """
        if not text:
            return
        voice = voice or self.voices.get(self.active, "af_heart")
        samples = self.tts.synth(text, voice=voice)
        dur = len(samples) / SR
        with self._lock:
            mt = None
            if move is not None:
                def _run():
                    try:
                        self.mini.play_move(move, sound=False)   # motion only; speech is the audio
                    except Exception:
                        pass
                mt = threading.Thread(target=_run, daemon=True)
                mt.start()
            self.mini.media.push_audio_sample(samples)           # speech starts immediately
            t0 = time.time()
            while time.time() - t0 < dur + 0.3:                  # drain mic so we don't hear ourselves
                self.mini.media.get_audio_sample()
            if mt is not None:
                mt.join(timeout=4)

    # ---------- chat ----------
    def chat(self, text, brain=None):
        text = (text or "").strip()
        if not text:
            return {"brain": self.active, "reply": ""}
        if self._maybe_volume_command(text):
            return {"brain": self.active, "reply": "[volume adjusted]", "command": "volume"}
        switched = detect_switch(text, self.active)
        if switched and switched in self.brains:
            self.active = switched
        elif brain and brain in self.brains:
            self.active = brain
        msg = strip_wake(text) if switched else text
        self._log("you", text)
        if not msg.strip():
            reply = f"{self.active.capitalize()} here. What's up?"
        else:
            reply = self.brains[self.active].reply(msg)
        mood, reply = strip_mood(reply)          # pull the brain's [mood] tag off the speech
        self._log(self.active, reply)
        move = None
        if self.behaviors.get("emotions_on_cue"):
            emo = MOOD_TO_EMOTION.get(mood) if mood else self._emotion_for(reply)
            if emo:
                move = self._get_emotion_move(emo)
        self._say_with_motion(reply, move)       # gesture + speech happen together
        return {"brain": self.active, "reply": reply, "mood": mood}

    def set_brain(self, brain):
        if brain in self.brains:
            self.active = brain
        return self.active

    # ---------- listening ----------
    def set_listening(self, on):
        on = bool(on)
        if on and not self._listening:
            self._listening = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._mic_loop, daemon=True)
            self._thread.start()
        elif not on and self._listening:
            self._listening = False
            self._stop.set()
        return self._listening

    def _mic_loop(self):
        while not self._stop.is_set():
            audio = self._capture()
            if audio is None:
                continue
            text = self.stt.transcribe(audio)
            if not text or len(text) < 2:
                continue
            if any(w in text.lower() for w in EXIT_WORDS):
                self.say("Okay, going quiet. Bye.")
                self._listening = False
                self._stop.set()
                break
            self.chat(text)

    def _capture(self, max_seconds=15.0):
        buf, in_speech, run, silence = [], False, 0, 0
        start = time.time()
        while not self._stop.is_set() and time.time() - start < max_seconds:
            with self._lock:
                s = self.mini.media.get_audio_sample()
            if s is None or len(s) == 0:
                continue
            m = _mono(s)
            loud = _rms(m) > self._thresh
            if not in_speech:
                if loud:
                    run += 1
                    buf.append(m)
                    if run >= SPEECH_START:
                        in_speech = True
                else:
                    run, buf = 0, []
            else:
                buf.append(m)
                silence = silence + 1 if not loud else 0
                if silence >= SILENCE_HANG:
                    break
        if not in_speech or len(buf) < MIN_CHUNKS:
            return None
        return np.concatenate(buf).astype(np.float32)

    # ---------- expressive moves (emotions + dances) ----------
    def _load_moves(self):
        if self.emotions is None:
            from reachy_mini.motion.recorded_move import RecordedMoves
            self.emotions = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
        if self._dances is None:
            from reachy_mini_dances_library.collection.dance import AVAILABLE_MOVES
            self._dances = list(AVAILABLE_MOVES)

    def list_moves(self):
        self._load_moves()
        return {"emotions": self.emotions.list_moves(), "dances": self._dances}

    def play(self, kind, name):
        self._load_moves()
        try:
            if kind == "emotion":
                move = self.emotions.get(name)
            elif kind == "dance":
                from reachy_mini_dances_library import DanceMove
                move = DanceMove(name)
            else:
                return {"error": f"unknown kind {kind!r}"}
        except Exception as e:
            return {"error": f"{name}: {e}"}
        with self._lock:
            self.mini.play_move(move)
        self._log("system", f"played {kind}: {name}")
        return {"ok": True, "kind": kind, "name": name}

    # ---------- autonomous behaviors ----------
    def set_behavior(self, name, on):
        if name in self.behaviors:
            self.behaviors[name] = bool(on)
        need_thread = self.behaviors["turn_to_sound"] or self.behaviors["face_track"]
        if need_thread and (self._behavior_thread is None or not self._behavior_thread.is_alive()):
            self._behavior_stop.clear()
            self._behavior_thread = threading.Thread(target=self._behavior_loop, daemon=True)
            self._behavior_thread.start()
        elif not need_thread:
            self._behavior_stop.set()
        return self.behaviors

    def _behavior_loop(self):
        while not self._behavior_stop.is_set() and (
                self.behaviors["turn_to_sound"] or self.behaviors["face_track"]):
            if self.behaviors["face_track"]:
                self._face_track_tick()
            if self.behaviors["turn_to_sound"]:
                self._turn_to_sound_tick()
            time.sleep(0.06)

    HEAD_LOOK_MAX = 0.6   # rad (~34 deg) the head will glance before the body helps

    def _turn_to_sound_tick(self):
        now = time.time()
        if now - self._last_doa_turn < 0.6:
            return
        try:
            with self._lock:
                doa = self.mini.media.get_DoA()
        except Exception:
            return
        if not doa:
            return
        angle, speech = doa
        if not speech:
            return
        delta = angle - (np.pi / 2)                  # offset from front (rad)
        if abs(delta) < 0.2:                         # ignore near-front jitter
            return
        from reachy_mini.utils import create_head_pose
        desired = self.doa_sign * delta              # turn this much toward the source
        head_yaw = max(-self.HEAD_LOOK_MAX, min(self.HEAD_LOOK_MAX, desired))  # head glances first
        body_add = desired - head_yaw                # body covers the rest for big angles
        target_body = max(-2.7, min(2.7, self._body_yaw + body_add))
        head = create_head_pose(yaw=float(np.rad2deg(head_yaw)), degrees=True)
        with self._lock:
            self.mini.goto_target(head=head, body_yaw=target_body, duration=0.4)
        self._body_yaw = target_body
        self._last_doa_turn = now

    def _detect_faces(self, frame):
        import os
        import cv2
        if self._cascade is None:
            self._cascade = cv2.CascadeClassifier(
                os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))

    def _face_track_tick(self):
        now = time.time()
        if now - self._last_face < 0.25:             # ~4 Hz tracking
            return False
        self._last_face = now
        try:
            frame = self.mini.media.get_frame()
        except Exception:
            return False
        if frame is None:
            return False
        faces = self._detect_faces(frame)
        if len(faces) == 0:
            return False
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])   # largest face
        cx, cy = x + w // 2, y + h // 2
        H, W = frame.shape[:2]
        if abs(cx - W / 2) < W * 0.08 and abs(cy - H / 2) < H * 0.08:
            return True                              # already centered -> hold
        with self._lock:
            try:
                self.mini.look_at_image(int(cx), int(cy), duration=0.35, perform_movement=True)
            except Exception:
                pass
        return True

    def _emotion_for(self, text):
        t = text.lower()
        rules = [
            (("thank",), "grateful1"),
            (("haha", "lol", "funny", "hilar"), "cheerful1"),
            (("wow", "whoa", "incredible", "amazing"), "amazed1"),
            (("sorry", "unfortunately", "afraid", "can't", "cannot"), "downcast1"),
            (("hmm", "not sure", "confus", "unclear"), "confused1"),
        ]
        for kws, emo in rules:
            if any(k in t for k in kws):
                return emo
        if "!" in text:
            return "enthusiastic1"
        if "?" in text:
            return "curious1"
        return None

    def _get_emotion_move(self, name):
        try:
            self._load_moves()
            return self.emotions.get(name)
        except Exception:
            return None

    # ---------- camera + manual jog ----------
    def get_jpeg(self, quality=70):
        try:
            frame = self.mini.media.get_frame()
        except Exception:
            return None
        if frame is None:
            return None
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    _JOG_LIMITS = {"pitch": 30, "roll": 30, "yaw": 60, "body": 90, "ant": 90}  # degrees
    ANT_REST_DEG = 8   # antennas held at exactly 0 deg hunt (backlash); a small offset stops it

    def _apply_pose(self):
        from reachy_mini.utils import create_head_pose
        p = self._pose
        head = create_head_pose(roll=p["roll"], pitch=p["pitch"], yaw=p["yaw"], degrees=True)
        ant = np.deg2rad([p["ant"] + self.ANT_REST_DEG, p["ant"] + self.ANT_REST_DEG])
        with self._lock:
            self.mini.goto_target(head=head, body_yaw=np.deg2rad(p["body"]),
                                  antennas=ant, duration=0.35)
        return dict(self._pose)

    def servos(self):
        """Live motor telemetry for the panel. Joint positions come from the SDK's WS
        client (fast); the REST /api/motors/status is slow (~2s) so we cache the mode."""
        out = {"mode": self._cached_mode, "body_yaw": None, "antenna_left": None, "antenna_right": None}
        try:
            with self._lock:
                head, ant = self.mini.get_current_joint_positions()
            out["body_yaw"] = round(float(np.rad2deg(head[0])), 1)   # head[0] = body yaw
            out["antenna_left"] = round(float(np.rad2deg(ant[0])), 1)
            out["antenna_right"] = round(float(np.rad2deg(ant[1])), 1)
        except Exception:
            pass
        now = time.time()
        if now - self._mode_ts > 10:
            try:
                self._cached_mode = json.loads(
                    urllib.request.urlopen(DAEMON + "/api/motors/status", timeout=3).read()).get("mode")
            except Exception:
                pass
            self._mode_ts = now
        out["mode"] = self._cached_mode
        return out

    def jog(self, part, delta):
        if part in self._pose:
            lim = self._JOG_LIMITS[part]
            self._pose[part] = max(-lim, min(lim, self._pose[part] + float(delta)))
        return self._apply_pose()

    def center(self):
        for k in self._pose:
            self._pose[k] = 0.0
        self._body_yaw = 0.0          # keep turn-to-sound's tracking in sync after a recenter
        return self._apply_pose()

    # ---------- volume (proxy daemon) ----------
    def get_volume(self):
        try:
            return json.loads(urllib.request.urlopen(DAEMON + "/api/volume/current", timeout=5).read())
        except Exception as e:
            return {"error": str(e)}

    def _maybe_volume_command(self, text):
        """Intercept spoken/typed volume commands. Returns True if handled (no brain call)."""
        t = text.lower()
        cur = self.get_volume().get("volume", 60) or 60
        target = None
        m = re.search(r"(?:volume|turn\s*(?:it\s*)?(?:up|down)?)\s*(?:to\s*)?(\d{1,3})", t)
        if "mute" in t:
            target = 0
        elif "volume" in t and re.search(r"\b(max|full|loudest|all the way)\b", t):
            target = 100
        elif m:
            target = int(m.group(1))
        elif re.search(r"\b(quieter|too loud|turn it down|turn down|lower|softer|down a bit|less loud)\b", t):
            target = cur - 15
        elif re.search(r"\b(louder|speak up|turn it up|turn up|can'?t hear|volume up|more volume)\b", t):
            target = cur + 15
        if target is None:
            return False
        target = max(0, min(100, target))
        self.set_volume(target)
        self._log("you", text)
        self._log("system", f"volume -> {target}")
        self.say(f"Okay, volume {target}." if target else "Muted.")
        return True

    def set_volume(self, v):
        body = json.dumps({"volume": int(v)}).encode()
        req = urllib.request.Request(DAEMON + "/api/volume/set", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            return json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception as e:
            return {"error": str(e)}

    # ---------- state for the panel ----------
    def state(self):
        return {
            "active": self.active,
            "listening": self._listening,
            "volume": self.get_volume().get("volume"),
            "brains": list(self.brains),
            "voices": self.voices,
            "behaviors": self.behaviors,
            "log": list(self.log)[-40:],
        }
