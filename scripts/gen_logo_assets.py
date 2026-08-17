#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# gen_logo_assets.py -- DoAI Workbench 品牌 LOGO 参数化生成器（bite-v2 定稿）
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
BITE_R = 135             # 左 bite 内凹弧半径（更深、更像自然咬痕）
TOP = (512, 225)         # 环顶
BOT = (512, 655)         # 环底
LEFT_TOP = (325.8, 332.5)   # 左半线上接点（bite 上端）
LEFT_BOT = (340.8, 570.0)   # 左半线下接点（沿环弧下移，底部更饱满）
A_PTS = [(555, 345), (505, 460), (605, 460)]   # A 三角
I_BAR = (628, 345, 658, 460)                   # I 竖线 x0 y0 x1 y1
I_RX = 15
SQUARE_BOX = (32, 32, 992, 992)                # 圆角方块外框
SQUARE_RX = 190
ARC_LEFT = ("M 512 225 A 215 215 0 0 0 325.8 332.5 "
            "A 135 135 0 0 0 340.8 570 A 215 215 0 0 0 512 655")
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


def rounded_polygon_points(vertices, radius, samples=8):
    """Return a polygon whose corners are quadratic Bézier rounds."""
    corners = []
    for i, point in enumerate(vertices):
        prev_point = vertices[i - 1]
        next_point = vertices[(i + 1) % len(vertices)]

        def toward(other):
            dx = other[0] - point[0]
            dy = other[1] - point[1]
            length = math.hypot(dx, dy)
            distance = min(radius, length / 2)
            return (point[0] + dx / length * distance,
                    point[1] + dy / length * distance)

        corners.append((toward(prev_point), toward(next_point)))

    result = [corners[0][0]]
    for i, point in enumerate(vertices):
        incoming = corners[i][0]
        outgoing = corners[i][1]
        if i > 0:
            result.append(incoming)
        for step in range(1, samples + 1):
            t = step / samples
            result.append((
                (1 - t) ** 2 * incoming[0] + 2 * (1 - t) * t * point[0] + t ** 2 * outgoing[0],
                (1 - t) ** 2 * incoming[1] + 2 * (1 - t) * t * point[1] + t ** 2 * outgoing[1],
            ))
    return result


def rounded_polygon_path(vertices, radius):
    """Return an SVG path for the same rounded polygon used by the raster asset."""
    corners = []
    for i, point in enumerate(vertices):
        prev_point = vertices[i - 1]
        next_point = vertices[(i + 1) % len(vertices)]

        def toward(other):
            dx = other[0] - point[0]
            dy = other[1] - point[1]
            length = math.hypot(dx, dy)
            distance = min(radius, length / 2)
            return (point[0] + dx / length * distance,
                    point[1] + dy / length * distance)

        corners.append((toward(prev_point), toward(next_point)))

    d = ["M %.2f %.2f" % corners[0][0]]
    for i, point in enumerate(vertices):
        next_i = (i + 1) % len(vertices)
        d.append("Q %.2f %.2f %.2f %.2f" % (point[0], point[1], corners[i][1][0], corners[i][1][1]))
        d.append("L %.2f %.2f" % corners[next_i][0])
    return " ".join(d) + " Z"


def left_polyline(step=2.0):
    pts = []
    d = -90.0
    left_top_angle = math.degrees(math.atan2(LEFT_TOP[1] - RING_CY, LEFT_TOP[0] - RING_CX))
    left_bot_angle = math.degrees(math.atan2(LEFT_BOT[1] - RING_CY, LEFT_BOT[0] - RING_CX))
    while d >= left_top_angle:
        pts.append(ring_pt(d))
        d -= step
    pts.append(LEFT_TOP)
    bite_mid_x = (LEFT_TOP[0] + LEFT_BOT[0]) / 2.0
    bite_mid_y = (LEFT_TOP[1] + LEFT_BOT[1]) / 2.0
    half = (LEFT_BOT[1] - LEFT_TOP[1]) / 2.0
    offset = math.sqrt(max(BITE_R * BITE_R - half * half, 0.0))
    bite_cx = bite_mid_x - offset
    bite_start = math.degrees(math.atan2(LEFT_TOP[1] - bite_mid_y, LEFT_TOP[0] - bite_cx))
    bite_end = math.degrees(math.atan2(LEFT_BOT[1] - bite_mid_y, LEFT_BOT[0] - bite_cx))
    d = bite_start
    while d <= bite_end:
        pts.append(bite_pt(d))
        d += step
    pts.append(LEFT_BOT)
    d = left_bot_angle
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
        scaled = [(p[0] * s, p[1] * s) for p in pts]
        draw.line(scaled, fill=color, width=w)
        for x, y in scaled:
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)
    for cap in (TOP, BOT):
        draw.ellipse([cap[0] * s - w / 2, cap[1] * s - w / 2,
                      cap[0] * s + w / 2, cap[1] * s + w / 2], fill=color)
    draw.polygon([(p[0] * s, p[1] * s) for p in rounded_polygon_points(A_PTS, 14)], fill=color)
    draw.rounded_rectangle([v * s for v in I_BAR], radius=I_RX * s, fill=color)


def glyph_svg(foreground, header_comments=""):
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" '
            'viewBox="0 0 1024 1024">')
    body = (
        '<path d="%s" fill="none" stroke="%s" stroke-width="%d" stroke-linecap="round"/>'
        '<path d="%s" fill="none" stroke="%s" stroke-width="%d" stroke-linecap="round"/>'
        '<path d="%s" fill="%s"/>'
        '<rect x="628" y="345" width="30" height="115" rx="13" fill="%s"/>'
    ) % (ARC_LEFT, foreground, SW, ARC_RIGHT, foreground, SW,
         rounded_polygon_path(A_PTS, 14), foreground, foreground)
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
        "<!-- DoAI Workbench LOGO 源（bite-v2 定稿）\n"
        "     几何参数（1024 画布，与 scripts/gen_logo_assets.py 保持一致）：\n"
        "     INK=#1B2A4A PAPER=#F5F7FA SW=60 RING_R=215 RING=(512,440)\n"
        "     BITE_R=135 LEFT_TOP=(325.8,332.5) LEFT_BOT=(340.8,570)\n"
        "     A=(555,345)-(505,460)-(605,460) rounded corners, I=628,345,30x115 rx15\n"
        "     左半线（含 bite 内凹弧）：M 512 225 A 215 215 0 0 0 325.8 332.5\n"
        "       A 135 135 0 0 0 340.8 570 A 215 215 0 0 0 512 655\n"
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
        '<path d="%s" fill="%s"/>'
        '<rect x="628" y="345" width="30" height="115" rx="13" fill="%s"/>'
        "</g>"
        '<text x="1000" y="668" font-family="Inter, -apple-system, Segoe UI, '
        'PingFang SC, Microsoft YaHei, sans-serif" font-size="190" font-weight="700" '
        'fill="%s">DoAI Workbench</text></svg>'
    ) % (ARC_LEFT, INK, ARC_RIGHT, INK, rounded_polygon_path(A_PTS, 14), INK, INK, INK)
    with open(paths["logo-with-text.svg"], "w", encoding="utf-8") as f:
        f.write(wordmark)

    print("generated:")
    for k, p in paths.items():
        print("  %-18s %s (%d bytes)" % (k, p, os.path.getsize(p)))


if __name__ == "__main__":
    main()
