"""Map user settings to shader inputs (no bpy dependency)."""

from . import lut, physics

# Fog and disk fade out completely inside this fraction of the influence radius,
# so nothing is cut off at the sphere's edge (where it would show a hard rim).
CONTENT_LIMIT = 0.9

def _rgb(c):
    return tuple(c[:3])

def derived(s):
    """Physical quantities derived from the settings, in r_s units."""
    spin = s.spin
    bound = s.bounding_radius
    if s.disk_inner_auto:
        r_in = physics.isco_radius(spin)
    else:
        r_in = s.disk_inner
    r_in = max(r_in, 1.6)
    # The soft outer edge reaches r_out * (1 + softness)
    r_out = min(max(s.disk_outer, r_in + 0.1), disk_outer_limit(s))
    return {"r_in": r_in, "r_out": r_out, "bound": bound, "horizon": physics.horizon_radius(spin)}

def disk_outer_limit(s):
    return CONTENT_LIMIT * s.bounding_radius / (1.0 + s.outer_softness)

def fog_outer_limit(s):
    return CONTENT_LIMIT * s.bounding_radius

def limited(s):
    """[(what, requested, allowed, radius needed)] for content the influence sphere cuts back."""
    out = []
    if s.disk_enable and s.disk_outer > disk_outer_limit(s) + 1e-4:
        out.append(("Disk", s.disk_outer, disk_outer_limit(s),
                    s.disk_outer * (1.0 + s.outer_softness) / CONTENT_LIMIT))
    if s.fog_enable and s.fog_outer > fog_outer_limit(s) + 1e-4:
        out.append(("Fog", s.fog_outer, fog_outer_limit(s), s.fog_outer / CONTENT_LIMIT))
    return out

def shader_values(s):
    """Values for the OSL inputs / named Value nodes (identical names)."""
    d = derived(s)
    bound = d["bound"]
    v = {
        # Object space of the lens sphere is in units of its radius
        "Scale": 1.0 / bound,
        "StepsPerOrbit": int(s.steps_per_orbit),
        "MaxOrbits": s.max_orbits,
        "Strength": s.lensing_strength,
        "ExitRadius": bound * 0.999,
        "Bound": bound,
        "LutBMax": bound * lut.B_MAX_FACTOR,
        "EdgeFade": s.edge_fade,
        "Hovering": 1.0 if s.hovering_observer else 0.0,
        "Spin": s.spin,
        "FrameDrag": s.frame_drag,
        "ShadowShift": s.shadow_shift,
        "RotationSign": 1.0 if s.rotation == "CCW" else -1.0,
        "DiskEnable": 1.0 if s.disk_enable else 0.0,
        "DiskInner": d["r_in"],
        "DiskOuter": d["r_out"],
        "PeakTemp": s.peak_temp,
        "TempExponent": s.temp_exponent,
        "Opacity": s.opacity,
        "EmissionStrength": s.emission_strength,
        "BrightnessExponent": s.brightness_exponent,
        "Doppler": s.doppler,
        "Gravity": s.gravity_shift,
        "ColorShift": s.color_shift,
        "InnerSoftness": s.inner_softness,
        "OuterSoftness": s.outer_softness,
        "NoiseScale": s.noise_scale,
        "NoiseDetail": s.noise_detail,
        "NoiseRoughness": s.noise_roughness,
        "NoiseContrast": s.noise_contrast,
        "NoiseSpeed": s.noise_speed,
        "Shear": s.shear,
        "DustLanes": s.dust_lanes,
        "DustScale": s.dust_scale,
        "TextureEnable": 1.0 if (s.texture_enable and s.disk_texture is not None) else 0.0,
        "TextureDensity": s.texture_density,
        "TextureEmission": s.texture_emission,
        "TextureContrast": s.texture_contrast,
        "TextureScale": s.texture_scale,
        "TextureRotation": s.texture_rotation,
        "TextureMotion": {"STATIC": 0.0, "RIGID": 1.0, "KEPLERIAN": 2.0}[s.texture_motion],
        "TextureSpeed": s.texture_speed,
        "DiskThickness": s.disk_thickness,
        "DiskFlare": s.disk_flare,
        "FogEnable": 1.0 if s.fog_enable else 0.0,
        "FogDensity": s.fog_density,
        "FogGlow": s.fog_glow,
        "FogAbsorption": s.fog_absorption,
        "FogHeight": max(s.disk_thickness, 0.005) if s.fog_match_disk else s.fog_height,
        "FogInner": s.fog_inner,
        "FogOuter": min(s.fog_outer, fog_outer_limit(s)),
        "FogFalloff": s.fog_falloff,
        "FogHalo": s.fog_halo,
        "HaloFalloff": s.halo_falloff,
        "FogNoise": s.fog_noise,
        "FogNoiseScale": s.fog_noise_scale,
        "FogNoiseDetail": s.fog_noise_detail,
        "FogSwirl": s.fog_swirl,
        "FogColor": _rgb(s.fog_color),
        "FogColorMix": s.fog_color_mix,
        "PhysicalMix": s.physical_mix,
        "ColorInner": _rgb(s.color_inner),
        "ColorMid": _rgb(s.color_mid),
        "ColorOuter": _rgb(s.color_outer),
        "PhotonRingBoost": s.photon_ring_boost,
        "RingGlow": s.ring_glow,
        "RingGlowWidth": s.ring_glow_width,
        "RingGlowColor": _rgb(s.ring_glow_color),
        "HorizonGlow": s.horizon_glow,
        "HorizonGlowColor": _rgb(s.horizon_glow_color),
        "Hue": s.hue_shift + 0.5,
        "Saturation": s.saturation,
        "JetEnable": 1.0 if s.jet_enable else 0.0,
        "JetLength": min(s.jet_length, bound * 0.98),
        "JetAngle": s.jet_angle,
        "JetBase": s.jet_base,
        "JetIntensity": s.jet_intensity,
        "JetNoise": s.jet_noise,
        "JetColor": _rgb(s.jet_color),
        "LensObjects": 1.0 if s.lens_objects else 0.0,
        "UseSky": 1.0 if (s.use_skybox and s.sky_image is not None) else 0.0,
        "SkyStrength": s.sky_strength,
        "SkyRotation": s.sky_rotation,
        "StarOverlay": s.star_overlay,
        "BgStrength": s.bg_strength,
        "IlluminationStrength": s.illumination_strength if s.illuminate_scene else 0.0,
        "StarDensity": s.star_density,
        "StarBrightness": s.star_brightness,
        "StarSize": s.star_size,
        "NebulaStrength": s.nebula_strength,
        "NebulaColor": _rgb(s.nebula_color),
    }
    return v

WORLD_KEYS = ("UseSky", "SkyStrength", "SkyRotation", "StarOverlay", "BgStrength", "StarDensity", "StarBrightness",
              "StarSize", "NebulaStrength", "NebulaColor")

# Physically correct values of the settings that change the physics or the
# physical look.  Everything else (turbulence, texture, fog, jets, sky) is
# structure or art direction and does not contradict the physics.
PHYSICAL_VALUES = {
    "lensing_strength": (1.0, "Lensing Strength"),
    "doppler": (1.0, "Doppler Beaming"),
    "gravity_shift": (1.0, "Gravitational Redshift"),
    "color_shift": (1.0, "Color Shift"),
    "brightness_exponent": (4.0, "Beaming Exponent"),
    "temp_exponent": (0.75, "Temperature Falloff"),
    "physical_mix": (1.0, "Physical Color"),
    "photon_ring_boost": (1.0, "Photon Ring Boost"),
    "ring_glow": (0.0, "Einstein Ring Glow"),
    "horizon_glow": (0.0, "Horizon Glow"),
    "hue_shift": (0.0, "Hue Shift"),
    "saturation": (1.0, "Saturation"),
    "disk_inner_auto": (True, "Inner Edge at ISCO"),
}

# Settings that are physical up to a limit: (limit, label).
PHYSICAL_LIMITS = {
    "disk_thickness": (0.1, "Disk Thickness (thin-disk limit)"),
}

def structure(s):
    """Settings that change the shader graph itself (a change triggers a rebuild)."""
    volume = s.fog_enable or s.disk_thickness > 0.0
    return (s.mode, int(s.volume_samples) if volume else 0, s.disk_thickness > 0.0)

def nonphysical(s, tol=1e-4):
    """[(label, current, physical)] for every setting that departs from real physics."""
    out = []
    for pid, (limit, label) in PHYSICAL_LIMITS.items():
        cur = getattr(s, pid)
        if cur > limit + tol:
            out.append((label, cur, limit))
    for pid, (value, label) in PHYSICAL_VALUES.items():
        cur = getattr(s, pid)
        if isinstance(value, bool):
            if bool(cur) != value:
                out.append((label, cur, value))
        elif abs(cur - value) > tol:
            out.append((label, cur, value))
    return out