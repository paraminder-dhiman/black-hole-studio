"""Create, rebuild and update black hole objects.

Hierarchy (root carries the settings, so it can be moved, scaled, animated):

    BlackHole            Empty, ``obj.blackhole`` settings
    ├── BlackHole.Lens       unit sphere scaled to the influence radius, lens material
    ├── BlackHole.DiskLight  camera-invisible emitter so the disk lights the scene
    └── BlackHole.Horizon    viewport-only empty marking the event horizon"""

import os

import bmesh
import bpy

from . import params, physics
from .nodes import materials

LENS_SUFFIX = ".Lens"
HORIZON_SUFFIX = ".Horizon"
LIGHT_SUFFIX = ".DiskLight"
WORLD_TAG = "bhs_managed"

def find_root(obj):
    while obj is not None:
        if getattr(obj, "blackhole", None) is not None and obj.blackhole.is_blackhole:
            return obj
        obj = obj.parent
    return None

def _child(root, suffix):
    for ch in root.children:
        if ch.get("bhs_role") == suffix:
            return ch
    return None

def _sphere_mesh(name):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=128, v_segments=64, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = True
    return mesh

def create(context, name="BlackHole", location=None):
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "SPHERE"
    root.empty_display_size = 1.0
    context.collection.objects.link(root)
    root.location = location if location is not None else context.scene.cursor.location
    root.blackhole.is_blackhole = True

    lens = bpy.data.objects.new(root.name + LENS_SUFFIX, _sphere_mesh(root.name + LENS_SUFFIX))
    lens["bhs_role"] = LENS_SUFFIX
    lens.parent = root
    # Drawn as a (transparent) surface so every engine renders it; the
    # wireframe overlay is the "Show Influence Sphere" option
    lens.display_type = "TEXTURED"
    lens.hide_select = False
    lens.visible_shadow = False
    lens.visible_diffuse = False
    lens.visible_volume_scatter = False
    context.collection.objects.link(lens)

    horizon = bpy.data.objects.new(root.name + HORIZON_SUFFIX, None)
    horizon["bhs_role"] = HORIZON_SUFFIX
    horizon.empty_display_type = "SPHERE"
    horizon.parent = root
    horizon.hide_render = True
    horizon.hide_select = True
    context.collection.objects.link(horizon)

    rebuild(root)
    return root

def _material(root):
    lens = _child(root, LENS_SUFFIX)
    if lens is None:
        return None, None
    mat = lens.active_material
    if mat is None:
        mat = bpy.data.materials.new("BHS " + root.name)
        lens.data.materials.append(mat)
    return lens, mat

def _lut_name(root):
    return "BHS LUT " + root.name

TEXTURE_PATH = os.path.join(os.path.dirname(__file__), "textures", "accretion_disk.png")
SKY_PATH = os.path.join(os.path.dirname(__file__), "textures", "milky_way_sky.exr")

LUT_VERSION = 1      # Table layout (lut.py)
VERSION = 1          # Bumped when the shader layout changes; older files are rebuilt

def disk_group(root):
    """This black hole's disk-shading node group (holds the disk texture)."""
    return bpy.data.node_groups.get(root.get("_bhs_disk_group", ""))

def _groups(root):
    """All node groups generated for this black hole (disk, fog, step, point)."""
    names = root.get("_bhs_groups", [])
    return [bpy.data.node_groups[n] for n in names if n in bpy.data.node_groups]

def _bundled_image(path, colorspace=None):
    """Load a bundled image once and pack it, so .blend files work without the extension."""
    img = bpy.data.images.load(path, check_existing=True)
    if colorspace is not None and img.colorspace_settings.name != colorspace:
        img.colorspace_settings.name = colorspace
    if img.packed_file is None:
        img.pack()
    return img

def default_texture():
    """The bundled accretion-disk density texture."""
    return _bundled_image(TEXTURE_PATH, "Non-Color")

def default_sky():
    """The bundled Milky Way skybox (NASA SVS Deep Star Maps 2020; linear HDR, equirectangular)."""
    return _bundled_image(SKY_PATH)

def _annulus_mesh(mesh, r_in, r_out, segments=128, rings=12):
    """Flat disk-plane ring in r_s units (the object scale converts to scene units)."""
    import math
    bm = bmesh.new()
    grid = []
    for j in range(rings + 1):
        r = r_in * (r_out / r_in) ** (j / rings)
        grid.append([bm.verts.new((r * math.cos(2 * math.pi * i / segments),
                                   r * math.sin(2 * math.pi * i / segments), 0.0))
                     for i in range(segments)])

    for j in range(rings):
        for i in range(segments):
            k = (i + 1) % segments
            bm.faces.new((grid[j][i], grid[j][k], grid[j + 1][k], grid[j + 1][i]))
    bm.to_mesh(mesh)
    bm.free()

def _ensure_disk_light(root):
    """Create/update the invisible emitter that lets the disk light nearby objects."""
    s = root.blackhole
    d = params.derived(s)
    ob = _child(root, LIGHT_SUFFIX)
    if ob is None:
        mesh = bpy.data.meshes.new(root.name + LIGHT_SUFFIX)
        ob = bpy.data.objects.new(root.name + LIGHT_SUFFIX, mesh)
        ob["bhs_role"] = LIGHT_SUFFIX
        ob.parent = root
        ob.hide_select = True
        ob.display_type = "BOUNDS"
        ob.display_bounds_type = "CYLINDER"
        for coll in root.users_collection:
            coll.objects.link(ob)
        # Lights the scene without being seen or casting shadows; the lens
        # sphere draws the visible (lensed) disk
        ob.visible_camera = False
        ob.visible_shadow = False
        ob.visible_transmission = False
        ob.visible_volume_scatter = False
        mat = bpy.data.materials.new("BHS Light " + root.name)
        materials.build_illumination(mat)
        mesh.materials.append(mat)
    r_out = d["r_out"] * (1.0 + s.outer_softness)
    key = f"{d['r_in']:.5g}|{r_out:.5g}"
    if ob.get("_bhs_mesh_key") != key:
        _annulus_mesh(ob.data, d["r_in"], r_out)
        ob["_bhs_mesh_key"] = key
    ob.scale = (s.scene_scale,) * 3
    ob.hide_render = not (s.illuminate_scene and s.disk_enable)
    ob.hide_viewport = ob.hide_render or not s.show_helpers
    return ob

def _drive_time(tree, data_path):
    if tree.animation_data:
        for fc in list(tree.animation_data.drivers):
            if fc.data_path == data_path:
                tree.animation_data.drivers.remove(fc)
    fc = tree.driver_add(data_path)
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame"

def rebuild(root):
    """(Re)generate the lens material for the current mode, then apply values."""
    lens, mat = _material(root)
    if mat is None:
        return
    s = root.blackhole
    root.pop("_bhs_lut_key", None)
    for old in _groups(root):
        bpy.data.node_groups.remove(old)
    _, samples, thick = params.structure(s)
    dgroup = materials.build_disk_group(root.name)
    groups = [dgroup]
    step = point = None
    lut_img = _ensure_lut(root, force=True) if s.mode == "FAST" else None
    if samples:
        fgroup = materials.build_fog_group(root.name)
        step = materials.build_step_group(root.name, dgroup, fgroup, thick)
        groups += [fgroup, step]
        if s.mode == "FAST":
            point = materials.build_point_group(root.name, lut_img)
            groups.append(point)
    root["_bhs_disk_group"] = dgroup.name
    root["_bhs_groups"] = [grp.name for grp in groups]
    if s.disk_texture is None and os.path.exists(TEXTURE_PATH):
        root["_bhs_suspend"] = True
        try:
            s.disk_texture = default_texture()
        finally:
            del root["_bhs_suspend"]
    if s.sky_image is None and os.path.exists(SKY_PATH):
        root["_bhs_suspend"] = True
        try:
            s.sky_image = default_sky()
        finally:
            del root["_bhs_suspend"]
    if s.mode == "EXACT":
        materials.build_exact(mat, dgroup, step, samples, thick)
    else:
        materials.build_fast(mat, lut_img, dgroup, step, point, samples, thick)
    # Invisible in Solid shading (Workbench uses the viewport colour), rendered normally
    mat.diffuse_color = (0.0, 0.0, 0.0, 0.0)
    _drive_time(mat.node_tree, 'nodes["Time"].outputs[0].default_value')
    root["_bhs_struct"] = repr(params.structure(s))
    root["_bhs_version"] = VERSION
    apply(root)
    for old in old_groups_unused():
        bpy.data.node_groups.remove(old)

def old_groups_unused():
    """Disk/fog/step/point groups no longer used by any material."""
    prefixes = (materials.DISK_GROUP_PREFIX, materials.FOG_GROUP_PREFIX, materials.STEP_GROUP_PREFIX,
                materials.POINT_GROUP_PREFIX)
    return [grp for grp in bpy.data.node_groups
            if grp.name.startswith(prefixes) and grp.users == 0]

def _ensure_lut(root, force=False):
    s = root.blackhole
    key = f"{s.bounding_radius:.6g}|{s.lensing_strength:.6g}|{LUT_VERSION}"
    img = bpy.data.images.get(_lut_name(root))
    if force or img is None or root.get("_bhs_lut_key") != key:
        img = materials.make_lut_image(_lut_name(root), s.bounding_radius, s.lensing_strength)
        root["_bhs_lut_key"] = key
    return img

def _set_node_tree_values(tree, values):
    nodes = tree.nodes
    for name, val in values.items():
        node = nodes.get(name)
        if node is None:
            continue
        if node.bl_idname == "ShaderNodeRGB":
            node.outputs[0].default_value = tuple(val[:3]) + (1.0,)
        elif node.bl_idname == "ShaderNodeValue":
            node.outputs[0].default_value = float(val)

def _set_env_image(tree, image):
    env = tree.nodes.get("Environment")
    if env is not None and env.image != image:
        env.image = image

def apply(root):
    """Push settings into objects and shader inputs (no rebuild)."""
    s = root.blackhole
    lens, mat = _material(root)
    if lens is None or mat is None or not mat.node_tree:
        return
    if root.get("_bhs_struct") != repr(params.structure(s)) or \
            root.get("_bhs_version", 0) != VERSION:
        rebuild(root)          # Fog/thickness/quality switched: the graph changes
        return

    scale = s.scene_scale
    lens.scale = (s.bounding_radius * scale,) * 3
    lens.show_wire = s.show_influence_sphere
    horizon = _child(root, HORIZON_SUFFIX)
    if horizon is not None:
        horizon.empty_display_size = physics.horizon_radius(s.spin) * scale
        horizon.hide_viewport = not s.show_helpers
    if s.show_helpers:
        root.empty_display_type = "SPHERE"
        root.empty_display_size = physics.PHOTON_SPHERE * scale
    else:
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 0.5 * scale
    light = _ensure_disk_light(root)

    values = params.shader_values(s)
    if s.mode == "FAST":
        img = _ensure_lut(root)
        lut_nodes = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"]
        lut_nodes += [grp.nodes["LUT"] for grp in _groups(root) if "LUT" in grp.nodes]
        for node in lut_nodes:
            if node.image != img:
                node.image = img

    _set_node_tree_values(mat.node_tree, values)
    if light.active_material is not None:
        _set_node_tree_values(light.active_material.node_tree, values)
    for group in _groups(root):
        _set_node_tree_values(group, values)
    tex = disk_group(root).nodes.get("DiskTexture") if disk_group(root) else None
    if tex is not None and tex.image != s.disk_texture:
        tex.image = s.disk_texture
    sky = s.sky_image if s.use_skybox else None
    _set_env_image(mat.node_tree, sky)

    world = bpy.context.scene.world if bpy.context.scene else None
    if s.drive_world and world is not None and world.get(WORLD_TAG):
        _set_node_tree_values(world.node_tree, {k: values[k] for k in params.WORLD_KEYS})
        _set_env_image(world.node_tree, sky)

def setup_world(scene, root):
    world = scene.world
    if world is None or not world.get(WORLD_TAG):
        if world is not None:
            world.use_fake_user = True      # keep the user's own World in the file
        world = bpy.data.worlds.new("BHS Sky")
        scene.world = world
    materials.build_world(world)
    world[WORLD_TAG] = True
    apply(root)
    return world

def remove(root):
    lut = bpy.data.images.get(_lut_name(root))
    for ch in list(root.children):
        mesh = ch.data if ch.type == "MESH" else None
        mats = list(mesh.materials) if mesh else []
        bpy.data.objects.remove(ch)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for m in mats:
            if m is not None and m.users == 0:
                bpy.data.materials.remove(m)

    if lut is not None:
        bpy.data.images.remove(lut)
    groups = _groups(root)
    bpy.data.objects.remove(root)
    for group in groups:
        if group.users == 0:
            bpy.data.node_groups.remove(group)

def frame_camera(context, root):
    """Place (or create) the scene camera at the chosen inclination/distance."""
    import math
    from mathutils import Matrix, Vector

    s = root.blackhole
    scene = context.scene
    cam = scene.camera
    if cam is None:
        cam = bpy.data.objects.new("BHS Camera", bpy.data.cameras.new("BHS Camera"))
        context.collection.objects.link(cam)
        scene.camera = cam

    rot = root.matrix_world.to_3x3().normalized()
    center = root.matrix_world.to_translation()
    dist = s.view_distance * s.scene_scale * max(root.matrix_world.to_scale())
    inc = s.view_inclination
    loc = center + rot @ (Vector((0.0, -math.sin(inc), math.cos(inc))) * dist)

    forward = (center - loc).normalized()
    up = rot @ Vector((0.0, 0.0, 1.0))
    right = forward.cross(up)
    if right.length < 1e-6:
        right = rot @ Vector((1.0, 0.0, 0.0))
    right.normalize()
    cam_up = right.cross(forward)

    basis = Matrix((right, cam_up, -forward)).transposed()
    cam.matrix_world = Matrix.Translation(loc) @ basis.to_4x4()
    cam.data.clip_start = min(cam.data.clip_start, dist * 0.01)
    cam.data.clip_end = max(cam.data.clip_end, dist * 10.0)
    return cam