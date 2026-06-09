"""Step 0 - confirm Reachy Mini is alive: connect, wiggle antennas, nod."""
import time
import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

with ReachyMini() as mini:  # auto-detects Lite + localhost
    print("connected:", mini)
    mini.goto_target(antennas=np.deg2rad([45, 45]), duration=0.4, method="cartoon")
    mini.goto_target(antennas=np.deg2rad([-45, -45]), duration=0.4, method="cartoon")
    mini.goto_target(head=create_head_pose(z=10, mm=True), duration=0.5)  # nod up
    mini.goto_target(head=create_head_pose(z=0, mm=True), duration=0.5)
    mini.goto_target(body_yaw=np.deg2rad(20), duration=0.6)               # look left
    mini.goto_target(body_yaw=np.deg2rad(-20), duration=0.6)              # look right
    mini.goto_target(body_yaw=0.0, antennas=np.deg2rad([0, 0]), duration=0.5)
    time.sleep(0.3)
    print("alive test done")
