from __future__ import annotations

import numpy as np


def runs(mask: np.ndarray, min_length: int) -> list[tuple[int, int]]:
    """返回一维布尔数组中连续 True 区间，区间格式为 [start, end)。"""
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= min_length:
                result.append((start, index))
            start = None
    if start is not None and len(mask) - start >= min_length:
        result.append((start, len(mask)))
    return result


def merge_close_runs(run_list: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """合并间隔很小的连续区间，用于把断裂的扫描白线/分隔线视为同一条线。"""
    if not run_list:
        return []
    merged = [run_list[0]]
    for start, end in run_list[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= max_gap:
            merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def smooth(values: np.ndarray, window: int) -> np.ndarray:
    """一维均值平滑，减少扫描噪点对分隔线评分的影响。"""
    if window <= 1:
        return values
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(values, kernel, mode="same")


def suppress_nested_and_duplicates(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """删除高度重叠的嵌套框和重复框，保留面积较大的候选照片框。"""
    def area(box: tuple[int, int, int, int]) -> int:
        return (box[2] - box[0]) * (box[3] - box[1])

    def intersection(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        return max(0, x2 - x1) * max(0, y2 - y1)

    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=area, reverse=True):
        box_area = area(box)
        if any(intersection(box, other) / max(1, min(box_area, area(other))) > 0.82 for other in kept):
            continue
        kept.append(box)

    return sorted(kept, key=lambda b: (b[1], b[0]))
