"""Egyszeri szkript az app ikon (harang motivum) legenaralasahoz."""
from PIL import Image, ImageDraw
import math
import os

SIZE = 1024
BG = "#14161c"
PANEL = "#1d202a"
ACCENT = "#6366f1"
ACCENT_HOVER = "#4f46e5"
TEXT = "#e7e9ee"


def rounded_rect(draw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def make_base():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Hatterlemez: lekerekitett negyzet, finom fenyes gradienssel
    margin = 0
    radius = int(SIZE * 0.22)
    rounded_rect(draw, [margin, margin, SIZE - margin, SIZE - margin], radius, BG)

    # Halvany radialis feny a tetejen (panel szinnel), hogy melysege legyen
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = SIZE * 0.5, SIZE * 0.32
    max_r = SIZE * 0.75
    steps = 120
    for i in range(steps, 0, -1):
        r = max_r * i / steps
        alpha = int(38 * (1 - i / steps))
        gdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(99, 102, 241, alpha))
    mask = Image.new("L", (SIZE, SIZE), 0)
    mdraw = ImageDraw.Draw(mask)
    rounded_rect(mdraw, [margin, margin, SIZE - margin, SIZE - margin], radius, 255)
    img = Image.composite(glow, img, Image.new("L", (SIZE, SIZE), 0))
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(base)
    rounded_rect(bdraw, [margin, margin, SIZE - margin, SIZE - margin], radius, BG)
    base = Image.alpha_composite(base, Image.composite(glow, Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)), mask))

    return base, mask


def draw_bell(base):
    draw = ImageDraw.Draw(base)
    cx = SIZE * 0.5
    cy = SIZE * 0.47
    w = SIZE * 0.40  # a harang test szelessege also szelen

    # Harang test: felkor teteje + ivelt oldalak, klasszikus csengo sziluett
    top_r = SIZE * 0.15
    top_cy = cy - SIZE * 0.10

    points = []
    n = 60
    # bal oldal felulrol lefele ivelve kifele
    for i in range(n + 1):
        t = i / n
        angle = math.pi + t * math.pi  # felkor also fele -> teteje
        x = cx + top_r * math.cos(angle)
        y = top_cy + top_r * math.sin(angle)
        points.append((x, y))

    body_top_y = top_cy
    body_bottom_y = cy + SIZE * 0.17
    left_top_x = cx - top_r
    right_top_x = cx + top_r
    left_bottom_x = cx - w / 2
    right_bottom_x = cx + w / 2

    outline = []
    outline.append((cx - top_r * 0.02, top_cy - top_r))
    steps = 40
    for i in range(steps + 1):
        t = i / steps
        angle = math.pi - t * math.pi
        x = cx - top_r * math.cos(angle * 0 + 0)
    # egyszerubb: kezi bezier-szeru pontok a jobb oldalra, majd tukrozve a balra
    right_side = []
    for i in range(steps + 1):
        t = i / steps
        y = body_top_y + t * (body_bottom_y - body_top_y)
        curve = math.sin(t * math.pi * 0.5) ** 1.6
        x = right_top_x + curve * (right_bottom_x - right_top_x)
        right_side.append((x, y))

    top_arc = []
    arc_steps = 40
    for i in range(arc_steps + 1):
        t = i / arc_steps
        angle = math.pi - t * math.pi
        x = cx + top_r * math.cos(angle)
        y = top_cy - top_r * math.sin(angle)
        top_arc.append((x, y))

    left_side = [(cx - (x - cx), y) for x, y in reversed(right_side)]

    polygon = top_arc + right_side + [(right_bottom_x, body_bottom_y), (left_bottom_x, body_bottom_y)] + left_side

    draw.polygon(polygon, fill=ACCENT)

    # also perem (kicsit szelesebb savsav)
    rim_h = SIZE * 0.028
    draw.rounded_rectangle(
        [left_bottom_x - SIZE * 0.02, body_bottom_y - rim_h * 0.3,
         right_bottom_x + SIZE * 0.02, body_bottom_y + rim_h],
        radius=rim_h,
        fill=ACCENT_HOVER,
    )

    # nyelv (clapper)
    clapper_cy = body_bottom_y + rim_h + SIZE * 0.045
    clapper_r = SIZE * 0.028
    draw.ellipse(
        [cx - clapper_r, clapper_cy - clapper_r, cx + clapper_r, clapper_cy + clapper_r],
        fill=ACCENT_HOVER,
    )

    # felso fogantyu
    handle_r = SIZE * 0.028
    draw.ellipse(
        [cx - handle_r, top_cy - top_r - handle_r * 1.6,
         cx + handle_r, top_cy - top_r + handle_r * 0.4],
        fill=ACCENT,
    )

    # hangvonalak (csenges) mindket oldalon
    def sound_lines(sign):
        base_x = cx + sign * (w / 2 + SIZE * 0.045)
        for i, (dx, dy, rr) in enumerate([
            (0.055, -0.02, 0.10),
            (0.10, -0.02, 0.16),
        ]):
            bbox = [
                base_x + sign * dx * SIZE - rr * SIZE,
                cy - 0.02 * SIZE - rr * SIZE,
                base_x + sign * dx * SIZE + rr * SIZE,
                cy - 0.02 * SIZE + rr * SIZE,
            ]
            start, end = (110, 250) if sign > 0 else (290, 70)
            draw.arc(bbox, start=start, end=end, fill=TEXT, width=int(SIZE * 0.018))

    sound_lines(1)
    sound_lines(-1)

    return base


def main():
    base, _ = make_base()
    base = draw_bell(base)

    out_dir = os.path.join(os.path.dirname(__file__))
    base.save(os.path.join(out_dir, "icon-1024.png"))

    for size in [512, 192, 180, 32, 16]:
        resized = base.resize((size, size), Image.LANCZOS)
        resized.save(os.path.join(out_dir, f"icon-{size}.png"))

    # favicon.ico tobbmeretes
    icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64)]
    imgs = [base.resize(s, Image.LANCZOS) for s in icon_sizes]
    imgs[0].save(os.path.join(out_dir, "favicon.ico"), format="ICO", sizes=icon_sizes)

    print("Kesz.")


if __name__ == "__main__":
    main()
