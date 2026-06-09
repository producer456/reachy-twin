"""Graceful sleep: move to the sleep pose and relax the motors."""
from reachy_mini import ReachyMini

with ReachyMini() as mini:
    mini.goto_sleep()
    mini.disable_motors()
    print("asleep")
