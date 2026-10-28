"""3D View sidebar panels (N-panel → "Black Hole" tab)."""

import math

import bpy
from mathutils import Vector

from . import builder, params, physics

def _limit_warning(layout, s, what):
    """Tell the user when the influence sphere cuts back the disk or fog."""
    for name, _req, allowed, needed in params.limited(s):
        if name == what:
            col = layout.column(align=True)
            col.label(text=f"{what} fades out at {allowed:.1f} r_s (Influence Radius)", icon="ERROR")
            col.label(text=f"   Raise Influence Radius to {needed:.0f} r_s to fit it")

def _root(context):
    return builder.find_root(context.active_object)

class BHS_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Black Hole"
    bl_label = "Black Hole Studio"

    def draw(self, context):
        layout = self.layout
        root = _root(context)
        row = layout.row(align=True)
        row.operator("blackhole.add", icon="ADD")
        row.operator("blackhole.help", text="", icon="QUESTION")
        if root is None:
            layout.label(text="Select a black hole to edit it", icon="INFO")
            return
        s = root.blackhole
        layout.prop(s, "mode", text="")

        if s.mode == "EXACT" and not (context.scene.render.engine == "CYCLES"
                                      and context.scene.cycles.shading_system):
            box = layout.box()
            box.label(text="Exact mode needs Cycles + OSL (CPU)", icon="ERROR")
            box.operator("blackhole.configure_render")

        row = layout.row(align=True)
        row.prop(s, "show_influence_sphere", text="Sphere", toggle=True, icon="SHADING_WIRE")
        row.prop(s, "show_helpers", text="Helpers", toggle=True, icon="EMPTY_AXIS")
        row = layout.row(align=True)
        row.operator_menu_enum("blackhole.preset", "preset", text="Presets", icon="PRESET")
        row.operator("blackhole.rebuild", text="", icon="FILE_REFRESH")
        row.operator("blackhole.remove", text="", icon="TRASH")

        d = params.derived(s)
        rs_m = physics.schwarzschild_radius_m(s.mass_solar)
        col = layout.column(align=True)
        col.label(text=f"r_s = {physics.format_length(rs_m)}  ({s.scene_scale:.3g} BU)")
        col.label(text=f"Shadow ⌀ ≈ {2 * physics.shadow_radius(s.spin):.3f} r_s"
                       f"  = {physics.format_length(2 * physics.shadow_radius(s.spin) * rs_m)}")
        col.label(text=f"ISCO = {physics.isco_radius(s.spin):.3f} r_s,  horizon = "
                       f"{physics.horizon_radius(s.spin):.3f} r_s")

        if s.disk_inner_auto and physics.isco_radius(s.spin) < d["r_in"] - 1e-6:
            col.label(text=f"Disk inner edge limited to {d['r_in']:.2f} r_s "
                           "(Schwarzschild light paths)", icon="INFO")

        cam = context.scene.camera
        if cam is not None:
            axis = root.matrix_world.to_3x3() @ Vector((0, 0, 1))
            to_cam = cam.matrix_world.to_translation() - root.matrix_world.to_translation()
            if to_cam.length > 0 and axis.length > 0:
                inc = math.degrees(axis.angle(to_cam))
                dist = to_cam.length / max(s.scene_scale * max(root.matrix_world.to_scale()), 1e-9)
                col.label(text=f"Camera: inclination {inc:.1f}°, distance {dist:.1f} r_s")

                if dist < d["horizon"]:
                    col.label(text="Camera is inside the event horizon", icon="ERROR")
                elif dist < 1.5 and s.mode == "FAST":
                    col.label(text="Inside the photon sphere: Exact mode is more accurate",
                              icon="INFO")

class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Black Hole"
    bl_parent_id = "BHS_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = True
        self.draw_settings(layout, _root(context).blackhole, context)

# ---------------------------------------------------------------------------
# Realism
# ---------------------------------------------------------------------------

class BHS_PT_realism(_Sub, bpy.types.Panel):
    bl_label = "Realism (Physics)"
    bl_options = set()

    def draw_settings(self, layout, s, context):
        layout.use_property_split = False
        deviations = params.nonphysical(s)
        box = layout.box()
        if not deviations:
            box.label(text="Physically accurate", icon="CHECKMARK")

        else:
            box.label(text=f"{len(deviations)} setting(s) depart from real physics:", icon="INFO")
            col = box.column(align=True)
            for label, cur, phys in deviations[:8]:
                fmt = (lambda v: str(v)) if isinstance(phys, bool) else (lambda v: f"{v:.3g}")
                col.label(text=f"   {label}: {fmt(cur)}  (real: {fmt(phys)})")
            if len(deviations) > 8:
                col.label(text=f"   … and {len(deviations) - 8} more")
        box.operator("blackhole.make_physical", icon="LIGHT_SUN")

class BHS_PT_hole(_Sub, bpy.types.Panel):
    bl_label = "Black Hole"
    bl_parent_id = "BHS_PT_realism"
    bl_options = set()

    def draw_settings(self, layout, s, context):
        layout.prop(s, "mass_solar")
        layout.prop(s, "scene_scale")
        layout.prop(s, "spin")
        layout.prop(s, "rotation")
        layout.prop(s, "lensing_strength")
        layout.prop(s, "bounding_radius")
        layout.prop(s, "edge_fade", slider=True)
        layout.prop(s, "hovering_observer")

        col = layout.column(heading="Spin Approximations")
        col.prop(s, "shadow_shift")
        sub = col.column()
        sub.active = s.mode == "EXACT"
        sub.prop(s, "frame_drag")
        if s.mode == "EXACT":
            col = layout.column(heading="Integrator")
            col.prop(s, "steps_per_orbit")
            col.prop(s, "max_orbits")

class BHS_PT_disk(_Sub, bpy.types.Panel):
    bl_label = "Accretion Disk"
    bl_parent_id = "BHS_PT_realism"

    def draw_header(self, context):
        self.layout.prop(_root(context).blackhole, "disk_enable", text="")

    def draw_settings(self, layout, s, context):
        layout.active = s.disk_enable
        layout.prop(s, "disk_inner_auto")
        row = layout.row()
        row.active = not s.disk_inner_auto
        row.prop(s, "disk_inner")
        layout.prop(s, "disk_outer")
        _limit_warning(layout, s, "Disk")

        layout.prop(s, "peak_temp")
        layout.prop(s, "temp_exponent")
        layout.prop(s, "opacity")
        layout.separator()
        layout.prop(s, "disk_thickness", slider=True)
        row = layout.row()
        row.active = s.disk_thickness > 0.0
        row.prop(s, "disk_flare")
        if s.disk_thickness > 0.0:
            layout.prop(s, "volume_samples")

class BHS_PT_relativity(_Sub, bpy.types.Panel):
    bl_label = "Doppler & Redshift"
    bl_parent_id = "BHS_PT_realism"

    def draw_settings(self, layout, s, context):
        layout.prop(s, "doppler")
        layout.prop(s, "gravity_shift")
        layout.prop(s, "color_shift")
        layout.prop(s, "brightness_exponent")

# ---------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------

class BHS_PT_lighting(_Sub, bpy.types.Panel):
    bl_label = "Lighting & Intensity"

    def draw_settings(self, layout, s, context):
        layout.prop(s, "emission_strength", text="Disk Brightness")
        row = layout.row()
        row.active = s.jet_enable
        row.prop(s, "jet_intensity", text="Jet Brightness")
        layout.prop(s, "ring_glow", text="Ring Glow")
        row = layout.row()
        row.active = s.fog_enable
        row.prop(s, "fog_glow", text="Fog Glow")

        layout.prop(s, "bg_strength")
        row = layout.row()
        row.active = s.use_skybox
        row.prop(s, "sky_strength")
        layout.separator()
        layout.prop(s, "illuminate_scene")
        row = layout.row()
        row.active = s.illuminate_scene
        row.prop(s, "illumination_strength")
        layout.separator()
        layout.prop(context.scene.view_settings, "exposure", text="Camera Exposure")

# ---------------------------------------------------------------------------
# Artistic
# ---------------------------------------------------------------------------

class BHS_PT_artistic(_Sub, bpy.types.Panel):
    bl_label = "Artistic"

    def draw_settings(self, layout, s, context):
        layout.prop(s, "physical_mix", slider=True)
        col = layout.column(align=True)
        col.active = s.physical_mix < 1.0
        col.prop(s, "color_inner")
        col.prop(s, "color_mid")
        col.prop(s, "color_outer")
        layout.prop(s, "hue_shift", slider=True)
        layout.prop(s, "saturation", slider=True)

class BHS_PT_glow(_Sub, bpy.types.Panel):
    bl_label = "Glow"
    bl_parent_id = "BHS_PT_artistic"

    def draw_settings(self, layout, s, context):
        layout.prop(s, "ring_glow")
        layout.prop(s, "ring_glow_width")
        layout.prop(s, "ring_glow_color")
        layout.prop(s, "horizon_glow")
        layout.prop(s, "horizon_glow_color")
        layout.prop(s, "photon_ring_boost")
        layout.separator()
        layout.operator("blackhole.setup_compositor", icon="NODE_COMPOSITING")

class BHS_PT_density(_Sub, bpy.types.Panel):
    bl_label = "Disk Texture"
    bl_parent_id = "BHS_PT_artistic"

    def draw_header(self, context):
        self.layout.prop(_root(context).blackhole, "texture_enable", text="")

    def draw_settings(self, layout, s, context):
        layout.active = s.disk_enable and s.texture_enable
        layout.template_ID(s, "disk_texture", open="image.open")
        for p in ("texture_density", "texture_emission", "texture_contrast", "texture_scale",
                  "texture_rotation", "texture_motion"):
            layout.prop(s, p)
        sub = layout.column()
        sub.active = s.texture_motion != "STATIC"
        sub.prop(s, "texture_speed")

class BHS_PT_fog(_Sub, bpy.types.Panel):
    bl_label = "Fog & Dust"
    bl_parent_id = "BHS_PT_artistic"

    def draw_header(self, context):
        self.layout.prop(_root(context).blackhole, "fog_enable", text="")

    def draw_settings(self, layout, s, context):
        layout.active = s.fog_enable
        col = layout.column(align=True)
        col.prop(s, "fog_density")
        col.prop(s, "fog_glow")
        col.prop(s, "fog_absorption")
        layout.separator()
        layout.prop(s, "fog_match_disk")
        row = layout.row()
        row.active = not s.fog_match_disk
        row.prop(s, "fog_height")
        layout.prop(s, "fog_halo", slider=True)
        layout.prop(s, "halo_falloff")

        col = layout.column(align=True)
        col.prop(s, "fog_inner")
        col.prop(s, "fog_outer")
        col.prop(s, "fog_falloff")
        _limit_warning(layout, s, "Fog")
        layout.separator()
        col = layout.column(align=True)
        col.prop(s, "fog_noise", slider=True)
        col.prop(s, "fog_noise_scale")
        col.prop(s, "fog_noise_detail")
        layout.prop(s, "fog_swirl")
        layout.prop(s, "fog_color")
        layout.prop(s, "fog_color_mix", slider=True)
        layout.separator()
        layout.prop(s, "volume_samples")
        layout.label(text="Volume rendering: ~3-9x render time (see Quality)", icon="INFO")

class BHS_PT_turbulence(_Sub, bpy.types.Panel):
    bl_label = "Turbulence & Edges"
    bl_parent_id = "BHS_PT_artistic"

    def draw_settings(self, layout, s, context):
        layout.active = s.disk_enable
        for p in ("noise_contrast", "noise_scale", "noise_detail", "noise_roughness", "shear",
                  "noise_speed", "dust_lanes", "dust_scale", "inner_softness", "outer_softness"):
            layout.prop(s, p)


class BHS_PT_jets(_Sub, bpy.types.Panel):
    bl_label = "Relativistic Jets"
    bl_parent_id = "BHS_PT_artistic"

    def draw_header(self, context):
        self.layout.prop(_root(context).blackhole, "jet_enable", text="")

    def draw_settings(self, layout, s, context):
        layout.active = s.jet_enable
        for p in ("jet_length", "jet_angle", "jet_base", "jet_intensity", "jet_noise", "jet_color"):
            layout.prop(s, p)

# ---------------------------------------------------------------------------
# Sky, camera
# ---------------------------------------------------------------------------

class BHS_PT_sky(_Sub, bpy.types.Panel):
    bl_label = "Skybox & Background"

    def draw_header(self, context):
        self.layout.prop(_root(context).blackhole, "use_skybox", text="")

    def draw_settings(self, layout, s, context):
        col = layout.column()
        col.active = s.use_skybox
        col.template_ID(s, "sky_image", open="image.open")
        col.prop(s, "sky_strength")
        col.prop(s, "sky_rotation")
        col.prop(s, "star_overlay", slider=True)
        layout.prop(s, "bg_strength")

        box = layout.column(heading="Procedural Stars")
        box.active = not s.use_skybox or s.star_overlay > 0.0
        for p in ("star_density", "star_brightness", "star_size", "nebula_strength",
                  "nebula_color"):
            box.prop(s, p)
        layout.separator()
        layout.prop(s, "lens_objects")
        layout.prop(s, "drive_world")
        layout.operator("blackhole.setup_world", icon="WORLD")

class BHS_PT_camera(_Sub, bpy.types.Panel):
    bl_label = "Camera & Render"

    def draw_settings(self, layout, s, context):
        layout.prop(s, "view_inclination")
        layout.prop(s, "view_distance")
        layout.operator("blackhole.frame_camera", icon="CAMERA_DATA")
        layout.separator()
        layout.prop(context.scene.view_settings, "exposure")
        layout.prop(context.scene.view_settings, "look")
        row = layout.row(align=True)
        row.operator("blackhole.configure_render", text="Preview").quality = "PREVIEW"
        row.operator("blackhole.configure_render", text="Final").quality = "FINAL"

classes = (BHS_PT_main,
           BHS_PT_realism, BHS_PT_hole, BHS_PT_disk, BHS_PT_relativity,
           BHS_PT_lighting,
           BHS_PT_artistic, BHS_PT_glow, BHS_PT_density, BHS_PT_fog, BHS_PT_turbulence,
           BHS_PT_jets,
           BHS_PT_sky, BHS_PT_camera)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)