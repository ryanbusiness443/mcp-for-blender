"""
thumb3d.py - builds and renders a YouTube thumbnail in Blender (4.x), in the style top creators use:
a few huge words with a flat bright face, a solid 3D block behind, thick black outline and hard shadow;
one subject that pops (a cut-out photo and/or photoreal 3D props) with a white stroke and a coloured
halo; a punchy background darkened behind the text.

Headless:  blender -b --factory-startup --python thumb3d.py -- spec.json
Live (MCP for Blender): send "SPEC = {...}" followed by this file; it builds in a new scene.

spec keys (all optional except title):
  title            "I FOUND\\nTHE CHEST"   big text; \\n forces a line break; long titles wrap
  highlight        ["CHEST"]               words drawn in highlight_color
  text_color       "#ffffff"   highlight_color "#ffe600"   depth_color "#101010"
  face             "flat" | "gold" | "chrome" | "fire" | "ice" | "toxic"   title face material
  font             path to a .ttf/.otf
  badge            "1 VS 4"                 small tag in a rounded pill (badge_color "#ff1f3d")
  background       image path; grade_background true -> contrast/saturation/darken in Blender
  subject          transparent PNG cut-out; subject_outline (png) optional, made automatically otherwise
  props            [{"path": "x.gltf" | "x.glb" | "x.blend", "scale": 1, "rot": 30}]   photoreal 3D props
  side             "right" | "left"        where subject / props go (text takes the other side)
  glow             "#ff2d55"               halo colour behind subject / props (false to disable)
  arrow            {"to": [x, y], "from": "left" | "right"}   circle {"at": [x, y], "radius": 0.1}
  hdri             .hdr / .exr environment for realistic lighting (else a procedural studio)
  tilt, yaw        title roll / turn in degrees (default -3, 10)
  out, width, height, samples, fresh (true = start from an empty file; false = new scene in the open file)
"""
import json, math, os, random, sys

import bpy
from mathutils import Vector

CAM_Y = -10.0
LENS = 35.0


# ---------------------------------------------------------------- materials
def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]  # sRGB -> linear
    return (*c, a)


def node_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    return m, nt, nt.nodes.new("ShaderNodeOutputMaterial")


def principled(name, color, metallic=0.0, rough=0.45, emit=None, emit_strength=0.0, spec=0.5):
    m, nt, out = node_mat(name)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = color
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = spec
    if emit is not None:
        b.inputs["Emission Color"].default_value = emit
        b.inputs["Emission Strength"].default_value = emit_strength
    nt.links.new(b.outputs[0], out.inputs[0])
    return m


def flat_text_mat(name, color, shade=0.14):
    """Poster-like fill: the exact colour, a touch darker at the bottom, unaffected by lights."""
    m, nt, out = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = 0.0, 1.0
    mr.inputs["To Min"].default_value, mr.inputs["To Max"].default_value = 1.0 - shade, 1.0
    comb = nt.nodes.new("ShaderNodeCombineColor")
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    mul.inputs["A"].default_value = color
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Y"], mr.inputs["Value"])
    for k in ("Red", "Green", "Blue"):
        nt.links.new(mr.outputs["Result"], comb.inputs[k])
    nt.links.new(comb.outputs["Color"], mul.inputs["B"])
    nt.links.new(mul.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def gradient_mat(name, stops, metallic=0.0, rough=0.35, emit_strength=0.0):
    """Bottom -> top colour ramp (fire / ice / toxic / gold faces)."""
    m, nt, out = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    for i, (pos, col) in enumerate(stops):
        e = els[i] if i < len(els) else els.new(pos)
        e.position, e.color = pos, col
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    if emit_strength:
        nt.links.new(ramp.outputs["Color"], b.inputs["Emission Color"])
        b.inputs["Emission Strength"].default_value = emit_strength
    nt.links.new(b.outputs[0], out.inputs[0])
    return m


def emission_mat(name, color, strength=1.0):
    m, nt, out = node_mat(name)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = color
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def image_mat(name, path, alpha=False):
    m, nt, out = node_mat(name)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path, check_existing=True)
    tex.extension = "CLIP"
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    if alpha:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs[0])
    else:
        nt.links.new(em.outputs[0], out.inputs[0])
    return m, nt, tex, em


def backdrop_grade(nt, tex, em, text_side, strength=1.0):
    """Punch + darken where the text goes + vignette, all in the shader (no external tools needed)."""
    hs = nt.nodes.new("ShaderNodeHueSaturation")
    hs.inputs["Saturation"].default_value = 1.0 + 0.25 * strength
    bc = nt.nodes.new("ShaderNodeBrightContrast")
    bc.inputs["Bright"].default_value = -0.04 * strength
    bc.inputs["Contrast"].default_value = 0.18 * strength
    nt.links.new(tex.outputs["Color"], hs.inputs["Color"])
    nt.links.new(hs.outputs["Color"], bc.inputs["Color"])
    uv = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs["UV"], sep.inputs[0])
    side = nt.nodes.new("ShaderNodeMapRange")
    side.clamp = True
    if text_side == "left":
        side.inputs["From Min"].default_value, side.inputs["From Max"].default_value = 0.0, 0.75
    elif text_side == "right":
        side.inputs["From Min"].default_value, side.inputs["From Max"].default_value = 1.0, 0.25
    else:
        side.inputs["From Min"].default_value, side.inputs["From Max"].default_value = 0.0, 1.0
    side.inputs["To Min"].default_value = (1.0 - 0.55 * strength) if text_side in ("left", "right") else 0.85
    side.inputs["To Max"].default_value = 1.0 if text_side in ("left", "right") else 0.85
    nt.links.new(sep.outputs["X"], side.inputs["Value"])
    vec = nt.nodes.new("ShaderNodeVectorMath")
    vec.operation = "DISTANCE"
    vec.inputs[1].default_value = (0.5, 0.5, 0.0)
    nt.links.new(uv.outputs["UV"], vec.inputs[0])
    vig = nt.nodes.new("ShaderNodeMapRange")
    vig.clamp = True
    vig.inputs["From Min"].default_value, vig.inputs["From Max"].default_value = 0.35, 0.75
    vig.inputs["To Min"].default_value, vig.inputs["To Max"].default_value = 1.0, 1.0 - 0.45 * strength
    nt.links.new(vec.outputs["Value"], vig.inputs["Value"])
    f = nt.nodes.new("ShaderNodeMath")
    f.operation = "MULTIPLY"
    nt.links.new(side.outputs["Result"], f.inputs[0])
    nt.links.new(vig.outputs["Result"], f.inputs[1])
    comb = nt.nodes.new("ShaderNodeCombineColor")
    for k in ("Red", "Green", "Blue"):
        nt.links.new(f.outputs[0], comb.inputs[k])
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    nt.links.new(bc.outputs["Color"], mul.inputs["A"])
    nt.links.new(comb.outputs["Color"], mul.inputs["B"])
    nt.links.new(mul.outputs["Result"], em.inputs["Color"])


def halo_mat(name, color, strength=2.0):
    """Soft radial glow (transparent at the edge) for behind the subject."""
    m, nt, out = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    vec = nt.nodes.new("ShaderNodeVectorMath")
    vec.operation = "DISTANCE"
    vec.inputs[1].default_value = (0.5, 0.5, 0.0)
    nt.links.new(tc.outputs["UV"], vec.inputs[0])
    fall = nt.nodes.new("ShaderNodeMapRange")
    fall.clamp = True
    fall.inputs["From Min"].default_value, fall.inputs["From Max"].default_value = 0.0, 0.5
    fall.inputs["To Min"].default_value, fall.inputs["To Max"].default_value = 1.0, 0.0
    nt.links.new(vec.outputs["Value"], fall.inputs["Value"])
    pw = nt.nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 1.8
    nt.links.new(fall.outputs["Result"], pw.inputs[0])
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = color
    em.inputs["Strength"].default_value = strength
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(pw.outputs[0], mix.inputs["Fac"])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    return m


FACES = {
    "gold": lambda: gradient_mat("gold", [(0.0, hex_rgba("#b8770d")), (0.6, hex_rgba("#ffd34a")), (1.0, hex_rgba("#fff1a8"))], metallic=1.0, rough=0.25),
    "chrome": lambda: gradient_mat("chrome", [(0.0, hex_rgba("#8a93a3")), (0.5, hex_rgba("#f4f7fb")), (1.0, hex_rgba("#ffffff"))], metallic=1.0, rough=0.12),
    "fire": lambda: gradient_mat("fire", [(0.0, hex_rgba("#e3260f")), (0.55, hex_rgba("#ff8a00")), (1.0, hex_rgba("#ffe14a"))], emit_strength=0.9),
    "ice": lambda: gradient_mat("ice", [(0.0, hex_rgba("#2b8cff")), (0.6, hex_rgba("#9fe6ff")), (1.0, hex_rgba("#ffffff"))], emit_strength=0.9),
    "toxic": lambda: gradient_mat("toxic", [(0.0, hex_rgba("#22b822")), (0.6, hex_rgba("#7dff3a")), (1.0, hex_rgba("#eaff7a"))], emit_strength=0.9),
}


# ---------------------------------------------------------------- geometry helpers
def frame_size(depth):
    w = depth * 36.0 / LENS
    return w, w * 9 / 16


def frame_point(u, v, y):
    w, h = frame_size(y - CAM_Y)
    return Vector(((u - 0.5) * w, y, (0.5 - v) * h))


def projected_box(scene, cam, objs):
    """Normalized camera-view bounds (x0, x1, y0, y1; origin bottom-left) of the objects."""
    from bpy_extras.object_utils import world_to_camera_view
    bpy.context.view_layer.update()
    xs, ys = [], []
    for o in objs:
        for c in o.bound_box:
            p = world_to_camera_view(scene, cam, o.matrix_world @ Vector(c))
            xs.append(p.x)
            ys.append(p.y)
    return min(xs), max(xs), min(ys), max(ys)


def fit(scene, cam, group, objs, box, iters=5):
    """Scale and move `group` until its on-screen projection fills `box`."""
    bx0, bx1, by0, by1 = box
    for _ in range(iters):
        x0, x1, y0, y1 = projected_box(scene, cam, objs)
        k = min((bx1 - bx0) / max(x1 - x0, 1e-4), (by1 - by0) / max(y1 - y0, 1e-4))
        group.scale = [v * k for v in group.scale]
        x0, x1, y0, y1 = projected_box(scene, cam, objs)
        w, h = frame_size(group.location.y - CAM_Y)
        group.location.x += ((bx0 + bx1) / 2 - (x0 + x1) / 2) * w
        group.location.z += ((by0 + by1) / 2 - (y0 + y1) / 2) * h


def text_obj(body, font_path, extrude=0.0, name="txt"):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = body
    if font_path and os.path.exists(font_path):
        cu.font = bpy.data.fonts.load(font_path, check_existing=True)
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    cu.space_line = 0.78
    cu.extrude = extrude
    ob = bpy.data.objects.new(name, cu)
    bpy.context.collection.objects.link(ob)
    ob.rotation_euler = (math.radians(90), 0, 0)  # stand up, facing the camera (-Y)
    return ob


def wrap_title(title, narrow):
    words = title.split()
    if "\n" in title or len(words) < 3 or not (narrow or len(title) > 16):
        return title
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def make_outline(path, px=14, color=(1.0, 1.0, 1.0)):
    """White stroke image around a cut-out's alpha (numpy max filter; works inside any Blender)."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=True)
    w, h = img.size
    a = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[:, :, 3]
    pad = px + 2
    big = np.zeros((h + 2 * pad, w + 2 * pad), dtype=np.float32)
    big[pad:pad + h, pad:pad + w] = a
    grown = big.copy()
    for d in range(1, px + 1):
        grown[:, d:] = np.maximum(grown[:, d:], big[:, :-d])
        grown[:, :-d] = np.maximum(grown[:, :-d], big[:, d:])
    rows = grown.copy()
    for d in range(1, px + 1):
        grown[d:, :] = np.maximum(grown[d:, :], rows[:-d, :])
        grown[:-d, :] = np.maximum(grown[:-d, :], rows[d:, :])
    out = np.zeros((h, w, 4), dtype=np.float32)
    out[:, :, 0], out[:, :, 1], out[:, :, 2] = color
    out[:, :, 3] = grown[pad:pad + h, pad:pad + w]
    o = bpy.data.images.new("subject_outline", w, h, alpha=True)
    o.pixels.foreach_set(out.ravel())
    dst = os.path.join(os.path.dirname(os.path.abspath(path)), "_thumb_subject_outline.png")
    o.filepath_raw = dst
    o.file_format = "PNG"
    o.save()
    return dst


def import_prop(path):
    """Imports a .gltf/.glb/.fbx/.obj/.blend prop; returns (root empty, mesh objects)."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o is not None:
                bpy.context.collection.objects.link(o)
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new("prop", None)
    bpy.context.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    return root, [o for o in new if o.type == "MESH"]


# ---------------------------------------------------------------- scene
def build(spec):
    if spec.get("fresh", True):
        bpy.ops.wm.read_factory_settings(use_empty=True)
    else:
        sc = bpy.data.scenes.new(spec.get("scene_name", "YouTube Thumbnail"))
        if bpy.context.window is not None:
            bpy.context.window.scene = sc
    random.seed(spec.get("seed", 7))
    scene = bpy.context.scene
    W, H = spec.get("width", 1280), spec.get("height", 720)
    glow_col = spec.get("glow") or spec.get("accent", "#ff2d55")

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = LENS
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = 36
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, CAM_Y, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.collection.objects.link(cam)
    scene.camera = cam

    # world: an HDRI (real reflections / soft light) or a procedural studio; never seen directly
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    wn = world.node_tree
    bg = wn.nodes["Background"]
    if spec.get("hdri") and os.path.exists(spec["hdri"]):
        env = wn.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(spec["hdri"], check_existing=True)
        wn.links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = spec.get("hdri_strength", 1.0)
    else:
        tc = wn.nodes.new("ShaderNodeTexCoord")
        sep = wn.nodes.new("ShaderNodeSeparateXYZ")
        mr = wn.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = -0.2, 0.9
        wn.links.new(tc.outputs["Generated"], sep.inputs[0])
        wn.links.new(sep.outputs["Z"], mr.inputs["Value"])
        wn.links.new(mr.outputs["Result"], bg.inputs["Strength"])
        bg.inputs["Color"].default_value = (0.9, 0.9, 0.92, 1)

    # what goes where
    has_subject = bool(spec.get("subject")) and os.path.exists(spec["subject"])
    props = [p for p in spec.get("props", []) if os.path.exists(p.get("path", ""))]
    focus = (spec.get("arrow") or {}).get("to") or (spec.get("circle") or {}).get("at")
    side = spec.get("side", spec.get("subject_side", "right"))
    if has_subject or props:
        text_side = "left" if side == "right" else "right"
    elif focus:
        text_side = "left" if focus[0] >= 0.5 else "right"
    else:
        text_side = "center"

    # backdrop
    back_y = 8.0
    bw, bh = frame_size(back_y - CAM_Y)
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, back_y, 0), rotation=(math.radians(90), 0, 0))
    plane = bpy.context.object
    plane.scale = (bw * 1.02, bh * 1.02, 1)
    if spec.get("background") and os.path.exists(spec["background"]):
        mat, nt, tex, em = image_mat("backdrop", spec["background"])
        if spec.get("grade_background", True):
            backdrop_grade(nt, tex, em, text_side, spec.get("grade_strength", 1.0))
    else:
        a = hex_rgba(glow_col)
        mat, nt, out = node_mat("backdrop")
        tc = nt.nodes.new("ShaderNodeTexCoord")
        vec = nt.nodes.new("ShaderNodeVectorMath")
        vec.operation = "DISTANCE"
        cxu = 0.5 if text_side == "center" else (0.72 if side == "right" else 0.28)
        vec.inputs[1].default_value = (cxu, 0.45, 0)
        nt.links.new(tc.outputs["UV"], vec.inputs[0])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = (a[0] * 0.55, a[1] * 0.55, a[2] * 0.55, 1)
        ramp.color_ramp.elements[1].position = 0.75
        ramp.color_ramp.elements[1].color = (0.006, 0.006, 0.01, 1)
        nt.links.new(vec.outputs["Value"], ramp.inputs["Fac"])
        em = nt.nodes.new("ShaderNodeEmission")
        nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
        nt.links.new(em.outputs[0], out.inputs[0])
    plane.data.materials.append(mat)
    plane.visible_shadow = False

    # subject cut-out and 3D props on `side`
    sub_y = 2.5
    sw, sh = frame_size(sub_y - CAM_Y)
    sx = (0.25 if side == "right" else -0.25) * sw
    if spec.get("glow") is not False and (has_subject or props):
        bpy.ops.mesh.primitive_plane_add(size=1, location=(sx, sub_y + 1.2, -sh * 0.02), rotation=(math.radians(90), 0, 0))
        halo = bpy.context.object
        halo.scale = (sh * 1.05, sh * 1.05, 1)
        halo.data.materials.append(halo_mat("halo", hex_rgba(glow_col), spec.get("glow_strength", 1.1)))
        halo.visible_shadow = False
    if has_subject:
        if not spec.get("subject_outline"):
            spec["subject_outline"] = make_outline(spec["subject"])
        img = bpy.data.images.load(spec["subject"], check_existing=True)
        aspect = img.size[0] / max(1, img.size[1])
        hgt = sh * spec.get("subject_height", 0.95)
        cz = -sh / 2 + hgt / 2 - sh * 0.02
        for path, dy, name in ((spec.get("subject_outline"), 0.02, "outline"), (spec["subject"], 0.0, "subject")):
            if path and os.path.exists(path):
                bpy.ops.mesh.primitive_plane_add(size=1, location=(sx, sub_y + dy, cz), rotation=(math.radians(90), 0, 0))
                p = bpy.context.object
                p.scale = (hgt * aspect, hgt, 1)
                m, *_ = image_mat(name, path, alpha=True)
                p.data.materials.append(m)
                p.visible_shadow = False
    n_items = len(props) + (1 if has_subject else 0)
    for i, pr in enumerate(props):
        root, meshes = import_prop(pr["path"])
        for mo in meshes:
            mo.pass_index = 2  # white stroke in the compositor
        root.rotation_euler = (math.radians(pr.get("tilt", 14)), 0, math.radians(pr.get("rot", -38 if side == "right" else 38)))
        root.location = (0, 1.0 - i * 0.8, 0)
        slot = i + (1 if has_subject else 0)
        span = 0.40 / n_items
        if side == "right":
            x0, x1 = 0.55 + slot * span, 0.55 + (slot + 1) * span
        else:
            x0, x1 = 0.05 + slot * span, 0.05 + (slot + 1) * span
        top = 0.74 if n_items == 1 else 0.6
        fit(scene, cam, root, meshes, (x0, x1, 0.06, top * pr.get("scale", 1.0)))

    # title: flat bright face + solid dark block behind (no bevels: fonts stay clean)
    font = spec.get("font")
    title = wrap_title(spec.get("title", "INSANE").upper(), text_side != "center")
    depth = spec.get("depth", 0.16)
    group = bpy.data.objects.new("title", None)
    bpy.context.collection.objects.link(group)
    face = text_obj(title, font, extrude=0.0, name="title_face")
    body = text_obj(title, font, extrude=depth, name="title_block")
    face.location.y = -(depth + 0.004)  # just in front of the block
    style = spec.get("face", "flat")
    main_mat = FACES[style]() if style in FACES else flat_text_mat("text", hex_rgba(spec.get("text_color", "#ffffff")))
    face.data.materials.append(main_mat)
    face.data.materials.append(flat_text_mat("highlight", hex_rgba(spec.get("highlight_color", "#ffe600"))))
    hl = [w.upper() for w in spec.get("highlight", [])]
    if hl:
        i = 0
        for line in title.split("\n"):
            for word in line.split(" "):
                if word.strip("!?.,'") in hl or word in hl:
                    for k in range(len(word)):
                        face.data.body_format[i + k].material_index = 1
                i += len(word) + 1
    dcol = hex_rgba(spec.get("depth_color", "#101010"))
    body.data.materials.append(principled("block", dcol, rough=0.5, emit=dcol, emit_strength=0.4, spec=0.2))
    for o in (face, body):
        o.parent = group
        o.pass_index = 1

    m = 0.045
    if text_side == "left":
        x0, x1 = m, 0.55
    elif text_side == "right":
        x0, x1 = 0.45, 1 - m
    else:
        x0, x1 = 0.07, 0.93
    badge_txt = spec.get("badge") or spec.get("subtitle")
    ty = spec.get("title_y", 0.0)
    box = (x0, x1, (0.30 if badge_txt else 0.18) + ty, 0.92 + ty)
    group.location = (((x0 + x1) / 2 - 0.5) * frame_size(10)[0], 0, 0)
    yaw = spec.get("yaw", 10) * (-1 if text_side == "right" else 1)
    group.rotation_euler = (math.radians(spec.get("pitch", 4)), math.radians(spec.get("tilt", -3)), math.radians(yaw))
    fit(scene, cam, group, [face, body], box)

    # badge: small tag in a rounded pill under the title
    if badge_txt:
        bgrp = bpy.data.objects.new("badge", None)
        bpy.context.collection.objects.link(bgrp)
        bt = text_obj(badge_txt.upper(), font, extrude=0.0, name="badge_text")
        bt.data.materials.append(flat_text_mat("badge_text", hex_rgba("#ffffff"), shade=0.0))
        bpy.context.view_layer.update()
        tw, th = bt.dimensions.x, bt.dimensions.y
        bpy.ops.mesh.primitive_cube_add(size=1)
        pill = bpy.context.object
        pill.scale = (tw + th * 0.9, 0.05, th * 1.55)
        bev = pill.modifiers.new("round", "BEVEL")
        bev.width = th * 0.5
        bev.segments = 8
        bev.limit_method = "NONE"
        pill.data.materials.append(flat_text_mat("badge", hex_rgba(spec.get("badge_color", "#ff1f3d")), shade=0.18))
        bt.location.y = -0.08
        for o in (bt, pill):
            o.parent = bgrp
            o.pass_index = 1
        tb = projected_box(scene, cam, [face, body])
        bgrp.location = (group.location.x, -0.5, 0)
        bgrp.rotation_euler = (0, math.radians(spec.get("tilt", -3) + 4), 0)
        w = (tb[1] - tb[0]) * 0.62
        mid = (tb[0] + tb[1]) / 2
        top = tb[2] - 0.03
        fit(scene, cam, bgrp, [pill], (mid - w / 2, mid + w / 2, max(0.04, top - 0.13), top))

    # call-outs
    if spec.get("arrow"):
        a = spec["arrow"]
        tip = frame_point(a["to"][0], a["to"][1], -1.0)
        frm = a.get("from", "right" if a["to"][0] >= 0.5 else "left")
        start = tip + Vector((2.0 if frm == "right" else -2.0, 0, 1.1 if a["to"][1] > 0.5 else -1.1))
        d = tip - start
        red = flat_text_mat("arrow", hex_rgba(a.get("color", "#ff1f3d")), shade=0.2)
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.14, depth=d.length * 0.68)
        shaft = bpy.context.object
        bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=0.42, depth=d.length * 0.36)
        head = bpy.context.object
        q = d.to_track_quat("Z", "Y").to_euler()
        for o, k in ((shaft, 0.34), (head, 0.82)):
            o.data.materials.append(red)
            o.rotation_euler = q
            o.location = start + d * k
            o.pass_index = 1
    if spec.get("circle"):
        c = spec["circle"]
        pos = frame_point(c["at"][0], c["at"][1], 1.0)
        rad = frame_size(1.0 - CAM_Y)[0] * c.get("radius", 0.1)
        bpy.ops.mesh.primitive_torus_add(major_radius=rad, minor_radius=rad * 0.075, location=pos, rotation=(math.radians(90), 0, 0))
        ring = bpy.context.object
        ring.data.materials.append(emission_mat("ring", hex_rgba(c.get("color", "#ff1f3d")), 1.0))
        ring.pass_index = 1

    # lights: soft key, fill, two coloured rims behind the subject side
    def light(loc, energy, color=(1, 1, 1), size=3.0, target=(0, 0, 0)):
        ld = bpy.data.lights.new("l", "AREA")
        ld.energy, ld.color, ld.size = energy, color[:3], size
        lo = bpy.data.objects.new("l", ld)
        lo.location = loc
        bpy.context.collection.objects.link(lo)
        lo.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()

    sxs = 1 if side == "right" else -1
    light((-3 * sxs, -8, 5), 500, size=6, target=(sx, 1, 0))
    light((4 * sxs, -6, -1), 150, size=5, target=(sx, 1, 0))
    light((sx + 4 * sxs, 4, 3), 1400, hex_rgba(glow_col), size=2.0, target=(sx, 1, 0))
    light((sx - 3 * sxs, 4, 2), 1000, hex_rgba(spec.get("accent2", "#ffffff")), size=2.0, target=(sx, 1, 0))

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = spec.get("samples", 48)
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.render.resolution_x, scene.render.resolution_y = W, H
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = spec.get("exposure", 0.0)
    compositor(scene, spec, W)

    out = spec.get("out", os.path.join(os.path.expanduser("~"), "thumbnail.jpg"))
    is_jpg = out.lower().endswith((".jpg", ".jpeg"))
    scene.render.image_settings.file_format = "JPEG" if is_jpg else "PNG"
    if is_jpg:
        scene.render.image_settings.quality = 93
    scene.render.filepath = out
    return scene


def compositor(scene, spec, W):
    """Hard drop shadow + thick black outline around the text (index 1) and a white stroke around 3D
    props (index 2), then a touch of contrast. Pure 2D, so strokes are clean on any font."""
    scene.view_layers[0].use_pass_object_index = True
    scene.use_nodes = True
    nt = scene.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    rl = nt.nodes.new("CompositorNodeRLayers")

    def idmask(index):
        n = nt.nodes.new("CompositorNodeIDMask")
        n.index = index
        n.use_antialiasing = True
        nt.links.new(rl.outputs["IndexOB"], n.inputs["ID value"])
        return n.outputs["Alpha"]

    def grow(mask, px):
        d = nt.nodes.new("CompositorNodeDilateErode")
        d.mode = "DISTANCE"
        d.distance = int(px)
        nt.links.new(mask, d.inputs["Mask"])
        b = nt.nodes.new("CompositorNodeBlur")
        b.filter_type = "GAUSS"
        b.size_x = b.size_y = 1
        nt.links.new(d.outputs["Mask"], b.inputs["Image"])
        return b.outputs["Image"]

    def paint(base, fac, color):
        mx = nt.nodes.new("CompositorNodeMixRGB")
        nt.links.new(fac, mx.inputs["Fac"])
        nt.links.new(base, mx.inputs[1])
        mx.inputs[2].default_value = color
        return mx.outputs["Image"]

    def keep(base, fac, original):
        mx = nt.nodes.new("CompositorNodeMixRGB")
        nt.links.new(fac, mx.inputs["Fac"])
        nt.links.new(base, mx.inputs[1])
        nt.links.new(original, mx.inputs[2])
        return mx.outputs["Image"]

    text = idmask(1)
    props = idmask(2)
    stroke = round(W * spec.get("stroke_width", 0.013))

    # drop shadow: the grown text silhouette, shifted down-right and softened, darkens the image
    tr = nt.nodes.new("CompositorNodeTranslate")
    tr.inputs["X"].default_value = W * 0.007
    tr.inputs["Y"].default_value = -W * 0.009
    nt.links.new(grow(text, stroke), tr.inputs["Image"])
    blur = nt.nodes.new("CompositorNodeBlur")
    blur.filter_type = "FAST_GAUSS"
    blur.size_x = blur.size_y = int(W * 0.006)
    nt.links.new(tr.outputs["Image"], blur.inputs["Image"])
    amt = nt.nodes.new("CompositorNodeMath")
    amt.operation = "MULTIPLY"
    amt.inputs[1].default_value = spec.get("shadow", 0.8)
    nt.links.new(blur.outputs["Image"], amt.inputs[0])
    img = paint(rl.outputs["Image"], amt.outputs[0], (0, 0, 0, 1))

    # white stroke around 3D props (props stay on top of it)
    img = keep(paint(img, grow(props, round(W * 0.006)), (1, 1, 1, 1)), props, rl.outputs["Image"])

    # text: optional outer ring, black outline, then the text itself back on top
    if spec.get("outer_stroke"):
        img = paint(img, grow(text, stroke + round(W * 0.006)), hex_rgba(spec["outer_stroke"]))
    img = paint(img, grow(text, stroke), hex_rgba(spec.get("stroke", "#000000")))
    img = keep(img, text, rl.outputs["Image"])

    cc = nt.nodes.new("CompositorNodeBrightContrast")
    cc.inputs["Contrast"].default_value = spec.get("contrast", 6)
    nt.links.new(img, cc.inputs["Image"])
    comp = nt.nodes.new("CompositorNodeComposite")
    nt.links.new(cc.outputs["Image"], comp.inputs["Image"])


def main(spec):
    build(spec)
    bpy.ops.render.render(write_still=True)
    if spec.get("blend"):
        bpy.ops.wm.save_as_mainfile(filepath=spec["blend"])
    print("THUMB_OK", spec.get("out"))


if "SPEC" in globals():  # sent through MCP for Blender (execute_code runs without __name__)
    main(SPEC)  # noqa: F821
elif globals().get("__name__") == "__main__" and "--" in sys.argv:
    main(json.load(open(sys.argv[sys.argv.index("--") + 1])))
