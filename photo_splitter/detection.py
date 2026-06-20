from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

from .geometry import merge_close_runs, runs, smooth, suppress_nested_and_duplicates
from .postprocess import auto_deskew_image, estimate_background_mode, normalize_background_mode
from .runtime_backend import (
    background_difference_mask as accelerated_background_difference_mask,
    canny_edges,
    close_and_dilate_edges,
    close_mask,
    close_u8,
    gray_and_blur,
    gray_and_channel_range,
)

DETECTION_STRATEGIES = {"balanced", "aggressive", "conservative"}


@dataclass(frozen=True)
class SplitStrategyConfig:
    """预设内置的检测策略，集中管理积极/保守模式的算法行为差异。"""

    name: str
    area_ratio_factor: float
    overlap_remove_threshold: float
    dark_merge_gap: int
    canny_lower_factor: float
    canny_upper_factor: float
    canny_min_lower: int
    canny_min_gap: int
    canny_max_upper: int
    edge_close_iterations: int
    edge_dilate_iterations: int
    contour_area_factor: float
    min_side: int
    aspect_min: float
    aspect_max: float
    approx_side_limit: float
    weak_shape_side_limit: float
    content_close_size: int
    min_component_pixels_factor: float
    allow_single_edge_seed: bool
    run_content_fallback: bool


SPLIT_STRATEGIES: dict[str, SplitStrategyConfig] = {
    "balanced": SplitStrategyConfig(
        name="balanced",
        area_ratio_factor=1.0,
        overlap_remove_threshold=0.70,
        dark_merge_gap=90,
        canny_lower_factor=1.0,
        canny_upper_factor=1.0,
        canny_min_lower=18,
        canny_min_gap=40,
        canny_max_upper=210,
        edge_close_iterations=2,
        edge_dilate_iterations=1,
        contour_area_factor=0.20,
        min_side=45,
        aspect_min=0.18,
        aspect_max=5.2,
        approx_side_limit=0.105,
        weak_shape_side_limit=0.110,
        content_close_size=7,
        min_component_pixels_factor=0.08,
        allow_single_edge_seed=True,
        run_content_fallback=True,
    ),
    "aggressive": SplitStrategyConfig(
        name="aggressive",
        area_ratio_factor=0.78,
        overlap_remove_threshold=0.82,
        dark_merge_gap=20,
        canny_lower_factor=0.78,
        canny_upper_factor=0.92,
        canny_min_lower=8,
        canny_min_gap=28,
        canny_max_upper=230,
        edge_close_iterations=3,
        edge_dilate_iterations=1,
        contour_area_factor=0.12,
        min_side=34,
        aspect_min=0.14,
        aspect_max=6.0,
        approx_side_limit=0.085,
        weak_shape_side_limit=0.090,
        content_close_size=9,
        min_component_pixels_factor=0.055,
        allow_single_edge_seed=True,
        run_content_fallback=True,
    ),
    "conservative": SplitStrategyConfig(
        name="conservative",
        area_ratio_factor=1.28,
        overlap_remove_threshold=0.58,
        dark_merge_gap=120,
        canny_lower_factor=1.18,
        canny_upper_factor=1.08,
        canny_min_lower=20,
        canny_min_gap=46,
        canny_max_upper=235,
        edge_close_iterations=1,
        edge_dilate_iterations=0,
        contour_area_factor=0.36,
        min_side=62,
        aspect_min=0.24,
        aspect_max=4.4,
        approx_side_limit=0.130,
        weak_shape_side_limit=0.135,
        content_close_size=5,
        min_component_pixels_factor=0.12,
        allow_single_edge_seed=False,
        run_content_fallback=False,
    ),
}


def normalize_detection_strategy(strategy: str | None) -> str:
    value = str(strategy or "balanced").strip().lower()
    return value if value in DETECTION_STRATEGIES else "balanced"


def split_strategy_config(strategy: str | None) -> SplitStrategyConfig:
    return SPLIT_STRATEGIES[normalize_detection_strategy(strategy)]


def _strategy_area_ratio(min_area_ratio: float, strategy: str) -> float:
    """按检测策略微调候选框面积门槛，预设滑块仍作为主控制量。"""
    config = split_strategy_config(strategy)
    if config.name == "aggressive":
        return max(0.0005, min_area_ratio * config.area_ratio_factor)
    if config.name == "conservative":
        return min(0.02, min_area_ratio * config.area_ratio_factor)
    return min_area_ratio * config.area_ratio_factor


def _strategy_dark_merge_gap(strategy: str) -> int:
    """黑底/灰底页面的相邻框合并距离，积极模式避免把弱分隔线两侧照片合并。"""
    return split_strategy_config(strategy).dark_merge_gap


def conservative_filter_boxes(
    boxes: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    """保守模式下过滤异常小碎框，避免纹理、文字或照片内部边缘被当成照片。"""
    boxes = suppress_nested_and_duplicates(boxes)
    if len(boxes) <= 8:
        return boxes

    whole_area = image_width * image_height
    areas = np.asarray([_box_area(box) for box in boxes], dtype=np.float32)
    median_area = float(np.median(areas)) if areas.size else 0.0

    filtered: list[tuple[int, int, int, int]] = []
    for box in boxes:
        area = _box_area(box)
        if area < median_area * 0.35:
            continue
        if area < whole_area * 0.004:
            continue
        filtered.append(box)
    return suppress_nested_and_duplicates(filtered)


def aggressive_filter_boxes(
    boxes: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    """积极模式保留弱候选，但清理明显小于主照片的装饰、文字和内部纹理碎片。"""
    boxes = _remove_high_overlap_fragments(suppress_nested_and_duplicates(boxes), overlap_threshold=0.72)
    if not boxes:
        return []

    whole_area = image_width * image_height
    largest_area = max(_box_area(box) for box in boxes)
    if len(boxes) > 12:
        keep_floor = max(whole_area * 0.006, largest_area * 0.18)
    elif len(boxes) > 8:
        keep_floor = max(whole_area * 0.0035, largest_area * 0.06)
    else:
        keep_floor = max(whole_area * 0.0025, largest_area * 0.06)

    filtered = [box for box in boxes if _box_area(box) >= keep_floor]
    if not filtered:
        filtered = [max(boxes, key=_box_area)]
    return suppress_nested_and_duplicates(filtered)


def finalize_strategy_boxes(
    boxes: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
    strategy: str,
) -> list[tuple[int, int, int, int]]:
    """按最终策略做一次轻量收口；积极模式保留候选，保守模式清理异常碎框。"""
    normalized = normalize_detection_strategy(strategy)
    if normalized == "conservative":
        return conservative_filter_boxes(boxes, image_width, image_height)
    if normalized == "aggressive":
        return aggressive_filter_boxes(boxes, image_width, image_height)
    return suppress_nested_and_duplicates(boxes)


def connected_components(mask: np.ndarray, min_pixels: int) -> list[tuple[int, int, int, int, int]]:
    # 优先使用 OpenCV 的 C++ 实现，比逐像素 Python 队列扫描快很多；无 OpenCV 时自动回退。
    try:
        import cv2

        labels_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8),
            connectivity=8,
        )
        boxes: list[tuple[int, int, int, int, int]] = []
        for label in range(1, labels_count):
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            count = int(stats[label, cv2.CC_STAT_AREA])
            if count >= min_pixels:
                boxes.append((x, y, x + width, y + height, count))
        return sorted(boxes, key=lambda item: (item[1], item[0]))
    except Exception:
        pass

    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    boxes: list[tuple[int, int, int, int, int]] = []

    for y in range(height):
        for x in range(width):
            if visited[y, x] or not mask[y, x]:
                continue

            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            count = 0

            while queue:
                cx, cy = queue.popleft()
                count += 1
                if cx < min_x:
                    min_x = cx
                elif cx > max_x:
                    max_x = cx
                if cy < min_y:
                    min_y = cy
                elif cy > max_y:
                    max_y = cy

                for ny in (cy - 1, cy, cy + 1):
                    if ny < 0 or ny >= height:
                        continue
                    for nx in (cx - 1, cx, cx + 1):
                        if nx < 0 or nx >= width or (nx == cx and ny == cy):
                            continue
                        if not visited[ny, nx] and mask[ny, nx]:
                            visited[ny, nx] = True
                            queue.append((nx, ny))

            if count >= min_pixels:
                boxes.append((min_x, min_y, max_x + 1, max_y + 1, count))

    return boxes


def crop_inner_content(gray: np.ndarray, box: tuple[int, int, int, int], threshold: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    local = gray[y1:y2, x1:x2] > threshold
    if local.size == 0:
        return box

    rows = np.where(local.mean(axis=1) > 0.03)[0]
    cols = np.where(local.mean(axis=0) > 0.03)[0]
    if rows.size == 0 or cols.size == 0:
        return box

    return x1 + int(cols[0]), y1 + int(rows[0]), x1 + int(cols[-1]) + 1, y1 + int(rows[-1]) + 1


def split_box_on_empty_gaps(
    content_mask: np.ndarray,
    box: tuple[int, int, int, int],
    min_side: int,
) -> list[tuple[int, int, int, int]]:
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    if width < min_side * 2 or height < min_side:
        return [box]

    local = content_mask[y1:y2, x1:x2]
    if local.size == 0:
        return [box]

    def best_gap(density: np.ndarray, length: int) -> tuple[int, int] | None:
        gap_min = max(16, min(width, height) // 8)
        best: tuple[int, int] | None = None
        start: int | None = None
        for index, value in enumerate(density):
            is_gap = value < 0.008
            if is_gap and start is None:
                start = index
            elif not is_gap and start is not None:
                if index - start >= gap_min and start > min_side and length - index > min_side:
                    if best is None or index - start > best[1] - best[0]:
                        best = (start, index)
                start = None
        if start is not None and length - start >= gap_min and start > min_side:
            if best is None or length - start > best[1] - best[0]:
                best = (start, length)
        return best

    column_gap = best_gap(local.mean(axis=0), width)
    row_gap = best_gap(local.mean(axis=1), height)

    if column_gap and (not row_gap or column_gap[1] - column_gap[0] >= row_gap[1] - row_gap[0]):
        split_at = x1 + (column_gap[0] + column_gap[1]) // 2
        left = (x1, y1, split_at, y2)
        right = (split_at, y1, x2, y2)
        return split_box_on_empty_gaps(content_mask, left, min_side) + split_box_on_empty_gaps(
            content_mask, right, min_side
        )

    if row_gap:
        split_at = y1 + (row_gap[0] + row_gap[1]) // 2
        top = (x1, y1, x2, split_at)
        bottom = (x1, split_at, x2, y2)
        return split_box_on_empty_gaps(content_mask, top, min_side) + split_box_on_empty_gaps(
            content_mask, bottom, min_side
        )

    return [box]


def looks_like_photo(rgb: np.ndarray, box: tuple[int, int, int, int], min_area: int) -> bool:
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    area = width * height
    if area < min_area or width < 35 or height < 35:
        return False

    aspect = width / height
    if aspect < 0.22 or aspect > 4.6:
        return False

    crop = rgb[y1:y2, x1:x2]
    gray, channel_range = gray_and_channel_range(crop)

    gray_std = float(gray.std())
    saturation_mean = float(channel_range.mean())
    edge_margin = max(2, min(width, height) // 60)
    inner = gray[edge_margin : height - edge_margin, edge_margin : width - edge_margin]
    inner_std = float(inner.std()) if inner.size else gray_std

    # This removes plain white separator blocks while keeping pale photos.
    gray_mean = float(gray.mean())
    if gray_mean > 232 and gray_std < 36 and saturation_mean < 16:
        return False
    if gray_std < 22 and inner_std < 22 and saturation_mean < 13:
        return False
    return True


def merge_boxes_without_separator(
    rgb: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """把被弱边界误切开的相邻小框合并，前提是中间没有明显白色分隔线。"""
    if len(boxes) < 2:
        return boxes

    gray, channel_range = gray_and_channel_range(rgb)
    if (gray < 70).mean() > 0.18:
        return boxes
    white = (gray > 220) & (channel_range < 48)
    height, width = white.shape

    def has_vertical_separator(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
        y1 = max(left[1], right[1])
        y2 = min(left[3], right[3])
        if y2 - y1 < min(left[3] - left[1], right[3] - right[1]) * 0.45:
            return True
        x = (left[2] + right[0]) // 2
        band = max(3, width // 400)
        region = white[y1:y2, max(0, x - band) : min(width, x + band + 1)]
        return bool(region.size and region.mean() > 0.56)

    def has_horizontal_separator(top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> bool:
        x1 = max(top[0], bottom[0])
        x2 = min(top[2], bottom[2])
        if x2 - x1 < min(top[2] - top[0], bottom[2] - bottom[0]) * 0.45:
            return True
        y = (top[3] + bottom[1]) // 2
        band = max(3, height // 400)
        region = white[max(0, y - band) : min(height, y + band + 1), x1:x2]
        return bool(region.size and region.mean() > 0.56)

    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(merged):
            for j, b in enumerate(merged[i + 1 :], start=i + 1):
                same_row = min(a[3], b[3]) - max(a[1], b[1]) > min(a[3] - a[1], b[3] - b[1]) * 0.72
                close_x = abs(a[2] - b[0]) < width * 0.04 or abs(b[2] - a[0]) < width * 0.04
                if same_row and close_x:
                    left, right = (a, b) if a[0] <= b[0] else (b, a)
                    if not has_vertical_separator(left, right):
                        merged[i] = (
                            min(a[0], b[0]),
                            min(a[1], b[1]),
                            max(a[2], b[2]),
                            max(a[3], b[3]),
                        )
                        del merged[j]
                        changed = True
                        break

                same_col = min(a[2], b[2]) - max(a[0], b[0]) > min(a[2] - a[0], b[2] - b[0]) * 0.72
                close_y = abs(a[3] - b[1]) < height * 0.04 or abs(b[3] - a[1]) < height * 0.04
                if same_col and close_y:
                    top, bottom = (a, b) if a[1] <= b[1] else (b, a)
                    if not has_horizontal_separator(top, bottom):
                        merged[i] = (
                            min(a[0], b[0]),
                            min(a[1], b[1]),
                            max(a[2], b[2]),
                            max(a[3], b[3]),
                        )
                        del merged[j]
                        changed = True
                        break
            if changed:
                break

    return suppress_nested_and_duplicates(merged)


def merge_cross_grid_fragments(
    rgb: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """处理白底网格/扫描页中跨格碎片，把同一张照片的碎片重新合并。"""
    if len(boxes) < 2:
        return boxes

    gray, channel_range = gray_and_channel_range(rgb)
    white = (gray > 220) & (channel_range < 48)
    dark = gray < 70
    height, width = gray.shape

    def area(box: tuple[int, int, int, int]) -> int:
        return max(0, box[2] - box[0]) * max(0, box[3] - box[1])

    def union_box(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])

    def edge_diff_horizontal(top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> float:
        x1 = max(top[0], bottom[0])
        x2 = min(top[2], bottom[2])
        if x2 <= x1:
            return 999.0
        band = max(8, min(80, (top[3] - top[1]) // 12, (bottom[3] - bottom[1]) // 12))
        top_strip = rgb[max(top[1], top[3] - band) : top[3], x1:x2].astype(np.float32)
        bottom_strip = rgb[bottom[1] : min(bottom[3], bottom[1] + band), x1:x2].astype(np.float32)
        if top_strip.size == 0 or bottom_strip.size == 0:
            return 999.0
        return float(np.mean(np.abs(top_strip.mean(axis=0) - bottom_strip.mean(axis=0))))

    def edge_diff_vertical(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
        y1 = max(left[1], right[1])
        y2 = min(left[3], right[3])
        if y2 <= y1:
            return 999.0
        band = max(8, min(80, (left[2] - left[0]) // 12, (right[2] - right[0]) // 12))
        left_strip = rgb[y1:y2, max(left[0], left[2] - band) : left[2]].astype(np.float32)
        right_strip = rgb[y1:y2, right[0] : min(right[2], right[0] + band)].astype(np.float32)
        if left_strip.size == 0 or right_strip.size == 0:
            return 999.0
        return float(np.mean(np.abs(left_strip.mean(axis=1) - right_strip.mean(axis=1))))

    def merge_score(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float | None:
        max_gap = max(18, min(95, int(min(width, height) * 0.014)))
        union = union_box(a, b)
        fill_ratio = (area(a) + area(b)) / max(1, area(union))
        if fill_ratio < 0.86:
            return None

        if a[3] <= b[1] or b[3] <= a[1]:
            top, bottom = (a, b) if a[1] <= b[1] else (b, a)
            gap = bottom[1] - top[3]
            overlap = min(top[2], bottom[2]) - max(top[0], bottom[0])
            overlap_ratio = overlap / max(1, min(top[2] - top[0], bottom[2] - bottom[0]))
            if gap < 0 or gap > max_gap or overlap_ratio < 0.52:
                return None
            x1 = max(top[0], bottom[0])
            x2 = min(top[2], bottom[2])
            gap_white = white[top[3] : bottom[1], x1:x2]
            gap_dark = dark[top[3] : bottom[1], x1:x2]
            gap_white_mean = float(gap_white.mean()) if gap_white.size else 0.0
            if gap_white_mean > 0.78:
                return None
            if gap_dark.size and float(gap_dark.mean()) > 0.45:
                return None
            diff = edge_diff_horizontal(top, bottom)
            diff_limit = 24.0 if gap <= 12 and fill_ratio > 0.96 and gap_white_mean < 0.75 else 18.0
            return diff if diff < diff_limit else None

        if a[2] <= b[0] or b[2] <= a[0]:
            left, right = (a, b) if a[0] <= b[0] else (b, a)
            gap = right[0] - left[2]
            overlap = min(left[3], right[3]) - max(left[1], right[1])
            overlap_ratio = overlap / max(1, min(left[3] - left[1], right[3] - right[1]))
            if gap < 0 or gap > max_gap or overlap_ratio < 0.52:
                return None
            y1 = max(left[1], right[1])
            y2 = min(left[3], right[3])
            gap_white = white[y1:y2, left[2] : right[0]]
            gap_dark = dark[y1:y2, left[2] : right[0]]
            gap_white_mean = float(gap_white.mean()) if gap_white.size else 0.0
            if gap_white_mean > 0.78:
                return None
            if gap_dark.size and float(gap_dark.mean()) > 0.45:
                return None
            diff = edge_diff_vertical(left, right)
            diff_limit = 24.0 if gap <= 12 and fill_ratio > 0.96 and gap_white_mean < 0.75 else 18.0
            return diff if diff < diff_limit else None

        return None

    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        best_pair: tuple[int, int] | None = None
        best_score = 999.0
        for i, a in enumerate(merged):
            for j, b in enumerate(merged[i + 1 :], start=i + 1):
                score = merge_score(a, b)
                if score is not None and score < best_score:
                    best_score = score
                    best_pair = (i, j)
        if best_pair is not None:
            i, j = best_pair
            merged[i] = union_box(merged[i], merged[j])
            del merged[j]
            merged = suppress_nested_and_duplicates(merged)
            changed = True

    areas = [area(box) for box in merged]
    median_area = float(np.median(np.asarray(areas, dtype=np.float32))) if areas else 0.0
    max_gap = max(18, min(95, int(min(width, height) * 0.02)))

    def are_cluster_neighbors(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        if a[3] <= b[1] or b[3] <= a[1]:
            top, bottom = (a, b) if a[1] <= b[1] else (b, a)
            gap = bottom[1] - top[3]
            overlap = min(top[2], bottom[2]) - max(top[0], bottom[0])
            overlap_ratio = overlap / max(1, min(top[2] - top[0], bottom[2] - bottom[0]))
            return 0 <= gap <= max_gap and overlap_ratio > 0.32 and edge_diff_horizontal(top, bottom) < 22.0
        if a[2] <= b[0] or b[2] <= a[0]:
            left, right = (a, b) if a[0] <= b[0] else (b, a)
            gap = right[0] - left[2]
            overlap = min(left[3], right[3]) - max(left[1], right[1])
            overlap_ratio = overlap / max(1, min(left[3] - left[1], right[3] - right[1]))
            return 0 <= gap <= max_gap and overlap_ratio > 0.32 and edge_diff_vertical(left, right) < 22.0
        return False

    changed = True
    while changed and median_area > 0:
        changed = False
        n = len(merged)
        adjacency = [set() for _ in range(n)]
        for i, a in enumerate(merged):
            for j, b in enumerate(merged[i + 1 :], start=i + 1):
                if are_cluster_neighbors(a, b):
                    adjacency[i].add(j)
                    adjacency[j].add(i)

        seen: set[int] = set()
        for start in range(n):
            if start in seen:
                continue
            stack = [start]
            component: list[int] = []
            seen.add(start)
            while stack:
                current = stack.pop()
                component.append(current)
                for nxt in adjacency[current]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            if len(component) < 3:
                continue

            component_boxes = [merged[index] for index in component]
            union = component_boxes[0]
            for box in component_boxes[1:]:
                union = union_box(union, box)
            fill_ratio = sum(area(box) for box in component_boxes) / max(1, area(union))
            small_count = sum(1 for box in component_boxes if area(box) < median_area * 0.55)
            if fill_ratio < 0.88 or small_count < 2 or area(union) < median_area * 1.15:
                continue

            for index in sorted(component, reverse=True):
                del merged[index]
            merged.append(union)
            merged = suppress_nested_and_duplicates(merged)
            changed = True
            break

    return suppress_nested_and_duplicates(merged)


def merge_dark_layout_fragments(
    rgb: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """处理黑色相框拼图：只有中间没有黑色相框线时才合并相邻检测框。"""
    if len(boxes) < 2:
        return boxes

    gray, _channel_range = gray_and_channel_range(rgb)
    dark = gray < 70
    height, width = dark.shape

    def has_dark_vertical_frame(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
        y1 = max(left[1], right[1])
        y2 = min(left[3], right[3])
        if y2 <= y1:
            return True
        x1 = min(left[2], right[2])
        x2 = max(left[0], right[0])
        if x2 <= x1:
            x = (left[2] + right[0]) // 2
            band = max(3, width // 350)
            region = dark[y1:y2, max(0, x - band) : min(width, x + band + 1)]
        else:
            region = dark[y1:y2, x1:x2]
        return bool(region.size and region.mean() > 0.45)

    def has_dark_horizontal_frame(top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> bool:
        x1 = max(top[0], bottom[0])
        x2 = min(top[2], bottom[2])
        if x2 <= x1:
            return True
        y1 = min(top[3], bottom[3])
        y2 = max(top[1], bottom[1])
        if y2 <= y1:
            y = (top[3] + bottom[1]) // 2
            band = max(3, height // 350)
            region = dark[max(0, y - band) : min(height, y + band + 1), x1:x2]
        else:
            region = dark[y1:y2, x1:x2]
        return bool(region.size and region.mean() > 0.45)

    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(merged):
            for j, b in enumerate(merged[i + 1 :], start=i + 1):
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                same_row = overlap_y > min(a[3] - a[1], b[3] - b[1]) * 0.45
                close_x = abs(a[2] - b[0]) < width * 0.035 or abs(b[2] - a[0]) < width * 0.035
                if same_row and close_x:
                    left, right = (a, b) if a[0] <= b[0] else (b, a)
                    if not has_dark_vertical_frame(left, right):
                        merged[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                        del merged[j]
                        changed = True
                        break

                same_col = overlap_x > min(a[2] - a[0], b[2] - b[0]) * 0.45
                close_y = abs(a[3] - b[1]) < height * 0.035 or abs(b[3] - a[1]) < height * 0.035
                if same_col and close_y:
                    top, bottom = (a, b) if a[1] <= b[1] else (b, a)
                    if not has_dark_horizontal_frame(top, bottom):
                        merged[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                        del merged[j]
                        changed = True
                        break
            if changed:
                break

    return suppress_nested_and_duplicates(merged)


def split_boxes_on_internal_separators(
    rgb: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
    min_area_ratio: float,
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    """如果一个检测框内部还有明显白色分隔线，则递归拆成多张照片。"""
    if not boxes:
        return boxes

    strategy = normalize_detection_strategy(detection_strategy)
    gray, channel_range = gray_and_channel_range(rgb)
    white_gray_threshold = 208 if strategy == "aggressive" else 226 if strategy == "conservative" else 218
    white_range_threshold = 62 if strategy == "aggressive" else 42 if strategy == "conservative" else 52
    white = (gray > white_gray_threshold) & (channel_range < white_range_threshold)
    whole_area = rgb.shape[0] * rgb.shape[1]
    min_area = max(900, int(whole_area * _strategy_area_ratio(min_area_ratio, strategy)))
    areas = np.asarray([(x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in boxes], dtype=np.float32)
    median_area = float(np.median(areas)) if areas.size else 0.0

    def split_one(box: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        if width < 120 or height < 120:
            return [box]

        local_white = white[y1:y2, x1:x2]
        min_sep = max(3 if strategy == "aggressive" else 5, min(width, height) // (460 if strategy == "aggressive" else 300 if strategy == "conservative" else 360))
        min_side = max(
            34 if strategy == "aggressive" else 62 if strategy == "conservative" else 45,
            min(width, height) // (8 if strategy == "aggressive" else 5 if strategy == "conservative" else 6),
        )
        area = width * height
        oversized = median_area > 0 and area > median_area * 1.15
        if strategy == "aggressive":
            col_thresholds = (0.62, 0.42, 0.28) if oversized else (0.62, 0.42)
            row_thresholds = (0.62, 0.42, 0.28) if oversized else (0.62, 0.42)
            max_separator_ratio = 0.24
        elif strategy == "conservative":
            col_thresholds = (0.84,)
            row_thresholds = (0.84,)
            max_separator_ratio = 0.12
        else:
            col_thresholds = (0.72, 0.34) if oversized else (0.72,)
            row_thresholds = (0.72, 0.34) if oversized else (0.72,)
            max_separator_ratio = 0.18

        for threshold in col_thresholds:
            col_runs = merge_close_runs(runs(local_white.mean(axis=0) > threshold, min_sep), max_gap=3)
            valid_col_runs = [
                (a, b)
                for a, b in col_runs
                if a > min_side and width - b > min_side and b - a <= width * max_separator_ratio
            ]
            valid_col_runs.sort(key=lambda run: abs(((run[0] + run[1]) / 2) - width / 2))
            for a, b in valid_col_runs:
                left = crop_inner_content(gray, (x1, y1, x1 + a, y2), threshold=35)
                right = crop_inner_content(gray, (x1 + b, y1, x2, y2), threshold=35)
                parts = []
                if looks_like_photo(rgb, left, min_area):
                    parts.extend(split_one(left))
                if looks_like_photo(rgb, right, min_area):
                    parts.extend(split_one(right))
                if len(parts) >= 2:
                    return parts

        for threshold in row_thresholds:
            row_runs = merge_close_runs(runs(local_white.mean(axis=1) > threshold, min_sep), max_gap=3)
            valid_row_runs = [
                (a, b)
                for a, b in row_runs
                if a > min_side and height - b > min_side and b - a <= height * max_separator_ratio
            ]
            valid_row_runs.sort(key=lambda run: abs(((run[0] + run[1]) / 2) - height / 2))
            for a, b in valid_row_runs:
                top = crop_inner_content(gray, (x1, y1, x2, y1 + a), threshold=35)
                bottom = crop_inner_content(gray, (x1, y1 + b, x2, y2), threshold=35)
                parts = []
                if looks_like_photo(rgb, top, min_area):
                    parts.extend(split_one(top))
                if looks_like_photo(rgb, bottom, min_area):
                    parts.extend(split_one(bottom))
                if len(parts) >= 2:
                    return parts

        return [box]

    result: list[tuple[int, int, int, int]] = []
    for box in boxes:
        result.extend(split_one(box))
    return suppress_nested_and_duplicates(result)


def _best_regular_boundaries(
    separator_score: np.ndarray,
    count: int,
    min_gap: int,
) -> tuple[list[int], float]:
    length = len(separator_score)
    if count <= 1:
        return [0, length], 0.0

    boundaries = [0]
    scores: list[float] = []
    for index in range(1, count):
        expected = length * index / count
        radius = max(min_gap, int(length / count * 0.22))
        start = max(boundaries[-1] + min_gap, int(expected - radius))
        end = min(length - min_gap, int(expected + radius))
        if end <= start:
            return [], 0.0
        local = separator_score[start:end]
        best = start + int(np.argmax(local))
        boundaries.append(best)
        scores.append(float(separator_score[best]))

    boundaries.append(length)
    if any(b2 - b1 < min_gap for b1, b2 in zip(boundaries, boundaries[1:])):
        return [], 0.0
    return boundaries, (sum(scores) / len(scores) if scores else 0.0)


def _detect_regular_grid_boxes(
    rgb_image: Image.Image,
    min_area_ratio: float,
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    """识别规则排版的白边网格，例如扫描页里多张照片按行列排列。"""
    strategy = normalize_detection_strategy(detection_strategy)
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    if (gray < 70).mean() > 0.18:
        return []
    if strategy == "aggressive":
        white = (gray > 208) & (channel_range < 58)
        score_threshold = 0.40
        uniform_threshold = 0.64
        separator_threshold = 0.14
        min_cell_w = max(55, rgb_image.width // 8)
        min_cell_h = max(55, rgb_image.height // 7)
    elif strategy == "conservative":
        white = (gray > 228) & (channel_range < 36)
        score_threshold = 0.55
        uniform_threshold = 0.82
        separator_threshold = 0.24
        min_cell_w = max(90, rgb_image.width // 6)
        min_cell_h = max(90, rgb_image.height // 5)
    else:
        white = (gray > 218) & (channel_range < 48)
        score_threshold = 0.45
        uniform_threshold = 0.72
        separator_threshold = 0.18
        min_cell_w = max(70, rgb_image.width // 7)
        min_cell_h = max(70, rgb_image.height // 6)
    height, width = gray.shape

    col_score = smooth(white.mean(axis=0).astype(np.float32), max(5, width // 320))
    row_score = smooth(white.mean(axis=1).astype(np.float32), max(5, height // 320))
    whole_area = width * height
    min_area = max(900, int(whole_area * _strategy_area_ratio(min_area_ratio, strategy)))

    best_boxes: list[tuple[int, int, int, int]] = []
    best_score = -1.0

    for cols in range(2, 6):
        x_boundaries, x_score = _best_regular_boundaries(col_score, cols, min_cell_w)
        if not x_boundaries:
            continue
        x_widths = np.diff(np.asarray(x_boundaries, dtype=np.float32))
        x_uniform = 1.0 - min(1.0, float(x_widths.std() / max(1.0, x_widths.mean())))
        if x_score < score_threshold or x_uniform < uniform_threshold:
            continue
        for rows in range(1, 5):
            y_boundaries, y_score = _best_regular_boundaries(row_score, rows, min_cell_h)
            if not y_boundaries:
                continue
            y_heights = np.diff(np.asarray(y_boundaries, dtype=np.float32))
            y_uniform = 1.0 - min(1.0, float(y_heights.std() / max(1.0, y_heights.mean())))
            if rows > 1 and (y_score < score_threshold or y_uniform < uniform_threshold):
                continue

            boxes: list[tuple[int, int, int, int]] = []
            for y1, y2 in zip(y_boundaries, y_boundaries[1:]):
                for x1, x2 in zip(x_boundaries, x_boundaries[1:]):
                    pad_x = max(2, (x2 - x1) // 120)
                    pad_y = max(2, (y2 - y1) // 120)
                    raw_box = (
                        min(width, x1 + pad_x),
                        min(height, y1 + pad_y),
                        max(0, x2 - pad_x),
                        max(0, y2 - pad_y),
                    )
                    box = crop_inner_content(gray, raw_box, threshold=35)
                    if looks_like_photo(rgb, box, min_area):
                        boxes.append(box)

            if len(boxes) < 2:
                continue

            total_cells = rows * cols
            fill_ratio = len(boxes) / total_cells
            aspects = np.asarray([(box[2] - box[0]) / max(1, box[3] - box[1]) for box in boxes], dtype=np.float32)
            extreme_ratio = float(((aspects > 2.8) | (aspects < 0.36)).mean())
            max_extreme_ratio = 0.34 if strategy == "aggressive" else 0.12 if strategy == "conservative" else 0.25
            if extreme_ratio > max_extreme_ratio:
                continue
            separator_strength = (x_score + y_score) / 2 if rows > 1 else x_score
            if separator_strength < separator_threshold:
                continue

            score = (
                min(fill_ratio, 1.0) * 3.0
                + separator_strength * 2.0
                + (x_uniform + y_uniform) * 2.0
                + len(boxes) * 0.16
            )
            if score > best_score:
                best_score = score
                if strategy == "conservative" and fill_ratio < 1.0:
                    best_boxes = merge_boxes_without_separator(rgb, boxes)
                elif strategy == "balanced" and fill_ratio < 0.98 and rows >= 3:
                    best_boxes = merge_boxes_without_separator(rgb, boxes)
                else:
                    best_boxes = boxes

    return suppress_nested_and_duplicates(best_boxes)


def _detect_white_grid_boxes(
    rgb_image: Image.Image,
    min_area_ratio: float,
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    """识别白色背景/白色分隔线主导的拼图页面。"""
    strategy = normalize_detection_strategy(detection_strategy)
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    if (gray < 70).mean() > 0.18:
        return []
    if strategy == "aggressive":
        white = (gray > 220) & (channel_range < 42)
        separator_mean = 0.46
        first_col_mean = 0.74
        fallback_col_mean = 0.70
        max_separator_ratio = 0.18
        min_cell_floor = 30
    elif strategy == "conservative":
        white = (gray > 240) & (channel_range < 22)
        separator_mean = 0.65
        first_col_mean = 0.91
        fallback_col_mean = 0.88
        max_separator_ratio = 0.08
        min_cell_floor = 48
    else:
        white = (gray > 232) & (channel_range < 28)
        separator_mean = 0.55
        first_col_mean = 0.86
        fallback_col_mean = 0.82
        max_separator_ratio = 0.12
        min_cell_floor = 35

    height, width = gray.shape
    min_sep = max(4, min(width, height) // 260)
    row_separators = merge_close_runs(runs(white.mean(axis=1) > separator_mean, min_sep), max_gap=min_sep)
    row_separators = [(a, b) for a, b in row_separators if b - a <= height * max_separator_ratio]
    row_mask = np.zeros(height, dtype=bool)
    for y1, y2 in row_separators:
        row_mask[y1:y2] = True

    min_cell_side = max(min_cell_floor, min(width, height) // 18)
    y_intervals = runs(~row_mask, min_cell_side)
    if not y_intervals:
        y_intervals = [(0, height)]

    whole_area = width * height
    min_area = max(900, int(whole_area * _strategy_area_ratio(min_area_ratio, strategy)))
    boxes: list[tuple[int, int, int, int]] = []

    for y1, y2 in y_intervals:
        local_white = white[y1:y2, :]
        col_separators = merge_close_runs(
            runs(local_white.mean(axis=0) > first_col_mean, min_sep),
            max_gap=max(3, min_sep),
        )
        col_separators = [(a, b) for a, b in col_separators if b - a <= width * max_separator_ratio]
        if not col_separators:
            continue

        col_mask = np.zeros(width, dtype=bool)
        for x1, x2 in col_separators:
            col_mask[x1:x2] = True
        x_intervals = runs(~col_mask, min_cell_side)
        for x1, x2 in x_intervals:
            box = crop_inner_content(gray, (x1, y1, x2, y2), threshold=35)
            if looks_like_photo(rgb, box, min_area):
                boxes.append(box)

    if boxes:
        return split_boxes_on_internal_separators(
            rgb,
            suppress_nested_and_duplicates(boxes),
            min_area_ratio,
            strategy,
        )

    col_separators = merge_close_runs(runs(white.mean(axis=0) > fallback_col_mean, min_sep), max_gap=min_sep)
    col_separators = [(a, b) for a, b in col_separators if b - a <= width * max_separator_ratio]
    if len(col_separators) < 1 or len(row_separators) < 1:
        return []

    col_mask = np.zeros(width, dtype=bool)
    row_mask = np.zeros(height, dtype=bool)
    for x1, x2 in col_separators:
        col_mask[x1:x2] = True
    for y1, y2 in row_separators:
        row_mask[y1:y2] = True

    x_intervals = runs(~col_mask, min_cell_side)
    y_intervals = runs(~row_mask, min_cell_side)
    if len(x_intervals) * len(y_intervals) < 2:
        return []

    for y1, y2 in y_intervals:
        for x1, x2 in x_intervals:
            box = crop_inner_content(gray, (x1, y1, x2, y2), threshold=35)
            if looks_like_photo(rgb, box, min_area):
                boxes.append(box)

    return split_boxes_on_internal_separators(
        rgb,
        suppress_nested_and_duplicates(boxes),
        min_area_ratio,
        strategy,
    )


def _detect_black_frame_boxes(
    rgb_image: Image.Image,
    min_area_ratio: float,
) -> list[tuple[int, int, int, int]]:
    """识别黑色相框/深色底板中嵌入的照片区域。"""
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    height, width = gray.shape

    edge = max(20, min(width, height) // 25)
    samples = np.concatenate(
        [
            rgb[:edge, :, :].reshape(-1, 3),
            rgb[-edge:, :, :].reshape(-1, 3),
            rgb[:, :edge, :].reshape(-1, 3),
            rgb[:, -edge:, :].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)
    sample_gray = (
        samples[:, 0] * 0.299 + samples[:, 1] * 0.587 + samples[:, 2] * 0.114
    )
    sample_range = samples.max(axis=1) - samples.min(axis=1)
    matte_samples = samples[(sample_gray < 175) & (sample_range < 55)]
    if matte_samples.size == 0:
        matte_samples = samples
    bg = np.median(matte_samples, axis=0)
    diff = np.sqrt(np.sum((rgb.astype(np.float32) - bg.reshape(1, 1, 3)) ** 2, axis=2))

    dark_frame = (diff < 34) & (gray < 180) & (channel_range < 70)
    dark_frame |= (gray < 42) & (channel_range < 55)
    if float(dark_frame.mean()) < 0.10:
        return []

    whole_area = width * height
    min_area = max(900, int(whole_area * min_area_ratio))
    min_pixels = max(260, int(min_area * 0.16))

    photo_mask = (~dark_frame & (diff > 30)) | (channel_range > 44) | (gray > 185)
    photo_mask &= ~((gray < 38) & (channel_range < 48))
    photo_mask = close_mask(photo_mask, close_size=max(3, min(width, height) // 620))
    raw_boxes = connected_components(photo_mask, min_pixels)

    boxes: list[tuple[int, int, int, int]] = []
    for x1, y1, x2, y2, _count in raw_boxes:
        raw_box = (x1, y1, x2, y2)
        raw_area = _box_area(raw_box)
        raw_width = x2 - x1
        raw_height = y2 - y1
        if raw_area > whole_area * 0.45 or (
            raw_width > width * 0.72 and raw_height > height * 0.24
        ):
            continue
        if raw_width < max(45, width // 35) or raw_height < max(45, height // 35):
            continue
        refined = crop_inner_content(gray, raw_box, threshold=45)
        crop = rgb[refined[1] : refined[3], refined[0] : refined[2]]
        crop_gray, crop_range = gray_and_channel_range(crop)
        if crop_gray.size and float(crop_gray.mean()) > 220 and float(crop_gray.std()) < 48 and float(crop_range.mean()) < 28:
            continue
        if looks_like_photo(rgb, refined, min_area):
            boxes.append(refined)

    boxes = suppress_nested_and_duplicates(boxes)
    boxes = _remove_high_overlap_fragments(boxes)
    boxes = _merge_touching_fragments(boxes)
    return boxes



def _box_area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def _remove_high_overlap_fragments(
    boxes: list[tuple[int, int, int, int]],
    overlap_threshold: float = 0.62,
) -> list[tuple[int, int, int, int]]:
    """移除被同一照片主体高度覆盖的局部碎片框。

    黑框拼图和强边缘照片里，人物脸部、衣帽或相框内缘有时会生成一个局部候选框。
    这类框通常与更大的照片框有大比例重叠，保留大框更符合分割预期。
    """
    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=_box_area, reverse=True):
        area = _box_area(box)
        if any(_intersection_area(box, other) / max(1, area) > overlap_threshold for other in kept):
            continue
        kept.append(box)
    return sorted(kept, key=lambda item: (item[1], item[0]))


def _merge_touching_fragments(
    boxes: list[tuple[int, int, int, int]],
    max_gap: int = 12,
) -> list[tuple[int, int, int, int]]:
    """合并几乎贴边且高度/宽度重叠很大的同图碎片。"""
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(merged):
            for j, b in enumerate(merged[i + 1 :], start=i + 1):
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                min_h = min(a[3] - a[1], b[3] - b[1])
                if overlap_y > min_h * 0.78:
                    left, right = (a, b) if a[0] <= b[0] else (b, a)
                    gap = right[0] - left[2]
                    if -max_gap <= gap <= max_gap:
                        merged[i] = (
                            min(a[0], b[0]),
                            min(a[1], b[1]),
                            max(a[2], b[2]),
                            max(a[3], b[3]),
                        )
                        del merged[j]
                        changed = True
                        break

                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                min_w = min(a[2] - a[0], b[2] - b[0])
                if overlap_x > min_w * 0.78:
                    top, bottom = (a, b) if a[1] <= b[1] else (b, a)
                    gap = bottom[1] - top[3]
                    if -max_gap <= gap <= max_gap:
                        merged[i] = (
                            min(a[0], b[0]),
                            min(a[1], b[1]),
                            max(a[2], b[2]),
                            max(a[3], b[3]),
                        )
                        del merged[j]
                        changed = True
                        break
            if changed:
                break
    return suppress_nested_and_duplicates(merged)


def _looks_like_page_frame(box: tuple[int, int, int, int], width: int, height: int) -> bool:
    return _box_area(box) > width * height * 0.92 and box[0] <= width * 0.03 and box[1] <= height * 0.03


def _filter_conservative_recovery_boxes(
    boxes: list[tuple[int, int, int, int]],
    width: int,
    height: int,
) -> list[tuple[int, int, int, int]]:
    """保守主流程失败后的收口过滤：保留明显照片候选，丢弃整页框和小纹理碎片。"""
    whole_area = width * height
    filtered: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if _looks_like_page_frame(box, width, height):
            continue
        area = _box_area(box)
        if area < whole_area * 0.006 or area > whole_area * 0.70:
            continue
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        aspect = box_width / max(1, box_height)
        if aspect < 0.22 or aspect > 5.8:
            continue
        filtered.append(box)

    filtered = _remove_high_overlap_fragments(filtered, overlap_threshold=0.58)
    if not filtered:
        return []

    largest_area = max(_box_area(box) for box in filtered)
    keep_floor = max(whole_area * 0.010, largest_area * 0.25)
    filtered = [box for box in filtered if _box_area(box) >= keep_floor]
    if not filtered:
        return []

    filtered = _merge_touching_fragments(filtered, max_gap=18)
    return conservative_filter_boxes(filtered, width, height)


def _recover_conservative_boxes(
    rgb_image: Image.Image,
    min_area_ratio: float,
    actual_mode: str,
) -> list[tuple[int, int, int, int]]:
    """保守模式最后兜底。

    只在强边界/明确网格都失败时启用，用稍宽松的边界候选恢复灰底、黄底相册页；
    不使用普通内容掩膜，避免把整张扫描页当作照片。
    """
    modes = tuple(dict.fromkeys((actual_mode, "gray", "white", "auto")))
    candidates: list[tuple[int, int, int, int]] = []
    for recovery_strategy in ("balanced", "aggressive"):
        for mode in modes:
            candidates.extend(
                box
                for box in _detect_edge_boundary_boxes(
                    rgb_image,
                    min_area_ratio,
                    mode,
                    recovery_strategy,
                )
                if not _looks_like_page_frame(box, rgb_image.width, rgb_image.height)
            )
    return _filter_conservative_recovery_boxes(candidates, rgb_image.width, rgb_image.height)


def _improve_conservative_boxes(
    rgb_image: Image.Image,
    boxes: list[tuple[int, int, int, int]],
    min_area_ratio: float,
    background_mode: str,
) -> list[tuple[int, int, int, int]]:
    """保守模式结果质量检查。

    如果主流程只框到了照片内部很小的区域，尝试用恢复检测换成更大的外层照片框。
    """
    boxes = conservative_filter_boxes(boxes, rgb_image.width, rgb_image.height)
    whole_area = rgb_image.width * rgb_image.height
    max_box_area = max((_box_area(box) for box in boxes), default=0)
    if boxes and max_box_area >= whole_area * 0.04:
        return boxes

    mode = normalize_background_mode(background_mode)
    actual_mode = estimate_background_mode(rgb_image) if mode == "auto" else mode
    recovered = _recover_conservative_boxes(rgb_image, min_area_ratio, actual_mode)
    if not recovered:
        return boxes

    max_recovered_area = max(_box_area(box) for box in recovered)
    if not boxes or max_recovered_area > max_box_area * 2.0:
        return recovered
    return boxes


def _matte_mask_for_dark_page(rgb: np.ndarray) -> np.ndarray:
    gray, channel_range = gray_and_channel_range(rgb)
    height, width = gray.shape
    edge = max(20, min(width, height) // 25)
    samples = np.concatenate(
        [
            rgb[:edge, :, :].reshape(-1, 3),
            rgb[-edge:, :, :].reshape(-1, 3),
            rgb[:, :edge, :].reshape(-1, 3),
            rgb[:, -edge:, :].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)
    sample_gray = samples[:, 0] * 0.299 + samples[:, 1] * 0.587 + samples[:, 2] * 0.114
    sample_range = samples.max(axis=1) - samples.min(axis=1)
    matte_samples = samples[(sample_gray < 180) & (sample_range < 60)]
    if matte_samples.size == 0:
        matte_samples = samples
    bg = np.median(matte_samples, axis=0)
    diff = np.sqrt(np.sum((rgb.astype(np.float32) - bg.reshape(1, 1, 3)) ** 2, axis=2))
    return ((diff < 42) & (gray < 190) & (channel_range < 78)) | ((gray < 42) & (channel_range < 55))


def _expand_boxes_to_matte(
    rgb: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """把照片内部候选框扩展到周围灰/黑底板边界。"""
    if not boxes:
        return []
    matte = _matte_mask_for_dark_page(rgb)
    height, width = matte.shape
    expanded: list[tuple[int, int, int, int]] = []

    for x1, y1, x2, y2 in boxes:
        bw = max(4, min(x2 - x1, y2 - y1) // 80)
        max_expand_x = max(20, int((x2 - x1) * 0.85))
        max_expand_y = max(20, int((y2 - y1) * 0.85))
        left = x1
        while left > max(0, x1 - max_expand_x):
            band = matte[max(0, y1):min(height, y2), max(0, left - bw):left]
            if band.size and float(band.mean()) > 0.58:
                break
            left -= bw
        right = x2
        while right < min(width, x2 + max_expand_x):
            band = matte[max(0, y1):min(height, y2), right:min(width, right + bw)]
            if band.size and float(band.mean()) > 0.58:
                break
            right += bw
        top = y1
        while top > max(0, y1 - max_expand_y):
            band = matte[max(0, top - bw):top, max(0, left):min(width, right)]
            if band.size and float(band.mean()) > 0.58:
                break
            top -= bw
        bottom = y2
        while bottom < min(height, y2 + max_expand_y):
            band = matte[bottom:min(height, bottom + bw), max(0, left):min(width, right)]
            if band.size and float(band.mean()) > 0.58:
                break
            bottom += bw
        expanded.append((max(0, left), max(0, top), min(width, right), min(height, bottom)))

    return suppress_nested_and_duplicates(_merge_touching_fragments(_remove_high_overlap_fragments(expanded)))


def _edge_support_score(edge_mask: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """计算候选框四边附近的边缘支撑度。

    照片边界通常是直线，即使不完全水平/垂直，轴向外接框四周也应有较集中的边缘响应。
    该分数用于过滤照片内部纹理、文字、衣服褶皱等非照片边界的误检轮廓。
    """
    x1, y1, x2, y2 = box
    h, w = edge_mask.shape
    x1 = max(0, min(w - 1, x1))
    x2 = max(x1 + 1, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(y1 + 1, min(h, y2))
    bw = max(2, min(x2 - x1, y2 - y1) // 45)
    top = edge_mask[y1 : min(y2, y1 + bw), x1:x2]
    bottom = edge_mask[max(y1, y2 - bw) : y2, x1:x2]
    left = edge_mask[y1:y2, x1 : min(x2, x1 + bw)]
    right = edge_mask[y1:y2, max(x1, x2 - bw) : x2]
    densities = [float(side.mean()) if side.size else 0.0 for side in (top, bottom, left, right)]
    strong_sides = sum(value > 0.020 for value in densities)
    return float(sum(densities) / 4.0 + strong_sides * 0.035)


def _background_difference_mask(rgb: np.ndarray, actual_mode: str) -> np.ndarray:
    """用外缘背景样本做弱前景掩膜。

    背景可能不均匀，因此这里不用单一颜色精确扣图，只把它作为 Canny/轮廓检测的辅助信号。
    真正是否保留候选框仍由直边、闭合轮廓、面积、长宽比和照片内容判断决定。
    """
    return accelerated_background_difference_mask(rgb, actual_mode)


def _detect_edge_boundary_boxes(
    rgb_image: Image.Image,
    min_area_ratio: float,
    actual_mode: str,
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    """边界优先的照片框检测。

    旧逻辑主要依赖“白底/黑底像素”分割，背景是灰色、米白、阴影不均或桌面纹理时容易误识别。
    本函数把照片本体当作目标：先找 Canny 边缘和闭合轮廓，再用外接矩形、边缘支撑度、面积、
    长宽比和照片内容过滤。背景颜色只调整阈值，不再决定候选框是否成立。
    """
    try:
        import cv2
    except Exception:
        return []

    strategy = normalize_detection_strategy(detection_strategy)
    strategy_config = split_strategy_config(strategy)
    rgb = np.asarray(rgb_image.convert("RGB"))
    height, width = rgb.shape[:2]
    whole_area = width * height
    effective_min_area_ratio = _strategy_area_ratio(min_area_ratio, strategy)
    min_area = max(900, int(whole_area * effective_min_area_ratio))
    if width < 80 or height < 80:
        return []

    gray, blur = gray_and_blur(rgb, kernel_size=5)

    # 自动 Canny 阈值：灰/米白/不均匀底色降低阈值，避免照片边缘太淡时漏检。
    median = float(np.median(blur))
    if actual_mode in {"white", "gray"}:
        lower = int(max(12, median * 0.45))
        upper = int(min(210, max(lower + 35, median * 1.35)))
    elif actual_mode == "black":
        lower = int(max(18, median * 0.35))
        upper = int(min(190, max(lower + 40, median * 1.25)))
    else:
        lower = int(max(18, median * 0.50))
        upper = int(min(210, max(lower + 40, median * 1.45)))
    lower = int(max(strategy_config.canny_min_lower, lower * strategy_config.canny_lower_factor))
    upper = int(
        min(
            strategy_config.canny_max_upper,
            max(lower + strategy_config.canny_min_gap, upper * strategy_config.canny_upper_factor),
        )
    )
    edges = canny_edges(blur, lower, upper)

    # 直边可能被老照片纹理、反光或扫描噪声打断，闭运算/轻微膨胀用于连接边界线段。
    k = max(3, min(width, height) // 260)
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    closed_edges = close_and_dilate_edges(
        edges,
        kernel,
        close_iterations=strategy_config.edge_close_iterations,
        dilate_iterations=strategy_config.edge_dilate_iterations,
    )

    # 背景差异只作为辅助，让轮廓更容易闭合；后续仍要求候选框有边缘支撑。
    fg = _background_difference_mask(rgb, actual_mode)
    fg = close_mask(fg, close_size=max(5, min(width, height) // 180)).astype(np.uint8) * 255
    combined = cv2.bitwise_or(closed_edges, fg)
    combined = close_u8(combined, kernel, iterations=1)

    edge_bool = edges > 0
    boxes: list[tuple[int, int, int, int]] = []

    for source_mask in (closed_edges, combined):
        contours, _hierarchy = cv2.findContours(source_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            contour_area = float(cv2.contourArea(contour))
            if contour_area < min_area * strategy_config.contour_area_factor:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            box = (int(x), int(y), int(x + w), int(y + h))
            area = w * h
            if area < min_area or w < strategy_config.min_side or h < strategy_config.min_side:
                continue
            if area > whole_area * 0.96:
                # 整张扫描页外框不是照片本体。
                continue
            aspect = w / max(1, h)
            if aspect < strategy_config.aspect_min or aspect > strategy_config.aspect_max:
                continue

            rect = cv2.minAreaRect(contour)
            rect_w, rect_h = rect[1]
            rect_area = max(1.0, float(rect_w * rect_h))
            contour_fill = contour_area / max(1.0, float(area))
            min_rect_fill = contour_area / rect_area
            approx = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
            side_score = _edge_support_score(edge_bool, box)

            # 允许轻微倾斜、圆角相纸、扫描边缘缺口；但必须像一个“大而直的照片边界”。
            if len(approx) > 14 and side_score < strategy_config.approx_side_limit:
                continue
            if contour_fill < 0.28 and min_rect_fill < 0.22 and side_score < strategy_config.weak_shape_side_limit:
                continue
            if not looks_like_photo(rgb, box, min_area):
                continue

            pad = max(1, min(w, h) // 180)
            boxes.append((max(0, x - pad), max(0, y - pad), min(width, x + w + pad), min(height, y + h + pad)))

    boxes = suppress_nested_and_duplicates(boxes)
    if len(boxes) <= 1:
        return boxes

    # 如果一个大框几乎包含多个已识别小框，优先保留小框，避免把整页当成单张照片。
    result: list[tuple[int, int, int, int]] = []
    for box in boxes:
        area = _box_area(box)
        contained = [other for other in boxes if other != box and _intersection_area(box, other) / max(1, _box_area(other)) > 0.88]
        contained_area = sum(_box_area(other) for other in contained)
        if len(contained) >= 2 and contained_area > area * 0.35:
            continue
        result.append(box)
    return suppress_nested_and_duplicates(result)


def _detect_photo_boxes_on_rgb(
    rgb_image: Image.Image,
    dark_threshold: int,
    min_area_ratio: float,
    background_mode: str = "auto",
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    strategy = normalize_detection_strategy(detection_strategy)
    strategy_config = split_strategy_config(strategy)
    # 先缩小超大图做检测，再把检测框映射回原图，减少大 TIFF 的计算量。
    max_detect_side = 2200
    if max(rgb_image.width, rgb_image.height) > max_detect_side:
        scale = max_detect_side / max(rgb_image.width, rgb_image.height)
        small_size = (max(1, int(rgb_image.width * scale)), max(1, int(rgb_image.height * scale)))
        small_image = rgb_image.resize(small_size, Image.Resampling.LANCZOS)
        small_boxes = _detect_photo_boxes_on_rgb(small_image, dark_threshold, min_area_ratio, background_mode, strategy)
        scaled_boxes: list[tuple[int, int, int, int]] = []
        for x1, y1, x2, y2 in small_boxes:
            scaled_boxes.append(
                (
                    max(0, int(round(x1 / scale))),
                    max(0, int(round(y1 / scale))),
                    min(rgb_image.width, int(round(x2 / scale))),
                    min(rgb_image.height, int(round(y2 / scale))),
                )
            )
        full_rgb = np.asarray(rgb_image)
        scaled_boxes = suppress_nested_and_duplicates(scaled_boxes)
        full_gray, _full_channel_range = gray_and_channel_range(full_rgb)
        actual_mode = estimate_background_mode(rgb_image) if normalize_background_mode(background_mode) == "auto" else normalize_background_mode(background_mode)
        if (
            len(scaled_boxes) <= 1
            or (actual_mode in {"black", "gray"} and len(scaled_boxes) < 4)
            or any(_looks_like_page_frame(box, rgb_image.width, rgb_image.height) for box in scaled_boxes)
        ):
            if strategy == "aggressive":
                fallback_modes = tuple(dict.fromkeys((actual_mode, "auto", "white", "gray")))
            else:
                fallback_modes = ("auto", "gray") if actual_mode == "black" else (actual_mode,)
            fallback_edge_boxes = []
            for fallback_mode in fallback_modes:
                candidate_boxes = [
                    box
                    for box in _detect_edge_boundary_boxes(rgb_image, min_area_ratio, fallback_mode, strategy)
                    if not _looks_like_page_frame(box, rgb_image.width, rgb_image.height)
                ]
                if len(candidate_boxes) > len(fallback_edge_boxes):
                    fallback_edge_boxes = candidate_boxes
            if len(fallback_edge_boxes) >= 2:
                fallback_edge_boxes = _remove_high_overlap_fragments(
                    fallback_edge_boxes,
                    overlap_threshold=strategy_config.overlap_remove_threshold,
                )
                if actual_mode in {"black", "gray"}:
                    return _merge_touching_fragments(
                        fallback_edge_boxes,
                        max_gap=_strategy_dark_merge_gap(strategy),
                    )
                split_boxes = split_boxes_on_internal_separators(full_rgb, fallback_edge_boxes, min_area_ratio, strategy)
                if strategy == "conservative":
                    split_boxes = merge_boxes_without_separator(full_rgb, split_boxes)
                return merge_cross_grid_fragments(full_rgb, split_boxes)
        if (full_gray < 70).mean() > 0.18:
            if strategy == "aggressive":
                split_boxes = split_boxes_on_internal_separators(full_rgb, scaled_boxes, min_area_ratio, strategy)
                return merge_cross_grid_fragments(full_rgb, split_boxes)
            return merge_dark_layout_fragments(full_rgb, scaled_boxes)
        split_boxes = split_boxes_on_internal_separators(full_rgb, scaled_boxes, min_area_ratio, strategy)
        if strategy == "conservative":
            split_boxes = merge_boxes_without_separator(full_rgb, split_boxes)
        return merge_cross_grid_fragments(full_rgb, split_boxes)

    rgb = np.asarray(rgb_image)
    mode = normalize_background_mode(background_mode)
    actual_mode = estimate_background_mode(rgb_image) if mode == "auto" else mode
    overlap_threshold = strategy_config.overlap_remove_threshold

    # 第一优先级：深色相框页面。先按非黑相框区域拆开，避免把整张黑框拼图当成一个大框。
    if actual_mode == "black":
        black_frame_boxes = _detect_black_frame_boxes(rgb_image, min_area_ratio)
        if len(black_frame_boxes) >= 2:
            return merge_dark_layout_fragments(rgb, black_frame_boxes)
        dark_edge_boxes = [
            box
            for box in _detect_edge_boundary_boxes(rgb_image, min_area_ratio, "auto", strategy)
            if not _looks_like_page_frame(box, rgb_image.width, rgb_image.height)
        ]
        if len(dark_edge_boxes) >= 2:
            return _merge_touching_fragments(
                _remove_high_overlap_fragments(dark_edge_boxes, overlap_threshold=overlap_threshold),
                max_gap=_strategy_dark_merge_gap(strategy),
            )

    # 第二优先级：明确的扫描/拼图网格。自动底色判断可能把发黄白底识别成灰底，
    # 因此这里无条件先尝试网格；网格不足时再进入边界优先算法。
    grid_boxes = _detect_white_grid_boxes(rgb_image, min_area_ratio, strategy)
    regular_grid_boxes = _detect_regular_grid_boxes(rgb_image, min_area_ratio, strategy)
    if len(grid_boxes) >= 2 or len(regular_grid_boxes) >= 3:
        selected = grid_boxes if len(grid_boxes) >= len(regular_grid_boxes) else regular_grid_boxes
        selected = _remove_high_overlap_fragments(selected, overlap_threshold=overlap_threshold)
        if len(selected) >= 2:
            if actual_mode == "black":
                return selected
            split_boxes = split_boxes_on_internal_separators(rgb, selected, min_area_ratio, strategy)
            if strategy == "conservative":
                split_boxes = merge_boxes_without_separator(rgb, split_boxes)
            return merge_cross_grid_fragments(rgb, split_boxes)

    # 第二优先级：边界检测。照片边缘通常是直线和闭合轮廓，背景颜色不均匀时也比纯颜色分割更稳。
    edge_boxes = _detect_edge_boundary_boxes(rgb_image, min_area_ratio, actual_mode, strategy)
    edge_boxes = [box for box in edge_boxes if not _looks_like_page_frame(box, rgb_image.width, rgb_image.height)]
    if len(edge_boxes) >= 2:
        edge_boxes = _remove_high_overlap_fragments(edge_boxes, overlap_threshold=overlap_threshold)
        if actual_mode in {"black", "gray"}:
            if strategy == "aggressive":
                return _merge_touching_fragments(edge_boxes, max_gap=_strategy_dark_merge_gap(strategy))
            return merge_dark_layout_fragments(rgb, edge_boxes)
        split_boxes = split_boxes_on_internal_separators(rgb, edge_boxes, min_area_ratio, strategy)
        if strategy == "conservative":
            split_boxes = merge_boxes_without_separator(rgb, split_boxes)
        return merge_cross_grid_fragments(rgb, split_boxes)

    # 第三优先级：底色明确时再次尝试颜色专用算法。灰底/杂色底保持边缘优先，避免纯颜色误分割。
    if actual_mode == "white":
        grid_boxes = _detect_white_grid_boxes(rgb_image, min_area_ratio, strategy)
        regular_grid_boxes = _detect_regular_grid_boxes(rgb_image, min_area_ratio, strategy)
        if len(grid_boxes) >= 2 or len(regular_grid_boxes) >= 3:
            selected = grid_boxes if len(grid_boxes) >= len(regular_grid_boxes) else regular_grid_boxes
            selected = _remove_high_overlap_fragments(selected, overlap_threshold=overlap_threshold)
            split_boxes = split_boxes_on_internal_separators(rgb, selected, min_area_ratio, strategy)
            if strategy == "conservative":
                split_boxes = merge_boxes_without_separator(rgb, split_boxes)
            return merge_cross_grid_fragments(rgb, split_boxes)

    gray, channel_range = gray_and_channel_range(rgb)

    # 第三优先级：通用内容掩膜。这里仍按底色微调阈值，但只作为边界算法失败后的兜底。
    if actual_mode == "white":
        plain_white = (gray > 210) & (channel_range < 55)
        frame_highlight = (gray < 105) & (channel_range < 18)
        interesting = (gray > dark_threshold) & ~plain_white & ~frame_highlight
    elif actual_mode == "black":
        plain_white = (gray > 238) & (channel_range < 18)
        dark_background = (gray < max(55, dark_threshold + 8)) & (channel_range < 45)
        interesting = ~dark_background & ~plain_white
    elif actual_mode == "gray":
        # 灰底/杂色底不做强颜色扣除，避免背景阴影和照片暗部混淆；主要保留明显不同于底板的内容。
        texture_or_color = (channel_range > 42) | (gray < 55) | (gray > 225)
        interesting = _background_difference_mask(rgb, "gray") | texture_or_color
    else:
        plain_white = (gray > 225) & (channel_range < 22)
        frame_highlight = (gray < 115) & (channel_range < 18)
        interesting = (gray > dark_threshold) & ~plain_white & ~frame_highlight

    if len(edge_boxes) == 1 and strategy_config.allow_single_edge_seed:
        # 只有一个高质量边界框时先保留，后面与兜底结果一起去重，防止单张照片模式漏检。
        boxes_seed = list(edge_boxes)
    else:
        boxes_seed = []
    if not strategy_config.run_content_fallback:
        recovered = _recover_conservative_boxes(rgb_image, min_area_ratio, actual_mode)
        if recovered:
            return recovered
        return finalize_strategy_boxes(boxes_seed, rgb_image.width, rgb_image.height, strategy)
    mask = close_mask(interesting, close_size=strategy_config.content_close_size)

    whole_area = rgb_image.width * rgb_image.height
    effective_min_area_ratio = _strategy_area_ratio(min_area_ratio, strategy)
    min_pixels = max(220, int(whole_area * effective_min_area_ratio * strategy_config.min_component_pixels_factor))
    min_area = max(900, int(whole_area * effective_min_area_ratio))

    raw_boxes = connected_components(mask, min_pixels)
    boxes: list[tuple[int, int, int, int]] = list(boxes_seed)
    for x1, y1, x2, y2, _count in raw_boxes:
        for split_box in split_box_on_empty_gaps(interesting, (x1, y1, x2, y2), min_side=35):
            refined = crop_inner_content(gray, split_box, dark_threshold)
            if looks_like_photo(rgb, refined, min_area):
                boxes.append(refined)

    boxes = suppress_nested_and_duplicates(boxes)
    if (gray < 70).mean() > 0.18:
        if strategy == "aggressive":
            return _merge_touching_fragments(boxes, max_gap=_strategy_dark_merge_gap(strategy))
        return merge_dark_layout_fragments(rgb, boxes)
    split_boxes = split_boxes_on_internal_separators(rgb, boxes, min_area_ratio, strategy)
    if strategy == "conservative":
        split_boxes = merge_boxes_without_separator(rgb, split_boxes)
    return merge_cross_grid_fragments(rgb, split_boxes)


def split_image(
    image: Image.Image,
    dark_threshold: int,
    min_area_ratio: float,
    auto_deskew: bool = False,
    deskew_min_score_gain: float = 1.04,
    background_mode: str = "auto",
    detection_strategy: str = "balanced",
) -> tuple[Image.Image, list[tuple[int, int, int, int]], float]:
    # 默认不在整张拼图上矫正倾斜；每张照片的倾斜角可能不同，分割后再处理更稳。
    if auto_deskew:
        processed_image, angle = auto_deskew_image(image, min_score_gain=deskew_min_score_gain)
    else:
        processed_image = ImageOps.exif_transpose(image).convert("RGB")
        angle = 0.0
    boxes = _detect_photo_boxes_on_rgb(processed_image, dark_threshold, min_area_ratio, background_mode, detection_strategy)
    if normalize_detection_strategy(detection_strategy) == "conservative":
        boxes = _improve_conservative_boxes(processed_image, boxes, min_area_ratio, background_mode)
    else:
        boxes = finalize_strategy_boxes(boxes, processed_image.width, processed_image.height, detection_strategy)
    return processed_image, boxes, angle


def detect_photo_boxes(
    image: Image.Image,
    dark_threshold: int,
    min_area_ratio: float,
    background_mode: str = "auto",
    detection_strategy: str = "balanced",
) -> list[tuple[int, int, int, int]]:
    _processed_image, boxes, _angle = split_image(
        image,
        dark_threshold,
        min_area_ratio,
        background_mode=background_mode,
        detection_strategy=detection_strategy,
    )
    return boxes
