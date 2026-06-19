from __future__ import annotations

import importlib
import io
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
from werkzeug.serving import make_server

from photo_splitter.config import BACKGROUND_MODES, DEFAULT_BACKGROUND_MODE, DEFAULT_PRESET_KEY, JPEG_QUALITY, PROCESSING_PRESETS


ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(tempfile.gettempdir()) / "photo_splitter_web"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}
SINGLE_CACHE: dict[str, dict[str, Any]] = {}
PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
WEBVIEW_WINDOW: Any | None = None
WEBVIEW_WINDOW_MAXIMIZED = False


def resource_path(relative_path: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
    return bundle_root / relative_path


WEB_STATIC = resource_path("photo_splitter/web_ui/dist")
app = Flask(__name__, static_folder=str(WEB_STATIC), static_url_path="")


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
    return {
        "preset": preset.key,
        "preset_name": preset.name,
        "dark_threshold": int(payload.get("dark_threshold", preset.dark_threshold)),
        "min_area_ratio": float(payload.get("min_area_ratio", preset.min_area_ratio)),
        "white_threshold": int(payload.get("white_threshold", preset.white_threshold)),
        "background_mode": background_mode,
        "skew_min_score_gain": 1.0 + int(payload.get("skew_gain_percent", preset.skew_gain_percent)) / 100,
        "skew_gain_percent": int(payload.get("skew_gain_percent", preset.skew_gain_percent)),
        "auto_face_rotate": bool(payload.get("auto_face_rotate", False)),
    }


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def thumb_url_for(path: Path) -> str:
    """生成本地缩略图接口地址，路径只在本机后端解析，不暴露给外部服务。"""
    return f"/api/source/thumb?path={quote(str(path), safe='')}"


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


def cache_processed_image(source: Path, page_stem: str, image: Any, boxes: list[tuple[int, int, int, int]], options: dict[str, Any]) -> str:
    """把检测后的图像留在内存缓存中，供预览、手工修正和最终导出复用。"""
    image_id = uuid.uuid4().hex
    SINGLE_CACHE[image_id] = {"source": str(source), "page_stem": page_stem, "image": image, "boxes": boxes, "options": options}
    return image_id


def detected_item_payload(
    source: Path,
    input_root: Path,
    page_stem: str,
    page_count: int,
    image_id: str,
    image: Any,
    boxes: list[tuple[int, int, int, int]],
    status: str = "已检测",
) -> dict[str, Any]:
    """生成批量检测预览用的数据结构，图片本体通过 image_id 从内存缓存读取。"""
    base = scan_item_payload(source, input_root, status=status)
    if page_count > 1:
        base["name"] = f"{source.name} / {page_stem}"
        base["relative"] = f"{base['relative']} / {page_stem}"
    base.update(
        {
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}",
            "page_stem": page_stem,
            "width": image.width,
            "height": image.height,
            "boxes": [list(box) for box in boxes],
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

    return jsonify({"ok": True, "runtime": detect_runtime_environment()})


def open_dialog(kind: str, title: str) -> str:
    tk = importlib.import_module("tkinter")
    filedialog = importlib.import_module("tkinter.filedialog")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "file":
            return filedialog.askopenfilename(title=title, filetypes=[("图片文件", "*.jpg *.jpeg *.tif *.tiff"), ("所有文件", "*.*")])
        return filedialog.askdirectory(title=title)
    finally:
        root.destroy()


@app.post("/api/dialog")
def api_dialog():
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind") or "directory")
    title = str(payload.get("title") or "选择路径")
    if kind not in {"directory", "file"}:
        return json_error("不支持的对话框类型。")
    selected = open_dialog(kind, title)
    return jsonify({"ok": True, "path": selected})


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
    from photo_splitter.io_utils import iter_source_images

    path = Path(str(request.args.get("path") or "")).expanduser()
    if not path.exists():
        return "not found", 404
    _page_stem, image = iter_source_images(path)[0]
    return image_to_response(image, max_side=360)


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
        return json_error("没有找到可处理的 JPG/TIFF 文件。")
    options = options_from_payload(payload.get("options") or {})
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
        "items": [scan_item_payload(path, input_root) for path in images],
        "logs": [f"开始批量检测 {len(images)} 个文件。"],
        "started_at": time.time(),
    }

    def worker() -> None:
        from photo_splitter.detection import split_image
        from photo_splitter.io_utils import iter_source_images

        detected_items: list[dict[str, Any]] = []
        # 检测阶段只缓存处理图和检测框，不提前写输出文件。
        for index, source in enumerate(images, start=1):
            job = JOBS[job_id]
            job["index"] = index
            job["items"][index - 1]["status"] = "处理中"
            job["logs"].append(f"正在检测：{source.name}")
            try:
                pages = iter_source_images(source)
                file_box_count = 0
                for page_stem, image in pages:
                    processed, boxes, _angle = split_image(
                        image,
                        int(options["dark_threshold"]),
                        float(options["min_area_ratio"]),
                        background_mode=str(options["background_mode"]),
                    )
                    image_id = cache_processed_image(source, page_stem, processed, boxes, options)
                    detected_items.append(detected_item_payload(source, input_root, page_stem, len(pages), image_id, processed, boxes))
                    file_box_count += len(boxes)
                job["saved"] += file_box_count
                job["items"][index - 1]["status"] = "已检测"
                job["items"][index - 1]["saved"] = file_box_count
                job["logs"].append(f"检测完成：{source.name}，检测框 {file_box_count} 个。")
            except Exception as exc:
                error = str(exc)
                job["items"][index - 1]["status"] = "失败"
                job["failed"].append({"source": str(source), "error": error})
                job["logs"].append(f"失败：{source.name}，{error}")
        job = JOBS[job_id]
        job["status"] = "done"
        job["items"] = detected_items or job["items"]
        job["logs"].append(f"批量检测完成，检测框 {job['saved']} 个。")

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
        for index, item_payload in enumerate(items, start=1):
            job = JOBS[job_id]
            job["index"] = index
            job["items"][index - 1]["status"] = "处理中"
            image_id = str(item_payload.get("image_id") or "")
            cache_item = SINGLE_CACHE.get(image_id)
            if not cache_item:
                error = "检测缓存不存在，请重新批量检测。"
                job["items"][index - 1]["status"] = "失败"
                job["failed"].append({"source": str(item_payload.get("path") or ""), "error": error})
                job["logs"].append(f"失败：{item_payload.get('name') or image_id}，{error}")
                continue
            try:
                image = cache_item["image"]
                source = Path(str(cache_item["source"]))
                page_stem = str(cache_item.get("page_stem") or source.stem)
                item_options = options_from_payload(item_payload.get("options") or cache_item.get("options") or options)
                boxes = [tuple(int(v) for v in box) for box in item_payload.get("boxes", [])]
                target_dir = target_dir_for_source(source, input_root, output_path, include_root_name=True)
                target_dir.mkdir(parents=True, exist_ok=True)
                image_name = safe_name(page_stem)
                saved: list[Path] = []
                for box_index, box in enumerate(boxes, start=1):
                    x1, y1, x2, y2 = box
                    clamped = (max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))
                    if clamped[2] - clamped[0] < 5 or clamped[3] - clamped[1] < 5:
                        continue
                    crop = refine_output_photo(
                        image.crop(clamped),
                        white_threshold=int(item_options["white_threshold"]),
                        skew_min_score_gain=float(item_options["skew_min_score_gain"]),
                        auto_face_rotate=bool(item_options["auto_face_rotate"]),
                        background_mode=str(item_options["background_mode"]),
                    )
                    target = unique_output_path(target_dir / f"{image_name}_{box_index:03d}.jpg", overwrite=False)
                    crop.save(target, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
                    saved.append(target)
                preview = image.copy()
                draw = ImageDraw.Draw(preview)
                for box_index, box in enumerate(boxes, start=1):
                    draw_preview_box(draw, tuple(box), f"{box_index:03d}", image.width)
                preview_path = unique_output_path(target_dir / f"分割预览_{image_name}.jpg", overwrite=False)
                preview.save(preview_path, "JPEG", quality=92)
                job["saved"] += len(saved)
                job["items"][index - 1]["status"] = "已完成"
                job["items"][index - 1]["saved"] = len(saved)
                job["logs"].append(f"导出完成：{item_payload.get('name') or source.name}，输出 {len(saved)} 张。")
            except Exception as exc:
                error = str(exc)
                job["items"][index - 1]["status"] = "失败"
                job["failed"].append({"source": str(item_payload.get("path") or ""), "error": error})
                job["logs"].append(f"失败：{item_payload.get('name') or image_id}，{error}")
        job = JOBS[job_id]
        job["status"] = "done"
        job["logs"].append(f"批量导出完成，输出 {job['saved']} 张。")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.post("/api/batch/item")
def api_batch_item_update():
    payload = request.get_json(silent=True) or {}
    image_id = str(payload.get("image_id") or "")
    item = SINGLE_CACHE.get(image_id)
    if not item:
        return json_error("检测缓存不存在，请重新检测。")
    boxes = [tuple(int(v) for v in box) for box in payload.get("boxes", [])]
    options = options_from_payload(payload.get("options") or item.get("options") or {})
    item["boxes"] = boxes
    item["options"] = options
    return jsonify(
        {
            "ok": True,
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}",
            "width": item["image"].width,
            "height": item["image"].height,
            "boxes": [list(box) for box in boxes],
            "box_count": len(boxes),
        }
    )


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return json_error("任务不存在。", 404)
    return jsonify({"ok": True, "job": job})


def image_to_response(image: Any, max_side: int | None = None):
    response_image = image.convert("RGB")
    if max_side:
        response_image = response_image.copy()
        response_image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    response_image.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


@app.post("/api/single/preview")
def api_single_preview():
    from photo_splitter.io_utils import iter_source_images

    payload = request.get_json(silent=True) or {}
    source = Path(str(payload.get("source") or "")).expanduser()
    if not source.exists():
        return json_error("单张照片不存在。")
    page_stem, image = iter_source_images(source)[0]
    preview_id = uuid.uuid4().hex
    PREVIEW_CACHE[preview_id] = {"source": str(source), "page_stem": page_stem, "image": image}
    trim_preview_cache()
    return jsonify(
        {
            "ok": True,
            "image_url": f"/api/source/preview/{preview_id}",
            "name": source.name,
            "width": image.width,
            "height": image.height,
        }
    )


@app.get("/api/source/preview/<preview_id>")
def api_source_preview(preview_id: str):
    item = PREVIEW_CACHE.get(preview_id)
    if not item:
        return "not found", 404
    return image_to_response(item["image"])


@app.post("/api/single/detect")
def api_single_detect():
    from photo_splitter.detection import split_image
    from photo_splitter.io_utils import iter_source_images

    payload = request.get_json(silent=True) or {}
    source = Path(str(payload.get("source") or "")).expanduser()
    if not source.exists():
        return json_error("单张照片不存在。")
    options = options_from_payload(payload.get("options") or {})
    page_stem, image = iter_source_images(source)[0]
    # 单图检测使用与批量处理相同的检测入口，保证 UI 预览和最终批量算法一致。
    processed, boxes, _angle = split_image(
        image,
        int(options["dark_threshold"]),
        float(options["min_area_ratio"]),
        background_mode=str(options["background_mode"]),
    )
    image_id = uuid.uuid4().hex
    SINGLE_CACHE[image_id] = {"source": str(source), "page_stem": page_stem, "image": processed, "boxes": boxes, "options": options}
    return jsonify(
        {
            "ok": True,
            "image_id": image_id,
            "image_url": f"/api/single/image/{image_id}",
            "width": processed.width,
            "height": processed.height,
            "boxes": [list(box) for box in boxes],
        }
    )


@app.get("/api/single/image/<image_id>")
def api_single_image(image_id: str):
    item = SINGLE_CACHE.get(image_id)
    if not item:
        return "not found", 404
    return image_to_response(item["image"])


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
    image = item["image"]
    source_name = safe_name(Path(str(item["source"])).stem or "single_photo")
    boxes = [tuple(int(v) for v in box) for box in payload.get("boxes", [])]
    saved: list[Path] = []
    for index, box in enumerate(boxes, start=1):
        x1, y1, x2, y2 = box
        clamped = (max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2))
        if clamped[2] - clamped[0] < 5 or clamped[3] - clamped[1] < 5:
            continue
        crop = refine_output_photo(
            image.crop(clamped),
            white_threshold=int(options["white_threshold"]),
            skew_min_score_gain=float(options["skew_min_score_gain"]),
            auto_face_rotate=bool(options["auto_face_rotate"]),
            background_mode=str(options["background_mode"]),
        )
        target = unique_output_path(output_dir / f"{source_name}_manual_{index:03d}.jpg", overwrite=False)
        crop.save(target, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
        saved.append(target)
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    for index, box in enumerate(boxes, start=1):
        draw_preview_box(draw, tuple(box), f"{index:03d}", image.width)
    preview_path = unique_output_path(output_dir / f"分割预览_{source_name}_manual.jpg", overwrite=False)
    preview.save(preview_path, "JPEG", quality=92)
    return jsonify(
        {
            "ok": True,
            "saved": len(saved),
            "outputs": [str(path) for path in saved],
            "preview": str(preview_path),
            "output_dir": str(output_dir),
        }
    )


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
        file_types = ("图片文件 (*.jpg;*.jpeg;*.tif;*.tiff)", "所有文件 (*.*)") if kind == "file" else ()
        selected = WEBVIEW_WINDOW.create_file_dialog(dialog_type=dialog_type, allow_multiple=False, file_types=file_types)
        if not selected:
            return ""
        return str(selected[0])

    def close(self) -> None:
        if WEBVIEW_WINDOW:
            WEBVIEW_WINDOW.destroy()


def main() -> None:
    set_windows_app_id()
    port = find_available_port()
    url = f"http://127.0.0.1:{port}"
    http_server = make_server("127.0.0.1", port, app, threaded=True)
    server = threading.Thread(target=http_server.serve_forever, daemon=True)
    server.start()
    wait_for_server(url)

    try:
        # pywebview/WebView2 的窗口初始化必须留在主线程，否则 Windows 下可能只渲染界面但不响应鼠标键盘。
        import webview
    except Exception:
        webbrowser.open(url)
        server.join()
        return

    webview.settings["DRAG_REGION_SELECTOR"] = ".window-drag"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
    window_api = WindowControlsApi()
    window = webview.create_window(
        "照片分割器",
        url,
        js_api=window_api,
        width=1560,
        height=1020,
        min_size=(1400, 920),
        resizable=True,
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#0d0f14",
    )
    bind_webview_window(window)
    try:
        webview.start(gui="edgechromium")
    finally:
        http_server.shutdown()


if __name__ == "__main__":
    main()
