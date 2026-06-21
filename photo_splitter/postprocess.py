from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .geometry import runs
from .runtime_backend import gray_and_channel_range

try:
    import cv2
except Exception:  # pragma: no cover - OpenCV may be absent in minimal installs.
    cv2 = None


def normalize_background_mode(background_mode: str | None) -> str:
    """标准化底色选项，防止 GUI/CLI 传入异常值导致分支失效。"""
    mode = str(background_mode or "auto").lower()
    return mode if mode in {"auto", "white", "gray", "black"} else "auto"


def estimate_background_mode(image: Image.Image) -> str:
    """根据源图外缘估计扫描/拼图底色，返回 white、gray、black 或 auto。

    这里不要求背景完全均匀：只取四周窄边做稳健统计，用中位数、低/高亮占比和色差判断
    “大概率属于哪类底色”。判断失败时返回 auto，后续检测会走通用边界优先策略。
    底色判断只用于选择阈值和后处理，不再作为提取照片的唯一依据。
    """
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width < 20 or height < 20:
        return "auto"
    edge = max(6, min(width, height) // 32)
    samples = np.concatenate(
        [
            rgb[:edge, :, :].reshape(-1, 3),
            rgb[-edge:, :, :].reshape(-1, 3),
            rgb[:, :edge, :].reshape(-1, 3),
            rgb[:, -edge:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    gray = 0.299 * samples[:, 0].astype(np.float32) + 0.587 * samples[:, 1].astype(np.float32) + 0.114 * samples[:, 2].astype(np.float32)
    channel_range = samples.max(axis=1).astype(np.int16) - samples.min(axis=1).astype(np.int16)
    median_gray = float(np.median(gray))
    gray_iqr = float(np.percentile(gray, 75) - np.percentile(gray, 25))
    low_chroma_ratio = float((channel_range < 42).mean())
    black_ratio = float((gray < 70).mean())
    white_ratio = float(((gray > 218) & (channel_range < 55)).mean())
    gray_ratio = float(((gray >= 70) & (gray <= 218) & (channel_range < 55)).mean())

    if black_ratio > 0.45 and median_gray < 95:
        return "black"
    # “不是纯白的白色”按 white 处理，阈值放宽到 218，并要求低色差占比较高。
    if white_ratio > 0.42 and median_gray > 198 and low_chroma_ratio > 0.55:
        return "white"
    # 灰底、旧扫描仪底板、阴影不均匀底色都归入 gray，用边界算法，不强依赖颜色分割。
    if gray_ratio > 0.45 and 75 <= median_gray <= 210 and low_chroma_ratio > 0.45 and gray_iqr < 90:
        return "gray"
    return "auto"


def trim_white_border(image: Image.Image, max_trim_ratio: float = 0.28, white_threshold: int = 225) -> Image.Image:
    """裁掉普通白边。

    只从图像外缘往内剥离，避免把照片内部的天空、墙面、白衣服等浅色内容误裁掉。
    max_trim_ratio 限制最大裁切比例，防止异常图片被裁成很小一块。
    """
    rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    white_threshold = max(180, min(250, int(white_threshold)))
    edge_white_x = (gray > white_threshold) & (channel_range < 55)
    edge_white_y = (gray > min(255, white_threshold + 10)) & (channel_range < 38)
    height, width = edge_white_x.shape
    if width < 30 or height < 30:
        return rgb_image

    max_x_trim = int(width * max_trim_ratio)
    max_y_trim = int(height * min(max_trim_ratio, 0.14))
    left = 0
    while left < max_x_trim and edge_white_x[:, left].mean() > 0.68:
        left += 1
    right = width
    while right > width - max_x_trim and edge_white_x[:, right - 1].mean() > 0.68:
        right -= 1
    top = 0
    while top < max_y_trim and edge_white_y[top, :].mean() > 0.82:
        top += 1
    bottom = height
    while bottom > height - max_y_trim and edge_white_y[bottom - 1, :].mean() > 0.82:
        bottom -= 1

    min_trim_x = max(3, width // 180)
    min_trim_y = max(3, height // 180)
    if left < min_trim_x:
        left = 0
    if width - right < min_trim_x:
        right = width
    if top < min_trim_y:
        top = 0
    if height - bottom < min_trim_y:
        bottom = height

    if right - left < width * 0.35 or bottom - top < height * 0.35:
        return rgb_image
    if left == 0 and right == width and top == 0 and bottom == height:
        return rgb_image
    return rgb_image.crop((left, top, right, bottom))


def trim_large_white_border(image: Image.Image) -> Image.Image:
    """裁掉扫描件里接近纯白的大边框。

    条件比 trim_white_border 更严格，只处理接近纯白且低色差的大面积空白。
    """
    rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    blank = (gray > 248) & (channel_range < 18)
    height, width = blank.shape
    if width < 30 or height < 30:
        return rgb_image

    max_x_trim = int(width * 0.42)
    max_y_trim = int(height * 0.18)
    left = 0
    while left < max_x_trim and blank[:, left].mean() > 0.96:
        left += 1
    right = width
    while right > width - max_x_trim and blank[:, right - 1].mean() > 0.96:
        right -= 1
    top = 0
    while top < max_y_trim and blank[top, :].mean() > 0.98:
        top += 1
    bottom = height
    while bottom > height - max_y_trim and blank[bottom - 1, :].mean() > 0.98:
        bottom -= 1

    if left == 0 and right == width and top == 0 and bottom == height:
        return rgb_image

    padding_x = max(4, width // 220)
    padding_y = max(4, height // 220)
    left = max(0, left - padding_x)
    right = min(width, right + padding_x)
    top = max(0, top - padding_y)
    bottom = min(height, bottom + padding_y)

    if right - left < width * 0.12 or bottom - top < height * 0.12:
        return rgb_image
    if left == 0 and right == width and top == 0 and bottom == height:
        return rgb_image
    return rgb_image.crop((left, top, right, bottom))


def trim_dark_border(image: Image.Image, max_trim_ratio: float = 0.20) -> Image.Image:
    """裁掉黑框照片外缘的深色边。

    只检查图片最外侧连续深色低色差区域，避免把照片内部阴影或黑衣服误裁掉。
    """
    rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    dark = (gray < 52) & (channel_range < 48)
    height, width = dark.shape
    if width < 30 or height < 30 or float(dark.mean()) < 0.01:
        return rgb_image

    max_x_trim = int(width * max_trim_ratio)
    max_y_trim = int(height * max_trim_ratio)
    left = 0
    while left < max_x_trim and dark[:, left].mean() > 0.45:
        left += 1
    right = width
    while right > width - max_x_trim and dark[:, right - 1].mean() > 0.45:
        right -= 1
    top = 0
    while top < max_y_trim and dark[top, :].mean() > 0.34:
        top += 1
    bottom = height
    while bottom > height - max_y_trim and dark[bottom - 1, :].mean() > 0.34:
        bottom -= 1

    min_trim_x = max(2, width // 320)
    min_trim_y = max(2, height // 320)
    if left < min_trim_x:
        left = 0
    if width - right < min_trim_x:
        right = width
    if top < min_trim_y:
        top = 0
    if height - bottom < min_trim_y:
        bottom = height
    if right - left < width * 0.55 or bottom - top < height * 0.55:
        return rgb_image
    if left == 0 and right == width and top == 0 and bottom == height:
        return rgb_image
    return rgb_image.crop((left, top, right, bottom))


def trim_neighbor_strip_by_separator(image: Image.Image) -> Image.Image:
    """剥离误带入的相邻照片窄条。

    只有外侧存在“邻图内容 + 明显分隔线”的组合时才裁切，避免把正常照片边缘误删。
    """
    rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image)
    gray, channel_range = gray_and_channel_range(rgb)
    height, width = gray.shape
    if width < 120 or height < 120:
        return rgb_image

    soft_separator = (gray > 200) & (channel_range < 80)
    strong_separator = (gray > 225) & (channel_range < 48)
    min_run = max(6, min(width, height) // 350)
    min_outer_y = max(24, int(height * 0.035))
    min_outer_x = max(24, int(width * 0.025))
    max_outer_y = int(height * 0.28)
    max_outer_x = int(width * 0.18)

    left = 0
    right = width
    top = 0
    bottom = height

    def looks_like_neighbor_strip(strip: np.ndarray) -> bool:
        if strip.size == 0:
            return False
        strip_gray = (
            0.299 * strip[:, :, 0].astype(np.float32)
            + 0.587 * strip[:, :, 1].astype(np.float32)
            + 0.114 * strip[:, :, 2].astype(np.float32)
        )
        strip_range = strip.max(axis=2).astype(np.int16) - strip.min(axis=2).astype(np.int16)
        if strip_gray.mean() > 185.0 and strip_range.mean() < 24.0:
            return False
        return bool(strip_gray.std() > 28.0 or strip_range.mean() > 24.0)

    row_scores = soft_separator.mean(axis=1)
    for run_start, run_end in runs(row_scores > 0.68, min_run):
        if min_outer_y <= run_start <= max_outer_y and run_end - run_start <= height * 0.06:
            if looks_like_neighbor_strip(rgb[:run_start, :]):
                top = max(top, run_end)
            break

    for run_start, run_end in reversed(runs(row_scores > 0.68, min_run)):
        outer = height - run_end
        if min_outer_y <= outer <= max_outer_y and run_end - run_start <= height * 0.06:
            if looks_like_neighbor_strip(rgb[run_end:, :]):
                bottom = min(bottom, run_start)
            break

    col_scores = strong_separator.mean(axis=0)
    for run_start, run_end in runs(col_scores > 0.88, min_run):
        if min_outer_x <= run_start <= max_outer_x and run_end - run_start <= width * 0.06:
            if looks_like_neighbor_strip(rgb[:, :run_start]):
                left = max(left, run_end)
            break

    for run_start, run_end in reversed(runs(col_scores > 0.88, min_run)):
        outer = width - run_end
        if 0 <= outer <= max_outer_x and run_start >= width * 0.65 and run_end - run_start <= width * 0.08:
            if looks_like_neighbor_strip(rgb[:, run_end:]):
                right = min(right, run_start)
            break

    if right - left < width * 0.50 or bottom - top < height * 0.50:
        return rgb_image
    if left == 0 and right == width and top == 0 and bottom == height:
        return rgb_image
    return rgb_image.crop((left, top, right, bottom))


def estimate_skew_correction_angle(
    image: Image.Image,
    max_angle: float = 3.0,
    step: float = 0.25,
    min_score_gain: float = 1.04,
) -> float:
    """估计小角度倾斜修正值。

    小幅度倾斜矫正主要看照片边缘，而不是看人脸。这里优先提取图像外缘附近的深浅边界，
    通过旋转后水平/垂直投影是否更集中来评分；人脸只用于后面的 0/90/180/270 正向判断。
    """
    sample = ImageOps.exif_transpose(image).convert("L")
    sample.thumbnail((900, 900))
    gray = np.asarray(sample, dtype=np.uint8)
    height, width = gray.shape
    if width < 40 or height < 40:
        return 0.0

    try:
        import cv2

        edges = cv2.Canny(gray, 50, 150)
        margin_for_lines = max(12, min(width, height) // 5)
        roi = np.zeros_like(edges)
        roi[:margin_for_lines, :] = edges[:margin_for_lines, :]
        roi[-margin_for_lines:, :] = edges[-margin_for_lines:, :]
        min_line_length = max(60, int(width * 0.35))
        lines = cv2.HoughLinesP(
            roi,
            rho=1,
            theta=np.pi / 180,
            threshold=max(30, width // 20),
            minLineLength=min_line_length,
            maxLineGap=max(8, width // 80),
        )
        line_angles: list[float] = []
        if lines is not None:
            for line in lines[:, 0, :]:
                x1, y1, x2, y2 = [int(v) for v in line]
                dx = x2 - x1
                dy = y2 - y1
                if abs(dx) < min_line_length:
                    continue
                angle = float(np.degrees(np.arctan2(dy, dx)))
                if abs(angle) <= max_angle:
                    line_angles.append(angle)
        if line_angles:
            median_angle = float(np.median(np.asarray(line_angles, dtype=np.float32)))
            if abs(median_angle) >= step:
                return -median_angle
    except Exception:
        pass

    # 只在外缘区域找边界，避免照片中心人物、衣服、建筑线条干扰整体倾斜判断。
    margin = max(10, min(width, height) // 8)
    edge_roi = np.zeros_like(gray, dtype=bool)
    edge_roi[:margin, :] = True
    edge_roi[-margin:, :] = True
    edge_roi[:, :margin] = True
    edge_roi[:, -margin:] = True

    dark_mask = (gray < 95) & edge_roi
    bright_mask = (gray > 210) & edge_roi
    edge_mask = dark_mask | bright_mask
    if edge_mask.mean() < 0.008:
        # 外缘边界不明显时退回深色边缘，但仍保持保守阈值。
        edge_mask = gray < 90
        if edge_mask.mean() < 0.015:
            return 0.0

    mask_image = Image.fromarray((edge_mask * 255).astype(np.uint8), "L")

    def score_for(angle: float) -> float:
        rotated = mask_image.rotate(angle, resample=Image.Resampling.NEAREST, expand=False, fillcolor=0)
        mask = np.asarray(rotated, dtype=np.float32) / 255.0
        row_score = float(np.percentile(mask.sum(axis=1), 98) - np.percentile(mask.sum(axis=1), 70))
        col_score = float(np.percentile(mask.sum(axis=0), 98) - np.percentile(mask.sum(axis=0), 70))
        return row_score + col_score

    angles = np.arange(-max_angle, max_angle + step / 2, step)
    scores = [(float(angle), score_for(float(angle))) for angle in angles]
    zero_score = score_for(0.0)
    best_angle, best_score = max(scores, key=lambda item: item[1])
    if abs(best_angle) < step or abs(best_angle) >= max_angle - step or best_score < zero_score * min_score_gain:
        return 0.0
    return best_angle


def auto_deskew_image(image: Image.Image, min_score_gain: float = 1.04) -> tuple[Image.Image, float]:
    """对整张源图做小角度纠偏。默认批量流程不会启用，因为每张照片的倾角可能不同。"""
    rgb_image = ImageOps.exif_transpose(image).convert("RGB")
    angle = estimate_skew_correction_angle(rgb_image, min_score_gain=min_score_gain)
    if angle == 0.0:
        return rgb_image, 0.0

    corrected = rgb_image.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=(255, 255, 255),
    )
    return corrected, angle


@dataclass(frozen=True)
class FaceOrientationPrediction:
    angle: int
    confidence: float
    face_count: int
    method: str
    scores: dict[int, float]


class YuNetFaceOrientationDetector:
    """OpenCV FaceDetectorYN / YuNet based 0/90/180/270 orientation detector."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or find_yunet_model()
        self.detector = None
        self.init_error = ""
        if cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
            self.init_error = "OpenCV FaceDetectorYN is unavailable."
            return
        if self.model_path is None or not self.model_path.exists():
            self.init_error = "YuNet model file was not found."
            return
        try:
            self.detector = self._create_detector(self.model_path)
        except Exception as exc:
            self.init_error = str(exc)
            self.detector = self._create_detector_from_ascii_temp(self.model_path)
            if self.detector is not None:
                self.init_error = ""

    @staticmethod
    def _create_detector(model_path: Path):
        return cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), 0.75, 0.3, 5000)

    def _create_detector_from_ascii_temp(self, model_path: Path):
        try:
            cache_path = Path(tempfile.gettempdir()) / "photo_splitter_yunet_2023mar.onnx"
            if not cache_path.exists() or cache_path.stat().st_size != model_path.stat().st_size:
                shutil.copy2(model_path, cache_path)
            return self._create_detector(cache_path)
        except Exception as exc:
            self.init_error = f"{self.init_error}; temp fallback failed: {exc}"
            return None

    @staticmethod
    def rotate_array(image: np.ndarray, angle: int) -> np.ndarray:
        angle = angle % 360
        if angle == 0:
            return image
        if angle == 90:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if angle == 180:
            return cv2.rotate(image, cv2.ROTATE_180)
        if angle == 270:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        raise ValueError("angle must be one of 0, 90, 180, 270")

    @staticmethod
    def resize_for_detection(image: np.ndarray, max_side: int = 1280) -> np.ndarray:
        height, width = image.shape[:2]
        longest = max(height, width)
        if longest <= max_side:
            return image
        scale = max_side / longest
        return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

    def detect_faces(self, image: np.ndarray) -> list[np.ndarray]:
        if self.detector is None:
            return []
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _retval, faces = self.detector.detect(image)
        if faces is None:
            return []
        return [face.astype(np.float32) for face in faces]

    @staticmethod
    def _face_confidence(face: np.ndarray) -> float:
        return float(face[14]) if len(face) >= 15 else 1.0

    @staticmethod
    def _face_landmarks(face: np.ndarray) -> np.ndarray | None:
        if len(face) < 15:
            return None
        return face[4:14].reshape(5, 2)

    @classmethod
    def _landmark_geometry_score(cls, face: np.ndarray) -> float:
        pts = cls._face_landmarks(face)
        if pts is None:
            return 1.0
        left_eye, right_eye, nose, left_mouth, right_mouth = pts
        score = 0.0
        if left_eye[1] < nose[1] and right_eye[1] < nose[1]:
            score += 1.0
        if nose[1] < (left_mouth[1] + right_mouth[1]) / 2.0:
            score += 1.0
        eye_dx = max(abs(float(left_eye[0] - right_eye[0])), 1.0)
        if abs(float(left_eye[1] - right_eye[1])) / eye_dx < 0.35:
            score += 1.0
        return score / 3.0

    def _score_faces(self, faces: list[np.ndarray], image_shape: tuple[int, ...]) -> float:
        if not faces:
            return 0.0
        img_area = float(image_shape[0] * image_shape[1])
        total = 0.0
        for face in faces:
            _x, _y, width, height = face[:4]
            area_ratio = max(float(width * height) / max(img_area, 1.0), 0.0)
            total += self._face_confidence(face) * area_ratio * (0.25 + 0.75 * self._landmark_geometry_score(face))
        return total

    def _face_position_prior(self, faces: list[np.ndarray], image_shape: tuple[int, ...]) -> float:
        if not faces:
            return 0.0
        img_h = max(float(image_shape[0]), 1.0)
        weighted_y = 0.0
        total_weight = 0.0
        for face in faces:
            _x, y, width, height = face[:4]
            weight = max(float(width * height) * max(self._face_confidence(face), 0.01), 1e-6)
            weighted_y += ((float(y) + float(height) / 2.0) / img_h) * weight
            total_weight += weight
        return max(-1.0, min(1.0, 0.5 - weighted_y / max(total_weight, 1e-9)))

    @staticmethod
    def _upright_scene_prior(image: np.ndarray) -> float:
        height = image.shape[0]
        if height < 12:
            return 0.0
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        top = hsv[: height // 3]
        bottom = hsv[-height // 3 :]
        top_sky = float(np.mean((top[:, :, 2] > 150) & (top[:, :, 1] < 90)))
        bottom_sky = float(np.mean((bottom[:, :, 2] > 150) & (bottom[:, :, 1] < 90)))
        brightness_delta = (float(top[:, :, 2].mean()) - float(bottom[:, :, 2].mean())) / 255.0
        saturation_delta = (float(bottom[:, :, 1].mean()) - float(top[:, :, 1].mean())) / 255.0
        return max(-1.0, min(1.0, (top_sky - bottom_sky) + 0.7 * brightness_delta + 0.35 * saturation_delta))

    @staticmethod
    def _right_edge_timestamp_prior(image: np.ndarray) -> float:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        _height, width = hsv.shape[:2]
        edge_width = max(1, width // 8)
        orange = (((hsv[:, :, 0] < 35) | (hsv[:, :, 0] > 170)) & (hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 115))
        left = float(orange[:, :edge_width].mean())
        right = float(orange[:, -edge_width:].mean())
        if max(left, right) < 0.006:
            return 0.0
        return max(-1.0, min(1.0, (right - left) * 10.0))

    def predict(self, image_bgr: np.ndarray) -> FaceOrientationPrediction:
        if self.detector is None or cv2 is None:
            return FaceOrientationPrediction(0, 0.0, 0, "yunet", {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0})

        small = self.resize_for_detection(image_bgr)
        scores: dict[int, float] = {}
        raw_scores: dict[int, float] = {}
        counts: dict[int, int] = {}
        rotated_images: dict[int, np.ndarray] = {}
        face_lists: dict[int, list[np.ndarray]] = {}

        for angle in (0, 90, 180, 270):
            rotated = self.rotate_array(small, angle)
            rotated_images[angle] = rotated
            faces = self.detect_faces(rotated)
            face_lists[angle] = faces
            raw_scores[angle] = self._score_faces(faces, rotated.shape)
            counts[angle] = len(faces)

        scores.update(raw_scores)
        for angle in (0, 90, 180, 270):
            if raw_scores[angle] > 0:
                scores[angle] = max(0.0, scores[angle] * (1.0 + 0.45 * self._face_position_prior(face_lists[angle], rotated_images[angle].shape)))

        side_best = max((90, 270), key=lambda item: raw_scores[item])
        side_other = 270 if side_best == 90 else 90
        if raw_scores[side_best] > 0 and raw_scores[side_other] > 0:
            side_ratio = raw_scores[side_other] / max(raw_scores[side_best], 1e-9)
            if counts[side_best] == counts[side_other] and side_ratio >= 0.45:
                for angle in (90, 270):
                    scores[angle] = max(0.0, raw_scores[angle] * (1.0 + 0.65 * self._upright_scene_prior(rotated_images[angle])))
            if side_ratio >= 0.55:
                for angle in (90, 270):
                    prior = self._right_edge_timestamp_prior(rotated_images[angle])
                    if prior:
                        scores[angle] = max(0.0, scores[angle] * (1.0 + 1.35 * prior))

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_angle, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score <= 0:
            return FaceOrientationPrediction(0, 0.0, 0, "yunet", scores)
        confidence = max(0.0, min(1.0, (best_score - second_score) / max(best_score, 1e-9)))
        return FaceOrientationPrediction(int(best_angle), float(confidence), counts[best_angle], "yunet", scores)


_YUNET_DETECTOR: YuNetFaceOrientationDetector | None = None


def find_yunet_model() -> Path | None:
    model_name = "face_detection_yunet_2023mar.onnx"
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parent
    candidates = [
        package_dir / "models" / model_name,
        repo_root / "photo_orientation_app" / "models" / model_name,
        repo_root / "图片批量旋转工具" / "photo_orientation_app" / "models" / model_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_yunet_detector() -> YuNetFaceOrientationDetector:
    global _YUNET_DETECTOR
    if _YUNET_DETECTOR is None:
        _YUNET_DETECTOR = YuNetFaceOrientationDetector()
    return _YUNET_DETECTOR


def predict_face_orientation(image: Image.Image) -> FaceOrientationPrediction:
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    if cv2 is None:
        return FaceOrientationPrediction(0, 0.0, 0, "yunet", {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0})
    image_bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    return get_yunet_detector().predict(image_bgr)


def rotate_pil_by_ccw_angle(image: Image.Image, angle: int) -> Image.Image:
    angle = angle % 360
    if angle == 0:
        return image
    if angle == 90:
        return image.transpose(Image.Transpose.ROTATE_90)
    if angle == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if angle == 270:
        return image.transpose(Image.Transpose.ROTATE_270)
    raise ValueError("angle must be one of 0, 90, 180, 270")


def rotate_by_face_orientation(image: Image.Image, min_score_ratio: float = 1.70) -> Image.Image:
    prediction = predict_face_orientation(image)
    if prediction.angle == 0 or prediction.face_count <= 0 or prediction.confidence < 0.25:
        return image.convert("RGB")
    return rotate_pil_by_ccw_angle(image.convert("RGB"), prediction.angle)

def refine_output_photo(
    image: Image.Image,
    white_threshold: int = 225,
    skew_min_score_gain: float = 1.04,
    auto_face_rotate: bool = False,
    background_mode: str = "auto",
) -> Image.Image:
    """单张照片输出前的后处理。

    顺序固定为：底色判断 -> 必要时裁白边/邻图窄条 -> 按边缘做小角度倾斜矫正 -> 可选人脸正向旋转。
    黑色/深色底色下跳过白边裁切，避免把暗底相册或黑框照片误裁得过小。
    """
    refined = image.convert("RGB")
    mode = normalize_background_mode(background_mode)
    actual_mode = estimate_background_mode(refined) if mode == "auto" else mode

    if actual_mode == "black":
        refined = trim_dark_border(refined)
    else:
        # 白底、灰底或不确定底色仍允许裁白边；黑底场景只依赖检测框本身，避免过度裁切。
        refined = trim_neighbor_strip_by_separator(
            trim_large_white_border(trim_white_border(refined, max_trim_ratio=0.42, white_threshold=white_threshold))
        )
        refined = trim_dark_border(refined, max_trim_ratio=0.12)

    angle = estimate_skew_correction_angle(refined, max_angle=2.5, step=0.25, min_score_gain=skew_min_score_gain)
    if angle:
        refined = refined.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=(0, 0, 0) if actual_mode == "black" else (255, 255, 255),
        )
    if auto_face_rotate:
        refined = rotate_by_face_orientation(refined)
    return refined
