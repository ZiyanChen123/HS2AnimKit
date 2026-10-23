"""Blender 操作符。"""

import contextlib
import os
import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from mathutils import Matrix, Vector
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import anim, bdata, compat, handpose, mmdrig, retarget, rig
from . import vmd as vmdlib
from .ripper import GameAssets
from .ripper import hdb as _hdb
from .ripper import mesh as _mesh

_ASSETS = {}
STATE = {}

ARM_NAME = "Armature"
BODY_NAME = "HS2_Body"
HEAD_NAME = "HS2_Head"


# --------------------------------------------------------------------------
# 环境
# --------------------------------------------------------------------------

def get_assets(game_dir):
    key = os.path.abspath(game_dir)
    if key not in _ASSETS:
        _ASSETS[key] = GameAssets(key)
    return _ASSETS[key]


def addon_prefs(context):
    try:
        return context.preferences.addons[__package__].preferences
    except Exception:
        return None


def detect_game_dir():
    """猜游戏目录：环境变量 → 插件所在目录往上找 → 几个常见安装位置。"""
    env = os.environ.get("HS2_GAME_DIR")
    if env and os.path.isfile(os.path.join(env, "abdata", "chara", "oo_base.unity3d")):
        return env.replace("\\", "/")
    cands = []
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cur = here
    for _ in range(4):
        cands.append(cur)
        cur = os.path.dirname(cur)
    for drive in ("D", "E", "C", "F"):
        cands += ["%s:/game/HS2" % drive, "%s:/HS2" % drive,
                  "%s:/game/HoneySelect2" % drive]
    for c in cands:
        if os.path.isfile(os.path.join(c, "abdata", "chara", "oo_base.unity3d")):
            return c.replace("\\", "/")
    return ""


def game_dir_of(context):
    p = addon_prefs(context)
    d = p.game_dir if p and p.game_dir else ""
    d = bpy.path.abspath(d) if d else ""
    if d and os.path.isfile(os.path.join(d, "abdata", "chara", "oo_base.unity3d")):
        return d
    auto = detect_game_dir()
    if auto and p is not None and not p.game_dir:
        p.game_dir = auto
    return auto or d


def _is_arm(ob):
    try:
        return (ob is not None
                and ob.name in bpy.data.objects
                and ob.type == "ARMATURE"
                and ob.data.bones.get("cf_J_Root") is not None)
    except Exception:
        return False


def current_arm():
    """当前操作的骨架：**选中的**优先，其次默认那个，最后随便一个。"""
    ob = bpy.context.view_layer.objects.active
    if _is_arm(ob):
        return ob
    for sel in bpy.context.selected_objects:
        if _is_arm(sel):
            return sel
    arm = bpy.data.objects.get(ARM_NAME)
    if _is_arm(arm):
        return arm
    for ob in bpy.data.objects:
        if ob.type == "ARMATURE" and ob.data.bones.get("cf_J_Root"):
            return ob
    return None


def game_dir_from_arm(arm):
    """骨架身上记着它来自哪个游戏目录，重开 .blend 也不会丢。"""
    d = (arm.get("hs2_game_dir") if arm is not None else "") or ""
    if d and os.path.isfile(os.path.join(d, "abdata", "chara",
                                         "oo_base.unity3d")):
        return d
    return detect_game_dir() or game_dir_of(bpy.context)


def _tag_redraw(context):
    try:
        for area in context.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


def remove_character(arm):
    """删角色：骨架 + 它自己的身体/头部网格 + 身上的衣服（不动别的角色）。"""
    if arm is None:
        return False
    try:
        from . import clothes as _clothes
        _clothes.clear_character(arm)
    except Exception:
        pass
    try:
        remove_male_genital(arm)
    except Exception:
        pass
    for key in ("hs2_body", "hs2_head"):
        nm = arm.get(key)
        if nm:
            _remove_object(nm)
    _remove_object(arm.name)
    return True


def rename_character(arm, name):
    """给角色改个名（列表里显示用），连带网格一起改。"""
    old = arm.name
    if not name or name == old:
        return False
    body = arm.get("hs2_body") or ""
    head = arm.get("hs2_head") or ""
    arm.name = name
    if body:
        ob = bpy.data.objects.get(body)
        if ob is not None:
            ob.name = body.replace(old, name, 1) if old in body else body
            arm["hs2_body"] = ob.name
    if head:
        ob = bpy.data.objects.get(head)
        if ob is not None:
            ob.name = head.replace(old, name, 1) if old in head else head
            arm["hs2_head"] = ob.name
    return True


def _remove_object(name):
    ob = bpy.data.objects.get(name)
    if ob is not None:
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and getattr(data, "users", 0) == 0:
            if isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)


def _genital_material():
    mat = bpy.data.materials.get("HS2_MaleGenitalSkin")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("HS2_MaleGenitalSkin")
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.82, 0.64, 0.55, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
    mat.diffuse_color = (0.82, 0.64, 0.55, 1.0)
    return mat


def _card_skin_material(color, gloss=None):
    """角色卡肤色材质；保证身体/头部都有可见材质槽。"""
    def lin(x):
        try:
            x = max(0.0, min(1.0, float(x)))
        except Exception:
            return 1.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    c = list(color or [0.95, 0.85, 0.82, 1.0])
    if len(c) < 3:
        c = [0.95, 0.85, 0.82, 1.0]
    rgba = (lin(c[0]), lin(c[1]), lin(c[2]), 1.0)
    name = "HS2_CardSkin"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgba
        try:
            g = float(gloss) if gloss is not None else 0.6
            g = max(0.05, min(1.0, g))
            bsdf.inputs["Roughness"].default_value = max(0.25, 1.0 - g)
        except Exception:
            bsdf.inputs["Roughness"].default_value = 0.55
    mat.diffuse_color = rgba
    return mat


def _assign_skin_material(mesh_obj, mat):
    if mesh_obj is None or getattr(mesh_obj, "data", None) is None:
        return
    me = mesh_obj.data
    me.materials.clear()
    me.materials.append(mat)
    for poly in me.polygons:
        poly.material_index = 0


def has_male_genital(arm):
    if arm is None:
        return False
    name = arm.get("hs2_genital_coll")
    return bool(name and bpy.data.collections.get(name))


def ensure_male_genital(arm):
    """H 场景里给男角色补上 oo_base 的 cm_o_dan00 / cm_o_dan_f。"""
    if arm is None or arm.get("hs2_gender") != "male":
        return None
    if has_male_genital(arm):
        return bpy.data.collections.get(arm.get("hs2_genital_coll"))
    try:
        g = STATE.get("game") or get_assets(game_dir_from_arm(arm))
        items = g.male_genital() if g is not None else []
    except Exception as e:
        print("[HS2] 男性生殖器加载失败：%s" % e)
        return None
    if not items:
        return None
    skel = STATE.get("skel") or g.skeleton()
    names = bdata._unique_names(skel)
    coll = bpy.data.collections.new("HS2_Genital_" + arm.name)
    bpy.context.scene.collection.children.link(coll)
    mat = _genital_material()
    scale = float(arm.get("hs2_scale") or 0.1)
    for item in items:
        md = dict(item["mesh"])
        md["flip"] = _mesh.needs_flip(md)
        obj = bdata.build_mesh(
            md, skel, names, arm,
            name="HS2_Penis_%s_%s" % (arm.name, item["name"]),
            scale=scale, collection=coll)
        obj.data.materials.append(mat)
        obj["hs2_genital"] = True
        obj["hs2_genital_for"] = arm.name
    arm["hs2_genital_coll"] = coll.name
    return coll


def remove_male_genital(arm):
    if arm is None:
        return
    name = arm.get("hs2_genital_coll")
    coll = bpy.data.collections.get(name) if name else None
    if coll is None:
        prefix = "HS2_Genital_" + arm.name
        for c in bpy.data.collections:
            if c.name == prefix or c.name.startswith(prefix + "."):
                coll = c
                break
    if coll is not None:
        for obj in list(coll.objects):
            _remove_object(obj.name)
        try:
            bpy.data.collections.remove(coll)
        except Exception:
            pass
    try:
        del arm["hs2_genital_coll"]
    except Exception:
        pass


# --------------------------------------------------------------------------
# 加载角色
# --------------------------------------------------------------------------

class HS2_OT_detect_dir(bpy.types.Operator):
    """自动找游戏目录（插件所在盘 + 常见安装位置）"""
    bl_idname = "hs2.detect_dir"
    bl_label = "自动检测游戏目录"

    def execute(self, context):
        p = addon_prefs(context)
        found = detect_game_dir()
        if not found:
            self.report({"ERROR"}, "没找到，请手动选 abdata 所在的目录")
            return {"CANCELLED"}
        if p is not None:
            p.game_dir = found
        self.report({"INFO"}, "游戏目录：%s" % found)
        _tag_redraw(context)
        return {"FINISHED"}


def taken_slots():
    """场景里已经占用的角色槽（女1/女2/男1/男2）。"""
    out = set()
    for ob in bpy.data.objects:
        if ob.type == "ARMATURE" and ob.get("hs2_slot"):
            out.add(ob.get("hs2_slot"))
    return out


def free_slot(gender):
    """给这个性别找个空槽；两个都占了就 None。"""
    order = ("f1", "f2") if gender == "female" else ("m1", "m2")
    taken = taken_slots()
    for sl in order:
        if sl not in taken:
            return sl
    return None


def base_gray_material():
    """素模用的中性灰材质，避免身体/头是纯白，衣服颜色看不准。"""
    mat = bpy.data.materials.get("HS2_BaseGray")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("HS2_BaseGray")
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == "BSDF_PRINCIPLED"), None)
    gray = (0.28, 0.28, 0.28, 1.0)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = gray
        try:
            bsdf.inputs["Roughness"].default_value = 0.55
        except Exception:
            pass
    mat.diffuse_color = gray
    return mat


def _apply_base_gray(obj):
    if obj is None or obj.type != "MESH":
        return
    mat = base_gray_material()
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def build_default(g, include_head=True, scale=0.1, cleanup=True,
                  body_mesh_name=None, name=None, gender="female", slot=None,
                  head_bundle=None, head_prefab=None):
    """加载默认角色（骨架 + 身体 + 头）。

    返回 (arm, names, body, head)；操作符和 :mod:`api` 都走这一个入口。
    ``body_mesh_name`` 可以换成 ``o_body_cm``（男角素体）。
    加载完会在骨架上记下 **槽位和性别** —— H 动作的阵容判断全靠它。
    """
    skel = g.skeleton()
    if body_mesh_name is None and gender == "male":
        body_mesh_name = _mesh.BODY_MALE_MESH
    data = g.body_mesh(body_mesh_name)

    head = None
    if include_head:
        try:
            if gender == "male":
                head = g.head(bundle_name=head_bundle or "mo_head_00",
                              prefab=head_prefab or "p_cm_head_00")
            else:
                head = g.head(bundle_name=head_bundle or "fo_head_00",
                              prefab=head_prefab or "p_cf_head_00")
        except Exception as e:
            print("[HS2] 头部没读到：%s" % e)

    arm_name = name or ARM_NAME
    if arm_name == ARM_NAME:
        body_name, head_name = BODY_NAME, HEAD_NAME
    else:
        tail = arm_name.replace("Armature", "").strip("_") or "x"
        body_name = "%s_%s" % (BODY_NAME, tail)
        head_name = "%s_%s" % (HEAD_NAME, tail)
    if cleanup:
        # 骨架名现在是很通用的 "Armature"，别把场景里别人同名的骨架当成旧的删掉
        if _is_arm(bpy.data.objects.get(arm_name)):
            _remove_object(arm_name)
        for nm in (body_name, head_name):
            _remove_object(nm)

    extra = []
    # 体模额外骨（高面数/带阴道骨等）：body mesh 的 bones 里可能有骨架
    # 没有的骨，先把它们补进骨架，否则权重会丢。
    try:
        known = {b.name for b in skel.bones}
        seen = set()
        for i, path in enumerate(data.get("bones") or []):
            name = str(path).split("/")[-1]
            if not name or name in known or name in seen:
                continue
            par = str(path).split("/")[-2] if "/" in str(path) else None
            w = data.get("bindpose") or []
            if i >= len(w):
                continue
            try:
                world = Matrix([w[i][j:j + 4] for j in range(0, 16, 4)])
            except Exception:
                continue
            extra.append({"name": name, "parent": par,
                          "world": world, "deform": True})
            seen.add(name)
    except Exception as e:
        print("[HS2] 体模额外骨读取失败：%s" % e)
    if head:
        extra += [{"name": b["name"], "parent": b["parent"],
                   "world": b["world"], "deform": b["deform"]}
                  for b in head["bones"]]

    arm, names = bdata.build_armature(skel, name=arm_name,
                                      scale=scale, extra=extra or None)
    arm["hs2_game_dir"] = g.game_dir
    arm["hs2_gender"] = gender
    arm["hs2_slot"] = slot or free_slot(gender) or (
        "f1" if gender == "female" else "m1")
    arm["hs2_body"] = body_name
    arm["hs2_head"] = head_name
    body = bdata.build_mesh(data, skel, names, arm, name=body_name,
                            scale=scale)
    _apply_base_gray(body)

    head_obj = None
    if head:
        hd = dict(head["mesh"])
        hd["group_names"] = hd.get("bone_names") or []
        head_obj = bdata.build_mesh(hd, skel, names, arm, name=head_name,
                                    scale=scale, xform=head["attach"])
        _apply_base_gray(head_obj)

    STATE.update(names=names, skel=skel, arm=arm, mesh=body, head=head_obj,
                 game=g, scale=scale)

    for ob in list(bpy.context.selected_objects):
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    return arm, names, body, head_obj


_CLOTHES_CATALOG = {}


def import_character_fbx(path, game_dir=None, male=None, align=True):
    """导入工作室 HS2MeshExporter 导出的角色 FBX。

    做三件事（细节见 :mod:`fbxrig`）：

    1. 按 FBX 里的 BindPose 重建 rest 骨，骨头和蒙皮严格对齐
    2. 把骨架摆成 p_cf_anim 默认骨架的姿势（T-pose + 直手）
    3. 标记成插件角色，之后套动作走 :func:`fbxrig.apply`

    返回 ``(arm, info)``。
    """
    from . import fbxrig

    before = {o.name for o in bpy.data.objects}
    # ignore_leaf_bones 必须关掉：HS2 的手指末节（cf_J_Hand_Index03_L 等）、
    # _s 副骨、眼球/瞳孔骨、vagina 骨都是叶子骨，而且是真正带权重的骨。
    # 开着它导入器会把这些骨删掉、权重并到别的骨上，蒙皮就少一大块。
    bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=True,
                             ignore_leaf_bones=False, use_anim=False)
    new = [o for o in bpy.data.objects if o.name not in before]
    arm = next((o for o in new if o.type == "ARMATURE"
                and o.data.bones.get("cf_J_Root")), None)
    if arm is None:
        arm = next((o for o in new if o.type == "ARMATURE"), None)
    if arm is None:
        raise RuntimeError("FBX 里没有骨架")

    # 工作室导出是 1/100，插件角色是 1/10（骨头和网格一起缩放，相对关系不变）
    try:
        arm.scale = (0.1, 0.1, 0.1)
    except Exception:
        pass
    bpy.context.view_layer.update()

    meshes = [o for o in new if o.type == "MESH"]
    for ob in meshes:
        try:
            if ob.parent is not arm:
                mw = ob.matrix_world.copy()
                ob.parent = arm
                ob.matrix_world = mw
        except Exception:
            pass

    # 清空工作室留下的空物体、相机、灯、Cube
    for ob in list(new):
        if ob is arm or ob in meshes:
            continue
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for ob in list(bpy.data.objects):
        if (ob.type == "MESH" and ob.name.startswith("Cube")
                and not ob.vertex_groups):
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass

    # ---- 1. 按 BindPose 修 rest ----
    bind = fbxrig.read_bindpose(path)
    fix = fbxrig.fix_rest(arm, bind)
    print("[HS2] FBX 骨架按 BindPose 重建：%d 根，平均移动 %.4f"
          % (fix.get("fixed", 0), fix.get("avg", 0.0)))

    # ---- 1b. 根骨收口：工作室会在外面套一层 BodyTop，删掉，让 p_cf_anim 做根 ----
    pruned = fbxrig.prune_root(arm)

    # 单位保护：正常角色 1.0 ~ 2.2m。超出说明导出器换了单位，
    # 按 1.5m 折算一次，别让角色变成 14m 或 1.4cm。
    try:
        hs = []
        for ob in meshes:
            hs += [(ob.matrix_world @ Vector(c)).z for c in ob.bound_box]
        if hs:
            h = max(hs) - min(hs)
            if h > 1e-6 and not (0.9 < h < 2.4):
                f = 1.5 / h
                arm.scale = tuple(s * f for s in arm.scale)
                bpy.context.view_layer.update()
                print("[HS2] FBX 单位异常（角色高 %.3fm），整体缩放 x%.3f" % (h, f))
    except Exception as e:
        print("[HS2] FBX 单位检查跳过：%s" % e)

    body = next((o for o in meshes if "o_body" in o.name.lower()), None)
    head = next((o for o in meshes if o.name.lower().startswith("o_head")), None)

    if male is None:
        bname = (body.name.lower() if body is not None else "")
        male = ("_cm" in bname or bname.endswith("cm"))
    gender = "male" if male else "female"
    slot = free_slot(gender) or ("m1" if male else "f1")
    arm["hs2_custom"] = True
    arm["hs2_gender"] = gender
    arm["hs2_slot"] = slot
    try:
        arm["hs2_scale"] = float(arm.scale.x)
    except Exception:
        arm["hs2_scale"] = 0.1
    arm["hs2_import_fbx"] = os.path.abspath(path)
    if game_dir:
        arm["hs2_game_dir"] = os.path.abspath(game_dir)
    if body is not None:
        arm["hs2_body"] = body.name
    if head is not None:
        arm["hs2_head"] = head.name

    # ---- 2. 对齐默认骨架姿势 ----
    aligned = 0
    ratio = 1.0
    if align and game_dir:
        try:
            skel = get_assets(game_dir).skeleton()
            _act, ainfo = fbxrig.apply(arm, skel, clip=None, scale=0.1)
            aligned = ainfo["bones"]
            ratio = ainfo["height_ratio"]
        except Exception as e:
            print("[HS2] 对齐默认骨架失败：%s" % e)

    # FBX 导出后个别骨长度异常，octahedral 显示会像大锥体，看着像错位
    try:
        arm.data.display_type = "STICK"
        arm.show_in_front = True
    except Exception:
        pass

    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    return arm, {"meshes": len(meshes), "scale": 0.1,
                 "body": body.name if body else "",
                 "head": head.name if head else "",
                 "fixed_bones": fix.get("fixed", 0),
                 "pruned": pruned,
                 "aligned": aligned,
                 "height_ratio": ratio,
                 "bind_pose": bool(bind)}


class HS2_OT_import_character_fbx(bpy.types.Operator, ImportHelper):
    """导入工作室角色 FBX：按 BindPose 修骨架 + 对齐默认骨架姿势"""
    bl_idname = "hs2.import_character_fbx"
    bl_label = "导入工作室角色 FBX"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})
    male: BoolProperty(name="男角色", default=False)
    align: BoolProperty(name="对齐默认骨架姿势", default=True,
                        description="把角色摆成 p_cf_anim 默认姿势"
                                    "（T-pose、手指伸直）")

    def invoke(self, context, event):
        if not self.filepath:
            gd = game_dir_of(context)
            if gd:
                self.filepath = os.path.join(gd, "Export", "")
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "male")
        col.prop(self, "align")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "找不到 FBX：%s" % path)
            return {"CANCELLED"}
        try:
            arm, info = import_character_fbx(path, game_dir_of(context),
                                             male=self.male, align=self.align)
        except Exception as e:
            self.report({"ERROR"}, "FBX 导入失败：%s" % e)
            return {"CANCELLED"}
        msg = ("已导入 %s：%d 个网格，修骨 %d 根"
               % (arm.name, info["meshes"], info["fixed_bones"]))
        if info.get("pruned"):
            msg += "，根骨收口 %s" % "/".join(info["pruned"])
        if info["aligned"]:
            msg += "，对齐 %d 根（身高 %.0f%%）" % (
                info["aligned"], info["height_ratio"] * 100.0)
        if not info["bind_pose"]:
            msg += "（没读到 BindPose，骨架可能对不齐）"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class HS2_OT_align_fbx_pose(bpy.types.Operator):
    """把工作室 FBX 角色的骨架摆成默认骨架姿势（T-pose + 直手）"""
    bl_idname = "hs2.align_fbx_pose"
    bl_label = "对齐默认骨架姿势"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import fbxrig
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "先选一个角色骨架")
            return {"CANCELLED"}
        gd = game_dir_from_arm(arm)
        if not gd:
            self.report({"ERROR"}, "不知道游戏目录，先在设置里指定")
            return {"CANCELLED"}
        try:
            skel = get_assets(gd).skeleton()
            _act, info = fbxrig.apply(arm, skel, clip=None, scale=0.1)
        except Exception as e:
            self.report({"ERROR"}, "对齐失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "已对齐 %d 根骨（身高 %.0f%%）"
                    % (info["bones"], info["height_ratio"] * 100.0))
        return {"FINISHED"}


def _clothing_catalog(game_dir):
    key = os.path.abspath(game_dir)
    if key not in _CLOTHES_CATALOG:
        from .ripper import clothes as _rc
        _CLOTHES_CATALOG[key] = _rc.load_catalog(game_dir)
    return _CLOTHES_CATALOG[key]


def load_character_card(path, game_dir):
    """从 HS2 PNG 角色卡加载身体/头部/衣服。

    骨架仍然用插件的 p_cf_anim（默认手型微弯），不经过 MeshExporter 的
    T-pose；卡里的 shapeValueBody[0] 连续映射到 cf_N_height，衣服按
    Coordinate 的 8 个槽位还原。面部/头发数据已经解析并挂在 arm 上，
    后续可以继续套用。
    """
    from . import api
    from . import card as cardlib
    from .ripper import clothes as _rc
    from . import clothes as _clothes

    c = cardlib.load_card(path)
    # 卡里的 sex 字段在部分卡上不可靠，用目录判断。
    gender = "male" if os.sep + "male" + os.sep in os.path.abspath(path).lower() else "female"
    head_id = int(c.get("face", {}).get("headId") or 0)
    # 头模：优先找 abdata/chara/<id>/fo_head_<id>.unity3d（30/35/38/50
    # 这些是游戏自带/替换头模）；没有就回退 00。模型仍然用同一副骨架。
    pre = "mo_head" if gender == "male" else "fo_head"
    head_bundle = "%s_00" % pre
    head_prefab = "p_cm_head_00" if gender == "male" else "p_cf_head_00"
    try:
        for d in os.listdir(os.path.join(game_dir, "abdata", "chara")):
            cand = "%s_%02d" % (pre, head_id) if head_id >= 0 else ""
            p2 = os.path.join(game_dir, "abdata", "chara", d,
                              cand + ".unity3d")
            if cand and os.path.isfile(p2):
                head_bundle = cand
                head_prefab = ("p_cm_head_%02d" if gender == "male"
                               else "p_cf_head_%02d") % head_id
                break
    except Exception:
        pass
    try:
        arm = api.character(gender=gender, head=True, cleanup=True,
                            head_bundle=head_bundle, head_prefab=head_prefab)
    except Exception:
        arm = api.character(gender=gender, head=True, cleanup=True)

    body = bpy.data.objects.get(arm.get("hs2_body") or BODY_NAME)
    head = bpy.data.objects.get(arm.get("hs2_head") or HEAD_NAME)
    mat = _card_skin_material(c.get("skin_color"), c.get("skin_gloss"))
    _assign_skin_material(body, mat)
    _assign_skin_material(head, mat)

    # 捏人：完整套用 shapeValueBody/Face（cf_J_*_s 骨骼）。
    try:
        from . import shapedata
        shapedata.apply_card_shape(arm, c, game_dir, male=(gender == "male"))
    except Exception as e:
        print("[HS2] 捏人骨骼映射失败：%s" % e)

    # 身高：卡里是连续 0..1，游戏对表是 0.8..1.0；只缩 cf_N_height，
    # 不逐骨乱缩，避免把骨架压扁。
    sb = c.get("shape_body") or []
    if sb:
        try:
            v = max(0.0, min(1.0, float(sb[0])))
            s = 0.8 + 0.2 * v
            pb = arm.pose.bones.get(api.HEIGHT_BONE)
            if pb is not None:
                pb.scale = (s, s, s)
            arm["hs2_height_scale"] = s
            arm["hs2_height_letter"] = "S" if v < 0.33 else ("L" if v > 0.66 else "M")
        except Exception:
            pass

    # 衣服：Coordinate.parts 的顺序就是游戏的 8 个槽位。
    slots = ("top", "bottom", "inner_t", "inner_b", "gloves",
             "panst", "socks", "shoes")
    worn, skipped = 0, 0
    try:
        recs = _clothing_catalog(game_dir)
    except Exception:
        recs = []
    by_key = {}
    for r in recs:
        if r.get("gender") != gender:
            continue
        try:
            by_key[(int(r.get("id")), r.get("slot"))] = r
        except Exception:
            continue
    skel = None
    try:
        skel = get_assets(game_dir).skeleton()
    except Exception:
        pass
    parts = (c.get("coordinate") or {}).get("parts") or []
    for i, part in enumerate(parts[:len(slots)]):
        try:
            ident = int(part.get("id") or 0)
            if ident <= 0:
                continue
            hide = list(part.get("hideOpt") or [])
            if hide and hide[0]:
                continue
            rec = by_key.get((ident, slots[i]))
            if rec is None:
                skipped += 1
                continue
            states = _rc.available_states(game_dir, rec)
            st = "" if "" in states else (states[0] if states else "A")
            _clothes.wear(game_dir, rec, st, arm, skel)
            worn += 1
        except Exception as e:
            print("[HS2] 卡片衣服 %s 失败：%s" % (i, e))
            skipped += 1

    # 把卡片数据留在物体上，方便后续补面部/头发/材质。
    arm["hs2_card"] = os.path.abspath(path)
    arm["hs2_card_name"] = c.get("name") or ""
    arm["hs2_card_head_id"] = int(c.get("face", {}).get("headId") or 0)
    arm["hs2_card_hair_id"] = int((c.get("hair", {}).get("parts") or [{}])[0].get("id") or 0)
    arm["hs2_card_shape_body"] = ",".join("%.6f" % x for x in (c.get("shape_body") or []))
    arm["hs2_card_shape_face"] = ",".join("%.6f" % x for x in (c.get("shape_face") or []))
    return arm, c, {"worn": worn, "skipped": skipped,
                    "body": body.name if body else "",
                    "head": head.name if head else ""}


class HS2_OT_load_character_card(bpy.types.Operator, ImportHelper):
    """从游戏 UserData/chara 的 PNG 角色卡直接加载角色（不经过工作室）。"""
    bl_idname = "hs2.load_character_card"
    bl_label = "从角色卡加载"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".png"
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.filepath:
            gd = game_dir_of(context)
            if gd:
                base = os.path.join(gd, "UserData", "chara", "female")
                if os.path.isdir(base):
                    self.filepath = os.path.join(base, "")
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        gd = game_dir_of(context)
        if not gd:
            self.report({"ERROR"}, "先设置游戏目录")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "找不到角色卡：%s" % path)
            return {"CANCELLED"}
        try:
            arm, c, st = load_character_card(path, gd)
        except Exception as e:
            self.report({"ERROR"}, "角色卡加载失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "已加载 %s：身体/头/衣服 %d 件（缺 %d），身高 %.3f"
                    % (c.get("name") or os.path.basename(path), st["worn"],
                       st["skipped"], arm.get("hs2_height_scale") or 0.0))
        return {"FINISHED"}


class HS2_OT_load_default(bpy.types.Operator):
    """加载 HS2 默认角色：默认骨架 + 素模（身体 + 头），完全没捏过的状态"""
    bl_idname = "hs2.load_default"
    bl_label = "1. 加载默认角色"
    bl_options = {"REGISTER", "UNDO"}

    cleanup: BoolProperty(name="清理已有 HS2 物体", default=True)
    include_head: BoolProperty(name="带头部", default=True,
                               description="从 fo_head_00 取头骨与头部网格")
    scale: FloatProperty(name="单位缩放", default=0.1, min=0.001, max=10.0)
    male: BoolProperty(name="男角素体", default=False,
                       description="用 o_body_cm（男角身体），骨架是同一副")

    def execute(self, context):
        t0 = time.time()
        g = get_assets(game_dir_of(context))
        gender = "male" if self.male else "female"
        slot = free_slot(gender)
        if slot is None:
            self.report({"ERROR"}, "已经是两个%s角色了" % (
                "男" if self.male else "女",))
            return {"CANCELLED"}
        name = {"f1": ARM_NAME, "m1": "Armature_Male",
                "f2": "Armature_f2", "m2": "Armature_m2"}.get(
                    slot, "Armature_" + slot)
        try:
            arm, names, body, head_obj = build_default(
                g, include_head=self.include_head, scale=self.scale,
                cleanup=self.cleanup, name=name, gender=gender, slot=slot,
                body_mesh_name=_mesh.BODY_MALE_MESH if self.male else None)
        except Exception as e:
            self.report({"ERROR"}, "读取失败：%s" % e)
            return {"CANCELLED"}

        total = len(arm.data.bones)
        self.report({"INFO"},
                    "已放 %s：骨架 %d 骨（含面部 %d），身体 %d 顶点，头 %s，用时 %.1fs"
                    % (slot, total, total - len(names),
                       len(body.data.vertices),
                       ("%d 顶点" % len(head_obj.data.vertices)) if head_obj
                       else "无", time.time() - t0))
        return {"FINISHED"}


class HS2_OT_add_head(bpy.types.Operator):
    """给现有骨架补上头部（骨骼 + 网格）"""
    bl_idname = "hs2.add_head"
    bl_label = "补头部"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "没有 HS2 骨架")
            return {"CANCELLED"}
        if arm.data.bones.get("cf_J_FaceRoot"):
            self.report({"INFO"}, "已经有头了")
            return {"CANCELLED"}

        g = get_assets(game_dir_of(context))
        try:
            head = g.head()
        except Exception as e:
            self.report({"ERROR"}, "头部读取失败：%s" % e)
            return {"CANCELLED"}

        scale = float(arm.get("hs2_scale", 0.1))
        extra = [{"name": b["name"], "parent": b["parent"],
                  "world": b["world"], "deform": b["deform"]}
                 for b in head["bones"]]

        # 复用 build_armature 的额外骨骼逻辑：借一份空骨架太重，这里单独插
        bdata.add_extra_bones(arm, extra, scale)

        for ob in list(bpy.context.selected_objects):
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        hd = dict(head["mesh"])
        hd["group_names"] = hd.get("bone_names") or []
        head_obj = bdata.build_mesh(
            hd, STATE.get("skel"), STATE.get("names") or [], arm,
            name=HEAD_NAME, scale=scale, xform=head["attach"])
        _apply_base_gray(head_obj)
        self.report({"INFO"}, "头部：%d 根骨，%d 顶点"
                    % (len(extra), len(head["mesh"]["vertices"])))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 动作
# --------------------------------------------------------------------------

class HS2_OT_scan_clips(bpy.types.Operator):
    """扫描游戏里的全部动作（第一次会慢些，之后有缓存）"""
    bl_idname = "hs2.scan_clips"
    bl_label = "重建动作库索引"

    rebuild: BoolProperty(name="重建", default=True,
                          description="丢掉旧缓存重新扫一遍")

    def execute(self, context):
        g = get_assets(game_dir_of(context))
        wm = context.window_manager

        def progress(i, total, name):
            wm.progress_update(int(i * 100 / max(total, 1)))

        wm.progress_begin(0, 100)
        t0 = time.time()
        try:
            idx = g.clip_index(rebuild=self.rebuild, progress=progress)
        except Exception as e:
            wm.progress_end()
            self.report({"ERROR"}, "扫描失败：%s" % e)
            return {"CANCELLED"}
        wm.progress_end()
        STATE["index"] = idx
        clear_enum_cache()
        try:
            from . import ops2
            ops2.invalidate_lists()
        except Exception:
            pass
        good = sum(1 for v in idx.values() if _fit_of(v) >= MIN_FIT)
        self.report({"INFO"}, "共 %d 个动作，其中 %d 个适配这副骨架，用时 %.0fs"
                    % (len(idx), good, time.time() - t0))
        _tag_redraw(context)
        return {"FINISHED"}


# 骨骼绑定匹配率低于这个值就当成「不是给这副骨架的动作」藏起来
MIN_FIT = 0.5


def _fit_of(entry):
    if isinstance(entry, dict):
        return float(entry.get("fit", 1.0))
    return 1.0


def _files_of(entry):
    if isinstance(entry, dict):
        return entry.get("files") or []
    return entry or []


def clip_table(context, fit=True):
    """统一来源：fit 过滤后的 {动作名: index条目}。

    「面板分类下拉」和「动作过滤」都用这一份数据，保证两边对分类/数量的
    理解一致 —— 之前分类下拉读 catalog()、过滤读 clip_index()，两边对
    group 的判断不同，导致选 sao 这类组时列表直接清空。
    """
    try:
        g = STATE.get("game") or get_assets(game_dir_of(context))
        idx = g.clip_index()
    except Exception:
        return {}
    if not idx:
        return {}
    if not fit:
        return idx
    return {n: idx[n] for n in idx if _fit_of(idx.get(n)) >= MIN_FIT}


# 工作室动作的身高变体：{基础名: {身高字母: 具体动作名}}
_STUDIO_VAR_CACHE = {"key": None, "map": None}


def _studio_variant_map(context):
    """把带 S/M/L 后缀的同一工作室动作并成一条（cook_01_S/M/L → cook_01）。

    这是 HS2 的「同一动作给三个身高」，列表里不该把三条全堆出来 ——
    用户看到一条 cook_01，用身高档位去切。返回：
        {基础名: (首选身高字母, {字母: 具体动作名})}
    也顺带记下首选的默认档（优先 M，其次 L，再 S）。

    一个基础名可能同时有干净版（chair_17_M）、loop 版（chair_17_M_loop）、
    甚至 dirty 版：为每个身高字母挑「最干净」的具体名（非 loop、非 dirty 优先）。
    """
    idx = clip_table(context)
    # 缓存：键用 (动作集指纹, 分类, 过滤) —— 分类/过滤变化时重建
    scn = getattr(context, "scene", None) if hasattr(context, "scene") else None
    cat = (getattr(scn, "hs2_clip_cat", "") or "") if scn else ""
    filt = (getattr(scn, "hs2_clip_filter", "") or "") if scn else ""
    sig = (tuple(sorted(idx)), cat, filt)
    if _STUDIO_VAR_CACHE["key"] == sig:
        return _STUDIO_VAR_CACHE["map"] or {}
    _BASE = _hdb.split_variants

    def _clean(v):
        """干净分：非 dirty 记 0，非 loop 记 0；两种情况各 +1。"""
        return (0 if not v["dirty"] else 1, 0 if not v["loop"] else 1)

    cands = {}   # base -> {letter: [(cleanliness, name), ...]}
    for name in idx:
        v = _BASE(name)
        if not v["height"]:
            continue
        base_map = cands.setdefault(v["base"], {})
        per_letter = base_map.setdefault(v["height"], [])
        per_letter.append((_clean(v), name))
    out = {}
    for b, per_letter in cands.items():
        hv = {}
        for letter, items in per_letter.items():
            if not items:
                continue
            items.sort(key=lambda x: x[0])          # 最干净在前
            hv[letter] = items[0][1]
        if not hv:
            continue
        default = ("M" if "M" in hv else
                   ("L" if "L" in hv else "S"))
        out[b] = (default, hv)
    _STUDIO_VAR_CACHE["key"] = sig
    _STUDIO_VAR_CACHE["map"] = out
    return out


def resolve_studio_clip(base_or_name, height=None):
    """把列表里显示的基础名还原成具体的动作名。

    ``cook_01`` + 身高 ``L`` → ``cook_01_L``（存在才返回，否则回退基础名）。
    传进来本来就是个具体动作名就原样返回。

    关键：动作**没有** S/M/L 变体时（比如 walk_Idle_00），必须原样返回基础名，
    绝不能拼成 ``xxx_M`` —— 否则会报「游戏里没有叫 xxx_M 的动作」。

    唯一键可能带控制器后缀（``attack_00 @ f_03_Sword``）—— 那时变体表里
    找不到（变体名不带后缀），直接原样返回就行：官方条目已经带着精确的
    path_id，本来就不需要再挑变体。
    """
    base = base_or_name
    if " @ " in base:
        return base
    # 保证变体表已建好（draw 时可能还没轮到本动作）
    if not _STUDIO_VAR_CACHE["map"]:
        try:
            _studio_variant_map(bpy.context)
        except Exception:
            pass
    idx = _STUDIO_VAR_CACHE["map"] or {}
    hv = idx.get(base)
    if hv:
        h = height or hv[0]          # 没指定就用该动作默认档
        if h in hv[1]:
            return hv[1][h]
        return hv[1][hv[0]]          # 档不存在就回退默认档
    return base


def clip_list(context, collapse=False):
    """当前过滤条件下的动作名列表（排序）。

    分类是**两级**的（跟游戏一致）：
      * ``hs2_clip_cat``  = 大分類（大きい項目）—— 角色 / バトル / キャラ2 …
      * ``hs2_clip_sub``  = 小分類（中間項目）—— 椅子 / 女 片手剣 / グラビア …

    分类过滤只看**分组键**，避免 category 子串误匹配导致数量爆炸/列表为空。
    默认**不折叠** S/M/L：每个具体动作名单独列出，搜 `turn_L` 才能搜到；
    之前折叠会把 `_L` 当成身高变体，左转动作因此被隐藏。

    默认把 studio 包内嵌的 H 动作（S_Idle / M_Idle / L_MLoop 这类 H 阶段片段，
    group=="H"）滤掉 —— 它们是 H 动画的阶段切块，当不成独立动作演；只
    在分类明确选「H」时才放进来。
    """
    idx = clip_table(context)
    if not idx:
        return []
    names = sorted(idx.keys())

    scn = getattr(context, "scene", None)
    cat = (getattr(scn, "hs2_clip_cat", "") or "").strip() if scn else ""
    sub = (getattr(scn, "hs2_clip_sub", "") or "").strip() if scn else ""
    if cat in ("", "ALL", "～", "所有"):
        cat = ""
    if sub in ("", "ALL", "～", "所有"):
        sub = ""
    only_h = (cat == "H")
    if cat or sub:
        kept = []
        for n in names:
            e = idx.get(n) or {}
            if e.get("group") == "H" and not only_h:
                continue              # H 阶段片段，默认不混进 Studio 列表
            if cat and not _in_top(e, cat):
                continue
            if sub and not _in_sub(e, sub):
                continue
            kept.append(n)
        names = kept
    else:
        # 没选分类时也要滤掉 H 阶段片段（否则折叠后只剩 MLoop/Idle 这类无意义的基底）
        names = [n for n in names
                 if only_h or (idx.get(n) or {}).get("group") != "H"]
    filt = (getattr(scn, "hs2_clip_filter", "") or "").strip().lower() if scn else ""
    if filt:
        # 同时搜内部动作名和官方表示名（「男 90回転 L」这种），否则
        # 搜「回転」永远搜不到 mc_m_move_04_L。
        def _match(n):
            if filt in n.lower():
                return True
            e = idx.get(n) or {}
            for key in ("name", "name_cn", "official", "label"):
                v = e.get(key)
                if isinstance(v, str) and filt in v.lower():
                    return True
            return False
        names = [n for n in names if _match(n)]
    if collapse:
        vmap = clip_variant_base(names, idx)
        return vmap
    return names


def _in_top(e, cat):
    """这条动作属于这个大分類吗？"""
    if cat == "H":
        return (e or {}).get("group") == "H"
    top = (e or {}).get("top_cn") or ""
    if top == cat:
        return True
    # 老条目（不在官方表里）没有 top_cn，退回按 category / group 判断
    return ((e or {}).get("top") == cat
            or (e or {}).get("category", "").split("/")[0] == cat)


def _in_sub(e, sub):
    """这条动作属于这个小分類吗？"""
    cat = (e or {}).get("category") or ""
    if "/" in cat and cat.split("/", 1)[1] == sub:
        return True
    return ((e or {}).get("group_cn") == sub
            or (e or {}).get("group") == sub)


def clip_variant_base(names, idx):
    """把一批具体动作名里带 S/M/L 身高变体的几条折叠成基础名。

    规则：只要 ``split_variants`` 认定这条带身高后缀（不管是 ``_loop`` 还是
    D_ 变体、还是普通后缀），就并成基础名。比如 chair_17_L / chair_17_M /
    chair_17_L_loop / chair_17_S_dirty... 全部 → ``chair_17``。

    顺序尽量稳定：同一基础名只出现一次。
    """
    _BASE = _hdb.split_variants
    seen = set()
    out = []
    for n in names:
        b = _BASE(n)
        if b["height"]:
            key = b["base"]               # 一切带身高后缀的都折叠到基础名
        else:
            key = n
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def clip_index(context):
    """当前选中的动作在过滤结果里的下标。"""
    names = clip_list(context)
    if not names:
        return 0, []
    scn = getattr(context, "scene", None)
    name = ""
    if scn is not None and hasattr(scn, "hs2_studio_items"):
        i = getattr(scn, "hs2_studio_index", -1)
        items = scn.hs2_studio_items
        if 0 <= i < len(items):
            name = items[i].value
    if name in names:
        return names.index(name), names
    return 0, names


def current_clip_name(context):
    """当前列表里选中的动作名（没有就返回第一个）。"""
    scn = getattr(context, "scene", None)
    if scn is not None and hasattr(scn, "hs2_studio_items"):
        i = getattr(scn, "hs2_studio_index", -1)
        items = scn.hs2_studio_items
        if 0 <= i < len(items):
            return items[i].value
    names = clip_list(context)
    return names[0] if names else ""


# --------------------------------------------------------------------------
# 自动加载：拖动滑条会连续触发 update，用定时器防抖，停手后才真的读动作
# --------------------------------------------------------------------------

_TIMER = {"fn": None}


def _clear_pending():
    fn = _TIMER.get("fn")
    if fn is not None:
        try:
            bpy.app.timers.unregister(fn)
        except Exception:
            pass
        _TIMER["fn"] = None


def _do_clip_load(name):
    """真正执行导入。在定时器里跑，只能拿 bpy.context，不能用传入的 context。"""
    try:
        import bpy as _bpy
        scn = getattr(_bpy.context, "scene", None)
        if scn is None:
            return
        arm = current_arm()
        if arm is None:
            return
        fps = float(getattr(scn.render, "fps", 30.0)) or 30.0
        _apply_clip(_bpy.context, arm, name, fps, follow=True)
        print("[HS2] 自动加载：%s" % name)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[HS2] 自动加载失败 %s：%s" % (name, e))


def schedule_clip_load(name, delay=0.35):
    """排一次导入；连续调用只会保留最后一次（防抖）。"""
    _clear_pending()
    name = (name or "").strip()
    if not name:
        return

    def run():
        _TIMER["fn"] = None
        _do_clip_load(name)
        return None

    _TIMER["fn"] = run
    try:
        bpy.app.timers.register(run, first_interval=delay)
    except Exception as e:
        print("[HS2] 定时器注册失败：%s" % e)
        _TIMER["fn"] = None
        _do_clip_load(name)


class HS2_OT_show_in_front(bpy.types.Operator):
    """让控制器画在网格前面（默认已开，老场景可能没有）"""
    bl_idname = "hs2.show_in_front"
    bl_label = "控制器显示在网格前面"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            return {"CANCELLED"}
        arm.show_in_front = True
        for nm in (rig.COLL_CTRL, rig.COLL_IK):
            coll = arm.data.collections_all.get(nm)
            if coll is not None:
                coll.is_visible = True
        self.report({"INFO"}, "已打开「在前面」")
        return {"FINISHED"}


def clear_enum_cache():
    """（旧接口，保留兼容；列表版界面不需要枚举缓存了）"""
    return None


_MMDCFG_FALLBACK = [("", "（没找到 _ref/mmdd/mmdd 下的配置）", "")]
_MMDCFG_CACHE = {"key": None, "items": _MMDCFG_FALLBACK}


def mmdcfg_items(self, context):
    """可用的 MMDD 角色配置（下拉框）。"""
    from . import mmdcfg
    out = mmdcfg.list_configs()
    if not out:
        _MMDCFG_CACHE["key"] = None
        _MMDCFG_CACHE["items"] = _MMDCFG_FALLBACK
        return _MMDCFG_CACHE["items"]
    key = tuple(out)
    if _MMDCFG_CACHE["key"] == key:
        return _MMDCFG_CACHE["items"]
    items = [(p, n, p) for n, p in out]
    # 默认配置（MMDD TDA v2.0）排最前
    def sort_key(it):
        return (0 if it[1] == mmdcfg.DEFAULT_INI else 1, it[1])
    items.sort(key=sort_key)
    _MMDCFG_CACHE["key"] = key
    _MMDCFG_CACHE["items"] = items
    return items


def _names_for(arm, skel):
    """骨架下标 -> Blender 骨骼名（和 bdata._unique_names 的去重规则一致）。

    重新打开 .blend 时 STATE 是空的，所以不能依赖内存里的缓存。
    """
    used = {}
    out = []
    for b in skel.bones:
        n = b.name or "bone"
        if n in used:
            used[n] += 1
            n = "%s_%03d" % (n, used[n])
        else:
            used[n] = 0
        out.append(n if n in arm.data.bones else None)
    return out


def apply_clip_by_name(arm, name, fps=None, follow=True):
    """按名字装 Studio 动作（给 :mod:`api` 用，不依赖 UI 上下文）。"""
    g = get_assets(game_dir_from_arm(arm))
    clip = g.load_clip(name, fps=fps)
    return apply_clip_data(arm, clip, name, follow=follow)


def apply_clip_data(arm, clip, name=None, follow=True, keyframe=True):
    """把一份 ClipData 装到骨架上（Studio / H / 道具都走这里）。"""
    skel = STATE.get("skel")
    if skel is None:
        g = get_assets(game_dir_from_arm(arm))
        skel = g.skeleton()
        STATE["skel"] = skel
    names = STATE.get("names")
    if not names:
        names = _names_for(arm, skel)
        STATE["names"] = names

    if arm.get("hs2_custom"):
        # 工作室 FBX 骨架：rest 是它自己的 BindPose，骨长/比例也和默认骨架
        # 不同，所以按「世界朝向直传」重定向（fbxrig.apply），不能用
        # apply_clip 的世界矩阵强制对齐。
        from . import fbxrig
        act, info = fbxrig.apply(arm, skel, clip,
                                 name=name or ("HS2_" + clip.name),
                                 scale=0.1, keyframe=keyframe)
    else:
        act, info = anim.apply_clip(arm, skel, names, clip,
                                    scale=STATE.get("scale"),
                                    action_name=name or ("HS2_" + clip.name))
    STATE["keyed"] = 0
    if act is None:
        return None, info
    scn = bpy.context.scene
    last = max(0, int(round(act.frame_range[1])))
    scn.frame_start = 0
    scn.frame_end = max(1, last)
    try:
        scn.render.fps = int(round(info["fps"]))
    except Exception:
        pass
    scn.frame_set(1)

    keyed = 0
    if follow and keyframe and rig.has_rig(arm):
        keyed = rig.key_targets(arm, scn.frame_start, last)
        scn.frame_set(1)

    STATE["action"] = act
    STATE["clip"] = clip
    STATE["action_name"] = name
    STATE["keyed"] = keyed
    return act, info


def _apply_clip(context, arm, name, fps, follow=True):
    """把游戏里的动作装进当前骨架。返回 (action, info, keyed)。"""
    g = get_assets(game_dir_of(context))
    skel = STATE.get("skel") or g.skeleton()
    STATE["skel"] = skel
    STATE["names"] = STATE.get("names") or _names_for(arm, skel)

    clip = g.load_clip(name, fps=fps)
    act, info = apply_clip_data(arm, clip, name, follow=follow)
    return act, info, STATE.get("keyed", 0)


class HS2_OT_toggle_jointfix(bpy.types.Operator):
    """开关关节补偿（dam 骨随关节按比例旋转）"""
    bl_idname = "hs2.toggle_jointfix"
    bl_label = "关节补偿"
    bl_options = {"REGISTER", "UNDO"}

    enable: BoolProperty(name="开启", default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        arm.hs2_jointfix = self.enable      # 属性回调里会去开关约束
        self.report({"INFO"}, "关节补偿 %s（改关节时 dam 骨会跟着转）"
                    % ("开" if self.enable else "关"))
        return {"FINISHED"}


class HS2_OT_import_clip(bpy.types.Operator):
    """从游戏目录导入一个动作，生成 Blender Action（可播放、可编辑）"""
    bl_idname = "hs2.import_clip"
    bl_label = "2. 导入动作"
    bl_options = {"REGISTER", "UNDO"}

    clip_name: StringProperty(name="动作名")
    fps: FloatProperty(name="帧率", default=30.0, min=1.0, max=120.0)
    follow: BoolProperty(name="控制器跟随动作", default=True,
                         description="导入后把 IK 控制器钉到这段动作上")

    def invoke(self, context, event):
        if not self.clip_name:
            self.clip_name = current_clip_name(context)
        if self.fps <= 0.0:
            self.fps = float(getattr(context.scene.render, "fps", 30.0)) or 30.0
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        _i, names = clip_index(context)
        self.layout.label(text="动作库里共 %d 个可用动作" % len(names)
                          if names else "先到「② 动作」里扫描动作库",
                          icon="INFO")
        self.layout.prop(self, "clip_name", text="动作名")
        self.layout.prop(self, "fps")
        if rig.has_rig(current_arm()):
            self.layout.prop(self, "follow")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)

        name = (self.clip_name or "").strip()
        if not name:
            # 没指定就用滑条当前选中的那个
            name = current_clip_name(context)
        if not name:
            self.report({"ERROR"}, "没有指定动作名（先点「扫描动作库」）")
            return {"CANCELLED"}

        t0 = time.time()
        try:
            act, info, keyed = _apply_clip(context, arm, name, self.fps, self.follow)
        except Exception as e:
            self.report({"ERROR"}, "读取动作失败：%s" % e)
            return {"CANCELLED"}

        self.report({"INFO"}, "%s：%d 帧 / %d 根骨%s，用时 %.1fs"
                    % (name, info["frames"], info["bone_tracks"],
                       ("，控制器 %d 帧" % keyed) if keyed else "",
                       time.time() - t0))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 外部动作：VMD / Mixamo
# --------------------------------------------------------------------------

def _after_import(context, arm, act, frames, follow):
    """导入后收尾：设帧范围 + 可选控制器打帧。

    （原「贴地」逻辑已彻底删除 —— MMDD 插件里没有这个功能，它 100% 忠实
    还原 VMD 数据，脚在哪儿就是哪儿。）
    """
    scn = context.scene
    last = max(0, int(round(act.frame_range[1])))
    scn.frame_start = 0
    scn.frame_end = max(1, last)
    scn.frame_set(0)
    keyed = 0
    if follow and rig.has_rig(arm):
        keyed = rig.key_targets(arm, scn.frame_start, last)
        scn.frame_set(1)
    STATE["action"] = act
    return keyed


class HS2_OT_import_vmd(bpy.types.Operator, ImportHelper):
    """导入 MMD 动作（.vmd），重定向到当前 HS2 骨架"""
    bl_idname = "hs2.import_vmd"
    bl_label = "导入 VMD 动作"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    scale: FloatProperty(name="单位换算 (米/MMD单位)", default=0.0832,
                         min=0.005, max=0.5, precision=4,
                         description="MMDD: AxisScale 0.832 × 角色缩放 0.1 = 0.0832。"
                                     "旧默认 0.08 会让位移和足 IK 目标短 4%")
    chara: EnumProperty(
        name="角色配置",
        description="MMDD 的角色骨骼配置（决定虚拟 MMD 骨架怎么搭）",
        items=lambda self, context: mmdcfg_items(self, context))
    use_dummy: BoolProperty(
        name="用虚拟 MMD 骨架（和 MMDD 一致）", default=True,
        description="在 HS2 骨上搭一棵虚拟 MMD 骨架，VMD 驱动它再同步回来。"
                    "MMD 的层级/腰キャンセル/扭转分散都原样保留，效果最接近 MMD；"
                    "关掉则退回「直接映射」，动作更轻量但躯干会走样")
    use_ik: BoolProperty(
        name="启用足部 IK", default=True,
        description="按 ini 的 BoneIKSet 用两骨链解析解（余弦定理 + CCD）"
                    "解算膝盖，脚踝精确落到足ＩＫ 目标。"
                    "有足ＩＫ 帧的 VMD 必开（腿的 FK 数据只是占位）；"
                    "关掉则 FK 直驱")
    joint_fix: BoolProperty(
        name="关节校正（dam 骨）", default=True,
        description="VMD 没有 dam 骨的烘焙曲线，默认打开膝/肘/臀内侧校正；"
                    "导入后也可以在「控制器」面板里随时开关")
    follow: BoolProperty(name="控制器跟随", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.prop(self, "chara")
        self.layout.prop(self, "use_dummy")
        if self.use_dummy:
            self.layout.prop(self, "use_ik")
            self.layout.prop(self, "joint_fix")
        self.layout.prop(self, "scale")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            # 不用先手动放素体：没有角色就直接生成一个默认女角
            try:
                from . import api
                arm = api.ensure_actor("female")
                self.report({"INFO"}, "没有可用的角色，已自动放一个默认女角")
            except Exception as e:
                self.report({"ERROR"}, "生不成角色：%s" % e)
                return {"CANCELLED"}
        handpose.release(arm)
        try:
            data = vmdlib.parse(self.filepath)
        except Exception as e:
            self.report({"ERROR"}, "VMD 解析失败：%s" % e)
            return {"CANCELLED"}
        if not data["bones"]:
            self.report({"ERROR"}, "VMD 里没有骨骼帧")
            return {"CANCELLED"}

        t0 = time.time()
        fps = context.scene.render.fps
        name = os.path.basename(self.filepath)

        if self.use_dummy:
            act, frames, ntrack = self._via_dummy(context, arm, data, fps, name)
        else:
            act, frames, ntrack = self._direct(context, arm, data, fps, name)
        if act is None:
            return {"CANCELLED"}

        keyed = _after_import(context, arm, act, frames, self.follow)
        self.report({"INFO"}, "%s：%d 帧 / %d 骨（%s），用时 %.1fs"
                    % (data.get("model") or "VMD", frames, ntrack,
                       "虚拟骨架" if self.use_dummy else "直接映射",
                       time.time() - t0))
        return {"FINISHED"}

    # ---- 方式 A：虚拟 MMD 骨架（复刻 MMDD）----
    def _via_dummy(self, context, arm, data, fps, name):
        from . import mmdcfg, mmdrig

        cfg = mmdcfg.CharaConfig(self.chara or None)
        if not cfg.bones:
            self.report({"ERROR"}, "角色配置读不出来（%s）" % cfg.path)
            return None, 0, 0

        # 1) VMD 里所有骨的逐帧旋转/位置（不映射，原样保留）
        raw = vmdlib.all_tracks(data, fps=fps)
        if not raw:
            self.report({"ERROR"}, "VMD 里没有可用的骨骼帧")
            return None, 0, 0

        # 2) 建虚拟骨架 + 同步。
        # 足 IK 用 write_curves 里的**解析解**（余弦定理 + CCD，直接写 fcurve），
        # 不再用 Blender IK 约束 —— 两套 IK 会打架（约束把解析结果拉歪，
        # 膝盖 51° vs 1° 就是这么来的）。
        # bo 偏移是 HS2 prefab 单位（×骨架缩放），不是 MMD 单位
        scene_scale = float(arm.get("hs2_scale", 0.1) or 0.1)
        stats = mmdrig.build(arm, cfg, unit=scene_scale)
        nsync = mmdrig.sync(arm, cfg)

        # 2.5) dam 骨约束（膝盖后/肘后等，随主骨按比例转）。
        # MMDD 官方不做这个（BoneWeight 里 dam 条目全注释），但膝盖/手肘
        # 弯曲时 dam 骨不跟随会导致体积塌陷 —— 实测比例已验证正确。
        from . import rig as _rig
        nfix = _rig._build_correctives(arm)
        # VMD 没有 dam 骨的烘焙曲线，默认把校正打开（导入对话框可关）。
        # 注意：游戏动作导入不走这里，仍保持自己的 dam 曲线，不受影响。
        use_fix = bool(getattr(self, "joint_fix", True))
        try:
            arm.hs2_jointfix = use_fix
        except Exception:
            arm["hs2_jointfix"] = 1 if use_fix else 0
        _rig.set_joint_fix(arm, use_fix)

        # 3) 写动作
        tilt = bool(getattr(context.scene, "hs2_arm_tilt", True))
        act, frames = mmdrig.write_curves(
            arm, cfg, raw, action_name="HS2_VMD_" + name,
            unit=self.scale, arm_tilt=tilt, vmd_data=data)
        bpy.context.view_layer.update()
        print("[MMDD] %s：虚拟骨 %d，同步 %d 条，帧 %d"
              % (cfg.name, stats["bones"], nsync, frames))
        return act, frames, len(raw)

    # ---- 方式 B：直接映射（老路径）----
    def _direct(self, context, arm, data, fps, name):
        mmdrig.clear(arm)      # 清掉之前虚拟骨架的残留
        tracks, frames = vmdlib.to_hs2(data, fps=fps, scale=self.scale,
                                       arm_deg=35.0)
        if not tracks:
            self.report({"ERROR"}, "没有一个 MMD 骨骼能对上 HS2")
            return None, 0, 0
        act, _n = retarget.write_curves(
            arm, tracks, action_name="HS2_VMD_" + name, fps=fps,
            frame_count=frames)
        return act, frames, len(tracks)


class HS2_OT_import_mixamo(bpy.types.Operator, ImportHelper):
    """导入带皮肤的 Mixamo FBX，把动作重定向到当前 HS2 骨架"""
    bl_idname = "hs2.import_mixamo"
    bl_label = "导入 Mixamo 动作"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    with_fingers: BoolProperty(name="含手指", default=True)
    follow: BoolProperty(name="控制器跟随", default=True)
    keep_source: BoolProperty(name="保留源骨架", default=False)
    foot_ik: BoolProperty(name="足部 IK 修正", default=True,
                          description="用两骨 IK 把脚踝对准源动作轨迹，减少滑步")
    joint_fix: BoolProperty(name="关节校正", default=True,
                            description="导入后自动打开膝/肘 dam 校正")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            try:
                from . import api
                arm = api.ensure_actor("female")
                self.report({"INFO"}, "没有可用的角色，已自动放一个默认女角")
            except Exception as e:
                self.report({"ERROR"}, "生不成角色：%s" % e)
                return {"CANCELLED"}
        handpose.release(arm)

        # 清掉上一次 VMD 虚拟骨架留下的虚拟骨和 MMDD 约束，
        # 否则那些 world→world 的 COPY_ROTATION 会把 Mixamo 的动画整个盖掉。
        mmdrig.clear(arm)

        before = {o.name for o in bpy.data.objects}
        try:
            bpy.ops.import_scene.fbx(filepath=self.filepath)
        except Exception as e:
            self.report({"ERROR"}, "FBX 导入失败：%s" % e)
            return {"CANCELLED"}
        new_objs = [o for o in bpy.data.objects if o.name not in before]
        src = retarget.find_source_armature(new_objs)
        if src is None:
            self.report({"ERROR"}, "这个 FBX 里没有 Mixamo 骨架")
            return {"CANCELLED"}

        scn = context.scene
        act_src = src.animation_data.action if src.animation_data else None
        if act_src is None:
            self.report({"ERROR"}, "FBX 里没有动画")
            return {"CANCELLED"}
        f0, f1 = act_src.frame_range
        f0, f1 = int(round(f0)), int(round(f1))

        src_name = src.name
        t0 = time.time()
        tracks, frames = retarget.mixamo_tracks(
            src, arm, f0, f1, with_fingers=self.with_fingers)
        act, n = retarget.write_curves(arm, tracks, action_name="HS2_Mixamo_" + src.name,
                                       fps=scn.render.fps, frame_count=frames)
        if self.foot_ik:
            retarget.foot_ik_pass(arm, src, act, 1, frames)
        ground = retarget.ground_contact_pass(arm, act, 1, frames)
        from . import rig as _rig
        if self.joint_fix:
            _rig._build_correctives(arm)
        try:
            arm.hs2_jointfix = bool(self.joint_fix)
        except Exception:
            arm["hs2_jointfix"] = 1 if self.joint_fix else 0
        try:
            _rig.set_joint_fix(arm, bool(self.joint_fix))
        except Exception:
            pass
        keyed = _after_import(context, arm, act, frames, self.follow)

        if not self.keep_source:
            for ob in new_objs:
                if ob.name not in bpy.data.objects:
                    continue
                data = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if isinstance(data, bpy.types.Armature) and data.users == 0:
                    bpy.data.armatures.remove(data)
                elif isinstance(data, bpy.types.Mesh) and getattr(data, "users", 0) == 0:
                    bpy.data.meshes.remove(data)

        fix = ("，贴地 %+.1f cm" % (ground * 100.0)) if ground else ""
        self.report({"INFO"}, "%s：%d 帧 / %d 骨%s，用时 %.1fs"
                    % (src_name, frames, len(tracks), fix, time.time() - t0))
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# --------------------------------------------------------------------------
# 工作室手势（cf_anmShapeHand）
# --------------------------------------------------------------------------

class HS2_OT_apply_hand_pose(bpy.types.Operator):
    """应用工作室手势（21 种，直接写手指骨）"""
    bl_idname = "hs2.apply_hand_pose"
    bl_label = "应用手势"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="目标", default="BOTH",
        items=(("L", "左手", ""), ("R", "右手", ""),
               ("BOTH", "双手", ""), ("RESET", "还原", "")))
    keyframe: BoolProperty(name="在当前帧打关键帧", default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        g = game_dir_of(context)
        if not g:
            self.report({"ERROR"}, "请先设置游戏目录")
            return {"CANCELLED"}
        try:
            poses = handpose.load_poses(g)
        except Exception as e:
            self.report({"ERROR"}, "读取手势失败：%s" % e)
            return {"CANCELLED"}
        scn = context.scene
        if self.mode == "RESET":
            n = handpose.apply_pose(arm, -1, side="BOTH",
                                    keyframe=self.keyframe, poses=poses)
            msg = "已还原手势"
        else:
            targets = [self.mode]
            if self.mode == "BOTH":
                targets = ["L", "R"]
            n = 0
            for t in targets:
                idx = int(scn.hs2_hand_L if t == "L" else scn.hs2_hand_R)
                n += handpose.apply_pose(arm, idx, side=t,
                                         keyframe=self.keyframe, poses=poses)
            msg = "按当前选择写入"
        if n == 0:
            self.report({"WARNING"}, "没有写入任何手指骨")
            return {"CANCELLED"}
        self.report({"INFO"}, "%s：写入 %d 根手指骨%s"
                    % (msg, n, "（已打帧）" if self.keyframe else ""))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 控制器
# --------------------------------------------------------------------------

class HS2_OT_add_rig(bpy.types.Operator):
    """生成 IK/FK 控制层（可重复执行：会先清掉旧的再重建）"""
    bl_idname = "hs2.add_rig"
    bl_label = "生成控制器"
    bl_options = {"REGISTER", "UNDO"}

    follow: BoolProperty(name="控制器跟随当前动作", default=True)
    fingers: BoolProperty(name="含手指控制器", default=False,
                          description="每根手指 3 个 FK 控制器（30 个），默认收起")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)

        for ob in list(bpy.context.selected_objects):
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        t0 = time.time()
        try:
            stats = rig.build(arm, key_animation=self.follow,
                              fingers=self.fingers)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "生成失败：%s" % e)
            return {"CANCELLED"}

        self.report({"INFO"},
                    "%d 控制器 / %d 约束 / %d 补偿%s，用时 %.1fs"
                    % (stats["controls"], stats["constraints"],
                       stats["correctives"],
                       ("，极向角 %s" % stats["pole"]) if stats["pole"] else "",
                       time.time() - t0))
        _tag_redraw(context)
        return {"FINISHED"}


class HS2_OT_clear_rig(bpy.types.Operator):
    """删掉控制层，只留游戏骨架"""
    bl_idname = "hs2.clear_rig"
    bl_label = "清除控制器"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            return {"CANCELLED"}
        handpose.release(arm)
        rig.clear(arm)
        self.report({"INFO"}, "已清除控制层")
        return {"FINISHED"}


class HS2_OT_toggle_ik(bpy.types.Operator):
    """开关 IK（走 ik 属性，可以逐肢体控制）"""
    bl_idname = "hs2.toggle_ik"
    bl_label = "开关 IK"
    bl_options = {"REGISTER", "UNDO"}

    enable: BoolProperty(default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            self.report({"ERROR"}, "还没有控制器")
            return {"CANCELLED"}
        rig.set_ik(arm, self.enable)
        self.report({"INFO"}, "%s IK" % ("启用" if self.enable else "关闭"))
        return {"FINISHED"}


class HS2_OT_snap_ik(bpy.types.Operator):
    """把控制器吸附到当前姿势（先吸附再开 IK，姿势不会跳）"""
    bl_idname = "hs2.snap_ik"
    bl_label = "控制器吸附当前姿势"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        rig.snap_ik_to_fk(arm)
        self.report({"INFO"}, "控制器已吸附")
        return {"FINISHED"}


class HS2_OT_snap_fk(bpy.types.Operator):
    """把 IK 解算结果写回 FK 骨骼（切回 FK 不跳）"""
    bl_idname = "hs2.snap_fk"
    bl_label = "IK 结果写回 FK"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        n = rig.snap_fk_to_ik(arm, key=True)
        self.report({"INFO"}, "%d 根骨已写回，IK 已关闭" % n)
        return {"FINISHED"}


class HS2_OT_key_targets(bpy.types.Operator):
    """把控制器重新钉到整段动作上（改完动作后重新对一次）"""
    bl_idname = "hs2.key_targets"
    bl_label = "控制器跟随整段动作"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        if arm.animation_data is None or arm.animation_data.action is None:
            self.report({"ERROR"}, "当前没有动作")
            return {"CANCELLED"}
        scn = context.scene
        n = rig.key_targets(arm, scn.frame_start, scn.frame_end)
        self.report({"INFO"}, "已重钉 %d 帧" % n)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def _bake_to_fk(arm, context):
    """把当前动作（含 IK 结果）烘成纯 FK。返回 (帧数, 骨骼数)。

    只烘「有动画的骨 + IK 链上的骨」：整副骨架 693 根全打帧会让 FBX 大一倍，
    而且大部分骨本来就不动。
    """
    import math  # noqa: F401  (nla.bake 需要 context 里没有的东西也无妨)

    scn = context.scene
    for ob in list(bpy.context.selected_objects):
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    mode = bpy.context.object.mode
    if mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")

    rig.set_ik(arm, True)
    bpy.ops.pose.select_all(action="DESELECT")

    deform = set(rig.deform_bone_names(arm))
    wanted = set()
    act = arm.animation_data.action if arm.animation_data else None
    if act is not None:
        from . import compat
        for fc in (compat.bag(act, arm) or []):
            dp = fc.data_path
            if dp.startswith('pose.bones["'):
                wanted.add(dp[12:dp.index('"]')])
    # IK 链上的骨即使没动画也要烘（手/脚可能只靠 IK 摆位）
    for side, ch in rig.LEG.items():
        wanted.update([ch["thigh"], ch["calf"], ch["roll"], ch["foot"], ch["toe"]])
    for side, ch in rig.ARM.items():
        wanted.update([ch["upper"], ch["lower"], ch["hand"]])
    # 带约束的变形骨全部要烘（Root/Hips/Head 上有叠加偏移）
    for pb in arm.pose.bones:
        if pb.constraints and pb.bone.use_deform:
            wanted.add(pb.name)

    sel = [n for n in wanted if n in deform]
    if len(sel) < 8:
        sel = list(deform)
    # Blender 5.x 移除了 Bone.select，只剩 PoseBone.select
    for pb in arm.pose.bones:
        want = pb.name in sel
        try:
            pb.select = want
        except Exception:
            pass
        try:
            pb.bone.select = want
        except Exception:
            pass

    bpy.ops.nla.bake(frame_start=scn.frame_start, frame_end=scn.frame_end,
                     only_selected=True, visual_keying=True,
                     clear_constraints=True, clear_parents=False,
                     use_current_action=True, bake_types={"POSE"})
    rig.set_ik(arm, False)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    arm["hs2_baked"] = 1
    return scn.frame_end - scn.frame_start + 1, len(sel)


class HS2_OT_bake_fk(bpy.types.Operator):
    """把当前动作（含 IK 结果）烘焙成纯 FK 关键帧"""
    bl_idname = "hs2.bake_fk"
    bl_label = "4. 烘焙为纯 FK"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        t0 = time.time()
        frames, bones = _bake_to_fk(arm, context)
        self.report({"INFO"}, "已烘焙 %d 帧 / %d 骨，用时 %.1fs"
                    % (frames, bones, time.time() - t0))
        return {"FINISHED"}


class HS2_OT_export_fbx(bpy.types.Operator, ExportHelper):
    """导出给游戏用的 FBX（骨架 + 可选蒙皮 + 动画）"""
    bl_idname = "hs2.export_fbx"
    bl_label = "5. 导出 FBX"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    include_mesh: BoolProperty(name="带蒙皮网格", default=False,
                               description="只在需要看效果时勾上；纯动作导出更干净")
    deform_only: BoolProperty(name="只导出变形骨", default=True)
    all_bones: BoolProperty(name="所有骨都打帧", default=False,
                            description="关掉可以显著减小文件；有些引擎要求所有骨都有曲线")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        path = _export(arm, bpy.path.abspath(self.filepath),
                       self.include_mesh, self.deform_only, self.all_bones)
        self.report({"INFO"}, "已导出 %s" % path)
        return {"FINISHED"}


def _safe_filename(name, fallback="anim"):
    s = "".join(c if c not in '<>:"/\\|?*' else "_" for c in (name or ""))
    return (s.strip(" .") or fallback)[:80]


class HS2_OT_export_char_anim(bpy.types.Operator, ExportHelper):
    """把当前角色身上这条动作导出成只带动画的 FBX（不含身体/头部网格）"""
    bl_idname = "hs2.export_char_anim"
    bl_label = "导出当前角色动画"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    name: StringProperty(name="角色", default="",
                         description="要导出的角色对象名；留空则用当前角色")
    deform_only: BoolProperty(name="只导出变形骨", default=True,
                              description="跟导出骨架时保持一致，避免 UE 里多出控制器骨")
    all_bones: BoolProperty(name="所有骨都打帧", default=True,
                            description="开着头骨/表情骨也不会缺曲线；关掉文件更小")

    def _arm(self):
        ob = bpy.data.objects.get(self.name) if self.name else None
        if ob is not None and ob.type == "ARMATURE":
            return ob
        return current_arm()

    def invoke(self, context, event):
        arm = self._arm()
        if arm is not None and not self.filepath:
            act = arm.animation_data.action if arm.animation_data else None
            stem = _safe_filename(act.name if act else arm.name + "_anim")
            base = (os.path.dirname(bpy.data.filepath) if bpy.data.filepath
                    else os.path.expanduser("~"))
            self.filepath = os.path.join(base, stem + ".fbx")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        arm = self._arm()
        if arm is None:
            self.report({"ERROR"}, "先在「编辑对象」里选一个角色")
            return {"CANCELLED"}
        act = arm.animation_data.action if arm.animation_data else None
        if act is None:
            self.report({"ERROR"}, "当前角色没有动作，先装一条动作再导出")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        if not path.lower().endswith(".fbx"):
            path += ".fbx"
        rng = object_action_range(arm) or (1, 1)
        # take 名 = 动作名（去掉文件名非法字符），UE 里导入的动画序列就直接叫这个
        take = _safe_filename(act.name, "anim")[:60]
        try:
            # export_object 会断父级、临时归零物体变换、按动作范围烘焙；
            # include_mesh=False → 只选骨架本身，身体/头部网格不会进 FBX。
            export_object(arm, path, axis="ue", include_mesh=False,
                          deform_only=self.deform_only,
                          all_bones=self.all_bones, take_name=take)
        except Exception as e:
            self.report({"ERROR"}, "导出失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "已导出 %s（动作 %s，%d 帧，仅动画）"
                    % (os.path.basename(path), act.name, rng[1] - rng[0] + 1))
        return {"FINISHED"}


def _export_root_name(obj):
    if obj.type != "ARMATURE" or not obj.data.bones.get("p_cf_anim"):
        return None
    return "p_cf_anim"


@contextlib.contextmanager
def _export_with_root(obj):
    """FBX 的骨架根节点用 p_cf_anim，而不是对象名 Armature。"""
    root = _export_root_name(obj)
    if not root or obj.name == root:
        yield
        return
    if bpy.data.objects.get(root) not in (None, obj):
        yield
        return
    old_obj, old_data = obj.name, obj.data.name
    try:
        obj.name = root
        obj.data.name = root
        yield
    finally:
        obj.name = old_obj
        obj.data.name = old_data


@contextlib.contextmanager
def _ue_unit(arm, meshes=(), unit="cm"):
    """unit="cm"：导出期间把骨架/网格换成厘米（理由见 :mod:`hs2_rig_kit.ueunit`）。

    不换的话，UE 导入时会把「米→厘米」的 100 倍乘到根骨 ``p_cf_anim`` 上，
    根骨缩放变成 100，做「兼容骨架」重定向时动画就对不上。
    """
    if unit != "cm":
        yield
        return
    from . import ueunit
    with ueunit.centimeters(arm, meshes):
        yield


def _export(arm, path, include_mesh=False, deform_only=True, all_bones=False,
            unit="cm"):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return _export_selected(arm, path, include_mesh, deform_only, all_bones,
                            unit=unit)


def _export_selected(arm, path, include_mesh=False, deform_only=True,
                     all_bones=False, unit="cm"):
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    meshes = []
    if include_mesh:
        # 默认角色叫 HS2_Body/HS2_Head，工作室 FBX 角色带的是 hs2_body/hs2_head
        names = [arm.get("hs2_body"), arm.get("hs2_head"), BODY_NAME, HEAD_NAME]
        seen = set()
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            ob = bpy.data.objects.get(name)
            if ob is not None and ob.type == "MESH":
                ob.select_set(True)
                meshes.append(ob)
    bpy.context.view_layer.objects.active = arm
    # UE 导出不要把骨架物体改名成 p_cf_anim：物体和根骨同名时，UE 的
    # FBX 导入器可能只保留一个，导致动画里的 p_cf_anim 根运动轨道对不上。
    with _ue_unit(arm, meshes, unit):
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=True,
            object_types={"ARMATURE", "MESH"},
            apply_scale_options="FBX_SCALE_ALL",
            use_mesh_modifiers=True, mesh_smooth_type="FACE",
            add_leaf_bones=False,
            use_armature_deform_only=deform_only,
            primary_bone_axis="Y", secondary_bone_axis="X",
            armature_nodetype="NULL",
            bake_anim=True, bake_anim_use_all_bones=all_bones,
            bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
            bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
            path_mode="COPY", embed_textures=False,
        )
    return path


# --------------------------------------------------------------------------
# 按物体导出 FBX（给 UE 用）
# --------------------------------------------------------------------------

# 轴向预设：UE / Unity 都吃 Y-up 的 FBX（它们导入时会自己转到 Z-up）
AXIS_PRESETS = {
    "ue": ("-Z", "Y"),
    "unity": ("-Z", "Y"),
    "blender": ("Y", "Z"),
}


def _has_object_curves(ob):
    """这个物体的动画里有没有「物体级」曲线（location/rotation/scale）？

    只有骨骼曲线时，物体自身的变换纯属「挂在哪」，导出前可以放心归零。
    """
    ad = getattr(ob, "animation_data", None)
    if ad is None or ad.action is None:
        return False
    try:
        curves = compat.bag(ad.action, ob) or []
    except Exception:
        return False
    for fc in curves:
        if not fc.data_path.startswith("pose.bones"):
            return True
    return False


def _export_selection(path, axis="ue", deform_only=False, all_bones=True,
                      frame_range=None, take_name=None, unit=None):
    fwd, up = AXIS_PRESETS.get(axis, AXIS_PRESETS["ue"])
    if unit is None:
        unit = "cm" if axis == "ue" else "m"
    arms = [ob for ob in bpy.context.selected_objects if ob.type == "ARMATURE"]
    meshes = [ob for ob in bpy.context.selected_objects if ob.type == "MESH"]
    scn = bpy.context.scene
    old_range = (scn.frame_start, scn.frame_end)
    old_name = scn.name
    if frame_range:
        scn.frame_start, scn.frame_end = int(frame_range[0]), int(frame_range[1])
    # Blender 的 FBX 导出在用「当前动作」烘焙时，take 名取的是场景名。
    # 临时把场景改成动作名，UE 里导入出来的动画序列才不是一堆 "Scene"。
    if take_name:
        try:
            scn.name = take_name
        except Exception:
            pass
    try:
        with contextlib.ExitStack() as stack:
            for ob in bpy.context.selected_objects:
                if ob.type == "ARMATURE" and axis != "ue":
                    stack.enter_context(_export_with_root(ob))
            for k, arm in enumerate(arms):
                # 网格只跟第一个骨架一起缩放，多骨架时不重复乘
                stack.enter_context(_ue_unit(arm, meshes if k == 0 else (), unit))
            if not arms and meshes:
                # 没有骨架（道具、纯网格）：网格自己也要换成厘米
                stack.enter_context(_ue_unit(None, meshes, unit))
            bpy.ops.export_scene.fbx(
                filepath=path, use_selection=True,
                object_types={"ARMATURE", "MESH"},
                apply_scale_options="FBX_SCALE_ALL", global_scale=1.0,
                use_mesh_modifiers=True, mesh_smooth_type="FACE",
                add_leaf_bones=False,
                use_armature_deform_only=deform_only,
                primary_bone_axis="Y", secondary_bone_axis="X",
                armature_nodetype="NULL",
                axis_forward=fwd, axis_up=up,
                bake_anim=True, bake_anim_use_all_bones=all_bones,
                bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
                bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
                path_mode="COPY", embed_textures=False,
            )
    finally:
        scn.frame_start, scn.frame_end = old_range
        if take_name and scn.name != old_name:
            try:
                scn.name = old_name
            except Exception:
                pass
    return path


def object_action_range(ob):
    ad = getattr(ob, "animation_data", None)
    if ad is None or ad.action is None:
        return None
    try:
        lo, hi = ad.action.frame_range
        return int(round(lo)), int(round(hi))
    except Exception:
        return None


def export_object(obj, path, axis="ue", include_mesh=True,
                  deform_only=False, all_bones=True, take_name=None):
    """把**一个**物体（连同它的蒙皮网格）导出成独立的 FBX。

    要点（给 UE 用）：
      * 先断开父级 —— 道具挂角色骨上时，父级的运动会漏进导出，变成
        「物体自己也在动」，导入 UE 就成双份动画；
      * 只有骨骼曲线时把物体变换归零，根节点落在原点、朝向干净；
      * 骨架和动画用同一套骨骼轴向（Y/X），UE 侧导入骨架和动画才对得上；
      * 导出期间把数据换成厘米（UE 的单位），这样 UE 导入后根骨 p_cf_anim
        的缩放是 1；不换的话 UE 会把「米→厘米」的 100 倍乘到根骨上，
        做「兼容骨架」重定向时动画就会错。
    """
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    saved = {
        "parent": obj.parent,
        "ptype": obj.parent_type,
        "pbone": obj.parent_bone,
        "pinv": obj.matrix_parent_inverse.copy(),
        "basis": obj.matrix_basis.copy(),
        "world": obj.matrix_world.copy(),
    }
    meshes = []
    if include_mesh:
        for ch in obj.children_recursive:
            if ch.type == "MESH":
                meshes.append((ch, ch.matrix_world.copy(), ch.parent))
    rng = object_action_range(obj)

    try:
        bpy.ops.object.select_all(action="DESELECT")
        has_obj_anim = _has_object_curves(obj)
        # 只清位置和旋转，**保留缩放**：工作室 FBX 角色的骨架是 0.1 缩放、
        # 骨头在原始单位里，连缩放一起清零会把角色放大 10 倍。
        keep = saved["world"].to_scale()
        scale_only = Matrix.Diagonal(keep.to_4d())
        if obj.parent is not None:
            obj.parent = None
            if not has_obj_anim:
                obj.matrix_world = scale_only
            else:
                obj.matrix_world = saved["world"]
        elif not has_obj_anim:
            obj.matrix_basis = scale_only

        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        for ch, _w, _p in meshes:
            ch.select_set(True)
        _export_selection(path, axis=axis, deform_only=deform_only,
                          all_bones=all_bones, frame_range=rng,
                          take_name=take_name)
    finally:
        # 还原父子关系和变换
        obj.parent = saved["parent"]
        if saved["parent"] is not None:
            obj.parent_type = saved["ptype"]
            obj.parent_bone = saved["pbone"]
            obj.matrix_parent_inverse = saved["pinv"]
        obj.matrix_basis = saved["basis"]
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    return path


def export_objects(objs, outdir=None, prefix="", axis="ue",
                   include_mesh=True):
    """一批物体，每个导出成 <名字>.fbx。返回 [(物体名, 路径)]"""
    import re
    out = []
    outdir = outdir or bpy.path.abspath("//") or os.getcwd()
    os.makedirs(outdir, exist_ok=True)
    for ob in objs:
        safe = re.sub(r'[<>:"/\\|?*]', "_", ob.name)
        path = os.path.join(outdir, "%s%s.fbx" % (prefix, safe))
        try:
            export_object(ob, path, axis=axis, include_mesh=include_mesh)
            out.append((ob.name, path))
        except Exception as e:
            print("[HS2] 导出 %s 失败：%s" % (ob.name, e))
    return out


class HS2_OT_batch_export(bpy.types.Operator):
    """批量导出：按名字过滤，一次导一排动作（原始 FK，不带控制器改动）"""
    bl_idname = "hs2.batch_export"
    bl_label = "批量导出动作"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="输出目录", subtype="DIR_PATH", default="")
    filter: StringProperty(name="名字过滤", default="",
                           description="子串匹配；留空 = 全部")
    limit: bpy.props.IntProperty(name="最多导出", default=0, min=0,
                                 description="0 = 不限制")
    include_mesh: BoolProperty(name="带蒙皮网格", default=False)

    def invoke(self, context, event):
        if not self.directory:
            blend = bpy.data.filepath
            base = os.path.dirname(blend) if blend else os.path.expanduser("~")
            self.directory = os.path.join(base, "hs2_fbx")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "directory")
        col.prop(self, "filter")
        col.prop(self, "limit")
        col.prop(self, "include_mesh")
        col.label(text="导出的游戏原生动作（纯 FK），不含控制器上的改动",
                  icon="INFO")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        out_dir = bpy.path.abspath(self.directory)
        if not out_dir:
            self.report({"ERROR"}, "没有输出目录")
            return {"CANCELLED"}
        os.makedirs(out_dir, exist_ok=True)

        g = get_assets(game_dir_of(context))
        idx = g.clip_index()
        names = sorted(idx.keys())
        if self.filter.strip():
            f = self.filter.strip().lower()
            names = [n for n in names if f in n.lower()]
        if self.limit:
            names = names[:self.limit]
        if not names:
            self.report({"ERROR"}, "没有匹配的动作")
            return {"CANCELLED"}

        had_rig = rig.has_rig(arm)
        if had_rig:
            rig.set_ik(arm, False)

        wm = context.window_manager
        wm.progress_begin(0, len(names))
        ok = 0
        failed = []
        t0 = time.time()
        try:
            for i, name in enumerate(names):
                wm.progress_update(i)
                try:
                    _apply_clip(context, arm, name, 30.0, follow=False)
                    safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)
                    _export(arm, os.path.join(out_dir, safe + ".fbx"),
                            self.include_mesh, True)
                    ok += 1
                except Exception as e:
                    failed.append(name)
                    print("[HS2] 批量导出失败", name, e)
                finally:
                    act = arm.animation_data.action if arm.animation_data else None
                    if act is not None:
                        bpy.data.actions.remove(act)
        finally:
            wm.progress_end()
            if had_rig:
                rig.set_ik(arm, True)

        msg = "导出 %d / %d 个动作，用时 %.0fs" % (ok, len(names), time.time() - t0)
        if failed:
            msg += "；失败 %d 个（见控制台）" % len(failed)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


CLASSES = (
    HS2_OT_detect_dir,
    HS2_OT_apply_hand_pose,
    HS2_OT_import_vmd,
    HS2_OT_import_mixamo,
    HS2_OT_import_character_fbx,
    HS2_OT_align_fbx_pose,
    HS2_OT_load_character_card,
    HS2_OT_load_default,
    HS2_OT_add_head,
    HS2_OT_scan_clips,
    HS2_OT_show_in_front,
    HS2_OT_toggle_jointfix,
    HS2_OT_import_clip,
    HS2_OT_add_rig,
    HS2_OT_clear_rig,
    HS2_OT_toggle_ik,
    HS2_OT_snap_ik,
    HS2_OT_snap_fk,
    HS2_OT_key_targets,
    HS2_OT_bake_fk,
    HS2_OT_export_fbx,
    HS2_OT_export_char_anim,
    HS2_OT_batch_export,
)


def _do_reload():
    """在操作符上下文之外执行重载（RNA 指针已释放，安全）。"""
    import sys

    import addon_utils

    pkg = __name__.rsplit(".", 1)[0]

    # 安全序列：先正规注销（RNA 类不能在注册状态下被 reload），
    # 再删掉模块缓存，最后 enable 全新导入。
    addon_utils.disable(pkg, default_set=False)
    for m in [m for m in list(sys.modules)
              if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[m]
    addon_utils.enable(pkg, default_set=True)
    print("[hs2] 插件代码已重载")
    return None                      # 不重复


class HS2_OT_reload(bpy.types.Operator):
    """重载插件代码（改完代码后点这个，不用重启 Blender）"""
    bl_idname = "hs2.reload"
    bl_label = "重载插件代码"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # 重载会注销本操作符自己的类 —— 不能在本体里直接做（RNA 指针
        # 还被 C 层拿着，report/返回都会段错误）。挂到 timer，
        # 等操作符完整返回后再执行。
        bpy.app.timers.register(_do_reload)
        self.report({"INFO"}, "正在重载插件代码…")
        return {"FINISHED"}


CLASSES = CLASSES + (HS2_OT_reload,)
