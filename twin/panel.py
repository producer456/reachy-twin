"""Web control panel for the Reachy twin. Serves a single page + JSON API.

Run with the daemon already running:
    python -m twin.panel
Then open http://127.0.0.1:8500
"""
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from twin.hub import RobotHub

STATIC = Path(__file__).parent / "static"
hub = RobotHub()
app = FastAPI()


class ChatReq(BaseModel):
    text: str
    brain: Optional[str] = None


class SayReq(BaseModel):
    text: str
    voice: Optional[str] = None


class BrainReq(BaseModel):
    brain: str


class ListenReq(BaseModel):
    on: bool


class VolumeReq(BaseModel):
    volume: int


class MoveReq(BaseModel):
    kind: str       # "emotion" | "dance"
    name: str


class BehaviorReq(BaseModel):
    name: str       # turn_to_sound | face_track | emotions_on_cue
    on: bool


@app.on_event("startup")
def _startup():
    hub.start()


@app.on_event("shutdown")
def _shutdown():
    hub.shutdown()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def get_state():
    return hub.state()


@app.post("/api/chat")          # sync def -> runs in threadpool, blocking is fine
def post_chat(r: ChatReq):
    return hub.chat(r.text, r.brain)


@app.post("/api/say")
def post_say(r: SayReq):
    hub.say(r.text, r.voice)
    return {"ok": True}


@app.post("/api/brain")
def post_brain(r: BrainReq):
    return {"active": hub.set_brain(r.brain)}


@app.post("/api/listen")
def post_listen(r: ListenReq):
    return {"listening": hub.set_listening(r.on)}


@app.post("/api/volume")
def post_volume(r: VolumeReq):
    return hub.set_volume(r.volume)


@app.get("/api/moves")
def get_moves():
    return hub.list_moves()


@app.post("/api/move")
def post_move(r: MoveReq):
    return hub.play(r.kind, r.name)


@app.post("/api/behavior")
def post_behavior(r: BehaviorReq):
    return {"behaviors": hub.set_behavior(r.name, r.on)}


def main():
    uvicorn.run(app, host="127.0.0.1", port=8500, log_level="warning")


if __name__ == "__main__":
    main()
