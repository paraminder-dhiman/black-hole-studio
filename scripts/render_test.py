"""Headless render check:  python scripts/render_test.py OUT_DIR [MODE ...]  (needs `bpy`)."""
import os
import sys

import bpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from blackhole_studio import builder, properties  # noqa: E402

out_dir = sys.argv[1]
modes = sys.argv[2:] or ["FAST", "EXACT"]
properties.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.render.resolution_x = scene.render.resolution_y = int(os.environ.get("RES", 256))
scene.cycles.samples = int(os.environ.get("SPP", 8))
scene.view_settings.view_transform = "AgX"

for mode in modes:
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    root = builder.create(bpy.context, location=(0, 0, 0))
    root.blackhole.mode = mode
    builder.setup_world(scene, root)
    builder.frame_camera(bpy.context, root)
    root.blackhole.view_distance = float(os.environ.get("DIST", 40))
    builder.frame_camera(bpy.context, root)
    scene.camera.data.lens = 50
    scene.cycles.shading_system = mode == "EXACT"
    scene.render.filepath = os.path.join(out_dir, f"bh_{mode.lower()}.png")
    bpy.ops.render.render(write_still=True)
    print("rendered", scene.render.filepath)