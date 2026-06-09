"""Web control panel for the Reachy twin. Serves a single page + JSON API.

Run with the daemon already running:
    python -m twin.panel
Then open http://127.0.0.1:8500
"""
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from twin.hub import RobotHub

STATIC = Path(__file__).parent / "static"
hub = RobotHub()

# The panel must outlive the daemon: connect to the robot in the background and
# keep retrying, so /api/state can say "robot: disconnected" instead of the whole
# process dying when the daemon isn't up yet (or restarts).
_connect_stop = threading.Event()


def _connect_loop():
    while not _connect_stop.is_set() and not hub.robot_connected:
        try:
            hub.start()
        except Exception as e:
            hub.last_error = str(e)
            print(f"[panel] robot connect failed: {e} -- retrying in 5s")
            _connect_stop.wait(5)


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_connect_loop, daemon=True).start()
    yield
    _connect_stop.set()
    hub.shutdown()


app = FastAPI(lifespan=lifespan)


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


class JogReq(BaseModel):
    part: str       # pitch | roll | yaw | body | ant
    delta: float    # degrees


class LookReq(BaseModel):
    yaw_deg: float      # + left (robot's frame), absolute
    pitch_deg: float    # + down, absolute


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


@app.get("/api/snapshot")
def snapshot():
    jpg = hub.get_jpeg()
    if not jpg:
        return Response(status_code=204)     # no frame available
    return Response(content=jpg, media_type="image/jpeg")


@app.post("/api/ipad_frame")
async def post_ipad_frame(request: Request):
    body = await request.body()
    hub.push_ipad_frame(body)
    return {"ok": True, "bytes": len(body)}


@app.get("/api/ipad_snapshot")
def ipad_snapshot():
    jpg = hub.get_ipad_jpeg()
    if not jpg:
        return Response(status_code=204)
    return Response(content=jpg, media_type="image/jpeg")


@app.post("/api/jog")
def post_jog(r: JogReq):
    return {"pose": hub.jog(r.part, r.delta)}


@app.post("/api/look")
def post_look(r: LookReq):
    return {"pose": hub.look(r.yaw_deg, r.pitch_deg)}


@app.post("/api/center")
def post_center():
    return {"pose": hub.center()}


@app.get("/api/servos")
def get_servos():
    return hub.servos()


def main():
    host = os.environ.get("REACHY_PANEL_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=8500, log_level="warning")


if __name__ == "__main__":
    main()
