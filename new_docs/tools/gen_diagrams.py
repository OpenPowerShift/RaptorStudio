"""Generate data-driven SVG images for the tutorial book (no matplotlib)."""
import math
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "images")

W, H = 720, 400


def svg(name, body):
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
    t += (f'<text x="{(x0+x1)/2}" y="{y0+28}" text-anchor="middle" '
          f'font-size="15" fill="{INK}">{xlab}</text>')
    t += (f'<text x="{x0-12}" y="{(y0+y1)/2}" text-anchor="middle" '
          f'font-size="15" fill="{INK}" transform="rotate(-90 {x0-12} {(y0+y1)/2})">{ylab}</text>')
    return t


# ------------------------------------------------------- dist_shapes.svg
x0, y0, x1, y1 = 70, 320, 660, 70
t = axes(x0, y0, x1, y1, "life of one part (hours)", "how likely")
xmax = 2000.0
ymax_plot = 330


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
    fy = (1 / 1000.0) * math.exp(-tt / 1000.0)
    pts.append((tt, fy))
t += poly(pts, "#2980b9")
t += ('<text x="%d" y="%d" font-size="14" fill="#2980b9">' 
      '</text>' % (0, 0))
t += (f'<text x="{x0+130}" y="{y0-140}" font-size="14" fill="#2980b9">Exponential:</text>'
      f'<text x="{x0+130}" y="{y0-122}" font-size="13" fill="#2980b9">many early deaths,</text>'
      f'<text x="{x0+130}" y="{y0-105}" font-size="13" fill="#2980b9">long thin tail</text>')

# Weibull shape 1.5 scale 1100
def weib_pdf(tt, k=1.5, lam=1100.0):
    return (k / lam) * (tt / lam) ** (k - 1) * math.exp(-((tt / lam) ** k))

pts = []
peak = max(weib_pdf(xmax * i / 400) for i in range(1, 401))
for i in range(201):
    tt = xmax * i / 200
    pts.append((tt, weib_pdf(tt) / peak * (1 / 1000.0)))
t += poly(pts, "#27ae60", dash="8 5")
t += (f'<text x="{x0+430}" y="{y0-60}" font-size="14" fill="#27ae60">Weibull (shape 1.5):</text>'
      f'<text x="{x0+430}" y="{y0-42}" font-size="13" fill="#27ae60">fewer very early deaths</text>')

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
for n in range(1, 500):
    succ += 1 if rnd(state) < p_true else 0
    if n >= 5:
        pts.append((float(n), succ / n))

lo, hi = 10.0, 480.0
def px(n): return x0 + (math.log10(n) - 0.7) / (math.log10(480) - 0.7) * (hi - x0)
def py(v): return y0 - v * (y0 - ymax_plot)

seg = " ".join(f"{px(n):.1f},{py(v):.1f}" for n, v in pts[1:])
t2 = axes(x0, y0, x1, y1, "number of trials (log scale)", "answer so far")
t2 += f'<polyline points="{seg}" fill="none" stroke="#8e44ad" stroke-width="2.5"/>'
t2 += (f'<line x1="{x0}" y1="{py(p_true)}" x2="{x1}" y2="{py(p_true)}" '
       f'stroke="#c0392b" stroke-width="2" stroke-dasharray="8 5"/>')
t2 += (f'<text x="{x1-8}" y="{py(p_true)-8}" text-anchor="end" font-size="14" '
       f'fill="#c0392b">true answer ≈ 0.39</text>')
for g in (10, 50, 100, 250):
    t2 += (f'<line x1="{px(g)}" y1="{y0}" x2="{px(g)}" y2="{y0+5}" stroke="{AX}"/>'
           f'<text x="{px(g)}" y="{y0+20}" text-anchor="middle" font-size="12" fill="{AX}">{g}</text>')
t2 += (f'<text x="{W/2}" y="34" text-anchor="middle" font-size="18" font-weight="bold" '
       f'fill="{INK}">More trials, less wobble</text>')
svg("runs_converge.svg", t2)
