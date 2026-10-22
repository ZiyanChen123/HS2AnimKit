"""HS2 clothing catalog and prefab extraction.

The characustom TextAsset is not CSV.  It is a small binary structure with
length-prefixed strings; ``parse_cha_list`` reads the column list and rows
without depending on Blender.
"""

import os
import re

from . import mesh as _mesh
from . import unityfs

# TextAsset name prefix -> (gender, slot, display label)
CATEGORIES = {
    "fo_top": ("female", "top", "上衣"),
    "fo_bot": ("female", "bottom", "下衣"),
    "fo_inner_t": ("female", "inner_top", "内衣上"),
    "fo_inner_b": ("female", "inner_bottom", "内衣下"),
    "fo_gloves": ("female", "gloves", "手套"),
    "fo_socks": ("female", "socks", "袜子"),
    "fo_shoes": ("female", "shoes", "鞋子"),
    "fo_panst": ("female", "panst", "连裤袜"),
    "mo_top": ("male", "top", "上衣"),
    "mo_bot": ("male", "bottom", "下衣"),
    "mo_gloves": ("male", "gloves", "手套"),
    "mo_shoes": ("male", "shoes", "鞋子"),
}

SLOT_ORDER = ("top", "bottom", "inner_top", "inner_bottom",
              "gloves", "socks", "shoes", "panst")

SLOT_LABELS = {
    "top": "上衣",
    "bottom": "下衣",
    "inner_top": "内衣上",
    "inner_bottom": "内衣下",
    "gloves": "手套",
    "socks": "袜子",
    "shoes": "鞋子",
    "panst": "连裤袜",
}

_VARIANT_RE = re.compile(r"^n_(?:top|bot|inner|gloves|socks|shoes|panst)_([a-zA-Z])$")


def _textasset_bytes(tt):
    val = tt.get("m_Script", b"")
    if isinstance(val, str):
        return val.encode("utf-8", "surrogateescape")
    return bytes(val)


def _read_string(data, pos):
    """Read one length-prefixed UTF-8 string (MessagePack str family).

    The characustom TextAsset is a small MessagePack-ish stream: column lists
    are arrays of strings and ``dictList`` is a map row-index -> array of
    strings.  Short values use fixstr (0xA0 | len); mod lists use str8/16/32.
    """
    b0 = data[pos]
    pos += 1
    if 0xA0 <= b0 <= 0xBF:
        n = b0 - 0xA0
    elif b0 == 0xD9:
        n = data[pos]
        pos += 1
    elif b0 == 0xDA:
        n = (data[pos] << 8) | data[pos + 1]
        pos += 2
    elif b0 == 0xDB:
        n = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4
    else:
        raise ValueError("bad string prefix 0x%02x at %d" % (b0, pos - 1))
    return data[pos:pos + n].decode("utf-8", "replace"), pos + n


def _read_array_header(data, pos):
    b = data[pos]
    pos += 1
    if 0x90 <= b <= 0x9F:
        return b - 0x90, pos
    if b == 0xDC:
        return (data[pos] << 8) | data[pos + 1], pos + 2
    if b == 0xDD:
        return int.from_bytes(data[pos:pos + 4], "big"), pos + 4
    raise ValueError("bad array prefix 0x%02x" % b)


def _read_map_header(data, pos):
    b = data[pos]
    pos += 1
    if 0x80 <= b <= 0x8F:
        return b - 0x80, pos
    if b == 0xDE:
        return (data[pos] << 8) | data[pos + 1], pos + 2
    if b == 0xDF:
        return int.from_bytes(data[pos:pos + 4], "big"), pos + 4
    raise ValueError("bad map prefix 0x%02x" % b)


def _read_int(data, pos):
    b = data[pos]
    pos += 1
    if b <= 0x7F:
        return b, pos
    if b == 0xCC:
        return data[pos], pos + 1
    if b == 0xCD:
        return (data[pos] << 8) | data[pos + 1], pos + 2
    if b >= 0xE0:                       # negative fixint
        return b - 256, pos
    raise ValueError("bad int prefix 0x%02x" % b)


def parse_cha_list(data):
    """Return (columns, rows) from a characustom TextAsset."""
    i = data.find(b"lstKey")
    if i < 0:
        raise ValueError("lstKey not found")
    pos = i + len(b"lstKey")
    ncols, pos = _read_array_header(data, pos)
    columns = []
    for _ in range(ncols):
        s, pos = _read_string(data, pos)
        columns.append(s)

    i = data.find(b"dictList", pos)
    if i < 0:
        return columns, []
    pos = i + len(b"dictList")
    nrows, pos = _read_map_header(data, pos)

    rows = []
    for _ in range(nrows):
        _key, pos = _read_int(data, pos)
        n, pos = _read_array_header(data, pos)
        vals = []
        for _ in range(n):
            s, pos = _read_string(data, pos)
            vals.append(s)
        rows.append(dict(zip(columns, vals)))
    return columns, rows


def _category_of(textasset_name):
    """fo_top_00 -> fo_top, fo_inner_t_00 -> fo_inner_t, ..."""
    for prefix in sorted(CATEGORIES, key=len, reverse=True):
        if textasset_name == prefix or textasset_name.startswith(prefix + "_"):
            return prefix
    return None


def _display_name(row):
    # Several HS2 lists contain literal "0" placeholders in the localized
    # columns; the Japanese Name column is the reliable one.
    for key in ("Name", "ZH_CN", "EN_US"):
        val = (row.get(key) or "").strip()
        if not val or val.isdigit():
            continue
        if val in ("無", "无", "なし", "None"):
            continue
        return val
    return (row.get("Name") or "").strip() or "?"


def _bundle_path(game_dir, main_ab):
    main_ab = (main_ab or "").replace("\\", "/")
    if not main_ab:
        return ""
    if main_ab.startswith("abdata/"):
        return os.path.join(game_dir, *main_ab.split("/"))
    return os.path.join(game_dir, "abdata", *main_ab.split("/"))


def load_catalog(game_dir, progress=None):
    """Scan all characustom list bundles and return clothing records."""
    root = os.path.join(game_dir, "abdata", "list", "characustom")
    if not os.path.isdir(root):
        raise RuntimeError("找不到 characustom 目录：%s" % root)
    files = [os.path.join(root, f) for f in os.listdir(root)
             if f.lower().endswith(".unity3d")]
    files.sort()
    out = []
    seen = set()
    for bi, path in enumerate(files):
        if progress:
            progress(bi, len(files), os.path.basename(path))
        b = unityfs.Bundle(path)
        try:
            for pid, obj in b.objects.items():
                if unityfs._type_id(obj) != 49:
                    continue
                tt = b.typetree(pid)
                name = tt.get("m_Name", "")
                prefix = _category_of(name)
                if prefix is None:
                    continue
                gender, slot, _label = CATEGORIES[prefix]
                try:
                    _cols, rows = parse_cha_list(_textasset_bytes(tt))
                except Exception as e:
                    print("[HS2C] list parse failed %s: %s" % (name, e))
                    continue
                for row in rows:
                    prefab = (row.get("MainData") or "").strip()
                    main_ab = (row.get("MainAB") or "").strip()
                    if not prefab or prefab == "p_dummy" or not main_ab:
                        continue
                    key = (main_ab, prefab, row.get("ID", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    rec = {
                        "id": (row.get("ID") or "").strip(),
                        "kind": (row.get("Kind") or "").strip(),
                        "gender": gender,
                        "slot": slot,
                        "prefix": prefix,
                        "name": _display_name(row),
                        "name_jp": (row.get("Name") or "").strip(),
                        "bundle": _bundle_path(game_dir, main_ab),
                        "main_ab": main_ab,
                        "prefab": prefab,
                        "main_tex": (row.get("MainTex") or "").strip(),
                        "mask_tex": (row.get("ColorMaskTex") or "").strip(),
                        "state_type": (row.get("StateType") or "").strip(),
                        "thumbnail": (row.get("ThumbTex") or "").strip(),
                    }
                    out.append(rec)
        finally:
            try:
                b.env = None
            except Exception:
                pass

    def sort_key(r):
        try:
            ident = int(r["id"])
        except Exception:
            ident = 10 ** 9
        return (r["gender"], r["slot"], ident, r["name"])

    out.sort(key=sort_key)
    return out


# ---------------------------------------------------------------------------
# Prefab extraction
# ---------------------------------------------------------------------------

def _find_child(b, tid, name):
    for c in b.nodes[tid].children:
        if b.nodes[c].name == name:
            return c
    return None


def _is_descendant(b, tid, root):
    cur = tid
    while cur:
        if cur == root:
            return True
        cur = b.nodes[cur].parent
    return False


def _variant_branch(b, nroot, state):
    """Find n_top_a/n_top_b style state branch under n_o_root."""
    want = (state or "A").upper()
    found = []
    for tid in b.subtree(nroot):
        name = b.nodes[tid].name
        m = _VARIANT_RE.match(name)
        if m:
            found.append((tid, m.group(1).upper(), name))
    if not found:
        return nroot, ""
    for tid, letter, name in found:
        if letter == want:
            return tid, name
    # If only one state exists, use it even if the requested label differs.
    letters = {x[1] for x in found}
    if len(letters) == 1:
        return found[0][0], found[0][2]
    return nroot, ""


def _pair(item):
    if isinstance(item, dict):
        return item.get("first"), item.get("second")
    try:
        return item[0], item[1]
    except Exception:
        return None, None


def _keyname(k):
    if isinstance(k, str):
        return k
    if isinstance(k, dict):
        return k.get("name") or k.get("m_Name") or ""
    return ""


def _material_info(b, pid):
    tt = b.typetree(pid)
    props = tt.get("m_SavedProperties") or {}
    colors = {}
    floats = {}
    texenvs = {}
    for item in props.get("m_Colors") or []:
        k, v = _pair(item)
        name = _keyname(k)
        if name and isinstance(v, dict):
            colors[name] = (float(v.get("r", 1.0)), float(v.get("g", 1.0)),
                            float(v.get("b", 1.0)), float(v.get("a", 1.0)))
    for item in props.get("m_Floats") or []:
        k, v = _pair(item)
        name = _keyname(k)
        if name:
            try:
                floats[name] = float(v)
            except Exception:
                pass
    for item in props.get("m_TexEnvs") or []:
        k, v = _pair(item)
        name = _keyname(k)
        v = v or {}
        ptr = (v.get("m_Texture") or {}).get("m_PathID")
        if not ptr or ptr not in b.objects:
            texenvs[name] = None
            continue
        t = b.typetree(ptr)
        texenvs[name] = {
            "pid": ptr,
            "name": t.get("m_Name", ""),
            "width": t.get("m_Width", 0),
            "height": t.get("m_Height", 0),
            "format": t.get("m_TextureFormat", 0),
            "scale": v.get("m_Scale"),
            "offset": v.get("m_Offset"),
        }
    return {
        "pid": pid,
        "name": tt.get("m_Name", ""),
        "colors": colors,
        "floats": floats,
        "texenvs": texenvs,
    }


def state_plan(game_dir, record, bundle=None):
    """Return ``(base_state, [variant letters...])``.

    ``base_state`` is ``""`` when the prefab has a real base mesh outside the
    a/b branches (default/no-suffix state).  If it only has accessory meshes
    outside the branches, the first real branch (usually A) is the default and
    the remaining branches are half/full variants.
    """
    b = bundle
    own = b is None
    if own:
        bundle_path = record.get("bundle") or ""
        if not os.path.isfile(bundle_path):
            raise RuntimeError("找不到服装包：%s" % bundle_path)
        b = unityfs.Bundle(bundle_path)
    try:
        try:
            root = b.find_root(record["prefab"])
        except KeyError:
            return "", []
        nroot = _find_child(b, root, "n_o_root") or root
        variant_nodes = [tid for tid in b.subtree(nroot)
                         if _VARIANT_RE.match(b.nodes[tid].name)]
        smr_nodes = []
        for pid in b.smrs():
            tt = b.typetree(pid)
            nt = b.go2node.get(tt.get("m_GameObject", {}).get("m_PathID"))
            if nt is None or not _is_descendant(b, nt, nroot):
                continue
            try:
                mesh_name = b.typetree(tt["m_Mesh"]["m_PathID"]).get(
                    "m_Name", "")
            except Exception:
                mesh_name = ""
            smr_nodes.append((nt, mesh_name))
        letters = set()
        for tid in variant_nodes:
            m = _VARIANT_RE.match(b.nodes[tid].name)
            if m and any(_is_descendant(b, nt, tid) for nt, _ in smr_nodes):
                letters.add(m.group(1).upper())
        letters = sorted(letters)
        common = [name for nt, name in smr_nodes
                  if not any(_is_descendant(b, nt, v) for v in variant_nodes)]
        if common and _has_base_names(common):
            return "", letters
        if letters:
            return letters[0], letters[1:]
        return "", []
    finally:
        if own:
            try:
                b.env = None
            except Exception:
                pass


def available_states(game_dir, record, bundle=None):
    """Which state branches actually contain a mesh (letters only)."""
    base, variants = state_plan(game_dir, record, bundle=bundle)
    letters = ([base] if base else []) + list(variants)
    return sorted(set(letters))


def _has_base_names(names):
    """分支外是否有「本体」网格（而不是只有蝴蝶结/腰带/兜帽这类配件）。"""
    hints = ("_op", "op1", "op2", "ribbon", "ribon", "obi", "himo", "huta",
             "belt", "beruto", "hood", "boa", "strap", "veil", "frill")
    for name in names or []:
        low = (name or "").lower()
        if not any(h in low for h in hints):
            return True
    return False


def _has_base_mesh(smrs):
    return _has_base_names([s.get("name") for s in smrs or []])


def _build_tex_index(b):
    idx = {}
    for pid, obj in b.objects.items():
        if unityfs._type_id(obj) != 28:      # Texture2D
            continue
        try:
            tt = b.typetree(pid)
        except Exception:
            continue
        name = tt.get("m_Name", "")
        if name and name not in idx:
            idx[name] = (pid, tt)
    return idx


def _tex_info(pid, tt, scale=None, offset=None):
    return {
        "pid": pid,
        "name": tt.get("m_Name", ""),
        "width": tt.get("m_Width", 0),
        "height": tt.get("m_Height", 0),
        "format": tt.get("m_TextureFormat", 0),
        "scale": scale or {"x": 1.0, "y": 1.0},
        "offset": offset or {"x": 0.0, "y": 0.0},
    }


def _material_base(name):
    n = name or ""
    if n.startswith("cf_m_"):
        return "cf_" + n[5:]
    if n.startswith("mo_m_"):
        return "mo_" + n[5:]
    if n.startswith("m_cf_"):
        return "cf_" + n[5:]
    if n.startswith("m_mo_"):
        return "mo_" + n[5:]
    if n.startswith("m_"):
        return n[2:]
    if n.endswith("_m"):
        return n[:-2]
    return n


def _apply_record_textures(b, record, mats):
    """材质里的 _MainTex 经常是 0；真正的图在衣服列表的 MainTex 列里。

    用列表的 MainTex 名称在同一个包里找 Texture2D，再按 _t/_n/_s/_o
    推断法线/金属/遮挡图。这样不会把本来有图的一大批衣服误判成缺图。
    """
    main = (record.get("main_tex") or "").strip()
    mask = (record.get("mask_tex") or "").strip()
    if main in ("0", "無", "无", "なし"):
        main = ""
    if mask in ("0", "無", "无", "なし"):
        mask = ""
    idx = None

    def find(name):
        nonlocal idx
        if not name:
            return None
        if idx is None:
            idx = _build_tex_index(b)
        hit = idx.get(name)
        return _tex_info(hit[0], hit[1]) if hit else None

    base = main[:-2] if main.endswith("_t") else ""
    for m in mats.values():
        envs = m.setdefault("texenvs", {})
        if not envs.get("_MainTex") and main:
            envs["_MainTex"] = find(main)
        if not envs.get("_MainTex"):
            mb = _material_base(m.get("name"))
            hit = find(mb + "_t")
            if hit:
                envs["_MainTex"] = hit
                if not base:
                    base = mb
        main_info = envs.get("_MainTex") or {}
        if not base and main_info.get("name", "").endswith("_t"):
            base = main_info["name"][:-2]
        if not base:
            base = _material_base(m.get("name"))
        for slot, suf in (("_BumpMap", "_n"),
                          ("_MetallicGlossMap", "_s"),
                          ("_OcclusionMap", "_o"),
                          ("_DetailMask", "_dm"),
                          ("_AlphaMask", "_ml")):
            if not envs.get(slot) and base:
                hit = find(base + suf)
                if hit:
                    envs[slot] = hit
        if not envs.get("_ColorMask") and mask:
            envs["_ColorMask"] = find(mask)
    return mats


def extract_garment(game_dir, record, state="A", bundle=None):
    """Extract one clothing prefab into plain Python data.

    The returned SMR meshes contain vertex data, UVs, weights, bone paths and
    material metadata.  No Blender objects are created here.
    ``bundle``可以传入已打开的 Bundle，批量导出时避免反复解包。
    """
    b = bundle
    own = b is None
    if own:
        bundle_path = record.get("bundle") or ""
        if not os.path.isfile(bundle_path):
            raise RuntimeError("找不到服装包：%s" % bundle_path)
        b = unityfs.Bundle(bundle_path)
    try:
        try:
            root = b.find_root(record["prefab"])
        except KeyError:
            raise RuntimeError("包里找不到 prefab：%s" % record["prefab"])
        nroot = _find_child(b, root, "n_o_root")
        if nroot is None:
            nroot = root
        # n_top_a / n_bot_b ... 等状态分支。分支外的网格是「所有状态共用」的
        # 零件（腰带、蝴蝶结、兜帽……），每个状态都要带上，不能只取分支内部。
        variant_nodes = [tid for tid in b.subtree(nroot)
                         if _VARIANT_RE.match(b.nodes[tid].name)]
        want = (state or "").strip().upper()
        branch, variant = _variant_branch(b, nroot, want)

        def collect(branch_id):
            found, mat_map = [], {}
            for pid in b.smrs():
                tt = b.typetree(pid)
                go = tt.get("m_GameObject", {}).get("m_PathID")
                nt = b.go2node.get(go)
                if nt is None or not _is_descendant(b, nt, nroot):
                    continue
                in_variant = any(_is_descendant(b, nt, v) for v in variant_nodes)
                if in_variant and (branch_id is None
                                   or not _is_descendant(b, nt, branch_id)):
                    continue
                mid = tt["m_Mesh"]["m_PathID"]
                mesh_name = b.typetree(mid).get("m_Name", "")
                bone_paths = []
                for ptr in tt.get("m_Bones") or []:
                    tid = ptr.get("m_PathID")
                    bone_paths.append(b.path_of(tid) if tid in b.nodes else "")
                data = _mesh.extract_by_pid(b, mid, mesh_name, bone_paths)
                mat_pids = []
                for mp in tt.get("m_Materials") or []:
                    mpid = mp.get("m_PathID")
                    if mpid in b.objects:
                        mat_pids.append(mpid)
                        if mpid not in mat_map:
                            mat_map[mpid] = _material_info(b, mpid)
                found.append({
                    "path": b.path_of(nt),
                    "name": mesh_name,
                    "mesh": data,
                    "materials": mat_pids,
                    "bindpose": data.get("bindpose") or [],
                    "submeshes": data.get("submeshes") or [],
                })
            return found, mat_map

        def first_branch():
            letters = sorted({_VARIANT_RE.match(b.nodes[tid].name).group(1).upper()
                              for tid in variant_nodes})
            for letter in letters:
                br, var = _variant_branch(b, nroot, letter)
                ss, mm = collect(br)
                if ss:
                    return ss, mm, var
            return [], {}

        if not want:
            # 默认/不脱：分支外的本体网格；只有配件时退回第一个有网格的状态
            common_smrs, common_mats = collect(None)
            if common_smrs and _has_base_mesh(common_smrs):
                smrs, mats = common_smrs, common_mats
            elif variant_nodes:
                smrs, mats, variant = first_branch()
                if not smrs:
                    smrs, mats = common_smrs, common_mats
            else:
                smrs, mats = common_smrs, common_mats
        else:
            selected = branch if variant else (
                nroot if not variant_nodes else None)
            smrs, mats = collect(selected)
            # Some switched items have an empty B branch.  Falling back avoids a
            # broken export while keeping A as the only visible variant.
            if not smrs and want != "A":
                alt_branch, alt_variant = _variant_branch(b, nroot, "A")
                if alt_branch != selected:
                    smrs, mats = collect(alt_branch)
                    variant = alt_variant
        if not smrs:
            raise RuntimeError("prefab 下没有找到服装 SMR：%s" % record["prefab"])
        _apply_record_textures(b, record, mats)
        return {
            "record": dict(record),
            "state": want,
            "variant": variant,
            "smrs": smrs,
            "materials": list(mats.values()),
        }
    finally:
        if own:
            try:
                b.env = None
            except Exception:
                pass
