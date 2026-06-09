"""Web control panel for the Reachy twin. Serves a single page + JSON API.

Run with the daemon already running:
    python -m twin.panel
Then open http://127.0.0.1:8500
"""
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
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


class JogReq(BaseModel):
    part: str       # pitch | roll | yaw | body | ant
    delta: float    # degrees


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


@app.get("/api/camera")
def camera():
    def gen():
        while True:
            jpg = hub.get_jpeg()
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.066)      # ~15 fps
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/jog")
def post_jog(r: JogReq):
    return {"pose": hub.jog(r.part, r.delta)}


@app.post("/api/center")
def post_center():
    return {"pose": hub.center()}


def main():
    uvicorn.run(app, host="127.0.0.1", port=8500, log_level="warning")


if __name__ == "__main__":
    main()
