"""图片处理。所有 PIL 操作都在线程池里跑，避免阻塞事件循环。"""

from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image as PILImage

JPEG_QUALITY = 85


def to_jpeg(data: bytes, quality: int = JPEG_QUALITY) -> bytes:
    """把任意格式图片转成 JPEG，透明区域填白。

    JPEG 不支持 alpha 通道，直接 save 会抛 OSError，所以带透明度的图
    一律先合成到白底再编码。
    """
    with PILImage.open(BytesIO(data)) as source:
        image = source
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            image = image.convert("RGBA")
            canvas = PILImage.new("RGB", image.size, (255, 255, 255))
            canvas.paste(image, mask=image.split()[-1])
            image = canvas
        elif image.mode != "RGB":
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        return buffer.getvalue()


async def to_jpeg_async(data: bytes, quality: int = JPEG_QUALITY) -> bytes:
    """to_jpeg 的线程池版本。"""
    return await asyncio.to_thread(to_jpeg, data, quality)


def crop_box(
    data: bytes,
    box: tuple[float, float, float, float],
    quality: int = 90,
) -> bytes:
    """按相对坐标裁剪图片并输出 JPEG。

    Args:
        data: 原图二进制。
        box: (left, top, right, bottom)，取值为 0~1 的相对比例。
        quality: 输出质量。

    上游在这里直接 save(buffer, "JPEG")，遇到 PNG 截图（RGBA）会抛
    OSError 让整条「出处」指令失败，所以裁剪后统一转 RGB。
    """
    with PILImage.open(BytesIO(data)) as source:
        width, height = source.size
        left = max(0, min(width, int(width * box[0])))
        top = max(0, min(height, int(height * box[1])))
        right = max(left + 1, min(width, int(width * box[2])))
        bottom = max(top + 1, min(height, int(height * box[3])))
        area = source.crop((left, top, right, bottom))
        if area.mode != "RGB":
            area = area.convert("RGB")
        buffer = BytesIO()
        area.save(buffer, "JPEG", quality=quality, optimize=True)
        return buffer.getvalue()


async def crop_box_async(
    data: bytes,
    box: tuple[float, float, float, float],
    quality: int = 90,
) -> bytes:
    """crop_box 的线程池版本。"""
    return await asyncio.to_thread(crop_box, data, box, quality)
