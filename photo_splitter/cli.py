from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .config import (
    BACKGROUND_MODES,
    DEFAULT_BACKGROUND_MODE,
    DEFAULT_DETECTION_STRATEGY,
    DEFAULT_PRESET_KEY,
    DETECTION_STRATEGIES,
    JPEG_QUALITY,
    PROCESSING_PRESETS,
)
from .io_utils import iter_images
from .processing import process_source_for_cli
from .runtime_backend import detect_runtime_environment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch split collage/framed photos into separate high-quality JPG files.")
    parser.add_argument("input", nargs="?", default=".", help="Input file or folder. Folders are processed recursively.")
    parser.add_argument("-o", "--output", default="split_output", help="Output folder. Default: split_output")
    parser.add_argument(
        "--preset",
        choices=sorted(PROCESSING_PRESETS),
        default=DEFAULT_PRESET_KEY,
        help=f"Processing preset. Default: {DEFAULT_PRESET_KEY}",
    )
    parser.add_argument("--dark-threshold", type=int, default=None, help="Override preset dark/frame threshold.")
    parser.add_argument("--min-area-ratio", type=float, default=None, help="Override preset minimum photo area ratio.")
    parser.add_argument("--white-threshold", type=int, default=None, help="Override preset white-border trim threshold.")
    parser.add_argument("--background-mode", choices=sorted(BACKGROUND_MODES), default=None, help="Source background mode: auto, white, gray or black. Black skips white-border trimming.")
    parser.add_argument("--detection-strategy", choices=sorted(DETECTION_STRATEGIES), default=None, help="Detection behavior: balanced, aggressive or conservative.")
    parser.add_argument("--split-strategy", dest="detection_strategy", choices=sorted(DETECTION_STRATEGIES), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--skew-gain-percent", type=int, default=None, help="Override preset deskew sensitivity, in percent.")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY, help=f"Output JPG quality, 1-100. Default: {JPEG_QUALITY}")
    parser.add_argument("--preview", action="store_true", help="Also save preview images with detected photo boxes drawn on the original.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel source files to process. Default: 1")
    parser.add_argument("--runtime-info", action="store_true", help="Print CPU/GPU/OpenCL runtime information before processing.")
    parser.add_argument(
        "--auto-face-rotate",
        action="store_true",
        help="Use OpenCV face detection to conservatively rotate extracted portrait photos to upright orientation.",
    )
    return parser.parse_args()


def resolved_options(args: argparse.Namespace) -> dict[str, object]:
    preset = PROCESSING_PRESETS[args.preset]
    return {
        "preset": preset.key,
        "dark_threshold": int(args.dark_threshold if args.dark_threshold is not None else preset.dark_threshold),
        "min_area_ratio": float(args.min_area_ratio if args.min_area_ratio is not None else preset.min_area_ratio),
        "white_threshold": int(args.white_threshold if args.white_threshold is not None else preset.white_threshold),
        "background_mode": str(args.background_mode if args.background_mode is not None else getattr(preset, "background_mode", DEFAULT_BACKGROUND_MODE)),
        "detection_strategy": str(args.detection_strategy if args.detection_strategy is not None else getattr(preset, "detection_strategy", DEFAULT_DETECTION_STRATEGY)),
        "skew_min_score_gain": 1.0 + int(args.skew_gain_percent if args.skew_gain_percent is not None else preset.skew_gain_percent) / 100,
        "auto_face_rotate": bool(args.auto_face_rotate),
    }


def main() -> int:
    args = parse_args()
    options = resolved_options(args)
    input_path = Path(args.input)
    output_path = Path(args.output)
    images = iter_images(input_path, output_path)

    if not images:
        print("No JPG/PNG/TIFF files found.")
        return 1

    input_root = input_path.resolve() if input_path.is_dir() else input_path.resolve().parent
    output_path.mkdir(parents=True, exist_ok=True)

    if args.runtime_info:
        runtime = detect_runtime_environment()
        print(
            "Runtime: "
            f"Python {runtime['python']}, CPU logical cores {runtime['cpu_count']}, "
            f"GPU {runtime['gpu_name'] or 'not detected'}, compute backend {runtime['compute_backend']}, "
            f"CUDA {'available' if runtime.get('cuda_available') else 'not enabled'}, "
            f"OpenCL {'available' if runtime.get('opencv_opencl_available') else 'not available'}"
        )
        if runtime.get("acceleration_note"):
            print(f"Runtime note: {runtime['acceleration_note']}")

    total_saved = 0
    worker_count = max(1, min(int(args.workers), len(images)))
    jobs = [
        (
            source,
            input_root,
            output_path,
            int(args.quality),
            bool(args.preview),
            bool(args.overwrite),
            int(options["dark_threshold"]),
            float(options["min_area_ratio"]),
            int(options["white_threshold"]),
            float(options["skew_min_score_gain"]),
            bool(options["auto_face_rotate"]),
            str(options["background_mode"]),
            str(options["detection_strategy"]),
            True,
        )
        for source in images
    ]

    if worker_count == 1:
        results = [process_source_for_cli(job) for job in jobs]
    else:
        results = []
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(process_source_for_cli, job): job[0] for job in jobs}
            for future in as_completed(future_map):
                results.append(future.result())

    for result in results:
        if result["ok"]:
            detected = int(result["detected"])
            total_saved += detected
            print(f"{result['source']}: saved {detected}")
        else:
            print(f"{result['source']}: failed: {result['error']}")

    print(f"Done. Saved {total_saved} photos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
