"""渲染检查：素模（身体 + 头）在静止姿势和一个动作帧下的样子。

    blender -b --factory-startup -P tools/dev_render.py -- [frame] [out.png]
"""
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

GAME = os.environ.get("HS2_GAME_DIR", "D:/game/HS2")
OUT = os.path.join(HERE, "_test")


def add_material(obj, color):
    me = obj.data
    mat = bpy.data.materials.get("HS2_Skin")
    if mat is None:
        mat = bpy.data.materials.new("HS2_Skin")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.62
    if not me.materials:
        me.materials.append(mat)


def look_at(cam, target):
    d = target - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main():
    for ob in list(bpy.data.objects):
        if ob.name in {"Cube", "Light", "Camera"}:
            bpy.data.objects.remove(ob, do_unlink=True)

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    frame = int(argv[0]) if argv else 0
    name = argv[1] if len(argv) > 1 else "render.png"

    bpy.ops.preferences.addon_enable(module="hs2_rig_kit")
    bpy.context.preferences.addons["hs2_rig_kit"].preferences.game_dir = GAME
    bpy.ops.hs2.load_default()

    for nm in ("HS2_Body", "HS2_Head"):
        ob = bpy.data.objects.get(nm)
        if ob is not None:
            add_material(ob, (0.82, 0.66, 0.57, 1.0))

    if frame:
        bpy.ops.hs2.import_clip(clip_name="swim_Idle_00_L", fps=30.0, follow=False)
        bpy.ops.hs2.add_rig(follow=True)
        bpy.context.scene.frame_set(frame)

    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.samples = 24
    scn.cycles.use_denoising = True
    scn.render.resolution_x = 540
    scn.render.resolution_y = 760
    scn.render.film_transparent = False
    scn.world = bpy.data.worlds.new("W")
    scn.world.use_nodes = True
    scn.world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.06, 1)
    scn.world.node_tree.nodes["Background"].inputs[1].default_value = 0.6

    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam_data.lens = 42
    cam.location = Vector((0.0, -4.2, 0.95))
    look_at(cam, Vector((0.0, 0.0, 0.88)))
    scn.camera = cam

    key = bpy.data.lights.new("Key", "AREA")
    key.energy = 420
    key.size = 2.2
    kob = bpy.data.objects.new("Key", key)
    bpy.context.collection.objects.link(kob)
    kob.location = Vector((-2.2, -2.6, 2.6))
    kob.visible_camera = False
    look_at(kob, Vector((0, 0, 1.0)))

    fill = bpy.data.lights.new("Fill", "AREA")
    fill.energy = 160
    fill.size = 3.0
    fob = bpy.data.objects.new("Fill", fill)
    bpy.context.collection.objects.link(fob)
    fob.location = Vector((2.4, -2.0, 1.4))
    fob.visible_camera = False
    look_at(fob, Vector((0, 0, 0.9)))

    scn.render.filepath = os.path.join(OUT, name)
    bpy.ops.render.render(write_still=True)
    print("[render]", scn.render.filepath)


main()
