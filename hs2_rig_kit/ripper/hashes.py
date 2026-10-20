"""Unity 的动画路径哈希。

Unity 用 CRC32（反射多项式 0xEDB88320）对 Transform 的层级路径做哈希，
动画曲线里只存哈希值。这里反向建表，把哈希换回骨骼路径名。
（实测：本项目 1063 条曲线命中 1035 条，剩余为衣服/头发等本角色没有的节点。）
"""

_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0xEDB88320 if _c & 1 else 0)
    _TABLE.append(_c)


def path_hash(path):
    crc = 0xFFFFFFFF
    for ch in path:
        crc = (crc >> 8) ^ _TABLE[(crc ^ ord(ch)) & 0xFF]
    return crc ^ 0xFFFFFFFF


def build_lookup(paths):
    """路径集合 -> {hash: path}。"""
    return {path_hash(p): p for p in paths}
