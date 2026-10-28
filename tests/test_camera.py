"""Rays that start inside the influence sphere, the edge fade and the
hovering observer (tests/fast_reference.py mirrors the Fast-mode nodes)."""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fast_reference as fr  # noqa: E402
from blackhole_studio import lut, physics  # noqa: E402

BOUND = 30.0

@pytest.fixture(scope="module")
def table():
    return lut.compute(BOUND)

def _rays(seed, count, r_lo=1.8, r_hi=BOUND * 0.95):
    rng = np.random.default_rng(seed)
    for _ in range(count):
        n = rng.normal(size=3)
        d = rng.normal(size=3)
        yield n / np.linalg.norm(n) * rng.uniform(r_lo, r_hi), d / np.linalg.norm(d)

def test_periapsis_root():
    b = np.array([3.0, 5.0, 20.0])
    u = lut.periapsis_u(b)
    assert np.allclose(u ** 3 - u ** 2 + 1 / b ** 2, 0.0, atol=1e-9)
    assert lut.periapsis_u(np.array([2.0]))[0] == 1.0          # Inside the shadow: captured
    for k in (0.5, 1.0, 2.0):
        bb = np.array([1.6, 2.7, 4.0, 9.0, 30.0]) * max(k, 1.0)
        assert np.allclose(lut.periapsis_u_closed(bb, k), lut.periapsis_u(bb, k), atol=1e-6)

def test_inverse_table(table):
    """theta at which an incoming ray reaches u, against direct integration."""
    for b in (2.0, 3.5, 6.0, 15.0):
        start = np.array([-1e4, b, 0.0])
        _, path = fr.trace(start, (1.0, 0.0, 0.0))
        u_max = lut.periapsis_u(np.array([b]))[0]
        r = np.linalg.norm(path, axis=1)
        incoming = np.concatenate([[True], np.diff(r) < 0])
        stop = np.argmin(incoming) if not incoming.all() else r.size

        for frac in (0.1, 0.5, 0.9, 0.99):
            u = frac * u_max
            i = np.argmin(np.abs(1.0 / r[:stop] - u))
            p = path[i]
            theta_true = math.atan2(p[1], -p[0])               # Angle from the incoming asymptote
            y = 1.0 - math.sqrt(1.0 - frac)
            theta_tab = fr._lookup(table, b, lut.inverse_v(y), BOUND, 3)
            assert abs(theta_tab - theta_true) < 3e-3, (b, frac, theta_tab, theta_true)

def test_start_inside_exit_direction(table):
    """Camera inside the sphere: escape/capture and exit direction match the geodesic."""
    errors = []
    for start, d in _rays(1, 80):
        s = fr.setup(table, start, d, BOUND)
        exit_dir, _ = fr.trace(start, d)
        assert (exit_dir is not None) == s["escaped"]
        if exit_dir is not None:
            errors.append(fr.angle(exit_dir, s["exit_dir"]))
    assert max(errors) < 2e-3 and np.median(errors) < 6e-4

def test_start_inside_path_points(table):
    """Points of the reconstructed path lie on the geodesic."""
    for start, d in _rays(2, 25):
        s = fr.setup(table, start, d, BOUND)
        _, path = fr.trace(start, d)
        r = np.linalg.norm(path, axis=1)
        path = path[r < BOUND * 0.98]
        if len(path) < 20:
            continue
        for frac in (0.25, 0.5, 0.75):
            th = s["theta_c"] + frac * (s["theta_final"] - s["theta_c"])
            p = fr.point(table, s, th, BOUND)
            if np.linalg.norm(p) > BOUND * 0.97:
                continue
            gap = np.min(np.linalg.norm(path - p, axis=1))
            assert gap < 0.02 * max(np.linalg.norm(p), 1.0) + 0.05, (start, d, frac, gap)

def test_far_camera_matches_infinity_start(table):
    """A distant camera agrees with an orbit started at infinity."""
    for b in (3.0, 6.0, 12.0, 20.0):
        camera = np.array([-1e4, b, 0.0])
        s = fr.setup(table, camera, (1.0, 0.0, 0.0), BOUND)
        theta_end = abs(fr._lookup(table, b, 0.5, BOUND, 1)) * lut.THETA_MAX
        from_inf = np.array([-math.cos(theta_end), math.sin(theta_end), 0.0])
        assert fr.angle(from_inf, s["exit_dir"]) < 2e-3

def _faded_deflection(table, b, dist=110.0):
    """Bending of a ray from a camera outside the sphere, at impact parameter b."""
    if b >= BOUND:
        return 0.0
    camera = np.array([-math.sqrt(dist ** 2 - b * b), b, 0.0])
    s = fr.setup(table, camera, (1.0, 0.0, 0.0), BOUND)
    return fr.angle((1.0, 0.0, 0.0), s["exit_faded"])

def test_edge_fade_is_seamless(table):
    """The bending reaches 0 at the edge with no jump or kink."""
    bs = np.linspace(14.0, BOUND, 81)
    defl = np.array([_faded_deflection(table, b) for b in bs])
    assert defl[-1] < 1e-6                                      # Nothing at the edge
    assert np.all(np.diff(defl) <= 1e-6)                        # Monotone fade
    slope = np.diff(defl) / np.diff(bs)
    assert abs(slope[-1]) < 0.05 * np.max(np.abs(slope))        # Flat into the edge: no ring
    assert np.max(np.abs(np.diff(slope))) < 0.1 * np.max(np.abs(slope))   # No kinks

@pytest.mark.parametrize("b", [3.0, 5.0, 9.0, 14.0])
def test_exact_gr_inside_fade_zone(table, b):
    """Rays through the inner sphere get the full GR bending from the camera."""
    dist = 110.0
    camera = np.array([-math.sqrt(dist ** 2 - b * b), b, 0.0])
    exit_true, _ = fr.trace(camera, (1.0, 0.0, 0.0))
    got = _faded_deflection(table, b, dist)
    assert got == pytest.approx(fr.angle((1.0, 0.0, 0.0), exit_true), abs=1.5e-3)

def test_weak_pull_whole_line():
    import reference_tracer as rt
    b = 40.0
    full = float(lut.weak_pull(math.pi / 2, b) - lut.weak_pull(-math.pi / 2, b))
    assert full == pytest.approx(2 / b + 15 * math.pi / 16 / b ** 2, rel=1e-9)
    assert rt.deflection_angle(b, r_start=1e6) == pytest.approx(full, rel=2e-3)

def test_camera_crossing_the_edge_is_continuous(table):
    """Moving the camera in through the sphere's surface changes nothing abruptly."""
    d = np.array([0.6, 0.8, 0.0])
    eps = 1e-3
    outside = np.array([-(BOUND + eps), 0.0, 0.0])
    inside = np.array([-(BOUND - eps), 0.0, 0.0])
    s_out = fr.setup(table, outside, d, BOUND)
    s_in = fr.setup(table, inside, d, BOUND)
    assert fr.angle(s_out["exit_faded"], s_in["exit_faded"]) < 2e-3

def test_hovering_observer():
    assert fr.observer_gamma(BOUND, BOUND) == pytest.approx(1.0)
    assert fr.observer_gamma(80.0, BOUND) == pytest.approx(1.0)          # Outside: no change
    g3 = fr.observer_gamma(3.0, BOUND)
    assert g3 == pytest.approx(math.sqrt((1 - 1 / BOUND) / (1 - 1 / 3.0)))
    # Light falling towards a hovering observer is blueshifted (gamma > 1)
    assert g3 > 1.0

def test_hovering_rays_match_geodesic(table):
    for start, d in _rays(3, 30, r_lo=2.0, r_hi=12.0):
        s = fr.setup(table, start, d, BOUND, hovering=True)
        exit_dir, _ = fr.trace(start, d, hovering=True)
        assert (exit_dir is not None) == s["escaped"]
        if exit_dir is not None:
            assert fr.angle(exit_dir, s["exit_dir"]) < 2e-3

@pytest.mark.parametrize("r", [1.8, 3.0, 6.0, 15.0])
def test_hovering_shadow_size(table, r):
    """Angular radius of the shadow for a hovering observer:
    sin(a) = b_c sqrt(1 - r_s/r) / r  (Synge 1966)."""
    start = np.array([-r, 0.0, 0.0])
    lo, hi = 0.0, math.pi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        d = np.array([math.cos(mid), math.sin(mid), 0.0])
        if fr.setup(table, start, d, BOUND, hovering=True)["escaped"]:
            hi = mid
        else:
            lo = mid
    expect = math.asin(physics.CRITICAL_IMPACT * math.sqrt(1 - 1 / r) / r)
    if r < 1.5:
        expect = math.pi - expect
    assert lo == pytest.approx(expect, abs=4e-3)