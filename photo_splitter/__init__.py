from __future__ import annotations

from .config import BACKGROUND_MODES, DEFAULT_BACKGROUND_MODE, DEFAULT_PRESET_KEY, PROCESSING_PRESETS, ProcessingPreset
from .detection import detect_photo_boxes, split_image
from .io_utils import iter_images, iter_source_images, unique_output_path
from .postprocess import estimate_background_mode, refine_output_photo, rotate_by_face_orientation
from .processing import save_split_photos
from .runtime_backend import detect_runtime_environment, get_compute_backend
from .visualization import draw_preview_box

__all__ = [
    "BACKGROUND_MODES",
    "DEFAULT_BACKGROUND_MODE",
    "DEFAULT_PRESET_KEY",
    "PROCESSING_PRESETS",
    "ProcessingPreset",
    "detect_photo_boxes",
    "split_image",
    "iter_images",
    "iter_source_images",
    "unique_output_path",
    "estimate_background_mode",
    "refine_output_photo",
    "rotate_by_face_orientation",
    "save_split_photos",
    "detect_runtime_environment",
    "get_compute_backend",
    "draw_preview_box",
]
