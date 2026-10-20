"""解码游戏里的动作（AnimationClip）。

HS2 的 Studio 动作是 Unity 的 humanoid clip，但真正存储骨骼运动的是
m_ClipBindingConstant 里的 genericBindings（352 根骨 × 位置/旋转/缩放）
加上 m_MuscleClip 里的三段曲线数据：

    StreamedClip  稀疏关键帧，每帧存 (时间, key 数, 每个 key 的曲线号 + 4 个 Hermite 系数)
    DenseClip     定帧率采样，m_SampleArray[frame * curveCount + ch]
    ConstantClip  常量

三段覆盖的通道号是连续的：streamed -> dense -> constant。
实测 3527 个通道 == 352*3 + 352*4 + 352*3 + 7，与数据长度完全吻合。
"""

import math
import struct

DIM = {1: 3, 2: 4, 3: 3}          # Transform 的 position / rotation / scale
ATTR_NAME = {1: "pos", 2: "rot", 3: "scale"}


class ClipData(object):
    def __init__(self, name, fps, duration, tracks, stats):
        self.name = name
        self.fps = fps
        self.duration = duration
        self.tracks = tracks
        self.stats = stats

    @property
    def frame_count(self):
        for tr in self.tracks.values():
            for v in tr.values():
                return len(v)
        return 0

    def to_dict(self):
        return {
            "format": "hs2-clip",
            "version": 1,
            "name": self.name,
            "fps": self.fps,
            "duration": self.duration,
            "frame_count": self.frame_count,
            "stats": self.stats,
            "tracks": self.tracks,
        }


# --------------------------------------------------------------------------
# 通道采样器
# --------------------------------------------------------------------------

class _Sampler(object):
    """把三段曲线包装成 单通道 -> 某时刻值 的函数。"""

    def __init__(self, clip_data):
        st = clip_data["m_StreamedClip"]
        de = clip_data["m_DenseClip"]
        co = clip_data["m_ConstantClip"]

        self.n_st = int(st.get("curveCount", 0))
        self.n_de = int(de.get("m_CurveCount", 0))
        self.n_co = len(co.get("data") or [])

        self.de_frames = int(de.get("m_FrameCount", 0))
        self.de_rate = float(de.get("m_SampleRate", 30.0)) or 30.0
        self.de_begin = float(de.get("m_BeginTime", 0.0))
        self.de_arr = de.get("m_SampleArray") or []
        self.const = co.get("data") or []

        # streamed：拆成 per-channel 关键帧表
        self.stream = {}
        data = st.get("data") or []
        if data:
            buf = b"".join(struct.pack("<I", w & 0xFFFFFFFF) for w in data)
            off = 0
            n = len(buf)
            while off + 8 <= n:
                t = struct.unpack_from("<f", buf, off)[0]
                off += 4
                keys = struct.unpack_from("<i", buf, off)[0]
                off += 4
                if keys < 0 or off + keys * 20 > n:
                    break
                tt = -1e9 if (t != t or t < -1e8) else t
                for _ in range(keys):
                    idx = struct.unpack_from("<i", buf, off)[0]
                    off += 4
                    c = struct.unpack_from("<4f", buf, off)
                    off += 16
                    if t != t or t > 1e30:      # inf 帧只是终止符
                        continue
                    self.stream.setdefault(idx, []).append((tt, c))

    def channel_count(self):
        return self.n_st + self.n_de + self.n_co

    def value(self, ch, t):
        if ch < self.n_st:
            keys = self.stream.get(ch)
            if not keys:
                return 0.0
            # 二分找最后一个 time <= t
            lo, hi = 0, len(keys)
            while lo < hi:
                mid = (lo + hi) // 2
                if keys[mid][0] <= t:
                    lo = mid + 1
                else:
                    hi = mid
            i = lo - 1
            if i < 0:
                return keys[0][1][3]
            kt, c = keys[i]
            dt = t - kt
            return ((c[0] * dt + c[1]) * dt + c[2]) * dt + c[3]

        if ch < self.n_st + self.n_de:
            if not self.de_frames:
                return 0.0
            i = ch - self.n_st
            x = (t - self.de_begin) * self.de_rate
            f0 = int(math.floor(x))
            if f0 < 0:
                f0 = 0
            if f0 > self.de_frames - 1:
                f0 = self.de_frames - 1
            f1 = min(f0 + 1, self.de_frames - 1)
            a = min(max(x - f0, 0.0), 1.0)
            cc = self.n_de
            v0 = self.de_arr[f0 * cc + i]
            v1 = self.de_arr[f1 * cc + i]
            return v0 + (v1 - v0) * a

        i = ch - self.n_st - self.n_de
        return self.const[i] if i < self.n_co else 0.0


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def decode(typetree, path_lookup, fps=None, only_paths=None):
    """typetree: AnimationClip 的 typetree 字典
    path_lookup: {hash: 骨骼路径}
    返回 ClipData
    """
    name = typetree.get("m_Name", "")
    sample_rate = float(typetree.get("m_SampleRate") or 30.0) or 30.0
    fps = fps or sample_rate

    muscle = typetree["m_MuscleClip"]
    stop = float(muscle.get("m_StopTime") or 0.0)
    start = float(muscle.get("m_StartTime") or 0.0)
    clip = muscle["m_Clip"]["data"]

    sampler = _Sampler(clip)
    binds = typetree["m_ClipBindingConstant"]["genericBindings"]

    # 通道分配：严格按 bindings 顺序
    spans = []
    ch = 0
    for b in binds:
        dim = DIM.get(b.get("attribute"), 1)
        spans.append((ch, dim, b))
        ch += dim

    total = sampler.channel_count()
    if ch != total:
        # 通道数和数据类型不符时仍然继续，但记录下来
        pass

    frames = int(round((stop - start) * fps)) + 1
    frames = max(frames, 2)
    times = [start + i / fps for i in range(frames)]

    tracks = {}
    matched = 0
    missing = 0
    for start_ch, dim, b in spans:
        attr = b.get("attribute")
        if attr not in ATTR_NAME or b.get("typeID") != 4:
            continue
        path = path_lookup.get(b.get("path"))
        if not path:
            missing += 1
            continue
        if only_paths is not None and path not in only_paths:
            continue
        key = ATTR_NAME[attr]
        vals = []
        for t in times:
            for k in range(dim):
                vals.append(sampler.value(start_ch + k, t))
        tr = tracks.setdefault(path, {})
        if key == "rot":
            tr["rot"] = _pack_quats(vals)
        else:
            tr[key] = _pack_vecs(vals)
    matched = len(tracks)

    stats = {
        "bindings": len(binds),
        "channels": ch,
        "curves_expected": total,
        "tracks": matched,
        "unresolved_bindings": missing,
        "frames": frames,
    }
    return ClipData(name, fps, stop - start, tracks, stats)


def _pack_vecs(flat):
    return [[round(flat[i], 5), round(flat[i + 1], 5), round(flat[i + 2], 5)]
            for i in range(0, len(flat) - 2, 3)]


def _pack_quats(flat):
    out = []
    prev = None
    for i in range(0, len(flat) - 3, 4):
        q = [flat[i], flat[i + 1], flat[i + 2], flat[i + 3]]
        n = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
        if n > 1e-8:
            q = [c / n for c in q]
        else:
            q = [0.0, 0.0, 0.0, 1.0]
        if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0.0:
            q = [-c for c in q]
        prev = q
        out.append([round(c, 6) for c in q])
    return out


def clip_names(bundle):
    names = []
    for pid in bundle.clips():
        try:
            names.append(bundle.typetree(pid).get("m_Name", ""))
        except Exception:
            continue
    return names


def clip_fit(typetree, lookup):
    """这个动作有多少比例的骨骼绑定能对上当前骨架（0~1）。

    HS2 的动作库里混着一堆不是给标准女骨的：男角色、道具、椅子、双人动作，
    它们的 binding 路径（比如 N_/k_f_ 前缀或用别的骨架）在当前骨架上找不到。
    把这些标出来，滑条/下拉框里就不会出现「点了没反应」的动作。
    """
    try:
        binds = typetree["m_ClipBindingConstant"]["genericBindings"]
    except Exception:
        return 0.0
    total = hit = 0
    for b in binds:
        if b.get("typeID") != 4:
            continue
        total += 1
        if b.get("path") in lookup:
            hit += 1
    return (hit / float(total)) if total else 0.0


def find_clip(bundle, name):
    for pid in bundle.clips():
        try:
            tt = bundle.typetree(pid)
        except Exception:
            continue
        if tt.get("m_Name") == name:
            return tt
    return None
