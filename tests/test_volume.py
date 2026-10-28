import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from blackhole_studio import volume  # noqa: E402

def test_normal_cdf_accuracy():
    xs = np.linspace(-6, 6, 2001)
    err = max(abs(volume.normal_cdf(x) - 0.5 * (1 + math.erf(x / math.sqrt(2)))) for x in xs)
    assert err < 2e-4

@pytest.mark.parametrize("h", [0.01, 0.1, 1.0])
@pytest.mark.parametrize("mu", [1.0, 0.5, 0.1])
def test_slab_column_matches_numeric_integral(h, mu):
    # straight segment from z = +2 to z = -2 at inclination mu = |dz/ds|
    z0, z1 = 2.0, -2.0
    ds = abs(z1 - z0) / mu
    fine = np.linspace(0, 1, 200001)
    z = z0 + (z1 - z0) * fine
    rho = np.exp(-0.5 * (z / h) ** 2) / (math.sqrt(2 * math.pi) * h)
    numeric = float(np.sum(0.5 * (rho[1:] + rho[:-1]) * np.diff(fine * ds)))
    assert volume.slab_column(z0, z1, h, ds) == pytest.approx(numeric, rel=2e-3, abs=1e-6)

def test_thin_slab_is_never_missed():
    # a slab 1000x thinner than the segment still gives the full column 1/mu
    mu = 0.3
    assert volume.slab_column(1.0, -1.0, 1e-3, 2.0 / mu) == pytest.approx(1.0 / mu, rel=1e-3)
    # splitting the segment does not change the answer
    total = volume.slab_column(1.0, 0.2, 1e-3, 0.8 / mu) + volume.slab_column(0.2, -1.0, 1e-3, 1.2 / mu)
    assert total == pytest.approx(1.0 / mu, rel=1e-3)

def test_flat_segment_uses_density():
    h = 0.5
    assert volume.slab_column(0.0, 0.0, h, 2.0) == pytest.approx(2.0 / (math.sqrt(2 * math.pi) * h))

def test_fog_transmittance_converges():
    s = dict(density=0.05, height=0.15, r_in=3.0, flare=0.0, halo=0.3, halo_falloff=1.5,
             inner=2.0, outer=25.0, falloff=0.5)
    ab = 1.0

    def tau(p0, p1, mid, ds):
        return ab * volume.fog_density(mid, s) * ds

    def path(n):
        # straight edge-on ray through the fog at height 0.4
        return [(-30.0 + 60.0 * i / n, 6.0, 0.4) for i in range(n + 1)]

    _, t_ref = volume.march(path(20000), tau, lambda m, ds: 0.0)
    _, t_24 = volume.march(path(48), tau, lambda m, ds: 0.0)
    assert 0.0 < t_ref < 1.0
    assert t_24 == pytest.approx(t_ref, abs=0.02)

def test_fog_window_and_halo():
    s = dict(density=1.0, height=0.1, r_in=3.0, flare=0.0, halo=0.0, halo_falloff=2.0,
             inner=3.0, outer=20.0, falloff=0.3)
    assert volume.fog_density((10.0, 0.0, 0.0), s) == pytest.approx(1.0)
    assert volume.fog_density((10.0, 0.0, 5.0), s) < 1e-5          # 5 scale heights above the plane
    assert volume.fog_density((25.0, 0.0, 0.0), s) == 0.0          # outside
    s["halo"] = 1.0
    assert volume.fog_density((10.0, 0.0, 5.0), s) > 0.05          # the halo fills it