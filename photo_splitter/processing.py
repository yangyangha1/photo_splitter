from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import ImageDraw

from .detection import split_image
from .io_utils import iter_source_images, safe_name, target_dir_for_source, unique_output_path
from .performance import jpeg_save_kwargs
from .postprocess import refine_output_photo
from .runtime_backend import configure_opencv_threads
from .visualization import draw_preview_box


def save_split_photos(
    source: Path,
    input_root: Path,
    output_root: Path,
    quality: int,
    preview: bool,
    overwrite: bool,
    dark_threshold: int,
    min_area_ratio: float,
    white_threshold: int = 225,
    skew_min_score_gain: float = 1.04,
    auto_face_rotate: bool = False,
    background_mode: str = "auto",
    detection_strategy: str = "balanced",
    include_root_name: bool = False,
) -> list[Path]:
    """分割单个源文件并保存输出照片。

    该函数是 CLI 和 GUI 共用的批处理核心。它只负责 I/O 和保存；检测逻辑在 detection，
    输出后处理在 postprocess，便于后续加入单图交互编辑时复用。
    """
    saved: list[Path] = []
    target_dir = target_dir_for_source(source, input_root, output_root, include_root_name=include_root_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    for page_stem, image in iter_source_images(source):
        processed_image, boxes, _angle = split_image(
            image,
            dark_threshold,
            min_area_ratio,
            background_mode=background_mode,
            detection_strategy=detection_strategy,
        )
        image_name = safe_name(page_stem)
        for index, box in enumerate(boxes, start=1):
            raw_crop = processed_image.crop(box)
            crop = refine_output_photo(
                raw_crop,
                white_threshold=white_threshold,
                skew_min_score_gain=skew_min_score_gain,
                auto_face_rotate=auto_face_rotate,
                background_mode=background_mode,
            )
            if crop is not raw_crop:
                raw_crop.close()
            target = unique_output_path(target_dir / f"{image_name}_{index:03d}.jpg", overwrite)
            try:
                crop.save(target, **jpeg_save_kwargs(fast=True, quality=quality))
                saved.append(target)
            finally:
                crop.close()

        if preview:
            preview_image = processed_image.copy()
            try:
                draw = ImageDraw.Draw(preview_image)
                for index, box in enumerate(boxes, start=1):
                    draw_preview_box(draw, box, f"{index:03d}", processed_image.width)
                preview_path = unique_output_path(target_dir / f"分割预览_{image_name}.jpg", overwrite)
                preview_image.save(preview_path, **jpeg_save_kwargs(fast=True, quality=92))
            finally:
                preview_image.close()
        if processed_image is not image:
            processed_image.close()
        image.close()

    return saved


def process_source_for_cli(args: tuple[Any, ...]) -> dict[str, Any]:
    """多进程 CLI 的任务入口。参数打包为 tuple，确保 ProcessPoolExecutor 可序列化。"""
    (
        source,
        input_root,
        output_path,
        quality,
        preview,
        overwrite,
        dark_threshold,
        min_area_ratio,
        white_threshold,
        skew_min_score_gain,
        auto_face_rotate,
        background_mode,
        detection_strategy,
        include_root_name,
    ) = args
    try:
        configure_opencv_threads(worker_count=1)
        saved = save_split_photos(
            source,
            input_root,
            output_path,
            quality,
            preview,
            overwrite,
            dark_threshold,
            min_area_ratio,
            white_threshold=white_threshold,
            skew_min_score_gain=skew_min_score_gain,
            auto_face_rotate=auto_face_rotate,
            background_mode=background_mode,
            detection_strategy=str(detection_strategy),
            include_root_name=bool(include_root_name),
        )
        return {
            "ok": True,
            "source": str(source),
            "detected": len(saved),
            "outputs": [str(path) for path in saved],
        }
    except Exception as exc:
        return {"ok": False, "source": str(source), "error": str(exc)}
