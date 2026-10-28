"""Physics core for Black Hole Studio.

Pure Python (no ``bpy``) so it can be unit-tested outside Blender.

Conventions
-----------
Geometric units, G = c= 1. Inside the add-on every length is expressed in
units of the Schwarzschild radius r_s = 2M, so M = 0.5. THe user-facing
"scene scale" convers r_s to Blender units.

References
-----------
* Schwarzschild photon orbit equation:  d^2u/dphi^2 = -u + 3 M u^2,  u = 1/r
* ISCO: Bardeen, Press & Teukolsky 1972, ApJ 178, 347
* Disk temperature: Shakura & Sunyaev 1973 / Novikov & Thorne 1973,
  T(r) ~ r^(-3/4) (1 - sqrt(r_in / r))^(1/4)
* Redshift of a circular emitter: 1 + z = u^t (1 - Omega L_z)  (E = 1)
"""

import math

# Physical constants (SI)
G = 6.67430e-11
C = 299_792_458.0
M_SUN = 1.98892e30
SIGMA_SB = 5.670374419e-8

# Geometric quanitites, in units of r_s
M_GEOM = 0.5                            # M in units of r_s
PHOTON_SPHERE = 1.5                     # r = 3M
CRITICAL_IMPACT = 1.5 * math.sqrt(3.0)  # b_c = 3*sqrt(3) M ~ 2.598 r_s

def schwarzschild_radius_m(mass_solar):
    """Schwarzschil radius in metres for a mass given in solar masses."""
    return 2.0 * G * mass_solar * M_SUN / C**2

def format_length(metres):
    """Readable length for UI."""
    au = 1.495978707e11
    if metres >= 0.1 * au:
        return f"{metres / au:.3g} AU"
    if metres >= 1e3:
        return f"{metres / 1e3:.4g} km"
    return f"{metres:.3g} m"

# Spin

def horizon_radius(spin):
    """Outer horizon r_+ = M + sqrt(M^2 - a^2), in units of r_s (a in units of M)."""
    a = max(-0.9999, min(0.9999, spin))
    return 0.5 * (1.0 + math.sqrt(1.0 - a * a))

def isco_radius(spin):
    """Innermost stable circular orbit in units of r_s.

    ``spin`` is the dimensionless a/M in [-1, 1]; positive values are
    prograde (disk co-rotating with the hole), negative are retrograde.
    """
    a = max(-0.9999, min(0.9999, spin))
    z1 = 1.0 + (1.0 - a * a) ** (1.0 / 3.0) * (
        (1.0 + a) ** (1.0 / 3.0) + (1.0 - a) ** (1.0 / 3.0))
    z2 = math.sqrt(3.0 * a * a + z1 * z1)
    sign = 1.0 if a >= 0.0 else -1.0
    r_isco_m = 3.0 + z2 - sign * math.sqrt((3.0 - z1) * (3.0 + z1 + 2.0 * z2))
    return 0.5 * r_isco_m

def orbital_omega(r, spin=0.0):
    """Keplerian angular velocity of an equatorial circular orbit (units r_s, c=1)."""
    r_m = 2.0 * r
    a = spin
    omega_m = 1.0 / (r_m ** 1.5 + a)
    return 2.0 * omega_m

def orbital_ut(r, spin=0.0):
    """Time component of an equatorial circular orbit"""
    r_m = 2.0 * r
    a = spin
    denom = r_m ** 1.5 - 3.0 * r_m ** 0.5 + 2.0 * a
    if denom <= 0.0:
        return float("inf")
    return (r_m ** 1.5 + a) / (r_m ** 0.75 * math.sqrt(denom))

def redshift_factor(r, lz, spin=0.0, doppler=1.0, gravity=1.0):
    """Observed/emitted frequency ratio g = 1/(1+z) for a disk emitter."""
    ut = orbital_ut(r, spin)
    omega = orbital_omega(r, spin)
    g_grav = 1.0 / ut
    g_dop = 1.0 / (1.0 - omega * lz)
    return g_grav ** gravity * g_dop ** doppler

# Accretion disk temperature

def temperature_peak_x(q=0.75):
    """x = r/r_in where x^-q (1 - x^-1/2)^(1/4) peaks (49/36 for q = 3/4)."""
    s = 8.0 * q / (8.0 * q + 1.0)
    return 1.0 / (s * s)

def _temperature_shape(x, q):
    if x <= 1.0:
        return 0.0
    return x ** (-q) * (1.0 - x ** -0.5) ** 0.25

def disk_temperature(r, r_in, t_peak, q=0.75):
    """Novikov-Thorne/Shakura-Sunyaev temperature, normalized so max = t_peak."""
    x = r /r_in
    return t_peak * _temperature_shape(x, q) / _temperature_shape(temperature_peak_x(q), q)

def shadow_radius(spin=0.0):
    """Apparent shadow radius in r_s. Exact for spin = 0; a linear fit (approximation) for spinning holes."""
    return CRITICAL_IMPACT * (1.0 - 0.067 * abs(spin))

def frame_drag_omega(r, spin):
    """Lense-Thirring angular velocity omega = 2Mar / (r^3 + a^2 r + 2Ma^2) (1/r_s)."""
    r_m = 2.0 * r
    a = spin
    w_m = 2.0 * a * r_m / (r_m ** 3 + a * a * r_m + 2.0 * a * a)
    return 2.0 * w_m