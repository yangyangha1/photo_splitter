from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from .config import IMAGE_EXTENSIONS, SKIP_DIR_PREFIXES


def iter_images(input_path: Path, output_path: Path) -> list[Path]:
    """递归查找可处理图片，并跳过输出目录、归档目录和历史预览图。

    输出目录必须跳过，否则批量处理会把上一次生成的分割结果再次当作输入。
    """
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in IMAGE_EXTENSIONS else []

    images: list[Path] = []
    for path in input_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if path.stem.endswith("_preview") or path.name.startswith("分割预览_"):
            continue
        if any(parent.name.startswith(SKIP_DIR_PREFIXES) or parent.name.lower().startswith(SKIP_DIR_PREFIXES) for parent in path.parents):
            continue
        try:
            path.resolve().relative_to(output_path)
            continue
        except ValueError:
            images.append(path)
    return sorted(images)


def iter_source_images(source: Path) -> list[tuple[str, Image.Image]]:
    """读取源图片。

    这里会把 PIL 图片复制到内存后关闭源文件，避免批量处理大量 TIFF/JPG 时占用文件句柄。
    对多页 TIFF，按“文件名_p001、文件名_p002...”拆成多个页面处理。
    """
    pages: list[tuple[str, Image.Image]] = []
    with Image.open(source) as image:
        page_count = getattr(image, "n_frames", 1)
        for index, page in enumerate(ImageSequence.Iterator(image), start=1):
            stem = source.stem if page_count <= 1 else f"{source.stem}_p{index:03d}"
            pages.append((stem, ImageOps.exif_transpose(page).convert("RGB").copy()))
    return pages


def unique_output_path(path: Path, overwrite: bool) -> Path:
    """生成不覆盖现有文件的输出路径。overwrite=True 时直接返回原路径。"""
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def safe_name(name: str) -> str:
    """把源文件名转换为适合 Windows/macOS/Linux 文件系统的安全输出名。"""
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid else char for char in name).strip()
    return cleaned or "照片"


def target_dir_for_source(source: Path, input_root: Path, output_path: Path, include_root_name: bool = False) -> Path:
    """根据源文件所在子目录生成输出目录。

    include_root_name=True 时，源目录根部没有放进子文件夹的散图输出到 output/源文件名/；
    已经在子文件夹里的图片仍保持原有子目录样式。
    """
    parent = source.parent.resolve()
    try:
        relative_parent = parent.relative_to(input_root.resolve())
    except ValueError:
        relative_parent = Path(parent.name)
    if str(relative_parent) == ".":
        relative_parent = Path(safe_name(source.stem)) if include_root_name else Path()
    return output_path / relative_parent


def open_in_file_manager(path: Path) -> None:
    """跨平台打开文件或目录。Windows 使用 os.startfile，macOS 使用 open，Linux 使用 xdg-open。"""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    subprocess.run(["xdg-open", str(path)], check=False)
