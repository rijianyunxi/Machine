"""
Training subprocess worker: reads an args JSON and runs ultralytics training.
Isolated so a training crash cannot affect the panel or the detection loop.
"""

import json
import sys


def main():
    args = json.loads(open(sys.argv[1], encoding="utf-8").read())
    from ultralytics import YOLO

    device = args.get("device", "auto")
    model = YOLO(args["model"])
    model.train(
        data=args["data"],
        epochs=args["epochs"],
        imgsz=args["imgsz"],
        batch=args["batch"],
        device=None if device == "auto" else device,
        project=args["project"],
        name=args["name"],
        exist_ok=True,
        patience=20,
    )
    print("[train-worker] done", flush=True)


if __name__ == "__main__":
    main()
