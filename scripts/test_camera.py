"""
Single camera connectivity test tool.

Usage:
    python scripts/test_camera.py <rtsp_url>

Example:
    python scripts/test_camera.py rtsp://192.168.1.101:554/stream1
"""

import sys
import time

import cv2


def test_camera(rtsp_url: str, timeout: int = 10):
    """
    Test RTSP camera connectivity by attempting to capture and display a frame.

    Args:
        rtsp_url: RTSP stream URL.
        timeout: Connection timeout in seconds.
    """
    print(f"Testing camera: {rtsp_url}")
    print(f"Timeout: {timeout}s")
    print("-" * 50)

    # Open RTSP stream
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

    if not cap.isOpened():
        print("[ERROR] Failed to open RTSP stream")
        print("  Possible causes:")
        print("  - Camera is offline or unreachable")
        print("  - Incorrect RTSP URL")
        print("  - Network/firewall blocking the connection")
        print("  - Camera requires authentication")
        return False

    print("[OK] Stream opened successfully")

    # Read stream properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"  Resolution: {width}x{height}")
    print(f"  FPS: {fps}")

    # Try to read a frame
    start_time = time.time()
    success, frame = cap.read()

    if not success or frame is None:
        print("[ERROR] Failed to read frame from stream")
        cap.release()
        return False

    elapsed = time.time() - start_time
    print(f"[OK] Frame captured in {elapsed:.2f}s")
    print(f"  Frame shape: {frame.shape}")

    # Save test frame to disk
    output_path = "test_frame.jpg"
    cv2.imwrite(output_path, frame)
    print(f"[OK] Test frame saved to: {output_path}")

    # Read a few more frames to test stability
    print("\nReading 10 frames to test stream stability...")
    success_count = 0
    fail_count = 0
    for i in range(10):
        ok, f = cap.read()
        if ok and f is not None:
            success_count += 1
        else:
            fail_count += 1
        time.sleep(0.1)

    print(f"  Success: {success_count}/10, Failed: {fail_count}/10")

    cap.release()

    if success_count >= 8:
        print("\n[PASS] Camera connectivity test passed")
        return True
    else:
        print("\n[WARN] Camera stream is unstable, check network/camera")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_camera.py <rtsp_url>")
        print("Example: python scripts/test_camera.py rtsp://192.168.1.101:554/stream1")
        sys.exit(1)

    url = sys.argv[1]
    result = test_camera(url)
    sys.exit(0 if result else 1)
