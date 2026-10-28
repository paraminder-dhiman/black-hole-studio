"""Per-object black hole settings (stored on the root empty)."""

import math

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty)

def _update(self, context):
    if self.id_data.get("_bhs_suspend"):
        return
    from . import builder
    builder.apply(self.id_data)

def _rebuild(self, context):
    if self.id_data.get("_bhs_suspend"):
        return
    from . import builder
    builder.rebuild(self.id_data)

def _f(name, default, lo, hi, desc, soft=None, update=_update, **kw):
    smin, smax = soft if soft else (lo, hi)
    return FloatProperty(name=name, default=default, min=lo, max=hi, soft_min=smin,
                         soft_max=smax, description=desc, update=update, **kw)

def _col(name, default, desc):
    return FloatVectorProperty(name=name, subtype="COLOR", size=3, min=0.0, soft_max=1.0,
                               default=default, description=desc, update=_update)

class BlackHoleSettings(bpy.types.PropertyGroup):
    is_blackhole: BoolProperty(default=False, options={"HIDDEN"})

    mode: EnumProperty(
        name="Render Mode",
        items=[
            ("FAST", "Fast (GPU / Eevee)",
             "Exact Schwarzschild geodesics from a precomputed lookup table, evaluated with "
             "shader nodes. Works on GPU and in Eevee"),
            ("EXACT", "Exact (Cycles OSL)",
             "Integrates every light ray with RK4 in an OSL shader. CPU only. Adds frame "
             "dragging and volumetric jets along curved paths"),
        ],
        default="FAST", update=_rebuild)

    # Physics
    mass_solar: _f("Mass (M☉)", 4.3e6, 1.0, 1e11, "Mass in solar masses. Only sets the real-world "
                   "size shown in the panel: in geometric units every black hole looks the same "
                   "at a given scale", soft=(1.0, 1e10))
    scene_scale: _f("Scale (BU per r_s)", 0.1, 1e-4, 1e4, "Blender units per Schwarzschild radius",
                    soft=(0.01, 10.0))
    spin: _f("Spin a/M", 0.0, -0.998, 0.998, "Dimensionless spin. Positive = disk co-rotates "
             "(prograde). Moves the ISCO and changes orbital velocities exactly; shadow shape and "
             "frame dragging are approximations")
    rotation: EnumProperty(name="Disk Rotation", items=[
        ("CCW", "Counter-clockwise", "Disk rotates counter-clockwise seen from +Z"),
        ("CW", "Clockwise", "Disk rotates clockwise seen from +Z")], default="CCW", update=_update)
    lensing_strength: _f("Lensing Strength", 1.0, 0.0, 3.0, "Scales the general-relativistic "
                         "bending term. 1 = real physics, 0 = no lensing")

    bounding_radius: _f("Influence Radius (r_s)", 30.0, 8.0, 200.0, "Radius of the lens sphere, "
                        "where light is bent. Bending fades out smoothly toward its edge (see Edge "
                        "Fade), so a larger radius is closer to real physics. Disk and fog must fit "
                        "inside 90% of it. The camera may be inside")
    edge_fade: _f("Edge Fade", 0.5, 0.05, 0.9, "Outer fraction of the influence radius over which "
                  "light bending fades to zero, so the sphere leaves no seam in the background. "
                  "Inside it, bending is exact GR", subtype="FACTOR")
    hovering_observer: BoolProperty(
        name="Hovering Observer", default=False, update=_update,
        description="Inside the influence sphere, render what a camera hovering in place (held "
        "by rockets) would see: light is blueshifted and brighter close to the hole, and the "
        "camera's view angles change the shadow's size. Off: the camera sees light as in flat "
        "space")

    steps_per_orbit: IntProperty(name="Steps / Orbit", default=256, min=32, max=4096,
                                 description="Exact mode: RK4 steps per 2π of orbital angle",
                                 update=_update)
    max_orbits: _f("Max Orbits", 3.0, 1.0, 10.0, "Exact mode: maximum windings traced (higher "
                   "resolves more photon-ring images)")
    frame_drag: _f("Frame Dragging", 1.0, 0.0, 5.0, "Exact mode: Lense-Thirring rotation of light "
                   "paths (approximation, scales with spin)")
    shadow_shift: _f("Shadow Shift", 1.0, 0.0, 3.0, "Bardeen shadow displacement for spinning "
                     "holes (approximation, scales with spin)")

    # Accretion disk
    disk_enable: BoolProperty(name="Accretion Disk", default=True, update=_update)
    disk_inner_auto: BoolProperty(name="Inner Edge at ISCO", default=True, update=_update,
                                  description="Put the inner edge at the innermost stable "
                                  "circular orbit for the current spin")
    disk_inner: _f("Inner Radius (r_s)", 3.0, 1.6, 100.0, "Inner edge of the disk")
    disk_outer: _f("Outer Radius (r_s)", 12.0, 2.0, 190.0, "Outer edge of the disk")
    peak_temp: _f("Peak Temperature (K)", 7000.0, 1000.0, 1e8, "Maximum emitted temperature "
                  "(Novikov-Thorne profile)", soft=(1500.0, 40000.0))
    temp_exponent: _f("Temperature Falloff", 0.75, 0.1, 2.0, "Radial exponent q in T ∝ r^-q "
                      "(1 - √(r_in/r))^¼. Thin disk physics: 0.75")
    opacity: _f("Opacity", 0.95, 0.0, 1.0, "Optical depth of the disk")
    emission_strength: _f("Emission", 1.0, 0.0, 1000.0, "Overall brightness", soft=(0.0, 20.0))
    brightness_exponent: _f("Beaming Exponent", 4.0, 0.0, 6.0, "I_obs = g^p · I_emit. 4 = "
                            "bolometric (physical), 3 = specific intensity")
    inner_softness: _f("Inner Softness", 0.05, 0.0, 2.0, "Fade width at the inner edge")
    outer_softness: _f("Outer Softness", 0.3, 0.0, 0.95, "Fade width at the outer edge")

    noise_scale: _f("Turbulence Scale", 1.5, 0.0, 20.0, "Size of the turbulent structures")
    noise_detail: _f("Turbulence Detail", 5.0, 0.0, 12.0, "Octaves of turbulence")
    noise_roughness: _f("Turbulence Roughness", 0.55, 0.0, 1.0, "Strength of fine detail")
    noise_contrast: _f("Turbulence Contrast", 0.35, 0.0, 1.0, "0 = smooth disk, 1 = clumpy streaks")
    noise_speed: _f("Rotation Speed", 0.5, -20.0, 20.0, "Time multiplier for the Keplerian "
                    "rotation of the turbulence (per frame, in r_s/c)", soft=(-5.0, 5.0))
    shear: _f("Spiral Shear", 2.0, -20.0, 20.0, "Logarithmic spiral winding of the structures",
              soft=(-8.0, 8.0))
    dust_lanes: _f("Dust Lanes", 0.0, 0.0, 1.0, "Dark concentric lanes")
    dust_scale: _f("Lane Frequency", 4.0, 0.1, 50.0, "Number of lanes per r_s")

    # Disk texture and density sections
    texture_enable: BoolProperty(name="Disk Texture", default=True, update=_update,
                                 description="Use an image (seen from above, disk centred) "
                                 "for the disk's density and brightness")
    disk_texture: PointerProperty(name="Texture", type=bpy.types.Image, update=_update,
                                  description="Grayscale top-down image of the disk. The image "
                                  "edge corresponds to Outer Radius × Texture Scale")
    texture_density: _f("Density Influence", 1.0, 0.0, 1.0, "How much the texture controls "
                        "the disk's density (opacity)")
    texture_emission: _f("Brightness Influence", 0.5, 0.0, 1.0, "How much the texture "
                         "controls the disk's brightness")
    texture_contrast: _f("Texture Contrast", 1.0, 0.05, 8.0, "Gamma applied to the texture: "
                         ">1 deepens dark gaps, <1 fills them", soft=(0.25, 4.0))
    texture_scale: _f("Texture Scale", 1.0, 0.05, 10.0, "Texture radius relative to the "
                      "outer radius", soft=(0.25, 3.0))
    texture_rotation: FloatProperty(name="Texture Rotation", default=0.0, subtype="ANGLE",
                                    update=_update, description="Fixed rotation of the texture")

    texture_motion: EnumProperty(name="Texture Motion", default="KEPLERIAN", update=_update, items=[
        ("STATIC", "Static", "The texture does not move"),
        ("RIGID", "Rigid", "The whole texture turns at one rate"),
        ("KEPLERIAN", "Keplerian", "Each radius orbits at its real Keplerian speed (inner parts "
         "faster), so structures wind into spirals over time")])
    texture_speed: _f("Texture Speed", 1.0, -50.0, 50.0, "Time multiplier for the texture's "
                      "rotation", soft=(-5.0, 5.0))

    # Disk Thickness
    disk_thickness: _f("Thickness (H/r)", 0.0, 0.0, 0.5, "Scale height of the disk as a fraction "
                       "of the radius. 0 = infinitely thin. Real thin disks: 0.01-0.1; above 0.1 "
                       "the flow is a thick torus rather than a thin disk")
    disk_flare: _f("Flare", 0.0, 0.0, 1.5, "How much thicker the disk (and fog) gets further out: "
                   "H ∝ r · (r / r_in)^flare")

    # Fog / Dust
    fog_enable: BoolProperty(name="Fog & Dust", default=False, update=_update,
                             description="Volumetric dust around the black hole, integrated along "
                             "the bent light paths (so it is lensed too)")
    fog_density: _f("Density", 0.05, 0.0, 5.0, "Amount of fog (per r_s of path)", soft=(0.0, 0.3))
    fog_glow: _f("Glow", 2.0, 0.0, 1000.0, "Light the fog scatters from the disk (glowing haze)",
                 soft=(0.0, 10.0))
    fog_absorption: _f("Dust Absorption", 1.0, 0.0, 100.0, "How strongly the fog dims what is "
                       "behind it (dark dust)", soft=(0.0, 5.0))
    fog_height: _f("Height (H/r)", 0.15, 0.005, 2.0, "Thickness of the haze around the disk "
                   "plane, as a fraction of the radius", soft=(0.01, 0.6))
    fog_match_disk: BoolProperty(name="Match Disk Thickness", default=False, update=_update,
                                 description="Use the disk's thickness for the fog height")
    fog_inner: _f("Inner Radius (r_s)", 2.0, 1.0, 200.0, "Where the fog starts")
    fog_outer: _f("Outer Radius (r_s)", 22.0, 2.0, 200.0, "Where the fog ends (capped by the "
                  "influence radius)")
    fog_falloff: _f("Edge Falloff", 0.5, 0.01, 1.0, "Fraction of the outer radius over which the "
                    "fog fades out")
    fog_halo: _f("Halo", 0.25, 0.0, 1.0, "Blend from a haze hugging the disk (0) to a spherical "
                 "cloud around the hole (1)")
    halo_falloff: _f("Halo Falloff", 1.5, 0.0, 4.0, "How quickly the halo thins with distance")

    fog_noise: _f("Clumpiness", 0.6, 0.0, 1.0, "0 = smooth fog, 1 = clumpy dust")
    fog_noise_scale: _f("Clump Scale", 0.35, 0.01, 10.0, "Frequency of the dust clumps",
                        soft=(0.05, 2.0))
    fog_noise_detail: _f("Clump Detail", 4.0, 0.0, 12.0, "Octaves of fog detail")
    fog_swirl: _f("Swirl Speed", 1.0, -20.0, 20.0, "Orbital speed of the fog relative to the "
                  "Keplerian rate (animation)", soft=(-5.0, 5.0))
    fog_color: _col("Fog Color", (0.55, 0.42, 0.36), "Colour of the dust")
    fog_color_mix: _f("Fog Color Mix", 0.5, 0.0, 1.0, "0 = tinted by the disk's light, "
                      "1 = Fog Color")

    volume_samples: EnumProperty(
        name="Volume Quality", default="16", update=_rebuild,
        description="Samples along each light path for fog and disk thickness (rebuilds the "
        "shader). More = smoother, slower; render time grows roughly linearly",
        items=[("8", "Draft (8)", "Fast look-dev"),
               ("16", "Standard (16)", "Good for fog; thick disks may show faint streaks"),
               ("24", "High (24)", "Clean thick disks"),
               ("32", "Very High (32)", ""), ("48", "Ultra (48)", "")])

    # Viewport
    show_influence_sphere: BoolProperty(name="Show Influence Sphere", default=False,
                                        update=_update, description="Draw the wireframe of the "
                                        "lens sphere in the viewport (it always renders)")
    show_helpers: BoolProperty(name="Show Helpers", default=True, update=_update,
                               description="Draw the photon-sphere and horizon markers and the "
                               "disk-light outline in the viewport")

    # Relativistic effects
    doppler: _f("Doppler Beaming", 1.0, 0.0, 3.0, "Relativistic Doppler factor exponent. "
                "1 = physical, 0 = off (as in the film Interstellar)")
    gravity_shift: _f("Gravitational Redshift", 1.0, 0.0, 3.0, "1 = physical, 0 = off")
    color_shift: _f("Color Shift", 1.0, 0.0, 1.0, "How much the redshift changes the observed "
                    "blackbody color (brightness is always shifted)")
    photon_ring_boost: _f("Photon Ring Boost", 1.0, 0.0, 20.0, "Multiplier on secondary and "
                          "higher-order disk images", soft=(0.0, 5.0))

    # Style
    physical_mix: _f("Physical Color", 1.0, 0.0, 1.0, "1 = blackbody colors, 0 = your gradient")
    color_inner: _col("Hot", (1.0, 0.95, 0.85), "Artistic color of the hottest region")
    color_mid: _col("Warm", (1.0, 0.55, 0.15), "Artistic color of the middle region")
    color_outer: _col("Cool", (0.6, 0.12, 0.02), "Artistic color of the coolest region")
    hue_shift: _f("Hue Shift", 0.0, -0.5, 0.5, "Rotate the hue of disk, jets and glow")
    saturation: _f("Saturation", 1.0, 0.0, 2.0, "Saturation of disk, jets and glow")
    ring_glow: _f("Einstein Ring Glow", 0.0, 0.0, 100.0, "Glow hugging the shadow edge",
                  soft=(0.0, 5.0))
    ring_glow_width: _f("Glow Width", 0.15, 0.001, 5.0, "Width of the glow in r_s")
    ring_glow_color: _col("Ring Color", (1.0, 0.7, 0.4), "Color of the ring glow")
    horizon_glow: _f("Horizon Glow", 0.0, 0.0, 100.0, "Glow inside the shadow edge",
                     soft=(0.0, 5.0))
    horizon_glow_color: _col("Horizon Color", (1.0, 0.4, 0.1), "Color of the horizon glow")

    # Jets
    jet_enable: BoolProperty(name="Relativistic Jets", default=False, update=_update)
    jet_length: _f("Length (r_s)", 20.0, 1.0, 200.0, "Jet length (capped by the influence radius)")
    jet_angle: FloatProperty(name="Opening Angle", default=math.radians(5.0), min=0.0,
                             max=math.radians(45.0), subtype="ANGLE", update=_update,
                             description="Half opening angle of the jet cone")
    jet_base: _f("Base Width (r_s)", 0.4, 0.01, 10.0, "Width of the jet at its base")
    jet_intensity: _f("Intensity", 1.0, 0.0, 1000.0, "Jet brightness", soft=(0.0, 20.0))
    jet_noise: _f("Turbulence", 0.5, 0.0, 1.0, "Knotty structure along the jet")
    jet_color: _col("Color", (0.4, 0.6, 1.0), "Jet color (synchrotron glow is bluish)")

    # Background / Lensing
    lens_objects: BoolProperty(name="Lens Scene Objects (Cycles)", default=False, update=_update,
                               description="Bend the view of real scene objects behind the black "
                               "hole using a steered refraction (Cycles only). Off: the lensed "
                               "background is the skybox/starfield")
    use_skybox: BoolProperty(name="Use Skybox", default=True, update=_update,
                             description="Use a panoramic sky image (equirectangular HDR/EXR) "
                             "as the background the black hole lenses. Off: procedural starfield")
    sky_image: PointerProperty(name="Skybox", type=bpy.types.Image, update=_update,
                               description="Equirectangular (2:1) sky image. Defaults to the "
                               "bundled Milky Way (NASA); use the folder icon to load your own")
    sky_strength: _f("Skybox Brightness", 2.5, 0.0, 1000.0, "Multiplier on the skybox image",
                     soft=(0.0, 30.0))
    sky_rotation: FloatProperty(name="Skybox Rotation", default=0.0, subtype="ANGLE",
                                update=_update, description="Rotate the skybox around the "
                                "world Z axis")

    star_overlay: _f("Stars Over Skybox", 0.0, 0.0, 1.0, "Add the procedural stars on top of "
                     "the skybox")
    bg_strength: _f("Background Brightness", 1.0, 0.0, 100.0, "Master brightness of the lensed "
                    "background and the world", soft=(0.0, 5.0))
    star_density: _f("Star Density", 220.0, 1.0, 5000.0, "Procedural stars per unit sphere",
                     soft=(20.0, 1000.0))
    star_brightness: _f("Star Brightness", 1.0, 0.0, 100.0, "Procedural star brightness",
                        soft=(0.0, 5.0))
    star_size: _f("Star Size", 0.06, 0.001, 0.5, "Procedural star radius")
    nebula_strength: _f("Nebula", 0.15, 0.0, 10.0, "Procedural nebula brightness",
                        soft=(0.0, 2.0))
    nebula_color: _col("Nebula Color", (0.25, 0.35, 0.8), "Procedural nebula color")

    illuminate_scene: BoolProperty(name="Disk Lights the Scene", default=True,
                                   update=_update, description="Add an invisible emitter "
                                   "matching the disk so it lights nearby objects (Cycles, Eevee)")
    illumination_strength: _f("Scene Light Strength", 1.0, 0.0, 1e5, "Brightness of the "
                              "disk's light on scene objects", soft=(0.0, 50.0))
    drive_world: BoolProperty(name="Drive World Background", default=True, update=_update,
                              description="Keep the scene world in sync with these sky settings")

    # Camera Helper
    view_inclination: FloatProperty(name="Inclination", default=math.radians(80.0), min=0.0,
                                    max=math.pi, subtype="ANGLE",
                                    description="Camera angle from the disk axis (90° = edge-on)")
    view_distance: FloatProperty(name="Distance (r_s)", default=60.0, min=2.0, max=10000.0,
                                 description="Camera distance from the hole")


classes = (BlackHoleSettings,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.blackhole = PointerProperty(type=BlackHoleSettings)

def unregister():
    del bpy.types.Object.blackhole
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)