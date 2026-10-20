"""渲染 VMD / Mixamo 重定向结果，用来看姿势对不对（尤其是脚）。

    blender -b --factory-startup -P tools/render_retarget.py
"""

import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import addon_utils
addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)

from hs2_rig_kit import ops  # noqa: E402

OUT = os.path.join(HERE, "_test", "retarget")
VMD = os.path.join(HERE, "小鹿乱撞.vmd")
FBX = os.path.join(HERE, "Stomp.fbx")


def setup_scene():
    scn = bpy.context.scene
    # 清掉 startup 自带的 Cube / Light / Camera（Cube 会把人物下半身罩住）
    for ob in list(bpy.data.objects):
        if ob.name in {"Cube", "Light", "Camera"} or ob.name.startswith("Cube."):
            bpy.data.objects.remove(ob, do_unlink=True)
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scn.render.engine = eng
            break
        except Exception:
            continue
    scn.render.resolution_x = 460
    scn.render.resolution_y = 640
    scn.render.image_settings.file_format = "PNG"
    if scn.world is None:
        scn.world = bpy.data.worlds.new("W")
    scn.world.use_nodes = True
    bg = scn.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.09, 0.09, 0.10, 1)
        bg.inputs[1].default_value = 1.2

    for name, loc, energy in (("K", (2.4, -3.0, 3.2), 900),
                              ("F", (-2.6, -2.4, 2.0), 400),
                              ("B", (0.0, 3.0, 2.6), 450)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.size = 3.0
        lo = bpy.data.objects.new(name, ld)
        lo.location = loc
        lo.rotation_euler = (Vector((0, 0, 0.9)) - Vector(loc)
                             ).to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.collection.objects.link(lo)

    bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, -0.002))
    gnd = bpy.context.object
    m = bpy.data.materials.new("G")
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.10, 0.10, 0.12, 1)
    m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
    gnd.data.materials.append(m)

    cd = bpy.data.cameras.new("C")
    cd.lens = 50
    cam = bpy.data.objects.new("C", cd)
    bpy.context.scene.collection.objects.link(cam)
    scn.camera = cam
    return scn, cam


def aim(cam, target, dist, height, yaw_deg=0.0):
    a = math.radians(yaw_deg)
    cam.location = Vector((math.sin(a) * dist, -math.cos(a) * dist, height))
    cam.rotation_euler = (Vector(target) - cam.location
                          ).to_track_quat("-Z", "Y").to_euler()


def shot(scn, name):
    p = os.path.join(OUT, name + ".png")
    scn.render.filepath = p
    bpy.ops.render.render(write_still=True)
    print("[shot]", p)


def paint_body():
    for nm, col in (("HS2_Body", (0.74, 0.63, 0.56, 1)),
                    ("HS2_Head", (0.76, 0.65, 0.58, 1))):
        ob = bpy.data.objects.get(nm)
        if ob is None:
            continue
        m = bpy.data.materials.new("Skin_" + nm)
        m.use_nodes = True
        bs = m.node_tree.nodes["Principled BSDF"]
        bs.inputs["Base Color"].default_value = col
        bs.inputs["Roughness"].default_value = 0.62
        ob.data.materials.clear()
        ob.data.materials.append(m)


def render_action(scn, cam, tag, total):
    """均匀取 5 帧，拍正面全身（看整体姿势对不对）。"""
    frames = [max(1, int(total * i / 5.0)) for i in range(5)]
    for k, f in enumerate(frames):
        scn.frame_set(f)
        aim(cam, (0, 0, 0.85), 4.8, 1.15, 0)
        shot(scn, "%s_f%d_%04d" % (tag, k, f))


def main():
    os.makedirs(OUT, exist_ok=True)
    scn, cam = setup_scene()

    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=True)
    arm = bpy.data.objects["HS2_Default"]
    arm.hide_render = True
    scn.render.fps = 30
    paint_body()

    if os.path.isfile(VMD):
        r = bpy.ops.hs2.import_vmd(filepath=VMD, scale=0.08,
                                   follow=False, use_dummy=False)
        print("[render] VMD(direct)", r, "frames", scn.frame_end)
        render_action(scn, cam, "vmd_direct", scn.frame_end)

        # 虚拟骨架模式（复刻 MMDD）
        bpy.ops.hs2.clear_rig()
        r = bpy.ops.hs2.import_vmd(filepath=VMD, scale=0.08,
                                   follow=False, use_dummy=True)
        print("[render] VMD(dummy)", r, "frames", scn.frame_end)
        render_action(scn, cam, "vmd_dummy", scn.frame_end)

    if os.path.isfile(FBX):
        r = bpy.ops.hs2.import_mixamo(filepath=FBX, with_fingers=True,
                                      follow=False, keep_source=False)
        print("[render] Mixamo", r, "frames", scn.frame_end)
        render_action(scn, cam, "mixamo", scn.frame_end)

    print("[render] done ->", OUT)


main()
