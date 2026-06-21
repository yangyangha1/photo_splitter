from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
import html
import io
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image

from photo_splitter.config import (
    BACKGROUND_MODES,
    DEFAULT_BACKGROUND_MODE,
    DEFAULT_DETECTION_STRATEGY,
    DEFAULT_PRESET_KEY,
    DETECTION_STRATEGIES,
    JPEG_QUALITY,
    PROCESSING_PRESETS,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(tempfile.gettempdir()) / "photo_splitter_web"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}
SINGLE_CACHE: dict[str, dict[str, Any]] = {}
PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
WEBVIEW_WINDOW: Any | None = None
WEBVIEW_WINDOW_MAXIMIZED = False
HTTP_SERVER: Any | None = None
HTTP_SERVER_THREAD: threading.Thread | None = None
MAIN_WINDOW_WIDTH = 1560
MAIN_WINDOW_HEIGHT = 1020
SPLASH_WINDOW_WIDTH = 560
SPLASH_WINDOW_HEIGHT = 320
ORIENTATION_DETECTOR_LOCK = threading.Lock()


def resource_path(relative_path: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
    return bundle_root / relative_path


WEB_STATIC = resource_path("photo_splitter/web_ui/dist")
app = Flask(__name__, static_folder=str(WEB_STATIC), static_url_path="")

STARTUP_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>照片分割器正在加载</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
      background: #0b1018;
      color: #e7edf5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
      background:
        radial-gradient(circle at 28% 24%, rgba(61, 155, 255, 0.18), transparent 30%),
        linear-gradient(135deg, #0b1018, #0d1118 46%, #070b11);
    }
    .shell {
      width: 100%;
      height: 100%;
      display: grid;
      place-items: center;
      -webkit-app-region: drag;
    }
    .panel {
      width: min(500px, calc(100vw - 48px));
      padding: 28px 30px;
      border: 1px solid rgba(255, 255, 255, 0.14);
      border-radius: 28px;
      background: linear-gradient(180deg, rgba(255,255,255,0.13), rgba(255,255,255,0.05)), rgba(18, 24, 34, 0.72);
      box-shadow: 0 28px 80px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.2);
      backdrop-filter: blur(24px) saturate(1.35);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 18px;
      margin-bottom: 24px;
    }
    .brand-icon {
      width: 72px;
      height: 72px;
      flex: 0 0 72px;
      display: block;
    }
    .brand-icon svg {
      display: block;
      width: 72px;
      height: 72px;
    }
    .brand-icon img {
      display: block;
      width: 72px;
      height: 72px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }
    p {
      margin: 8px 0 0;
      color: #9da8b7;
      font-size: 14px;
      line-height: 1.7;
    }
    .track {
      height: 10px;
      margin-top: 26px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.12);
      box-shadow: inset 0 1px 2px rgba(0,0,0,0.35);
    }
    .track i {
      display: block;
      width: 38%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #56b6ff, #3d9bff, #8ad8ff);
      box-shadow: 0 0 20px rgba(61, 155, 255, 0.58);
      animation: flow 1.2s ease-in-out infinite;
    }
    @keyframes flow {
      from { transform: translateX(-120%); }
      to { transform: translateX(280%); }
    }
  </style>
</head>
<body>
  <main class="shell window-drag">
    <section class="panel">
      <div class="brand">
        <div class="brand-icon" aria-hidden="true">__ICON_MARKUP__</div>
        <div>
          <h1>照片分割器</h1>
          <p>正在加载本地处理服务和界面资源...</p>
        </div>
      </div>
      <div class="track"><i></i></div>
      <p>首次打开可能需要等待 EXE 解压和系统安全扫描。</p>
    </section>
  </main>
</body>
</html>
"""


def startup_html() -> str:
    """生成无需依赖本地 HTTP 服务的启动页，保证主界面加载前窗口先出现。"""
    try:
        icon_markup = resource_path("photo_splitter/assets/photo_splitter_icon.svg").read_text(encoding="utf-8")
    except Exception:
        try:
            icon_bytes = resource_path("photo_splitter/assets/photo_splitter_icon_preview.png").read_bytes()
            icon_src = "data:image/png;base64," + base64.b64encode(icon_bytes).decode("ascii")
            icon_markup = f'<img src="{icon_src}" alt="">'
        except Exception:
            icon_markup = ""
    return STARTUP_HTML.replace("__ICON_MARKUP__", icon_markup)


def startup_error_html(message: str) -> str:
    """启动失败时仍在程序窗口内展示错误，避免用户只看到空白窗口。"""
    safe_message = html.escape(message)
    return startup_html().replace(
        "正在加载本地处理服务和界面资源...",
        f"加载失败：{safe_message}",
    ).replace(
        "首次打开可能需要等待 EXE 解压和系统安全扫描。",
        "请关闭窗口后重新打开；如果持续失败，请把 gui_startup.log 发给 CODEX 检查。",
    )


def preset_payload() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "name": preset.name,
            "description": preset.description,
            "dark_threshold": preset.dark_threshold,
            "min_area_ratio": preset.min_area_ratio,
            "white_threshold": preset.white_threshold,
            "background_mode": preset.background_mode,
            "skew_gain_percent": preset.skew_gain_percent,
            "detection_strategy": preset.detection_strategy,
            "split_strategy": preset.split_strategy,
        }
        for key, preset in PROCESSING_PRESETS.items()
    ]


def options_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把前端参数转换成算法需要的强类型配置，并兜底到当前预设。"""
    preset_key = str(payload.get("preset") or DEFAULT_PRESET_KEY)
    preset = PROCESSING_PRESETS.get(preset_key, PROCESSING_PRESETS[DEFAULT_PRESET_KEY])
    background_mode = str(payload.get("background_mode") or preset.background_mode or DEFAULT_BACKGROUND_MODE)
    if background_mode not in BACKGROUND_MODES:
        background_mode = DEFAULT_BACKGROUND_MODE
    detection_strategy = str(
        payload.get("detection_strategy")
        or payload.get("split_strategy")
        or preset.detection_strategy
        or DEFAULT_DETECTION_STRATEGY
    )
    if detection_strategy not in DETECTION_STRATEGIES:
        detection_strategy = DEFAULT_DETECTION_STRATEGY
    return {
        "preset": preset.key,
        "preset_name": preset.name,
        "dark_threshold": int(payload.get("dark_threshold", preset.dark_threshold)),
        "min_area_ratio": float(payload.get("min_area_ratio", preset.min_area_ratio)),
        "white_threshold": int(payload.get("white_threshold", preset.white_threshold)),
        "background_mode": background_mode,
        "detection_strategy": detection_strategy,
        "split_strategy": detection_strategy,
        "skew_min_score_gain": 1.0 + int(payload.get("skew_gain_percent", preset.skew_gain_percent)) / 100,
        "skew_gain_percent": int(payload.get("skew_gain_percent", preset.skew_gain_percent)),
        "auto_face_rotate": bool(payload.get("auto_face_rotate", True)),
        "save_split_preview": bool(payload.get("save_split_preview", False)),
    }


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


ROTATION_MARKERS = {0, 90, 180, 270}
ORIENTATION_CONFIDENCE_THRESHOLD = 0.30


def normalize_rotation_marker(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        marker = int(value)
    except (TypeError, ValueError):
        return None
    return marker if marker in ROTATION_MARKERS else None


def box_coords(box: Any) -> tuple[int, int, int, int]:
    values = list(box or [])
    if len(values) < 4:
        raise ValueError("检测框坐标不完整。")
    return tuple(int(round(float(values[index]))) for index in range(4))


def box_payload(box: Any) -> list[int | float | str | bool | None]:
    values = list(box or [])
    x1, y1, x2, y2 = box_coords(values)
    marker = normalize_rotation_marker(values[4] if len(values) > 4 else None)
    confidence = values[5] if len(values) > 5 and values[5] is not None else None
    method = str(values[6]) if len(values) > 6 and values[6] is not None else None
    auto_checked = bool(values[7]) if len(values) > 7 else False
    manual_changed = bool(values[8]) if len(values) > 8 else False
    return [x1, y1, x2, y2, marker, confidence, method, auto_checked, manual_changed]


def marker_to_export_rotation(rotation_marker: int | None) -> int:
    marker = normalize_rotation_marker(rotation_marker)
    if marker is None:
        return 0
    return {0: 0, 90: 270, 180: 180, 270: 90}[marker]


def export_rotation_to_marker(export_rotation: int) -> int:
    return {0: 0, 90: 270, 180: 180, 270: 90}[int(export_rotation) % 360]


def rotate_pil_by_export_angle(image: Image.Image, angle: int) -> Image.Image:
    angle = int(angle) % 360
    if angle == 0:
        return image
    if angle == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if angle == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if angle == 270:
        return image.transpose(Image.Transpose.ROTATE_90)
    raise ValueError("旋转角度必须是 0/90/180/270。")


def apply_rotation_marker(image: Image.Image, rotation_marker: int | None) -> Image.Image:
    return rotate_pil_by_export_angle(image, marker_to_export_rotation(rotation_marker))


def apply_orientation_detection_after_boxes(
    image: Image.Image,
    boxes: list[Any],
    enabled: bool,
    confidence_threshold: float = ORIENTATION_CONFIDENCE_THRESHOLD,
) -> list[list[int | float | str | bool | None]]:
    """在分割框生成后逐框裁切判断方向，只记录 marker，不修改预览图。"""
    result_boxes: list[list[int | float | str | bool | None]] = []
    predictor = None
    if enabled:
        from photo_splitter.postprocess import predict_face_orientation

        predictor = predict_face_orientation
    for box in boxes:
        x1, y1, x2, y2 = box_coords(box)
        marker: int | None = None
        confidence: float | None = None
        method: str | None = None
        face_count = 0
        auto_checked = False
        if predictor is not None:
            clamped = (max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))
            if clamped[2] - clamped[0] >= 5 and clamped[3] - clamped[1] >= 5:
                crop = image.crop(clamped)
                try:
                    with ORIENTATION_DETECTOR_LOCK:
                        prediction = predictor(crop)
                    confidence = float(prediction.confidence)
                    method = str(prediction.method)
                    face_count = int(prediction.face_count)
                    auto_checked = True
                    if face_count > 0 and confidence >= confidence_threshold and int(prediction.angle) % 360 != 0:
                        marker = export_rotation_to_marker((360 - int(prediction.angle)) % 360)
                finally:
                    crop.close()
        result_boxes.append([x1, y1, x2, y2, marker, confidence, method, auto_checked, False])
    return result_boxes


def thumb_url_for(path: Path, page_stem: str | None = None, max_side: int = 360) -> str:
    """生成本地缩略图接口地址，路径只在本机后端解析，不暴露给外部服务。"""
    try:
        stat = path.stat()
        version = f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        version = str(time.time_ns())
    url = f"/api/source/thumb?path={quote(str(path), safe='')}&v={version}&size={max(80, min(720, int(max_side)))}"
    if page_stem:
        url += f"&page_stem={quote(str(page_stem), safe='')}"
    return url


def scan_item_payload(path: Path, input_root: Path, status: str = "待处理") -> dict[str, Any]:
    """批量队列和预览共用的文件信息结构。"""
    try:
        rel = path.resolve().relative_to(input_root)
    except ValueError:
        rel = Path(path.name)
    return {
        "path": str(path),
        "name": path.name,
        "relative": str(rel),
        "status": status,
        "saved": 0,
        "thumb_url": thumb_url_for(path),
    }


def load_source_page_image(source: Path, page_stem: str | None = None) -> Any:
    """按需读取源图页。批量结果不常驻图片，进入单图编辑或导出时再临时加载。"""
    from photo_splitter.io_utils import iter_source_images

    pages = iter_source_images(source)
    if not pages:
        raise ValueError("源图没有可读取的页面。")
    selected_index = 0
    if page_stem:
        for index, (stem, _image) in enumerate(pages):
            if stem == page_stem:
                selected_index = index
                break
    for index, (_stem, image) in enumerate(pages):
        if index != selected_index:
            image.close()
    return pages[selected_index][1]


def cache_processed_image(source: Path, page_stem: str, image: Any, boxes: list[Any], options: dict[str, Any]) -> str:
    """只缓存检测结果元数据，不缓存大图；图片在点击预览或导出时按需重读。"""
    image_id = uuid.uuid4().hex
    SINGLE_CACHE[image_id] = {
        "source": str(source),
        "page_stem": page_stem,
        "width": image.width,
        "height": image.height,
        "boxes": [box_payload(box) for box in boxes],
        "options": options,
    }
    return image_id


def image_for_cached_item(item: dict[str, Any]) -> tuple[Any, bool]:
    """返回缓存项对应图片；第二个返回值表示调用方是否需要负责 close。"""
    if item.get("image") is not None:
        return item["image"], False
    source = Path(str(item["source"]))
    return load_source_page_image(source, str(item.get("page_stem") or source.stem)), True


def detected_item_payload(
    source: Path,
    input_root: Path,
    page_stem: str,
    page_count: int,
    image_id: str,
    image: Any,
    boxes: list[Any],
    status: str = "已检测",
) -> dict[str, Any]:
    """生成批量检测预览数据；只保存检测框和尺寸，图片点开时再按需读取。"""
    base = scan_item_payload(source, input_root, status=status)
    if page_count > 1:
        base["name"] = f"{source.name} / {page_stem}"
        base["relative"] = f"{base['relative']} / {page_stem}"
    base.update(
        {
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}?size=1600",
            "full_image_url": f"/api/single/image/{image_id}?full=1",
            "thumb_url": thumb_url_for(source, page_stem, max_side=260),
            "page_stem": page_stem,
            "width": image.width,
            "height": image.height,
            "boxes": [box_payload(box) for box in boxes],
            "box_count": len(boxes),
            "saved": len(boxes),
            "edited": False,
        }
    )
    return base


def trim_preview_cache(limit: int = 60) -> None:
    """限制单图预览缓存数量，避免长时间使用后把大图一直留在内存里。"""
    overflow = len(PREVIEW_CACHE) - limit
    if overflow <= 0:
        return
    for key in list(PREVIEW_CACHE.keys())[:overflow]:
        PREVIEW_CACHE.pop(key, None)


def release_accelerator_memory() -> None:
    """任务结束后释放可回收的 GPU 缓存；界面缩略图和检测框数据不依赖这部分显存。"""
    try:
        from photo_splitter.runtime_backend import release_cupy_memory

        release_cupy_memory()
    except Exception:
        pass


def worker_override_from_env(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def batch_worker_count(kind: str, total: int) -> tuple[int, str]:
    """按当前计算后端选择批处理并发度，避免 GPU 后端被过度并发拖慢。"""
    if total <= 1:
        return 1, "single-item"
    env_name = "PHOTO_SPLITTER_DETECT_WORKERS" if kind == "detect" else "PHOTO_SPLITTER_EXPORT_WORKERS"
    override = worker_override_from_env(env_name)
    if override is not None:
        return max(1, min(total, override)), f"{env_name}={override}"

    cpu_count = max(1, os.cpu_count() or 1)
    try:
        from photo_splitter.runtime_backend import get_compute_backend

        backend = get_compute_backend()
    except Exception:
        backend = "unknown"

    if kind == "detect":
        if backend in {"cupy-cuda", "opencv-cuda"}:
            return 1, backend
        if backend == "opencv-opencl":
            return min(total, 2), backend
        if backend == "opencv-cpu":
            return min(total, max(2, min(4, cpu_count // 2 or 1))), backend
        return min(total, max(2, min(3, cpu_count // 2 or 1))), backend

    if backend in {"cupy-cuda", "opencv-cuda"}:
        return min(total, max(2, min(4, cpu_count // 2 or 1))), backend
    if backend == "opencv-opencl":
        return min(total, max(2, min(4, cpu_count // 2 or 1))), backend
    return min(total, max(2, min(6, cpu_count // 2 or 1))), backend


def clear_detection_state(keep_recent_jobs: int = 3) -> None:
    """开始新一轮检测前清理旧检测结果，避免确认页和历史任务越积越多。"""
    SINGLE_CACHE.clear()
    PREVIEW_CACHE.clear()
    if len(JOBS) > keep_recent_jobs:
        for key in list(JOBS.keys())[:-keep_recent_jobs]:
            JOBS.pop(key, None)
    gc.collect()
    release_accelerator_memory()


@app.get("/photo_splitter_icon_preview.png")
def app_icon_preview():
    return send_file(resource_path("photo_splitter/assets/photo_splitter_icon_preview.png"), mimetype="image/png")


@app.get("/favicon.ico")
def app_favicon():
    return send_file(resource_path("photo_splitter/assets/photo_splitter_icon.ico"), mimetype="image/x-icon")


@app.get("/")
def index():
    index_path = WEB_STATIC / "index.html"
    if not index_path.exists():
        return "Vue UI has not been built. Run: cd photo_splitter\\web_ui && npm install && npm run build", 500
    return send_from_directory(WEB_STATIC, "index.html")


@app.get("/assets/<path:path>")
def vue_assets(path: str):
    return send_from_directory(WEB_STATIC / "assets", path)


@app.get("/api/config")
def api_config():
    return jsonify(
        {
            "ok": True,
            "presets": preset_payload(),
            "background_modes": [{"key": key, "label": label} for key, label in BACKGROUND_MODES.items()],
            "default_preset": DEFAULT_PRESET_KEY,
            "jpeg_quality": JPEG_QUALITY,
        }
    )


@app.get("/api/runtime")
def api_runtime():
    from photo_splitter.runtime_backend import detect_runtime_environment

    probe = str(request.args.get("probe") or "1").strip().lower() not in {"0", "false", "no"}
    return jsonify({"ok": True, "runtime": detect_runtime_environment(probe_accelerators=probe)})


@app.post("/api/open-path")
def api_open_path():
    from photo_splitter.io_utils import open_in_file_manager

    payload = request.get_json(silent=True) or {}
    path = Path(str(payload.get("path") or ""))
    if not path.exists():
        return json_error("路径不存在。")
    open_in_file_manager(path)
    return jsonify({"ok": True})


@app.get("/api/source/thumb")
def api_source_thumb():
    path = Path(str(request.args.get("path") or "")).expanduser()
    if not path.exists():
        return "not found", 404
    page_stem = str(request.args.get("page_stem") or "")
    try:
        max_side = max(80, min(720, int(request.args.get("size") or 360)))
    except (TypeError, ValueError):
        max_side = 360
    image = load_source_page_image(path, page_stem)
    response = image_to_response(image, max_side=max_side)
    image.close()
    gc.collect()
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.post("/api/batch/scan")
def api_batch_scan():
    from photo_splitter.io_utils import iter_images

    payload = request.get_json(silent=True) or {}
    input_value = str(payload.get("input_dir") or "").strip()
    if not input_value:
        return json_error("输入目录为空。")
    input_path = Path(input_value).expanduser()
    output_path = Path(str(payload.get("output_dir") or input_path / "split_result")).expanduser()
    if not input_path.exists():
        return json_error("输入目录不存在。")
    images = iter_images(input_path, output_path)
    input_root = input_path.resolve() if input_path.is_dir() else input_path.resolve().parent
    items = [scan_item_payload(path, input_root) for path in images]
    return jsonify({"ok": True, "items": items, "count": len(items), "output_dir": str(output_path)})


@app.post("/api/batch/detect")
@app.post("/api/batch/start")
def api_batch_detect():
    from photo_splitter.io_utils import iter_images

    payload = request.get_json(silent=True) or {}
    input_value = str(payload.get("input_dir") or "").strip()
    if not input_value:
        return json_error("输入目录为空。")
    input_path = Path(input_value).expanduser()
    output_path = Path(str(payload.get("output_dir") or input_path / "split_result")).expanduser()
    if not input_path.exists():
        return json_error("输入目录不存在。")
    images = [Path(item) for item in payload.get("images", []) if str(item).strip()]
    if not images:
        images = iter_images(input_path, output_path)
    if not images:
        return json_error("没有找到可处理的 JPG/PNG/TIFF 文件。")
    options = options_from_payload(payload.get("options") or {})
    preserve_detection_state = bool(payload.get("preserve_detection_state"))
    waiting_status = "等待重新检测" if preserve_detection_state else "待处理"
    running_status = "重新检测中" if preserve_detection_state else "处理中"
    done_status = "已重新检测" if preserve_detection_state else "已检测"
    action_label = "重新检测" if preserve_detection_state else "检测"
    if preserve_detection_state:
        PREVIEW_CACHE.clear()
        gc.collect()
        release_accelerator_memory()
    else:
        clear_detection_state()
    job_id = uuid.uuid4().hex
    input_root = input_path.resolve() if input_path.is_dir() else input_path.resolve().parent
    JOBS[job_id] = {
        "id": job_id,
        "kind": "detect",
        "status": "running",
        "total": len(images),
        "index": 0,
        "saved": 0,
        "failed": [],
        "output_dir": str(output_path),
        "items": [scan_item_payload(path, input_root, status=waiting_status) for path in images],
        "logs": [f"开始批量{action_label} {len(images)} 个文件。"],
        "started_at": time.time(),
    }

    def worker() -> None:
        from photo_splitter.detection import split_image
        from photo_splitter.io_utils import iter_source_images

        def detect_source(index: int, source: Path) -> dict[str, Any]:
            job = JOBS.get(job_id)
            if job:
                job["items"][index - 1]["status"] = running_status
            try:
                pages = iter_source_images(source)
                file_box_count = 0
                item_results: list[dict[str, Any]] = []
                for page_stem, image in pages:
                    processed, boxes, _angle = split_image(
                        image,
                        int(options["dark_threshold"]),
                        float(options["min_area_ratio"]),
                        background_mode=str(options["background_mode"]),
                        detection_strategy=str(options["detection_strategy"]),
                    )
                    marked_boxes = apply_orientation_detection_after_boxes(processed, boxes, bool(options["auto_face_rotate"]))
                    image_id = cache_processed_image(source, page_stem, processed, marked_boxes, options)
                    item_results.append(detected_item_payload(source, input_root, page_stem, len(pages), image_id, processed, marked_boxes, status=done_status))
                    file_box_count += len(marked_boxes)
                    if processed is not image:
                        processed.close()
                    image.close()
                pages.clear()
                gc.collect()
                return {"ok": True, "index": index, "source": source, "items": item_results, "box_count": file_box_count}
            except Exception as exc:
                return {"ok": False, "index": index, "source": source, "error": str(exc)}

        detected_by_index: dict[int, list[dict[str, Any]]] = {}
        workers, backend_note = batch_worker_count("detect", len(images))
        job = JOBS[job_id]
        job["logs"].append(f"{action_label}并发：{workers} 个 worker（后端：{backend_note}）。")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            for index, source in enumerate(images, start=1):
                job["items"][index - 1]["status"] = waiting_status
                job["logs"].append(f"排队{action_label}：{source.name}")
                futures.append(executor.submit(detect_source, index, source))
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                result_index = int(result["index"])
                source = Path(result["source"])
                job = JOBS[job_id]
                job["index"] = completed
                if result["ok"]:
                    box_count = int(result["box_count"])
                    detected_by_index[result_index] = list(result["items"])
                    job["saved"] += box_count
                    job["items"][result_index - 1]["status"] = done_status
                    job["items"][result_index - 1]["saved"] = box_count
                    job["logs"].append(f"{action_label}完成：{source.name}，检测框 {box_count} 个。")
                else:
                    error = str(result["error"])
                    job["items"][result_index - 1]["status"] = "失败"
                    job["failed"].append({"source": str(source), "error": error})
                    job["logs"].append(f"失败：{source.name}，{error}")

        detected_items: list[dict[str, Any]] = []
        for index in range(1, len(images) + 1):
            detected_items.extend(detected_by_index.get(index, []))
        job = JOBS[job_id]
        job["status"] = "done"
        job["items"] = detected_items or job["items"]
        job["logs"].append(f"批量检测完成，检测框 {job['saved']} 个。")
        release_accelerator_memory()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.post("/api/batch/export")
def api_batch_export():
    payload = request.get_json(silent=True) or {}
    input_value = str(payload.get("input_dir") or "").strip()
    output_value = str(payload.get("output_dir") or "").strip()
    if not input_value:
        return json_error("输入目录为空。")
    if not output_value:
        return json_error("输出目录为空。")
    input_path = Path(input_value).expanduser()
    output_path = Path(output_value).expanduser()
    if not input_path.exists():
        return json_error("输入目录不存在。")
    items = [item for item in payload.get("items", []) if str(item.get("image_id", "")).strip()]
    if not items:
        return json_error("没有可导出的检测结果，请先批量检测。")
    options = options_from_payload(payload.get("options") or {})
    save_split_preview = bool(options.get("save_split_preview", False))
    input_root = input_path.resolve() if input_path.is_dir() else input_path.resolve().parent
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "kind": "export",
        "status": "running",
        "total": len(items),
        "index": 0,
        "saved": 0,
        "failed": [],
        "output_dir": str(output_path),
        "items": [{**item, "status": "待导出"} for item in items],
        "logs": [f"开始确认导出 {len(items)} 个检测结果。"],
        "started_at": time.time(),
    }

    def worker() -> None:
        from PIL import ImageDraw

        from photo_splitter.io_utils import safe_name, target_dir_for_source, unique_output_path
        from photo_splitter.postprocess import refine_output_photo
        from photo_splitter.visualization import draw_preview_box

        output_path.mkdir(parents=True, exist_ok=True)

        def export_item(index: int, item_payload: dict[str, Any]) -> dict[str, Any]:
            job = JOBS.get(job_id)
            if job:
                job["items"][index - 1]["status"] = "处理中"
            image_id = str(item_payload.get("image_id") or "")
            cache_item = SINGLE_CACHE.get(image_id)
            if not cache_item:
                error = "检测缓存不存在，请重新批量检测。"
                return {"ok": False, "index": index, "name": item_payload.get("name") or image_id, "source": str(item_payload.get("path") or ""), "error": error}
            image = None
            should_close_image = False
            try:
                image, should_close_image = image_for_cached_item(cache_item)
                source = Path(str(cache_item["source"]))
                page_stem = str(cache_item.get("page_stem") or source.stem)
                item_options = options_from_payload(item_payload.get("options") or cache_item.get("options") or options)
                boxes = [box_payload(box) for box in item_payload.get("boxes", [])]
                target_dir = target_dir_for_source(source, input_root, output_path, include_root_name=True)
                target_dir.mkdir(parents=True, exist_ok=True)
                image_name = safe_name(page_stem)
                saved: list[Path] = []
                for box_index, box in enumerate(boxes, start=1):
                    x1, y1, x2, y2, rotation_marker = box[:5]
                    clamped = (max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))
                    if clamped[2] - clamped[0] < 5 or clamped[3] - clamped[1] < 5:
                        continue
                    raw_crop = image.crop(clamped)
                    oriented_crop = apply_rotation_marker(raw_crop, rotation_marker)
                    crop = refine_output_photo(
                        oriented_crop,
                        white_threshold=int(item_options["white_threshold"]),
                        skew_min_score_gain=float(item_options["skew_min_score_gain"]),
                        auto_face_rotate=False,
                        background_mode=str(item_options["background_mode"]),
                    )
                    if crop is not oriented_crop:
                        oriented_crop.close()
                    if oriented_crop is not raw_crop:
                        raw_crop.close()
                    target = unique_output_path(target_dir / f"{image_name}_{box_index:03d}.jpg", overwrite=False)
                    crop.save(target, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
                    crop.close()
                    saved.append(target)
                if save_split_preview:
                    preview = image.copy()
                    draw = ImageDraw.Draw(preview)
                    for box_index, box in enumerate(boxes, start=1):
                        draw_preview_box(draw, tuple(box[:4]), f"{box_index:03d}", image.width)
                    preview_path = unique_output_path(target_dir / f"分割预览_{image_name}.jpg", overwrite=False)
                    preview.save(preview_path, "JPEG", quality=92)
                    preview.close()
                return {
                    "ok": True,
                    "index": index,
                    "name": item_payload.get("name") or source.name,
                    "source": str(source),
                    "saved": len(saved),
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "index": index,
                    "name": item_payload.get("name") or image_id,
                    "source": str(item_payload.get("path") or ""),
                    "error": str(exc),
                }
            finally:
                if should_close_image and image is not None:
                    image.close()
                gc.collect()

        workers, backend_note = batch_worker_count("export", len(items))
        job = JOBS[job_id]
        job["logs"].append(f"导出并发：{workers} 个 worker（后端：{backend_note}）。")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            for index, item_payload in enumerate(items, start=1):
                job["items"][index - 1]["status"] = "待导出"
                futures.append(executor.submit(export_item, index, item_payload))
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                result_index = int(result["index"])
                job = JOBS[job_id]
                job["index"] = completed
                if result["ok"]:
                    saved_count = int(result["saved"])
                    job["saved"] += saved_count
                    job["items"][result_index - 1]["status"] = "已完成"
                    job["items"][result_index - 1]["saved"] = saved_count
                    job["logs"].append(f"导出完成：{result['name']}，输出 {saved_count} 张。")
                else:
                    error = str(result["error"])
                    job["items"][result_index - 1]["status"] = "失败"
                    job["failed"].append({"source": str(result["source"]), "error": error})
                    job["logs"].append(f"失败：{result['name']}，{error}")
        job = JOBS[job_id]
        job["status"] = "done"
        job["logs"].append(f"批量导出完成，输出 {job['saved']} 张。")
        release_accelerator_memory()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.post("/api/batch/item")
def api_batch_item_update():
    payload = request.get_json(silent=True) or {}
    image_id = str(payload.get("image_id") or "")
    item = SINGLE_CACHE.get(image_id)
    if not item:
        return json_error("检测缓存不存在，请重新检测。")
    boxes = [box_payload(box) for box in payload.get("boxes", [])]
    options = options_from_payload(payload.get("options") or item.get("options") or {})
    item["boxes"] = boxes
    item["options"] = options
    return jsonify(
        {
            "ok": True,
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}?size=1600",
            "full_image_url": f"/api/single/image/{image_id}?full=1",
            "width": int(item.get("width") or 1),
            "height": int(item.get("height") or 1),
            "boxes": [box_payload(box) for box in boxes],
            "box_count": len(boxes),
        }
    )


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return json_error("任务不存在。", 404)
    return jsonify({"ok": True, "job": job})


def image_to_response(image: Any, max_side: int | None = None, quality: int = 92):
    response_image = image.convert("RGB")
    try:
        if max_side and max(response_image.size) > max_side:
            response_image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        response_image.save(buf, format="JPEG", quality=quality, optimize=True)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")
    finally:
        response_image.close()


@app.post("/api/single/preview")
def api_single_preview():
    from photo_splitter.io_utils import iter_source_images

    payload = request.get_json(silent=True) or {}
    source = Path(str(payload.get("source") or "")).expanduser()
    if not source.exists():
        return json_error("单张照片不存在。")
    pages = iter_source_images(source)
    if not pages:
        return json_error("单张照片没有可读取的页面。")
    page_stem, image = pages[0]
    for _unused_stem, unused_image in pages[1:]:
        unused_image.close()
    preview_id = uuid.uuid4().hex
    PREVIEW_CACHE[preview_id] = {"source": str(source), "page_stem": page_stem, "width": image.width, "height": image.height}
    width, height = image.width, image.height
    image.close()
    gc.collect()
    trim_preview_cache()
    return jsonify(
        {
            "ok": True,
            "image_url": f"/api/source/preview/{preview_id}?size=1600",
            "full_image_url": f"/api/source/preview/{preview_id}?full=1",
            "name": source.name,
            "width": width,
            "height": height,
        }
    )


@app.get("/api/source/preview/<preview_id>")
def api_source_preview(preview_id: str):
    item = PREVIEW_CACHE.get(preview_id)
    if not item:
        return "not found", 404
    image = load_source_page_image(Path(str(item["source"])), str(item.get("page_stem") or ""))
    try:
        full = str(request.args.get("full") or "0").lower() in {"1", "true", "yes"}
        if full:
            max_side = None
        else:
            try:
                max_side = max(720, min(2200, int(request.args.get("size") or 1600)))
            except (TypeError, ValueError):
                max_side = 1600
        response = image_to_response(image, max_side=max_side, quality=88 if not full else 92)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    finally:
        image.close()
        gc.collect()


@app.post("/api/single/detect")
def api_single_detect():
    from photo_splitter.detection import split_image
    from photo_splitter.io_utils import iter_source_images

    payload = request.get_json(silent=True) or {}
    source = Path(str(payload.get("source") or "")).expanduser()
    if not source.exists():
        return json_error("单张照片不存在。")
    options = options_from_payload(payload.get("options") or {})
    preserve_image_id = str(payload.get("preserve_image_id") or "").strip()
    # 从批量预览进入单图修正后，重新检测当前图时必须保留整批检测缓存。
    # 否则其它批量项的 image_id 会失效，后续确认导出会提示缓存不存在。
    preserve_detection_state = bool(payload.get("preserve_detection_state")) or preserve_image_id in SINGLE_CACHE
    if preserve_detection_state:
        PREVIEW_CACHE.clear()
        gc.collect()
        release_accelerator_memory()
    else:
        clear_detection_state()
    pages = iter_source_images(source)
    if not pages:
        return json_error("单张照片没有可读取的页面。")
    page_stem, image = pages[0]
    for _unused_stem, unused_image in pages[1:]:
        unused_image.close()
    # 单图检测使用与批量处理相同的检测入口，保证 UI 预览和最终批量算法一致。
    processed, boxes, _angle = split_image(
        image,
        int(options["dark_threshold"]),
        float(options["min_area_ratio"]),
        background_mode=str(options["background_mode"]),
        detection_strategy=str(options["detection_strategy"]),
    )
    marked_boxes = apply_orientation_detection_after_boxes(processed, boxes, bool(options["auto_face_rotate"]))
    image_id = cache_processed_image(source, page_stem, processed, marked_boxes, options)
    width, height = processed.width, processed.height
    if processed is not image:
        processed.close()
    image.close()
    gc.collect()
    release_accelerator_memory()
    return jsonify(
        {
            "ok": True,
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}?size=1600",
            "full_image_url": f"/api/single/image/{image_id}?full=1",
            "width": width,
            "height": height,
            "boxes": [box_payload(box) for box in marked_boxes],
        }
    )


@app.get("/api/single/image/<image_id>")
def api_single_image(image_id: str):
    item = SINGLE_CACHE.get(image_id)
    if not item:
        return "not found", 404
    image, should_close_image = image_for_cached_item(item)
    try:
        full = str(request.args.get("full") or "0").lower() in {"1", "true", "yes"}
        if full:
            max_side = None
        else:
            try:
                max_side = max(720, min(2200, int(request.args.get("size") or 1600)))
            except (TypeError, ValueError):
                max_side = 1600

        response = image_to_response(image, max_side=max_side, quality=88 if not full else 92)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    finally:
        if should_close_image:
            image.close()
        gc.collect()


@app.post("/api/single/export")
def api_single_export():
    from PIL import ImageDraw

    from photo_splitter.io_utils import safe_name, unique_output_path
    from photo_splitter.postprocess import refine_output_photo
    from photo_splitter.visualization import draw_preview_box

    payload = request.get_json(silent=True) or {}
    image_id = str(payload.get("image_id") or "")
    item = SINGLE_CACHE.get(image_id)
    if not item:
        return json_error("单图缓存不存在，请重新检测。")
    output_value = str(payload.get("output_dir") or "").strip()
    if not output_value:
        return json_error("输出目录为空。")
    output_dir = Path(output_value).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    options = options_from_payload(payload.get("options") or item.get("options") or {})
    image, should_close_image = image_for_cached_item(item)
    try:
        source_name = safe_name(Path(str(item["source"])).stem or "single_photo")
        boxes = [box_payload(box) for box in payload.get("boxes", [])]
        saved: list[Path] = []
        for index, box in enumerate(boxes, start=1):
            x1, y1, x2, y2, rotation_marker = box[:5]
            clamped = (max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))
            if clamped[2] - clamped[0] < 5 or clamped[3] - clamped[1] < 5:
                continue
            raw_crop = image.crop(clamped)
            oriented_crop = apply_rotation_marker(raw_crop, rotation_marker)
            crop = refine_output_photo(
                oriented_crop,
                white_threshold=int(options["white_threshold"]),
                skew_min_score_gain=float(options["skew_min_score_gain"]),
                auto_face_rotate=False,
                background_mode=str(options["background_mode"]),
            )
            if crop is not oriented_crop:
                oriented_crop.close()
            if oriented_crop is not raw_crop:
                raw_crop.close()
            target = unique_output_path(output_dir / f"{source_name}_manual_{index:03d}.jpg", overwrite=False)
            crop.save(target, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
            crop.close()
            saved.append(target)
        preview_path: Path | None = None
        if bool(options.get("save_split_preview", False)):
            preview = image.copy()
            draw = ImageDraw.Draw(preview)
            for index, box in enumerate(boxes, start=1):
                draw_preview_box(draw, tuple(box[:4]), f"{index:03d}", image.width)
            preview_path = unique_output_path(output_dir / f"分割预览_{source_name}_manual.jpg", overwrite=False)
            preview.save(preview_path, "JPEG", quality=92)
            preview.close()
        return jsonify(
            {
                "ok": True,
                "saved": len(saved),
                "outputs": [str(path) for path in saved],
                "preview": str(preview_path) if preview_path else "",
                "output_dir": str(output_dir),
            }
        )
    finally:
        if should_close_image:
            image.close()
        gc.collect()
        release_accelerator_memory()


@app.get("/<path:path>")
def spa_fallback(path: str):
    target = WEB_STATIC / path
    if target.exists() and target.is_file():
        return send_from_directory(WEB_STATIC, path)
    return send_from_directory(WEB_STATIC, "index.html")


def set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YY.PhotoSplitter.VueDemo")
    except Exception:
        pass


def configure_webview_runtime() -> None:
    """限制 WebView2 自身占用 GPU，避免缩略图列表和玻璃效果被误认为 CUDA 计算负载。

    这里禁用的是 Edge WebView2 的界面合成 GPU 加速，不影响 CuPy/OpenCV 对 CUDA 的调用。
    """
    if sys.platform != "win32":
        return
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "").strip()
    flags = [
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-accelerated-2d-canvas",
    ]
    merged = existing.split() if existing else []
    for flag in flags:
        if flag not in merged:
            merged.append(flag)
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(merged)


def centered_window_position(width: int, height: int) -> tuple[int | None, int | None]:
    """计算主窗口初始居中位置；Windows 下按可用工作区居中，避开任务栏。"""
    if sys.platform != "win32":
        return None, None
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        work_area = RECT()
        spi_get_workarea = 0x0030
        if not ctypes.windll.user32.SystemParametersInfoW(spi_get_workarea, 0, ctypes.byref(work_area), 0):
            return None, None
        work_width = max(1, int(work_area.right - work_area.left))
        work_height = max(1, int(work_area.bottom - work_area.top))
        x = int(work_area.left + max(0, (work_width - width) // 2))
        y = int(work_area.top + max(0, (work_height - height) // 2))
        return x, y
    except Exception:
        return None, None


def find_available_port(start: int = 8765, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("没有可用的本地端口。")


def start_http_server(port: int) -> None:
    """在加载页已经显示后再启动 Flask 服务，缩短用户看到空白窗口的时间。"""
    global HTTP_SERVER, HTTP_SERVER_THREAD

    if HTTP_SERVER is not None:
        return

    from werkzeug.serving import make_server

    HTTP_SERVER = make_server("127.0.0.1", port, app, threaded=True)
    HTTP_SERVER_THREAD = threading.Thread(target=HTTP_SERVER.serve_forever, daemon=True)
    HTTP_SERVER_THREAD.start()


def wait_for_server(url: str, timeout: float = 45.0) -> None:
    """等待 Flask 本地服务可用。

    PyInstaller onefile 首次启动需要解压和导入依赖，低配机器或杀毒扫描时
    8 秒很容易误判失败，因此这里给桌面版保留更宽裕的启动窗口。
    """
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(f"{url}/api/config", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error:
        raise RuntimeError(f"本地服务启动超时：{last_error}") from last_error
    raise RuntimeError("本地服务启动超时。")


def load_application_when_ready(window: Any, url: str, port: int) -> None:
    """pywebview 启动后再启动 Flask，并从加载页切换到正式界面。"""
    try:
        # 给 Edge WebView2 一个绘制首帧的时间，避免服务很快启动时看不到加载页。
        time.sleep(0.2)
        start_http_server(port)
        wait_for_server(url)
        window.load_url(url)
    except Exception as exc:
        try:
            window.load_html(startup_error_html(str(exc)))
        except Exception:
            pass


def bind_webview_window(window: Any) -> None:
    """缓存 pywebview 窗口对象。

    不要把 window 挂到 js_api 实例上。pywebview 会反射 js_api 对象，
    复杂窗口对象会让 Edge WebView2 宿主卡住，表现为界面渲染但窗口无响应。
    """
    global WEBVIEW_WINDOW, WEBVIEW_WINDOW_MAXIMIZED

    WEBVIEW_WINDOW = window
    WEBVIEW_WINDOW_MAXIMIZED = False


class WindowControlsApi:
    """暴露给 Vue 的轻量窗口控制 API。"""

    def minimize(self) -> None:
        if WEBVIEW_WINDOW:
            WEBVIEW_WINDOW.minimize()

    def toggle_maximize(self) -> bool:
        global WEBVIEW_WINDOW_MAXIMIZED

        if not WEBVIEW_WINDOW:
            return False
        if WEBVIEW_WINDOW_MAXIMIZED:
            WEBVIEW_WINDOW.restore()
            WEBVIEW_WINDOW_MAXIMIZED = False
        else:
            WEBVIEW_WINDOW.maximize()
            WEBVIEW_WINDOW_MAXIMIZED = True
        return WEBVIEW_WINDOW_MAXIMIZED

    def bounds(self) -> dict[str, int | bool]:
        """返回当前窗口边界，供无边框窗口模拟 Windows 拖边缩放使用。"""
        if not WEBVIEW_WINDOW:
            return {"x": 0, "y": 0, "width": 1560, "height": 1020, "maximized": False}
        return {
            "x": int(WEBVIEW_WINDOW.x),
            "y": int(WEBVIEW_WINDOW.y),
            "width": int(WEBVIEW_WINDOW.width),
            "height": int(WEBVIEW_WINDOW.height),
            "maximized": WEBVIEW_WINDOW_MAXIMIZED,
        }

    def resize_window(self, payload: dict[str, Any]) -> dict[str, int | bool]:
        """按前端计算结果移动/缩放无边框窗口，保留最小尺寸限制。"""
        if not WEBVIEW_WINDOW or WEBVIEW_WINDOW_MAXIMIZED:
            return self.bounds()
        width = max(1400, int(payload.get("width", WEBVIEW_WINDOW.width)))
        height = max(920, int(payload.get("height", WEBVIEW_WINDOW.height)))
        x = int(payload.get("x", WEBVIEW_WINDOW.x))
        y = int(payload.get("y", WEBVIEW_WINDOW.y))
        WEBVIEW_WINDOW.move(x, y)
        WEBVIEW_WINDOW.resize(width, height)
        return self.bounds()

    def select_path(self, kind: str = "directory", title: str = "") -> str:
        """优先使用 pywebview 原生对话框，减少 EXE 对 tkinter/Tcl 的依赖。"""
        if not WEBVIEW_WINDOW:
            return ""
        import webview

        dialog_type = webview.FileDialog.FOLDER if kind == "directory" else webview.FileDialog.OPEN
        file_types = ("图片文件 (*.jpg;*.jpeg;*.png;*.tif;*.tiff)", "所有文件 (*.*)") if kind == "file" else ()
        selected = WEBVIEW_WINDOW.create_file_dialog(dialog_type=dialog_type, allow_multiple=False, file_types=file_types)
        if not selected:
            return ""
        return str(selected[0])

    def close(self) -> None:
        if WEBVIEW_WINDOW:
            WEBVIEW_WINDOW.destroy()


def main() -> None:
    set_windows_app_id()
    configure_webview_runtime()
    port = find_available_port()
    url = f"http://127.0.0.1:{port}"

    try:
        # pywebview/WebView2 的窗口初始化必须留在主线程，否则 Windows 下可能只渲染界面但不响应鼠标键盘。
        import webview
    except Exception:
        start_http_server(port)
        wait_for_server(url)
        webbrowser.open(url)
        if HTTP_SERVER_THREAD:
            HTTP_SERVER_THREAD.join()
        return

    webview.settings["DRAG_REGION_SELECTOR"] = ".window-drag"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
    window_api = WindowControlsApi()
    initial_x, initial_y = centered_window_position(MAIN_WINDOW_WIDTH, MAIN_WINDOW_HEIGHT)
    window = webview.create_window(
        "照片分割器",
        html=startup_html(),
        js_api=window_api,
        width=MAIN_WINDOW_WIDTH,
        height=MAIN_WINDOW_HEIGHT,
        x=initial_x,
        y=initial_y,
        min_size=(1200, 760),
        resizable=True,
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#0d0f14",
    )
    bind_webview_window(window)
    try:
        webview.start(load_application_when_ready, args=(window, url, port), gui="edgechromium")
    finally:
        if HTTP_SERVER is not None:
            HTTP_SERVER.shutdown()


if __name__ == "__main__":
    main()
