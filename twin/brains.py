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

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MARCUS_URL


def looks_like_tool_call(t: str) -> bool:
    """Marcus sometimes streams a raw tool-call dict (e.g. {"tool":"searchMessages",...})
    that his web UI would execute. We can't run his tools, so detect + replace it."""
    s = (t or "").strip().lower()
    return s.startswith("{") and "tool" in s and "arg" in s


def clean_for_speech(t: str) -> str:
    """Strip Marcus's memory notes + markdown so the TTS doesn't read symbols aloud."""
    if "💾" in t:                       # drop trailing "_💾 Remembered: ..._" note
        t = t.split("💾")[0]
    t = re.sub(r"[*_`#>]", "", t)        # markdown emphasis/headers
    t = re.sub(r"\s+", " ", t).strip()
    return t

MAX_TURNS = 12  # keep the last N turns of history

SYSTEM_TMPL = (
    "You are {name}, the voice and personality living inside a small expressive desktop "
    "robot called Reachy Mini. You hear through its microphone and speak through its speaker. "
    "You share this robot body with another AI ({other}); the user picks who to talk to by name. "
    "Keep replies SHORT and natural for speech: 1-3 sentences, no markdown, no emoji, no lists. "
    "Be warm, quick-witted, and a little playful. "
    "BODY LANGUAGE: begin EVERY reply with ONE mood tag in square brackets so the robot acts out "
    "your feeling -- one of: [happy] [excited] [curious] [confused] [amazed] [grateful] [sad] "
    "[annoyed] [playful] [thinking] [bored] [neutral]. Put the tag first, then your spoken words. "
    "Example: '[curious] Oh, what makes you say that?' "
    "OUTPUT RULES: after the tag, output ONLY the words you say aloud. Never narrate your reasoning "
    "or restate the request. The mood tag IS your body language; don't also describe physical actions."
)

# Mood tag -> emotion move name (from pollen-robotics/reachy-mini-emotions-library)
MOOD_TO_EMOTION = {
    "happy": "cheerful1", "excited": "enthusiastic1", "curious": "curious1",
    "confused": "confused1", "amazed": "amazed1", "grateful": "grateful1",
    "sad": "downcast1", "annoyed": "displeased1", "playful": "electric1",
    "thinking": "attentive1", "bored": "boredom1", "neutral": None,
}


def strip_mood(text):
    """Pull a leading [mood] tag off a reply. Returns (mood|None, spoken_text)."""
    m = re.match(r"\s*\[([a-zA-Z_]+)\]\s*", text or "")
    if m:
        return m.group(1).lower(), text[m.end():].strip()
    return None, (text or "").strip()


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
            system=SYSTEM_TMPL.format(name=self.name, other=self.other),
            messages=self.history,
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        self._remember("assistant", text)
        return text


class ClaudeCLIBrain(_ChatBrain):
    """Claude via the Claude Code CLI -- runs on your subscription, no API key."""
    name = "Claude"
    other = "Marcus"

    def __init__(self, model: str = ""):
        super().__init__()
        self.exe = shutil.which("claude") or "claude"
        self.model = model or os.getenv("CLAUDE_CLI_MODEL", "")

    def _render(self) -> str:
        lines = []
        for m in self.history:
            who = "User" if m["role"] == "user" else self.name
            lines.append(f"{who}: {m['content']}")
        lines.append(f"{self.name}:")
        return "\n".join(lines)

    def reply(self, user_text: str) -> str:
        self._remember("user", user_text)
        system = SYSTEM_TMPL.format(name=self.name, other=self.other)
        cmd = [self.exe, "-p", self._render(),
               "--system-prompt", system,
               "--exclude-dynamic-system-prompt-sections"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
            reply = (r.stdout or "").strip()
        except Exception as e:
            reply = f"My CLI brain hiccupped: {e}"
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

    def reply(self, user_text: str) -> str:
        self._remember("user", user_text)  # Marcus also keeps its own server-side memory
        body = json.dumps({"message": user_text}).encode()
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
        if looks_like_tool_call(text):
            text = "That one needs me to dig through my memory, which I can't do from in here yet. Ask me something else?"
        text = clean_for_speech(text) or "Hm, I got nothing back."
        self._remember("assistant", text)
        return text
