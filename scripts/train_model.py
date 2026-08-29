"""
Train a PPE detection model using Ultralytics Construction-PPE dataset.

This script trains a YOLOv8 model on the official Construction-PPE dataset
which contains 11 classes:
  0: helmet
  1: gloves
  2: vest
  3: boots
  4: goggles
  5: none
  6: Person
  7: no_helmet
  8: no_goggle
  9: no_gloves
  10: no_boots

Usage:
    python scripts/train_model.py                  # Train with default settings
    python scripts/train_model.py --epochs 200     # Custom epochs
    python scripts/train_model.py --device cpu      # Force CPU training

After training, copy the best model:
    cp runs/detect/train/weights/best.pt models/yolov8-ppe.pt

Then update config/settings.yaml:
    model:
      path: "models/yolov8-ppe.pt"
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


def train(
    model_size: str = "yolov8n",
    epochs: int = 100,
    img_size: int = 640,
    batch: int = 16,
    device: str = "auto",
    project: str = "runs/detect",
    name: str = "ppe_train",
):
    """
    Train YOLOv8 model on Construction-PPE dataset.

    Args:
        model_size: Model size (yolov8n, yolov8s, yolov8m, yolov8l, yolov8x)
        epochs: Number of training epochs
        img_size: Input image size
        batch: Batch size
        device: Training device (auto, cpu, 0, 0,1, etc.)
        project: Output project directory
        name: Run name
    """
    from ultralytics import YOLO

    print("=" * 60)
    print("  PPE Detection Model Training")
    print("=" * 60)
    print(f"  Model:    {model_size}.pt")
    print(f"  Dataset:  construction-ppe (11 classes, 1416 images)")
    print(f"  Epochs:   {epochs}")
    print(f"  Img size: {img_size}")
    print(f"  Batch:    {batch}")
    print(f"  Device:   {device}")
    print("=" * 60)
    print()

    # Load pretrained model
    print(f"[INFO] Loading pretrained {model_size}.pt...")
    model = YOLO(f"{model_size}.pt")

    # Train on Construction-PPE dataset
    # Dataset will auto-download on first run (~178 MB)
    print("[INFO] Starting training on construction-ppe dataset...")
    print("[INFO] Dataset will be downloaded automatically if not present")
    print()

    results = model.train(
        data="construction-ppe.yaml",
        epochs=epochs,
        imgsz=img_size,
        batch=batch,
        device=device if device != "auto" else None,
        project=project,
        name=name,
        patience=20,          # Early stopping patience
        optimizer="auto",
        lr0=0.001,
        lrf=0.01,
        augment=True,         # Data augmentation
        mosaic=1.0,           # Mosaic augmentation
        mixup=0.1,            # MixUp augmentation
        copy_paste=0.1,       # Copy-paste augmentation
        verbose=True,
    )

    # Output results
    best_model = os.path.join(project, name, "weights", "best.pt")
    last_model = os.path.join(project, name, "weights", "last.pt")

    print()
    print("=" * 60)
    print("  Training Complete!")
    print("=" * 60)
    print(f"  Best model: {best_model}")
    print(f"  Last model: {last_model}")
    print()
    print("  Next steps:")
    print(f"  1. Copy model: cp {best_model} models/yolov8-ppe.pt")
    print(f'  2. Update config/settings.yaml -> model.path: "models/yolov8-ppe.pt"')
    print(f"  3. Run: python main.py")
    print("=" * 60)

    return best_model


def validate(model_path: str, data: str = "construction-ppe.yaml"):
    """Validate a trained model on the test set."""
    from ultralytics import YOLO

    print(f"[INFO] Validating model: {model_path}")
    model = YOLO(model_path)
    results = model.val(data=data, split="test")

    print(f"\n  mAP50:    {results.box.map50:.4f}")
    print(f"  mAP50-95: {results.box.map:.4f}")
    print(f"  Precision: {results.box.mp:.4f}")
    print(f"  Recall:    {results.box.mr:.4f}")

    # Per-class results
    print("\n  Per-class mAP50:")
    for i, (name, ap50) in enumerate(
        zip(results.names.values(), results.box.ap50)
    ):
        print(f"    {name:15s}: {ap50:.4f}")


def export_onnx(model_path: str, img_size: int = 640):
    """Export model to ONNX format for faster inference."""
    from ultralytics import YOLO

    print(f"[INFO] Exporting {model_path} to ONNX...")
    model = YOLO(model_path)
    onnx_path = model.export(format="onnx", imgsz=img_size, simplify=True)
    print(f"[OK] ONNX model saved: {onnx_path}")
    return onnx_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train PPE detection model")
    parser.add_argument(
        "--mode",
        choices=["train", "validate", "export"],
        default="train",
        help="Operation mode",
    )
    parser.add_argument("--model", type=str, default="yolov8n", help="Model size or path")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--img-size", type=int, default=640, help="Image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--device", type=str, default="auto", help="Device")
    parser.add_argument("--model-path", type=str, default="", help="Model path for validate/export")

    args = parser.parse_args()

    if args.mode == "train":
        train(
            model_size=args.model,
            epochs=args.epochs,
            img_size=args.img_size,
            batch=args.batch,
            device=args.device,
        )
    elif args.mode == "validate":
        if not args.model_path:
            print("ERROR: --model-path required for validate mode")
            sys.exit(1)
        validate(args.model_path)
    elif args.mode == "export":
        if not args.model_path:
            print("ERROR: --model-path required for export mode")
            sys.exit(1)
        export_onnx(args.model_path, args.img_size)
