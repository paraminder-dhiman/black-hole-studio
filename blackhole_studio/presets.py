"""Look presets.  Each is a partial set of BlackHoleSettings values applied over the defaults."""

import math

DEFAULTS_SKIP = {"is_blackhole", "mode", "sky_image", "disk_texture", "mass_solar", "scene_scale",
                 "drive_world", "use_skybox", "sky_strength", "sky_rotation", "lens_objects",
                 "view_distance", "illuminate_scene", "illumination_strength", "volume_samples",
                 "show_influence_sphere", "show_helpers", "hovering_observer", "edge_fade"}

PRESETS = {
    "DEFAULT": ("Physical Default", "Thin Novikov-Thorne disk around a non-spinning hole", {}),

    "GARGANTUA": ("Gargantua (Interstellar)",
                  "Fast-spinning hole seen nearly edge-on, with Doppler and redshift switched off "
                  "as in the film", {
                      "spin": 0.6, "peak_temp": 4800.0, "doppler": 0.0, "gravity_shift": 0.0,
                      "color_shift": 0.0, "physical_mix": 0.35, "brightness_exponent": 4.0,
                      "color_inner": (1.0, 0.93, 0.8), "color_mid": (1.0, 0.7, 0.38),
                      "color_outer": (0.75, 0.38, 0.12), "disk_outer": 16.0,
                      "noise_contrast": 0.75, "noise_scale": 2.5, "shear": 0.5, "opacity": 0.9,
                      "outer_softness": 0.4, "ring_glow": 0.3, "ring_glow_width": 0.08,
                      "emission_strength": 2.0, "view_inclination": math.radians(86.0),
                      "nebula_strength": 0.0, "star_brightness": 0.6,
                      "disk_thickness": 0.02, "fog_enable": True, "fog_density": 0.03,
                      "fog_glow": 0.4, "fog_absorption": 1.5, "fog_halo": 0.1, "fog_height": 0.08,
                      "fog_outer": 20.0, "fog_color": (0.6, 0.45, 0.32)}),

    "EHT": ("Event Horizon Telescope",
            "Hot, optically thin flow seen at low inclination, strong beaming (M87*-like view)", {
                "spin": 0.9, "peak_temp": 9000.0, "opacity": 0.45, "noise_contrast": 0.5,
                "noise_scale": 1.0, "physical_mix": 0.0, "color_inner": (1.0, 0.85, 0.5),
                "color_mid": (1.0, 0.45, 0.08), "color_outer": (0.5, 0.08, 0.0),
                "disk_outer": 8.0, "outer_softness": 0.8, "photon_ring_boost": 2.0,
                "disk_thickness": 0.12, "disk_flare": 0.3,
                "emission_strength": 3.0, "view_inclination": math.radians(17.0),
                "star_brightness": 0.0, "nebula_strength": 0.0}),

    "QUASAR": ("Quasar", "Very hot, bright disk with relativistic jets", {
        "spin": 0.95, "peak_temp": 30000.0, "emission_strength": 1.5, "disk_outer": 24.0,
        "noise_contrast": 0.5, "jet_enable": True, "jet_intensity": 4.0, "jet_length": 28.0,
        "jet_angle": math.radians(4.0), "jet_color": (0.45, 0.6, 1.0), "ring_glow": 0.4,
        "view_inclination": math.radians(65.0), "fog_enable": True, "fog_density": 0.03,
        "fog_glow": 3.0, "fog_absorption": 0.3, "fog_halo": 0.7, "fog_outer": 26.0,
        "fog_color_mix": 0.2}),

    "NEON": ("Neon Stylized", "Artistic gradient, strong glow, saturated colors", {
        "physical_mix": 0.0, "color_inner": (0.9, 0.95, 1.0), "color_mid": (0.1, 0.8, 1.0),
        "color_outer": (0.6, 0.0, 1.0), "saturation": 1.4, "ring_glow": 2.0,
        "ring_glow_color": (0.2, 0.9, 1.0), "ring_glow_width": 0.25, "horizon_glow": 0.4,
        "horizon_glow_color": (0.6, 0.0, 1.0), "noise_contrast": 0.9, "shear": 4.0,
        "dust_lanes": 0.4, "emission_strength": 2.5, "nebula_strength": 0.6,
        "nebula_color": (0.5, 0.1, 0.9)}),

    "SHADOW": ("Pure Lensing", "No disk: only the shadow, photon ring and lensed star field", {
        "disk_enable": False, "ring_glow": 0.6, "ring_glow_width": 0.05,
        "star_density": 600.0, "star_brightness": 2.0, "nebula_strength": 0.4}),
}

def items():
    return [(key, label, desc) for key, (label, desc, _) in PRESETS.items()]

def apply(settings, key):
    """Reset to defaults, then apply the preset's overrides."""
    from . import builder
    root = settings.id_data
    # suspend per-property updates by writing through a temporary flag
    root["_bhs_suspend"] = True
    try:
        for prop in settings.bl_rna.properties:
            pid = prop.identifier
            if pid in DEFAULTS_SKIP or pid == "rna_type" or prop.is_readonly:
                continue
            if getattr(prop, "is_array", False):
                setattr(settings, pid, tuple(prop.default_array))
            elif prop.type in {"FLOAT", "INT", "BOOLEAN", "ENUM"}:
                setattr(settings, pid, prop.default)
        for pid, value in PRESETS[key][2].items():
            setattr(settings, pid, value)
    finally:
        del root["_bhs_suspend"]
    builder.apply(root)