"""渲染一张 H 场景（女角 + 男角 + 道具）看看对不对。

    blender -b --factory-startup -P tools/hrender.py -- "男拘束フェラ" M_WLoop1
"""
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api  # noqa: E402


def look_at(cam, target):
    d = target - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    label = argv[0] if argv else "拘束玩具攻め"
    stage = argv[1] if len(argv) > 1 else None
    out = os.path.join(HERE, "_test", "out", "h_scene.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    m = api.find_mode(label)
    if m is None:
        raise SystemExit("没找到 H 动作 %r" % label)
    api.setup_cast(m)            # 按动作的人数/性别自动补角色
    api.load_h(m, stage, with_props=True)
    res = {"mode": m, "props": [o for o in bpy.data.objects
                                if o.get("hs2_prop_mode")],
           "stage": {"cn": ""}}
    print("[render] %s · %s · 道具 %d" % (res["mode"]["label"],
                                         res.get("stage", {}).get("cn"),
                                         len(res["props"])))

    scn = bpy.context.scene
    scn.frame_set(10)
    bpy.context.view_layer.update()

    # 皮肤材质
    skin = bpy.data.materials.new("HS2_Skin")
    skin.use_nodes = True
    bsdf = skin.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.85, 0.72, 0.66, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.6
    for ob in bpy.data.objects:
        if ob.type == "MESH" and not ob.get("hs2_prop") and not ob.data.materials:
            ob.data.materials.append(skin)

    light_data = bpy.data.lights.new("L", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("L", light_data)
    bpy.context.collection.objects.link(light)
    light.rotation_euler = (math.radians(55), 0, math.radians(35))

    cam_data = bpy.data.cameras.new("C")
    cam_data.lens = 50
    cam = bpy.data.objects.new("C", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector((1.9, -2.4, 1.35))
    look_at(cam, Vector((0.0, 0.0, 0.85)))
    scn.camera = cam

    scn.render.engine = "BLENDER_WORKBENCH"
    scn.render.resolution_x = 900
    scn.render.resolution_y = 700
    scn.render.filepath = out
    try:
        scn.display.shading.light = "STUDIO"
        scn.display.shading.color_type = "MATERIAL"
        scn.display.shading.show_shadows = True
    except Exception:
        pass
    bpy.ops.render.render(write_still=True)
    print("[render] ->", out)


if __name__ == "__main__":
    main()
