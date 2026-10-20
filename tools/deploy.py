"""把源码同步到各 Blender 版本的 addons 目录（部署 / 修版本漂移）。

用法：
    python tools/deploy.py            # 同步到所有找到的 Blender 版本
    python tools/deploy.py 5.2        # 只同步指定版本

规则：
  * 目标是符号链接（指向源码）→ 跳过，本来就是同步的。
  * 目标是实体目录 → 整目录替换（先备份成 .bak-<时间戳>）。
  * 目标不存在 → 直接复制。
  * 永远不复制 __pycache__ / .pyc。
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "hs2_rig_kit")
PKG = "hs2_rig_kit"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def addons_root(ver):
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(appdata, "Blender Foundation", "Blender", ver, "scripts", "addons")


def installed_versions():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    root = os.path.join(appdata, "Blender Foundation", "Blender")
    if not os.path.isdir(root):
        return []
    out = []
    for v in sorted(os.listdir(root)):
        if os.path.isdir(os.path.join(root, v, "scripts", "addons")):
            out.append(v)
    return out


def read_version(pkg_dir):
    """从 __init__.py 里抠 bl_info 的 version。"""
    f = os.path.join(pkg_dir, "__init__.py")
    if not os.path.isfile(f):
        return None
    try:
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return None
    key = '"version"'
    i = txt.find(key)
    if i < 0:
        return None
    seg = txt[i : i + 40]
    a = seg.find("(")
    b = seg.find(")")
    return seg[a : b + 1] if a >= 0 and b > a else None


def sync_one(ver):
    dst = os.path.join(addons_root(ver), PKG)
    src_v = read_version(SRC)

    if os.path.islink(dst):
        print(f"  [{ver}] 符号链接 → 源码，跳过（{read_version(dst)}）")
        return "link"

    if os.path.isdir(dst):
        old_v = read_version(dst)
        if old_v == src_v:
            # 同版本也覆盖一次，防止目录是旧拷贝但版本号相同
            pass
        bak = dst + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.move(dst, bak)
        print(f"  [{ver}] 旧实体拷贝 {old_v} → 备份 {os.path.basename(bak)}")
        shutil.copytree(SRC, dst, ignore=IGNORE)
        print(f"  [{ver}] 已安装 {read_version(dst)}")
        return "copy"

    shutil.copytree(SRC, dst, ignore=IGNORE)
    print(f"  [{ver}] 全新安装 {read_version(dst)}")
    return "new"


def main():
    if not os.path.isdir(SRC):
        print("找不到源码目录:", SRC)
        return 1
    want = sys.argv[1:] or installed_versions()
    if not want:
        print("没找到任何 Blender addons 目录")
        return 1
    print("源码版本:", read_version(SRC))
    for v in want:
        if not os.path.isdir(addons_root(v)):
            print(f"  [{v}] 无 addons 目录，跳过")
            continue
        try:
            sync_one(v)
        except Exception as e:
            print(f"  [{v}] 失败: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
