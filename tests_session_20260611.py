"""Bug sweep for the 2026-06-11 Reachy session: identity unification, tag
robustness, attentive beep, scan-room sweep, room memory. Pure-logic (no robot)."""
import re
import numpy as np

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else:    FAIL += 1; print(f"  FAIL {name}  {detail}")

print("== identity (reachy_system) ==")
import twin.brains as B
B.set_actions_hint(["simplenod", "wiggle"], ["wave"])
sys = B.reachy_system()
check("says You are Reachy", "You are Reachy" in sys)
check("no Marcus leak", "Marcus" not in sys)
check("no Claude leak", "Claude" not in sys)
check("no unfilled {name}/{other}", "{" not in sys and "}" not in sys)
check("actions hint baked in", "simplenod" in sys)
check("ClaudeBrain uses reachy_system", "You are Reachy" in
      B.SYSTEM_TMPL.format(actions=""))

print("== strip_mood (every tag pulled, real brackets kept) ==")
cases = [
    ("[happy] hi",            "happy", "hi"),
    ("[playful} I am Reachy", "playful", "I am Reachy"),
    ("(excited) yo",          "excited", "yo"),
    ("[curious) hm",          "curious", "hm"),
    ("[happy] a [excited] b", "happy", "a b"),          # first wins, both removed
    ("no tag here",           None, "no tag here"),
    ("text [note] stays",     None, "text [note] stays"),# unknown word kept
    ("  [GRATEFUL]  Thanks",  "grateful", "Thanks"),     # case-insensitive
]
for text, want_mood, want_clean in cases:
    m, c = B.strip_mood(text)
    check(f"strip_mood {text!r}", m == want_mood and c == want_clean,
          f"got mood={m} clean={c!r}")

print("== action extraction (bracket-tolerant) ==")
from twin.hub import ACTION_RX
def extract(text):
    acts = [(m.group(1).lower(), (m.group(2) or "").strip().lower() or None)
            for m in ACTION_RX.finditer(text or "")]
    clean = re.sub(r"\s{2,}", " ", ACTION_RX.sub("", text or "")).strip()
    return acts, clean
acases = [
    ("Watch! [dance:[simplenod]]", [("dance", "simplenod")], "Watch!"),
    ("[dance]",                    [("dance", None)], ""),
    ("[gesture:wave] hi",          [("gesture", "wave")], "hi"),
    ("look [look:left] now",       [("look", "left")], "look now"),
    ("[dance: simple_nod ]",       [("dance", "simple_nod")], ""),
    ("{look:right}",               [("look", "right")], ""),
    ("a [dance] b [gesture:hi] c", [("dance", None), ("gesture", "hi")], "a b c"),
    ("normal [brackets] text",     [], "normal [brackets] text"),
    ("[dancing] in the rain",      [], "[dancing] in the rain"),  # not a real kind
]
for text, wa, wc in acases:
    a, c = extract(text)
    check(f"extract {text!r}", a == wa and c == wc, f"got {a} clean={c!r}")

print("== bracketless action directives (12B drops the brackets) ==")
from twin.hub import RobotHub as _RH
bcases = [
    ("nice to meet you. dance:", [("dance", None)], "nice to meet you."),  # David's real leak
    ("sure dance:simplenod", [("dance", "simplenod")], "sure"),
    ("here look:left", [("look", "left")], "here"),
    ("I really love this dance", [], "I really love this dance"),         # prose: untouched
    ("Let us dance!", [], "Let us dance!"),                                # prose: untouched
]
for text, wa, wc in bcases:
    a, c = _RH._extract_actions(text)
    check(f"bare-action {text!r}", a == wa and c == wc, f"got {a} clean={c!r}")

print("== combined flow (mood then action, nothing spoken-as-tag) ==")
for text in ["[playful} Reachy! [dance:[simplenod]]",
             "[happy] sure [gesture:wave] watch",
             "[excited] [dance] woo"]:
    m, t = B.strip_mood(text)
    a, t = extract(t)
    leaked = bool(re.search(r"[\[\({].*?(dance|gesture|look|happy|excited|playful)", t, re.I))
    check(f"no tag leaks into speech {text!r}", not leaked, f"spoken={t!r}")

print("== attentive beep ==")
import twin.hub as H
hub = H.RobotHub()
bank = hub._beep_bank()
check("4 beeps", len(bank) == 4)
check("format (n,1) float32", all(b.ndim == 2 and b.shape[1] == 1 and b.dtype == np.float32 for b in bank))
check("durations 100-400ms", all(0.10 <= b.shape[0]/16000 <= 0.40 for b in bank))
check("soft peak <= 0.3", all(float(abs(b).max()) <= 0.30 for b in bank))
check("no clicks (starts/ends near 0)", all(abs(float(b[0,0])) < 0.05 and abs(float(b[-1,0])) < 0.05 for b in bank))
check("cached (same objects)", hub._beep_bank() is bank)
check("_play_beep safe when mini=None", hub._play_beep() is None)

print("== scan-room geometry ==")
pos = hub._scan_positions()
check("within body limit", all(abs(p) <= hub._JOG_LIMITS["body"] for p in pos))
check("arc reaches +/-SCAN_ARC_DEG", max(pos) == hub.SCAN_ARC_DEG and min(pos) == -hub.SCAN_ARC_DEG)
check("ping-pong (returns toward start)", pos[0] == -hub.SCAN_ARC_DEG and pos[len(pos)//2] == hub.SCAN_ARC_DEG)
check("no giant single jump", all(abs(pos[i+1]-pos[i]) <= hub.SCAN_ARC_DEG/hub.SCAN_STEPS + 0.5 for i in range(len(pos)-1)))
check("scan thread not running at boot", hub._scan_thread is None)

print("== room memory ==")
from twin.room_memory import RoomMemory
rm = RoomMemory()
rm.set_retention(8)
check("retention set", rm.state()["retention_hours"] == 8)
rm.add("vision", "a person at a laptop")
rm.add("vision", "a person at a laptop")   # dup
rm.add("presence", "someone came into view")
txt, shown, total = rm.timeline_text()
check("dup collapsed", total <= 2, f"total={total}")
check("timeline non-empty", bool(txt))
check("retention clamps high", (rm.set_retention(999) or rm.state()["retention_hours"]) <= 48)
check("retention clamps low", (rm.set_retention(0) or rm.state()["retention_hours"]) >= 1)

print(f"\n==== {PASS} passed, {FAIL} failed ====")
import sys; sys.exit(1 if FAIL else 0)
