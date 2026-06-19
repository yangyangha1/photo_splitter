from __future__ import annotations

from PIL import ImageDraw, ImageFont


def draw_preview_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    image_width: int,
) -> None:
    """在预览图上绘制红色检测框和编号。字号、线宽随源图宽度缩放。"""
    line_width = max(2, image_width // 500)
    draw.rectangle(box, outline=(255, 40, 40), width=line_width)
    x, y = box[0] + line_width + 4, box[1] + line_width + 4
    text = str(label)
    font_size = max(18, image_width // 110)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
    try:
        text_box = draw.textbbox((x, y), text, font=font)
    except AttributeError:
        text_width, text_height = draw.textsize(text, font=font)
        text_box = (x, y, x + text_width, y + text_height)
    padding = max(3, image_width // 900)
    background = (
        max(0, text_box[0] - padding),
        max(0, text_box[1] - padding),
        text_box[2] + padding,
        text_box[3] + padding,
    )
    draw.rectangle(background, fill=(255, 255, 255), outline=(255, 40, 40), width=max(1, line_width // 2))
    draw.text((x, y), text, fill=(220, 0, 0), font=font)
