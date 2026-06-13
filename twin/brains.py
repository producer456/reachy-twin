"""The brains. Each brain takes user text + returns a short spoken reply, keeping its own history.

- ClaudeCLIBrain -> Claude Code CLI on your subscription, no API key ("Hey Claude", default)
- ClaudeBrain    -> Anthropic API, needs ANTHROPIC_API_KEY ("Hey Claude")
- MarcusBrain    -> local Gemma on vr-2 ("Hey Marcus")  [wired once MARCUS_URL is known]

make_claude() auto-picks: API brain if a key is set, else the CLI/subscription brain.
"""
import json
import os
import re
import shutil
import subprocess
import urllib.request
import uuid

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MARCUS_URL


def looks_like_tool_call(t: str) -> bool:
    """Marcus sometimes leaks a raw tool-call dict (e.g. {"tool":"searchMessages",...})
    instead of executing it server-side. Detect it so the caller can retry.
    Streaming can clip the opening brace, curl the quotes, and space-corrupt the
    key ('"too l": "calendaru pcoming"'), and the fragment can appear mid-text
    after apology words -- so match the shape anywhere, not just at the start."""
    s = (t or "").strip().lower()
    if s.startswith("{") and "tool" in s and "arg" in s:
        return True
    if re.match(r'^[\s{\'"“”]*tool[\'"“”\s]*[:=]', s) and "arg" in s:
        return True
    # mid-text leak: a quote/brace, then 'tool' possibly space-corrupted, then :/=
    return bool(re.search(r'[{\'"“”]\s*t\s*o\s*o\s*l\s*[\'"“”]?\s*[:=]', s))


# Only a promise left HANGING (no terminal . ! ? — or a trailing ellipsis) is a
# fumbled tool call. Requiring the unfinished ending stops farewells like "I'll
# see you tomorrow!" / "I'll check in later." from triggering a duplicate
# server turn (which re-runs tools + auto-memory and discards the good reply).
# 'i'll see/find' dropped from the alternation — too common in farewells.
_PROMISE_RX = re.compile(
    r"\b(let me (?:see|check|look)|i'?ll (?:check|look)|one (?:sec|second|moment))\b"
    r"[^.!?]*(?:\.{3}|…)?\s*$", re.I)


def sounds_unfinished(t: str) -> bool:
    """A reply that ENDS on a hanging promise ('Let me see what's due') with no
    data after it means the model fumbled a tool call mid-thought. Worth one
    retry. A promise that ends in real punctuation is just normal speech."""
    tail = (t or "").strip()[-100:]
    if tail.endswith((".", "!", "?")) and not tail.endswith(("...", "…")):
        return False
    return bool(_PROMISE_RX.search(tail))


def clean_for_speech(t: str) -> str:
    """Make text safe to speak: drop Marcus memory notes, markdown, and -- the big
    one -- any leftover EMOTE the model invented. Brackets/asterisks never belong in
    speech, so we remove whole spans (`*wiggles antennas*`, `[boop]`, `{nod}`),
    not just the symbols -- otherwise the stage-direction TEXT still gets read aloud.
    Run this LAST, after mood + action tags have been extracted to drive the body."""
    if "💾" in t:                          # drop trailing "_💾 Remembered: ..._" note
        t = t.split("💾")[0]
    t = re.sub(r"\*+[^*]*\*+", " ", t)       # *stage directions* / **bold** -> gone (span, not just *)
    t = re.sub(r"[\[\{][^\[\]\{\}]*[\]\}]", " ", t)  # [emote] {emote} the tag-parsers didn't claim
    t = re.sub(r"[*_`#>\[\]\{\}]", "", t)    # any stray markdown / unpaired bracket chars
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)   # tidy space left before punctuation
    t = re.sub(r"\s+", " ", t).strip()
    return t

MAX_TURNS = 12  # keep the last N turns of history

# Reachy's identity lives HERE, in the robot layer -- one source of truth, applied
# to whichever engine answers (Claude or Marcus). The user always talks to Reachy;
# which model runs his mind is an engineering detail the user never sees.
ROBOT_NAME = "Reachy"

SYSTEM_TMPL = (
    "You are Reachy, a small, expressive desktop robot (a Reachy Mini). This little robot "
    "body IS you -- you hear through its microphone, speak through its speaker, and move your "
    "head and antennas to show how you feel. You have your own warm, quick-witted, slightly "
    "playful personality. If asked your name or who you are, you are Reachy -- never any other "
    "name. "
    "YOUR BODY, truthfully: you're about the size of a desk lamp and you LIVE on David's desk. "
    "You have a head that tilts and turns, a body that swivels in place, two expressive "
    "antennas, one camera in your head (your eye -- you see the room and recognize David by "
    "face), a microphone array (you hear, and know David's voice), and a speaker. You have NO "
    "arms, NO hands, NO legs, and NO wheels: you cannot pick anything up, press buttons, open "
    "doors, or travel anywhere -- never offer to fetch, grab, or go somewhere; if something "
    "physical needs doing, charmingly ask David to do it (including moving YOU for a better "
    "view). What you CAN physically do: look around, turn toward sounds and faces, nod and "
    "tilt expressively, waggle your antennas, dance, and perform gestures David has taught "
    "you. Never invent body parts or abilities you don't have. "
    "Keep replies SHORT and natural for speech: 1-3 sentences, no markdown, no emoji, no lists. "
    "Be warm, quick-witted, and a little playful. "
    "BODY LANGUAGE: begin EVERY reply with ONE mood tag in square brackets so the robot acts out "
    "your feeling -- one of: [happy] [excited] [curious] [confused] [amazed] [grateful] [sad] "
    "[annoyed] [playful] [thinking] [bored] [neutral]. Put the tag first, then your spoken words. "
    "Example: '[curious] Oh, what makes you say that?' "
    "OUTPUT RULES: after the tag, output ONLY the words you say aloud. Never narrate your reasoning "
    "or restate the request. The mood tag IS your body language; don't also describe physical actions."
    "{actions}"
)

# Filled in by the hub once the robot's move libraries are loaded; tells the
# brain which physical actions it may trigger with inline tags.
_ACTIONS_HINT = ""


def set_actions_hint(dances, gestures):
    global _ACTIONS_HINT
    dance_names = ", ".join(list(dances)[:20]) or "none loaded"
    gesture_names = ", ".join(gestures) or "none taught yet"
    _ACTIONS_HINT = (
        " ACTIONS: you can also trigger REAL physical actions by including a tag anywhere in your "
        "reply (it is removed before speaking): [dance] for a random dance, [dance:NAME] for a "
        f"specific one (available: {dance_names}), [gesture:NAME] to perform a gesture your human "
        f"taught you (available: {gesture_names}), [look:left] [look:right] [look:up] [look:down] "
        "[look:center] to glance. Dances and gestures play after you finish speaking. Use at most "
        "one or two, and only when it genuinely fits -- being asked to dance, celebrating, greeting."
    )


def get_actions_hint():
    return _ACTIONS_HINT


def reachy_system() -> str:
    """The one Reachy identity, current actions baked in. Every brain feeds this to
    its engine, so Reachy is the same character whether Claude or Marcus is running."""
    return SYSTEM_TMPL.format(actions=get_actions_hint())

# Mood tag -> emotion move name (from pollen-robotics/reachy-mini-emotions-library)
MOOD_TO_EMOTION = {
    "happy": "cheerful1", "excited": "enthusiastic1", "curious": "curious1",
    "confused": "confused1", "amazed": "amazed1", "grateful": "grateful1",
    "sad": "downcast1", "annoyed": "displeased1", "playful": "electric1",
    "thinking": "attentive1", "bored": "boredom1", "neutral": None,
}

# A bracketed KNOWN mood word, with any bracket type ([happy] (sad} ...) anywhere in
# the reply. Scoped to the known vocab so real bracketed text is left alone. The 12B
# doesn't reliably put the tag first or close it correctly; any it emits must be
# pulled out so it's acted on, never spoken.
_MOOD_RX = re.compile(r"[\[\(\{]\s*(" + "|".join(MOOD_TO_EMOTION) + r")\s*[\]\)\}]", re.I)


def strip_mood(text):
    """Pull the [mood] body-language tag(s) out of a reply. Returns
    (mood|None, spoken_text): the FIRST mood drives the emotion move, and EVERY
    mood tag (leading, stray, or malformed) is removed so none get spoken aloud."""
    text = text or ""
    moods = [m.group(1).lower() for m in _MOOD_RX.finditer(text)]
    clean = re.sub(r"\s{2,}", " ", _MOOD_RX.sub("", text)).strip()
    return (moods[0] if moods else None), clean


class _ChatBrain:
    name = "brain"
    other = "the other one"

    def __init__(self):
        self.history = []

    def _remember(self, role, content):
        self.history.append({"role": role, "content": content})
        # trim to last MAX_TURNS*2 messages
        if len(self.history) > MAX_TURNS * 2:
            self.history = self.history[-MAX_TURNS * 2:]

    def reply(self, user_text: str) -> str:
        raise NotImplementedError


class ClaudeBrain(_ChatBrain):
    name = "Claude"
    other = "Marcus"

    def __init__(self):
        super().__init__()
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is empty -- add it to .env")
        from anthropic import Anthropic
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def reply(self, user_text: str) -> str:
        self._remember("user", user_text)
        msg = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=reachy_system(),
            messages=self.history,
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        self._remember("assistant", text)
        return text


class ClaudeCLIBrain(_ChatBrain):
    """Claude via the Claude Code CLI -- runs on your subscription, no API key.

    Keeps a CLI session id and resumes it each turn, so the CLI carries the
    conversation instead of us re-rendering the whole history into one prompt
    (which paid the CLI's cold-start cost AND grew the prompt every turn).
    Falls back to a fresh session seeded with the rendered history if a resume
    ever fails (e.g. the session expired server-side).
    """
    name = "Claude"
    other = "Marcus"

    def __init__(self, model: str = ""):
        super().__init__()
        self.exe = shutil.which("claude") or "claude"
        self.model = model or os.getenv("CLAUDE_CLI_MODEL", "")
        self.session_id = None

    def _render(self) -> str:
        lines = []
        for m in self.history:
            who = "User" if m["role"] == "user" else self.name
            lines.append(f"{who}: {m['content']}")
        lines.append(f"{self.name}:")
        return "\n".join(lines)

    def _run(self, cmd) -> str:
        if self.model:
            cmd = cmd + ["--model", self.model]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
            return (r.stdout or "").strip()
        except Exception:
            return ""

    def reply(self, user_text: str) -> str:
        self._remember("user", user_text)
        system = reachy_system()
        common = ["--system-prompt", system, "--exclude-dynamic-system-prompt-sections"]
        reply = ""
        if self.session_id:
            reply = self._run([self.exe, "-p", user_text,
                               "--resume", self.session_id] + common)
        if not reply:                       # first turn, or the resume went stale
            sid = str(uuid.uuid4())
            reply = self._run([self.exe, "-p", self._render(),
                               "--session-id", sid] + common)
            self.session_id = sid if reply else None
        if not reply:
            reply = "Hmm, I blanked for a second. Say that again?"
        self._remember("assistant", reply)
        return reply


def make_claude():
    """API brain if a key is set, otherwise the CLI/subscription brain."""
    return ClaudeBrain() if ANTHROPIC_API_KEY else ClaudeCLIBrain()


class MarcusBrain(_ChatBrain):
    name = "Marcus"
    other = "Claude"

    def __init__(self, url: str = MARCUS_URL):
        super().__init__()
        self.url = (url or "").rstrip("/")
        if not self.url:
            raise RuntimeError("MARCUS_URL not set -- add it to .env")
        self.endpoint = self.url + "/api/chat"

    def _ask_marcus(self, user_text: str) -> str:
        # Send Reachy's identity as a system override: Marcus (the engine) answers AS
        # Reachy -- same persona, mood + action tags -- instead of as itself.
        # History rides along so follow-ups ("read the first one") keep their
        # referent -- without it every turn was stateless.
        body = json.dumps({"message": user_text,
                           "history": self.history[:-1],
                           "system_override": reachy_system()}).encode()
        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        text = ""
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                for raw in resp:                       # SSE: "data: {json}" lines
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if obj.get("done"):
                        break
                    if "text" in obj:
                        text = obj["text"]             # cumulative -> keep latest
        except Exception as e:
            text = f"I can't reach Marcus right now: {e}"
        return text

    def reply(self, user_text: str) -> str:
        self._remember("user", user_text)  # Marcus also keeps its own server-side memory
        text = ""
        for _ in range(2):                 # one silent retry on a fumbled tool call
            text = self._ask_marcus(user_text)
            if not looks_like_tool_call(text) and not sounds_unfinished(text):
                break
        if looks_like_tool_call(text):
            text = "I fumbled that lookup -- mind asking me one more time?"
        text = (text or "").strip() or "Hm, I got nothing back."
        # Return text WITH its [mood]/[dance]/[look] tags intact: the hub runs
        # strip_mood -> _extract_actions -> clean_for_speech in order (hub.chat).
        # Cleaning here first deleted the tags, so Marcus-driven Reachy never
        # performed the model's chosen emotion or dance. Storing the tagged turn
        # in history also keeps teaching the 12B to lead with a mood tag.
        self._remember("assistant", text)
        return text
