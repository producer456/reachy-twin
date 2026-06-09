"""Raw OS-level capture from the Reachy mic (bypasses SDK/daemon) to isolate hw vs software."""
import numpy as np
import sounddevice as sd

dev = None
sr = 16000
for i, d in enumerate(sd.query_devices()):
    if "Reachy Mini Audio" in d["name"] and d["max_input_channels"] > 0:
        dev = i
        sr = int(d["default_samplerate"])
        break

print(f"recording from device {dev} ({sd.query_devices(dev)['name']}) @ {sr}Hz for 7s...")
rec = sd.rec(int(7 * sr), samplerate=sr, channels=2, device=dev, dtype="float32")
sd.wait()
mono = rec.mean(axis=1)
rms = float(np.sqrt(np.mean(mono ** 2)))
peak = float(np.abs(mono).max())
# windowed peak to see if ANY speech burst landed
win = sr // 2
wins = [float(np.abs(mono[i:i + win]).max()) for i in range(0, len(mono) - win, win)]
print(f"RMS={rms:.5f}  PEAK={peak:.5f}")
print("half-second window peaks:", [round(w, 4) for w in wins])
print("VERDICT:", "SIGNAL PRESENT (mic works)" if peak > 0.01 else "ALL ZEROS / DEAD (hardware/cable)")
