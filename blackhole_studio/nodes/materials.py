"""Shader graphs for the black hole lens sphere and the world background.

Both render modes produce the same five quantities in black-hole space and
share one output stage:

    disk  - emitted light (disk, jets) composited front-to-back
    glow  - artistic photon-ring / horizon glow
    trans - remaining transmittance after the disk
    esc   - 1 if the photon escaped, 0 if it fell into the hole
    exit  - asymptotic (bent) direction in world space -> background lookup

Exact mode gets them from the OSL geodesic integrator, Fast mode from a
precomputed geodesic table (see lut.py) evaluated with ordinary math nodes,
which runs on GPU and in Eevee.
"""

import math
import os

import bpy

from .. import lut, physics
from .nodemath import Graph, Sock

STARFIELD_GROUP = "BHS Starfield"
OSL_TEXT = "BHS Geodesic.osl"
SHADER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders", "blackhole_geodesic.osl")

# --------------------------------------------------------------------------
# Starfield (shared by world + lens so the lensed sky matches the real sky)
# --------------------------------------------------------------------------

def ensure_starfield_group():
    group = bpy.data.node_groups.get(STARFIELD_GROUP)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(STARFIELD_GROUP, "ShaderNodeTree")

    iface = group.interface
    iface.new_socket("Vector", in_out="INPUT", socket_type="NodeSocketVector")
    for name, default in (("Density", 220.0), ("Brightness", 1.0), ("Star Size", 0.06),
                          ("Nebula", 0.15)):
        s = iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
        s.default_value = default
    s = iface.new_socket("Nebula Color", in_out="INPUT", socket_type="NodeSocketColor")
    s.default_value = (0.25, 0.35, 0.8, 1.0)
    iface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")

    g = Graph(group)
    gin = group.nodes.new("NodeGroupInput")
    gin.location = (-600, 0)
    gout = group.nodes.new("NodeGroupOutput")
    gout.location = (1600, 0)
    v = g.normalize(Sock(g, gin.outputs["Vector"], True))
    density = Sock(g, gin.outputs["Density"])
    bright = Sock(g, gin.outputs["Brightness"])
    size = Sock(g, gin.outputs["Star Size"])

    def star_layer(scale_mul, power, gain):
        vor = g.node("ShaderNodeTexVoronoi", voronoi_dimensions="3D", feature="F1")
        g._set(vor.inputs["Vector"], v)
        g._set(vor.inputs["Scale"], density * scale_mul)
        g._set(vor.inputs["Randomness"], 1.0)
        dist = Sock(g, vor.outputs["Distance"])
        rnd_r, rnd_g, _ = g.xyz(Sock(g, vor.outputs["Color"], True))

        core = 1.0 - g.smoothstep(0.0, size, dist)
        inten = (rnd_r ** power) * gain * core
        tint = g.blackbody(3000.0 + rnd_g * 9000.0)
        return g.mul(g.div(tint, g.max(g.luminance(tint), 1e-4)), inten)

    stars = g.add(star_layer(1.0, 10.0, 25.0), star_layer(0.25, 14.0, 80.0))
    neb = g.noise(v, scale=2.5, detail=8.0, roughness=0.6)
    neb2 = g.noise(g.mul(v, 1.7), scale=4.0, detail=4.0, roughness=0.5)
    neb_mask = g.smoothstep(0.48, 0.8, neb) * g.smoothstep(0.35, 0.7, neb2)
    nebula = g.mul(Sock(g, gin.outputs["Nebula Color"], True), neb_mask * Sock(g, gin.outputs["Nebula"]))
    g.links.new(g.add(g.mul(stars, bright), nebula).socket, gout.inputs["Color"])
    return group

def _starfield_node(g, vector):
    node = g.node("ShaderNodeGroup")
    node.node_tree = ensure_starfield_group()
    node.name = "Starfield"
    g._set(node.inputs["Vector"], vector)
    for sock, pname, default in (("Density", "StarDensity", 220.0), ("Brightness", "StarBrightness", 1.0),
                                 ("Star Size", "StarSize", 0.06), ("Nebula", "NebulaStrength", 0.15)):
        g._set(node.inputs[sock], g.param(pname, default))
    g._set(node.inputs["Nebula Color"], g.color_param("NebulaColor", (0.25, 0.35, 0.8, 1.0)))
    return Sock(g, node.outputs["Color"], True)

def _background(g, direction):
    """Skybox image and/or procedural starfield in a world-space direction."""
    rot = g.node("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
    g._set(rot.inputs["Vector"], direction)
    g._set(rot.inputs["Angle"], -g.param("SkyRotation", 0.0))
    direction = Sock(g, rot.outputs["Vector"], True)

    stars = _starfield_node(g, direction)
    env = g.node("ShaderNodeTexEnvironment")
    env.name = "Environment"
    g._set(env.inputs["Vector"], direction)
    use_sky = g.param("UseSky", 0.0)
    sky = g.mul(Sock(g, env.outputs["Color"], True), g.param("SkyStrength", 1.0) * use_sky)
    star_weight = (1.0 - use_sky) + use_sky * g.param("StarOverlay", 0.0)
    return g.add(sky, g.mul(stars, star_weight))

def _enable_nodes(idblock):
    """Blender < 5.0 needs use_nodes; 5.x always has a node tree and deprecates the flag."""
    if bpy.app.version < (5, 0, 0):
        idblock.use_nodes = True

def build_world(world):
    """World shader using the same skybox/starfield as the lens sphere."""
    _enable_nodes(world)
    tree = world.node_tree
    tree.nodes.clear()
    g = Graph(tree)
    coord = g.node("ShaderNodeTexCoord")
    bg_col = _background(g, Sock(g, coord.outputs["Generated"], True))
    bg = g.node("ShaderNodeBackground")
    g._set(bg.inputs["Color"], bg_col)
    g._set(bg.inputs["Strength"], g.param("BgStrength", 1.0))
    out = tree.nodes.new("ShaderNodeOutputWorld")
    out.location = (g.x0 + 1400, 0)
    tree.links.new(bg.outputs[0], out.inputs["Surface"])