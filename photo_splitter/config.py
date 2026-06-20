from __future__ import annotations

from dataclasses import dataclass


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
SKIP_DIR_PREFIXES = ("split_output", "split_result", "测试照片输出", "归档", "__pycache__")
JPEG_QUALITY = 95
BACKGROUND_MODES = {"auto": "自动判断", "white": "白色/浅色底色", "gray": "灰色/杂色底色", "black": "黑色/深色底色"}
DEFAULT_BACKGROUND_MODE = "auto"
DETECTION_STRATEGIES = {"balanced": "平衡策略", "aggressive": "积极策略", "conservative": "保守策略"}
DEFAULT_DETECTION_STRATEGY = "balanced"


@dataclass(frozen=True)
class ProcessingPreset:
    """一组面向常见扫描/拼图场景的检测参数。

    dark_threshold：越低越容易把黑边/暗边当作分隔线过滤；越高越容易保留暗部内容。
    min_area_ratio：候选照片占整张源图的最小面积比例；值越低越容易保留小照片，也更容易误检。
    white_threshold：输出单张照片时裁切白边的阈值；值越低裁白边越积极。
    background_mode：源图底色判断；自动模式会先判断白/灰/黑底，再选择更合适的边界优先算法。黑色底色会跳过白边裁切，避免误裁。
    skew_gain_percent：小角度纠偏所需的最低得分提升百分比；值越低越积极，值越高越保守。
    detection_strategy：检测策略；积极模式偏向找小图和弱分隔线，保守模式偏向减少误切和误检。
    """

    key: str
    name: str
    description: str
    dark_threshold: int
    min_area_ratio: float
    white_threshold: int
    background_mode: str
    skew_gain_percent: int
    detection_strategy: str = DEFAULT_DETECTION_STRATEGY

    @property
    def split_strategy(self) -> str:
        """兼容旧说明里的命名；实际代码统一使用 detection_strategy。"""
        return self.detection_strategy


PROCESSING_PRESETS: dict[str, ProcessingPreset] = {
    "balanced": ProcessingPreset(
        key="balanced",
        name="通用平衡",
        description="适合大多数白底扫描件和普通拼图，误切与漏切之间取平衡。",
        dark_threshold=70,
        min_area_ratio=0.0020,
        white_threshold=225,
        background_mode="auto",
        skew_gain_percent=4,
        detection_strategy="balanced",
    ),
    "white_scan": ProcessingPreset(
        key="white_scan",
        name="白底扫描件",
        description="适合多张老照片放在白色扫描底板上，优先识别白色分隔线和大白边。",
        dark_threshold=68,
        min_area_ratio=0.0016,
        white_threshold=218,
        background_mode="white",
        skew_gain_percent=4,
        detection_strategy="balanced",
    ),
    "dark_frame": ProcessingPreset(
        key="dark_frame",
        name="黑框/暗边相册",
        description="适合黑色相框、暗色背景或深色分隔线主导的拼图页面。",
        dark_threshold=58,
        min_area_ratio=0.0025,
        white_threshold=240,
        background_mode="black",
        skew_gain_percent=5,
        detection_strategy="balanced",
    ),
    "aggressive": ProcessingPreset(
        key="aggressive",
        name="积极分割",
        description="更容易找出小照片和细分隔线，但可能增加误检。",
        dark_threshold=78,
        min_area_ratio=0.0012,
        white_threshold=212,
        background_mode="auto",
        skew_gain_percent=3,
        detection_strategy="aggressive",
    ),
    "conservative": ProcessingPreset(
        key="conservative",
        name="保守分割",
        description="降低误检概率，适合照片数量少、边界清楚的源图。",
        dark_threshold=62,
        min_area_ratio=0.0035,
        white_threshold=235,
        background_mode="auto",
        skew_gain_percent=7,
        detection_strategy="conservative",
    ),
}

DEFAULT_PRESET_KEY = "balanced"
