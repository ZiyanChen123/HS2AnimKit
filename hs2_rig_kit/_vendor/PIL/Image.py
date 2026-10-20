"""最小 Image 占位实现。"""


class _Stub(object):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("未内置 Pillow，无法处理贴图")

    def __call__(self, *args, **kwargs):
        raise RuntimeError("未内置 Pillow，无法处理贴图")


Image = _Stub


def new(*args, **kwargs):
    raise RuntimeError("未内置 Pillow，无法处理贴图")


def open(*args, **kwargs):
    raise RuntimeError("未内置 Pillow，无法处理贴图")


def frombytes(*args, **kwargs):
    raise RuntimeError("未内置 Pillow，无法处理贴图")
