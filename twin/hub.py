"""RobotHub: one ReachyMini connection shared by the voice loop and the web panel.

Thread-safety: all robot media access (speak + mic capture) is serialized by a single
lock. The mic capture loop grabs the lock per ~10ms chunk, so a panel-triggered say()
can slip in between chunks, hold the lock for its playback, and the capture loop simply
pauses until he's done talking. Brain calls (Claude CLI / Marcus HTTP) are not locked.

Speech is queued: chat()/say() return as soon as the reply text is known; a single
worker thread synthesizes + plays in order, so HTTP callers never wait out the audio.

The hub may start with no robot attached (panel retries in the background); every
method that touches self.mini degrades gracefully until the daemon connects.
"""
import glob
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque

import numpy as np
from reachy_mini import ReachyMini

from twin.stt import STT
from twin.tts import KokoroTTS
from twin.voice import (
    build_brains, detect_switch, strip_wake, calibrate_floor,
    _rms, _mono, SR, EXIT_WORDS, SPEECH_START, SILENCE_HANG, MIN_CHUNKS,
)
from twin.brains import strip_mood, MOOD_TO_EMOTION

DAEMON = "http://localhost:8000"
BODY_YAW_MAX = 2.7   # rad, mechanical-ish limit used everywhere we command the body


class RobotHub:
    def __init__(self):
        self.stt = STT()
        self.tts = KokoroTTS()
        self.brains, self.voices = build_brains()
        self.active = "claude"
        self.mini = None
        self.last_error = None     # last robot-connect failure (shown in /api/state)
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
        self.doa_sign = -1.0       # flip if he turns the wrong way (tuned live)
        # gaze controller (single owner of neck/body orientation):
        # turn-to-sound saccades the head fast; the follow layer then walks the body
        # under the gaze in ONE smooth move with the head counter-rotating.
        self._doa_hist = deque(maxlen=3)   # recent (t, absolute target yaw) candidates
        self._move_until = 0.0             # wall-clock end of in-flight goto; don't interrupt
        self._follow_since = None          # dwell timer before the body commits
        self._vol_cache = None     # cache slow /api/volume/current
        self._vol_ts = 0.0
        self._vol_refreshing = False
        self._last_face = 0.0
        self._face_seen_ts = 0.0   # last time a face was actually detected
        self._cached_mode = "?"    # /api/motors/status is slow (~2s) -> cache it
        self._mode_ts = 0.0
        self._servo_cache = {"body_yaw": None, "head_yaw": None, "head_pitch": None,
                             "antenna_left": None, "antenna_right": None}
        self._cascade = None       # opencv face detector (lazy)
        self._pose = {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "body": 0.0, "ant": 0.0}  # degrees
        # iPad-relayed camera frame (used when robot camera is unavailable, e.g. Windows host)
        self._ipad_jpeg = None
        self._ipad_jpeg_ts = 0.0
        # ordered speech queue -> single worker, so replies don't block HTTP responses
        self._speech_q = queue.Queue()
        self._speech_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self._speech_thread.start()
        # supervisor (auto-reconnect across robot replugs / daemon restarts)
        self._supervisor_thread = None
        self._supervisor_stop = threading.Event()
        self._last_kick = 0.0
        self._thinking = threading.Event()   # set while a brain works; drives antenna flutter

    def push_ipad_frame(self, jpeg_bytes):
        if jpeg_bytes:
            self._ipad_jpeg = jpeg_bytes
            self._ipad_jpeg_ts = time.time()

    def get_ipad_jpeg(self, max_age_sec=10.0):
        if not self._ipad_jpeg:
            return None
        if time.time() - self._ipad_jpeg_ts > max_age_sec:
            return None
        return self._ipad_jpeg

    # ---------- lifecycle ----------
    def start(self):
        # On Windows the LOCAL/GStreamer-IPC camera path is broken, so let the env
        # force webrtc (cross-platform). Default behavior unchanged for Linux/macOS.
        backend = os.environ.get("REACHY_MEDIA_BACKEND", "default")
        mini = ReachyMini(media_backend=backend)
        mini.__enter__()
        try:
            mini.media.start_recording()
            mini.media.start_playing()
            self._thresh = calibrate_floor(mini)
        except Exception:
            mini.__exit__(None, None, None)
            raise
        self.mini = mini           # publish only once fully usable
        self.last_error = None
        try:
            self._load_moves()
        except Exception as e:
            self._log("system", f"moves library unavailable: {e}")
        self._log("system", f"online - brains: {', '.join(self.brains)} | gate {self._thresh:.4f}")
        # always-on by default: ears find him a face, eyes hold it, moods get acted out
        self.set_behavior("face_track", True)
        self.set_behavior("turn_to_sound", True)
        self.set_behavior("emotions_on_cue", True)

    def shutdown(self):
        self._supervisor_stop.set()
        self._behavior_stop.set()
        self.set_listening(False)
        self._teardown_mini()

    @property
    def robot_connected(self):
        return self.mini is not None

    def _teardown_mini(self):
        mini, self.mini = self.mini, None
        if mini is None:
            return
        try:
            mini.media.stop_recording()
            mini.media.stop_playing()
            mini.__exit__(None, None, None)
        except Exception:
            pass

    # ---------- supervision (auto-reconnect) ----------
    # Owns the robot link end to end: initial connect, reconnect after the robot
    # is replugged, and bouncing a "robot-less" daemon (one that started while
    # the robot was unplugged -- it serves HTTP but has no motors, and SDK
    # clients can't attach to it).
    SUPERVISE_PERIOD = 5.0
    KICK_COOLDOWN = 60.0

    def start_supervisor(self):
        if self._supervisor_thread is None or not self._supervisor_thread.is_alive():
            self._supervisor_stop.clear()
            self._supervisor_thread = threading.Thread(target=self._supervise, daemon=True)
            self._supervisor_thread.start()

    def stop_supervisor(self):
        self._supervisor_stop.set()

    def _daemon_alive(self):
        try:
            urllib.request.urlopen(DAEMON + "/api/daemon/status", timeout=2)
            return True
        except Exception:
            return False

    def _serial_present(self):
        if sys.platform == "darwin":
            return bool(glob.glob("/dev/cu.usbmodem*"))
        return True   # elsewhere, don't gate on it

    def _kick_daemon(self, force=False):
        """Restart the daemon's launchd service (macOS host only)."""
        now = time.time()
        if not force and now - self._last_kick < self.KICK_COOLDOWN:
            return False
        if sys.platform != "darwin":
            return False
        self._last_kick = now
        self._log("system", "daemon has no robot -- restarting it")
        try:
            subprocess.run(["launchctl", "kickstart", "-k",
                            f"gui/{os.getuid()}/com.legionstage.reachy-daemon"],
                           timeout=10, capture_output=True)
            return True
        except Exception as e:
            self._log("system", f"daemon restart failed: {e}")
            return False

    def _supervise(self):
        connect_fails = 0
        link_fails = 0
        cam_fails = 0
        cam_ok_once = False   # only treat a dead camera as fatal if it worked this session
        while not self._supervisor_stop.is_set():
            self._supervisor_stop.wait(self.SUPERVISE_PERIOD)
            if self._supervisor_stop.is_set():
                break
            if self.mini is None:
                try:
                    self.start()
                    connect_fails = 0
                except Exception as e:
                    self.last_error = str(e)
                    connect_fails += 1
                    # daemon serving + robot USB present, yet clients can't attach
                    # -> the daemon started while the robot was away; bounce it
                    if connect_fails >= 2 and self._serial_present() and self._daemon_alive():
                        self._kick_daemon()
                continue
            # link liveness: cheap joint read; skip the check while he's speaking
            if not self._lock.acquire(timeout=1.5):
                continue
            try:
                self.mini.get_current_joint_positions()
                link_fails = 0
            except Exception:
                link_fails += 1
            finally:
                self._lock.release()
            if link_fails >= 2:                 # two strikes -> rebuild the link
                self._log("system", "robot link stale -- reconnecting")
                link_fails = 0
                self._teardown_mini()
                continue
            # camera liveness: motors can survive a replug while the daemon's
            # video pipeline silently dies bound to the old USB device
            jpg = self.get_jpeg(quality=40)
            if jpg:
                cam_ok_once = True
                cam_fails = 0
            elif cam_ok_once:
                cam_fails += 1
                if cam_fails >= 4:              # ~20s of no frames after having worked
                    cam_fails = 0
                    self._log("system", "camera stream died -- restarting daemon")
                    if self._kick_daemon():
                        self._teardown_mini()   # rebuild our client against the new daemon

    def reconnect(self):
        """Client-requested: force-reestablish the robot link right now."""
        self._teardown_mini()
        try:
            self.start()
            return {"ok": True, "robot": "connected"}
        except Exception as e:
            self.last_error = str(e)
            if self._serial_present() and self._daemon_alive() and self._kick_daemon(force=True):
                note = "daemon restarting -- auto-reconnect will finish in ~30s"
            else:
                note = str(e)
            return {"ok": False, "robot": "disconnected", "note": note}

    # ---------- logging ----------
    def _log(self, who, text):
        self.log.append({"who": who, "text": text, "t": time.time()})

    # ---------- speech (queued) ----------
    def say(self, text, voice=None):
        self._enqueue_say(text, None, voice)

    def _enqueue_say(self, text, move, voice=None):
        if not text:
            return
        if self.mini is None:
            self._log("system", "(robot offline -- reply not spoken)")
            return
        self._speech_q.put((text, move, voice or self.voices.get(self.active, "af_heart")))

    def _speech_worker(self):
        while True:
            text, move, voice = self._speech_q.get()
            try:
                self._speak_now(text, move, voice)
            except Exception as e:
                self._log("system", f"speech error: {e}")

    @staticmethod
    def _sentences(text, min_len=24):
        """Split a reply into speakable chunks; merge fragments so chunks stay natural."""
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        out = []
        for p in parts:
            if not p:
                continue
            if out and (len(out[-1]) < min_len or len(p) < 12):
                out[-1] += " " + p
            else:
                out.append(p)
        return out or [text]

    def _speak_now(self, text, move, voice):
        """Speak while an (optional) emotion move plays concurrently -- like real body language.

        Synthesis is sentence-chunked: he starts talking after the FIRST sentence is
        ready instead of waiting for the whole reply, and later sentences synthesize
        while earlier ones play. push_audio_sample is non-blocking and the daemon
        composes its audio-reactive wobble on top of the move's pose, so motion +
        speech layer instead of fighting.
        """
        chunks = self._sentences(text)
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
            total_dur = 0.0
            t0 = time.time()
            for ch in chunks:
                samples = self.tts.synth(ch, voice=voice)
                self.mini.media.push_audio_sample(samples)       # queues; playback is continuous
                total_dur += len(samples) / SR
            while time.time() - t0 < total_dur + 0.3:            # drain mic so we don't hear ourselves
                s = self.mini.media.get_audio_sample()
                if s is None or len(s) == 0:
                    time.sleep(0.005)
            if mt is not None:
                mt.join(timeout=4)

    # ---------- chat ----------
    THINK_FILLERS = ["Hmm.", "Hm, let me think.", "Mmm.", "Oh!", "Good question."]

    def _think_cue(self, spoken):
        """Instant acknowledgment while the brain works: antennas perk up and
        FLUTTER until the reply is ready (visible 'I'm on it'), plus a short
        verbal filler when the input came in by voice."""
        if self.mini is None:
            return
        if spoken:
            self._enqueue_say(random.choice(self.THINK_FILLERS), None)
        if not self._thinking.is_set():
            self._thinking.set()
            threading.Thread(target=self._antenna_flutter, daemon=True).start()

    def _antenna_flutter(self):
        """Oscillate the antennas while `_thinking` is set, then settle back."""
        try:
            base = self._pose["ant"] + self.ANT_REST_DEG
            t0 = time.time()
            up = True
            while self._thinking.is_set() and time.time() - t0 < 30:   # safety cap
                a = base + (22 if up else 12)
                up = not up
                if self._lock.acquire(timeout=0.5):     # skip beats while he speaks
                    try:
                        self.mini.goto_target(antennas=np.deg2rad([a, a]), duration=0.15)
                    finally:
                        self._lock.release()
                time.sleep(0.18)
            for _ in range(10):                          # settle back -- retry through lock contention
                if self._lock.acquire(timeout=1.0):
                    try:
                        self.mini.goto_target(antennas=np.deg2rad([base, base]), duration=0.4)
                        break
                    finally:
                        self._lock.release()
                time.sleep(0.3)
        except Exception:
            pass

    def chat(self, text, brain=None, spoken=False):
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
        self._think_cue(spoken)                  # he reacts NOW; the answer follows
        try:
            if not msg.strip():
                reply = f"{self.active.capitalize()} here. What's up?"
            else:
                reply = self.brains[self.active].reply(msg)
        finally:
            self._thinking.clear()               # stop the flutter; time to talk
        mood, reply = strip_mood(reply)          # pull the brain's [mood] tag off the speech
        self._log(self.active, reply)
        move = None
        if self.behaviors.get("emotions_on_cue"):
            emo = MOOD_TO_EMOTION.get(mood) if mood else self._emotion_for(reply)
            if emo:
                move = self._get_emotion_move(emo)
        self._enqueue_say(reply, move)           # speech is async; reply returns immediately
        return {"brain": self.active, "reply": reply, "mood": mood}

    def set_brain(self, brain):
        if brain in self.brains:
            self.active = brain
        return self.active

    # ---------- listening ----------
    def set_listening(self, on):
        on = bool(on)
        if on and not self._listening:
            self._stop.set()                     # stop + join any straggler loop first
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=2)
            if self.mini is None:
                self._log("system", "(robot offline -- can't listen)")
                return False
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
            self.chat(text, spoken=True)

    def _capture(self, max_seconds=15.0):
        buf, in_speech, run, silence = [], False, 0, 0
        start = time.time()
        while not self._stop.is_set() and time.time() - start < max_seconds:
            with self._lock:
                s = self.mini.media.get_audio_sample()
            if s is None or len(s) == 0:
                time.sleep(0.005)                # don't spin a core on an idle pipeline
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
        if self.mini is None:
            return {"error": "robot not connected"}
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

    FACE_HOLDS_GAZE_S = 2.5   # eyes own the head this long after seeing a face

    def _behavior_loop(self):
        while not self._behavior_stop.is_set() and (
                self.behaviors["turn_to_sound"] or self.behaviors["face_track"]):
            if self.behaviors["face_track"]:
                self._face_track_tick()
            # ears find, eyes hold: sound only steers when no face is in view,
            # so a noise can't yank his gaze off the person he's looking at.
            if self.behaviors["turn_to_sound"] and (
                    not self.behaviors["face_track"]
                    or time.time() - self._face_seen_ts > self.FACE_HOLDS_GAZE_S):
                self._gaze_tick()
            self._follow_tick()         # always-on postural layer: body follows a held head turn
            time.sleep(0.06)

    # ---------- gaze controller ----------
    # Head saccades fast toward a sound (like a startle/glance); the body then squares
    # up underneath in one slow move while the head counter-rotates to hold the gaze,
    # settling with a small head lead so he never looks bolt-straight (reads as alive).
    HEAD_LOOK_MAX = 0.6     # rad (~34 deg) the head will glance on its own
    SACCADE_S = 0.25        # fast head move
    DOA_STABLE_RAD = 0.17   # commit only when recent DoA estimates agree within ~10 deg
    FOLLOW_TRIGGER_DEG = 14 # head held past this -> body starts to follow
    LEAD_DEG = 5            # residual head lead left after settling
    FOLLOW_DWELL = 1.0      # seconds the head must stay off-center before the body commits
    FOLLOW_S = 1.2          # slow body move

    def _read_yaws(self):
        """Measured (body_yaw, head_yaw) in rad from the servos -- no dead reckoning."""
        b, y, _ = self._read_pose()
        return b, y

    def _read_pose(self):
        """Measured (body_yaw, head_yaw, head_pitch) in rad from the servos."""
        try:
            with self._lock:
                head, _ = self.mini.get_current_joint_positions()
                m = np.asarray(self.mini.get_current_head_pose(), dtype=float)
            yaw = float(np.arctan2(m[1, 0], m[0, 0]))
            pitch = float(-np.arcsin(max(-1.0, min(1.0, m[2, 0]))))
            return float(head[0]), yaw, pitch
        except Exception:
            return None, None, None

    def _gaze_tick(self):
        now = time.time()
        if now < self._move_until:           # let the in-flight move finish
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
        delta = self.doa_sign * (angle - (np.pi / 2))   # offset from current facing (rad)
        if abs(delta) < 0.2:                 # near-front jitter -> already looking there
            self._doa_hist.clear()
            return
        body, head = self._read_yaws()
        if body is None:
            return
        # DoA is relative to where he's facing NOW -> make it an absolute target,
        # so consecutive estimates can be compared/filtered instead of re-chased.
        self._doa_hist.append((now, body + head + delta))
        fresh = [t for ts, t in self._doa_hist if now - ts < 2.0]
        if len(fresh) < 2 or max(fresh) - min(fresh) > self.DOA_STABLE_RAD:
            return                           # wait for two agreeing estimates (kills jitter)
        target = float(np.mean(fresh))
        self._doa_hist.clear()
        from reachy_mini.utils import create_head_pose
        head_target = max(-self.HEAD_LOOK_MAX, min(self.HEAD_LOOK_MAX, target - body))
        pose = create_head_pose(yaw=float(np.rad2deg(head_target)), degrees=True)
        with self._lock:
            self.mini.goto_target(head=pose, duration=self.SACCADE_S)
        self._move_until = now + self.SACCADE_S + 0.05
        self._follow_since = now             # follow dwell starts at the glance

    def _follow_tick(self):
        now = time.time()
        if now < self._move_until:
            return
        body, head = self._read_yaws()
        if body is None:
            return
        head_deg = float(np.rad2deg(head))
        if abs(head_deg) <= self.FOLLOW_TRIGGER_DEG:     # comfortable -> nothing to do
            self._follow_since = None
            return
        if self._follow_since is None:                   # start the dwell timer
            self._follow_since = now
            return
        if now - self._follow_since < self.FOLLOW_DWELL:
            return
        self._follow_since = None
        # one smooth move: body squares up under the gaze while the head counter-rotates
        # in the SAME goto, so the gaze direction holds throughout.
        sign = 1.0 if head_deg > 0 else -1.0
        target_total = body + head
        new_head = sign * float(np.deg2rad(self.LEAD_DEG))
        new_body = max(-BODY_YAW_MAX, min(BODY_YAW_MAX, target_total - new_head))
        if abs(new_body - body) < np.deg2rad(2):         # body already there (or at limit)
            return
        new_head = target_total - new_body               # exact gaze hold after body clamp
        from reachy_mini.utils import create_head_pose
        with self._lock:
            if self.behaviors.get("face_track"):
                # the camera re-centers the head on its own; just bring the body around
                self.mini.goto_target(body_yaw=new_body, duration=self.FOLLOW_S)
            else:
                pose = create_head_pose(yaw=float(np.rad2deg(new_head)), degrees=True)
                self.mini.goto_target(head=pose, body_yaw=new_body, duration=self.FOLLOW_S)
        self._move_until = now + self.FOLLOW_S + 0.1

    def _detect_faces(self, frame):
        import os
        import cv2
        if self._cascade is None:
            self._cascade = cv2.CascadeClassifier(
                os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._cascade.detectMultiScale(gray, 1.2, 5, minSize=(60, 60))

    # Bounded face tracking: we aim the head ourselves (instead of the SDK's
    # unbounded look_at_image) so tracking can never command a pose where the
    # head rim contacts the shell -- the bump zone is deep pitch at yaw.
    FT_YAW_MAX_DEG = 38.0      # head-only; the follow layer brings the body for more
    FT_PITCH_UP_DEG = -18.0    # looking up (negative = up); shell clearance limit
    FT_PITCH_DOWN_DEG = 25.0   # looking down
    FT_GAIN_YAW = 16.0         # deg of correction for a face at the frame edge
    FT_GAIN_PITCH = 11.0

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
        self._face_seen_ts = now                             # eyes have a target
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])   # largest face
        cx, cy = x + w // 2, y + h // 2
        H, W = frame.shape[:2]
        if abs(cx - W / 2) < W * 0.08 and abs(cy - H / 2) < H * 0.08:
            return True                              # already centered -> hold
        _, head_yaw, head_pitch = self._read_pose()
        if head_yaw is None:
            return False
        dx = (cx - W / 2) / (W / 2)                  # -1..1, + = face right of center
        dy = (cy - H / 2) / (H / 2)                  # + = face below center
        new_yaw = max(-self.FT_YAW_MAX_DEG, min(self.FT_YAW_MAX_DEG,
                      float(np.rad2deg(head_yaw)) - dx * self.FT_GAIN_YAW))
        new_pitch = max(self.FT_PITCH_UP_DEG, min(self.FT_PITCH_DOWN_DEG,
                        float(np.rad2deg(head_pitch)) + dy * self.FT_GAIN_PITCH))
        from reachy_mini.utils import create_head_pose
        pose = create_head_pose(yaw=new_yaw, pitch=new_pitch, degrees=True)
        with self._lock:
            try:
                self.mini.goto_target(head=pose, duration=0.3)
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
    def get_jpeg(self, quality=70, timeout=0.5):
        # bound get_frame() so a stalled/empty camera pipeline can't hang the request
        result = [None]

        def _grab():
            try:
                result[0] = self.mini.media.get_frame()
            except Exception:
                pass

        t = threading.Thread(target=_grab, daemon=True)
        t.start()
        t.join(timeout)
        frame = result[0]
        if frame is None:
            return None
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    _JOG_LIMITS = {"pitch": 30, "roll": 30, "yaw": 60, "body": 90, "ant": 90}  # degrees
    ANT_REST_DEG = 8   # antennas held at exactly 0 deg hunt (backlash); a small offset stops it

    def _apply_pose(self):
        if self.mini is None:
            return dict(self._pose)
        from reachy_mini.utils import create_head_pose
        p = self._pose
        head = create_head_pose(roll=p["roll"], pitch=p["pitch"], yaw=p["yaw"], degrees=True)
        ant = np.deg2rad([p["ant"] + self.ANT_REST_DEG, p["ant"] + self.ANT_REST_DEG])
        with self._lock:
            self.mini.goto_target(head=head, body_yaw=np.deg2rad(p["body"]),
                                  antennas=ant, duration=0.35)
        return dict(self._pose)

    def servos(self):
        """Live motor telemetry for the panel. Non-blocking: if the robot lock is busy
        (e.g. he's mid-speech), return the last-known values instead of stalling the
        request until the utterance ends."""
        out = {"mode": self._cached_mode, **self._servo_cache}
        if self._lock.acquire(timeout=0.2):
            try:
                head, ant = self.mini.get_current_joint_positions()
                m = np.asarray(self.mini.get_current_head_pose(), dtype=float)
                self._servo_cache = {
                    "body_yaw": round(float(np.rad2deg(head[0])), 1),   # head[0] = body yaw
                    "head_yaw": round(float(np.rad2deg(np.arctan2(m[1, 0], m[0, 0]))), 1),
                    "head_pitch": round(float(np.rad2deg(-np.arcsin(max(-1.0, min(1.0, m[2, 0]))))), 1),
                    "antenna_left": round(float(np.rad2deg(ant[0])), 1),
                    "antenna_right": round(float(np.rad2deg(ant[1])), 1),
                }
                out.update(self._servo_cache)
            except Exception:
                pass
            finally:
                self._lock.release()
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

    def look(self, yaw_deg, pitch_deg):
        """Aim the head at an absolute yaw/pitch -- used by the iPad app's Vision
        face-tracking. Routed through _pose so look, jog, and center share one state."""
        self._pose["yaw"] = max(-self._JOG_LIMITS["yaw"], min(self._JOG_LIMITS["yaw"], float(yaw_deg)))
        self._pose["pitch"] = max(-self._JOG_LIMITS["pitch"], min(self._JOG_LIMITS["pitch"], float(pitch_deg)))
        return self._apply_pose()

    def jog(self, part, delta):
        if part in self._pose:
            if part == "body":
                # the gaze controller moves the body behind _pose's back; re-sync from
                # the servos so a jog nudges from where he ACTUALLY is, not a stale target
                b, _ = self._read_yaws()
                if b is not None:
                    self._pose["body"] = float(np.rad2deg(b))
            lim = self._JOG_LIMITS[part]
            self._pose[part] = max(-lim, min(lim, self._pose[part] + float(delta)))
        return self._apply_pose()

    def center(self):
        for k in self._pose:
            self._pose[k] = 0.0
        self._doa_hist.clear()
        self._follow_since = None
        return self._apply_pose()

    # ---------- volume (proxy daemon) ----------
    def get_volume(self):
        try:
            return json.loads(urllib.request.urlopen(DAEMON + "/api/volume/current", timeout=5).read())
        except Exception as e:
            return {"error": str(e)}

    # only treat these as commands when they're unambiguous -- "what does mute mean?"
    # must reach the brain, not silence the robot.
    _MUTE_RX = re.compile(
        r"(?:hey\s+\w+[,\s]+)?(?:please\s+)?(?:mute|silence)"
        r"(?:\s+(?:yourself|it|the\s+(?:volume|sound|audio)))?\s*[.!]*", re.I)
    _VOL_NUM_RX = re.compile(r"\b(?:set|turn|put)?\s*(?:the\s+)?volume\s+(?:up\s+|down\s+)?(?:to|at)?\s*(\d{1,3})\b", re.I)
    _VOL_DOWN_RX = re.compile(r"\b(quieter|too loud|turn (?:it|the volume) down|turn down|volume down|softer|less loud|lower (?:the )?(?:volume|it))\b", re.I)
    _VOL_UP_RX = re.compile(r"\b(louder|speak up|turn (?:it|the volume) up|turn up|can'?t hear|volume up|more volume)\b", re.I)

    def _maybe_volume_command(self, text):
        """Intercept spoken/typed volume commands. Returns True if handled (no brain call)."""
        t = text.lower().strip()
        v = self.get_volume().get("volume")
        cur = int(v) if isinstance(v, (int, float)) else 60
        target = None
        if self._MUTE_RX.fullmatch(t):
            target = 0
        elif "volume" in t and re.search(r"\b(max|full|loudest|all the way)\b", t):
            target = 100
        elif (m := self._VOL_NUM_RX.search(t)):
            target = int(m.group(1))
        elif self._VOL_DOWN_RX.search(t):
            target = cur - 15
        elif self._VOL_UP_RX.search(t):
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
        self._vol_cache = int(v)            # reflect immediately in the panel
        self._vol_ts = time.time()
        body = json.dumps({"volume": int(v)}).encode()
        req = urllib.request.Request(DAEMON + "/api/volume/set", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            return json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception as e:
            return {"error": str(e)}

    def _volume_cached(self):
        """Never blocks: refreshes in the background so /api/state stays fast even
        when the daemon is slow or down."""
        if time.time() - self._vol_ts > 3 and not self._vol_refreshing:
            self._vol_refreshing = True
            threading.Thread(target=self._refresh_volume, daemon=True).start()
        return self._vol_cache

    def _refresh_volume(self):
        try:
            v = self.get_volume().get("volume")
            if v is not None:
                self._vol_cache = int(v)
        finally:
            self._vol_ts = time.time()
            self._vol_refreshing = False

    # ---------- state for the panel ----------
    def state(self):
        return {
            "active": self.active,
            "listening": self._listening,
            "volume": self._volume_cached(),
            "brains": list(self.brains),
            "voices": self.voices,
            "behaviors": self.behaviors,
            "robot": "connected" if self.robot_connected else "disconnected",
            "robot_error": None if self.robot_connected else self.last_error,
            "log": list(self.log)[-40:],
        }
