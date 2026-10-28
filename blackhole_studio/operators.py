"""Operators: add/remove black holes, presets, render, world and compositor setup."""

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import builder, presets

def _active_root(context):
    return builder.find_root(context.active_object)

class _NeedsBlackHole:
    @classmethod
    def poll(cls, context):
        return _active_root(context) is not None

class BHS_OT_add(bpy.types.Operator):
    """Add a physically based black hole at the 3D cursor"""
    bl_idname = "blackhole.add"
    bl_label = "Add Black Hole"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(name="Render Mode", items=[("FAST", "Fast (GPU / Eevee)", ""),
                                                  ("EXACT", "Exact (Cycles OSL)", "")],
                       default="FAST")
    setup_world: BoolProperty(name="Starfield World", default=True,
                              description="Use a matching lensed starfield World (your current World is kept)")
    setup_camera: BoolProperty(name="Frame Camera", default=True,
                               description="Point the scene camera at the black hole")
    setup_render: BoolProperty(name="Configure Render", default=True,
                               description="Switch to Cycles and set suitable render settings")

    def execute(self, context):
        root = builder.create(context)
        if self.mode != root.blackhole.mode:
            root.blackhole.mode = self.mode
        if self.setup_world:
            builder.setup_world(context.scene, root)
        if self.setup_camera:
            builder.frame_camera(context, root)
        if self.setup_render:
            configure_render(context.scene, root, "PREVIEW")

        for ob in context.selected_objects:
            ob.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        return {"FINISHED"}

class BHS_OT_rebuild(_NeedsBlackHole, bpy.types.Operator):
    """Regenerate the shader and lookup table"""
    bl_idname = "blackhole.rebuild"
    bl_label = "Rebuild Shader"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        builder.rebuild(_active_root(context))
        return {"FINISHED"}

class BHS_OT_remove(_NeedsBlackHole, bpy.types.Operator):
    """Delete the black hole, its lens sphere, material and lookup table"""
    bl_idname = "blackhole.remove"
    bl_label = "Remove Black Hole"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        builder.remove(_active_root(context))
        return {"FINISHED"}

class BHS_OT_preset(_NeedsBlackHole, bpy.types.Operator):
    """Apply a look preset (camera-independent settings are reset first)"""
    bl_idname = "blackhole.preset"
    bl_label = "Apply Preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=presets.items())

    def execute(self, context):
        presets.apply(_active_root(context).blackhole, self.preset)
        return {"FINISHED"}

class BHS_OT_make_physical(_NeedsBlackHole, bpy.types.Operator):
    """Reset every setting that departs from real physics to its physical value
    (structure, texture, fog, sky and camera are kept)"""
    bl_idname = "blackhole.make_physical"
    bl_label = "Make Physically Accurate"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import params
        root = _active_root(context)
        s = root.blackhole
        root["_bhs_suspend"] = True
        try:
            for pid, (value, _) in params.PHYSICAL_VALUES.items():
                setattr(s, pid, value)
            for pid, (limit, _) in params.PHYSICAL_LIMITS.items():
                setattr(s, pid, min(getattr(s, pid), limit))
        finally:
            del root["_bhs_suspend"]
        builder.apply(root)
        return {"FINISHED"}

class BHS_OT_frame_camera(_NeedsBlackHole, bpy.types.Operator):
    """Move the scene camera to the chosen inclination and distance"""
    bl_idname = "blackhole.frame_camera"
    bl_label = "Frame Camera"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        builder.frame_camera(context, _active_root(context))
        return {"FINISHED"}

class BHS_OT_setup_world(_NeedsBlackHole, bpy.types.Operator):
    """Switch the scene to a World with the skybox/starfield that the black hole lenses (the current World is kept)"""
    bl_idname = "blackhole.setup_world"
    bl_label = "Use Matching World"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        builder.setup_world(context.scene, _active_root(context))
        return {"FINISHED"}

def configure_render(scene, root, quality):
    s = root.blackhole
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    if s.mode == "EXACT":
        cy.device = "CPU"
        cy.shading_system = True
    else:
        cy.shading_system = False

    final = quality == "FINAL"
    cy.samples = 256 if final else 32
    cy.preview_samples = 32 if final else 8
    cy.use_denoising = True
    cy.max_bounces = max(cy.max_bounces, 12)
    cy.transmission_bounces = max(cy.transmission_bounces, 8)
    cy.transparent_max_bounces = max(cy.transparent_max_bounces, 16)

    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Punchy" if not final else "None"
    except TypeError:
        scene.view_settings.view_transform = "Filmic"

class BHS_OT_configure_render(_NeedsBlackHole, bpy.types.Operator):
    """Set Cycles, OSL/device, bounces and color management for this black hole's mode"""
    bl_idname = "blackhole.configure_render"
    bl_label = "Configure Render Settings"
    bl_options = {"REGISTER", "UNDO"}

    quality: EnumProperty(name="Quality", items=[("PREVIEW", "Preview", "Fast look-dev"),
                                                 ("FINAL", "Final", "High sample count")])

    def execute(self, context):
        configure_render(context.scene, _active_root(context), self.quality)
        if _active_root(context).blackhole.mode == "EXACT":
            self.report({"INFO"}, "Cycles CPU + Open Shading Language enabled (required for Exact mode)")
        return {"FINISHED"}

class BHS_OT_setup_compositor(bpy.types.Operator):
    """Add bloom/glare and chromatic aberration in the compositor"""
    bl_idname = "blackhole.setup_compositor"
    bl_label = "Add Glow & Lens Effects"
    bl_options = {"REGISTER", "UNDO"}

    strength: bpy.props.FloatProperty(name="Glow Strength", default=1.0, min=0.0, max=2.0,
                                      description="0 = no glow, 1 = default, 2 = only glow")
    threshold: bpy.props.FloatProperty(name="Threshold", default=0.8, min=0.0, max=100.0)
    size: bpy.props.FloatProperty(name="Size", default=0.7, min=0.0, max=1.0,
                                  description="Spread of the glow")
    dispersion: bpy.props.FloatProperty(name="Chromatic Aberration", default=0.01, min=0.0, max=1.0)

    def execute(self, context):
        if bpy.app.version >= (5, 0, 0):
            self._setup_node_group(context.scene)
        else:
            self._setup_scene_tree(context.scene)
        return {"FINISHED"}

    def _setup_node_group(self, scene):
        """Blender 5.x: the compositor is a node group assigned to the scene."""
        tree = scene.compositing_node_group
        if tree is None:
            tree = bpy.data.node_groups.new("BHS Compositing", "CompositorNodeTree")
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            scene.compositing_node_group = tree
        nodes, links = tree.nodes, tree.links
        for n in [n for n in nodes if n.name.startswith("BHS ")]:
            nodes.remove(n)

        rl = next((n for n in nodes if n.bl_idname == "CompositorNodeRLayers"), None) \
            or nodes.new("CompositorNodeRLayers")
        out = next((n for n in nodes if n.bl_idname == "NodeGroupOutput"), None) \
            or nodes.new("NodeGroupOutput")
        if not out.inputs or out.inputs[0].type == "CUSTOM":
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")

        glare = nodes.new("CompositorNodeGlare")
        glare.name = "BHS Glare"
        glare.inputs["Type"].default_value = "Fog Glow"
        glare.inputs["Quality"].default_value = "High"
        glare.inputs["Threshold"].default_value = self.threshold
        glare.inputs["Strength"].default_value = self.strength
        glare.inputs["Size"].default_value = self.size

        lens = nodes.new("CompositorNodeLensdist")
        lens.name = "BHS Lens"
        lens.inputs["Fit"].default_value = True
        lens.inputs["Dispersion"].default_value = self.dispersion
        glare.location = (rl.location.x + 300, rl.location.y)
        lens.location = (rl.location.x + 520, rl.location.y)
        out.location = (rl.location.x + 760, rl.location.y)
        links.new(rl.outputs["Image"], glare.inputs["Image"])
        links.new(glare.outputs["Image"], lens.inputs["Image"])
        links.new(lens.outputs["Image"], out.inputs[0])

    def _setup_scene_tree(self, scene):
        """Blender 4.x: the compositor is the scene's own node tree."""
        scene.use_nodes = True
        tree = scene.node_tree
        nodes, links = tree.nodes, tree.links
        for n in [n for n in nodes if n.name.startswith("BHS ")]:
            nodes.remove(n)
        rl = next((n for n in nodes if n.bl_idname == "CompositorNodeRLayers"), None) \
            or nodes.new("CompositorNodeRLayers")
        comp = next((n for n in nodes if n.bl_idname == "CompositorNodeComposite"), None) \
            or nodes.new("CompositorNodeComposite")

        glare = nodes.new("CompositorNodeGlare")
        glare.name = "BHS Glare"
        glare.glare_type = "FOG_GLOW"
        glare.quality = "HIGH"
        glare.mix = self.strength - 1.0
        glare.threshold = self.threshold
        glare.size = int(round(6 + 3 * self.size))

        lens = nodes.new("CompositorNodeLensdist")
        lens.name = "BHS Lens"
        lens.use_fit = True
        lens.inputs["Dispersion"].default_value = self.dispersion
        glare.location = (rl.location.x + 300, rl.location.y)
        lens.location = (rl.location.x + 520, rl.location.y)
        comp.location = (rl.location.x + 760, rl.location.y)
        links.new(rl.outputs["Image"], glare.inputs["Image"])
        links.new(glare.outputs["Image"], lens.inputs["Image"])
        links.new(lens.outputs["Image"], comp.inputs["Image"])

classes = (BHS_OT_add, BHS_OT_rebuild, BHS_OT_remove, BHS_OT_preset, BHS_OT_make_physical,
           BHS_OT_frame_camera,
           BHS_OT_setup_world, BHS_OT_configure_render, BHS_OT_setup_compositor)


def menu_func(self, context):
    self.layout.operator(BHS_OT_add.bl_idname, text="Black Hole", icon="SHADING_SOLID")

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_add.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(menu_func)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)