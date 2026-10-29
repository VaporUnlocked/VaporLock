"""Run a trained S2 .cloud on one image and print its detection JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "runtime_sdk"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--backend", choices=("numpy", "torch"), default="numpy")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--nms-iou", type=float, default=0.5)
    parser.add_argument("--max-detections", type=int, default=32)
    args = parser.parse_args()
    try:
        from PIL import Image
        from cloud_sdk import CloudRuntime, MASK_ARCHITECTURE

        runtime = CloudRuntime(args.model, backend=args.backend, device=args.device)
        if runtime.config.architecture != MASK_ARCHITECTURE:
            raise ValueError(f"Expected current S2 ({MASK_ARCHITECTURE}); got {runtime.config.architecture}.")
        with Image.open(args.image) as image:
            result = runtime.predict(image, confidence=args.confidence,
                                     nms_iou=args.nms_iou,
                                     max_detections=args.max_detections, debug=True)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ImportError, OSError, ValueError, TypeError, RuntimeError) as exc:
        print(f"Cloud inference: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
