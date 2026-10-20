"""Pillow 占位模块。

HS2 Anim Kit 只读骨架/网格/动画，不解码贴图；UnityPy 会 import PIL，
这里给出最小实现让 import 通过。真要用贴图功能请自行安装 Pillow。
"""

from . import Image  # noqa: F401

__version__ = "0.0.0-stub"


def __getattr__(name):
    def _fail(*args, **kwargs):
        raise RuntimeError("未内置 Pillow，无法处理贴图：%s" % name)
    return _fail
