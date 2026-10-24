"""HS2 动画工具链。

* 在 Blender 里当插件用（侧栏 HS2 面板）
* 也可以脱离 Blender 单独跑解包器：``python -m hs2_rig_kit.ripper``
"""

import os
import sys

bl_info = {
    "name": "HS2 Anim Kit",
    "author": "HS2AnimKit",
    "version": (5, 1, 0),
    "blender": (3, 6, 0),
    "location": "3D View > 侧栏 > HS2",
    "description": "读取 HS2 角色、衣服、Studio 动作、H 动作与 H 道具，带 IK 控制层，"
                   "可直接导入工作室 FBX 角色（自动修骨架、对齐默认姿势），"
                   "可镜像/变速/贴地/重定向，按物体导出 UE 用的 FBX",
    "category": "Animation",
}

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if (os.path.isdir(_VENDOR) and _VENDOR not in sys.path
        and not os.environ.get("HS2_NO_VENDOR")):
    sys.path.insert(0, _VENDOR)

from . import ripper  # noqa: E402,F401

try:
    import bpy  # noqa: F401
    _HAS_BPY = True
except ImportError:
    _HAS_BPY = False


def register():
    from . import addon
    addon.register()


def unregister():
    from . import addon
    addon.unregister()
