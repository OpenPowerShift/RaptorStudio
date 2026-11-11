"""Generate data-driven SVG images for the tutorial book (no matplotlib)."""
import math
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "images")

W, H = 720, 400


def svg(name, body, W=W, H=H):
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="Helvetica, Arial, sans-serif">'
        '<rect width="%d" height="%d" fill="#fffdf7"/>%s</svg>'
        % (W, H, W, H, W, H, body)
    )
    path = os.path.join(OUT, name)
    with open(path, "w") as fh:
        fh.write(doc)
    print("wrote", path)


AX = "#5a5a52"
INK = "#2b3a55"


def axes(x0, y0, x1, y1, xlab, ylab):
    t = ""
    t += f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" stroke="{AX}" stroke-width="1.5"/>'
    t += f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="{AX}" stroke-width="1.5"/>'
    t += (f'<text x="{(x0+x1)/2}" y="{y0+44}" text-anchor="middle" '
          f'font-size="15" fill="{INK}">{xlab}</text>')
    t += (f'<text x="{x0-44}" y="{(y0+y1)/2}" text-anchor="middle" '
          f'font-size="15" fill="{INK}" transform="rotate(-90 {x0-44} {(y0+y1)/2})">{ylab}</text>')
    return t


# ------------------------------------------------------- dist_shapes.svg
x0, y0, x1, y1 = 70, 320, 660, 70
t = axes(x0, y0, x1, y1, "life of one part (hours)", "how likely")
xmax = 2000.0
ymax_plot = 100  # pixel row of the top of the plot area (must be above y0)


def poly(points, color, dash=None):
    pts = " ".join(f"{x0 + fx / xmax * (x1 - x0):.1f},{y0 - fy * (y0 - ymax_plot):.1f}"
                   for fx, fy in points)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="3"{d}/>')


# Fixed at 1000 h: one vertical line
spike_x = x0 + 1000 / xmax * (x1 - x0)
t += f'<line x1="{spike_x}" y1="{y0}" x2="{spike_x}" y2="{ymax_plot+10}" stroke="#c0392b" stroke-width="3"/>'
t += (f'<text x="{spike_x}" y="{ymax_plot}" text-anchor="middle" font-size="14" '
      f'fill="#c0392b">Fixed: dies at 1000 h, never before</text>')

# Exponential mean 1000: pdf f(t) = lam*exp(-lam*t), peak at 0
pts = []
for i in range(201):
    tt = xmax * i / 200
    fy = math.exp(-tt / 1000.0)  # pdf x 1000, so the peak is 1.0
    pts.append((tt, fy))
t += poly(pts, "#2980b9")
t += (f'<text x="{x0+130}" y="{y0-205}" font-size="14" fill="#2980b9">Exponential:</text>'
      f'<text x="{x0+130}" y="{y0-187}" font-size="13" fill="#2980b9">many early deaths,</text>'
      f'<text x="{x0+130}" y="{y0-170}" font-size="13" fill="#2980b9">long thin tail</text>')

# Weibull shape 1.5 scale 1100
def weib_pdf(tt, k=1.5, lam=1100.0):
    return (k / lam) * (tt / lam) ** (k - 1) * math.exp(-((tt / lam) ** k))

pts = []
for i in range(201):
    tt = xmax * i / 200
    pts.append((tt, weib_pdf(tt) * 1000.0))  # same scale as the exponential
t += poly(pts, "#27ae60", dash="8 5")
t += (f'<text x="{x0+400}" y="{y0-120}" font-size="14" fill="#27ae60">Weibull (shape 1.5):</text>'
      f'<text x="{x0+400}" y="{y0-102}" font-size="13" fill="#27ae60">fewer very early deaths</text>')

t += (f'<text x="{W/2}" y="34" text-anchor="middle" font-size="18" font-weight="bold" '
      f'fill="{INK}">Three ways to die near an average of 1000 hours</text>')
svg("dist-shapes.svg", t)


# ----------------------------------------------------- runs_converge.svg
# Reliability estimate vs number of trials for a Lantern-style p≈0.39 coin.
p_true = 0.385
seed = 12345


def rnd(state):
    state[0] = (1103515245 * state[0] + 12345) % (1 << 31)
    return state[0] / (1 << 31)

state = [seed]
succ = 0
pts = [(0, 0)]
for n in range(1, 501):
    succ += 1 if rnd(state) < p_true else 0
    if n >= 5:
        pts.append((float(n), succ / n))

N_LO, N_HI, V_MAX = 5.0, 500.0, 0.8
def px(n): return x0 + (math.log10(n) - math.log10(N_LO)) / (math.log10(N_HI) - math.log10(N_LO)) * (x1 - x0)
def py(v): return y0 - v / V_MAX * (y0 - ymax_plot)

seg = " ".join(f"{px(n):.1f},{py(v):.1f}" for n, v in pts[1:])
# +/- one standard error around the true value: the funnel the wobble settles into
up = [(n, p_true + math.sqrt(p_true * (1 - p_true) / n)) for n in range(5, 501, 5)]
dn = [(n, p_true - math.sqrt(p_true * (1 - p_true) / n)) for n in range(5, 501, 5)]
band = " ".join(f"{px(n):.1f},{py(v):.1f}" for n, v in up + dn[::-1])
t2 = axes(x0, y0, x1, y1, "number of trials (log scale)", "answer so far")
t2 += f'<polygon points="{band}" fill="#8e44ad" fill-opacity="0.12"/>'
for g in (0.0, 0.2, 0.4, 0.6, 0.8):
    t2 += (f'<line x1="{x0-5}" y1="{py(g):.1f}" x2="{x0}" y2="{py(g):.1f}" stroke="{AX}"/>'
           f'<text x="{x0-9}" y="{py(g)+4:.1f}" text-anchor="end" font-size="12" fill="{AX}">{g:.1f}</text>')
t2 += f'<polyline points="{seg}" fill="none" stroke="#8e44ad" stroke-width="2.5"/>'
t2 += (f'<line x1="{x0}" y1="{py(p_true):.1f}" x2="{x1}" y2="{py(p_true):.1f}" '
       f'stroke="#c0392b" stroke-width="2" stroke-dasharray="8 5"/>')
t2 += (f'<text x="{x1-8}" y="{py(p_true)+24:.1f}" text-anchor="end" font-size="14" '
       f'fill="#c0392b">true answer \u2248 0.39</text>')
t2 += (f'<text x="{px(7):.1f}" y="{py(0.74):.1f}" font-size="13" fill="#8e44ad">'
       f'shaded band: the typical wobble, one standard error</text>')
for g in (5, 10, 50, 100, 250, 500):
    t2 += (f'<line x1="{px(g):.1f}" y1="{y0}" x2="{px(g):.1f}" y2="{y0+5}" stroke="{AX}"/>'
           f'<text x="{px(g):.1f}" y="{y0+20}" text-anchor="middle" font-size="12" fill="{AX}">{g}</text>')
t2 += (f'<text x="{W/2}" y="34" text-anchor="middle" font-size="18" font-weight="bold" '
       f'fill="{INK}">More trials, less wobble</text>')
svg("runs_converge.svg", t2)


# ------------------------------------------------ block-diagram helpers
BOX_FILL, BOX_EDGE = "#eaf2f8", INK
DEFS = ('<defs><marker id="head" viewBox="0 0 10 10" refX="10" refY="5" '
        'markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 Z" fill="{INK}"/></marker></defs>')


def box(x, y, w, h, label, sub=None, fill=BOX_FILL, edge=BOX_EDGE, ink=INK, size=13):
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
           f'stroke="{edge}" stroke-width="2"/>')
    cy = y + h / 2
    if sub:
        out += (f'<text x="{x+w/2}" y="{cy-2}" text-anchor="middle" font-size="{size}" fill="{ink}">{label}</text>'
                f'<text x="{x+w/2}" y="{cy+13}" text-anchor="middle" font-size="11" fill="{ink}">{sub}</text>')
    else:
        out += (f'<text x="{x+w/2}" y="{cy+4.5}" text-anchor="middle" font-size="{size}" fill="{ink}">{label}</text>')
    return out


def node(cx, cy, label, fill):
    return (f'<circle cx="{cx}" cy="{cy}" r="16" fill="{fill}"/>'
            f'<text x="{cx}" y="{cy+4.5}" text-anchor="middle" font-size="13" fill="#fff">{label}</text>')


def arrow(x1, y1, x2, y2):
    """Line whose arrow tip lands exactly on (x2, y2), the box edge."""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" '
            f'stroke-width="1.8" marker-end="url(#head)"/>')


def plain(x1, y1, x2, y2):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" stroke-width="1.8"/>'


# ------------------------------------------------------ series-lantern.svg
SW, SH = 720, 240
t = f'<text x="{SW/2}" y="36" text-anchor="middle" font-size="18" font-weight="bold" fill="{INK}">Series: one break, whole lantern dies</text>'
cy = 118
parts = [(120, 130, "Battery_Pack"), (310, 110, "Switch"), (480, 120, "LED_Panel")]
t += node(40, cy, "S", "#27ae60") + node(SW - 40, cy, "E", "#c0392b")
t += arrow(56, cy, parts[0][0], cy)
for (xa, wa, _), (xb, _, _) in zip(parts, parts[1:]):
    t += arrow(xa + wa, cy, xb, cy)
t += arrow(parts[-1][0] + parts[-1][1], cy, SW - 56, cy)
for x, w, name in parts:
    t += box(x, cy - 25, w, 50, name, size=14)
t += (f'<text x="{SW/2}" y="205" text-anchor="middle" font-size="14" fill="#7b241c">'
      'Any single failure = system down. Reliability multiplies.</text>')
svg("series-lantern.svg", DEFS + t, SW, SH)


# --------------------------------------------------------- kofn-drone.svg
KW, KH = 760, 400
t = f'<text x="{KW/2}" y="34" text-anchor="middle" font-size="18" font-weight="bold" fill="{INK}">Vote (4 of 6): lose two motors, keep flying</text>'
ys = [88 + 46 * i for i in range(6)]
cy = (ys[0] + ys[-1]) / 2
bat_x, bat_w = 70, 80
mot_x, mot_w, mot_h = 232, 96, 34
vote_x, vote_w = 410, 124
fc_x, fc_w = 580, 110
t += node(30, cy, "S", "#27ae60") + node(KW - 30, cy, "E", "#c0392b")
t += arrow(46, cy, bat_x, cy)
trunk_l, trunk_r = 190, 372
t += plain(bat_x + bat_w, cy, trunk_l, cy) + plain(trunk_l, ys[0], trunk_l, ys[-1])
for i, y in enumerate(ys):
    t += arrow(trunk_l, y, mot_x, y)
    t += plain(mot_x + mot_w, y, trunk_r, y)
    t += box(mot_x, y - mot_h / 2, mot_w, mot_h, f"Motor_{i+1}", size=12)
t += plain(trunk_r, ys[0], trunk_r, ys[-1]) + arrow(trunk_r, cy, vote_x, cy)
t += box(bat_x, cy - 23, bat_w, 46, "Battery")
t += box(vote_x, cy - 28, vote_w, 56, "Motors_need_4", "need 4 of 6",
         fill="#d5f5e3", edge="#1e8449", ink="#145a32")
t += arrow(vote_x + vote_w, cy, fc_x, cy)
t += box(fc_x, cy - 23, fc_w, 46, "Flight_Controller", size=12)
t += arrow(fc_x + fc_w, cy, KW - 46, cy)
t += (f'<text x="{KW/2}" y="378" text-anchor="middle" font-size="14" fill="#145a32">'
      'Down only when a third motor dies.</text>')
svg("kofn-drone.svg", DEFS + t, KW, KH)
