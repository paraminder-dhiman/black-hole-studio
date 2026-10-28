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
which runs on GPU and in Eevee."""

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

# --------------------------------------------------------------------------
# Shared output stage
# --------------------------------------------------------------------------

def _output(g, tree, disk, glow, trans, esc, exit_dir, passthrough, boost):
    """passthrough: 1 where the sphere must be invisible (its inside seen from
    outside, e.g. by a ray refracted through it); boost: hovering-observer
    colour factor for the background."""
    geo = g.node("ShaderNodeNewGeometry")
    d_world = g.normalize(-Sock(g, geo.outputs["Incoming"], True))
    g.next_column()

    hsv = g.node("ShaderNodeHueSaturation")
    g._set(hsv.inputs["Color"], g.add(disk, glow))
    g._set(hsv.inputs["Hue"], g.param("Hue", 0.5))
    g._set(hsv.inputs["Saturation"], g.param("Saturation", 1.0))
    styled = Sock(g, hsv.outputs["Color"], True)

    bg = _background(g, exit_dir)
    sky = g.mul(g.mul(bg, boost), trans * esc * g.param("BgStrength", 1.0))
    front_col = g.add(styled, sky)

    emit_all = g.node("ShaderNodeEmission")
    g._set(emit_all.inputs["Color"], front_col)
    emit_all.inputs["Strength"].default_value = 1.0

    # "Lens scene objects": steer a refraction with a synthetic normal so the
    # transmitted ray leaves exactly along the bent direction. Snell gives
    # N ~ d - eta d' (eta = 4 allows deflections up to ~75 deg); larger
    # deflections fall back to the skybox/starfield lookup.
    eta = 4.0
    normal = g.normalize(g.sub(d_world, g.mul(exit_dir, eta)))
    refr = g.node("ShaderNodeBsdfRefraction", distribution="GGX")
    refr.inputs["Roughness"].default_value = 0.0
    refr.inputs["IOR"].default_value = eta
    refr.inputs["Color"].default_value = (1, 1, 1, 1)
    g._set(refr.inputs["Normal"], normal)

    emit_disk = g.node("ShaderNodeEmission")
    g._set(emit_disk.inputs["Color"], styled)
    black = g.node("ShaderNodeEmission")
    black.inputs["Strength"].default_value = 0.0
    weighted = g.node("ShaderNodeMixShader")
    g._set(weighted.inputs[0], trans * esc)
    tree.links.new(black.outputs[0], weighted.inputs[1])
    tree.links.new(refr.outputs[0], weighted.inputs[2])
    lens = g.node("ShaderNodeAddShader")
    tree.links.new(emit_disk.outputs[0], lens.inputs[0])
    tree.links.new(weighted.outputs[0], lens.inputs[1])

    steerable = g.gt(g.dot(d_world, exit_dir), 1.0 / eta + 0.02)

    choose = g.node("ShaderNodeMixShader")
    g._set(choose.inputs[0], g.param("LensObjects", 0.0) * steerable)
    tree.links.new(emit_all.outputs[0], choose.inputs[1])
    tree.links.new(lens.outputs[0], choose.inputs[2])

    transp = g.node("ShaderNodeBsdfTransparent")
    final = g.node("ShaderNodeMixShader")
    g._set(final.inputs[0], passthrough)
    tree.links.new(choose.outputs[0], final.inputs[1])
    tree.links.new(transp.outputs[0], final.inputs[2])

    out = tree.nodes.new("ShaderNodeOutputMaterial")
    out.location = (g.x0 + (g._col + 2) * 180, 0)
    tree.links.new(final.outputs[0], out.inputs["Surface"])

# --------------------------------------------------------------------------
# Where the light path starts, and the hovering observer
# --------------------------------------------------------------------------

def _ray_origin(g):
    """World-space origin of the ray being shaded.

    Cycles: exact for every ray (shading point minus the ray length).  Where
    the ray length is unavailable (Eevee), the camera position."""
    geo = g.node("ShaderNodeNewGeometry")
    pos = Sock(g, geo.outputs["Position"], True)
    d = g.normalize(-Sock(g, geo.outputs["Incoming"], True))
    path = g.node("ShaderNodeLightPath")
    length = Sock(g, path.outputs["Ray Length"])
    cam = g.transform((0.0, 0.0, 0.0), "CAMERA", "WORLD", "POINT")
    traced = g.sub(pos, g.mul(d, length))
    return g.lerp(cam, traced, g.gt(length, 0.0))

def _observer_boost(g, gamma):
    """Colour factor for light without its own spectrum (sky, fog, jets) seen
    by a hovering observer: bolometric gamma^4 and a blackbody-like tint."""
    ref = g.blackbody(6500.0)
    hot = g.blackbody(6500.0 * gamma)
    tint = g.div(g.div(hot, g.max(g.luminance(hot), 1e-4)), g.div(ref, g.max(g.luminance(ref), 1e-4)))
    return g.mul(tint, gamma * gamma * gamma * gamma)

# --------------------------------------------------------------------------
# Per-black-hole node groups: disk sample and fog sample
# --------------------------------------------------------------------------
#
# The groups hold their own named Value/RGB parameter nodes (builder.apply
# writes into them like into the material) and are rebuilt with the shader,
# so they carry no user data.  Each is instanced once per disk crossing or
# per path segment.

DISK_GROUP_PREFIX = "BHS Disk "
FOG_GROUP_PREFIX = "BHS Fog "
GROUP_VERSION = 1

_COLOR_DEFAULTS = {"ColorInner": (1.0, 0.95, 0.85, 1), "ColorMid": (1.0, 0.55, 0.15, 1),
                   "ColorOuter": (0.6, 0.12, 0.02, 1), "RingGlowColor": (1.0, 0.7, 0.4, 1),
                   "HorizonGlowColor": (1.0, 0.4, 0.1, 1), "JetColor": (0.4, 0.6, 1.0, 1),
                   "FogColor": (0.55, 0.42, 0.36, 1)}


def _color(g, name):
    return g.color_param(name, _COLOR_DEFAULTS[name])

def _temp_shape(g, x, q):
    x = g.max(x, 1.0001)
    return (x ** -q) * ((1.0 - x ** -0.5) ** 0.25)

def _new_group(name, inputs, outputs):
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group["bhs_version"] = GROUP_VERSION
    for sock_name, kind in inputs:
        group.interface.new_socket(sock_name, in_out="INPUT", socket_type=kind)
    for sock_name, kind in outputs:
        group.interface.new_socket(sock_name, in_out="OUTPUT", socket_type=kind)

    g = Graph(group)
    gin = group.nodes.new("NodeGroupInput")
    gin.location = (-600, 200)
    gout = group.nodes.new("NodeGroupOutput")
    vec = {n for n, k in inputs if k in ("NodeSocketVector", "NodeSocketColor")}
    ins = {n: Sock(g, gin.outputs[n], n in vec) for n, _ in inputs}
    return group, g, ins, gout

def _finish_group(group, g, gout, outputs):
    for sock_name, value in outputs.items():
        group.links.new(value.socket, gout.inputs[sock_name])
    gout.location = (g.x0 + (g._col + 2) * 180, 0)
    return group

def _keplerian(g, r, spin):
    """Omega and u^t of an equatorial circular orbit at r (r_s units)."""
    rm = 2.0 * r
    rm15 = rm ** 1.5
    omega = 2.0 / (rm15 + spin)
    ut = (rm15 + spin) / ((rm ** 0.75) * g.sqrt(g.max(rm15 - 3.0 * g.sqrt(rm) + 2.0 * spin, 1e-4)))
    return omega, ut

def build_disk_group(name):
    """Disk shading at one point of the disk plane.

    Inputs:  Hit (r_s, z ignored), Lz (photon angular momentum), Boost, Time,
             Shift (observer frequency factor, 1 unless Hovering Observer)
    Outputs: Emission (colour, before opacity), Alpha (thin-disk opacity)
    Thin-disk temperature, exact redshift g, blackbody/artistic colour,
    procedural turbulence, dust lanes, the disk texture and the edges."""

    group, g, i, gout = _new_group(
        DISK_GROUP_PREFIX + name,
        [("Hit", "NodeSocketVector"), ("Lz", "NodeSocketFloat"), ("Boost", "NodeSocketFloat"),
         ("Time", "NodeSocketFloat"), ("Shift", "NodeSocketFloat")],
        [("Emission", "NodeSocketColor"), ("Alpha", "NodeSocketFloat")])

    p = g.param
    spin = p("Spin", 0.0)
    sign = p("RotationSign", 1.0)
    r_in = p("DiskInner", 3.0)
    r_out = p("DiskOuter", 12.0)
    t_peak = p("PeakTemp", 7000.0)
    q = p("TempExponent", 0.75)

    s_pk = 8.0 * q / (8.0 * q + 1.0)
    shape_max = _temp_shape(g, 1.0 / (s_pk * s_pk), q)
    time = i["Time"]
    noise_time = time * p("NoiseSpeed", 1.0)

    hx, hy, _ = g.xyz(i["Hit"])
    r = g.max(g.sqrt(hx * hx + hy * hy), 1e-3)
    temp = t_peak * _temp_shape(g, r / r_in, q) / shape_max
    omega, ut = _keplerian(g, r, spin)

    g_grav = 1.0 / ut
    g_dop = 1.0 / g.max(1.0 - omega * i["Lz"], 0.05)
    gfac = (g_grav ** p("Gravity", 1.0)) * (g_dop ** p("Doppler", 1.0)) * i["Shift"]
    t_obs = temp * g.lerp(1.0, gfac, p("ColorShift", 1.0))

    bb = g.blackbody(t_obs)
    phys = g.div(bb, g.max(g.luminance(bb), 1e-4))
    tn = g.clamp01(temp / t_peak)
    hi = g.lerp(_color(g, "ColorMid"), _color(g, "ColorInner"), g.clamp01((tn - 0.5) * 2.0))
    lo = g.lerp(_color(g, "ColorOuter"), _color(g, "ColorMid"), g.clamp01(tn * 2.0))
    art = g.lerp(lo, hi, g.gt(tn, 0.5))
    col = g.lerp(art, phys, p("PhysicalMix", 1.0))

    bright = g.max(gfac * temp / t_peak, 0.0) ** p("BrightnessExponent", 4.0)

    # Differentially rotating procedural turbulence (Keplerian shear)
    lr = g.log(r)
    ang = g.atan2(hy, hx) - omega * sign * noise_time + p("Shear", 2.0) * lr
    nvec = g.mul(g.vec(g.cos(ang) * lr * 3.0, g.sin(ang) * lr * 3.0, lr * 0.5), p("NoiseScale", 1.5))
    n = g.noise(nvec, scale=1.0, detail=p("NoiseDetail", 5.0), roughness=p("NoiseRoughness", 0.55))

    dens = g.lerp(1.0, g.smoothstep(0.15, 0.85, n) * 1.6, p("NoiseContrast", 0.6))
    lanes = 0.5 + 0.5 * g.sin(r * p("DustScale", 4.0) + 3.0 * g.noise(g.mul(nvec, 0.7), detail=3.0))
    dens = dens * g.lerp(1.0, g.smoothstep(0.2, 0.8, lanes), p("DustLanes", 0.0))

    # Disk texture: top-down map, fixed rotation plus optional rigid/Keplerian motion
    motion = p("TextureMotion", 0.0)                     # 0 static, 1 rigid, 2 Keplerian
    rigid = g.math("COMPARE", motion, 1.0, 0.5)
    kepler = g.math("COMPARE", motion, 2.0, 0.5)
    omega_rigid = 2.0 / (r_out ** 1.5)                   # Keplerian rate at mid-disk (r_out / 2)

    beta = p("TextureRotation", 0.0) + sign * p("TextureSpeed", 1.0) * time * \
        (rigid * omega_rigid + kepler * omega)
    cb, sb = g.cos(beta), g.sin(beta)
    extent = 2.0 * r_out * p("TextureScale", 1.0)
    u = 0.5 + (cb * hx + sb * hy) / extent
    v = 0.5 + (cb * hy - sb * hx) / extent

    img = g.node("ShaderNodeTexImage", interpolation="Linear", extension="EXTEND")
    img.name = img.label = "DiskTexture"
    img.hide = False
    g._set(img.inputs["Vector"], g.vec(u, v, 0.0))
    tex = g.max(g.luminance(Sock(g, img.outputs["Color"], True)), 0.0) ** p("TextureContrast", 1.0)

    on = p("TextureEnable", 1.0)
    dens = dens * g.lerp(1.0, tex, p("TextureDensity", 1.0) * on)
    tex_emission = g.lerp(1.0, tex, p("TextureEmission", 0.5) * on)

    soft_o = p("OuterSoftness", 0.3)
    edge = g.smoothstep(r_in, r_in * (1.0 + p("InnerSoftness", 0.05)), r) * \
        (1.0 - g.smoothstep(r_out * (1.0 - soft_o), r_out * (1.0 + soft_o), r))
    alpha = g.clamp01(p("Opacity", 0.95) * dens * edge * p("DiskEnable", 1.0))

    emission = g.mul(col, bright * p("EmissionStrength", 1.0) * i["Boost"] * tex_emission)
    return _finish_group(group, g, gout, {"Emission": emission, "Alpha": alpha})

def build_fog_group(name):
    """Fog at the midpoint of one path segment (see volume.py for the maths).

    Inputs:  Pos (r_s), Ds (segment length), Time
    Outputs: Glow (scalar, already x ds; the colour is applied once by the
             compositor), Tau (extinction optical depth)
    """
    group, g, i, gout = _new_group(
        FOG_GROUP_PREFIX + name,
        [("Pos", "NodeSocketVector"), ("Ds", "NodeSocketFloat"), ("Time", "NodeSocketFloat")],
        [("Glow", "NodeSocketFloat"), ("Tau", "NodeSocketFloat")])

    p = g.param
    x, y, z = g.xyz(i["Pos"])
    rc = g.max(g.sqrt(x * x + y * y), 1e-4)
    r = g.max(g.sqrt(x * x + y * y + z * z), 1e-4)
    r_in = p("DiskInner", 3.0)
    hf = g.max(p("FogHeight", 0.15) * rc * ((rc / r_in) ** p("DiskFlare", 0.0)), 1e-3)

    halo = p("FogHalo", 0.25)
    disk_part = g.exp(-0.5 * (z / hf) * (z / hf))
    halo_part = g.min(r_in / r, 1.0) ** p("HaloFalloff", 1.5)
    shape = (1.0 - halo) * disk_part + halo * halo_part

    inner, outer = p("FogInner", 2.0), p("FogOuter", 22.0)
    window = g.smoothstep(inner, inner * 1.3, r) * \
        (1.0 - g.smoothstep(outer * (1.0 - p("FogFalloff", 0.5)), outer, r))

    # Turbulence that orbits at the local Keplerian rate
    omega, _ = _keplerian(g, rc, p("Spin", 0.0))
    ang = -omega * p("RotationSign", 1.0) * i["Time"] * p("FogSwirl", 1.0)
    ca, sa = g.cos(ang), g.sin(ang)
    rot = g.vec(ca * x - sa * y, sa * x + ca * y, z * 2.0)

    n = g.noise(rot, scale=p("FogNoiseScale", 0.35), detail=p("FogNoiseDetail", 4.0), roughness=0.55)
    noise = g.lerp(1.0, g.smoothstep(0.3, 0.75, n) * 2.0, p("FogNoise", 0.6))

    rho = p("FogDensity", 0.05) * window * shape * noise * p("FogEnable", 0.0)
    ds = i["Ds"]
    tau = rho * p("FogAbsorption", 1.0) * ds
    lit = g.min((r_in / r) * (r_in / r), 1.0)
    glow = rho * p("FogGlow", 2.0) * lit * ds
    return _finish_group(group, g, gout, {"Glow": glow, "Tau": tau})

def _fog_color(g):
    """Fog glow colour: the disk's peak blackbody blended with Fog Color."""
    p = g.param
    bb = g.blackbody(p("PeakTemp", 7000.0))
    disk_col = g.div(bb, g.max(g.luminance(bb), 1e-4))
    return g.lerp(disk_col, _color(g, "FogColor"), p("FogColorMix", 0.5))

def _call(g, group, **inputs):
    node = g.node("ShaderNodeGroup")
    node.node_tree = group
    for key, value in inputs.items():
        g._set(node.inputs[key], value)
    return node

# --------------------------------------------------------------------------
# Compositing shared by both modes
# --------------------------------------------------------------------------

def _glow(g, b, trans):
    """Artistic glow around the shadow edge, behind everything the ray passed."""
    p = g.param
    b_sh = physics.CRITICAL_IMPACT * (1.0 - 0.067 * g.abs(p("Spin", 0.0)))
    width = g.max(p("RingGlowWidth", 0.15), 1e-3)
    xg = (b - b_sh) / width
    glow = g.mul(_color(g, "RingGlowColor"), p("RingGlow", 0.0) * g.exp(-xg * xg))

    inside = g.lt(b, b_sh)
    hglow = g.mul(_color(g, "HorizonGlowColor"),
                  p("HorizonGlow", 0.0) * inside * g.exp(-(b_sh - b) / (width * 2.0)))
    return g.mul(g.add(glow, hglow), trans)

def _thin_layers(g, disk_group, hits, lz, shift):
    """(emission, alpha) of each thin-disk crossing, via the disk group."""
    layers = []
    for k, (hit, valid) in enumerate(hits):
        g.next_column()
        node = _call(g, disk_group, Hit=hit, Lz=lz, Time=g.param("Time", 0.0), Shift=shift,
                     Boost=1.0 if k == 0 else g.param("PhotonRingBoost", 1.0))
        layers.append((Sock(g, node.outputs["Emission"], True), Sock(g, node.outputs["Alpha"]) * valid))
    return layers

def _composite_thin(g, disk_group, hits, lz, b, shift, jets=None):
    """Thin disk, no volume: up to three crossings front to back, plus jets and glow.

    jets: optional [light before crossing 1, between 1-2, 2-3, behind 3]
    Returns (emitted colour, glow colour, remaining transmittance).
    """
    acc = jets[0] if jets else None
    trans = 1.0
    for k, (em, a) in enumerate(_thin_layers(g, disk_group, hits, lz, shift)):
        layer = g.mul(em, a if k == 0 else trans * a)
        acc = layer if acc is None else g.add(acc, layer)
        trans = 1.0 - a if k == 0 else trans * (1.0 - a)
        if jets and jets[k + 1] is not None:
            acc = g.add(acc, g.mul(jets[k + 1], trans))
    return acc, _glow(g, b, trans), trans

def _offset_xyz(g, vec, dep):
    x, y, z = g.xyz(vec)
    return (x, y, z) if isinstance(dep, float) else (x + dep, y, z)

def _pixel_jitter(g):
    """Uniform [0, 1) value that differs between neighbouring shading points."""
    geo = g.node("ShaderNodeNewGeometry")
    node = g.node("ShaderNodeTexWhiteNoise", noise_dimensions="3D")
    g._set(node.inputs["Vector"], g.mul(Sock(g, geo.outputs["Position"], True), 4096.0))
    return Sock(g, node.outputs["Value"])

def _normal_cdf(g, x):
    return 0.5 + 0.5 * g.math("TANH", 0.7978845608 * (x + 0.044715 * x * x * x))

STEP_GROUP_PREFIX = "BHS Step "
POINT_GROUP_PREFIX = "BHS Point "

def build_step_group(name, disk_group, fog_group, thick):
    """One segment of the volume march, as a reusable group.

    The material chains one instance per path segment, so the per-segment
    logic is built once (fast to build in Python) and Cycles sees a strictly
    sequential chain (every value depends on the incoming state, which keeps
    its SVM stack small whatever order the nodes are compiled in).

    State in/out: Acc (coloured light), FogAcc (scalar fog glow), VolTrans
    (volume transmittance), FogBefore (optical depth in front of each of the
    three thin-disk crossings, as a vector).  See _composite_volume."""

    group, g, i, gout = _new_group(
        STEP_GROUP_PREFIX + name,
        [("P0", "NodeSocketVector"), ("P1", "NodeSocketVector"), ("Acc", "NodeSocketColor"),
         ("FogAcc", "NodeSocketFloat"), ("VolTrans", "NodeSocketFloat"),
         ("FogBefore", "NodeSocketVector"), ("J", "NodeSocketFloat"), ("Lz", "NodeSocketFloat"),
         ("Time", "NodeSocketFloat"), ("LayerAlpha", "NodeSocketVector"),
         ("LayerT", "NodeSocketVector"), ("Shift", "NodeSocketFloat")],
        [("Acc", "NodeSocketColor"), ("FogAcc", "NodeSocketFloat"), ("VolTrans", "NodeSocketFloat"),
         ("FogBefore", "NodeSocketVector"), ("Dep", "NodeSocketFloat")])

    p = g.param
    r_in = p("DiskInner", 3.0)
    vol_trans = i["VolTrans"]
    dep = vol_trans * 1e-30           # Ties every term below to the incoming state
    pos_j = i["J"] + dep

    x0, y0, z0 = g.xyz(i["P0"])
    x1, y1, z1 = g.xyz(i["P1"])
    ddx, ddy, dz = x1 - x0, y1 - y0, z1 - z0
    ds = g.sqrt(ddx * ddx + ddy * ddy + dz * dz)
    fog = _call(g, fog_group, Pos=g.vec((x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5),
                Ds=ds, Time=i["Time"])
    glow = Sock(g, fog.outputs["Glow"])
    tau = Sock(g, fog.outputs["Tau"])
    acc = i["Acc"]
    weight = vol_trans

    if thick:
        # Gaussian slab, integrated in closed form over the segment; the disk is
        # shaded where the segment crosses the midplane, else at its midpoint
        den = z0 - z1
        t_cross = g.clamp01(z0 / (g.math("SIGN", den) * g.max(g.abs(den), 1e-6)))
        t_star = g.lerp(0.5, t_cross, g.lt(z0 * z1, 0.0))
        qx, qy = x0 + ddx * t_star, y0 + ddy * t_star

        rc = g.max(g.sqrt(qx * qx + qy * qy), 1e-3)
        h = g.max(p("DiskThickness", 0.0) * rc * ((rc / r_in) ** p("DiskFlare", 0.0)), 1e-4)
        steep = g.gt(g.abs(dz), h * 1e-3)
        column_steep = ds * g.abs(_normal_cdf(g, z1 / h) - _normal_cdf(g, z0 / h)) / g.max(g.abs(dz), 1e-6)
        zm = (z0 + z1) * 0.5
        column_flat = ds * g.exp(-0.5 * (zm / h) * (zm / h)) / (2.5066283 * h)
        column = g.lerp(column_flat, column_steep, steep)

        disk = _call(g, disk_group, Hit=g.vec(qx, qy, 0.0), Lz=i["Lz"], Time=i["Time"], Boost=1.0,
                     Shift=i["Shift"])
        alpha = g.min(Sock(g, disk.outputs["Alpha"]), 0.999)
        tau_d = -g.log(1.0 - alpha) * column
        em = g.mul(Sock(g, disk.outputs["Emission"], True), (1.0 - g.exp(-tau_d)) * weight)
        acc = g.add(acc, em)
        tau = tau + tau_d
        fog_before = i["FogBefore"]

    else:
        # thin-disk crossings in front of this segment dim it
        ax, ay, az = g.xyz(i["LayerAlpha"])
        tx, ty, tz = g.xyz(i["LayerT"])
        mid_t = pos_j + 0.5
        for a, tk in ((ax, tx), (ay, ty), (az, tz)):
            weight = weight * (1.0 - a * g.lt(tk, mid_t))

        fbx, fby, fbz = g.xyz(i["FogBefore"])
        fog_before = g.vec(fbx + tau * g.clamp01(tx - pos_j), fby + tau * g.clamp01(ty - pos_j),
                           fbz + tau * g.clamp01(tz - pos_j))

    fog_acc = i["FogAcc"] + glow * weight
    vol_out = vol_trans * g.exp(-tau)

    # "Seal": every output depends (x 1e-30) on all of them. Blender 5's Cycles
    # schedules nodes Sethi-Ullman style, finishing one dependency chain before the
    # next; with separate accumulator chains it would run one through all segments
    # while holding the others' inputs on the stack. Sealed, a step is computed
    # as a unit and the chains advance together.
    seal = (vol_out + fog_acc + g.dot(fog_before, (1.0, 1.0, 1.0)) + g.luminance(acc)) * 1e-30
    sealed_vec = g.vec(seal, seal, seal)
    return _finish_group(group, g, gout, {
        "Acc": g.add(acc, sealed_vec), "FogAcc": fog_acc + seal, "VolTrans": vol_out + seal,
        "FogBefore": g.add(fog_before, sealed_vec), "Dep": seal})

def build_point_group(name, lut_image):
    """Fast mode: the path point at segment boundary J, from the geodesic table."""
    group, g, i, gout = _new_group(
        POINT_GROUP_PREFIX + name,
        [("J", "NodeSocketFloat"), ("Dep", "NodeSocketFloat"), ("D", "NodeSocketVector"),
         ("E", "NodeSocketVector"), ("ThetaIn", "NodeSocketFloat"), ("Span", "NodeSocketFloat"),
         ("ThetaEnd", "NodeSocketFloat"), ("Jitter", "NodeSocketFloat"),
         ("LutX", "NodeSocketFloat"), ("Samples", "NodeSocketFloat")],
        [("P", "NodeSocketVector")])

    # Dep first: every term depends on the chain
    theta = ((i["Dep"] + i["J"]) + i["Jitter"]) / i["Samples"] * i["Span"] + i["ThetaIn"]

    img = g.node("ShaderNodeTexImage", interpolation="Linear", extension="EXTEND")
    img.name = img.label = "LUT"
    img.image = lut_image
    g._set(img.inputs["Vector"], g.vec(i["LutX"], g.min(theta, i["ThetaEnd"] - 1e-3) / lut.THETA_MAX, 0.0))
    u, _, _ = g.xyz(Sock(g, img.outputs["Color"], True))

    r_exit = g.param("ExitRadius", 30.0)
    r = 1.0 / g.clamp(u, 1.0 / r_exit, 1.0)
    point = g.mul(g.add(g.mul(i["D"], -g.cos(theta)), g.mul(i["E"], g.sin(theta))), r)
    return _finish_group(group, g, gout, {"P": point})

def _composite_volume(g, step_group, point_at, n_seg, layers, hit_t, b, jets, thick, lz,
                      shift, boost):
    """Fog and/or thick disk integrated along the path polyline P_0..P_N.

    point_at(j, dep) returns P_j.  Each segment is one instance of the step
    group (see build_step_group).  Thin disk: the exact crossings (layers) are
    composited at their positions hit_t along the polyline.  Thick disk: the
    step group integrates the Gaussian slab (volume.slab_column).
    Returns (emitted colour, glow colour, remaining transmittance)."""

    p = g.param
    time = p("Time", 0.0)
    if layers:
        alphas = g.vec(*[a for _, a in layers])
        ts = g.vec(*hit_t)
    else:
        alphas = ts = (0.0, 0.0, 0.0)
    acc = jets[0] if jets else (0.0, 0.0, 0.0, 1.0)
    fog_acc, vol_trans, fog_before, dep = 0.0, 1.0, (0.0, 0.0, 0.0), 0.0

    p0 = point_at(0, 0.0)
    for j in range(n_seg):
        g.next_column()
        p1 = point_at(j + 1, dep)
        step = _call(g, step_group, P0=p0, P1=p1, Acc=acc, FogAcc=fog_acc, VolTrans=vol_trans,
                     FogBefore=fog_before, J=float(j), Lz=lz, Time=time, LayerAlpha=alphas,
                     LayerT=ts, Shift=shift)
        acc = Sock(g, step.outputs["Acc"], True)
        fog_acc = Sock(g, step.outputs["FogAcc"])
        vol_trans = Sock(g, step.outputs["VolTrans"])
        fog_before = Sock(g, step.outputs["FogBefore"], True)
        dep = Sock(g, step.outputs["Dep"])
        p0 = p1

    acc = g.add(acc, g.mul(g.mul(_fog_color(g), boost), fog_acc))

    if layers:
        fbs = g.xyz(fog_before)

        def thin_before(t):
            out = 1.0
            for (_, a), tk in zip(layers, hit_t):
                out = out * (1.0 - a * g.lt(tk, t))
            return out

        for k, ((em, a), tk) in enumerate(zip(layers, hit_t)):
            acc = g.add(acc, g.mul(em, a * thin_before(tk) * g.exp(-fbs[k])))

    trans = vol_trans
    for _, a in layers:
        trans = trans * (1.0 - a)
    if jets:
        for k in (1, 2, 3):
            if jets[k] is not None:
                acc = g.add(acc, g.mul(jets[k], trans))
    return acc, _glow(g, b, trans), trans