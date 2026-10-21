# -*- coding: utf-8 -*-
"""dump_h_tables.py —— 把 H 数据表原样导出成 json，用来校列。

    python tools/dump_h_tables.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hs2_rig_kit.ripper import hdb, unityfs  # noqa: E402

GAME = os.environ.get("HS2_DIR", r"D:\game\HS2")


def iter_tables(sub):
    d = os.path.join(GAME, "abdata", "list", "h", sub)
    if not os.path.isdir(d):
        return
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".unity3d"):
            continue
        p = os.path.join(d, f)
        try:
            b = unityfs.Bundle(p)
        except Exception as e:
            print("skip", f, e)
            continue
        for pid in list(b.objects):
            o = b.objects[pid]
            if unityfs._type_id(o) != 114:
                continue
            try:
                tt = o.read_typetree()
            except Exception:
                continue
            nm = tt.get("m_Name") or ""
            head, body = hdb._rows(tt)
            if len(head) < 10:
                continue
            yield sub, f, nm, head, body


def main():
    out = {"game_dir": GAME, "tables": []}
    for sub in ("animationinfo", "hitemobj"):
        for s, f, nm, head, body in iter_tables(sub):
            out["tables"].append({
                "sub": sub, "file": f, "name": nm,
                "head": list(head),
                "rows": [list(r) for r in body],
            })
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_h_tables.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("tables:", len(out["tables"]), "->", path)
    # 汇总表头长度分布
    import collections
    c = collections.Counter(len(t["head"]) for t in out["tables"])
    print("head lens:", dict(c))
    return out


if __name__ == "__main__":
    main()
