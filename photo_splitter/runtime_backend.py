from __future__ import annotations

import os
import platform
import subprocess
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageFilter


def _opencl_disabled() -> bool:
    return os.environ.get("PHOTO_SPLITTER_DISABLE_OPENCL", "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _cv2_module():
    import cv2

    if hasattr(cv2, "ocl") and _opencl_disabled():
        try:
            cv2.ocl.setUseOpenCL(False)
        except Exception:
            pass
    return cv2


def _rgb_u8(rgb: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)


def _filter_size_from_kernel(kernel: np.ndarray | int) -> int:
    if isinstance(kernel, np.ndarray):
        size = int(max(kernel.shape[:2]))
    else:
        size = int(kernel)
    size = max(1, size)
    return size if size % 2 == 1 else size + 1


def _pil_close_and_dilate(src: np.ndarray, kernel: np.ndarray | int, close_iterations: int = 1, dilate_iterations: int = 0) -> np.ndarray:
    size = _filter_size_from_kernel(kernel)
    image = Image.fromarray((np.asarray(src) > 0).astype(np.uint8) * 255, "L")
    if size > 1:
        for _ in range(max(0, close_iterations)):
            image = image.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
        for _ in range(max(0, dilate_iterations)):
            image = image.filter(ImageFilter.MaxFilter(size))
    return np.asarray(image, dtype=np.uint8)


def _numpy_edge_mask(blur: np.ndarray, lower: int, upper: int) -> np.ndarray:
    gray = np.asarray(blur, dtype=np.float32)
    gx = np.zeros_like(gray, dtype=np.float32)
    gy = np.zeros_like(gray, dtype=np.float32)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    magnitude = np.sqrt(gx * gx + gy * gy)
    threshold = max(float(lower), min(float(upper), float(np.percentile(magnitude, 86))))
    return (magnitude >= threshold).astype(np.uint8) * 255


@lru_cache(maxsize=1)
def opencv_available() -> bool:
    try:
        _cv2_module()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def opencv_opencl_available() -> bool:
    if _opencl_disabled():
        return False
    try:
        cv2 = _cv2_module()
        if not hasattr(cv2, "ocl") or not cv2.ocl.haveOpenCL():
            return False
        cv2.ocl.setUseOpenCL(True)
        test = np.zeros((8, 8, 3), dtype=np.uint8)
        umat = cv2.UMat(test)
        _ = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY).get()
        return bool(cv2.ocl.useOpenCL())
    except Exception:
        return False


def configure_opencv_threads(worker_count: int | None = None) -> int:
    cpu_count = max(1, os.cpu_count() or 1)
    outer_workers = max(1, int(worker_count or 1))
    threads = max(1, min(8, cpu_count // outer_workers or 1))
    try:
        cv2 = _cv2_module()
        cv2.setNumThreads(threads)
        return int(cv2.getNumThreads())
    except Exception:
        return threads


def get_compute_backend() -> str:
    if opencv_opencl_available():
        return "opencv-opencl"
    if opencv_available():
        return "opencv-cpu"
    return "numpy-cpu"


def gray_and_channel_range(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rgb_u8 = _rgb_u8(rgb)

    if opencv_opencl_available():
        try:
            cv2 = _cv2_module()
            umat = cv2.UMat(rgb_u8)
            gray = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY).get().astype(np.float32)
            channel_range = rgb_u8.max(axis=2).astype(np.int16) - rgb_u8.min(axis=2).astype(np.int16)
            return gray, channel_range
        except Exception:
            pass

    if opencv_available():
        try:
            cv2 = _cv2_module()
            gray = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY).astype(np.float32)
            r, g, b = cv2.split(rgb_u8)
            min_channel = cv2.min(cv2.min(r, g), b)
            max_channel = cv2.max(cv2.max(r, g), b)
            channel_range = cv2.subtract(max_channel, min_channel).astype(np.int16)
            return gray, channel_range
        except Exception:
            pass

    rgb_float = rgb_u8.astype(np.float32)
    gray = 0.299 * rgb_float[:, :, 0] + 0.587 * rgb_float[:, :, 1] + 0.114 * rgb_float[:, :, 2]
    channel_range = rgb_u8.max(axis=2).astype(np.int16) - rgb_u8.min(axis=2).astype(np.int16)
    return gray, channel_range


def gray_and_blur(rgb: np.ndarray, kernel_size: int = 5) -> tuple[np.ndarray, np.ndarray]:
    rgb_u8 = _rgb_u8(rgb)
    kernel_size = max(3, int(kernel_size))
    if kernel_size % 2 == 0:
        kernel_size += 1

    if opencv_opencl_available():
        try:
            cv2 = _cv2_module()
            umat = cv2.UMat(rgb_u8)
            gray_u = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY)
            blur_u = cv2.GaussianBlur(gray_u, (kernel_size, kernel_size), 0)
            return gray_u.get(), blur_u.get()
        except Exception:
            pass

    if opencv_available():
        try:
            cv2 = _cv2_module()
            gray = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY)
            blur = cv2.GaussianBlur(gray, (kernel_size, kernel_size), 0)
            return gray, blur
        except Exception:
            pass

    rgb_float = rgb_u8.astype(np.float32)
    gray = np.clip(0.299 * rgb_float[:, :, 0] + 0.587 * rgb_float[:, :, 1] + 0.114 * rgb_float[:, :, 2], 0, 255).astype(np.uint8)
    blur = np.asarray(Image.fromarray(gray, "L").filter(ImageFilter.GaussianBlur(radius=max(1.0, kernel_size / 3.0))), dtype=np.uint8)
    return gray, blur


def canny_edges(blur: np.ndarray, lower: int, upper: int) -> np.ndarray:
    blur_u8 = np.ascontiguousarray(blur, dtype=np.uint8)

    if opencv_opencl_available():
        try:
            cv2 = _cv2_module()
            return cv2.Canny(cv2.UMat(blur_u8), lower, upper).get()
        except Exception:
            pass

    if opencv_available():
        try:
            cv2 = _cv2_module()
            return cv2.Canny(blur_u8, lower, upper)
        except Exception:
            pass

    return _numpy_edge_mask(blur_u8, lower, upper)


def close_and_dilate_edges(edges: np.ndarray, kernel: np.ndarray, close_iterations: int = 2, dilate_iterations: int = 1) -> np.ndarray:
    src = np.ascontiguousarray(edges, dtype=np.uint8)

    if opencv_opencl_available():
        try:
            cv2 = _cv2_module()
            umat = cv2.UMat(src)
            closed = cv2.morphologyEx(umat, cv2.MORPH_CLOSE, kernel, iterations=close_iterations)
            if dilate_iterations:
                closed = cv2.dilate(closed, kernel, iterations=dilate_iterations)
            return closed.get()
        except Exception:
            pass

    if opencv_available():
        try:
            cv2 = _cv2_module()
            closed_edges = cv2.morphologyEx(src, cv2.MORPH_CLOSE, kernel, iterations=close_iterations)
            if dilate_iterations:
                closed_edges = cv2.dilate(closed_edges, kernel, iterations=dilate_iterations)
            return closed_edges
        except Exception:
            pass

    return _pil_close_and_dilate(src, kernel, close_iterations=close_iterations, dilate_iterations=dilate_iterations)


def close_u8(mask: np.ndarray, kernel: np.ndarray, iterations: int = 1) -> np.ndarray:
    src = np.ascontiguousarray(mask, dtype=np.uint8)

    if opencv_opencl_available():
        try:
            cv2 = _cv2_module()
            return cv2.morphologyEx(cv2.UMat(src), cv2.MORPH_CLOSE, kernel, iterations=iterations).get()
        except Exception:
            pass

    if opencv_available():
        try:
            cv2 = _cv2_module()
            return cv2.morphologyEx(src, cv2.MORPH_CLOSE, kernel, iterations=iterations)
        except Exception:
            pass

    return _pil_close_and_dilate(src, kernel, close_iterations=iterations)


def background_difference_mask(rgb: np.ndarray, actual_mode: str) -> np.ndarray:
    height, width = rgb.shape[:2]
    edge = max(6, min(width, height) // 32)
    rgb_u8 = _rgb_u8(rgb)
    samples = np.concatenate(
        [
            rgb_u8[:edge, :, :].reshape(-1, 3),
            rgb_u8[-edge:, :, :].reshape(-1, 3),
            rgb_u8[:, :edge, :].reshape(-1, 3),
            rgb_u8[:, -edge:, :].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)
    bg = np.median(samples, axis=0)
    diff = np.sqrt(np.sum((rgb_u8.astype(np.float32) - bg.reshape(1, 1, 3)) ** 2, axis=2))
    gray, channel_range = gray_and_channel_range(rgb_u8)

    if actual_mode == "black":
        threshold = 34.0
        plain_background = gray < 65
    elif actual_mode == "white":
        threshold = 28.0
        plain_background = (gray > 210) & (channel_range < 60)
    elif actual_mode == "gray":
        threshold = 24.0
        plain_background = (diff < 24) & (channel_range < 65)
    else:
        threshold = max(26.0, float(np.percentile(diff, 55)))
        plain_background = diff < threshold * 0.75

    return (diff > threshold) & ~plain_background


def close_mask(mask: np.ndarray, close_size: int) -> np.ndarray:
    close_size = max(1, int(close_size))
    if close_size <= 1:
        return mask.astype(bool)

    src = mask.astype(np.uint8) * 255
    if opencv_available():
        try:
            cv2 = _cv2_module()
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
            closed = close_u8(src, kernel)
            return closed > 0
        except Exception:
            pass

    image = Image.fromarray(src, "L")
    return np.asarray(image.filter(ImageFilter.MaxFilter(close_size)).filter(ImageFilter.MinFilter(close_size))) > 0


def detect_runtime_environment(probe_accelerators: bool = True) -> dict[str, Any]:
    info: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_name": "",
        "cpu_count": os.cpu_count() or 1,
        "gpu_name": "",
        "gpu_backend": "hardware-info-only",
        "gpu_available": False,
        "cuda_available": False,
        "cupy_cuda_available": False,
        "opencv_cuda_available": False,
        "opencv_opencl_available": False,
        "opencv_available": False,
        "opencv_threads": configure_opencv_threads(),
        "compute_backend": "pending-cpu" if not probe_accelerators else "numpy-cpu",
        "acceleration_note": "",
        "runtime_errors": {},
    }

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            info["gpu_name"] = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        pass
    info["gpu_available"] = bool(info["gpu_name"])

    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "Name", "/value"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Name="):
                info["cpu_name"] = line.split("=", 1)[1].strip()
                break
    except (OSError, subprocess.SubprocessError):
        pass
    if not info["cpu_name"]:
        info["cpu_name"] = platform.processor() or ""

    info["opencv_opencl_available"] = opencv_opencl_available()
    info["opencv_available"] = opencv_available()
    backend = get_compute_backend()
    info["compute_backend"] = backend
    if backend == "opencv-opencl":
        info["gpu_backend"] = "opencv-opencl"
        info["acceleration_note"] = "OpenCV OpenCL/T-API backend is active; JPEG encoding and cropping still run on CPU."
    elif backend == "opencv-cpu":
        info["acceleration_note"] = f"OpenCV CPU backend is active with {info['opencv_threads']} thread(s)."
    else:
        info["acceleration_note"] = "NumPy/Pillow CPU fallback is active; install OpenCV for the optimized CPU backend."
    return info
