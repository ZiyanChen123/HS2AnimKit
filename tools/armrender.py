"""手臂轴倾 A/B 渲染：同一帧分别用 无轴倾 / 正 / 负 三种算法出图。

    HS2_AXIS_SIGN=0 blender -b -P tools/armrender.py -- 112 out_a.png
"""

import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
VMD = os.path.join(HERE, "小鹿乱撞.vmd")
OUT = os.path.join(HERE, "_test", "arm")


def look_at(cam, target):
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    frame = int(argv[0]) if argv else 112
    name = argv[1] if len(argv) > 1 else "arm.png"
    os.makedirs(OUT, exist_ok=True)

    import addon_utils
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    from hs2_rig_kit import ops

    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    p.game_dir = p.game_dir or ops.detect_game_dir()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    bpy.ops.hs2.load_default(cleanup=True, include_head=True)

    mat = bpy.data.materials.new("skin")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.82, 0.66, 0.57, 1)
    mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.7
    for nm in ("HS2_Body", "HS2_Head"):
        ob = bpy.data.objects.get(nm)
        if ob and not ob.data.materials:
            ob.data.materials.append(mat)

    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.ops.hs2.import_vmd(filepath=VMD)
    scn = bpy.context.scene
    scn.frame_set(frame)

    # 相机：正面偏右 30°，看上半身
    cam_d = bpy.data.cameras.new("C")
    cam = bpy.data.objects.new("C", cam_d)
    scn.collection.objects.link(cam)
    target = Vector((0.0, 0.0, 1.25))
    cam.location = target + Vector((1.3, -2.2, 0.35))
    cam_d.lens = 60
    look_at(cam, target)
    scn.camera = cam

    for nm, loc, e in (("K1", (2, -3, 3), 600), ("K2", (-2, -2, 1.5), 250)):
        ld = bpy.data.lights.new(nm, "AREA")
        ld.energy = e
        ld.size = 3
        lo = bpy.data.objects.new(nm, ld)
        lo.location = loc
        look_at(lo, target)
        scn.collection.objects.link(lo)

    scn.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in [
        i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    ] else "BLENDER_EEVEE"
    scn.render.resolution_x = 640
    scn.render.resolution_y = 800
    scn.render.film_transparent = False
    scn.render.filepath = os.path.join(OUT, name)
    bpy.ops.render.render(write_still=True)
    print("[R] 输出:", scn.render.filepath)


main()
