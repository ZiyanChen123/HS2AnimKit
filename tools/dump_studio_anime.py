# -*- coding: utf-8 -*-
"""把 abdata/studio/info/ 下的 Anime* 表全量导出成 JSON。

结构（Anime_XX_YY_ZZ）：
    rows[0]     = ["大きい項目名", "中間項目名", ...]   ← 分组标题行
    rows[1]     = ["表示順番", "管理番号", ...]          ← 列名行
    rows[2:]    = 数据行
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from hs2_rig_kit.ripper import unityfs

G = os.environ.get("HS2_DIR", r"D:\game\HS2")


def flat(v):
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict) and "list" in x:
                out.append(flat(x["list"]))
            else:
                out.append(x)
        return out
    return v


def collect(base, prefixes):
    """返回 {表名: {...}}"""
    res = {}
    for f in sorted(os.listdir(base)):
        if not f.lower().endswith(".unity3d"):
            continue
        p = os.path.join(base, f)
        try:
            b = unityfs.Bundle(p)
        except Exception:
            continue
        for pid in list(b.objects):
            if unityfs._type_id(b.objects[pid]) != 114:
                continue
            try:
                tt = b.objects[pid].read_typetree()
            except Exception:
                continue
            nm = tt.get("m_Name") or ""
            # Anime_00_00_07 → "Anime"；AnimeGroup_00 → "AnimeGroup"
            pre = nm.split("_")[0] if "_" in nm else nm
            if nm.startswith("AnimeGroup"):
                pre = "AnimeGroup"
            elif nm.startswith("AnimeCategory"):
                pre = "AnimeCategory"
            elif nm.startswith("Anime_"):
                pre = "Anime"
            if pre not in prefixes:
                continue
            rows = flat(tt.get("list") or [])
            if len(rows) < 3:
                continue
            res[nm] = {
                "file": f,
                "title": rows[0],
                "cols": rows[1],
                "rows": rows[2:],
            }
        b.env = None
    return res


if __name__ == "__main__":
    base = os.path.join(G, "abdata", "studio", "info")
    data = {
        "anime": collect(base, {"Anime"}),
        "animegroup": collect(base, {"AnimeGroup"}),
        "animecategory": collect(base, {"AnimeCategory"}),
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_studio_info.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("anime tables      :", len(data["anime"]))
    print("animegroup tables :", len(data["animegroup"]))
    print("animecategory     :", len(data["animecategory"]))
    tot = sum(len(v["rows"]) for v in data["anime"].values())
    print("total anime rows  :", tot)
    print("->", out)
