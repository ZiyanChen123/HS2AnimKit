"""在 Blender 里启用（并重新加载）hs2_rig_kit，同时保存偏好设置。

用法：
    blender.exe -b -P tools/enable_addon.py
"""

import addon_utils
import bpy

MOD = "hs2_rig_kit"

try:
    bpy.ops.preferences.addon_enable(module=MOD)
except Exception as e:
    print("[install] 启用失败：%s" % e)
    raise SystemExit(1)

ok = any(getattr(m, "__name__", "") == MOD for m in addon_utils.modules())
print("[install] 已注册模块: %s" % ok)
try:
    print("[install] 已启用: %s" % (MOD in bpy.context.preferences.addons))
except Exception:
    pass

try:
    bpy.ops.wm.save_userpref()
    print("[install] 偏好设置已保存")
except Exception as e:
    print("[install] 保存偏好失败：%s" % e)

import sys
sys.modules.get(MOD)
m = sys.modules.get(MOD)
if m is not None:
    print("[install] 版本 %s" % (m.bl_info.get("version"),))
    print("[install] 路径 %s" % m.__file__)
