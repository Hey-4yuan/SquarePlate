#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
squareplate.py
把图片补白边居中成正方形，并叠加可定制的文字/图片水印。

设计目标：
  1. 输出严格正方形，原图完整居中，不裁切、不拉伸
  2. 边框可自定义：纯色 / 毛玻璃模糊 / 纯色+内外阴影
  3. 水印可定制：文字水印（字体/字号/颜色/透明度/描边/阴影/位置/平铺）
     也支持贴图水印（PNG logo）
  4. 保留 EXIF 拍摄信息与 ICC 色彩描述文件
  5. 支持单文件 / 多文件 / 整个目录递归，批量处理

依赖：Pillow >= 9.0
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:
    sys.exit("缺少依赖 Pillow，请先安装：pip install Pillow")


Image.MAX_IMAGE_PIXELS = None

# 输出质量档位：id -> (名称, JPEG quality, 说明)
Q_LEVELS = {
    "max":   ("极致", 100, "留给后期裁剪修图"),
    "high":  ("高",   95,  "日常出图推荐"),
    "std":   ("标准", 90,  "体积与画质均衡"),
    "share": ("分享", 82,  "社交平台发图"),
    "lite":  ("轻量", 75,  "传输优先"),
}  # 解除超大图保护限制（本地工具，放开）

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}

# 九宫格位置别名 -> (水平锚点比例, 垂直锚点比例)
ANCHOR_PRESETS: dict[str, Tuple[float, float]] = {
    "tl": (0.0, 0.0),
    "tc": (0.5, 0.0),
    "tr": (1.0, 0.0),
    "ml": (0.0, 0.5),
    "mc": (0.5, 0.5),
    "mr": (1.0, 0.5),
    "bl": (0.0, 1.0),
    "bc": (0.5, 1.0),
    "br": (1.0, 1.0),
}


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------
def resolve_font(path: Optional[str], fallback_size: int) -> ImageFont.FreeTypeFont:
    """
    按路径加载字体；路径无效时退回 PIL 内置位图字体。
    内置字体不支持 truetype(size)，故用 try 兜住。
    """
    if path:
        try:
            return ImageFont.truetype(path, fallback_size)
        except OSError:
            print(f"[warn] 字体加载失败，改用内置字体：{path}", file=sys.stderr)
    return ImageFont.load_default()


# --------------------------------------------------------------------------
# EXIF 读取与相机厂商信息
# --------------------------------------------------------------------------
BRANDS = [
    # (id, 中文名, 文字字标, 强调色, 底色深浅, 匹配关键字)
    ("hasselblad", "哈苏",  "HASSELBLAD", (0, 0, 0),      "light", ("hasselblad", "哈苏")),
    ("leica",       "徕卡",  "LEICA",       (200, 16, 46), "light", ("leica", "徕卡")),
    ("sony",        "索尼",  "SONY",        (184, 184, 184),"dark",  ("sony", "索尼")),
    ("nikon",       "尼康",  "NIKON",       (187, 0, 0),    "light", ("nikon", "尼康")),
    ("canon",       "佳能",  "Canon",       (188, 0, 36),   "light", ("canon", "佳能", "tokina")),
    ("fujifilm",    "富士",  "FUJIFILM",    (0, 160, 75),   "light", ("fujifilm", "fuji", "富士")),
    ("panasonic",   "松下",  "LUMIX",       (74, 127, 193), "dark",  ("panasonic", "lumix", "松下")),
    ("olympus",     "奥林巴斯/OM", "OM SYSTEM", (10, 10, 10), "light", ("olympus", "om digital", "奥林巴斯", "om system")),
    ("ricoh",       "理光",  "RICOH",       (217, 38, 28),  "light", ("ricoh", "pentax", "理光", "宾得")),
    ("apple",       "苹果",  "iPhone",      (110, 110, 115),"light", ("apple", "iphone", "ipad", "苹果")),
    ("huawei",      "华为",  "HUAWEI",      (199, 0, 11),   "light", ("huawei", "华为", "honor", "荣耀")),
    ("xiaomi",      "小米",  "Xiaomi",      (255, 105, 0),  "light", ("xiaomi", "redmi", "小米", "红米")),
    ("samsung",     "三星",  "SAMSUNG",     (20, 40, 160),  "light", ("samsung", "三星")),
    ("dji",         "大疆",  "DJI",         (90, 90, 90),   "dark",  ("dji", "大疆")),
    ("gopro",       "GoPro", "GoPro",       (0, 165, 207),  "light", ("gopro",)),
    ("zeiss",       "蔡司",  "ZEISS",       (74, 144, 217), "light", ("zeiss", "蔡司")),
    ("sigma",       "适马",  "SIGMA",       (0, 41, 75),    "light", ("sigma", "适马")),
    ("tamron",      "腾龙",  "TAMRON",      (0, 105, 62),   "light", ("tamron", "腾龙")),
    ("oppo",        "OPPO",  "OPPO",        (26, 26, 26),   "light", ("oppo", "欧珀")),
    ("vivo",        "vivo",  "vivo",        (65, 95, 255),  "light", ("vivo", "iqoo")),
    ("oneplus",     "一加",  "OnePlus",     (235, 0, 40),   "light", ("oneplus", "一加")),
    ("realme",      "真我",  "realme",      (255, 201, 21), "light", ("realme", "真我")),
    ("nubia",       "努比亚", "NUBIA",      (0, 0, 0),      "light", ("nubia", "红魔", "努比亚")),
    ("meizu",       "魅族",  "MEIZU",       (0, 0, 0),      "light", ("meizu", "魅族")),
    ("nothing",     "Nothing", "Nothing",   (0, 0, 0),      "light", ("nothing",)),
]


def detect_brand(exif: dict) -> Optional[dict]:
    """按 EXIF 的 Make/Model 识别相机厂商。"""
    if not exif:
        return None
    hay = f"{exif.get('Make', '')} {exif.get('Model', '')}".lower()
    for bid, name, en, accent, plate, keys in BRANDS:
        if any(k.lower() in hay for k in keys):
            return {"id": bid, "name": name, "en": en, "accent": accent, "plate": plate}
    return None


def _num(v) -> Optional[float]:
    """EXIF 里的值可能是 IFDRational 或 int，统一转 float。"""
    if v is None:
        return None
    try:
        if isinstance(v, tuple) and len(v) == 2 and v[1]:
            return float(v[0]) / float(v[1])
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def fmt_exposure(v) -> Optional[str]:
    v = _num(v)
    if not v or v <= 0:
        return None
    if v >= 1:
        return f"{v:.1f}s"
    return f"1/{round(1 / v)}s"


def fmt_fnumber(v) -> Optional[str]:
    v = _num(v)
    if not v or v <= 0:
        return None
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"f/{s}"


def _pick(exif: dict, *names):
    """按多个候选键名取值，兼容不同写法的 EXIF 标签。"""
    for n in names:
        v = (exif or {}).get(n)
        if v is not None:
            return v
    return None


def build_info_lines(exif: dict, brand: Optional[dict], opts: dict) -> list:
    """按开关生成相机信息文本行。"""
    lines = []
    model = _pick(exif, "Model", "Make") or ""
    if opts.get("model") and model:
        lines.append(model)

    parts = []
    if opts.get("lens") and _pick(exif, "LensModel", "Lens"):
        parts.append(str(_pick(exif, "LensModel", "Lens")).strip())
    if opts.get("exp"):
        e = fmt_exposure(_pick(exif, "ExposureTime", "ShutterSpeedValue"))
        f = fmt_fnumber(_pick(exif, "FNumber", "ApertureValue"))
        if e or f:
            parts.append(f"{e or ''}{' · ' if e and f else ''}{f or ''}")
    if opts.get("iso"):
        iso = _num(_pick(exif, "ISO", "ISOSpeedRatings", "PhotographicSensitivity"))
        if iso:
            parts.append(f"ISO {round(iso)}")
    if opts.get("focal"):
        fl = _num(_pick(exif, "FocalLength"))
        if fl:
            txt = f"{round(fl)}mm"
            eq = _num(_pick(exif, "FocalLengthIn35mmFilm", "FocalLength35mm"))
            if opts.get("eq35") and eq:
                txt += f" ({round(eq)}mm 等效)"
            parts.append(txt)
    if opts.get("date"):
        d = _pick(exif, "DateTimeOriginal", "DateTimeDigitized", "DateTime")
        if d and len(str(d)) >= 10:
            d = str(d)
            parts.append(f"{d[0:4]}-{d[5:7]}-{d[8:10]}")
    if parts:
        lines.append("  ·  ".join(parts))
    return [x for x in lines if x]


def draw_info_watermark(canvas, exif, brand, opts, size, color, opacity):
    """
    画相机信息水印。厂商字标 + 机型 + 拍摄参数，可选浅底卡片/深底色条。
    """
    lines = build_info_lines(exif, brand, opts)
    if not lines and not brand:
        return

    W, H = canvas.size
    light_plate = opts.get("plate") == "light"
    margin = max(8, int(round(min(W, H) * 0.045)))
    size = int(max(9, min(int(size), int((W - margin * 2) / 14))))

    rows = len(lines) + (1 if brand else 0)
    if rows == 0:
        return
    draw = ImageDraw.Draw(canvas)

    # 自动收缩：文字过宽时二分缩小字号，避免被裁掉
    def measure(sz):
        fb = resolve_font(opts.get("font"), int(round(sz * 1.05))) if brand else None
        wb = draw.textlength(brand["en"], font=fb) if brand else 0
        fr = resolve_font(opts.get("font"), sz)
        wb_line = max((draw.textlength(x, font=fr) for x in lines), default=0)
        return max(wb, wb_line)

    max_w = W - margin * 2
    if measure(size) > max_w:
        lo, hi = 9, size
        for _ in range(12):
            mid = (lo + hi) / 2
            if measure(mid) > max_w:
                hi = mid
            else:
                lo = mid
        size = max(9, int(lo))

    f_reg = resolve_font(opts.get("font"), size)
    f_brand = resolve_font(opts.get("font"), int(round(size * 1.05)))
    lh = int(round(size * 1.5))
    block_h = lh * rows

    w_brand = draw.textlength(brand["en"], font=f_brand) if brand else 0
    w_body = max((draw.textlength(x, font=f_reg) for x in lines), default=0)
    box_w = int(min(max(w_brand, w_body), max_w))

    ax, ay = opts.get("anchor", (0.0, 1.0))
    ox = margin if ax <= 0 else (W - box_w - margin if ax >= 1 else int((W - box_w) * ax))
    oy = margin if ay <= 0 else (H - block_h - margin if ay >= 1 else int((H - block_h) * ay))
    ox = max(0, min(W - box_w, ox))
    oy = max(0, min(H - block_h, oy))

    # 背板
    if opts.get("style") in ("bar", "card"):
        pad_x, pad_y = int(size * 0.7), int(size * 0.55)
        bh = int(block_h + pad_y * 2)
        if opts["style"] == "bar" and ay >= 1:
            by = H - bh
            canvas.paste(Image.new("RGB", (W, bh), (0, 0, 0)), (0, by),
                         Image.new("L", (W, bh), 178))
            if brand:
                canvas.paste(Image.new("RGB", (max(3, int(bh * 0.05)), bh), brand["accent"]),
                             (0, by))
            oy = by + pad_y
        else:
            bw = int(box_w + pad_x * 2)
            bx = max(0, min(W - bw, ox - pad_x))
            by = max(0, min(H - bh, oy - pad_y))
            if opts["style"] == "bar":
                canvas.paste(Image.new("RGB", (W, bh), (0, 0, 0)), (0, by),
                             Image.new("L", (W, bh), 178))
                if brand:
                    canvas.paste(Image.new("RGB", (max(3, int(bh * 0.06)), bh), brand["accent"]),
                                 (0, by))
                ox = max(pad_x, ox)
            else:
                plate = opts.get("bg") or (248, 248, 246)
                plate_layer = Image.new("RGB", (bw, bh), plate)
                if brand:
                    plate_layer = _rounded_with_border(plate_layer, brand["accent"],
                                                        max(1, int(size * 0.05)))
                canvas.paste(plate_layer, (bx, by))

    ink_brand = brand["accent"] if (light_plate and brand) else (255, 255, 255)
    ink_text = (26, 26, 26) if light_plate else (255, 255, 255)

    y = oy
    if brand:
        draw.text((ox, y + int(size * 0.1)), brand["en"], font=f_brand, fill=ink_brand)
        y += lh
    for line in lines:
        draw.text((ox, y + int(size * 0.1)), line, font=f_reg, fill=ink_text)
        y += lh


def _rounded_with_border(layer: Image.Image, border_color, width: int) -> Image.Image:
    """给浅色卡片加一圈圆角描边。"""
    w, h = layer.size
    r = max(2, min(w, h) // 8)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    border = Image.new("RGB", (w, h), border_color)
    inner = Image.new("L", (w, h), 0)
    ImageDraw.Draw(inner).rounded_rectangle(
        [width, width, w - 1 - width, h - 1 - width], radius=max(1, r - width), fill=255)
    border.paste(layer, (0, 0), inner)
    out = Image.new("RGB", (w, h), border_color)
    out.paste(border, (0, 0), mask)
    return out


# --------------------------------------------------------------------------
# 边框底板
# --------------------------------------------------------------------------
def build_canvas(
    size: int,
    border_style: str,
    border_color: Tuple[int, int, int],
    blur_radius: int,
) -> Image.Image:
    """
    生成 size x size 的边框底板。
      solid      纯色
      blur       模糊扩展（由 build_blur_canvas 单独处理）
      shadow     纯色 + 内缩投影，让照片浮起来
    """
    if border_style == "blur":
        # blur 模式走 build_blur_canvas，这里只是占位
        return Image.new("RGB", (size, size), (235, 235, 235))

    canvas = Image.new("RGB", (size, size), border_color)
    if border_style != "shadow":
        return canvas

    # 投影：右下方向偏移的深色块，再盖回原色
    offset = max(2, size // 200)
    canvas.paste(Image.new("RGB", (size, size), (0, 0, 0)), (offset, offset), None)
    canvas.paste(Image.new("RGB", (size - offset, size - offset), border_color), (0, 0), None)
    return canvas


def build_blur_canvas(img: Image.Image, target: int, blur_radius: int) -> Image.Image:
    """
    毛玻璃边框：把原图缩放裁切成正方形后高斯模糊，作为底板。
    """
    cover = ImageOps.fit(img.convert("RGB"), (target, target), method=Image.LANCZOS)
    return cover.filter(ImageFilter.GaussianBlur(radius=blur_radius))


# --------------------------------------------------------------------------
# 文字水印
# --------------------------------------------------------------------------
def draw_text_watermark(
    base: Image.Image,
    text: str,
    font_path: Optional[str],
    font_size: Optional[int],
    color: Tuple[int, int, int],
    opacity: float,
    stroke_width: int,
    stroke_color: Optional[Tuple[int, int, int]],
    shadow: bool,
    anchor: str,
    margin: int,
    tile: bool,
    line_spacing: float,
) -> Image.Image:
    """
    在 base 上叠加文字水印。返回 RGBA 图层已合成的 RGB 图。
    """
    if not text:
        return base

    W, H = base.size
    size = font_size if font_size and font_size > 0 else max(14, int(min(W, H) * 0.045))
    font = resolve_font(font_path, size)

    # margin 传 None 表示按图幅 4% 自动
    mg = int(min(W, H) * 0.04) if margin is None else margin

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    alpha = max(0, min(255, int(opacity * 255)))
    fill = (color[0], color[1], color[2], alpha)

    stroke_fill = None
    if stroke_width > 0:
        sc = stroke_color if stroke_color else (0, 0, 0)
        # 描边跟着主文字透明度走，稍微提一点避免糊
        stroke_fill = (sc[0], sc[1], sc[2], min(255, alpha + 40))

    kwargs = dict(fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)

    # 阴影：先画一层偏移的黑色，再画正片
    if shadow:
        off = max(1, size // 18)
        sx, sy = anchor
        pos = _anchor_xy(sx, sy, W, H, text, font, mg)
        draw.text(
            (pos[0] + off, pos[1] + off), text, font=font,
            fill=(0, 0, 0, int(alpha * 0.5)), anchor="la",
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, int(alpha * 0.5)),
        )

    if tile:
        layer = _tile_layer(layer, alpha, font, text, size, line_spacing)
    else:
        ax, ay = anchor
        pos = _anchor_xy(ax, ay, W, H, text, font, mg)
        draw.text(pos, text, font=font, anchor="la", **kwargs)

    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def _anchor_xy(
    ax: float, ay: float, W: int, H: int,
    text: str, font: ImageFont.FreeTypeFont, margin: int,
) -> Tuple[int, int]:
    """
    把九宫格比例换算成文字左上角坐标。
    ax/ay 为 0~1，0=靠左/靠上，1=靠右/靠下。始终尊重 margin。
    """
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:  # noqa: BLE001
        tw, th = font.getsize(text)

    if ax <= 0.0:
        x = margin
    elif ax >= 1.0:
        x = W - tw - margin
    else:
        x = int((W - tw) * ax)

    if ay <= 0.0:
        y = margin
    elif ay >= 1.0:
        y = H - th - margin
    else:
        y = int((H - th) * ay)

    return (max(0, x), max(0, y))


def _tile_layer(
    layer: Image.Image, alpha: int, font: ImageFont.FreeTypeFont,
    text: str, size: int, line_spacing: float,
) -> Image.Image:
    """
    平铺水印：把文字以对角线方向重复铺满整张图。
    做法：先在比画布更大的透明层上画一行，再旋转 45 度后居中贴回。
    """
    W, H = layer.size
    diag = int((W * W + H * H) ** 0.5) + 40

    strip = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    step_y = int(size * (1.6 + line_spacing))
    y = 0
    while y < diag:
        x = 0
        while x < diag:
            sd.text((x, y), text, font=font,
                    fill=(255, 255, 255, alpha), anchor="la")
            x += max(int(font.getlength(text)) + int(size * 1.2), 10)
        y += step_y

    rotated = strip.rotate(45, resample=Image.BICUBIC, expand=False)
    left = (strip.width - W) // 2
    top = (strip.height - H) // 2
    return rotated.crop((left, top, left + W, top + H))


# --------------------------------------------------------------------------
# 图片水印（logo）
# --------------------------------------------------------------------------
def paste_image_watermark(
    base: Image.Image, logo_path: str, ratio: float,
    opacity: float, anchor: str, margin: int,
) -> Image.Image:
    try:
        with Image.open(logo_path) as lg:
            logo = lg.convert("RGBA")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 水印图片读取失败，跳过：{e}", file=sys.stderr)
        return base

    W, H = base.size
    target_w = max(8, int(W * ratio))
    logo.thumbnail((target_w, target_w), Image.LANCZOS)

    if opacity < 1.0:
        a = logo.getchannel("A")
        logo.putalpha(a.point(lambda p: int(p * opacity) if p > 0 else 0))

    ax, ay = anchor
    px = margin if ax <= 0.0 else (W - logo.width - margin if ax >= 1.0 else int((W - logo.width) * ax))
    py = margin if ay <= 0.0 else (H - logo.height - margin if ay >= 1.0 else int((H - logo.height) * ay))

    base_rgba = base.convert("RGBA")
    base_rgba.paste(logo, (max(0, px), max(0, py)), logo)
    return base_rgba.convert("RGB")


# --------------------------------------------------------------------------
# classic 布局（哈苏式）：照片 1:1 保真，画布按照片尺寸外扩
# --------------------------------------------------------------------------
SERIF_CANDIDATES = [  # 斜体衬线，用于字标
    "C:/Windows/Fonts/georgiai.ttf",
    "C:/Windows/Fonts/timesi.ttf",
    "C:/Windows/Fonts/timb____.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
]


def _serif_font(size: int):
    for path in SERIF_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def classic_layout(w: int, h: int, zoom: int, has_info: bool,
                   flags: dict, scale: int = 100) -> dict:
    """
    照片 1:1 居中，画布边长 = 照片最长边 × zoom%（≥100，向外扩充）。
    信息开启时若留白不够放字标/机型/参数带，自动扩大画布。
    """
    base = max(w, h)
    S = round(base * max(100, zoom) / 100)
    k = max(50, min(220, scale)) / 100
    fs_brand = max(10, round(S * 0.030 * k))
    fs_model = max(11, round(S * 0.026 * k))
    fs_val = max(9, round(S * 0.019 * k))
    top_need = round(fs_brand * 2.4) if (has_info and flags.get("brand_on")) else 0
    params_on = has_info and any(flags.get(k) for k in
                                 ("focal", "aperture", "shutter", "iso", "eq35", "date"))
    bottom_need = round((fs_model * 2.2 if (has_info and flags.get("model")) else 0)
                        + (fs_val * 2.6 if params_on else 0))
    if has_info:
        S = max(S, h + top_need + bottom_need)
    photo_x = round((S - w) / 2)
    photo_y = round((S - h) / 2)
    return {"S": S, "photo_x": photo_x, "photo_y": photo_y,
            "photo_top": photo_y, "photo_bottom": photo_y + h}


def _shutter_text(v: float) -> str:
    return (f"{round(v * 10) / 10:g}s" if v >= 1 else f"1/{round(1 / v)}")


def draw_classic_info(canvas, exif: dict, brand: Optional[dict], opts: dict, bands: dict):
    """
    哈苏式排版：字标（照片上方，画布底色配色）+ 机型/参数（照片下方，可选纯色底条）。
    所有文字水平居中；字标与条上文字颜色各自按所处背景明暗独立判定。
    """
    S = canvas.width
    H = canvas.height
    draw = ImageDraw.Draw(canvas)

    on_bar = opts.get("bar", False)
    bar_color = opts.get("bar_color", (26, 26, 26))
    canvas_ink = (26, 26, 26) if _is_light(opts.get("bg_rgb", (255, 255, 255))) else (255, 255, 255)
    canvas_gray = (154, 154, 160) if _is_light(opts.get("bg_rgb", (255, 255, 255))) else (255, 255, 255)
    bar_ink = ((26, 26, 26) if _is_light(bar_color) else (255, 255, 255)) if on_bar else canvas_ink
    bar_gray = ((154, 154, 160) if _is_light(bar_color) else (255, 255, 255)) if on_bar else canvas_gray

    photo_bottom = bands["photo_bottom"]
    photo_top = bands["photo_top"]
    bar_h = H - photo_bottom

    # —— 纯色底条（顶边贴合照片下边缘，延伸到画布底）——
    if on_bar and bar_h > 0:
        canvas.paste(Image.new("RGB", (S, bar_h), bar_color), (0, photo_bottom))

    fw = int(opts.get("weight", 500))
    # —— 顶部字标（斜体衬线宽字距，贴近照片上缘）——
    if opts.get("brand_on") and brand:
        fs = max(10, round(S * 0.030 * (opts.get("scale", 100) / 100)))
        gap = round(fs * 0.32)
        font = _serif_font(fs)
        text = brand["en"].upper()
        widths = [draw.textlength(ch, font=font) for ch in text]
        total = sum(widths) + gap * (len(text) - 1)
        x = S / 2 - total / 2
        base_y = photo_top - max(round(fs * 0.9), round(photo_top * 0.32)) - fs * 0.35
        for ch, cw in zip(text, widths):
            draw.text((x, base_y), ch, font=font, fill=canvas_ink)
            x += cw + gap

    # —— 底部机型行 ——
    model = (opts.get("model_text") or "").strip() or (_pick(exif, "Model", "Make") or "")
    if opts.get("model") and model:
        fs = max(11, round(S * 0.026 * (opts.get("scale", 100) / 100)))
        font = resolve_font(_weight_font(opts.get("font"), fw), fs)
        draw.text((S / 2, photo_bottom + bar_h * 0.30), model,
                  font=font, fill=bar_ink, anchor="ma")

    # —— 底部参数行 ——
    LL = opts.get("label_lang", "en")
    LBL = {
        "en":  {"focal": "FL", "aperture": "Aperture", "shutter": "Shutter",
                "iso": "ISO", "eq35": "35mm", "date": "Date"},
        "zh":  {"focal": "焦距", "aperture": "光圈", "shutter": "快门",
                "iso": "ISO", "eq35": "等效", "date": "日期"},
        "none": {"focal": "", "aperture": "", "shutter": "",
                 "iso": "", "eq35": "", "date": ""},
    }.get(LL, {})
    groups = []
    if exif:
        fl = _num(_pick(exif, "FocalLength"))
        fl_e = _num(_pick(exif, "FocalLengthIn35mmFilm"))
        if opts.get("focal") and fl:
            if opts.get("eq35") and fl_e:
                groups.append((LBL.get("eq35", ""), f"{round(fl_e)}mm"))
            groups.append((LBL.get("focal", ""), f"{round(fl)}mm"))
        if opts.get("aperture", True):
            fn = _num(_pick(exif, "FNumber"))
            if fn:
                groups.append((LBL.get("aperture", ""), "f/" + f"{fn:.1f}".rstrip("0").rstrip(".")))
        if opts.get("shutter", True):
            ev = _num(_pick(exif, "ExposureTime"))
            if ev:
                groups.append((LBL.get("shutter", ""), _shutter_text(ev)))
        if opts.get("iso"):
            iso = _num(_pick(exif, "ISO", "ISOSpeedRatings"))
            if iso:
                groups.append((LBL.get("iso", ""), str(round(iso))))
        if opts.get("date"):
            d = _pick(exif, "DateTimeOriginal", "DateTime")
            if d and len(str(d)) >= 10:
                d = str(d)
                groups.append((LBL.get("date", ""), f"{d[0:4]}-{d[5:7]}-{d[8:10]}"))
    if groups:
        fs_v = max(9, round(S * 0.019 * (opts.get("scale", 100) / 100)))
        fs_l = max(8, round(fs_v * 0.85))
        f_v = resolve_font(_weight_font(opts.get("font"), fw), fs_v)
        f_l = resolve_font(_weight_font(opts.get("font"), fw), fs_l)
        g_in = round(fs_v * 0.7)
        g_out = round(fs_v * 1.6)
        widths = []
        for lab, val in groups:
            widths.append((draw.textlength(lab, font=f_l) if lab else 0,
                           draw.textlength(val, font=f_v)))
        total = int(sum(a + (g_in if lab else 0) + b for (lab, _), (a, b) in zip(groups, widths))
                    + g_out * (len(groups) - 1))
        max_w = int(S * 0.92)
        scale = min(1.0, max_w / total) if total > max_w else 1.0
        if scale < 1.0:
            fs_v = max(9, int(fs_v * scale))
            fs_l = max(8, int(fs_l * scale))
            f_v = resolve_font(opts.get("font"), fs_v)
            f_l = resolve_font(opts.get("font"), fs_l)
            widths = []
            for lab, val in groups:
                widths.append((draw.textlength(lab, font=f_l) if lab else 0,
                               draw.textlength(val, font=f_v)))
            total = int(sum(a + (g_in if lab else 0) + b for (lab, _), (a, b) in zip(groups, widths))
                        + g_out * (len(groups) - 1))
        x = S / 2 - total / 2
        base_y = photo_bottom + bar_h * 0.72
        for (lab, val), (wl, wv) in zip(groups, widths):
            if lab:
                draw.text((x, base_y), lab, font=f_l, fill=bar_gray)
                x += wl + g_in
            draw.text((x, base_y), val, font=f_v, fill=bar_ink)
            x += wv + g_out


def _weight_font(base: Optional[str], weight: int) -> Optional[str]:
    """按字重挑字体文件：>=600 用粗体变体（仅覆盖常见 Windows 字体）。"""
    if weight < 600 or not base:
        return base
    table = {
        "msyh": "C:/Windows/Fonts/msyhbd.ttc",
        "simhei": "C:/Windows/Fonts/simhei.ttf",
        "arial": "C:/Windows/Fonts/arialbd.ttf",
        "segoe": "C:/Windows/Fonts/segoeuib.ttf",
    }
    key = Path(base).stem.lower()
    for k, v in table.items():
        if k in key and Path(v).exists():
            return v
    return base


def _is_light(rgb) -> bool:
    r, g, b = rgb[:3]
    return (r + g + b) / 3 > 150


def process_one(args: tuple) -> str:
    (
        src, dst, border_style, border_color, blur_radius,
        text, font_path, font_size, color, opacity, stroke_width, stroke_color,
        text_shadow, anchor, margin, tile, line_spacing,
        logo, logo_ratio, logo_opacity, logo_anchor, logo_margin,
        fmt, keep_meta, overwrite,
        zoom,
        info, brand_id,
        show_model, show_brand,
        show_focal, show_aperture, show_shutter, show_iso,
        show_eq35, show_date,
        q_level,
        wcolor_auto, wcolor_str, model_text, label_lang,
        info_bar, info_bar_color, info_scale, info_weight,
    ) = args

    src_p, dst_p = Path(src), Path(dst)
    if dst_p.exists() and not overwrite:
        return f"skip  {src_p.name}（已存在）"

    try:
        with Image.open(src_p) as raw:
            # EXIF 方向修正：手机竖拍照片必须先转正，否则补边方向会错
            img = ImageOps.exif_transpose(raw)
            meta = dict(raw.info)
            exif = raw.getexif()

            im = img.convert("RGB")
            w, h = im.size

            # 唯一模式：照片 1:1 保真，画布按照片 × zoom 向外扩充
            exif_d: dict = {}
            if exif:
                from PIL.ExifTags import TAGS as _T
                for _tid, _val in exif.items():
                    exif_d[_T.get(_tid, _tid)] = _val
                try:
                    for _tid, _val in exif.get_ifd(0x8769).items():
                        exif_d[_T.get(_tid, _tid)] = _val
                except Exception:  # noqa: BLE001
                    pass
            br = (detect_brand(exif_d) if brand_id == "auto" else next(
                ({"id": x[0], "name": x[1], "en": x[2], "accent": x[3], "plate": x[4]}
                 for x in BRANDS if x[0] == brand_id), None))
            flags = {"brand_on": info and show_brand, "model": info and show_model,
                     "focal": info and show_focal, "aperture": info and show_aperture,
                     "shutter": info and show_shutter, "iso": info and show_iso,
                     "eq35": info and show_eq35, "date": info and show_date}
            L = classic_layout(img.width, img.height, zoom, info, flags, info_scale)
            S2 = L["S"]
            bg_rgb = border_color if border_style != "blur" else (255, 255, 255)
            canvas2 = Image.new("RGB", (S2, S2), bg_rgb)
            canvas2.paste(img, (L["photo_x"], L["photo_y"]))
            if info:
                draw_classic_info(canvas2, exif_d, br,
                                  {"model": show_model,
                                   "aperture": show_aperture, "shutter": show_shutter,
                                   "iso": show_iso, "focal": show_focal,
                                   "eq35": show_eq35, "date": show_date,
                                   "font": font_path, "model_text": model_text,
                                   "label_lang": label_lang,
                                   "brand_on": show_brand,
                                   "bar": info_bar, "bar_color": info_bar_color,
                                   "bg_rgb": bg_rgb,
                                   "scale": info_scale, "weight": info_weight},
                                  {"photo_top": L["photo_top"],
                                   "photo_bottom": L["photo_bottom"]})
            # 用户文字水印（画在信息层之上）
            if text:
                wc = ((26, 26, 26) if _is_light(bg_rgb) else (255, 255, 255)) \
                    if wcolor_auto else parse_color(wcolor_str)
                draw_text_watermark(
                    canvas2, text, font_path, font_size,
                    wc, opacity, stroke_width,
                    tuple(stroke_color) if stroke_color else None, text_shadow,
                    anchor, margin, tile, line_spacing,
                )

            # 存盘
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            out_p = dst_p.with_suffix(".png") if fmt == "png" else dst_p
            save_kw: dict = {}
            if fmt == "png":
                save_kw = dict(optimize=True)
            else:
                save_kw = dict(quality=Q_LEVELS.get(q_level, ("", 95))[1],
                               subsampling=0, optimize=True)
            if keep_meta and exif:
                save_kw["exif"] = exif.tobytes()
            if keep_meta and meta.get("icc_profile"):
                save_kw["icc_profile"] = meta["icc_profile"]
            canvas2.save(out_p, **save_kw)
            return f"ok    {src_p.name} -> {out_p.name} ({S2}x{S2})"

        return f"ok    {src_p.name} -> {dst_p.name}"

    except Exception as e:  # noqa: BLE001
        import traceback; traceback.print_exc()
        return f"FAIL  {src_p.name}: {e}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_anchor(name: str) -> Tuple[float, float]:
    key = name.lower()
    if key in ANCHOR_PRESETS:
        return ANCHOR_PRESETS[key]
    if "," in key:  # 支持 0.25,0.75 这种精确比例
        try:
            x, y = key.split(",", 1)
            return (max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y))))
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"位置需为 {','.join(ANCHOR_PRESETS)} 或 x,y 比例，收到：{name}"
    )


def parse_color(name: str) -> Tuple[int, int, int]:
    n = name.strip()
    named = {
        "white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128),
        "grey": (128, 128, 128), "red": (220, 50, 50), "yellow": (250, 210, 60),
    }
    if n.lower() in named:
        return named[n.lower()]
    if n.startswith("#"):
        n = n[1:]
    if len(n) == 3 and all(c in "0123456789abcdefABCDEF" for c in n):
        n = "".join(c * 2 for c in n)
    if len(n) == 6 and all(c in "0123456789abcdefABCDEF" for c in n):
        return (int(n[0:2], 16), int(n[2:4], 16), int(n[4:6], 16))
    raise argparse.ArgumentTypeError(f"无法解析颜色：{name}")


def collect_inputs(inputs: Sequence[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            files += [f for f in it if f.is_file() and f.suffix.lower() in SUPPORTED_EXT]
        elif p.is_file():
            files.append(p)
        else:
            print(f"[warn] 不存在的路径，已跳过：{raw}", file=sys.stderr)
    # 去重并保持顺序
    seen, uniq = set(), []
    for f in files:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(f)
    return uniq


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="squareplate.py",
        description="图片补边居中成正方形 + 定制水印",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  # 补白边成正方形，输出到 out/
  python squareplate.py photos/ -o out/

  # 指定 1080x1080，右下角半透明文字水印
  python squareplate.py photos/ -o out/ --size 1080 --text "@my_ig" --anchor br --opacity 0.6

  # 毛玻璃边框 + 斜向平铺水印
  python squareplate.py photos/ -o out/ --border blur --blur 40 --text "© 2026" --tile

  # 贴 logo 图片水印
  python squareplate.py a.jpg -o out/ --logo logo.png --logo-ratio 0.15 --logo-anchor br
""",
    )
    p.add_argument("inputs", nargs="*", help="图片文件或目录（可多个）")
    p.add_argument("-o", "--output", default="./squared", help="输出目录（默认 ./squared）")
    p.add_argument("-r", "--recursive", action="store_true", help="递归扫描子目录")

    g = p.add_argument_group("边框")
    g.add_argument("--size", type=int, default=0, help="正方形边长像素，0=沿用原图最长边")
    g.add_argument("--border", choices=["solid", "blur", "shadow"], default="solid",
                   help="边框风格：solid 纯色 / blur 毛玻璃 / shadow 纯色+投影")
    g.add_argument("--border-color", type=parse_color, default=(255, 255, 255),
                   help="边框颜色，白/黑/gray/#RRGGBB（默认白）")
    g.add_argument("--blur", type=int, default=30, help="blur 模式下的模糊半径")
    g.add_argument("--shadow", action="store_true", help="启用投影（等价 --border shadow）")

    g = p.add_argument_group("文字水印")
    g.add_argument("--text", default="", help="水印文字内容")
    g.add_argument("--font", default=None, help="字体文件路径，如 C:/Windows/Fonts/msyh.ttc")
    g.add_argument("--font-size", type=int, default=0, help="字号，0=按图幅自动")
    g.add_argument("--wcolor", default="auto",
                   help="文字水印颜色：auto=按画布底色自动黑/白（默认），或十六进制色值如 ffffff")
    g.add_argument("--no-wauto", action="store_true",
                   help="关闭文字颜色自动（固定使用 --wcolor 指定的颜色）")
    g.add_argument("--color", type=parse_color, default=(255, 255, 255), help=argparse.SUPPRESS)
    g.add_argument("--opacity", type=float, default=0.6, help="不透明度 0~1（默认 0.6）")
    g.add_argument("--stroke", type=int, default=0, help="描边宽度像素")
    g.add_argument("--stroke-color", type=parse_color, default=None, help="描边颜色，默认黑")
    g.add_argument("--text-shadow", action="store_true", help="文字投影，提升暗底可读性")
    g.add_argument("--anchor", type=parse_anchor, default=ANCHOR_PRESETS["br"],
                   help="位置 九宫格(tl/tc/tr/ml/mc/mr/bl/bc/br) 或 x,y")
    g.add_argument("--margin", type=int, default=None, help="边距像素，默认按图幅 4%%")
    g.add_argument("--tile", action="store_true", help="斜向平铺满图")
    g.add_argument("--line-spacing", type=float, default=0.4, help="平铺时的行距系数")

    g = p.add_argument_group("图片水印")
    g.add_argument("--logo", default=None, help="PNG logo 路径")
    g.add_argument("--logo-ratio", type=float, default=0.15, help="logo 宽度占图幅比例")
    g.add_argument("--logo-opacity", type=float, default=0.8, help="logo 不透明度 0~1")
    g.add_argument("--logo-anchor", type=parse_anchor, default=ANCHOR_PRESETS["br"], help="logo 位置")
    g.add_argument("--logo-margin", type=int, default=24, help="logo 边距像素")

    g = p.add_argument_group("画布与输出")
    g.add_argument("--canvas-mode", choices=["fit", "fixed"], default="fit",
                   help="fit=边长由图片决定、照片 1:1 保真（默认）；fixed=指定边长 --size")
    g.add_argument("--quality-level", choices=list(Q_LEVELS.keys()), default="high",
                   help="输出质量档位（JPG）：max/high/std/share/lite")

    g = p.add_argument_group("原图占比与位置")
    g.add_argument("--zoom", type=int, default=100,
                   help="原图在正方形中的占比 %%（默认 100，即贴满最长边）")
    g.add_argument("--offx", type=float, default=0, help="水平偏移，单位为画布尺寸的百分比")
    g.add_argument("--offy", type=float, default=0, help="垂直偏移，单位为画布尺寸的百分比")
    g.add_argument("--overflow", action="store_true",
                   help="放大溢出时裁掉超出部分（配合 --zoom >100 或大偏移使用）")

    g = p.add_argument_group("相机信息水印")
    g.add_argument("--no-info", action="store_true",
                   help="关闭相机信息水印（默认启用，读 EXIF 自动识别厂商机型）")
    g.add_argument("--info-bar", action="store_true",
                   help="信息区加纯色底条（贴合照片下边缘，文字颜色自动适配）")
    g.add_argument("--info-bar-color", type=parse_color, default=(26, 26, 26),
                   help="底条颜色（默认近黑）")
    g.add_argument("--info-scale", type=int, default=100,
                   help="信息文字整体字号缩放 %%（50~220，100=自动默认）")
    g.add_argument("--info-weight", type=int, default=500,
                   help="信息文字字重：300细 / 400常规 / 500中等 / 700粗")
    g.add_argument("--brand", default="auto",
                   help="厂商：auto 自动识别，或 " + "/".join(b[0] for b in BRANDS))
    g.add_argument("--info-anchor", type=parse_anchor, default=ANCHOR_PRESETS["bl"],
                   help="信息栏位置（默认 bl 左下）")
    g.add_argument("--info-size", type=int, default=0, help="字号，0=按图幅自动")
    g.add_argument("--info-color", type=parse_color, default=(255, 255, 255), help="信息文字颜色")
    g.add_argument("--info-bg", type=parse_color, default=None, help="背板颜色（默认按厂商习惯自动）")
    g.add_argument("--info-opacity", type=float, default=0.9, help="信息水印不透明度 0~1")
    g.add_argument("--no-model", action="store_true", help="不显示机型")
    g.add_argument("--no-brand", action="store_true", help="不显示厂商字标")
    g.add_argument("--lens", action="store_true", help="显示镜头名")
    g.add_argument("--no-aperture", action="store_true", help="经典排版不显示光圈")
    g.add_argument("--no-shutter", action="store_true", help="经典排版不显示快门")
    g.add_argument("--no-exp", action="store_true", help="角标模式不显示光圈/快门")
    g.add_argument("--no-iso", action="store_true", help="不显示 ISO")
    g.add_argument("--model-text", default="", help="自定义机型行文字（留空用 EXIF）")
    g.add_argument("--label-lang", choices=["en", "zh", "none"], default="en",
                   help="经典排版参数行标签语言")
    g.add_argument("--focal", dest="focal", action="store_true", default=True,
                   help="显示焦距（默认开，经典排版需要）")
    g.add_argument("--no-focal", dest="focal", action="store_false", help="不显示焦距")
    g.add_argument("--eq35", action="store_true", help="显示 35mm 等效焦距")
    g.add_argument("--date", dest="date", action="store_true", default=True,
                   help="显示拍摄日期（经典排版）")
    g.add_argument("--no-date", dest="date", action="store_false", help="不显示日期")

    g = p.add_argument_group("输出")
    g.add_argument("-f", "--format", choices=["keep", "jpg", "png"], default="keep",
                   help="输出格式，默认沿用原格式")
    g.add_argument("-q", "--quality", type=int, default=95, help="JPEG 质量 1~100")
    g.add_argument("--no-meta", action="store_true", help="丢弃 EXIF / ICC 信息")
    g.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出文件")
    g.add_argument("-j", "--jobs", type=int, default=0, help="并发进程数，0=自动")
    return p


def main() -> int:
    parser = build_parser()
    a = parser.parse_args()

    # 不给图片路径时，改为打印友好的上手提示，而不是抛 argparse 的用法错误
    if not a.inputs:
        print(__doc__.strip())
        print("\n" + "=" * 60)
        print("快速上手：把图片或文件夹路径放在脚本名后面即可\n")
        print("  # 补白边成正方形")
        print("  python squareplate.py 你的图片目录 -o out/")
        print()
        print("  # 指定 1080 方图 + 右下角半透明水印")
        print('  python squareplate.py 你的图片目录 -o out/ --size 1080 --text "@my_ig" --anchor br')
        print()
        print("  # 中文水印（务必指定中文字体）")
        print('  python squareplate.py a.jpg -o out/ --text "南信大" --font "C:/Windows/Fonts/msyh.ttc"')
        print()
        print("  # 毛玻璃边框 + 斜向平铺防盗图")
        print('  python squareplate.py 你的图片目录 -o out/ --border blur --text "© 2026" --tile')
        print()
        print("  # 贴 logo")
        print("  python squareplate.py 你的图片目录 -o out/ --logo logo.png --logo-anchor br")
        print()
        print("  # 递归整个目录树；发图前剥离 EXIF/GPS")
        print("  python squareplate.py 你的图片目录 -o out/ -r --no-meta")
        print("\n" + "=" * 60)
        print("完整参数见 README.md，或加 -h 查看。")
        print()
        return 0

    files = collect_inputs(a.inputs, a.recursive)
    if not files:
        print("没有找到可处理的图片。")
        return 1

    out_root = Path(a.output)
    border_style = "shadow" if a.shadow else a.border

    jobs = []
    for f in files:
        # 保持原目录结构，避免不同文件夹同名文件互相覆盖
        if len(a.inputs) == 1 and Path(a.inputs[0]).is_dir():
            rel = f.relative_to(a.inputs[0])
            stem = rel.with_suffix("").as_posix().replace("/", "__")
        else:
            stem = f.stem
        ext = f.suffix.lower()

        if a.format == "keep":
            fmt = "jpg" if ext in {".jpg", ".jpeg"} else "png"
        else:
            fmt = a.format
        ext_out = ".jpg" if fmt == "jpg" else ".png"
        dst = out_root / f"{stem}_sq{ext_out}"

        jobs.append((
            str(f), str(dst), border_style, a.border_color, a.blur,
            a.text, a.font, a.font_size, a.color, a.opacity, a.stroke, a.stroke_color,
            a.text_shadow, a.anchor, a.margin, a.tile, a.line_spacing,
            a.logo, a.logo_ratio, a.logo_opacity, a.logo_anchor, a.logo_margin,
            fmt, not a.no_meta, a.overwrite,
            a.zoom,
            not a.no_info, a.brand,
            not a.no_model, not a.no_brand,
            a.focal, not a.no_aperture, not a.no_shutter, not a.no_iso,
            a.eq35, a.date,
            a.quality_level,
            (a.wcolor == "auto") and not a.no_wauto, a.wcolor, a.model_text, a.label_lang,
            a.info_bar, a.info_bar_color,
            a.info_scale, a.info_weight,
        ))

    n_proc = a.jobs if a.jobs > 0 else min(8, max(1, (len(jobs) // 8) + 1))
    print(f"共 {len(jobs)} 张图，并发 {n_proc} 进程，输出到 {out_root}/")

    if n_proc == 1 or len(jobs) == 1:
        results = [process_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=n_proc) as ex:
            results = list(ex.map(process_one, jobs))

    for r in results:
        print(r)

    ok = sum(1 for r in results if r.startswith("ok"))
    skip = sum(1 for r in results if r.startswith("skip"))
    bad = [r for r in results if r.startswith("FAIL")]

    print(f"\n完成：成功 {ok}，跳过 {skip}，失败 {len(bad)}")
    for b in bad:
        print("  " + b)
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
