"""
Generates the default "AS BUILT MARK UP" stamp image used by AutoStamp.

This recreates the company stamp (YANDA "AS BUILT MARK UP" block) as a PNG
with a transparent background so it can be dropped onto drawings cleanly.

NOTE: This is a best-effort re-creation built from a description of the
original stamp graphic (no source image file was available when this tool
was built). If you have the *actual* stamp image/logo file, just replace
assets/as_built_stamp.png with it (or point the GUI's "Stamp image" field
at your file) -- the auto-stamper works with any PNG/JPG stamp image, this
script only exists to produce a reasonable default so the tool works out
of the box.

Run:  python3 assets/generate_stamp.py
"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1200, 580
SCALE = 1  # supersampling handled by drawing at this resolution directly

RED = (196, 22, 28, 255)
DARK_RED = (150, 15, 20, 255)
GREEN = (0, 140, 62, 255)
BLUE = (18, 74, 163, 255)
YELLOW = (214, 140, 0, 255)
BLACK = (20, 20, 20, 255)
WHITE = (255, 255, 255, 255)
LOGO_BLUE = (16, 82, 155, 255)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


F_TITLE = font("DejaVuSans-Bold.ttf", 58)
F_CERT = font("DejaVuSans-Bold.ttf", 22)
F_CODE = font("DejaVuSans-Bold.ttf", 24)
F_LABEL = font("DejaVuSans-Bold.ttf", 20)
F_VALUE = font("DejaVuSans-Bold.ttf", 26)
F_LOGO = font("DejaVuSans-Bold.ttf", 40)
F_LOGO_SUB = font("DejaVuSans-Bold.ttf", 13)


def centered_text(draw, cx, y, text, fnt, fill):
    bbox = draw.textbbox((0, 0), text, font=fnt)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=fnt, fill=fill)


def draw_logo(draw, x, y, w, h):
    # Blue rounded box
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=LOGO_BLUE)
    # Simple pinwheel/"Y" triangle mark, three triangles around a center point
    cx, cy, r = x + h * 0.5, y + h * 0.5, h * 0.32
    import math
    for i in range(3):
        ang = math.radians(90 + i * 120)
        ang2 = math.radians(90 + i * 120 + 100)
        p1 = (cx, cy)
        p2 = (cx + r * math.cos(ang), cy - r * math.sin(ang))
        p3 = (cx + r * math.cos(ang2), cy - r * math.sin(ang2))
        draw.polygon([p1, p2, p3], fill=WHITE)
    draw.ellipse([cx - r * 0.22, cy - r * 0.22, cx + r * 0.22, cy + r * 0.22], fill=LOGO_BLUE)

    tx = x + h + 14
    draw.text((tx, y + h * 0.16), "YANDA", font=F_LOGO, fill=WHITE)
    draw.text((tx + 2, y + h * 0.66), "YANDA CANADA", font=F_LOGO_SUB, fill=WHITE)


def build():
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)

    border = 6
    d.rectangle([border, border, W - border, H - border], outline=DARK_RED, width=3)

    pad = 34
    draw_logo(d, pad + 4, pad + 2, 230, 74)

    centered_text(d, W / 2, 34, "AS BUILT MARK UP", F_TITLE, RED)

    cert_lines = [
        "I CERTIFY THIS DRAWING IS MARKED UP TO AS BUILT",
        "AND ACCURATELY REFLECTS THE AS BUILT FIELD CONDTIONS",
        "USING THE FOLLOWING COLOUR CODES",
    ]
    cy = 128
    for line in cert_lines:
        centered_text(d, W / 2, cy, line, F_CERT, RED)
        cy += 30

    # Colour code legend, two columns
    legend_y = cy + 20
    left_x = 170
    right_x = 650
    d.text((left_x, legend_y), "RED", font=F_CODE, fill=RED)
    bbox = d.textbbox((left_x, legend_y), "RED", font=F_CODE)
    d.text((bbox[2] + 8, legend_y), "-ADDITIONS", font=F_CODE, fill=RED)

    d.text((right_x, legend_y), "GREEN", font=F_CODE, fill=GREEN)
    bbox = d.textbbox((right_x, legend_y), "GREEN", font=F_CODE)
    d.text((bbox[2] + 8, legend_y), "-DELETIONS", font=F_CODE, fill=GREEN)

    legend_y2 = legend_y + 40
    d.text((left_x, legend_y2), "BLUE", font=F_CODE, fill=BLUE)
    bbox = d.textbbox((left_x, legend_y2), "BLUE", font=F_CODE)
    d.text((bbox[2] + 8, legend_y2), "-COMMENTS", font=F_CODE, fill=BLUE)

    d.text((right_x, legend_y2), "YELLOW", font=F_CODE, fill=YELLOW)
    bbox = d.textbbox((right_x, legend_y2), "YELLOW", font=F_CODE)
    d.text((bbox[2] + 8, legend_y2), "-NO CHANGE", font=F_CODE, fill=YELLOW)

    # Bottom fields
    field_y = legend_y2 + 62
    label_x_l = 90
    value_x_l = 250
    label_x_r = 610
    value_x_r = 900

    d.text((label_x_l, field_y), "MARKED UP", font=F_LABEL, fill=RED)
    d.text((label_x_l, field_y + 24), "BY:", font=F_LABEL, fill=RED)
    d.text((value_x_l, field_y + 6), "Levy Ostrup", font=F_VALUE, fill=BLACK)
    d.line([value_x_l - 10, field_y + 44, value_x_l + 280, field_y + 44], fill=DARK_RED, width=2)

    d.text((label_x_r, field_y), "CHECKED/CERTIFIED", font=F_LABEL, fill=RED)
    d.text((label_x_r, field_y + 24), "BY:", font=F_LABEL, fill=RED)
    d.line([value_x_r - 10, field_y + 44, value_x_r + 240, field_y + 44], fill=DARK_RED, width=2)

    field_y2 = field_y + 78
    d.text((label_x_l, field_y2 + 6), "PHONE NO.:", font=F_LABEL, fill=RED)
    d.text((value_x_l, field_y2 - 2), "403-892-8877", font=F_VALUE, fill=BLACK)
    d.line([value_x_l - 10, field_y2 + 36, value_x_l + 280, field_y2 + 36], fill=DARK_RED, width=2)

    d.text((label_x_r, field_y2 + 6), "DATE:", font=F_LABEL, fill=RED)
    d.line([value_x_r - 10, field_y2 + 36, value_x_r + 240, field_y2 + 36], fill=DARK_RED, width=2)

    return img


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "as_built_stamp.png")
    build().save(out)
    print("wrote", out)
