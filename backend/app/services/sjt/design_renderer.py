"""
Draws a candidate's web page layout as a picture (PNG) so a vision model can LOOK at it.

The editor in the browser is plain boxes and text on a 1200 px canvas, so the same page can be drawn on the server from the stored layout without a
browser: every element is painted in the order it was stacked, clipped to its own box, with the same fills, corner radii, borders, alignment and
text wrapping the editor shows. The picture is drawn here, from validated data, so nothing the candidate's browser sends as an image is ever trusted.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1200
MIN_HEIGHT, MAX_HEIGHT = 500, 1700
DEFAULT_SCALE = 0.8

_FONT_FILES = {
    False: [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    True: [r"C:\Windows\Fonts\segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
}

_font_cache: dict = {}


def _font(size: float, bold: bool):

    key = (round(size), bold)

    if key in _font_cache:
        return _font_cache[key]

    font = None
    real_bold = False

    for path in _FONT_FILES[bold]:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, max(6, round(size)))
                real_bold = bold
                break
            except OSError:
                font = None

    if font is None:
        font = ImageFont.load_default(size=max(6, round(size)))          # Pillow's own scalable font: always available

    font._real_bold = real_bold                                         # a bold face was found; otherwise bold text is drawn with a stroke

    _font_cache[key] = font
    return font


def _rgb(hex_color: str, default=(17, 24, 39)):
    try:
        return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        return default


def _wrap(draw, text: str, font, width: float) -> list[str]:

    lines = []

    for paragraph in str(text).split("\n"):

        words = paragraph.split(" ")
        current = ""

        for word in words:
            trial = (current + " " + word).strip()
            if current and draw.textlength(trial, font=font) > width:
                lines.append(current)
                current = word
            else:
                current = trial

        lines.append(current)

    return lines


def _draw_element(canvas: Image.Image, e: dict, s: float) -> None:

    w, h = max(1, round(e["w"] * s)), max(1, round(e["h"] * s))
    x, y = round(e["x"] * s), round(e["y"] * s)
    kind = e["type"]

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    radius = round(e["radius"] * s)
    fill = _rgb(e["bg"]) if e["bg"] else None
    border = None

    if kind == "card":
        border = (225, 227, 236)
    elif kind == "input":
        border = (170, 174, 190)
    elif kind == "image":
        border = (140, 146, 160)

    box = [0, 0, w - 1, h - 1]

    if fill is not None or border is not None:
        draw.rounded_rectangle(box, radius=min(radius, h // 2, w // 2), fill=fill, outline=border, width=max(1, round(1.5 * s)) if border else 0)

    if kind == "image":
        draw.line([0, 0, w - 1, h - 1], fill=(160, 165, 178), width=max(1, round(s)))
        draw.line([0, h - 1, w - 1, 0], fill=(160, 165, 178), width=max(1, round(s)))

    text = e["text"]

    if text and kind != "divider":

        size = e["fontSize"] * s
        bold = e["fontWeight"] >= 600
        font = _font(size, bold)
        color = _rgb(e["color"])
        pad = round({"nav": 24, "footer": 24, "card": 16, "input": 14}.get(kind, 0) * s)
        label = ("[image] " + text) if kind == "image" else text
        lines = _wrap(draw, label, font, max(10, w - 2 * pad))
        line_height = size * 1.3
        block = line_height * len(lines)

        centered_vertically = kind in ("nav", "footer", "button", "input", "image")
        top = (h - block) / 2 if centered_vertically else (pad if kind == "card" else 0)

        for index, line in enumerate(lines):

            line_width = draw.textlength(line, font=font)
            align = "center" if kind in ("button", "image") else e["align"]
            left = (w - line_width) / 2 if align == "center" else (w - pad - line_width) if align == "right" else pad
            position = (left, top + index * line_height + (line_height - size) / 2 - size * 0.12)

            fake_bold = bold and not getattr(font, "_real_bold", False)
            draw.text(position, line, font=font, fill=color, stroke_width=1 if fake_bold else 0, stroke_fill=color)

    # a soft shadow under cards, like the editor
    if kind == "card":
        shadow = Image.new("RGBA", (w + 6, h + 8), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([3, 5, w + 2, h + 4], radius=min(radius, 24), fill=(20, 24, 58, 28))
        canvas.paste(shadow, (x - 3, y - 2), shadow)

    canvas.paste(layer, (x, y), layer)


def render_png(layout: dict, scale: float = DEFAULT_SCALE) -> bytes:
    """The page as a PNG: white page, elements in stacking order. The height follows the content (500 to 1700 px at full size)."""

    elements = layout.get("elements") or []
    bottom = max([e["y"] + e["h"] for e in elements] or [0]) + 40
    height = max(MIN_HEIGHT, min(MAX_HEIGHT, bottom))

    canvas = Image.new("RGBA", (round(CANVAS_W * scale), round(height * scale)), (255, 255, 255, 255))

    for e in elements:
        _draw_element(canvas, e, scale)

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)

    return output.getvalue()
