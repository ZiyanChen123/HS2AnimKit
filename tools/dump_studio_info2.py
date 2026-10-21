# -*- coding: utf-8 -*-
"""深入导出 abdata/studio/info/*.unity3d 的嵌套 list 结构。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from hs2_rig_kit.ripper import unityfs

G = os.environ.get("HS2_DIR", r"D:\game\HS2")


def flat(v, depth=0):
    """把 Unity 的嵌套 list[list[str]] 拍平成 [[...], [...]]"""
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict) and "list" in x:
                out.append(flat(x["list"], depth + 1))
            else:
                out.append(x)
        return out
    return v


def show(path, want_prefix=None, limit=3):
    b = unityfs.Bundle(path)
    for pid in list(b.objects):
        o = b.objects[pid]
        if unityfs._type_id(o) != 114:
            continue
        try:
            tt = o.read_typetree()
        except Exception:
            continue
        nm = tt.get("m_Name") or ""
        if want_prefix and not nm.startswith(want_prefix):
            continue
        rows = flat(tt.get("list") or [])
        if not rows:
            continue
        head = rows[0]
        body = rows[1:]
        print("### %s  cols=%d rows=%d" % (nm, len(head), len(body)))
        for i, h in enumerate(head):
            print("   %2d | %s" % (i, h))
        for r in body[:limit]:
            print("   ROW:", " | ".join("%d:%s" % (i, v) for i, v in enumerate(r)))
        print()
    b.env = None


if __name__ == "__main__":
    base = os.path.join(G, "abdata", "studio", "info")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for f in sorted(os.listdir(base)):
        if not f.lower().endswith(".unity3d"):
            continue
        p = os.path.join(base, f)
        print("=" * 78)
        print("FILE", f)
        try:
            show(p, only)
        except Exception as e:
            print("  fail", e)
