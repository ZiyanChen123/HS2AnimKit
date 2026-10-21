# -*- coding: utf-8 -*-
"""prop_gender_check.py —— 道具归谁拿？这一层以前是反的（0=男 1=女）。

装几段有代表性的 H 动作，检查每件道具挂到了哪个性别的角色身上。

    blender -b --factory-startup -P tools/prop_gender_check.py
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print("%s %-46s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def gender_of_obj(ob):
    """顺着父级链找到真正的角色（道具挂着道具时会套好几层）。"""
    seen = 0
    while ob is not None and seen < 8:
        if ob.get("hs2_gender"):
            return ob.get("hs2_gender")
        ob = ob.parent
        seen += 1
    return ""


def run_mode(label, expect):
    """expect: {道具名片段: 期望性别}"""
    h = api.h_data()
    mode = None
    for m in h["modes"]:
        if m["label"] == label:
            mode = m
            break
    if mode is None:
        check("找到 %s" % label, False, "目录里没有")
        return
    fem = api.character("female")
    male = api.character("male")
    res = api.load_h(mode, None)
    check("%s：装上" % label, bool(res["actions"]),
          "角色 %s / 道具 %d" % (",".join(sorted(res["actions"])),
                                len(res["props"])))
    bad = []
    for ob in res["props"]:
        want = None
        for frag, g in expect.items():
            if frag in (ob.get("hs2_prop_note") or ""):
                want = g
                break
        if want is None:
            # expect 里没列的道具也要能挂上，并且性别和表里一致
            want = ob.get("hs2_prop_who")
        got = gender_of_obj(ob.parent)
        bone = ob.get("hs2_attached_to") or ""
        if ob.parent is None or not bone:
            bad.append("%s 没挂上" % ob.name)
            continue
        if ob.parent.pose.bones.get(bone) is None:
            bad.append("%s 骨 %s 不在父级上" % (ob.name, bone))
            continue
        if want in ("female", "male") and got != want:
            bad.append("%s 想给%s，实际挂在%s(%s)"
                       % (ob.name, want, ob.parent.name, got))
    check("%s：道具都归对了" % label, not bad, "; ".join(bad[:3]))


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # 男角牵着牵引绳（把手 motite 在男角右手，项圈/链子在女角脖子）
    run_mode("ワンコバック", {
        "p_item_motite": "male",
        "p_item_kubiwa": "female",
        "p_item_chain": "female",
    })
    api.clear_cast()

    # 「男」拘束フェラ：绳子枷锁全在男角身上
    run_mode("男拘束フェラ", {
        "p_item_nawa_h104_armL": "male",
        "p_item_himo_h104_armL": "male",
        "p_item_girotinkousoku": "male",
    })
    api.clear_cast()

    # 拘束二穴バイブ：普通振动棒男角手持，アナルバイブ直接落在女角身上
    run_mode("拘束二穴バイブ", {
        "p_item_vibe": "male",
        "p_item_analvibe": "female",
    })
    api.clear_cast()

    # 吊るし挿入：被吊起来的是女角
    run_mode("吊るし挿入", {
        "p_item_himo_armL2": "female",
        "p_item_himolong_armL": "female",
    })
    api.clear_cast()

    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS
          else "全部通过")


if __name__ == "__main__":
    main()
