"""Mic probe: measure chunk size, RMS (noise floor vs speech), and DoA/VAD availability."""
import time

import numpy as np
from reachy_mini import ReachyMini

with ReachyMini(media_backend="default") as mini:
    try:
        sr = mini.media.get_input_audio_samplerate()
        ch = mini.media.get_input_channels()
        print(f"input samplerate={sr} channels={ch}")
    except Exception as e:
        print("samplerate/channels query failed:", e)

    mini.media.start_recording()
    doa_ok = True
    t0 = time.time()
    rms_vals = []
    n = 0
    try:
        while time.time() - t0 < 6.0:
            s = mini.media.get_audio_sample()
            if s is None or len(s) == 0:
                time.sleep(0.01)
                continue
            mono = s.mean(axis=1) if getattr(s, "ndim", 1) == 2 else s
            rms = float(np.sqrt(np.mean(mono.astype(np.float32) ** 2)))
            rms_vals.append(rms)
            doa = sp = None
            if doa_ok:
                try:
                    doa, sp = mini.media.get_DoA()
                except Exception as e:
                    doa_ok = False
                    sp = f"<DoA unavailable: {e}>"
            if n % 4 == 0:
                print(f"chunk={getattr(s,'shape',len(s))} rms={rms:.4f} doa={doa} speech={sp}")
            n += 1
    finally:
        mini.media.stop_recording()

    if rms_vals:
        arr = np.array(rms_vals)
        print(f"\nchunks={n} rms min={arr.min():.4f} median={np.median(arr):.4f} "
              f"p90={np.percentile(arr,90):.4f} max={arr.max():.4f}")
    print("DoA/VAD available:", doa_ok)
