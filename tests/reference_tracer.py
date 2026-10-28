"""NumPy refrence implementation of the Exact-mode geodesic tracer.

The OSL shader (blackhole_studio/shaders/blackhole_geodesic.osl) is a 
line-by-line port of ``trace_rays`` below, so tests on this module validate 
the shader's maths. It also renders a quick preview image:

    python tests/reference_tracer.py out.png"""

import math
import os
import struct
import sys
import zlib

import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from blackhole_studio import physics    # noqa: E402

def _rhs(u, du, coupling):
    """Orbit equation d^2u/dphi^2 = -u + 3M u^2 with M = 0.5 (r_s units)."""
    return du, -u + 1.5 * coupling * u * u

def rk4_step(u, du, h, coupling=1.0):
    k1u, k1d = _rhs(u, du, coupling)
    k2u, k2d = _rhs(u + 0.5 * h * k1u, du + 0.5 * h * k1d, coupling)
    k3u, k3d = _rhs(u + 0.5 * h * k2u, du + 0.5 * h * k2d, coupling)
    k4u, k4d = _rhs(u + h * k3u, du + h * k3d, coupling)
    return (u + h / 6.0 * (k1u + 2 * k2u + 2 * k3u + k4u),
            du + h / 6.0 * (k1d + 2 * k2d + 2 * k3d + k4d))

def deflection_angle(b, coupling=1.0, r_start=1e4, steps_per_orbit=4096):
    """Total bending angle of a ray with impact parameter b (r_s units).
    
    Returns None when the ray is captured."""
    u0 = 1.0 / r_start
    sin_a = b / r_start
    du0 = -math.sqrt(max(1.0 - sin_a * sin_a, 0.0)) / sin_a / r_start
    u, du = u0, -du0                # incoming: u increases
    h = 2.0 * math.pi / steps_per_orbit
    phi = 0.0
    phi0 = math.asin(sin_a)         # angle already subtended at the start point

    while True:
        u_new, du_new = rk4_step(u, du, h, coupling)
        phi += h
        if u_new >= 1.0:
            return None
        if u_new <= 0.0:
            # interpolate where u crosses 0 (r -> infinity)
            phi -= h * u_new / (u_new - u)
            return phi + phi0 - math.pi
        u, du = u_new, du_new
        if phi > 20 * math.pi:
            return None

def trace_rays(origins, dirs, params):
    """Vectorised port of the OSL shader for many rays at once.
    
    origins, dirs: (N, 3) arrays in black-hole local space, r_s units, disk in z=0.
    Returns dict with 'disk' (N,3) emitted color proxy (g-weighted intensity
    in all channels), 'alpha', 'captured', 'exit_dir', 'crossings'."""
    p = params
    n = origins.shape[0]
    r0 = np.linalg.norm(origins, axis=1)
    e1 = origins / r0[:, None]
    normal = np.cross(origins, dirs)
    nlen = np.linalg.norm(normal, axis=1)
    radial = nlen < 1e-9
    normal[radial] = np.cross(e1[radial], np.array([0.0, 0.0, 1.0]) + 1e-3)
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    e2 = np.cross(normal, e1)

    cos_a = np.sum(dirs * e1, axis=1)
    sin_a = np.maximum(np.sum(dirs * e2, axis=1), 1e-9)
    u = 1.0 / r0
    du = -cos_a / sin_a / r0
    # Photon (physical) momentum is -dir; L = r x (-d), |L| = b
    lz = -(np.cross(origins, dirs)[:, 2])

    h = 2.0 * math.pi / p["steps_per_orbit"]
    max_steps = int(p["steps_per_orbit"] * p["max_orbits"])
    u_h = 1.0 / physics.horizon_radius(p["spin"])
    u_exit = 1.0 / p["r_exit"]

    color = np.zeros(n)
    trans = np.ones(n)
    crossings = np.zeros(n, dtype=int)
    captured = np.zeros(n, dtype=bool)
    done = np.zeros(n, dtype=bool)
    exit_dir = dirs.copy()

    phi = np.zeros(n)
    prev_pos = origins.copy()

    for _ in range(max_steps):
        active = ~done
        if not active.any():
            break
        u_new, du_new = rk4_step(u, du, h, p["coupling"])
        phi_new = phi + h
        r_new = 1.0 / np.maximum(u_new, 1e-12)
        pos = r_new[:, None] * (np.cos(phi_new)[:, None] * e1 + np.sin(phi_new)[:, None] * e2)

        # disk crossing
        z0, z1 = prev_pos[:, 2], pos[:, 2]
        cross = active & (z0 * z1 < 0.0) & (u_new > 0.0)
        t = np.where(cross, z0 / np.where(cross, z0 - z1, 1.0), 0.0)
        hit = prev_pos + t[:, None] * (pos - prev_pos)
        rh = np.linalg.norm(hit[:, :2], axis=1)
        in_disk = cross & (rh >= p["r_in"]) & (rh <= p["r_out"])

        if in_disk.any():
            rr = rh[in_disk]
            temp = np.array([physics.disk_temperature(x, p["r_in"], 1.0) for x in rr])
            g = np.array([physics.redshift_factor(x, l) for x, l in zip(rr, lz[in_disk])])
            intensity = (g * temp) ** 4
            a = p["opacity"]
            color[in_disk] += trans[in_disk] * a * intensity
            trans[in_disk] *= 1.0 - a
            crossings[in_disk] += 1

        cap = active & (u_new >= u_h)
        esc = active & (u_new <= u_exit) & (du_new < 0.0)
        if esc.any():
            # tangent of the orbit: dr/dphi e_r + r e_phi
            dr = -du_new[esc] / (u_new[esc] ** 2)
            er = np.cos(phi_new[esc])[:, None] * e1[esc] + np.sin(phi_new[esc])[:, None] * e2[esc]
            ep = -np.sin(phi_new[esc])[:, None] * e1[esc] + np.cos(phi_new[esc])[:, None] * e2[esc]
            d = dr[:, None] * er + r_new[esc][:, None] * ep
            exit_dir[esc] = d / np.linalg.norm(d, axis=1)[:, None]

        captured |= cap
        done |= cap | esc
        u = np.where(done, u, u_new)
        du = np.where(done, du, du_new)
        phi = np.where(done, phi, phi_new)
        prev_pos = np.where(done[:, None], prev_pos, pos)

    return {"disk": color, "alpha": 1.0 - trans, "captured": captured,
            "exit_dir": exit_dir, "crossings": crossings}

def _write_png(path, rgb):
    h, w, _ = rgb.shape
    data = (np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(h))

    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)

def render_preview(path, size=256, inclination_deg=80.0, distance=30.0, fov_deg=40.0):
    inc = math.radians(inclination_deg)
    cam = distance * np.array([0.0, -math.sin(inc), math.cos(inc)])
    fwd = -cam / np.linalg.norm(cam)
    right = np.array([1.0, 0.0, 0.0])
    up = np.cross(right, fwd)
    half = math.tan(math.radians(fov_deg) / 2)
    ys, xs = np.mgrid[0:size, 0:size]
    sx = (xs + 0.5) / size * 2 - 1
    sy = 1 - (ys + 0.5) / size * 2
    dirs = fwd + half * (sx[..., None] * right + sy[..., None] * up)
    dirs = dirs.reshape(-1, 3)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    origins = np.repeat(cam[None], dirs.shape[0], axis=0)
    
    params = dict(steps_per_orbit=256, max_orbits=3, spin=0.0, r_exit=40.0,
                  coupling=1.0, r_in=physics.isco_radius(0.0), r_out=12.0, opacity=0.9)
    res = trace_rays(origins, dirs, params)
    disk = res["disk"].reshape(size, size)
    disk = disk / np.percentile(disk[disk > 0], 99) if (disk > 0).any() else disk

    # fake blackbody-ish tint: orange -> white with intensity
    rgb = np.stack([np.clip(disk * 1.2, 0, 1), np.clip(disk * 0.8, 0, 1), np.clip(disk * 0.45, 0, 1)], -1)
    # lensed background checker so bending is visible
    ed = res["exit_dir"].reshape(size, size, 3)
    lon = np.arctan2(ed[..., 1], ed[..., 0])
    lat = np.arcsin(np.clip(ed[..., 2], -1, 1))
    checker = ((np.floor(lon * 6 / math.pi) + np.floor(lat * 6 / math.pi)) % 2) * 0.08 + 0.02
    bg = (1 - res["alpha"].reshape(size, size))[..., None] * checker[..., None]
    rgb = rgb + bg
    rgb[res["captured"].reshape(size, size) & (disk <= 0)] = 0.0
    _write_png(path, rgb)


if __name__ == "__main__":
    render_preview(sys.argv[1] if len(sys.argv) > 1 else "preview.png")