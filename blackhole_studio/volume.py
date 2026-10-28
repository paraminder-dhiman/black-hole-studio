"""Volume maths for the thick disk and the fog (pure Python, no bpy).

Both render modes march the bent light path as a polyline of points
P_0 .. P_N (black-hole space, r_s units).  For every segment the node graph
evaluates the formulas below; these reference versions are unit-tested.

Thick disk
----------
Vertical density of a Gaussian slab with scale height H, normalised to unit
column (integral over z = 1):

    rho(z) = exp(-z^2 / 2H^2) / (sqrt(2 pi) H)

Along a straight segment from z0 to z1 of length ds the integral of rho is
exact in closed form:

    int rho ds = ds * |Phi(z1/H) - Phi(z0/H)| / |z1 - z0|      (Phi = normal CDF)

which stays correct when the slab is much thinner than the segment, so thin
disks are never missed between samples.  The segment's optical depth is
tau0 * (int rho ds), with tau0 = -ln(1 - alpha) the face-on optical depth of
the existing thin-disk opacity.

Scale height:  H(r) = thickness * r * (r / r_in)^flare.

Fog
---
    rho_f = density * window(r) * [(1 - halo) exp(-z^2 / 2 Hf^2) + halo (r_in / r)^k] * noise
    tau   = absorption * rho_f * ds                         (dark dust)
    E     = glow * rho_f * min((r_in / r)^2, 1) * colour * ds  (disk-lit glow)
"""

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)

def normal_cdf(x):
    """tanh approximation of the standard normal CDF (max error 1.8e-4)."""
    return 0.5 * (1.0 + math.tanh(0.7978845608 * (x + 0.044715 * x * x * x)))

def scale_height(r, r_in, thickness, flare):
    return max(thickness * r * (r / r_in) ** flare, 1e-5)

def slab_column(z0, z1, h, ds):
    """Integral of the unit-column Gaussian slab along a straight segment."""
    dz = z1 - z0
    if abs(dz) > 1e-3 * h:
        return ds * abs(normal_cdf(z1 / h) - normal_cdf(z0 / h)) / abs(dz)
    zm = 0.5 * (z0 + z1)
    return ds * math.exp(-0.5 * (zm / h) ** 2) / (SQRT_2PI * h)

def smoothstep(e0, e1, x):
    if e1 == e0:
        return 0.0 if x < e0 else 1.0
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)

def fog_window(r, inner, outer, falloff):
    """Radial extent of the fog: soft inner edge, fade over `falloff` of the outer radius."""
    return smoothstep(inner, inner * 1.3, r) * (1.0 - smoothstep(outer * (1.0 - falloff), outer, r))

def fog_density(p, s):
    """Fog density at point p (x, y, z) without noise.  ``s`` is a dict of fog settings."""
    x, y, z = p
    rc = max(math.hypot(x, y), 1e-4)
    r = max(math.sqrt(x * x + y * y + z * z), 1e-4)
    hf = max(s["height"] * rc * (rc / s["r_in"]) ** s["flare"], 1e-3)
    shape = (1.0 - s["halo"]) * math.exp(-0.5 * (z / hf) ** 2) + \
        s["halo"] * min(s["r_in"] / r, 1.0) ** s["halo_falloff"]
    return s["density"] * fog_window(r, s["inner"], s["outer"], s["falloff"]) * shape

def march(points, tau_fn, emit_fn):
    """Front-to-back composite along a polyline: returns (emission, transmittance)."""
    acc, trans = 0.0, 1.0
    for p0, p1 in zip(points[:-1], points[1:]):
        ds = math.dist(p0, p1)
        mid = tuple(0.5 * (a + b) for a, b in zip(p0, p1))
        acc += trans * emit_fn(mid, ds)
        trans *= math.exp(-tau_fn(p0, p1, mid, ds))
    return acc, trans