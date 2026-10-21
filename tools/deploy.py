"""把源码挂到各 Blender 版本的 addons 目录（默认用目录联接，不复制）。

用法：
    python tools/deploy.py                # 同步到所有找到的 Blender 版本（优先做联接）
    python tools/deploy.py 5.2            # 只处理指定版本
    python tools/deploy.py --status       # 看每个版本现在是什么状态（不修改）
    python tools/deploy.py --clean        # 删掉所有 .bak-* 备份（不修改安装）
    python tools/deploy.py --copy         # 强制用复制（不给做联接时用）

为什么默认改成目录联接：
  以前是「复制一份」，于是每跑一次就多一份实体拷贝，旧的那份被改名成
  ``hs2_rig_kit.bak-<时间戳>``。Blender 会把 addons 下**每个**带 bl_info 的
  文件夹都列出来，结果插件列表里堆出几十个同名的 "HS2 Anim Kit"，
  还搞不清哪个是新的。联接只有一个目录条目，改代码立即生效。

规则：
  * 目标已经是联接 → 跳过。
  * 目标是实体目录 → 备份成 .bak-<时间戳>，再建联接；备份只留最近 3 个。
  * 目标不存在 → 直接建联接。
  * 复制模式下，复制完会校验 __init__.py 在不在，失败就还原备份。
"""

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "hs2_rig_kit")
PKG = "hs2_rig_kit"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
KEEP_BACKUPS = 3

IS_WIN = os.name == "nt"


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------

def blender_root():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(appdata, "Blender Foundation", "Blender")


def addons_root(ver):
    return os.path.join(blender_root(), ver, "scripts", "addons")


def installed_versions():
    root = blender_root()
    if not os.path.isdir(root):
        return []
    out = []
    for v in sorted(os.listdir(root)):
        if os.path.isdir(os.path.join(root, v, "scripts", "addons")):
            out.append(v)
    return out


def is_junction(path):
    """Windows 目录联接（junction）也算「指向源码」。"""
    if not os.path.exists(path):
        return False
    try:
        if os.path.islink(path):
            return True
    except OSError:
        pass
    try:
        if os.path.isjunction(path):          # Python 3.12+
            return True
    except AttributeError:
        pass
    try:
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & 0x400)            # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def make_junction(link, target):
    if IS_WIN:
        # cmd 的输出是 GBK，不能当 utf-8 解
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True)
        if r.returncode != 0:
            msg = (r.stdout or b"").decode("mbcs", "replace") + \
                  (r.stderr or b"").decode("mbcs", "replace")
            raise OSError(msg.strip())
    else:
        os.symlink(target, link, target_is_directory=True)


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
    i = txt.find('"version"')
    if i < 0:
        return None
    seg = txt[i:i + 40]
    a, b = seg.find("("), seg.find(")")
    return seg[a:b + 1] if a >= 0 and b > a else None


def backups(dst):
    """dst 旁边所有 .bak-* 备份，旧的在前。"""
    d = os.path.dirname(dst)
    base = os.path.basename(dst) + ".bak-"
    if not os.path.isdir(d):
        return []
    out = [os.path.join(d, n) for n in os.listdir(d) if n.startswith(base)]
    return sorted(out)


def prune_backups(dst, keep=KEEP_BACKUPS):
    olds = backups(dst)
    dead = olds[:-keep] if keep > 0 else olds
    for p in dead:
        shutil.rmtree(p, ignore_errors=True)
    return len(dead)


# --------------------------------------------------------------------------
# 状态 / 清理
# --------------------------------------------------------------------------

def show_status():
    src_v = read_version(SRC)
    print("源码：%s  %s" % (SRC, src_v))
    rows = []
    for v in installed_versions():
        dst = os.path.join(addons_root(v), PKG)
        baks = backups(dst)
        if is_junction(dst):
            state = "联接 → %s" % read_version(dst)
        elif os.path.isdir(dst):
            cur = read_version(dst)
            state = "实体拷贝 %s%s" % (cur, "" if cur == src_v else "  ← 旧！")
        else:
            state = "未安装"
        rows.append((v, state, len(baks)))
        # 顺手看看有没有同名的僵尸条目（别的名字里带 hs2_rig_kit）
        d = addons_root(v)
        others = [n for n in os.listdir(d)
                  if "hs2_rig_kit" in n and n != PKG
                  and os.path.isdir(os.path.join(d, n))]
        for n in others:
            rows.append((v, "多余条目：" + n, 0))
    for v, state, n in rows:
        print("  [%-4s] %-46s 备份 %d" % (v, state, n))
    return 0


def dir_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def clean_backups(want=None):
    total = 0
    freed = 0
    for v in (want or installed_versions()):
        dst = os.path.join(addons_root(v), PKG)
        n = 0
        for p in backups(dst):
            freed += dir_size(p)
            shutil.rmtree(p, ignore_errors=True)
            n += 1
        total += n
        print("  [%s] 删掉 %d 个备份" % (v, n))
    print("共清理 %d 个备份目录，释放约 %.0f MB"
          % (total, freed / 1024.0 / 1024.0))
    return 0


# --------------------------------------------------------------------------
# 同步
# --------------------------------------------------------------------------

def sync_one(ver, mode="link"):
    dst = os.path.join(addons_root(ver), PKG)
    src_v = read_version(SRC)

    if is_junction(dst):
        got = read_version(dst)
        print("  [%s] 已经是联接 → %s，跳过" % (ver, got))
        n = prune_backups(dst, keep=0)   # 联接旁边的备份都是垃圾
        if n:
            print("  [%s] 顺手清掉 %d 个旧备份" % (ver, n))
        return "link"

    if os.path.isdir(dst):
        bak = dst + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.move(dst, bak)
        print("  [%s] 旧实体拷贝 %s → 备份 %s"
              % (ver, read_version(bak), os.path.basename(bak)))
    else:
        bak = None

    try:
        if mode == "link":
            make_junction(dst, SRC)
            print("  [%s] 已建目录联接 %s" % (ver, read_version(dst)))
            kind = "link"
        else:
            shutil.copytree(SRC, dst, ignore=IGNORE)
            if read_version(dst) != src_v:
                raise OSError("复制完版本号不对（%s ≠ %s）"
                              % (read_version(dst), src_v))
            print("  [%s] 已复制 %s" % (ver, read_version(dst)))
            kind = "copy"
    except Exception:
        # 出事了就把备份还原回去，绝不留「什么都没有」的状态
        if os.path.isdir(dst) and not is_junction(dst):
            shutil.rmtree(dst, ignore_errors=True)
        if bak and os.path.isdir(bak):
            shutil.move(bak, dst)
            print("  [%s] 失败，已还原备份" % ver)
        raise

    if kind == "link":
        # 联接指向源码，备份纯属垃圾（还会被 Blender 当成同名插件列出来）
        n = prune_backups(dst, keep=0)
        if n:
            print("  [%s] 联接建好，顺手清掉 %d 个旧备份" % (ver, n))
    else:
        n = prune_backups(dst)
        if n:
            print("  [%s] 清掉 %d 个旧备份（只留最近 %d 个）"
                  % (ver, n, KEEP_BACKUPS))
    return kind


def main(argv):
    if not os.path.isdir(SRC):
        print("找不到源码目录:", SRC)
        return 1

    if "--status" in argv:
        return show_status()
    if "--clean" in argv:
        return clean_backups([a for a in argv if not a.startswith("--")] or None)

    mode = "copy" if "--copy" in argv else "link"
    want = [a for a in argv if not a.startswith("-")] or installed_versions()
    if not want:
        print("没找到任何 Blender addons 目录")
        return 1

    print("源码版本：%s（%s）" % (read_version(SRC), mode == "link" and "联接" or "复制"))
    for v in want:
        if not os.path.isdir(addons_root(v)):
            print("  [%s] 无 addons 目录，跳过" % v)
            continue
        try:
            sync_one(v, mode=mode)
        except Exception as e:
            print("  [%s] 失败：%s: %s" % (v, type(e).__name__, e))
            if mode == "link" and IS_WIN:
                print("        （可以试试 python tools/deploy.py %s --copy）" % v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
