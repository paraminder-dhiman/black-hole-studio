import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from blackhole_studio import lut, physics  # noqa: E402
import reference_tracer as rt  # noqa: E402

def test_schwarzschild_radius_sun():
    assert physics.schwarzschild_radius_m(1.0) == pytest.approx(2953.3, rel=1e-3)

def test_critical_impact_parameter():
    assert physics.CRITICAL_IMPACT == pytest.approx(2.598, abs=1e-3)
    # Just outside b_c escapes after many windings, just inside is captured
    assert rt.deflection_angle(physics.CRITICAL_IMPACT * 0.995) is None
    # Strong-deflection limit: alpha ~ -ln(b/b_c - 1) + ln(216(7 - 4 sqrt 3)) - pi
    expected = -math.log(0.005) + math.log(216 * (7 - 4 * math.sqrt(3))) - math.pi
    assert rt.deflection_angle(physics.CRITICAL_IMPACT * 1.005, r_start=1e5) == pytest.approx(expected, abs=0.02)

@pytest.mark.parametrize("b", [50.0, 100.0, 200.0])
def test_weak_field_deflection(b):
    # Einstein: alpha = 4M/b = 2 r_s / b  (+ second-order (15 pi/16)(r_s/2b)^2 ... small)
    alpha = rt.deflection_angle(b, r_start=1e6)
    assert alpha == pytest.approx(2.0 / b, rel=0.05)

def test_strong_field_deflection_known_value():
    # alpha(b = 3 r_s) from the exact elliptic-integral solution ~ 1.7194 rad
    assert rt.deflection_angle(3.0, r_start=1e5) == pytest.approx(1.7194, abs=2e-3)

def test_lensing_strength_zero_is_straight():
    assert rt.deflection_angle(5.0, coupling=0.0, r_start=1e5) == pytest.approx(0.0, abs=1e-3)

def test_isco():
    assert physics.isco_radius(0.0) == pytest.approx(3.0)                # 6M
    assert physics.isco_radius(0.9999) == pytest.approx(0.5, abs=0.05)   # -> 1M
    assert physics.isco_radius(-0.9999) == pytest.approx(4.5, abs=1e-3)  # 9M

def test_horizon():
    assert physics.horizon_radius(0.0) == pytest.approx(1.0)
    assert physics.horizon_radius(0.9999) == pytest.approx(0.5, abs=0.01)

def test_temperature_profile_peak():
    r_in = 3.0
    x_peak = physics.temperature_peak_x()
    assert x_peak == pytest.approx(49 / 36)
    t_peak = physics.disk_temperature(x_peak * r_in, r_in, 1e7)
    assert t_peak == pytest.approx(1e7)
    rs = np.linspace(r_in * 1.001, 30, 2000)
    assert max(physics.disk_temperature(r, r_in, 1e7) for r in rs) <= 1e7 * (1 + 1e-6)
    assert physics.disk_temperature(r_in, r_in, 1e7) == 0.0

def test_redshift_static_limits():
    r = 10.0
    # Photon with no angular momentum: pure gravitational + transverse Doppler
    g = physics.redshift_factor(r, 0.0)
    assert g == pytest.approx(math.sqrt(1 - 1.5 / r))
    # Photons moving with the flow are blueshifted, against it redshifted
    assert physics.redshift_factor(r, 5.0) > g > physics.redshift_factor(r, -5.0)
    # Disable switches
    assert physics.redshift_factor(r, 5.0, doppler=0.0, gravity=0.0) == 1.0

def test_kepler_omega_schwarzschild():
    # Omega = sqrt(M / r^3) with M = 0.5
    assert physics.orbital_omega(10.0) == pytest.approx(math.sqrt(0.5 / 1000.0))

@pytest.fixture(scope="module")
def table():
    return lut.compute(40.0 / lut.B_MAX_FACTOR)

def test_lut_mapping_roundtrip():
    for b in (0.5, 2.6, 10.0, 39.0):
        assert lut.b_from_x(lut.x_from_b(b, 40.0), 40.0) == pytest.approx(b)

@pytest.mark.parametrize("b", [3.0, 5.0, 10.0])
def test_lut_matches_direct_integration(table, b):
    # Total swept angle at escape = pi + deflection
    theta_end = lut.sample(table, b, 0.0, 40.0)[1] * lut.THETA_MAX
    assert theta_end == pytest.approx(math.pi + rt.deflection_angle(b, r_start=1e5), abs=0.01)
    # u at theta = pi/2 against a fine direct integration
    u, du, th = 0.0, 1.0 / b, 0.0
    h = 1e-4
    while th < math.pi / 2 - 1e-12:
        u, du = rt.rk4_step(u, du, h)
        th += h
    assert lut.sample(table, b, math.pi / 2, 40.0)[0] == pytest.approx(u, rel=0.01)

def test_lut_capture_flag(table):
    # Captured orbits store a negative swept angle
    assert lut.sample(table, 1.0, 0.0, 40.0)[1] < 0.0
    assert lut.sample(table, 5.0, 0.0, 40.0)[1] > 0.0

def test_tracer_disk_and_shadow():
    # Camera on the axis looking straight at the hole: centre captured, disk ring visible
    cam = np.array([[0.0, 0.0, 30.0]] * 3)
    dirs = np.array([[0.0, 0.0, -1.0], [0.25, 0.0, -1.0], [0.6, 0.0, -1.0]])
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    params = dict(steps_per_orbit=512, max_orbits=3, spin=0.0, r_exit=40.0,
                  coupling=1.0, r_in=3.0, r_out=12.0, opacity=1.0)
    res = rt.trace_rays(cam, dirs, params)
    assert res["captured"][0]
    assert res["alpha"][1] > 0.99      # b ~ 7.3 lands on the disk
    assert not res["captured"][2] and res["alpha"][2] == 0.0

def test_uedelta_roundtrip():
    """The UE source-texture delta filter decoded by tools/extract_uasset_texture.py."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    pytest.importorskip("PIL")
    import extract_uasset_texture as ex
    rng = np.random.default_rng(1)
    img = rng.integers(0, 65536, size=(130, 32), dtype=np.int64)
    enc = img.copy()
    for y in range(1, img.shape[0]):
        if y % ex.UEDELTA_KEY_ROWS:
            enc[y] = (img[y] - img[y - 1] + ex.UEDELTA_BIAS) % 65536
    assert np.array_equal(ex.undo_uedelta(enc.astype(np.uint16)), img.astype(np.uint16))

def test_nonphysical_detection():
    from types import SimpleNamespace
    from blackhole_studio import params
    s = SimpleNamespace(**{k: v for k, (v, _) in params.PHYSICAL_VALUES.items()}, disk_thickness=0.05)
    assert params.nonphysical(s) == []
    s.doppler = 0.0
    s.disk_inner_auto = False
    s.disk_thickness = 0.2            # Beyond the thin-disk limit
    labels = [label for label, _, _ in params.nonphysical(s)]
    assert labels == ["Disk Thickness (thin-disk limit)", "Doppler Beaming", "Inner Edge at ISCO"]