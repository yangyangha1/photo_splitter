from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerPlan:
    count: int
    reason: str


def _positive_env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def plan_workers(kind: str, total: int, backend: str = "opencv-cpu") -> WorkerPlan:
    if total <= 1:
        return WorkerPlan(1, "single-item")

    env_name = "PHOTO_SPLITTER_DETECT_WORKERS" if kind == "detect" else "PHOTO_SPLITTER_EXPORT_WORKERS"
    override = _positive_env_int(env_name)
    if override is not None:
        return WorkerPlan(max(1, min(total, override)), f"{env_name}={override}")

    cpu_count = max(1, os.cpu_count() or 1)
    if backend == "opencv-opencl":
        count = 2 if kind == "detect" else max(2, min(4, cpu_count // 2 or 1))
        return WorkerPlan(min(total, count), backend)

    if kind == "detect":
        count = max(2, min(6, cpu_count // 2 or 1))
    else:
        count = max(2, min(8, cpu_count // 2 or 1))
    return WorkerPlan(min(total, count), backend)


def jpeg_save_kwargs(fast: bool = True, quality: int = 95) -> dict[str, object]:
    optimize = os.environ.get("PHOTO_SPLITTER_JPEG_OPTIMIZE", "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "format": "JPEG",
        "quality": int(quality),
        "subsampling": 0,
        "optimize": bool(optimize and not fast),
        "progressive": bool(optimize and not fast),
    }
