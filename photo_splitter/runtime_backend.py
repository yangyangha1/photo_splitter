from __future__ import annotations

import os
import platform
import subprocess
import traceback
from importlib.util import find_spec
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter


_DLL_DIRECTORY_HANDLES: list[Any] = []
_RUNTIME_ERRORS: dict[str, str] = {}


def _remember_runtime_error(key: str, exc: Exception | None) -> None:
    if exc is None:
        _RUNTIME_ERRORS.pop(key, None)
    else:
        _RUNTIME_ERRORS[key] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


@lru_cache(maxsize=1)
def prepare_cuda_dll_directories() -> None:
    """把系统 CUDA bin 目录加入当前进程 DLL 搜索路径。

    PyInstaller onefile 启动后会优先搜索临时解包目录，Windows 不一定会自动
    使用 CUDA_PATH 或 PATH 中的 CUDA 目录。这里显式注册，确保 cupy-cuda12x
    能找到目标机器已安装的 CUDA 12 runtime DLL。
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = []
    for env_name in ("CUDA_PATH", "CUDA_PATH_V12_8", "CUDA_PATH_V12_7", "CUDA_PATH_V12_6"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value) / "bin")

    for path_item in os.environ.get("PATH", "").split(os.pathsep):
        if path_item and "CUDA" in path_item.upper():
            candidates.append(Path(path_item))

    cuda_root = Path("C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA")
    if cuda_root.exists():
        candidates.extend(path / "bin" for path in sorted(cuda_root.glob("v12.*"), reverse=True))

    seen: set[str] = set()
    for directory in candidates:
        try:
            resolved = str(directory.resolve())
        except OSError:
            continue
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        if not any(directory.glob("cudart64_12.dll")):
            continue
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(resolved))
        except OSError:
            pass


def _rgb_u8(rgb: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)


def _filter_size_from_kernel(kernel: np.ndarray | int) -> int:
    """把 OpenCV kernel 尺寸转换成 Pillow 形态学滤镜需要的奇数窗口。"""
    if isinstance(kernel, np.ndarray):
        size = int(max(kernel.shape[:2]))
    else:
        size = int(kernel)
    size = max(1, size)
    return size if size % 2 == 1 else size + 1


def _pil_close_and_dilate(src: np.ndarray, kernel: np.ndarray | int, close_iterations: int = 1, dilate_iterations: int = 0) -> np.ndarray:
    """无 OpenCV 时的二值形态学后备实现。

    Pillow 的 MaxFilter 等价于二值膨胀，MinFilter 等价于二值腐蚀；先膨胀再腐蚀即闭运算。
    """
    size = _filter_size_from_kernel(kernel)
    image = Image.fromarray((np.asarray(src) > 0).astype(np.uint8) * 255, "L")
    if size > 1:
        for _ in range(max(0, close_iterations)):
            image = image.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
        for _ in range(max(0, dilate_iterations)):
            image = image.filter(ImageFilter.MaxFilter(size))
    return np.asarray(image, dtype=np.uint8)


def _numpy_edge_mask(blur: np.ndarray, lower: int, upper: int) -> np.ndarray:
    """无 OpenCV 时的轻量边缘后备实现。

    这不是完整 Canny，但足以让 no-opencv 版本保留基础边缘提示；正式版仍推荐 OpenCV。
    """
    gray = np.asarray(blur, dtype=np.float32)
    gx = np.zeros_like(gray, dtype=np.float32)
    gy = np.zeros_like(gray, dtype=np.float32)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    magnitude = np.sqrt(gx * gx + gy * gy)
    threshold = max(float(lower), min(float(upper), float(np.percentile(magnitude, 86))))
    return (magnitude >= threshold).astype(np.uint8) * 255


def release_cupy_memory(cp_module: Any | None = None) -> None:
    """释放 CuPy 默认显存池，避免检测结束后显存长期显示为被程序占用。

    CuPy 为了加速后续计算会缓存显存块；这不是泄漏，但桌面工具更需要空闲时及时归还显存。
    CUDA context 仍会保留少量基础显存，这是驱动行为，只有进程退出才会完全释放。
    """
    try:
        import gc

        cp = cp_module
        if cp is None:
            import cupy as cp

        cp.cuda.Stream.null.synchronize()
        cp.cuda.Device().synchronize()
        gc.collect()
        cp.get_default_memory_pool().free_all_blocks()
        cp.get_default_pinned_memory_pool().free_all_blocks()
    except Exception:
        pass


@lru_cache(maxsize=1)
def cupy_cuda_available() -> bool:
    """实际试跑 CuPy CUDA。

    不能只判断 import 成功，因为打包环境里可能存在 CuPy 包但没有可用驱动、显卡或 CUDA runtime。
    这里会执行一次小数组上传、计算和下载；任何环节失败都回退到 False。
    """
    try:
        prepare_cuda_dll_directories()
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() <= 0:
            return False
        test = cp.asarray(np.zeros((8, 8, 3), dtype=np.uint8))
        _ = cp.asnumpy(test.max(axis=2) - test.min(axis=2))
        test = None
        release_cupy_memory(cp)
        _remember_runtime_error("cupy_cuda", None)
        return True
    except Exception as exc:
        _remember_runtime_error("cupy_cuda", exc)
        return False


@lru_cache(maxsize=1)
def opencv_cuda_available() -> bool:
    """实际试跑 OpenCV CUDA。

    pip 常见的 opencv-python/opencv-python-headless 通常不带 CUDA 编译支持，
    所以必须用一次真实 GpuMat 运算验证，不能只看 cv2.cuda 是否存在。
    """
    try:
        prepare_cuda_dll_directories()
        import cv2

        if not hasattr(cv2, "cuda") or cv2.cuda.getCudaEnabledDeviceCount() <= 0:
            return False
        test = np.zeros((8, 8, 3), dtype=np.uint8)
        gpu_mat = cv2.cuda_GpuMat()
        gpu_mat.upload(test)
        _ = cv2.cuda.cvtColor(gpu_mat, cv2.COLOR_RGB2GRAY).download()
        _remember_runtime_error("opencv_cuda", None)
        return True
    except Exception as exc:
        _remember_runtime_error("opencv_cuda", exc)
        return False


@lru_cache(maxsize=1)
def opencv_opencl_available() -> bool:
    """实际试跑 OpenCV OpenCL / T-API。

    OpenCL 可能运行在独显、核显或 CPU OpenCL 驱动上，因此这里只标记为“加速后端”，
    不把它绝对等同于 GPU。失败时安全回退到 CPU。
    """
    try:
        import cv2

        if not cv2.ocl.haveOpenCL():
            return False
        cv2.ocl.setUseOpenCL(True)
        test = np.zeros((8, 8, 3), dtype=np.uint8)
        umat = cv2.UMat(test)
        _ = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY).get()
        return bool(cv2.ocl.useOpenCL())
    except Exception:
        return False


@lru_cache(maxsize=1)
def opencv_available() -> bool:
    """判断 OpenCV 基础 CPU 功能是否可用。即使没有 GPU，它也能加速灰度转换、形态学、连通域等操作。"""
    try:
        import cv2  # noqa: F401

        return True
    except Exception:
        return False


def get_compute_backend() -> str:
    """返回当前可用计算后端；按 CuPy CUDA、OpenCV CUDA、OpenCL、OpenCV CPU、NumPy CPU 的顺序降级。"""
    if cupy_cuda_available():
        return "cupy-cuda"
    if opencv_cuda_available():
        return "opencv-cuda"
    if opencv_opencl_available():
        return "opencv-opencl"
    if opencv_available():
        return "opencv-cpu"
    return "numpy-cpu"


def gray_and_channel_range(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """核心像素预处理：返回灰度图和 RGB 通道差。

    通道差用于区分“接近白/灰的扫描底色”和“有颜色/纹理的照片内容”。
    CuPy 路径能把灰度和通道差都放到 CUDA 上；OpenCV CUDA 路径目前只稳定加速灰度转换，
    通道差仍由 NumPy 计算，避免依赖不同 OpenCV CUDA 构建里不一致的算子。
    """
    rgb_u8 = _rgb_u8(rgb)

    if cupy_cuda_available():
        try:
            import cupy as cp

            gpu_rgb = cp.asarray(rgb_u8)
            gray_gpu = 0.299 * gpu_rgb[:, :, 0] + 0.587 * gpu_rgb[:, :, 1] + 0.114 * gpu_rgb[:, :, 2]
            range_gpu = gpu_rgb.max(axis=2).astype(cp.int16) - gpu_rgb.min(axis=2).astype(cp.int16)
            gray = cp.asnumpy(gray_gpu).astype(np.float32)
            channel_range = cp.asnumpy(range_gpu)
            gpu_rgb = gray_gpu = range_gpu = None
            release_cupy_memory(cp)
            return gray, channel_range
        except Exception:
            release_cupy_memory()
            pass

    if opencv_cuda_available():
        try:
            import cv2

            gpu_mat = cv2.cuda_GpuMat()
            gpu_mat.upload(rgb_u8)
            gray = cv2.cuda.cvtColor(gpu_mat, cv2.COLOR_RGB2GRAY).download().astype(np.float32)
            channel_range = rgb_u8.max(axis=2).astype(np.int16) - rgb_u8.min(axis=2).astype(np.int16)
            return gray, channel_range
        except Exception:
            pass

    if opencv_opencl_available():
        try:
            import cv2

            umat = cv2.UMat(rgb_u8)
            gray = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY).get().astype(np.float32)
            channel_range = rgb_u8.max(axis=2).astype(np.int16) - rgb_u8.min(axis=2).astype(np.int16)
            return gray, channel_range
        except Exception:
            pass

    if opencv_available():
        try:
            import cv2

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


def _cupy_background_difference_mask(rgb_u8: np.ndarray, bg: np.ndarray, actual_mode: str) -> np.ndarray:
    """在显存中完成背景差异、灰度、通道差和前景掩膜计算，只把最终 mask 拉回内存。"""
    import cupy as cp

    gpu_rgb = gpu_bg = diff_gpu = gray_gpu = min_channel = max_channel = channel_range_gpu = None
    plain_background = mask_gpu = channel_diff = diff_sample = None
    gpu_rgb = cp.asarray(rgb_u8)
    gpu_bg = cp.asarray(bg, dtype=cp.float32)

    diff_gpu = gpu_rgb[:, :, 0].astype(cp.float32)
    diff_gpu -= gpu_bg[0]
    cp.square(diff_gpu, out=diff_gpu)
    for channel_index in (1, 2):
        channel_diff = gpu_rgb[:, :, channel_index].astype(cp.float32)
        channel_diff -= gpu_bg[channel_index]
        cp.square(channel_diff, out=channel_diff)
        diff_gpu += channel_diff
    cp.sqrt(diff_gpu, out=diff_gpu)

    gray_gpu = gpu_rgb[:, :, 0].astype(cp.float32)
    gray_gpu *= cp.float32(0.299)
    gray_gpu += gpu_rgb[:, :, 1].astype(cp.float32) * cp.float32(0.587)
    gray_gpu += gpu_rgb[:, :, 2].astype(cp.float32) * cp.float32(0.114)

    min_channel = cp.minimum(cp.minimum(gpu_rgb[:, :, 0], gpu_rgb[:, :, 1]), gpu_rgb[:, :, 2])
    max_channel = cp.maximum(cp.maximum(gpu_rgb[:, :, 0], gpu_rgb[:, :, 1]), gpu_rgb[:, :, 2])
    channel_range_gpu = max_channel - min_channel

    if actual_mode == "black":
        threshold = cp.float32(34.0)
        plain_background = gray_gpu < cp.float32(65.0)
    elif actual_mode == "white":
        threshold = cp.float32(28.0)
        plain_background = (gray_gpu > cp.float32(210.0)) & (channel_range_gpu < 60)
    elif actual_mode == "gray":
        threshold = cp.float32(24.0)
        plain_background = (diff_gpu < cp.float32(24.0)) & (channel_range_gpu < 65)
    else:
        # 全量 cp.percentile 会在 GPU 上产生较大的排序/归约临时缓存。
        # 背景阈值只需要稳定估计，抽样回 CPU 计算可以显著降低检测阶段显存峰值。
        stride = max(1, int(max(diff_gpu.shape) // 900))
        diff_sample = cp.asnumpy(diff_gpu[::stride, ::stride])
        threshold = cp.float32(max(26.0, float(np.percentile(diff_sample, 55))))
        plain_background = diff_gpu < threshold * cp.float32(0.75)

    mask_gpu = (diff_gpu > threshold) & ~plain_background
    result = cp.asnumpy(mask_gpu).astype(bool, copy=False)
    gpu_rgb = gpu_bg = diff_gpu = gray_gpu = min_channel = max_channel = channel_range_gpu = None
    plain_background = mask_gpu = channel_diff = diff_sample = None
    release_cupy_memory(cp)
    return result


def gray_and_blur(rgb: np.ndarray, kernel_size: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """返回灰度图和高斯模糊图，优先使用 CUDA/OpenCL 可用路径。"""
    rgb_u8 = _rgb_u8(rgb)
    kernel_size = max(3, int(kernel_size))
    if kernel_size % 2 == 0:
        kernel_size += 1

    if opencv_cuda_available():
        try:
            import cv2

            gpu_rgb = cv2.cuda_GpuMat()
            gpu_rgb.upload(rgb_u8)
            gpu_gray = cv2.cuda.cvtColor(gpu_rgb, cv2.COLOR_RGB2GRAY)
            blur_filter = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_8UC1, (kernel_size, kernel_size), 0)
            gpu_blur = blur_filter.apply(gpu_gray)
            return gpu_gray.download(), gpu_blur.download()
        except Exception:
            pass

    if opencv_opencl_available():
        try:
            import cv2

            umat = cv2.UMat(rgb_u8)
            gray_u = cv2.cvtColor(umat, cv2.COLOR_RGB2GRAY)
            blur_u = cv2.GaussianBlur(gray_u, (kernel_size, kernel_size), 0)
            return gray_u.get(), blur_u.get()
        except Exception:
            pass

    if opencv_available():
        try:
            import cv2

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
    """Canny 边缘检测；CUDA/OpenCL 可用时优先走硬件路径，失败自动回退。"""
    blur_u8 = np.ascontiguousarray(blur, dtype=np.uint8)

    if opencv_cuda_available():
        try:
            import cv2

            gpu_blur = cv2.cuda_GpuMat()
            gpu_blur.upload(blur_u8)
            detector = cv2.cuda.createCannyEdgeDetector(float(lower), float(upper))
            return detector.detect(gpu_blur).download()
        except Exception:
            pass

    if opencv_opencl_available():
        try:
            import cv2

            return cv2.Canny(cv2.UMat(blur_u8), lower, upper).get()
        except Exception:
            pass

    if opencv_available():
        try:
            import cv2

            return cv2.Canny(blur_u8, lower, upper)
        except Exception:
            pass

    return _numpy_edge_mask(blur_u8, lower, upper)


def close_and_dilate_edges(edges: np.ndarray, kernel: np.ndarray, close_iterations: int = 2, dilate_iterations: int = 1) -> np.ndarray:
    """连接断裂边缘；OpenCL/CUDA 只加速形态学阶段，轮廓提取仍由 CPU 完成。"""
    src = np.ascontiguousarray(edges, dtype=np.uint8)

    if opencv_cuda_available():
        try:
            import cv2

            gpu = cv2.cuda_GpuMat()
            gpu.upload(src)
            close_filter = cv2.cuda.createMorphologyFilter(cv2.MORPH_CLOSE, cv2.CV_8UC1, kernel)
            for _ in range(max(1, close_iterations)):
                gpu = close_filter.apply(gpu)
            if dilate_iterations:
                dilate_filter = cv2.cuda.createMorphologyFilter(cv2.MORPH_DILATE, cv2.CV_8UC1, kernel)
                for _ in range(max(1, dilate_iterations)):
                    gpu = dilate_filter.apply(gpu)
            return gpu.download()
        except Exception:
            pass

    if opencv_opencl_available():
        try:
            import cv2

            umat = cv2.UMat(src)
            closed = cv2.morphologyEx(umat, cv2.MORPH_CLOSE, kernel, iterations=close_iterations)
            if dilate_iterations:
                closed = cv2.dilate(closed, kernel, iterations=dilate_iterations)
            return closed.get()
        except Exception:
            pass

    if opencv_available():
        try:
            import cv2

            closed_edges = cv2.morphologyEx(src, cv2.MORPH_CLOSE, kernel, iterations=close_iterations)
            if dilate_iterations:
                closed_edges = cv2.dilate(closed_edges, kernel, iterations=dilate_iterations)
            return closed_edges
        except Exception:
            pass

    return _pil_close_and_dilate(src, kernel, close_iterations=close_iterations, dilate_iterations=dilate_iterations)


def close_u8(mask: np.ndarray, kernel: np.ndarray, iterations: int = 1) -> np.ndarray:
    """对 8-bit 掩膜做闭运算，用于边界候选合并。"""
    src = np.ascontiguousarray(mask, dtype=np.uint8)

    if opencv_opencl_available():
        try:
            import cv2

            return cv2.morphologyEx(cv2.UMat(src), cv2.MORPH_CLOSE, kernel, iterations=iterations).get()
        except Exception:
            pass

    if opencv_available():
        try:
            import cv2

            return cv2.morphologyEx(src, cv2.MORPH_CLOSE, kernel, iterations=iterations)
        except Exception:
            pass

    return _pil_close_and_dilate(src, kernel, close_iterations=iterations)


def background_difference_mask(rgb: np.ndarray, actual_mode: str) -> np.ndarray:
    """用外缘背景样本生成弱前景掩膜；CuPy 可用时把大数组差异计算放到 CUDA。"""
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

    if cupy_cuda_available():
        try:
            return _cupy_background_difference_mask(rgb_u8, bg, actual_mode)
        except Exception:
            release_cupy_memory()
            diff = np.sqrt(np.sum((rgb_u8.astype(np.float32) - bg.reshape(1, 1, 3)) ** 2, axis=2))
    else:
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
    """对二值内容掩膜做闭运算，连接照片内部断裂纹理并填补小空洞。

    原代码使用 PIL MaxFilter + MinFilter。这里优先用 OpenCV 的 C/C++ 实现，通常更快；
    OpenCL 可用时使用 UMat，让 OpenCV 自行决定是否走硬件加速。
    """
    close_size = max(1, int(close_size))
    if close_size <= 1:
        return mask.astype(bool)

    src = (mask.astype(np.uint8) * 255)
    if opencv_available():
        try:
            import cv2

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
            closed = close_u8(src, kernel)
            return closed > 0
        except Exception:
            pass

    from PIL import Image, ImageFilter

    image = Image.fromarray(src, "L")
    return np.asarray(image.filter(ImageFilter.MaxFilter(close_size)).filter(ImageFilter.MinFilter(close_size))) > 0


def detect_runtime_environment(probe_accelerators: bool = True) -> dict[str, Any]:
    """检测运行环境，只有图像处理代码能实际调用成功时才标记加速后端可用。"""
    info: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_name": "",
        "cpu_count": os.cpu_count() or 1,
        "gpu_name": "",
        "gpu_backend": "none",
        "gpu_available": False,
        "cuda_available": False,
        "cupy_cuda_available": False,
        "opencv_cuda_available": False,
        "opencv_opencl_available": False,
        "opencv_available": False,
        "compute_backend": "numpy-cpu",
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

    if not probe_accelerators:
        cupy_installed = find_spec("cupy") is not None
        info["gpu_available"] = bool(info["gpu_name"])
        info["gpu_backend"] = "pending"
        info["compute_backend"] = "pending-cuda" if info["gpu_name"] and cupy_installed else "pending-cpu"
        info["acceleration_note"] = "启动阶段只做轻量检测；首次执行照片检测时再初始化 CuPy/CUDA，避免空闲时提前占用显存。"
        return info

    info["cupy_cuda_available"] = cupy_cuda_available()
    info["opencv_cuda_available"] = opencv_cuda_available()
    info["opencv_opencl_available"] = opencv_opencl_available()
    info["opencv_available"] = opencv_available()
    info["cuda_available"] = bool(info["cupy_cuda_available"] or info["opencv_cuda_available"])

    backend = get_compute_backend()
    info["compute_backend"] = backend
    info["runtime_errors"] = dict(_RUNTIME_ERRORS)
    if backend == "cupy-cuda":
        info["gpu_backend"] = backend
        info["gpu_available"] = True
        info["acceleration_note"] = "CuPy CUDA 加速已启用：灰度、通道差和背景差异计算会优先走 GPU；轮廓合并、裁切和 JPEG 编码仍由 CPU 完成。"
    elif backend == "opencv-cuda":
        info["gpu_backend"] = backend
        info["gpu_available"] = True
        info["acceleration_note"] = "OpenCV CUDA 加速已启用：灰度、边缘和部分形态学会优先走 GPU；轮廓合并、裁切和 JPEG 编码仍由 CPU 完成。"
    elif backend == "opencv-opencl":
        info["gpu_backend"] = backend
        info["gpu_available"] = bool(info["gpu_name"])
        info["acceleration_note"] = "CUDA 未启用，当前使用 OpenCV OpenCL/T-API 加速灰度、边缘和部分形态学；轮廓、裁切和 JPEG 保存仍主要由 CPU 执行。"
    elif backend == "opencv-cpu":
        info["acceleration_note"] = "CUDA/OpenCL 后端不可用，当前使用 OpenCV CPU 加速。"
    else:
        info["acceleration_note"] = "当前使用 NumPy CPU 后端。"

    return info
