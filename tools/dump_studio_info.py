# -*- coding: utf-8 -*-
"""导出 abdata/studio/info/*.unity3d 的表结构（找官方分组）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from hs2_rig_kit.ripper import unityfs

G = os.environ.get("HS2_DIR", r"D:\game\HS2")
base = os.path.join(G, "abdata", "studio", "info")


def dump(path, max_obj=6):
    print("=" * 78)
    print("FILE", os.path.basename(path))
    try:
        b = unityfs.Bundle(path)
    except Exception as e:
        print("  open fail:", e)
        return
    n = 0
    for pid in list(b.objects):
        o = b.objects[pid]
        tid = unityfs._type_id(o)
        try:
            tt = o.read_typetree()
        except Exception:
            continue
        nm = tt.get("m_Name") or ""
        keys = list(tt.keys())
        print("  -- type=%d name=%r keys=%s" % (tid, nm, keys[:14]))
        # MonoBehaviour 里的 list[list[str]]
        raw = tt.get("list")
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            head = (raw[0] or {}).get("list") or []
            body = raw[1:]
            print("     TABLE cols=%d rows=%d" % (len(head), len(body)))
            for i, h in enumerate(head):
                print("       %2d %s" % (i, h))
            for r in body[:3]:
                print("       ROW:", " | ".join("%d:%s" % (i, v) for i, v in enumerate(r) if str(v).strip()))
        n += 1
        if n >= max_obj:
            break
    b.env = None


if __name__ == "__main__":
    files = sorted(os.listdir(base))
    for f in files:
        if f.lower().endswith(".unity3d"):
            dump(os.path.join(base, f))
