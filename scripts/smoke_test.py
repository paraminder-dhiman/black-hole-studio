"""End-to-end smoke test for Black Hole Studio.

Run inside Blender:   blender -b --python scripts/smoke_test.py -- OUT_DIR
or with the bpy module: python scripts/smoke_test.py OUT_DIR

Registers the extension, exercises every operator and preset, renders small
images in both modes and checks that the shadow is black and the disk is lit.
"""

import math
import os
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import blackhole_studio  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OUT = argv[0] if argv else os.path.join(os.getcwd(), "smoke_renders")
RES = int(os.environ.get("RES", 160))
SPP = int(os.environ.get("SPP", 4))
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
blackhole_studio.register()
failures = []

def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)

def render(name):
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.cycles.samples = SPP
    scene.cycles.device = "CPU"
    scene.render.filepath = os.path.join(OUT, name + ".png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[..., :3]
    bpy.data.images.remove(img)
    return px

def clear():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)

def active_root():
    return blackhole_studio.builder.find_root(bpy.context.view_layer.objects.active)

for mode in ("FAST", "EXACT"):
    clear()
    bpy.ops.blackhole.add(mode=mode)
    root = active_root()
    check(root is not None and root.blackhole.mode == mode, f"{mode}: add operator")
    s = root.blackhole
    s.view_distance = 45.0
    bpy.ops.blackhole.frame_camera()
    px = render(f"{mode.lower()}_default")
    h, w, _ = px.shape
    # Blender pixel rows start at the bottom; the shadow sits just above the image centre
    centre = px[int(h * 0.52):int(h * 0.6), w // 2 - 2:w // 2 + 2].mean()
    check(centre < 0.02, f"{mode}: shadow is black ({centre:.4f})")
    check(px.max() > 0.5, f"{mode}: disk is bright ({px.max():.3f})")

    for key in blackhole_studio.presets.PRESETS:
        bpy.ops.blackhole.preset(preset=key)
        bpy.ops.blackhole.frame_camera()
        px = render(f"{mode.lower()}_{key.lower()}")
        check(np.isfinite(px).all() and px.max() > 0.05, f"{mode}: preset {key} renders")

    # Live updates must not rebuild, structural ones must
    nodes_before = len(blackhole_studio.builder._child(root, ".Lens").active_material.node_tree.nodes)
    s.spin = 0.7
    s.peak_temp = 12000.0
    check(len(blackhole_studio.builder._child(root, ".Lens").active_material.node_tree.nodes) == nodes_before,
          f"{mode}: live parameter update keeps the node tree")

# Disk Texture, Viewport helpers
clear()
bpy.ops.blackhole.add(mode="FAST")
root = active_root()
s = root.blackhole
check(s.disk_texture is not None and s.disk_texture.packed_file is not None,
      "bundled disk texture is assigned and packed")
dgroup = blackhole_studio.builder.disk_group(root)
check(dgroup is not None and dgroup.nodes["DiskTexture"].image == s.disk_texture,
      "texture is wired into the disk group")
lens = blackhole_studio.builder._child(root, ".Lens")
check(lens.display_type == "TEXTURED" and not lens.show_wire and lens.active_material.diffuse_color[3] == 0.0,
      "influence sphere hidden in the viewport by default")

s.show_influence_sphere = True
check(lens.show_wire, "influence sphere wireframe can be shown")
s.show_helpers = False
horizon = blackhole_studio.builder._child(root, ".Horizon")
light = blackhole_studio.builder._child(root, ".DiskLight")
check(horizon.hide_viewport and light.hide_viewport and not light.hide_render
      and root.empty_display_type == "PLAIN_AXES", "helpers can be hidden (disk light still renders)")
s.show_helpers = True
s.show_influence_sphere = False

# Disk thickness and fog
s.use_skybox = False
s.star_brightness = 0.0
s.nebula_strength = 0.0
s.view_inclination = math.radians(88.0)
s.view_distance = 45.0
bpy.ops.blackhole.frame_camera()

def vertical_extent(px, thresh=0.08):
    """Rows (fraction of the image) in the left disk wing brighter than thresh."""
    h, w, _ = px.shape
    band = px[:, int(w * 0.12):int(w * 0.28)].mean(axis=(1, 2))
    return float((band > thresh).sum()) / h

def timed_render(name):
    t0 = time.time()
    px = render(name)
    return px, time.time() - t0

thin, t_thin = timed_render("thin_edge_on")
s.disk_thickness = 0.1
thick, t_thick = timed_render("thick_edge_on")
check(vertical_extent(thick) > 2.0 * vertical_extent(thin),
      f"thickness makes the edge-on disk taller ({vertical_extent(thick):.3f} vs {vertical_extent(thin):.3f})")
s.disk_thickness = 0.0
back = render("thin_again")
check(float(np.abs(back - thin).mean()) < 0.005, "thickness 0 restores the thin disk exactly")

s.view_inclination = math.radians(75.0)
bpy.ops.blackhole.frame_camera()
nofog, t_nofog = timed_render("fog_off")
s.fog_enable = True
s.fog_glow = 0.0
s.fog_absorption = 4.0
s.fog_density = 0.1
dusty, t_fog = timed_render("fog_dust")
check(dusty.mean() < 0.8 * nofog.mean(), f"dust absorption darkens the disk ({dusty.mean():.3f} vs {nofog.mean():.3f})")

s.disk_enable = False
s.fog_absorption = 0.3
s.fog_glow = 3.0
s.fog_halo = 0.0
glow_disk = render("fog_glow_disk")
check(glow_disk.mean() > 0.01, f"fog glow adds light ({glow_disk.mean():.4f})")
s.fog_halo = 1.0
glow_halo = render("fog_glow_halo")
h = glow_halo.shape[0]
top_rows = slice(int(h * 0.75), int(h * 0.9))       # pixel rows start at the bottom
check(glow_halo[top_rows].mean() > 1.5 * glow_disk[top_rows].mean(),
      "halo fog fills the space above the disk")
print(f"TIMING thin {t_nofog:.1f}s, fog {t_fog:.1f}s ({t_fog / t_nofog:.1f}x), "
      f"thick disk {t_thick:.1f}s ({t_thick / t_thin:.1f}x)")

# Parity with smooth fog and a thick disk
s.disk_enable = True
s.fog_halo = 0.3
s.fog_noise = 0.0
s.fog_absorption = 1.0
s.fog_glow = 1.0
s.fog_density = 0.05
s.disk_thickness = 0.05
fast_px = render("volume_fast")
s.mode = "EXACT"
bpy.ops.blackhole.configure_render(quality="PREVIEW")      # Exact mode needs Cycles + OSL
exact_px = render("volume_exact")
diff = float(np.abs(exact_px - fast_px).mean())
check(diff < 0.02, f"Exact and Fast agree with fog and a thick disk (mean diff {diff:.4f})")

# A black hole with a different version stamp rebuilds on its next update
root["_bhs_version"] = 0
s.peak_temp = s.peak_temp + 1.0
check(root.get("_bhs_version") == blackhole_studio.builder.VERSION,
      "an out-of-date black hole rebuilds when a setting changes")

blend = os.path.join(OUT, "reload_test.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
bpy.ops.wm.open_mainfile(filepath=blend)
root = blackhole_studio.builder.find_root(bpy.data.objects["BlackHole"])
bpy.context.view_layer.objects.active = root
check(root.blackhole.disk_texture is not None and root.blackhole.disk_texture.packed_file is not None
      and root.blackhole.fog_enable and abs(root.blackhole.disk_thickness - 0.05) < 1e-6,
      "texture packed and settings kept after save and reload")

# Skybox, lighting and realism controls
clear()
bpy.ops.blackhole.add(mode="FAST")
root = active_root()
s = root.blackhole
check(s.use_skybox and s.sky_image is not None and s.sky_image.packed_file is not None,
      "bundled Milky Way skybox is on by default and packed")                           # changed
env_nodes = [n for n in bpy.context.scene.world.node_tree.nodes if n.name == "Environment"]
check(env_nodes and env_nodes[0].image == s.sky_image, "world uses the same skybox")

s.view_distance = 45.0
bpy.ops.blackhole.frame_camera()
sky_on = render("skybox_on")
s.use_skybox = False
sky_off = render("skybox_off")
check(np.abs(sky_on - sky_off).mean() > 0.02, "skybox checkbox switches the background")
s.use_skybox = True
custom = bpy.data.images.new("CustomSky", 64, 32, float_buffer=True)
custom.pixels.foreach_set(np.tile(np.array([0.0, 0.0, 4.0, 1.0], np.float32), 64 * 32))
s.sky_image = custom
blue = render("skybox_custom")
tint = lambda px: float((px[..., 2] - px[..., 0]).mean())          # blue minus red
check(tint(blue) > tint(sky_on) + 0.05, f"swapping the skybox image takes effect "
      f"({tint(blue):.3f} vs {tint(sky_on):.3f})")
s.sky_image = blackhole_studio.builder.default_sky()

light = blackhole_studio.builder._child(root, ".DiskLight")
check(light is not None and not light.hide_render and not light.visible_camera,
      "disk light exists and is invisible to the camera")
s.illuminate_scene = False
check(light.hide_render, "scene illumination can be switched off")
s.illuminate_scene = True

s.doppler = 0.0
s.ring_glow = 2.0
check(len(blackhole_studio.params.nonphysical(s)) == 2, "realism panel lists departures from physics")
bpy.ops.blackhole.make_physical()
check(not blackhole_studio.params.nonphysical(s), "make physically accurate resets them")

bpy.ops.blackhole.setup_compositor()
comp_tree = (bpy.context.scene.compositing_node_group if bpy.app.version >= (5, 0, 0)
             else bpy.context.scene.node_tree)
check(comp_tree is not None and comp_tree.nodes.get("BHS Glare") is not None, "compositor setup")
bpy.ops.blackhole.configure_render(quality="FINAL")
check(bpy.context.scene.cycles.samples == 256, "configure render")

# Seamless sphere edge, Camera inside the sphere, Hovering observer
def edge_ratio(px, dist, bound):
    """Brightness gradient on the sphere's outline relative to just around it (~1: no rim)."""
    im = px.mean(2)
    h, w = im.shape
    gy, gx = np.gradient(im)
    grad = np.hypot(gx, gy)
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - w / 2 + 0.5, yy - h / 2 + 0.5)
    focal = w / 2 / math.tan(bpy.context.scene.camera.data.angle / 2)
    radius = focal * math.tan(math.asin(bound / dist))
    on = grad[np.abs(rr - radius) < 1.5].mean()
    around = grad[(np.abs(rr - radius) > 4) & (np.abs(rr - radius) < 15)].mean()
    return on / around

clear()
bpy.ops.blackhole.add(mode="FAST")
root = active_root()
s = root.blackhole
s.view_distance = 110.0
bpy.ops.blackhole.frame_camera()
for mode in ("FAST", "EXACT"):
    s.mode = mode
    if mode == "EXACT":
        bpy.ops.blackhole.configure_render(quality="PREVIEW")
    for fog in (False, True):
        s.fog_enable = fog
        s.fog_outer, s.fog_falloff, s.fog_halo = 29.0, 0.2, 0.6
        ratio = edge_ratio(render(f"edge_{mode.lower()}_{int(fog)}"), 110.0, s.bounding_radius)
        check(ratio < 1.5, f"{mode}: no rim at the sphere's edge{' with fog' if fog else ''} "
              f"(edge/around gradient {ratio:.2f})")
s.fog_enable = False

s.fog_enable, s.fog_outer = True, 40.0
lens_tree = blackhole_studio.builder._child(root, ".Lens").active_material.node_tree
fog_group = next(gr for gr in blackhole_studio.builder._groups(root) if gr.name.startswith("BHS Fog"))
check([w[0] for w in blackhole_studio.params.limited(s)] == ["Fog"]
      and fog_group.nodes["FogOuter"].outputs[0].default_value <= 0.9 * s.bounding_radius + 1e-4,
      "fog beyond the sphere fades out inside it, with a warning")
s.fog_enable, s.fog_outer = False, 22.0

# Fly toward the hole: the black hole stays visible inside the sphere, Fast matches Exact
# (close in, the shadow covers most of the sky: a wide-angle lens keeps the disk in view)
for dist in (25.0, 10.0, 4.0, 2.2):
    s.view_distance = dist
    bpy.ops.blackhole.frame_camera()
    bpy.context.scene.camera.data.lens = 50.0 if dist > 5.0 else 10.0
    s.mode = "FAST"
    fast_px = render(f"inside_{dist:g}_fast")
    s.mode = "EXACT"
    exact_px = render(f"inside_{dist:g}_exact")
    h, w, _ = fast_px.shape
    centre = float(fast_px[h // 2 - 2:h // 2 + 3, w // 2 - 2:w // 2 + 3].mean())
    diff = float(np.abs(exact_px - fast_px).mean())
    check(centre < 0.05 and fast_px.max() > 0.3,
          f"camera at {dist:g} r_s: black hole visible in Fast mode (centre {centre:.3f})")
    check(diff < 0.03, f"camera at {dist:g} r_s: Fast and Exact agree (mean diff {diff:.4f})")

s.view_distance = 4.0
bpy.ops.blackhole.frame_camera()
bpy.context.scene.camera.data.lens = 10.0
for mode in ("FAST", "EXACT"):
    s.mode = mode
    s.hovering_observer = False
    still = render(f"hover_off_{mode.lower()}")
    s.hovering_observer = True
    hover = render(f"hover_on_{mode.lower()}")
    bright = still.mean(2) > 0.05
    blue = lambda px: float(px[bright][:, 2].mean() / max(px[bright][:, 0].mean(), 1e-6))
    check(hover[bright].mean() > still[bright].mean() and blue(hover) > blue(still),
          f"{mode}: hovering observer sees light brighter and bluer "
          f"({hover[bright].mean():.3f} vs {still[bright].mean():.3f})")
    if mode == "FAST":
        hover_fast = hover
diff = float(np.abs(hover - hover_fast).mean())
check(diff < 0.03, f"hovering observer: Fast and Exact agree (mean diff {diff:.4f})")
s.hovering_observer = False

# Lensing of real scene objects (Fast mode, Cycles)
clear()
bpy.ops.blackhole.add(mode="FAST")
root = active_root()
root.blackhole.view_inclination = 1.5708
root.blackhole.view_distance = 45.0
root.blackhole.disk_enable = False
bpy.ops.blackhole.frame_camera()
mesh = bpy.data.meshes.new("Wall")
mesh.from_pydata([(-20, 12, -20), (20, 12, -20), (20, 12, 20), (-20, 12, 20)], [], [(0, 1, 2, 3)])
wall = bpy.data.objects.new("Wall", mesh)
bpy.context.collection.objects.link(wall)

mat = bpy.data.materials.new("Checker")
if bpy.app.version < (5, 0, 0):
    mat.use_nodes = True
nt = mat.node_tree
chk = nt.nodes.new("ShaderNodeTexChecker")
chk.inputs["Scale"].default_value = 12.0
em = nt.nodes.new("ShaderNodeEmission")
nt.links.new(chk.outputs["Color"], em.inputs["Color"])
nt.links.new(em.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
wall.data.materials.append(mat)
root.blackhole.lens_objects = True
px_on = render("lens_objects_on")
root.blackhole.lens_objects = False
px_off = render("lens_objects_off")
check(np.abs(px_on - px_off).mean() > 0.01, "lens objects changes the image behind the hole")

bpy.ops.blackhole.remove()
check(not any(o.name.startswith("BlackHole") for o in bpy.data.objects), "remove operator")

blackhole_studio.unregister()
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)