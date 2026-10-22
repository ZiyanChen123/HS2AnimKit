"""给角色穿衣服 / 把游戏里的衣服导出成 FBX + 贴图。

数据层在 :mod:`hs2_rig_kit.ripper.clothes`；这里只负责 Blender 对象、
材质预览和导出。衣服直接建在**当前角色**的骨架上，所以会跟着角色动作走。
"""

import contextlib
import os
import re

import bpy

from . import bdata
from . import tex
from .ripper import clothes as rc
from .ripper import mesh as rmesh

CLOTH_COLL = "HS2_Clothes"
EXPORT_COLL = "HS2_ClothExport"


def _linear_color(c):
    """HS2 的材质颜色是 sRGB 显示值，Blender 节点输入要线性值。"""
    if not c:
        return None
    def f(x):
        x = max(0.0, min(1.0, float(x)))
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    return (f(c[0]), f(c[1]), f(c[2]),
            float(c[3]) if len(c) > 3 else 1.0)

ROLE_MAP = (
    ("_MainTex", "basecolor"),
    ("_BumpMap", "normal"),
    ("_MetallicGlossMap", "metallic_gloss"),
    ("_OcclusionMap", "occlusion"),
    ("_AlphaMask", "alpha_mask"),
    ("_AlphaMask2", "alpha_mask2"),
    ("_ColorMask", "color_mask"),
    ("_DetailMask", "detail_mask"),
    ("_DetailGlossMap", "detail_gloss"),
    ("_DetailGlossMap2", "detail_gloss2"),
    ("_DetailAlbedoMap", "detail_albedo"),
    ("_DetailNormalMap", "detail_normal"),
    ("_DetailMainTex", "detail_main"),
    ("_EmissionMap", "emission"),
    ("_ParallaxMap", "parallax"),
    ("_PatternMask1", "pattern_mask1"),
    ("_PatternMask2", "pattern_mask2"),
    ("_PatternMask3", "pattern_mask3"),
    ("_WeatheringMap", "weathering"),
    ("_WeatheringMask", "weathering_mask"),
)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _safe(name, fallback="item"):
    s = re.sub(r'[<>:"/\\|?*]+', "_", name or "").strip(" ._")
    return s or fallback


def _remove_object(obj):
    if obj is None or obj.name not in bpy.data.objects:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
        except Exception:
            pass


def _remove_collection(coll):
    if coll is None:
        return
    for obj in list(coll.objects):
        _remove_object(obj)
    name = coll.name
    try:
        bpy.data.collections.remove(coll)
    except Exception:
        pass
    return name


def _character_collection(arm, create=True):
    """每个角色一个衣服集合，集合名记在骨架自定义属性上。"""
    name = arm.get("hs2_cloth_coll")
    coll = bpy.data.collections.get(name) if name else None
    if coll is None:
        prefix = CLOTH_COLL + "_" + arm.name
        for c in bpy.data.collections:
            if c.name == prefix or c.name.startswith(prefix + "."):
                coll = c
                break
    if coll is None and create:
        coll = bpy.data.collections.new(CLOTH_COLL + "_" + arm.name)
        bpy.context.scene.collection.children.link(coll)
        arm["hs2_cloth_coll"] = coll.name
    return coll


def _bone_names(arm, skel):
    names = bdata._unique_names(skel)
    if len(names) > len(arm.data.bones):
        names = [b.name for b in arm.data.bones]
    return names


def _bone_indices(data, skel):
    out = []
    for path in data.get("bones") or []:
        idx = skel.index_of(path)
        out.append(idx if idx is not None else -1)
    return out


def _material(info, tag, bundle_path, bundle, preview, use_alpha=False):
    name = "%s_%s" % (tag, _safe(info.get("name"), "mat"))
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    # 不能按名字找：Blender 5.2 里默认节点的显示名会本地化。按类型找，
    # 并且优先用已经接到 Material Output 的那个，避免出现「输出接着空 BSDF、
    # 贴图全连在另一个 BSDF 上」。
    out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
    connected = None
    try:
        if out.inputs["Surface"].links:
            src = out.inputs["Surface"].links[0].from_node
            if src.type == "BSDF_PRINCIPLED":
                connected = src
    except Exception:
        pass
    bsdf = connected or next(
        (n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    # 生成材质每次重建一套干净节点（旧的重复 BSDF/贴图/混合节点全清掉）。
    old_imgs = [n.image for n in nodes
                if n.type == "TEX_IMAGE" and n.image is not None]
    for n in list(nodes):
        if n not in (out, bsdf):
            try:
                nodes.remove(n)
            except Exception:
                pass
    try:
        links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    except Exception:
        pass
    for img in old_imgs:
        try:
            if img.users == 0:
                bpy.data.images.remove(img)
        except Exception:
            pass
    raw_color = (info.get("colors") or {}).get("_Color") \
        or (info.get("colors") or {}).get("_BaseColor")
    color = _linear_color(raw_color)
    if color:
        try:
            bsdf.inputs["Base Color"].default_value = color
            mat.diffuse_color = color
            if "Alpha" in bsdf.inputs:
                # 透明度只由 MainTex 的 A 遮罩决定，不用 _Color 的 alpha。
                bsdf.inputs["Alpha"].default_value = 1.0
        except Exception:
            pass
    main = (info.get("texenvs") or {}).get("_MainTex") if preview else None
    if main:
        try:
            img, _err = tex._load_texture(None, bundle_path, main, bundle=bundle)
            if img is not None:
                try:
                    img.colorspace_settings.name = "sRGB"
                except Exception:
                    pass
                node = nodes.new("ShaderNodeTexImage")
                node.image = img
                node.label = "MainTex"
                base_socket = node.outputs["Color"]
                if color:
                    # HS2 的 MainTex 多半是白底/遮罩，真正颜色在 _Color 里。
                    # 直接接贴图会整件发白，先乘上 _Color 才看得出是哪套。
                    mix = nodes.new("ShaderNodeMixRGB")
                    mix.blend_type = "MULTIPLY"
                    mix.inputs[0].default_value = 1.0
                    mix.inputs[1].default_value = color
                    links.new(node.outputs["Color"], mix.inputs[2])
                    base_socket = mix.outputs[0]
                # 先接上基础色，后面图案节点即使失败也不会掉回白色。
                links.new(base_socket, bsdf.inputs["Base Color"])
                # 图案色在 _Color2/_Color3/_Color4，由 ColorMask 的 R/G/B
                # 分区。只乘 _Color 会把有领子/袖口/花纹的衣服渲染成一片白。
                try:
                    mask = (info.get("texenvs") or {}).get("_ColorMask") \
                        if preview else None
                    if mask:
                        mask_img, _merr = tex._load_texture(
                            None, bundle_path, mask, bundle=bundle)
                        if mask_img is not None:
                            try:
                                mask_img.colorspace_settings.name = "Non-Color"
                            except Exception:
                                pass
                            mnode = nodes.new("ShaderNodeTexImage")
                            mnode.image = mask_img
                            mnode.label = "ColorMask"
                            try:
                                sep = nodes.new("ShaderNodeSeparateColor")
                            except Exception:
                                sep = nodes.new("ShaderNodeSeparateRGB")
                            links.new(mnode.outputs["Color"], sep.inputs["Color"])
                            # DetailMask.R 标记「真正布料」的区域；按钮/警徽
                            # 的 UV 岛在 DetailMask 里是 0。不乘它，ColorMask
                            # 的矩形会把整块按钮底涂成黑色。
                            detail_factor = None
                            dmask = (info.get("texenvs") or {}).get("_DetailMask") \
                                if preview else None
                            if dmask:
                                d_img, _derr = tex._load_texture(
                                    None, bundle_path, dmask, bundle=bundle)
                                if d_img is not None:
                                    try:
                                        d_img.colorspace_settings.name = "Non-Color"
                                    except Exception:
                                        pass
                                    dnode = nodes.new("ShaderNodeTexImage")
                                    dnode.image = d_img
                                    dnode.label = "DetailMask"
                                    try:
                                        dsep = nodes.new("ShaderNodeSeparateColor")
                                    except Exception:
                                        dsep = nodes.new("ShaderNodeSeparateRGB")
                                    links.new(dnode.outputs["Color"],
                                              dsep.inputs["Color"])
                                    detail_factor = dsep.outputs["Red"]
                            colors = info.get("colors") or {}
                            for cname, channel in (("_Color2", "Red"),
                                                   ("_Color3", "Green"),
                                                   ("_Color4", "Blue")):
                                cval = _linear_color(colors.get(cname))
                                if not cval:
                                    continue
                                pat = nodes.new("ShaderNodeMixRGB")
                                pat.blend_type = "MULTIPLY"
                                pat.inputs[0].default_value = 1.0
                                pat.inputs[1].default_value = cval
                                links.new(node.outputs["Color"], pat.inputs[2])
                                mix = nodes.new("ShaderNodeMixRGB")
                                mix.blend_type = "MIX"
                                fac = sep.outputs[channel]
                                if detail_factor is not None:
                                    gate = nodes.new("ShaderNodeMath")
                                    gate.operation = "MULTIPLY"
                                    links.new(fac, gate.inputs[0])
                                    links.new(detail_factor, gate.inputs[1])
                                    fac = gate.outputs[0]
                                links.new(fac, mix.inputs[0])
                                links.new(base_socket, mix.inputs[1])
                                links.new(pat.outputs[0], mix.inputs[2])
                                base_socket = mix.outputs[0]
                    links.new(base_socket, bsdf.inputs["Base Color"])
                except Exception:
                    pass
                # MainTex 的 A 通道不一定是透明度：很多衣服拿它当颜色
                # 遮罩，整件 A=1 只有领子/花纹处 A=0。直接接 Alpha 会把
                # 这些地方变透明，露出白色身体。只有整张图明显带透明
                # （平均 A 明显小于 1）时才当透明度用。
                alpha_mean = 1.0
                try:
                    alpha_mean = float(tex._image_pixels(img)[:, :, 3].mean())
                except Exception:
                    pass
                if "Alpha" in bsdf.inputs:
                    if use_alpha or alpha_mean < 0.9:
                        links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
                        try:
                            mat.surface_render_method = "DITHERED"
                        except Exception:
                            pass
                    else:
                        # 大身布料不整体用 A，但「黑底 + A<0.5」的贴纸边框
                        # （扣子/警徽的方框）要切掉；白色领子即使 A=0 也
                        # 保持不透明，避免领子消失。
                        try:
                            bw = nodes.new("ShaderNodeRGBToBW")
                            links.new(node.outputs["Color"], bw.inputs["Color"])
                            dark = nodes.new("ShaderNodeMath")
                            dark.operation = "LESS_THAN"
                            dark.inputs[1].default_value = 0.08
                            links.new(bw.outputs[0], dark.inputs[0])
                            at = nodes.new("ShaderNodeMath")
                            at.operation = "LESS_THAN"
                            at.inputs[1].default_value = 0.5
                            links.new(node.outputs["Alpha"], at.inputs[0])
                            cut = nodes.new("ShaderNodeMath")
                            cut.operation = "MULTIPLY"
                            links.new(dark.outputs[0], cut.inputs[0])
                            links.new(at.outputs[0], cut.inputs[1])
                            keep = nodes.new("ShaderNodeMath")
                            keep.operation = "SUBTRACT"
                            keep.inputs[0].default_value = 1.0
                            links.new(cut.outputs[0], keep.inputs[1])
                            links.new(keep.outputs[0], bsdf.inputs["Alpha"])
                            mat.surface_render_method = "DITHERED"
                        except Exception:
                            bsdf.inputs["Alpha"].default_value = 1.0
        except Exception:
            pass
    # 法线：_BumpMap 是 DXT5nm/BC7，先转成标准切线空间法线再接 Normal Map。
    if preview:
        bump = (info.get("texenvs") or {}).get("_BumpMap")
        if bump:
            try:
                nimg, _err = tex._load_texture(
                    None, bundle_path, bump, bundle=bundle)
                if nimg is not None:
                    try:
                        nimg.colorspace_settings.name = "Non-Color"
                    except Exception:
                        pass
                    tex._normalize_normal(nimg)
                    nnode = nodes.new("ShaderNodeTexImage")
                    nnode.image = nimg
                    nnode.label = "Normal"
                    nmap = nodes.new("ShaderNodeNormalMap")
                    try:
                        strength = float((info.get("floats") or {}).get(
                            "_BumpScale", 1.0) or 1.0)
                        nmap.inputs["Strength"].default_value = max(0.0, strength)
                    except Exception:
                        pass
                    links.new(nnode.outputs["Color"], nmap.inputs["Color"])
                    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
            except Exception:
                pass
    # 衣服基本都需要双面可见。
    try:
        mat.use_backface_culling = False
    except Exception:
        pass
    return mat


# --------------------------------------------------------------------------
# 建衣服
# --------------------------------------------------------------------------

def build_garment(game_dir, record, state, arm, skel, collection,
                  preview=True, bundle=None, data=None):
    """把一件衣服建到给定骨架上，返回 (data, objects)。"""
    if data is None:
        data = rc.extract_garment(game_dir, record, state, bundle=bundle)
    names = _bone_names(arm, skel)
    scale = float(arm.get("hs2_scale") or 0.1)
    obj_tag = record.get("prefab", "garment") + (("_" + state) if state else "")
    # 预览材质会引用临时 DDS，导出时必须用另一套材质名，免得 FBX 去复制
    # 已经删掉的临时贴图。
    mat_tag = obj_tag + ("_prev" if preview else "")
    objects = []
    mat_cache = {}
    used_mats = []
    for smr in data["smrs"]:
        md = dict(smr["mesh"])
        # Unity 的 UV 原点是左下、贴图按 DDS 解码后是上下相反的；不翻 V
        # 会把整张图集上下颠倒地贴到衣服上（看起来像平铺/错位）。
        uvs = md.get("uvs") or []
        if uvs:
            md["uvs"] = [(u, 1.0 - v) for u, v in uvs]
        md["bone_index"] = _bone_indices(md, skel)
        md["flip"] = rmesh.needs_flip(md)
        face_count = len(md.get("triangles") or []) // 3
        smr_blob = (smr.get("name") or "").lower()
        for mpid in smr.get("materials") or []:
            info = next((m for m in data["materials"] if m["pid"] == mpid), None)
            if info:
                smr_blob += " " + (info.get("name") or "").lower()
        # 小面片（扣子、警徽、贴章）通常是带透明底的 decal，必须用 alpha；
        # 大身布料则不能拿 MainTex 的 A 当透明度，否则领口/花纹会消失。
        use_alpha = (face_count < 1200 or "emblem" in smr_blob
                     or "badge" in smr_blob or "button" in smr_blob)
        obj = bdata.build_mesh(
            md, skel, names, arm,
            name="%s_%s" % (obj_tag, smr.get("name") or "part"),
            scale=scale, collection=collection)
        obj["hs2_cloth_slot"] = record.get("slot", "")
        obj["hs2_cloth_prefab"] = record.get("prefab", "")
        obj["hs2_cloth_state"] = state
        obj["hs2_cloth_id"] = record.get("id", "")
        obj["hs2_cloth_bundle"] = record.get("bundle", "")
        have = {m.name for m in obj.data.materials if m}
        pid_slot = {}
        for mpid in smr.get("materials") or []:
            info = next((m for m in data["materials"] if m["pid"] == mpid), None)
            if info is None:
                continue
            mat = mat_cache.get((mpid, use_alpha))
            if mat is None:
                mat = _material(info, mat_tag, record.get("bundle", ""),
                                bundle, preview, use_alpha=use_alpha)
                mat_cache[(mpid, use_alpha)] = mat
                used_mats.append(mat)
            if mat.name not in have:
                obj.data.materials.append(mat)
                have.add(mat.name)
            slot = 0
            for si, slot_mat in enumerate(obj.data.materials):
                if slot_mat is not None and slot_mat.name == mat.name:
                    slot = si
                    break
            pid_slot[mpid] = slot
        # 一个 SMR 里可能有多段 submesh，分别对应不同材质。只把所有面都
        # 设成材质 0 会把警徽/扣子/织带等小面片也套上主布料的 ColorMask，
        # 看起来就像每个扣子后面多了一个黑色方框。
        submeshes = smr.get("submeshes") or []
        mat_pids = smr.get("materials") or []
        if len(submeshes) > 1 and mat_pids and len(obj.data.materials) > 1:
            isize = 2 if md.get("index_format", 0) == 0 else 4
            polys = obj.data.polygons
            for si, (first, count) in enumerate(submeshes):
                mpid = mat_pids[min(si, len(mat_pids) - 1)]
                slot = pid_slot.get(mpid, 0)
                start = max(0, int(first) // isize // 3)
                end = min(len(polys), start + max(0, int(count) // 3))
                for pi in range(start, end):
                    polys[pi].material_index = slot
        objects.append(obj)
    data["_mats"] = used_mats
    return data, objects


def wear(game_dir, record, state, arm, skel, preview=True, bundle=None):
    """把一件衣服穿到角色身上；同槽位旧的先脱掉。"""
    slot = record.get("slot", "")
    remove_slot(arm, slot)
    coll = _character_collection(arm, create=True)
    data, objects = build_garment(game_dir, record, state, arm, skel, coll,
                                  preview=preview, bundle=bundle)
    return data, objects


def _cleanup_materials(mats):
    """预览材质用完后把没人引用的材质/贴图一起清掉，避免换几十套后内存暴涨。"""
    for mat in mats:
        if mat is None:
            continue
        imgs = []
        try:
            if mat.use_nodes:
                imgs = [n.image for n in mat.node_tree.nodes
                        if n.type == "TEX_IMAGE" and n.image is not None]
        except Exception:
            pass
        try:
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        except Exception:
            pass
        for img in imgs:
            try:
                if img.users == 0:
                    bpy.data.images.remove(img)
            except Exception:
                pass


def remove_slot(arm, slot):
    coll = _character_collection(arm, create=False)
    if coll is None:
        return 0
    removed = 0
    mats = []
    for obj in list(coll.objects):
        if obj.get("hs2_cloth_slot") == slot:
            mats.extend(m for m in obj.data.materials if m)
            _remove_object(obj)
            removed += 1
    _cleanup_materials(mats)
    if removed and not coll.objects:
        name = coll.name
        _remove_collection(coll)
        if arm.get("hs2_cloth_coll") == name:
            try:
                del arm["hs2_cloth_coll"]
            except Exception:
                pass
    return removed


def clear_character(arm):
    coll = _character_collection(arm, create=False)
    if coll is None:
        return
    mats = []
    for obj in coll.objects:
        mats.extend(m for m in obj.data.materials if m)
    _remove_collection(coll)
    _cleanup_materials(mats)
    try:
        del arm["hs2_cloth_coll"]
    except Exception:
        pass


def current_slots(arm):
    """当前这个角色身上每件衣服的 (slot, prefab, state)。"""
    coll = _character_collection(arm, create=False)
    if coll is None:
        return {}
    out = {}
    for obj in coll.objects:
        slot = obj.get("hs2_cloth_slot")
        if slot:
            out[slot] = (obj.get("hs2_cloth_prefab"),
                         obj.get("hs2_cloth_state"))
    return out


def worn_jobs(arm):
    """当前角色身上穿的衣服（多网格只算一件）。"""
    coll = _character_collection(arm, create=False)
    if coll is None:
        return []
    out = []
    seen = set()
    for obj in coll.objects:
        prefab = obj.get("hs2_cloth_prefab")
        if not prefab:
            continue
        bundle = obj.get("hs2_cloth_bundle") or ""
        ident = obj.get("hs2_cloth_id") or ""
        state = obj.get("hs2_cloth_state") or ""
        key = (bundle, prefab, str(ident), state)
        if key in seen:
            continue
        seen.add(key)
        out.append({"bundle": bundle, "prefab": prefab, "id": str(ident),
                    "state": state, "slot": obj.get("hs2_cloth_slot") or ""})
    return out


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _export_with_root(arm):
    if arm.name == "p_cf_anim":
        yield
        return
    old_obj, old_data = arm.name, arm.data.name
    try:
        arm.name = "p_cf_anim"
        arm.data.name = "p_cf_anim"
        yield
    finally:
        arm.name = old_obj
        arm.data.name = old_data


def _export_fbx(arm, objects, path):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = arm
    ad = arm.animation_data
    if ad is not None:
        ad.action = None
    with _export_with_root(arm):
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=True,
            object_types={"ARMATURE", "MESH"},
            apply_scale_options="FBX_SCALE_ALL", global_scale=1.0,
            use_mesh_modifiers=True, mesh_smooth_type="FACE",
            add_leaf_bones=False,
            use_armature_deform_only=False,
            primary_bone_axis="Y", secondary_bone_axis="X",
            armature_nodetype="NULL",
            axis_forward="-Z", axis_up="Y",
            bake_anim=False,
            path_mode="COPY", embed_textures=False,
        )


def _export_textures(record, data, out_dir, bundle, flip_green=False):
    """把用到的材质贴图分别写成 PNG，返回 (manifest, warnings, count)。"""
    bundle_path = record.get("bundle", "")
    stem = data.get("_stem") or _safe(record.get("prefab"), "garment")
    tex_dir = os.path.join(out_dir, "textures")
    manifest = []
    warnings = []
    count = 0
    for mat in data.get("materials") or []:
        m = {"name": mat.get("name", ""), "textures": {}}
        mat_prefix = "%s_%s" % (stem, _safe(mat.get("name"), "material"))
        tint = _linear_color(
            (mat.get("colors") or {}).get("_Color")
            or (mat.get("colors") or {}).get("_BaseColor"))
        for slot, role in ROLE_MAP:
            info = (mat.get("texenvs") or {}).get(slot)
            if not info:
                continue
            fname = "%s_%s.png" % (mat_prefix, role)
            path = os.path.join(tex_dir, fname)
            if os.path.isfile(path):
                m["textures"][role] = os.path.relpath(path, out_dir)
                count += 1
                continue
            os.makedirs(tex_dir, exist_ok=True)
            written, err = tex.export_texture(
                None, bundle_path, info, path, role,
                flip_green=flip_green, bundle=bundle,
                tint=(tint if role == "basecolor" else None))
            if written:
                m["textures"][role] = os.path.relpath(written, out_dir)
                count += 1
            elif err:
                warnings.append("%s/%s: %s" % (m["name"], role, err))
        manifest.append(m)
    return manifest, warnings, count


def export_one(game_dir, record, state, out_root, arm, skel,
               bundle=None, states=None, export_textures=True,
               flip_green=False, skip_existing=True):
    """导出一件衣服（一个状态）：<out>/<gender>/<slot>/<asset>/<asset>.fbx。"""
    if states is None:
        states = rc.available_states(game_dir, record, bundle=bundle)
    state = (state or "").upper()
    stem = _safe(record.get("prefab"), "garment")
    if states and state:
        stem += "_" + state.lower()
    out_dir = os.path.join(out_root, record.get("gender", "female"),
                           record.get("slot", "cloth"), stem)
    fbx_path = os.path.join(out_dir, stem + ".fbx")
    if skip_existing and os.path.isfile(fbx_path):
        return {"fbx": fbx_path, "skipped": True, "textures": 0,
                "warnings": []}

    data = rc.extract_garment(game_dir, record, state, bundle=bundle)
    data["_stem"] = stem
    coll = bpy.data.collections.new(EXPORT_COLL + "_" + stem)
    bpy.context.scene.collection.children.link(coll)
    objects = []
    mats = []
    try:
        data, objects = build_garment(game_dir, record, state, arm, skel, coll,
                                      preview=False, bundle=bundle, data=data)
        mats = data.get("_mats") or []
        os.makedirs(out_dir, exist_ok=True)
        warnings, ntex = [], 0
        if export_textures:
            _manifest, warnings, ntex = _export_textures(
                record, data, out_dir, bundle, flip_green=flip_green)
        _export_fbx(arm, objects, fbx_path)
        return {"fbx": fbx_path, "skipped": False, "textures": ntex,
                "warnings": warnings}
    finally:
        for obj in list(objects):
            _remove_object(obj)
        _remove_collection(coll)
        for mat in mats:
            try:
                if mat.users == 0:
                    bpy.data.materials.remove(mat)
            except Exception:
                pass


def export_batch(game_dir, records, out_root, progress=None,
                 export_textures=True, flip_green=False,
                 skip_existing=True):
    """一次导一批衣服；每件都单独一个 FBX，a/b 变体各一个文件。"""
    from .ripper import GameAssets
    g = GameAssets(game_dir)
    skel = g.skeleton()

    coll = bpy.data.collections.new(EXPORT_COLL + "_batch")
    bpy.context.scene.collection.children.link(coll)
    arm = None
    bundles = {}
    ok, skipped, failed = 0, 0, []
    total_tex = 0
    warn_count = 0
    try:
        arm, _names = bdata.build_armature(
            skel, name=EXPORT_COLL, scale=0.1, collection=coll)
        for i, rec in enumerate(records):
            try:
                bp = rec.get("bundle")
                b = bundles.get(bp)
                if b is None:
                    b = rc.unityfs.Bundle(bp)
                    bundles[bp] = b
                states = rc.available_states(game_dir, rec, bundle=b) or []
                for st in (states or ["A"]):
                    res = export_one(
                        game_dir, rec, st, out_root, arm, skel,
                        bundle=b, states=states,
                        export_textures=export_textures,
                        flip_green=flip_green, skip_existing=skip_existing)
                    if res["skipped"]:
                        skipped += 1
                    else:
                        ok += 1
                        total_tex += res["textures"]
                        for w in res.get("warnings") or []:
                            warn_count += 1
                            print("[HS2] 贴图警告 %s: %s"
                                  % (rec.get("prefab"), w))
            except Exception as e:
                failed.append((rec.get("prefab") or rec.get("name") or "?",
                               str(e)))
                print("[HS2] 衣服导出失败 %s: %s" % (rec.get("prefab"), e))
            if progress:
                progress(i, len(records), rec.get("name", ""))
    finally:
        for b in bundles.values():
            try:
                b.env = None
            except Exception:
                pass
        if arm is not None:
            _remove_object(arm)
        _remove_collection(coll)
    return {"ok": ok, "skipped": skipped, "failed": failed,
            "textures": total_tex, "warnings": warn_count}


def export_worn(game_dir, jobs, out_root, progress=None,
                export_textures=True, flip_green=False,
                skip_existing=True):
    """只导出角色身上正在穿的衣服。jobs = [(record, state), ...]"""
    from .ripper import GameAssets
    g = GameAssets(game_dir)
    skel = g.skeleton()
    coll = bpy.data.collections.new(EXPORT_COLL + "_worn")
    bpy.context.scene.collection.children.link(coll)
    arm = None
    bundles = {}
    ok, skipped, failed = 0, 0, []
    total_tex = 0
    warn_count = 0
    try:
        arm, _names = bdata.build_armature(
            skel, name=EXPORT_COLL, scale=0.1, collection=coll)
        for i, (rec, state) in enumerate(jobs):
            try:
                bp = rec.get("bundle")
                b = bundles.get(bp)
                if b is None:
                    b = rc.unityfs.Bundle(bp)
                    bundles[bp] = b
                states = rc.available_states(game_dir, rec, bundle=b) or []
                st = state
                res = export_one(
                    game_dir, rec, st, out_root, arm, skel,
                    bundle=b, states=states,
                    export_textures=export_textures,
                    flip_green=flip_green, skip_existing=skip_existing)
                if res["skipped"]:
                    skipped += 1
                else:
                    ok += 1
                    total_tex += res["textures"]
                    for w in res.get("warnings") or []:
                        warn_count += 1
                        print("[HS2] 贴图警告 %s: %s"
                              % (rec.get("prefab"), w))
            except Exception as e:
                failed.append((rec.get("prefab") or rec.get("name") or "?",
                               str(e)))
                print("[HS2] 衣服导出失败 %s: %s" % (rec.get("prefab"), e))
            if progress:
                progress(i, len(jobs), rec.get("name", ""))
    finally:
        for b in bundles.values():
            try:
                b.env = None
            except Exception:
                pass
        if arm is not None:
            _remove_object(arm)
        _remove_collection(coll)
    return {"ok": ok, "skipped": skipped, "failed": failed,
            "textures": total_tex, "warnings": warn_count}
