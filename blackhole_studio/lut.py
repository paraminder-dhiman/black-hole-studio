"""Precomputed Schwarzschild geodesic lookup table for the node-based mode.

Every photon arriving from infinity with impact parameter b follows an orbit
u(theta; b), u = 1/r, where theta is the polar angle in the orbital plane
measured from the incoming asymptote.  We integrate

    d^2u/dtheta^2 = -u + (3/2) k u^2,   u(0) = 0,  u'(0) = 1/b     (r_s = 1)

for a grid of b with RK4 (k = lensing strength, 1 = GR) and store:

    R  u(b, theta)    (sentinels: -0.05 after escape, 2.0 after capture)
    G  +-theta_end(b) / THETA_MAX   total swept angle at escape (+) or capture (-)
    B  0 (unused)
    A  theta(b, u)     inverse orbit: angle at which the incoming ray reaches u,
                       rows y = 1 - sqrt(1 - u / u_max)  (u_max = periapsis or horizon),
                       row j at y = j / (HEIGHT - 1), so both ends are exact; sample
                       at texture v = inverse_v(y)

Shader nodes then evaluate the orbit at any angle with one texture lookup,
which is exact GR up to bilinear interpolation.  The inverse table lets a ray
start anywhere on its orbit (a camera inside the influence sphere).

Edge fade: the sphere only bends light that passes through it.  Rays that
pass well inside it get the exact GR bending from the camera to infinity
(the stretch between a camera outside the sphere and the sphere is added in
closed form, see weak_pull); the bending of rays through the outer part of
the sphere is tapered smoothly to zero at its edge (edge_taper), so the
sphere leaves no seam in the background.
"""

import math
import numpy as np
from . import physics

WIDTH = 1024
HEIGHT = 512
THETA_MAX = 3.0 * math.pi     # Covers the primary, secondary and tertiary images
SUBSTEPS = 8

# The table covers impact parameters a little beyond the sphere: a ray that
# Enters it tangentially has b = R / sqrt(1 - k / R) > R
B_MAX_FACTOR = 1.05

U_ESCAPED = -0.05
U_CAPTURED = 2.0

def b_from_x(x, b_max):
    """Texture x in [0,1] -> impact parameter.  Denser sampling near the shadow."""
    s = physics.CRITICAL_IMPACT
    y = np.asarray(x) * b_max / (b_max + s)
    return s * y / (1.0 - y)


def x_from_b(b, b_max):
    s = physics.CRITICAL_IMPACT
    return (b / (b + s)) * ((b_max + s) / b_max)

def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, dtype=float) - e0) / max(e1 - e0, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)

def periapsis_u(b, strength=1.0):
    """Largest u reached by an incoming ray of impact parameter b (1 if captured).

    Smallest positive root of k u^3 - u^2 + 1/b^2 = 0 (bisection, vectorised).
    """
    b = np.asarray(b, dtype=float)
    k = strength
    lo = np.zeros_like(b)
    hi = np.minimum(np.full_like(b, 1.0), 1.0 / b) if k <= 0 else np.full_like(b, min(2.0 / (3.0 * k), 1.0))
    f_hi = k * hi ** 3 - hi * hi + 1.0 / (b * b)
    captured = f_hi > 0.0                      # No turning point before the horizon
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f = k * mid ** 3 - mid * mid + 1.0 / (b * b)
        lo = np.where(f > 0.0, mid, lo)
        hi = np.where(f > 0.0, hi, mid)
    return np.where(captured, 1.0, 0.5 * (lo + hi))

def periapsis_u_closed(b, strength=1.0):
    """periapsis_u in closed form (trigonometric cubic root), as the shader nodes.

    k u^3 - u^2 + 1/b^2 = 0 has the smallest positive root
    u = (1 + 2 cos(acos(1 - 27 k^2 / (2 b^2)) / 3 - 2 pi / 3)) / (3k);  1/b when k -> 0.
    Returns 1 for captured rays (b < b_c = 2.598 k).
    """
    b = np.asarray(b, dtype=float)
    k = max(strength, 1e-6)
    x = 1.0 - 13.5 * k * k / (b * b)
    u = (1.0 + 2.0 * np.cos(np.arccos(np.clip(x, -1.0, 1.0)) / 3.0 - 2.0 * math.pi / 3.0)) / (3.0 * k)
    if strength < 0.01:
        u = 1.0 / b
    return np.where(x < -1.0, 1.0, np.minimum(u, 1.0))

def weak_pull(phi, b, strength=1.0):
    """Antiderivative of the weak-field bending along a straight ray, to second order.

    The ray is r = b / cos(phi) (phi = asin(s / r), s along the ray from its
    closest approach); the bending between phi0 and phi1 is
    weak_pull(phi1) - weak_pull(phi0).  Over the whole line it gives the GR
    deflection 2k/b + (15 pi / 16)(k/b)^2.
    """
    kb = strength / np.asarray(b, dtype=float)
    phi = np.asarray(phi, dtype=float)
    return kb * np.sin(phi) + 1.875 * kb * kb * (0.5 * phi + 0.25 * np.sin(2.0 * phi))


FADE_MIN_RADIUS = 6.0     # The fade never reaches the strongly bent rays near the shadow

def edge_taper(b, bound, fade=0.5):
    """1 for rays through the inner sphere, falling smoothly to 0 at its edge."""
    r0 = min(max((1.0 - fade) * bound, FADE_MIN_RADIUS), 0.95 * bound)
    return 1.0 - smoothstep(r0, bound, b)

def compute(bound, strength=1.0, width=WIDTH, height=HEIGHT):
    """Table for an influence sphere of radius `bound` (r_s), as a
    (height, width, 4) float32 array ready for Image.pixels."""
    b_max = B_MAX_FACTOR * bound
    xs = (np.arange(width) + 0.5) / width
    b = b_from_x(xs, b_max)
    h = THETA_MAX / (height * SUBSTEPS)
    c = 1.5 * strength

    u = np.zeros(width)
    du = 1.0 / b
    state = np.zeros(width, dtype=np.int8)          # 0 flying, 1 escaped, 2 captured
    theta_end = np.full(width, THETA_MAX)
    out = np.zeros((height, width, 4), dtype=np.float32)

    def f(uu):
        return -uu + c * uu * uu

    theta = 0.0
    row = 0
    # Incoming branch, for the inverse table: (u, theta) until the turning point
    hist_u = [u.copy()]
    hist_t = [0.0]
    for step in range(height * SUBSTEPS):
        k1u, k1d = du, f(u)
        k2u, k2d = du + 0.5 * h * k1d, f(u + 0.5 * h * k1u)
        k3u, k3d = du + 0.5 * h * k2d, f(u + 0.5 * h * k2u)
        k4u, k4d = du + h * k3d, f(u + h * k3u)
        u_new = u + h / 6.0 * (k1u + 2 * k2u + 2 * k3u + k4u)
        du_new = du + h / 6.0 * (k1d + 2 * k2d + 2 * k3d + k4d)
        flying = state == 0

        esc = flying & (u_new <= 0.0)
        theta_end[esc] = theta + h * u[esc] / (u[esc] - u_new[esc])
        state[esc] = 1
        cap = flying & (u_new >= 1.0)
        theta_end[cap] = theta + h * (1.0 - u[cap]) / (u_new[cap] - u[cap])
        state[cap] = 2

        u = np.where(state == 0, u_new, u)
        du = np.where(state == 0, du_new, du)
        theta += h
        hist_u.append(np.where(state == 0, u, np.nan))
        hist_t.append(theta)

        # sample at row centres
        while row < height and (row + 0.5) / height * THETA_MAX <= theta + 1e-12:
            val = np.where(state == 0, u, np.where(state == 1, U_ESCAPED, U_CAPTURED))
            out[row, :, 0] = val
            row += 1

    captured = state == 2
    out[:, :, 1] = (np.minimum(theta_end / THETA_MAX, 1.0) * np.where(captured, -1.0, 1.0))[None, :]
    out[:, :, 2] = 0.0
    out[:, :, 3] = _inverse_table(b, np.array(hist_u), np.array(hist_t), theta_end, captured,
                                  strength, height)
    return out

def inverse_v(y, height=HEIGHT):
    """Texture coordinate of the inverse-table row for y in [0, 1]."""
    return (y * (height - 1) + 0.5) / height

def _inverse_table(b, hist_u, hist_t, theta_end, captured, strength, height):
    """theta at which the incoming ray reaches u = q u_max, rows y = 1 - sqrt(1 - q).

    Interpolated in x = sqrt(u_max - u), in which theta is smooth (linear) at
    the turning point.  Escaping orbits are symmetric: theta_peri = theta_end / 2.
    """
    u_max = periapsis_u(b, strength)
    y = np.arange(height) / (height - 1)
    table = np.zeros((height, b.size), dtype=np.float32)

    for c in range(b.size):
        us = hist_u[:, c]
        ok = np.isfinite(us)
        us, ts = us[ok], hist_t[ok]
        if not captured[c]:
            inc = np.concatenate([[True], np.diff(us) > 0])
            stop = np.argmin(inc) if not inc.all() else inc.size
            us, ts = us[:stop], ts[:stop]
            t_top = 0.5 * theta_end[c]
        else:
            t_top = theta_end[c]

        x = np.sqrt(np.maximum(u_max[c] - us, 0.0))
        x = np.concatenate([x, [0.0]])
        ts = np.concatenate([ts, [t_top]])
        target = np.sqrt(u_max[c]) * (1.0 - y)
        # np.interp needs increasing x
        table[:, c] = np.interp(target, x[::-1], ts[::-1])
    return table

def sample(table, b, theta, b_max):
    """Bilinear lookup matching Blender's 'Linear' + 'Extend' image sampling."""
    height, width, _ = table.shape
    fx = np.clip(x_from_b(b, b_max) * width - 0.5, 0, width - 1)
    fy = np.clip(theta / THETA_MAX * height - 0.5, 0, height - 1)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(x0 + 1, width - 1), min(y0 + 1, height - 1)
    tx, ty = fx - x0, fy - y0
    top = table[y0, x0] * (1 - tx) + table[y0, x1] * tx
    bot = table[y1, x0] * (1 - tx) + table[y1, x1] * tx
    return top * (1 - ty) + bot * ty