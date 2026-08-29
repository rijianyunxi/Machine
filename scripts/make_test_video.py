"""
Generate a local test video from a sample image (no RTSP needed for dev).

Usage:
    python scripts/make_test_video.py [source_image] [output] [seconds]
"""

import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_video(src="autodetect", out="test_videos/bus.mp4", seconds=20, fps=10):
    if src == "autodetect":
        import ultralytics

        pkg = Path(ultralytics.__file__).parent
        candidates = [pkg / "assets" / "bus.jpg",
                      pkg / "assets" / "zidane.jpg"]
        src = next(str(p) for p in candidates if p.exists())
    img = cv2.imread(src)
    assert img is not None, f"cannot read {src}"

    out_path = PROJECT_ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"avc1"),
                             fps, (w, h))
    n = int(seconds * fps)
    for i in range(n):
        # tiny vertical jitter so frames are not byte-identical
        shift = i % 4
        frame = img[shift:, :, :] if shift == 0 else img[:-shift, :, :]
        frame = cv2.resize(frame, (w, h))
        writer.write(frame)
    writer.release()
    print(f"[OK] {out_path} ({n} frames @ {fps}fps, from {src})")


if __name__ == "__main__":
    make_video()
