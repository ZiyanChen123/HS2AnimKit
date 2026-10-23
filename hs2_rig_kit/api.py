"""给「Agent / 脚本 / 面板」共用的一层薄 API。

设计目标：**一句话就能干活**。所以每个函数都尽量短、参数都能猜对，
再加一个 ``hs2.cmd("...")`` 文本入口给 Agent 用 —— 不需要知道 Blender 的
操作符、上下文、面板在哪。

典型用法（Blender 的 Python 控制台里）::

    import hs2_rig_kit.api as hs2
    hs2.character()                      # 加载默认角色
    hs2.load("chair_17_M")               # 装一个工作室动作
    hs2.h_scene("正常位", "M_WLoop1")     # 装一个 H 动作（含道具）
    hs2.export_fbx(r"D:\\out.fbx")
"""
import os
import time

import bpy

from . import animlib, jpcn, ops, props, rig
from .ripper import GameAssets, hdb

STATE = ops.STATE


# --------------------------------------------------------------------------
# 环境
# --------------------------------------------------------------------------

def game_dir():
    p = None
    try:
        p = bpy.context.preferences.addons[__package__.rsplit(".", 1)[0]].preferences
    except Exception:
        p = None
    d = getattr(p, "game_dir", "") if p is not None else ""
    d = bpy.path.abspath(d) if d else ""
    if d and os.path.isfile(os.path.join(d, "abdata", "chara",
                                         "oo_base.unity3d")):
        return d
    return ops.detect_game_dir() or d


def assets():
    d = game_dir()
    if not d:
        raise RuntimeError("没找到游戏目录，先在面板里设置 abdata 的上级目录")
    return ops.get_assets(d)


def arm():
    return ops.current_arm()


def catalog(rebuild=False, progress=None):
    return assets().catalog(rebuild=rebuild, progress=progress)


def h_data(rebuild=False):
    return catalog(rebuild=rebuild).get("h") or {}


# --------------------------------------------------------------------------
# 角色
# --------------------------------------------------------------------------

def character(gender="female", head=True, generic=False, cleanup=True,
              slot=None, name=None, head_bundle=None, head_prefab=None):
    """加载角色。gender: female / male。

    每个角色会记下自己的**槽位**（女1/女2/男1/男2）和性别，H 动作按槽位装，
    「阶段联动」也靠它判断场景里是不是凑齐了一段 3P/百合。
    """
    g = assets()
    taken = set(characters())
    want = slot or (free_slot(gender) if not name else None)
    if want is None:
        want = "f1" if gender == "female" else "m1"
    obj_name = name
    if obj_name is None:
        if want in taken:
            obj_name = "%s_%s_%s" % (ops.ARM_NAME, gender[0].upper(), want)
        else:
            obj_name = ops.ARM_NAME if want == "f1" else (
                "Armature_Male" if want == "m1" else
                "%s_%s" % (ops.ARM_NAME, want))
    if gender == "male":
        arm_obj, names, body, head_obj = ops.build_default(
            g, include_head=head, cleanup=False,
            body_mesh_name="o_body_cm", name=obj_name, gender="male",
            slot=want, head_bundle=head_bundle, head_prefab=head_prefab)
    else:
        arm_obj, names, body, head_obj = ops.build_default(
            g, include_head=head, cleanup=cleanup, name=obj_name,
            gender=gender, slot=want, head_bundle=head_bundle,
            head_prefab=head_prefab)
    arm_obj["hs2_gender"] = gender
    arm_obj["hs2_slot"] = want
    return arm_obj


# --------------------------------------------------------------------------
# 目录 / 搜索
# --------------------------------------------------------------------------

def categories(include_h=True):
    """{大类: {小组: [动作名, ...]}} —— 面板和 Agent 都用这份结构。"""
    cat = catalog()
    out = {}
    for name, e in (cat.get("studio") or {}).items():
        top = e.get("top") or "其它"
        grp = e.get("group") or top
        out.setdefault(top, {}).setdefault(grp, []).append(name)
    for top in out:
        for grp in out[top]:
            out[top][grp].sort()
    if include_h:
        h = cat.get("h") or {}
        if h.get("modes"):
            hh = out.setdefault("h", {}).setdefault("H 场景动作", [])
            for m in h["modes"]:
                if m.get("roles"):
                    hh.append(jpcn.label_of(m))
    return out


def search(keyword="", kind="all", group=None, limit=200, min_fit=None):
    """按关键词/分类搜动作，返回 [{name, kind, category, ...}]"""
    cat = catalog()
    kw = (keyword or "").strip().lower()
    out = []
    if kind in ("all", "studio"):
        for name, e in (cat.get("studio") or {}).items():
            if group and group.lower() not in (e.get("group") or "").lower():
                continue
            if min_fit is not None and (e.get("fit") or 1.0) < min_fit:
                continue
            if kw and kw not in name.lower() and kw not in \
                    (e.get("category") or "").lower() and kw not in \
                    (e.get("group_cn") or ""):
                continue
            out.append({"name": name, "kind": "studio",
                        "category": e.get("category", ""),
                        "group": e.get("group", ""),
                        "group_cn": e.get("group_cn", ""),
                        "fit": e.get("fit", 1.0)})
    if kind in ("all", "h"):
        h = cat.get("h") or {}
        for m in h.get("modes") or []:
            if not m.get("roles"):
                continue
            label = jpcn.label_of(m)
            label_jp = m.get("label_jp") or m.get("label") or ""
            type_jp = m.get("type_jp") or ""
            type_cn = m.get("type_cn") or ""
            if kw and kw not in label.lower() and kw not in label_jp.lower() \
                    and kw not in m["type"].lower() \
                    and kw not in type_jp.lower() and kw not in type_cn.lower():
                continue
            out.append({"name": label, "kind": "h", "key": m["key"],
                        "type": m["type"], "type_jp": type_jp,
                        "type_cn": type_cn, "label_jp": label_jp,
                        "props": [p.get("prefab") for p in m.get("props") or []],
                        "roles": list(m.get("roles") or {}),
                        "category": "%s/%s" % (m["type"], m["pack"]),
                        "group": m["type"],
                        "group_cn": type_cn or type_jp})
    if kw:
        out.sort(key=lambda e: (0 if e["name"].lower().startswith(kw) else 1,
                                len(e["name"])))
    return out[:limit]


def find_mode(text):
    """按中文名 / 日文名 / id / key 找 H 模式。"""
    h = h_data()
    modes = [m for m in (h.get("modes") or []) if m.get("roles")]
    t = (text or "").strip()
    if not t:
        return None
    for m in modes:
        if m.get("key") == t:
            return m
    for m in modes:
        if t in (m.get("label") or "", m.get("label_jp") or "",
                 m.get("label_cn") or ""):
            return m
    low = t.lower()
    hits = [m for m in modes
            if low in (m.get("label") or "").lower()
            or low in (m.get("label_jp") or "").lower()
            or low in (m.get("label_cn") or "").lower()]
    if hits:
        return hits[0]
    if t.isdigit():
        hits = [m for m in modes if str(m.get("id")) == t]
        if len(hits) == 1:
            return hits[0]
    return None


def stages(mode, slot="f1", clean=False):
    """某段 H 动作的阶段列表（原始 clip 级，含 S/M/L 每个变体）。"""
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        return []
    out = hdb.mode_stages(h_data(), m, slot)
    if clean:
        out = [s for s in out if not _is_dirty(s)] or out
    return out


def stage_groups(mode, slot="f1"):
    """阶段按体型分组：一个动作一行，身高/胸围是组内的连续轴。

    游戏里就是这样的：S/M/L 是同一个动作的三个体型版本，靠 height 参数
    （Unity 的 1D 混合树，阈值 0/0.5/1）连续插值；胸部动作则是 5 档胸围。
    """
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        return []
    ctrl_key = (m.get("roles") or {}).get(slot)
    ctrl = (h_data().get("ctrls") or {}).get(ctrl_key or "")
    if not ctrl:
        return []
    return hdb.group_stages(hdb.stage_list(ctrl))


def _find_group(groups, want):
    if not groups:
        return None
    if not want:
        return None
    w = str(want).strip()
    for g in groups:
        if g["key"] == w or g["label"] == w or g["base"] == w:
            return g
    for g in groups:
        if w in g["label"] or any(w == it["name"] for it in g["variants"].values()):
            return g
    return None


def _pick_group(groups, want=None):
    """默认挑「抽插」那一组 —— 比停在待机帧更像一段能看的动作。"""
    g = _find_group(groups, want)
    if g:
        return g
    for key in ("抽插·弱1", "抽插·弱", "抽插·中", "抽插·强"):
        for x in groups:
            if x["label"].startswith(key):
                return x
    for x in groups:
        if "待机" not in x["label"]:
            return x
    return groups[0] if groups else None


def _blend_group(group, height=0.5, bust=0.5, fps=None, paths=None):
    """把组里 1~2 个变体解码出来，按体型参数混合成一条。

    paths 给自定义路径表时按它解码（道具动画要用道具自己的骨骼路径）；
    不给就用角色的骨架路径表。
    """
    from .ripper import clips as _c
    t = bust if group.get("kind") == "bust" else height
    picks = hdb.pick_variant(group, t)
    if not picks:
        raise RuntimeError("这个阶段没有可用变体")
    out = None
    for it, w in picks:
        clip = assets().load_ref(it["bundle"], it["pid"], fps=fps,
                                 name=it["name"], paths=paths)
        if out is None:
            out = clip
            first_w = w
        else:
            out = _c.blend(out, clip, w / max(1e-6, first_w + w))
    return out, picks


HEIGHT_BONE = "cf_N_height"


def set_height_scale(char, letter="M"):
    """按身高档位缩放身高骨骼（S=0.8 / M=0.9 / L=1.0）。

    S/M/L 三档动画是「同一套动作给三个身高」，动画里的手/胯接触点按对应身高
    算的。切到 S 档就要把身体缩矮，否则手会悬空/错位。

    实现：只缩放 ``cf_N_height`` 一根骨（根→骨盆，整体缩「髋以上身体」）。
    比例来自游戏捏人对表（cf_anmShapeBody 的 cf_s_height，线性 0.8→1.0）。

    ⚠️ 不要给**运行姿势骨**再加逐骨非等比缩放：那会把骨架压扁（第 0 帧
    大扭曲、刚加载人变扁）——游戏里的逐骨 shape 是发给独立 blend/shape
    骨、并在 rest 层生效的，直接当 pose 缩放套到主骨骼链上会叠 1 倍以上。
    """
    if char is None:
        return
    pb = char.pose.bones.get(HEIGHT_BONE)
    if pb is None:
        return
    s = hdb.HEIGHT_SCALE.get(letter, 0.9)
    pb.scale = (s, s, s)
    char["hs2_height_scale"] = s
    char["hs2_height_letter"] = letter


def _pick_group_clip(group, letter, fps=None, paths=None):
    """组里取某一档的 clip（**不混合**）。letter 取不到就挑中间档。"""
    it = hdb.pick_variant_item(group, letter)
    if it is None:
        raise RuntimeError("这个阶段没有可用变体")
    return assets().load_ref(it["bundle"], it["pid"], fps=fps,
                             name=it["name"], paths=paths)


# --------------------------------------------------------------------------
# 加载动作
# --------------------------------------------------------------------------

def load(name, fps=None, follow=True):
    """装一个 Studio 动作（按名字），装到当前角色身上。"""
    arm_obj = active_character() or arm()
    if arm_obj is None:
        raise RuntimeError("先加载角色：hs2.character()")
    act, info = ops.apply_clip_by_name(arm_obj, name, fps=fps, follow=follow)
    _remember(act.name)
    try:
        bpy.context.scene.hs2_last = "%s · %d 帧" % (name, info["frames"])
    except Exception:
        pass
    return act, info


# --------------------------------------------------------------------------
# 角色槽：场景里谁是谁
# --------------------------------------------------------------------------

SLOT_CN = {"f1": "女1", "f2": "女2", "m1": "男1", "m2": "男2"}


def _alive(ob):
    """这个物体还在不在（刚被删掉的 RNA 指针会抛异常）。"""
    try:
        return ob is not None and ob.name in bpy.data.objects
    except Exception:
        return False


def characters():
    """{槽: 骨架} —— 只认本插件加载、且带槽位标记的骨架。"""
    out = {}
    for ob in bpy.data.objects:
        try:
            if ob.type != "ARMATURE":
                continue
            if not ob.data.bones.get("cf_J_Root"):
                continue
            slot = ob.get("hs2_slot")
        except Exception:
            continue
        if slot:
            out[slot] = ob
    return out


def active_character():
    """当前要操作的角色：选中的 > 激活的 > 女1 > 第一个。"""
    try:
        ob = bpy.context.view_layer.objects.active
        if _alive(ob) and ob.type == "ARMATURE" and ob.get("hs2_slot"):
            return ob
        for o in bpy.context.selected_objects:
            if _alive(o) and o.type == "ARMATURE" and o.get("hs2_slot"):
                return o
    except Exception:
        pass
    chars = characters()
    if "f1" in chars:
        return chars["f1"]
    return next(iter(chars.values()), None)


def free_slot(gender):
    """给一个性别找空槽：先 f1 再 f2 / 先 m1 再 m2。"""
    return ops.free_slot(gender)


def slot_of(char):
    return char.get("hs2_slot") if char is not None else ""


def gender_of(char):
    if char is None:
        return ""
    return char.get("hs2_gender") or hdb.slot_gender(slot_of(char) or "f1")


def scene_text():
    chars = characters()
    if not chars:
        return "场景里还没有角色"
    parts = []
    for slot in sorted(chars, key=hdb.slot_sort):
        c = chars[slot]
        mode = c.get("hs2_h_mode") or ""
        st = c.get("hs2_h_stage") or ""
        parts.append("%s=%s%s" % (SLOT_CN.get(slot, slot), c.name,
                                  ("（%s %s）" % (mode, st)) if mode else ""))
    return " · ".join(parts)


# --------------------------------------------------------------------------
# H 动作：装到「某个角色」身上
# --------------------------------------------------------------------------

def mode_cast(mode):
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        return {"cast": [], "cast_text": "", "roles": {}}
    roles = m.get("roles") or {}
    return {"cast": hdb.cast_of(roles),
            "cast_text": hdb.cast_text(roles),
            "roles": roles}


def _slot_for(m, char, slot=None):
    """这个角色该用哪个槽。优先它自己的槽，其次按性别找空的。"""
    roles = m.get("roles") or {}
    own = slot or char.get("hs2_slot")
    if own in roles:
        return own
    gender = gender_of(char)
    used = {c.get("hs2_slot") for c in characters().values() if c is not char}
    got = hdb.pick_slot(m, gender, used)
    if got:
        return got
    cand = hdb.slots_of_gender(m, gender)
    return cand[0] if cand else None


def setup_cast(mode):
    """按这段动作需要的人数/性别，把缺的角色补上（最多 3 个）。

    比如 3P 会补成 女1 + 男1 + 男2，百合会补成 女1 + 女2。
    """
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        raise KeyError("没找到 H 模式 %r" % (mode,))
    need = hdb.cast_of(m.get("roles") or {})
    chars = characters()
    added = []
    for slot in need:
        if slot in chars:
            continue
        gender = hdb.slot_gender(slot)
        char = character(gender, slot=slot)
        added.append(slot)
        chars[slot] = char
    # H 场景里的男性生殖器：这段动作有男角就补，没有就摘掉。
    for slot, char in chars.items():
        if hdb.slot_gender(slot) != "male":
            continue
        if slot in need:
            ops.ensure_male_genital(char)
        else:
            ops.remove_male_genital(char)
    return {"slots": need, "added": added, "chars": chars}


def apply_h(mode, stage=None, target=None, slot=None, with_props=True,
            fps=None, follow=True, height=None, bust=None):
    """把一段 H 动作装到**一个**角色上（默认当前选中的角色）。

    mode  : 日文名 / key / 数字 id
    stage : 阶段（组名/中文名/clip 名都行）；不给就挑「抽插·弱1」
    height: 身高档位 "S"/"M"/"L"（矮/中/高），只对「身高」阶段有意义。
            选 S 会同时把 cf_N_height 骨骼缩到 0.8，让身体和 S 档动画对上。
    bust  : 胸围档位 "S"/"SM"/"M"/"ML"/"L"，只对「胸部动作」阶段有意义
            （只切动画，不捏身体 —— 身体变形要解析捏人对表，暂不做）
    返回 {"mode", "slot", "stage", "stages", "action", "info", "props"}
    """
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        raise KeyError("没找到 H 模式 %r" % (mode,))
    char = target or active_character()
    if char is None:
        raise RuntimeError("先加载角色：hs2.character()")
    data = h_data()
    slot = _slot_for(m, char, slot)
    if not slot:
        raise RuntimeError("这段动作里没有%s角色的位置"
                           % ("女" if gender_of(char) == "female" else "男"))
    ctrl_key = (m.get("roles") or {}).get(slot)
    ctrl = (data.get("ctrls") or {}).get(ctrl_key or "")
    if not ctrl:
        raise RuntimeError("这段动作没有 %s 的控制器" % SLOT_CN.get(slot, slot))

    groups = hdb.group_stages(hdb.stage_list(ctrl))
    group = _pick_group(groups, stage)
    if group is None:
        raise RuntimeError("这段动作没有可用的阶段")

    # 档位字母：height/bust 参数 → 记在角色上的字母 → 默认中间档
    h_letter = (height or char.get("hs2_h_height_letter") or "M")
    b_letter = (bust or char.get("hs2_h_bust_letter") or "M")
    letter = (b_letter if group["kind"] == "bust" else h_letter)
    if letter not in group["variants"]:
        letter = {"height": "M", "bust": "M"}.get(group["kind"], letter)
        if letter not in group["variants"]:   # 没有 M 档就取实际存在的中间档
            order = hdb.BUST_ORDER if group["kind"] == "bust" else hdb.HEIGHT_ORDER
            avail = [l for l in order if l in group["variants"]]
            letter = avail[len(avail) // 2] if avail else list(group["variants"])[0]

    clip = _pick_group_clip(group, letter, fps=fps)
    aname = "H_%s_%s_%s" % (jpcn.label_of(m) or m["id"], group["label"], slot)
    act, info = ops.apply_clip_data(char, clip, aname, follow=follow)
    _remember(act.name)

    char["hs2_h_mode"] = m["key"]
    char["hs2_h_slot"] = slot
    char["hs2_h_stage"] = group["key"]
    char["hs2_h_stage_cn"] = group["label"]
    char["hs2_h_kind"] = group["kind"]
    char["hs2_h_height_letter"] = h_letter
    char["hs2_h_bust_letter"] = b_letter
    char["hs2_h_letter"] = letter

    # 身高阶段：切档时同步缩放身高骨骼，让身体和动画对上
    if group["kind"] == "height":
        set_height_scale(char, letter)

    if group["kind"] == "bust":
        size_txt = "胸围·%s" % hdb.BUST_CN.get(letter, letter)
    elif group["kind"] == "height":
        size_txt = "身高·%s" % hdb.HEIGHT_CN.get(letter, letter)
    else:
        size_txt = ""
    out = {"mode": m, "slot": slot,
           "stage": {"name": group["key"], "cn": group["label"],
                     "kind": group["kind"], "size": size_txt,
                     "letter": letter,
                     "variants": [it["name"] for it in group["variants"].values()]},
           "stages": groups, "action": act, "info": info, "props": []}
    if with_props:
        out["props"] = load_mode_props(m, stage=group, char_slot=slot,
                                       height=h_letter, bust=b_letter)
    if gender_of(char) == "male" and slot in (m.get("roles") or {}):
        ops.ensure_male_genital(char)
    return out


def load_h(mode, stage=None, roles=None, fps=None, follow=True,
           with_props=True, keep_props=True, target=None,
           height=None, bust=None):
    """装一段 H 动作。

    不给 roles 时：装到场景里**性别对得上**的角色身上（能装几个装几个）；
    给了 roles（f1/f2/m1/m2 或 female/male）就只装那些槽。
    height/bust 是档位字母（"S"/"M"/"L" 或胸围 5 档），不给就沿用角色已记录的。
    """
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        raise KeyError("没找到 H 模式 %r" % (mode,))
    chars = characters()
    if target is not None:
        res = apply_h(m, stage, target=target, with_props=with_props, fps=fps,
                      follow=follow, height=height, bust=bust)
        if gender_of(target) == "male":
            ops.ensure_male_genital(target)
        return {"mode": m, "actions": {res["slot"]: res["action"]},
                "info": {res["slot"]: res["info"]}, "stage": res["stage"],
                "stages": res["stages"], "props": res["props"]}

    wanted = []
    if roles:
        for r in roles:
            if r in hdb.SLOTS:
                wanted.append(r)
            elif r == "female":
                wanted += [s for s in hdb.SLOTS if hdb.slot_gender(s) == "female"]
            elif r == "male":
                wanted += [s for s in hdb.SLOTS if hdb.slot_gender(s) == "male"]
    else:
        wanted = [s for s in hdb.cast_of(m.get("roles") or {})]

    out = {"mode": m, "actions": {}, "info": {}, "props": [],
           "stage": None, "stages": []}
    for slot in wanted:
        char = chars.get(slot)
        if char is None or slot not in (m.get("roles") or {}):
            continue
        res = apply_h(m, stage, target=char, slot=slot, with_props=False,
                      fps=fps, follow=follow, height=height, bust=bust)
        out["actions"][slot] = res["action"]
        out["info"][slot] = res["info"]
        out["stage"] = res["stage"]
        out["stages"] = res["stages"]
    # 道具是「这一段动作」共享的，整段只装一次（按第一个角色的档位）
    if with_props and out["stage"]:
        first_char = next((c for c in chars.values()
                           if c.get("hs2_h_mode") == m["key"]), None)
        letter = height or bust or (
            first_char.get("hs2_h_letter") if first_char else "M") or "M"
        out["props"] = load_mode_props(m, stage=out["stage"],
                                       height=letter, bust=letter)
    # 有男角就补生殖器；没有的男角色摘掉
    need = set((m.get("roles") or {}).keys())
    for slot, char in chars.items():
        if hdb.slot_gender(slot) != "male":
            continue
        if slot in need:
            ops.ensure_male_genital(char)
        else:
            ops.remove_male_genital(char)
    return out


def h_scene(mode, stage=None, **kw):
    """load_h 的别名 —— 「装一段 H 场景」读起来更顺。"""
    return load_h(mode, stage, **kw)


def _is_dirty(st):
    n = st.get("name") or ""
    return n.startswith("D_") or "_D_" in n


def _pick_stage(stage_list_items, want):
    if not stage_list_items:
        return None
    # D_ 变体是「另一种接续」，界面默认不挑它
    clean = [s for s in stage_list_items if not _is_dirty(s)] or list(stage_list_items)
    if want:
        w = str(want).strip()
        for s in stage_list_items:
            if s["name"] == w or s["cn"] == w or s["stage"] == w:
                return s
        low = w.lower()
        for s in stage_list_items:
            if low in s["name"].lower() or low in s["cn"].lower():
                return s
    for s in clean:
        if s["stage"] in ("WLoop1", "MLoop", "WLoop") and s["speed"] == "M":
            return s
    for s in clean:
        if s["stage"] in ("WLoop1", "MLoop", "WLoop"):
            return s
    return clean[0]


def _char_for_gender(gender, prefer_slot=None):
    chars = characters()
    if prefer_slot and prefer_slot in chars:
        return chars[prefer_slot]
    for slot in sorted(chars, key=hdb.slot_sort):
        if hdb.slot_gender(slot) == gender:
            return chars[slot]
    return active_character()


def _stage_want(stage):
    """把「阶段」归一成 _find_group 能搜的字符串（组 key / 中文名都行）。"""
    if stage is None:
        return None
    if isinstance(stage, dict):
        return (stage.get("key") or stage.get("name")
                or stage.get("cn") or stage.get("label"))
    return str(stage)


def _prop_ctrl_for(p):
    """道具这一行自己的动画控制器 → (key, ctrl)，没有动画就 (None, None)。

    一个 H 模式可能挂好几件道具，每件道具的动画是**独立控制器**
    （控制器名里带着 prop 名，比如 ais_i_45_himolong_armL），
    有的道具（纯挂绳）根本没有动画，表里 animator 是空的。
    """
    name = (p.get("animator") or "").strip()
    if not name:
        return None, None
    bundle = (p.get("anim_bundle") or "").strip().replace("\\", "/")
    ctrls = h_data().get("ctrls") or {}
    # 精确：名字 + 包都对得上（优先）
    for key, c in ctrls.items():
        if c.get("name") == name:
            cb = (c.get("bundle") or "").replace("\\", "/")
            if not bundle or cb.endswith(bundle):
                return key, c
    # 只按名字
    for key, c in ctrls.items():
        if c.get("name") == name:
            return key, c
    # 结尾模糊
    for key, c in ctrls.items():
        cname = c.get("name") or ""
        if cname and (cname.endswith(name) or name.endswith(cname)):
            return key, c
    return None, None


def _prop_group(ctrl_key, want=None):
    """道具控制器的阶段组（按 want 找，找不到挑默认）。

    道具的阶段比角色的粗：角色有 WLoop1/WLoop2，道具只有 WLoop。
    所以先精确找，再去掉数字后缀（WLoop1 → WLoop）找，最后挑默认。
    """
    ctrl = (h_data().get("ctrls") or {}).get(ctrl_key or "")
    if not ctrl:
        return None
    ig = hdb.group_stages(hdb.stage_list(ctrl))
    if not ig:
        return None
    g = _find_group(ig, want)
    if g:
        return g
    w = str(want or "").strip()
    base = w.rstrip("0123456789")
    if base and base != w:
        g = _find_group(ig, base)
        if g:
            return g
    return _pick_group(ig, None)


def _apply_prop_stage(m):
    """把所有属于这段动作的道具，切到当前记录的阶段 + 体型。返回改了几个。

    每件道具用自己的控制器（建道具时记在 hs2_prop_ctrl 上）。
    身高/胸围按道具挂的角色（女/男）各自的档位取，避免混用时拿错档。
    """
    chars = [c for c in characters().values()
             if c.get("hs2_h_mode") == m["key"]]
    if not chars:
        return 0
    # 缺省用第一个角色（新角色没有额外信息时兜底）
    default_char = chars[0]
    want = default_char.get("hs2_h_stage")
    n = 0
    for ob in bpy.data.objects:
        if ob.get("hs2_prop_mode") != m["key"]:
            continue
        ctrl_key = ob.get("hs2_prop_ctrl") or ""
        g = _prop_group(ctrl_key, want)
        if g is None:
            continue
        # 找到这个道具挂的角色（按性别）
        char = default_char
        who = ob.get("hs2_prop_who") or ""
        if who in ("female", "male"):
            for c in chars:
                if gender_of(c) == who:
                    char = c
                    break
        letter = (char.get("hs2_h_bust_letter")
                  if g.get("kind") == "bust"
                  else char.get("hs2_h_height_letter")) or "M"
        try:
            clip = _pick_group_clip(g, letter, paths=props.prop_paths(ob))
            props.apply_clip(ob, clip)
            n += 1
        except Exception:
            pass
    return n


def load_mode_props(mode, stage=None, keep=True, char_slot=None,
                    height=None, bust=None):
    """把这段 H 动画要用的道具全部装进场景并挂好（带道具动画）。

    道具自己的动作也有 S/M/L 身高变体，height/bust 传档位字母（"S"/"M"/"L"…），
    道具跟角色走同一档。
    """
    made = []
    seen = set()
    letter = height or bust or "M"
    for p in hdb.playable_props(mode.get("props") or []):
        prefab = p.get("prefab")
        if not prefab:
            continue
        who = p.get("parent_who") or "female"
        if who not in ("female", "male"):
            # 场景里的固定物（房间、淋浴柱…）——表里写的骨不在角色身上，
            # 交给 load_prop 去「谁有这根骨就挂谁」，找不到就不挂。
            who = ""
        # 同一件道具可能被两个圆盘指到同一件物品上：(prefab, 骨, 挂点) 才算重复
        key = (prefab, p.get("parent_bone") or "", p.get("attach") or "")
        if key in seen:
            continue
        seen.add(key)
        slot = char_slot if (char_slot and hdb.slot_gender(char_slot) == who) else None
        char = _char_for_gender(who, slot) if who else None
        try:
            ob = load_prop(prefab, pack=_pack_of(p), bone=p.get("parent_bone"),
                           char=char, who=who, attach=p.get("attach") or "",
                           scale_adapt=p.get("scale_adapt") == "1",
                           mode=mode.get("key") or "",
                           fps=None)
        except Exception as e:
            print("[HS2] 道具 %s 失败：%s" % (prefab, e))
            continue
        # 每件道具用自己的控制器做动画（有的道具是纯挂件，没有动画）
        ctrl_key, ctrl = _prop_ctrl_for(p)
        if ctrl is not None:
            ob["hs2_prop_ctrl"] = ctrl_key
            g = _prop_group(ctrl_key, _stage_want(stage))
            if g is not None:
                try:
                    # 道具动画要用道具自己的骨骼路径表来解
                    clip = _pick_group_clip(g, letter,
                                            paths=props.prop_paths(ob))
                    props.apply_clip(ob, clip)
                except Exception as e:
                    print("[HS2] 道具动画 %s 失败：%s" % (prefab, e))
        made.append(ob)
    return made


def _pack_of(prop_row):
    bundle = prop_row.get("bundle") or ""
    base = os.path.basename(bundle)
    return base[:-8] if base.endswith(".unity3d") else (base or "01")


def find_bone_owner(bone_name, exclude=None):
    """谁身上有这根骨？

    大多数道具挂在角色的 k_f_* / cf_J_* 上，但有几件是挂在**别的道具**上的
    （比如拘束机械的两条线挂在机械本体的 n_munesaki_L 上、
    淋浴绳挂在 n_rope1 上）。所以这里在「角色 + 已装道具」里一起找。
    """
    for ob in bpy.data.objects:
        if ob is exclude or ob.type != "ARMATURE":
            continue
        if ob.pose.bones.get(bone_name):
            return ob
    return None


def load_prop(prefab, pack="01", bone=None, who=None, anim=None, fps=None,
              char=None, attach="", scale_adapt=False, mode=""):
    """装一个 H 道具。bone 给了就挂到该骨骼（找不到就换到有这根骨的对象上）。

    关键：``parent_who`` 决定了道具归**哪个性别**拿。搜索挂点时要优先在
    对应性别的角色（及其道具）上找，绝不能按 Blender 对象顺序全局抓——
    否则男女都有同名 `k_f_*` 手骨时，男角拿的道具会抓到女角手上。
    """
    g = assets()
    ob = props.build_prop(g, pack, prefab, scale=STATE.get("scale") or 0.1)
    if char is None and who in ("female", "male"):
        char = _char_for_gender(who)

    target = char
    if bone:
        # 先在「这个性别的角色」身上找（char 已按 who 选好）
        if char is not None and char.pose.bones.get(bone):
            target = char
        else:
            # 骨不在角色身上时：先看性别对得上的角色 / 道具，再看谁有这根骨
            target = _find_bone_owner_for_who(bone, who, exclude=ob) or char
        if target is not None:
            if not props.attach_to_bone(ob, target, bone, attach=attach,
                                        scale_adapt=scale_adapt):
                print("[HS2] %s 上没有骨骼 %s" % (target.name, bone))
                target = None
        else:
            print("[HS2] 场景里没有骨骼 %s" % bone)
    ob["hs2_prop_note"] = "%s/%s" % (pack, prefab)
    ob["hs2_prop_who"] = (who or (gender_of(target) if target is not None
                                  else "") or "scene")
    if mode:
        ob["hs2_prop_mode"] = mode
    return ob


def _find_bone_owner_for_who(bone_name, who, exclude=None):
    """只在和 ``who`` 同性别的对象（角色及其子道具）里找这根骨。

    男女角色骨架共用一套 `k_f_*` 骨名，按对象顺序全局 `find_bone_owner`
    会最先抓到先加载的那个（往往是女角），导致男角道具挂到女角身上。
    """
    chars = characters()
    # 先在自己这套性别里找（角色优先，其次道具）
    for slot, ch in sorted(chars.items(), key=lambda kv: hdb.slot_sort(kv[0])):
        if ch is exclude:
            continue
        if who in ("female", "male") and hdb.slot_gender(slot) != who:
            continue
        if ch.pose.bones.get(bone_name):
            return ch
    return _find_in_props(bone_name, who, exclude)


def _find_in_props(bone_name, who, exclude=None):
    """在已装的道具里找这根骨（有的道具是挂在**另一件道具**上的）。"""
    for ob in bpy.data.objects:
        if ob.type != "ARMATURE" or ob is exclude:
            continue
        if ob.get("hs2_slot") or not ob.pose.bones.get(bone_name):
            continue
        par = ob.parent
        if par is None:
            if not who:
                return ob
            continue
        # 道具的父是角色或另一件道具；顺着回角色判断性别
        owner = par
        while owner is not None and owner.type == "ARMATURE" \
                and owner.get("hs2_prop_prefab"):
            owner = owner.parent
        if owner is None or owner.type != "ARMATURE":
            if not who:
                return ob
            continue
        g = gender_of(owner)
        if who in ("female", "male"):
            if g == who:
                return ob
        elif not who:
            return ob
    return None




# --------------------------------------------------------------------------
# 动作驱动：装动作 = 换一场景
# --------------------------------------------------------------------------

def _remove_tree(ob):
    """删一个物体连同它的子物体（道具的网格是子物体）。"""
    for ch in list(ob.children_recursive):
        data = ch.data
        bpy.data.objects.remove(ch, do_unlink=True)
        if data is not None and getattr(data, "users", 0) == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    ops.remove_character(ob) if ob.get("hs2_slot") else \
        bpy.data.objects.remove(ob, do_unlink=True)


def clear_cast():
    """把上一次装进来的演员和道具清掉（用户自己的物体不动）。

    换动作时先清场再装 —— 这是「动作驱动」的核心：场景里有什么，由上一条
    动作决定，不用手动管谁是谁。
    """
    n = 0
    for ob in list(bpy.data.objects):
        try:
            if ob.type != "ARMATURE":
                continue
            if not (ob.get("hs2_slot") or ob.get("hs2_prop_note")):
                continue
        except Exception:
            continue
        try:
            _remove_tree(ob)
            n += 1
        except Exception:
            pass
    # 删完把激活/选中清掉，否则 context 里还攥着已释放的指针
    try:
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        bpy.context.view_layer.objects.active = None
        bpy.context.view_layer.update()
    except Exception:
        pass
    return n


def studio_gender(name):
    """工作室动作是男角还是女角的（按动作库里的分类判断）。

    官方表里的**控制器名**最准：``f_03_Sword`` 是女角、``m_03_Sword`` 是男角，
    ``h2_f_studio_50`` 是 H 女角那一套。老条目没有控制器名，退回看名字前缀。
    """
    try:
        e = (ops.get_assets(game_dir()).clip_index().get(name)) or {}
    except Exception:
        e = {}
    ctrl = (e.get("ctrl") or "").lower()
    if ctrl:
        if ctrl.startswith("m_") and not ctrl.startswith("mc_"):
            return "male"
        if ctrl.startswith("f_"):
            return "female"
        if ctrl.startswith("h2_f"):
            return "female"
        if ctrl.startswith("h2_m"):
            return "male"
    g = (e.get("group") or "").lower()
    if g in ("m", "mc"):
        return "male"
    low = (name or "").lower()
    if low.startswith("m_") and not low.startswith("mc_"):
        return "male"
    return "female"


def load_studio_fresh(name, fps=None):
    """装一条工作室动作：优先保留从角色卡加载的演员。

    ``name`` 可能是带 S/M/L 后缀的身高变体（cook_01_L），装完按变体调整
    角色身高骨架，让身体和该身高的动作对上。
    """
    gender = studio_gender(name)
    chars = characters()
    card = next((c for c in chars.values()
                 if (c.get("hs2_card") or c.get("hs2_custom"))
                 and gender_of(c) == gender), None)
    if card is not None:
        # 卡角色的比例/衣服都是卡里的，不要清场重建；只把动作装到它身上。
        char = card
        try:
            for ob in bpy.context.selected_objects:
                ob.select_set(False)
            char.select_set(True)
            bpy.context.view_layer.objects.active = char
        except Exception:
            pass
    else:
        clear_cast()
        char = character(gender)
    act, info = load(name, fps=fps)
    # 身高变体只对默认角色套；卡角色保留自己的身高。
    if card is None:
        v = hdb.split_variants(name)
        if v["height"]:
            try:
                set_height_scale(char, v["height"])
            except Exception:
                pass
            char["hs2_height_letter"] = v["height"]
    return {"action": act, "info": info, "char": char, "gender": gender}


def _cast_ready(m):
    """场景里现在的演员，正好就是这段动作要的那几个吗？"""
    need = set(hdb.cast_of(m.get("roles") or {}))
    have = set(characters())
    if need != have:
        return False
    for slot in need:
        if gender_of(characters()[slot]) != hdb.slot_gender(slot):
            return False
    return True


def apply_stage_all(m, stage=None, height=None, bust=None):
    """只换阶段/体型：不重建演员，所有角色 + 道具一起切。"""
    done = []
    for slot in hdb.cast_of(m.get("roles") or {}):
        char = characters().get(slot)
        if char is None:
            continue
        apply_h(m, stage, target=char, slot=slot, with_props=False,
                height=height, bust=bust)
        done.append(slot)
    if done:
        _apply_prop_stage(m)
    return done


def apply_variant(m, letter, kind="height", stage=None, target=None):
    """只改**当前角色**的身高/胸围档位，别的演员保持自己的档位。

    H 动作里同一个姿势，男女是**各自的身形**（有高有矮），不该把一套档位
    同步到所有演员身上。这里只更新目标角色的档位字母、重装它那一段动画，
    并同步缩放它的身高骨骼 —— 不碰其他角色。
    """
    m = m if isinstance(m, dict) else find_mode(m)
    if m is None:
        raise KeyError("没找到 H 动作 %r" % (m,))
    char = target or active_character()
    if char is None:
        raise RuntimeError("先加载角色")
    slot = _slot_for(m, char)
    if not slot:
        return None
    if kind == "bust":
        char["hs2_h_bust_letter"] = letter
    else:
        char["hs2_h_height_letter"] = letter
    res = apply_h(m, stage, target=char, slot=slot, with_props=False)
    # 道具是这段动作共享的，按当前档位把道具动画也切过去
    try:
        _apply_prop_stage(m)
    except Exception:
        pass
    return res


def load_h_fresh(mode, stage=None, force=False, height=None, bust=None):
    """装一段 H 动作：还是这段动作就只换阶段，换成别的就清场重装。

    height/bust 是档位字母（"S"/"M"/"L"…），选档时传进来，演员不动、只重装
    这一段动作并同步缩放身高骨骼。
    """
    m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        raise KeyError("没找到 H 动作 %r" % (mode,))
    chars = characters()
    same_mode = bool(chars) and all(
        c.get("hs2_h_mode") == m["key"] for c in chars.values())
    if not force and same_mode and _cast_ready(m):
        apply_stage_all(m, stage, height=height, bust=bust)
        slot0 = hdb.cast_of(m.get("roles") or {})[0]
        ch = characters()[slot0]
        return {"mode": m, "stage": {"cn": ch.get("hs2_h_stage_cn") or ""},
                "reused": True}
    clear_cast()
    setup_cast(m)
    res = load_h(m, stage, height=height, bust=bust)
    res["reused"] = False
    return res


def play(name, fps=None):
    """播一条工作室动作（演员自动进场）。load_studio_fresh 的短名字。"""
    return load_studio_fresh(name, fps)


def play_h(mode, stage=None):
    """播一段 H 动作（人、阶段、道具一起上）。"""
    return load_h_fresh(mode, stage)


def set_stage(stage):
    """只换阶段：当前这条 H 动作的所有人和道具一起切。"""
    char = active_character()
    m = _mode_by_key(char.get("hs2_h_mode")) if char is not None else None
    if m is None:
        raise RuntimeError("当前不是 H 动作，没法切阶段")
    apply_stage_all(m, stage)
    return jpcn.label_of(m)


def ensure_actor(gender="female"):
    """保证场景里有个演员（导入外部动作时用）。"""
    char = active_character()
    if char is not None:
        return char
    return character(gender)

# --------------------------------------------------------------------------
# 阶段联动：一段 H 动作的所有角色 + 道具一起换阶段
# --------------------------------------------------------------------------

def sync_state(mode=None):
    """场景里的角色是否凑齐同一段 H 动作、性别对不对。

    返回 (ok, 说明)。面板上的「阶段联动」按钮就靠它决定能不能点。
    """
    chars = characters()
    tagged = {s: c for s, c in chars.items() if c.get("hs2_h_mode")}
    if not tagged:
        return False, "还没有角色装过 H 动作"

    if mode is None:
        active = active_character()
        mk = (active.get("hs2_h_mode") if active is not None else "") or ""
        if not mk:
            counts = {}
            for c in tagged.values():
                counts[c.get("hs2_h_mode")] = counts.get(c.get("hs2_h_mode"), 0) + 1
            mk = max(counts, key=counts.get)
        m = find_mode(mk) or _mode_by_key(mk)
    else:
        m = mode if isinstance(mode, dict) else find_mode(mode)
    if m is None:
        return False, "找不到这段动作的数据"

    need = hdb.cast_of(m.get("roles") or {})
    wrong_mode = [SLOT_CN.get(s, s) for s, c in tagged.items()
                  if c.get("hs2_h_mode") != m["key"]]
    if wrong_mode:
        return False, "这些角色装的是别的动作：%s" % "、".join(wrong_mode)

    missing = [s for s in need if s not in tagged]
    if missing:
        return False, "还差：%s（%s）" % (
            "、".join(SLOT_CN.get(s, s) for s in missing),
            hdb.cast_text(m.get("roles") or {}))

    extra = [s for s in tagged if s not in need]
    if extra:
        return False, "多出来的角色：%s（这段只要 %s）" % (
            "、".join(SLOT_CN.get(s, s) for s in extra),
            hdb.cast_text(m.get("roles") or {}))

    for slot in need:
        want = hdb.slot_gender(slot)
        got = gender_of(tagged[slot])
        if got != want:
            return False, "%s 的性别不对：需要%s，现在是%s" % (
                SLOT_CN.get(slot, slot), "女" if want == "female" else "男",
                "女" if got == "female" else "男")

    return True, "%s（%s）已就位，可以联动" % (jpcn.label_of(m),
                                              hdb.cast_text(m.get("roles") or {}))


def _mode_by_key(key):
    for m in (h_data().get("modes") or []):
        if m.get("key") == key:
            return m
    return None


def sync_stage(stage=None, mode=None, force=False, height=None, bust=None):
    """把同一段 H 动作的所有角色（和它们的道具）切到同一个阶段/体型。"""
    ok, msg = sync_state(mode)
    if not ok and not force:
        raise RuntimeError(msg)
    m = mode if isinstance(mode, dict) else (find_mode(mode) if mode else None)
    if m is None:
        active = active_character()
        m = _mode_by_key(active.get("hs2_h_mode")) if active is not None else None
    if m is None:
        raise RuntimeError("找不到这段动作")
    chars = {s: c for s, c in characters().items()
             if c.get("hs2_h_mode") == m["key"]}
    done = []
    for slot in hdb.cast_of(m.get("roles") or {}):
        char = chars.get(slot)
        if char is None:
            continue
        res = apply_h(m, stage, target=char, slot=slot, with_props=False,
                      height=height, bust=bust)
        done.append("%s→%s" % (SLOT_CN.get(slot, slot),
                               res["stage"]["cn"]))
    props_done = _apply_prop_stage(m)
    return "%s ｜ %s%s" % (jpcn.label_of(m), "、".join(done),
                           ("，道具 %d" % props_done) if props_done else "")


# --------------------------------------------------------------------------
# 动作编辑（镜像 / 变速 / 贴地 / 根运动）
# --------------------------------------------------------------------------

_RECENT = []


def _remember(name):
    """记住最近装过的动作，混合时的 B 默认值就靠它。"""
    if not name:
        return
    if name in _RECENT:
        _RECENT.remove(name)
    _RECENT.append(name)
    del _RECENT[:-8]


def current_action():
    a = active_character() or arm()
    if a is None or a.animation_data is None:
        return None
    return a.animation_data.action


def action_of(name, near=None):
    """按名字找 Action；找不到就在目录里现装。"""
    if not (name or "").strip():
        act = current_action()
        if act is not None:
            return act
        raise RuntimeError("还没有动作，先在「② 动作」里选一个")
    act = bpy.data.actions.get(name)
    if act is not None:
        return act
    if near is not None:
        m = find_mode(near)
        if m is not None:
            res = load_h(m, None)
            key = list(res["actions"])[0]
            return res["actions"][key]
    return load(name)[0]


def use(act):
    """把某个 Action 挂到当前角色上并返回它。"""
    a = active_character() or arm()
    animlib._assign(a, act)
    _remember(act.name)
    return act


def mirror(act=None, name=None):
    a = arm()
    act = act or current_action()
    out = animlib.mirror(a, act, name=name)
    use(out)
    return out


def retime(act=None, factor=1.0, name=None):
    a = arm()
    act = act or current_action()
    out = animlib.retime(a, act, factor=factor, name=name)
    use(out)
    return out


def ground(act=None, target=0.0):
    a = arm()
    act = act or current_action()
    out = animlib.ground(a, act, target=target)
    use(out)
    return out


def root_motion(act=None, horizontal=True, turn=True, vertical=False,
                root="p_cf_anim", hips="cf_J_Hips", name=None):
    """把 Hips 的位移/转身/跳跃提取到根骨，画面不变。"""
    a = arm()
    act = act or current_action()
    out = animlib.root_motion(a, act, horizontal=horizontal, turn=turn,
                              vertical=vertical, root=root, hips=hips,
                              name=name)
    use(out)
    return out


def keyframe_rig():
    """把 IK 控制器重新钉到当前动作上（改完混合后一定要来一下）。"""
    a = active_character() or arm()
    if a is None or not rig.has_rig(a):
        return 0
    f0 = bpy.context.scene.frame_start
    f1 = bpy.context.scene.frame_end
    return rig.key_targets(a, f0, f1)


def export_fbx(path, include_mesh=False):
    a = active_character() or arm()
    if a is None:
        raise RuntimeError("没有骨架")
    ops._export(a, path, include_mesh=include_mesh, deform_only=True)
    return path


# --------------------------------------------------------------------------
# 文本命令（给 Agent 用：一句话一条）
# --------------------------------------------------------------------------

_HELP = """\
character [female|male] [槽]  加载角色（槽：f1/f2/m1/m2，不写自动分配）
chars                     看场景里有哪些角色、装的什么动作
scan                      重新扫描动作库并缓存
list [关键词] [--h]       列出动作（默认 Studio）
cats                      列出分类统计
hlist [关键词]            列出 H 模式
stages <模式>             列出某段 H 动作的阶段
load <名字>               装 Studio 动作（演员自动进场）
h <模式> [阶段]           装 H 动作（演员和道具自动进场）
h1 <模式> [阶段]          只装到当前选中的角色
cast <模式>               按人数/性别把缺的角色补上（3P 补 3 个）
sync [阶段]               阶段联动（所有角色 + 道具一起换）
prop <prefab> [骨名] [pack]  装道具
mirror                    左右镜像
speed <倍数>              变速
ground                    放回地面
rig                       给当前动作重新打 IK 控制器关键帧
info                      当前状态
export <路径>             导出角色 FBX
exportobj [目录]          选中的物体各导一个 FBX（给 UE）
"""


def cmd(text):
    """执行一条（或一段）文本命令，返回可读的结果字符串。"""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out = []
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        out.append(_one(ln))
    return "\n".join(out)


def _one(line):
    parts = line.split()
    op = parts[0].lower()
    args = parts[1:]

    def opt(flag):
        return flag in args

    args = [a for a in args if not a.startswith("--")]

    try:
        if op in ("help", "?"):
            return _HELP
        if op == "character":
            gender = args[0] if args and args[0] in ("male", "female") else "female"
            slot = args[1] if len(args) > 1 and args[1] in hdb.SLOTS else None
            a = character(gender=gender, slot=slot)
            return "已加载%s角色：%s（%s，%d 骨）" % (
                "男" if gender == "male" else "女", a.name,
                SLOT_CN.get(a.get("hs2_slot"), "?"), len(a.data.bones))
        if op in ("chars", "role"):
            return scene_text()
        if op == "cast":
            m = find_mode(args[0]) if args else None
            if not m:
                return "没找到这段 H 动作"
            r = setup_cast(m)
            return "阵容：%s（%s）%s" % (
                hdb.cast_text(m.get("roles") or {}),
                "、".join("%s=%s" % (SLOT_CN.get(s, s), c.name)
                         for s, c in r["chars"].items()),
                "，新加 %d 个" % len(r["added"]) if r["added"] else "")
        if op == "scan":
            t0 = time.time()
            cat = catalog(rebuild=True)
            return "扫描完成：Studio %d 个动作，H %d 段（%.0fs）" % (
                len(cat.get("studio") or {}),
                len((cat.get("h") or {}).get("modes") or []), time.time() - t0)
        if op == "list":
            kw = args[0] if args else ""
            res = search(kw, kind="h" if opt("--h") else "studio")
            return "\n".join("%-28s %s" % (e["name"], e["group_cn"] or
                                          e["category"]) for e in res[:40]) or "没有匹配"
        if op in ("cats", "categories"):
            c = categories()
            return "\n".join("%-10s %s" % (k, ", ".join(
                "%s(%d)" % (g, len(v)) for g, v in sorted(sub.items())[:8]))
                for k, sub in sorted(c.items()))
        if op in ("hlist", "h"):
            if op == "h" and args:
                mode = args[0]
                stage = args[1] if len(args) > 1 else None
                res = load_h_fresh(mode, stage)
                info = res.get("info") or {}
                msg = "H 动作：%s · %s（%d 人）" % (
                    jpcn.label_of(res["mode"]), res.get("stage", {}).get("cn", "?"),
                    len(characters())) if info or res.get("reused") else "没装上"
                if res["props"]:
                    msg += "，道具 %d 个" % len(res["props"])
                return msg
            kw = args[0] if args else ""
            res = search(kw, kind="h")
            return "\n".join("%-14s %-6s 道具=%s" % (
                e["name"], e["type_cn"] or e["type_jp"], ",".join(p for p in e["props"] if p) or "-")
                for e in res[:40]) or "没有匹配"
        if op in ("stages", "stage"):
            m = find_mode(args[0] if args else "")
            if not m:
                return "没找到这段 H 动作"
            st = stages(m)
            return "%s 有 %d 个阶段：\n%s" % (
                jpcn.label_of(m), len(st), "\n".join(
                    "  %-14s %s" % (s["name"], s["cn"]) for s in st))
        if op in ("h1", "hsel"):
            m = find_mode(args[0]) if args else None
            if not m:
                return "没找到这段 H 动作"
            char = active_character()
            if char is None:
                return "先加载角色"
            res = apply_h(m, args[1] if len(args) > 1 else None, target=char)
            return "已装到 %s：%s · %s（%d 帧）%s" % (
                SLOT_CN.get(res["slot"], res["slot"]), jpcn.label_of(m),
                res["stage"]["cn"], res["info"]["frames"],
                "，道具 %d" % len(res["props"]) if res["props"] else "")
        if op == "sync":
            try:
                return sync_stage(args[0] if args else None)
            except Exception as e:
                return "联动没成立：%s" % e
        if op in ("exportobj", "objexport"):
            outdir = args[0] if args else None
            objs = [o for o in bpy.context.selected_objects
                    if o.type in ("ARMATURE", "MESH")]
            if not objs:
                return "先在 3D 视图里选中要导出的物体"
            made = ops.export_objects(objs, outdir=outdir)
            return "导出 %d 个：\n%s" % (len(made), "\n".join(
                "  %s → %s" % (n, p) for n, p in made))
        if op == "load":
            res = load_studio_fresh(args[0])
            act, info = res["action"], res["info"]
            return "已装 %s：%d 帧（%s演员）" % (
                args[0], info["frames"],
                "男" if res["gender"] == "male" else "女")
        if op == "prop":
            prefab = args[0]
            bone = args[1] if len(args) > 1 else None
            pack = args[2] if len(args) > 2 else "01"
            ob = load_prop(prefab, pack=pack, bone=bone)
            return "道具 %s：%d 骨 %d 网格%s" % (
                prefab, len(ob.data.bones), ob.get("hs2_prop_meshes", 0),
                ("，挂到 %s" % bone) if bone else "")
        if op == "mirror":
            return "镜像完成 → %s" % mirror().name
        if op == "speed":
            return "变速完成 → %s" % retime(
                factor=1.0 / max(0.01, float(args[0])) if args else 1.0).name
        if op == "ground":
            return "贴地完成 → %s" % ground().name
        if op == "rig":
            n = keyframe_rig()
            return "已重新打控制器关键帧：%d 帧" % n
        if op == "info":
            a = arm()
            if a is None:
                return "没有角色"
            act = current_action()
            return "角色 %s · %d 骨 · 动作 %s · 帧 %d-%d" % (
                a.name, len(a.data.bones), act.name if act else "无",
                bpy.context.scene.frame_start, bpy.context.scene.frame_end)
        if op == "export":
            return "已导出 " + export_fbx(args[0] if args else "hs2_out.fbx")
    except Exception as e:
        return "命令 %r 失败：%s" % (line, e)
    return "不认识的命令 %r，输入 help 看用法" % op
