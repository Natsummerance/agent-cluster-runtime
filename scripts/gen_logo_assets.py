#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# gen_logo_assets.py -- DoAI Workbench 品牌 LOGO 参数化生成器（bite-v0 定稿）
#
# 用法：
#   python scripts/gen_logo_assets.py          # 需 Pillow（系统 python 已带；uv 环境可用 uv run --with pillow）
#
# 输出资产：
#   desktop/build/icon-source.svg      咬环符号源（1024 透明，含参数注释，electron-builder buildResources）
#   desktop/build/icon.png             1024 深海蓝圆角方块版（Windows 应用/安装包图标）
#   desktop/build/icon.ico             16/32/48/64/128/256 @32bpp 多尺寸
#   frontend/public/logo.svg           透明底深蓝符号（前端 favicon + 页眉）
#   docs/logo.svg                      同上（README 引用）
#   docs/logo-with-text.svg            横排字标（符号 + DoAI Workbench）
#
# 日后替换 LOGO：只改下方「几何参数」区，重跑本脚本即可，无需触碰品牌迁移代码。

import math
import os

from PIL import Image, ImageDraw

# ---------------------------------------------------------------- 几何参数
CANVAS = 1024
INK = "#1B2A4A"          # 深海蓝（符号主体，Anthropic 家族深色）
PAPER = "#F5F7FA"        # 奶油纸白（深底反白）
SW = 60                  # 环线宽（圆帽）
RING_R = 215             # 主环半径
RING_CX, RING_CY = 512, 440
BITE_R = 150             # 左 bite 内凹弧半径
TOP = (512, 225)         # 环顶
BOT = (512, 655)         # 环底
LEFT_TOP = (325.8, 332.5)   # 左半线上接点（bite 上端）
LEFT_BOT = (325.8, 547.5)   # 左半线下接点（bite 下端）
A_PTS = [(555, 345), (505, 460), (605, 460)]   # A 三角
I_BAR = (628, 345, 658, 460)                   # I 竖线 x0 y0 x1 y1
I_RX = 13
SQUARE_BOX = (32, 32, 992, 992)                # 圆角方块外框
SQUARE_RX = 190
ARC_LEFT = ("M 512 225 A 215 215 0 0 0 325.8 332.5 "
            "A 150 150 0 0 1 325.8 547.5 A 215 215 0 0 0 512 655")
ARC_RIGHT = "M 512 225 A 215 215 0 0 1 512 655"
# ------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ring_pt(deg):
    r = math.radians(deg)
    return (RING_CX + RING_R * math.cos(r), RING_CY + RING_R * math.sin(r))


def bite_pt(deg):
    # bite 圆弧中心：弦中点沿垂直方向（向环内/左侧）偏移 d，弧向右鼓
    chord_mid_x = (LEFT_TOP[0] + LEFT_BOT[0]) / 2.0
    half = (LEFT_BOT[1] - LEFT_TOP[1]) / 2.0
    d = math.sqrt(max(BITE_R * BITE_R - half * half, 0.0))
    cx, cy = chord_mid_x - d, (LEFT_TOP[1] + LEFT_BOT[1]) / 2.0
    r = math.radians(deg)
    return (cx + BITE_R * math.cos(r), cy + BITE_R * math.sin(r))


def left_polyline(step=2.0):
    pts = []
    d = -90.0
    while d >= -150.0:
        pts.append(ring_pt(d))
        d -= step
    pts.append(LEFT_TOP)
    d = -45.8
    while d <= 45.8:
        pts.append(bite_pt(d))
        d += step
    pts.append(LEFT_BOT)
    d = 150.0
    while d >= 90.0:
        pts.append(ring_pt(d))
        d -= step
    return pts


def right_polyline(step=2.0):
    pts = []
    d = -90.0
    while d <= 90.0:
        pts.append(ring_pt(d))
        d += step
    return pts


def draw_glyph(draw, color, s):
    # s = 超采样倍率
    w = SW * s
    for pts in (left_polyline(), right_polyline()):
        draw.line([(p[0] * s, p[1] * s) for p in pts], fill=color, width=w, joint="curve")
    for cap in (TOP, BOT):
        draw.ellipse([cap[0] * s - w / 2, cap[1] * s - w / 2,
                      cap[0] * s + w / 2, cap[1] * s + w / 2], fill=color)
    draw.polygon([(p[0] * s, p[1] * s) for p in A_PTS], fill=color)
    draw.rounded_rectangle([v * s for v in I_BAR], radius=I_RX * s, fill=color)


def glyph_svg(foreground, header_comments=""):
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" '
            'viewBox="0 0 1024 1024">')
    body = (
        '<path d="%s" fill="none" stroke="%s" stroke-width="%d" stroke-linecap="round"/>'
        '<path d="%s" fill="none" stroke="%s" stroke-width="%d" stroke-linecap="round"/>'
        '<path d="M 555 345 L 505 460 L 605 460 Z" fill="%s"/>'
        '<rect x="628" y="345" width="30" height="115" rx="13" fill="%s"/>'
    ) % (ARC_LEFT, foreground, SW, ARC_RIGHT, foreground, SW, foreground, foreground)
    return head + body + "</svg>"


def render(canvas_size=CANVAS, square=False):
    s = 4  # 超采样
    size = canvas_size * s
    if square:
        im = Image.new("RGB", (size, size), "white")
    else:
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    if square:
        draw.rounded_rectangle([v * s for v in SQUARE_BOX], radius=SQUARE_RX * s, fill=INK)
        draw_glyph(draw, PAPER, s)
    else:
        draw_glyph(draw, INK, s)
    return im.resize((canvas_size, canvas_size), Image.LANCZOS)


def main():
    root = REPO_ROOT
    paths = {
        "icon-source.svg": os.path.join(root, "desktop", "build", "icon-source.svg"),
        "icon.png": os.path.join(root, "desktop", "build", "icon.png"),
        "icon.ico": os.path.join(root, "desktop", "build", "icon.ico"),
        "frontend-logo.svg": os.path.join(root, "frontend", "public", "logo.svg"),
        "docs-logo.svg": os.path.join(root, "docs", "logo.svg"),
        "logo-with-text.svg": os.path.join(root, "docs", "logo-with-text.svg"),
    }
    for p in paths.values():
        os.makedirs(os.path.dirname(p), exist_ok=True)

    params = (
        "<!-- DoAI Workbench LOGO 源（bite-v0 定稿）\n"
        "     几何参数（1024 画布，与 scripts/gen_logo_assets.py 保持一致）：\n"
        "     INK=#1B2A4A PAPER=#F5F7FA SW=60 RING_R=215 RING=(512,440)\n"
        "     BITE_R=150 LEFT_TOP=(325.8,332.5) LEFT_BOT=(325.8,547.5)\n"
        "     A=(555,345)-(505,460)-(605,460) I=628,345,30x115 rx13\n"
        "     左半线（含 bite 内凹弧）：M 512 225 A 215 215 0 0 0 325.8 332.5\n"
        "       A 150 150 0 0 1 325.8 547.5 A 215 215 0 0 0 512 655\n"
        "     右半环：M 512 225 A 215 215 0 0 1 512 655\n"
        "     替换 LOGO：改 scripts/gen_logo_assets.py 参数后重跑即可 -->\n"
    )
    with open(paths["icon-source.svg"], "w", encoding="utf-8") as f:
        f.write(params + glyph_svg(INK))

    square_im = render(square=True)
    square_im.save(paths["icon.png"])
    square_im.save(paths["icon.ico"], sizes=[(16, 16), (32, 32), (48, 48),
                                             (64, 64), (128, 128), (256, 256)])

    glyph = glyph_svg(INK)
    with open(paths["frontend-logo.svg"], "w", encoding="utf-8") as f:
        f.write(glyph)
    with open(paths["docs-logo.svg"], "w", encoding="utf-8") as f:
        f.write(glyph)

    wordmark = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1024" '
        'viewBox="0 0 2400 1024">'
        '<g transform="translate(150,0)">'
        '<path d="%s" fill="none" stroke="%s" stroke-width="60" stroke-linecap="round"/>'
        '<path d="%s" fill="none" stroke="%s" stroke-width="60" stroke-linecap="round"/>'
        '<path d="M 555 345 L 505 460 L 605 460 Z" fill="%s"/>'
        '<rect x="628" y="345" width="30" height="115" rx="13" fill="%s"/>'
        "</g>"
        '<text x="1000" y="668" font-family="Inter, -apple-system, Segoe UI, '
        'PingFang SC, Microsoft YaHei, sans-serif" font-size="190" font-weight="700" '
        'fill="%s">DoAI Workbench</text></svg>'
    ) % (ARC_LEFT, INK, ARC_RIGHT, INK, INK, INK, INK)
    with open(paths["logo-with-text.svg"], "w", encoding="utf-8") as f:
        f.write(wordmark)

    print("generated:")
    for k, p in paths.items():
        print("  %-18s %s (%d bytes)" % (k, p, os.path.getsize(p)))


if __name__ == "__main__":
    main()
