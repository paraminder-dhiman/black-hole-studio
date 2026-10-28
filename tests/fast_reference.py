"""NumPy reference of the Fast-mode ray set-up, mirroring the node graph.

Given a ray start (the camera when it is inside the influence sphere, else the
point where the ray enters the sphere) and a direction, both in r_s units,
reconstruct the light path from the geodesic table:

* theta_c, the orbital angle of the start point (inverse table),
* the orbital-plane basis so that  P(theta) = r(theta) (A cos theta + B sin theta),
* the sign sigma of the table direction (-1: an outgoing ray on an orbit that
  falls in from infinity, i.e. the time-reversed table orbit),
* the asymptotic exit direction, with the edge fade applied.

blackhole_studio/nodes/materials.py::build_fast builds exactly these steps
from shader nodes; tests/test_camera.py checks them against direct
integration of the geodesic."""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from blackhole_studio import lut, physics  # noqa: E402

def _lookup(table, b, y, bound, channel):
    height, width, _ = table.shape
    b_max = lut.B_MAX_FACTOR * bound
    fx = np.clip(lut.x_from_b(b, b_max) * width - 0.5, 0, width - 1)
    fy = np.clip(y * height - 0.5, 0, height - 1)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(x0 + 1, width - 1), min(y0 + 1, height - 1)
    tx, ty = fx - x0, fy - y0
    c = table[..., channel]
    top = c[y0, x0] * (1 - tx) + c[y0, x1] * tx
    bot = c[y1, x0] * (1 - tx) + c[y1, x1] * tx
    return float(top * (1 - ty) + bot * ty)

def observer_gamma(r_c, bound, strength=1.0):
    """Frequency factor of a hovering observer at r_c, relative to the edge of
    the influence sphere (1 at and beyond the edge, so nothing pops when the
    camera crosses it)."""
    r = min(max(r_c, 1.0001), bound)
    return math.sqrt(max(1.0 - strength / bound, 1e-6) / max(1.0 - strength / r, 1e-6))

def impact_parameter(r_c, sin_a, strength=1.0, hovering=False):
    """Impact parameter of a ray leaving the camera at angle a to the radial direction.

    Default camera: the ray starts along the camera direction in Schwarzschild
    coordinates (du/dphi = -u cot a), so b = r sin a / sqrt(1 - k u sin^2 a).
    Hovering observer: a is measured in the observer's own frame,
    b = r sin a / sqrt(1 - k u)  (the shadow looks smaller).                # changed
    """
    u = 1.0 / r_c
    s2 = 1.0 if hovering else sin_a * sin_a
    return r_c * sin_a / math.sqrt(max(1.0 - strength * u * s2, 1e-6))

def faded_direction(d, exit_dir, L, extra, taper):
    """Add `extra` bending about the orbit normal L, then scale the total
    bending by taper (as the Fast and Exact graphs)."""
    q = np.cross(L, d)
    psi = math.atan2(float(np.dot(exit_dir, q)), float(np.dot(exit_dir, d)))
    psi = (psi + extra) * taper
    return d * math.cos(psi) + q * math.sin(psi)

def setup(table, start, d, bound, strength=1.0, hovering=False, fade=0.5):
    """start: where the ray starts (the camera, inside or outside the sphere)."""
    start = np.asarray(start, float)
    d = np.asarray(d, float) / np.linalg.norm(d)
    r_c = np.linalg.norm(start)
    n = start / r_c
    tv = d - n * np.dot(n, d)
    if np.linalg.norm(tv) < 1e-9:
        tv = np.cross(d, np.array([0.577, 0.577, 0.577]))
    tv /= np.linalg.norm(tv)

    gamma = observer_gamma(r_c, bound, strength) if hovering else 1.0
    b = impact_parameter(r_c, np.linalg.norm(np.cross(n, d)), strength, hovering)

    g_end = _lookup(table, b, 0.5, bound, 1)
    captured = g_end < 0.0
    theta_end = abs(g_end) * lut.THETA_MAX
    u_max = 1.0 if captured else float(lut.periapsis_u_closed(b, strength))

    def inverse(u):
        q = min(u / u_max, 1.0)
        return _lookup(table, b, lut.inverse_v(1.0 - math.sqrt(1.0 - q)), bound, 3)

    u_c = 1.0 / r_c
    trapped = (not captured) and u_c > u_max * 1.001            # Bound orbit inside the photon sphere
    th_c, th_r = inverse(u_c), inverse(1.0 / bound)
    inward = np.dot(n, d) < 0.0
    sigma = 1.0 if (inward or not captured) else -1.0
    if not inward and not captured:
        th_c = theta_end - th_c                                 # Outgoing branch of the same orbit
    escaped = (sigma < 0 or not captured) and not trapped

    if sigma > 0:
        th_final = theta_end if captured else theta_end - th_r
        th_inf = theta_end
    else:
        th_final, th_inf = th_r, 0.0
    A = n * math.cos(th_c) - sigma * tv * math.sin(th_c)
    B = n * math.sin(th_c) + sigma * tv * math.cos(th_c)
    exit_dir = A * math.cos(th_inf) + B * math.sin(th_inf)

    L = np.cross(n, tv)
    taper = float(lut.edge_taper(b, bound, fade))
    faded = faded_direction(d, exit_dir, L, 0.0, taper)
    # Fog and disk are sampled from where the ray enters the sphere
    th_start = th_c if r_c < bound else th_r
    return {"b": b, "gamma": gamma, "theta_c": th_c, "theta_start": th_start,
            "theta_final": th_final, "sigma": sigma,
            "A": A, "B": B, "escaped": escaped, "exit_dir": exit_dir, "exit_faded": faded,
            "taper": taper}

def point(table, s, theta, bound):
    """Path point at orbital angle theta (as the Fast-mode point group)."""
    u = lut.sample(table, s["b"], theta, lut.B_MAX_FACTOR * bound)[0]
    r = 1.0 / min(max(u, 1.0 / bound), 1.0)
    return r * (s["A"] * math.cos(theta) + s["B"] * math.sin(theta))

def trace(start, d, strength=1.0, r_far=1e5, steps_per_orbit=8192, hovering=False):
    """Direct RK4 integration from the start point (as the OSL shader).
    Returns (exit_dir or None, path)."""
    start = np.asarray(start, float)
    d = np.asarray(d, float) / np.linalg.norm(d)
    r0 = np.linalg.norm(start)
    e1 = start / r0
    nrm = np.cross(start, d)
    if np.linalg.norm(nrm) < 1e-9:
        nrm = np.cross(e1, np.array([0.577, 0.577, 0.577]))
    nrm /= np.linalg.norm(nrm)
    e2 = np.cross(nrm, e1)

    u = 1.0 / r0
    k = strength
    cos_a, sin_a = np.dot(d, e1), max(np.dot(d, e2), 1e-9)
    ud = -cos_a / sin_a * u
    if hovering:
        ud *= math.sqrt(max(1.0 - k * u, 1e-6))
    h = 2.0 * math.pi / steps_per_orbit
    phi = 0.0
    path = [start]

    for _ in range(steps_per_orbit * 6):
        # Adaptive near infinity is unnecessary: u -> 0 is reached in < pi
        u_n, ud_n = _rk4(u, ud, h, k)
        phi += h
        if u_n >= 1.0:
            return None, np.array(path)

        if u_n <= 1.0 / r_far:
            if u_n <= 0.0:                     # Stepped past infinity: interpolate (as the OSL shader)
                phi_inf = phi - h + h * u / max(u - u_n, 1e-12)
                v = math.cos(phi_inf) * e1 + math.sin(phi_inf) * e2
                return v, np.array(path)
            dr = -ud_n / max(u_n, 1e-12) ** 2
            er = math.cos(phi) * e1 + math.sin(phi) * e2
            ep = -math.sin(phi) * e1 + math.cos(phi) * e2
            v = dr * er + (1.0 / max(u_n, 1e-12)) * ep
            return v / np.linalg.norm(v), np.array(path)
        
        u, ud = u_n, ud_n
        path.append((1.0 / u) * (math.cos(phi) * e1 + math.sin(phi) * e2))
    return None, np.array(path)

def _rk4(u, ud, h, k):
    def f(a, b):
        return b, -a + 1.5 * k * a * a
    k1 = f(u, ud)
    k2 = f(u + 0.5 * h * k1[0], ud + 0.5 * h * k1[1])
    k3 = f(u + 0.5 * h * k2[0], ud + 0.5 * h * k2[1])
    k4 = f(u + h * k3[0], ud + h * k3[1])
    return (u + h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]),
            ud + h / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]))

def angle(a, b):
    return math.acos(max(-1.0, min(1.0, float(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b)))))