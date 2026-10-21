"""角色槽 + 阶段联动自检。

    blender -b --factory-startup -P tools/sync_check.py

覆盖：百合（女女）、3P（女+男+男）、单人、性别不符时按钮不解锁、联动换阶段。
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print("%s %-44s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def find_mode(gender):
    for m in (api.h_data().get("modes") or []):
        if m.get("cast_gender") == gender and m.get("roles"):
            return m
    return None


def main():
    # ---------------- 百合 ----------------
    reset()
    f1 = api.character("female")
    f2 = api.character("female")
    check("两个女角", f1.get("hs2_slot") == "f1" and f2.get("hs2_slot") == "f2",
          "%s / %s" % (f1.name, f2.name))

    yuri = find_mode({"female": 2, "male": 0})
    check("找到百合动作", yuri is not None, yuri["label"] if yuri else "")
    if yuri:
        a = api.apply_h(yuri, None, target=f1)
        b = api.apply_h(yuri, None, target=f2)
        check("百合装到两个女角", a["slot"] == "f1" and b["slot"] == "f2",
              "%s / %s · %s" % (a["action"].name, b["action"].name,
                                a["stage"]["cn"]))
        ok, msg = api.sync_state()
        check("联动解锁（女女）", ok, msg)

        # 换阶段（stages 现在是按体型归组后的列表，用 key 识别）
        stages = [s for s in a["stages"] if s["key"] != a["stage"]["name"]]
        if stages:
            msg = api.sync_stage(stages[0]["key"])
            same = (f1.get("hs2_h_stage") == f2.get("hs2_h_stage") ==
                    stages[0]["key"])
            check("联动换阶段", same, msg)

        # 性别不对 → 不许联动
        f2["hs2_gender"] = "male"
        ok2, msg2 = api.sync_state()
        check("性别不符不解锁", not ok2, msg2)
        f2["hs2_gender"] = "female"

        # 少一个人 → 不许联动
        f2["hs2_h_mode"] = ""
        ok3, msg3 = api.sync_state()
        check("少一个角色不解锁", not ok3, msg3)

    # ---------------- 3P ----------------
    reset()
    api.character("female")
    api.character("male")
    m2 = api.character("male")
    check("第二个男角", m2.get("hs2_slot") == "m2",
          "%s · %s" % (m2.name, m2.get("hs2_slot")))
    three = find_mode({"female": 1, "male": 2})
    check("找到 3P 动作", three is not None,
          three["label"] if three else "")
    if three:
        res = api.load_h(three, None)
        check("3P 三个角色都装上", len(res["actions"]) == 3,
              "、".join(res["actions"]))
        ok, msg = api.sync_state()
        check("3P 联动解锁", ok, msg)

    # ---------------- 单人 ----------------
    reset()
    api.character("female")
    solo = find_mode({"female": 1, "male": 0})
    check("找到单人动作", solo is not None, solo["label"] if solo else "")
    if solo:
        res = api.load_h(solo, None)
        check("单人只装一个", len(res["actions"]) == 1, "、".join(res["actions"]))
        ok, msg = api.sync_state()
        check("单人也能联动（只有一个）", ok, msg)

    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS
          else "全部通过")


if __name__ == "__main__":
    main()
