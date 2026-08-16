"""Rebuild the original Field Signal ambience used by the public evaluation build."""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 22_050
DURATION_SECONDS = 24
OUTPUT = Path(__file__).resolve().parents[1] / "hud" / "media" / "field-signal.wav"


def envelope(index: int, total: int) -> float:
    phase = index / total
    fade = min(1.0, phase * 8.0, (1.0 - phase) * 8.0)
    return max(0.0, fade)


def build() -> Path:
    randomizer = random.Random(511)
    total = SAMPLE_RATE * DURATION_SECONDS
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        frames = bytearray()
        filtered_noise = 0.0
        for index in range(total):
            time = index / SAMPLE_RATE
            filtered_noise = (filtered_noise * 0.996) + (randomizer.uniform(-1, 1) * 0.004)
            low_pulse = math.sin(2 * math.pi * 55 * time) * 0.10
            harmonic = math.sin(2 * math.pi * 82.5 * time + math.sin(time * 0.17)) * 0.045
            beacon = math.sin(2 * math.pi * 220 * time) * (0.018 if int(time * 2) % 8 == 0 else 0.0)
            sample = (low_pulse + harmonic + filtered_noise * 0.08 + beacon) * envelope(index, total)
            frames.extend(struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767)))
        target.writeframes(frames)
    return OUTPUT


if __name__ == "__main__":
    print(build())
