"""HS2 / AI-Shoujo 角色卡（PNG）解析。

卡片的角色数据不在 PNG 图像块里，而是接在 IEND 之后：
  uint32 length + block
第一个 block 是 ``【AIS_Chara】`` 标记，第二个是 MessagePack 的
``lstInfo`` 索引（Custom / Coordinate / Parameter ... 的 offset/size），
第三个是这些子块的合并数据。

``Custom`` 子块本身是 ChaFileCustom.SaveBytes()：
  int32 face_len + face(msgpack)
  int32 body_len + body(msgpack)
  int32 hair_len + hair(msgpack)

Coordinate 子块是 ChaFileCoordinate：4 字节版本 + msgpack
{version, parts, ExtendedSaveData}，parts[0..7] 对应游戏的衣服槽。

这里只做只读解析，不修改卡片。
"""
import io
import os
import struct


def _read_png_tail(path):
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 角色卡")
    # 第一个 IEND 才是 PNG 结束；后面的角色数据里可能碰巧出现 "IEND"。
    i = raw.find(b"IEND")
    if i < 0:
        raise ValueError("PNG 里没有 IEND")
    return raw[i + 8:]


def _read_u32(data, off):
    if off + 4 > len(data):
        raise ValueError("角色卡数据截断")
    return struct.unpack_from("<I", data, off)[0], off + 4


def _read_block(data, off):
    n, off = _read_u32(data, off)
    if n == 0 or off + n > len(data):
        raise ValueError("角色卡 block 长度异常")
    return data[off:off + n], off + n


def _mp_first(data, start=0):
    """最小 MessagePack 解码器（只读），返回 (object, next_offset)。

    不依赖 msgpack 包：Blender 自带的 Python 没装它。
    """
    def need(n, o):
        if o + n > len(data):
            raise ValueError("MessagePack 数据截断")
        return o + n

    def dec(o):
        b = data[o]
        o += 1
        if b <= 0x7f:
            return b, o
        if b >= 0xe0:
            return b - 0x100, o
        if 0x80 <= b <= 0x8f:
            n = b & 0x0f
            d = {}
            for _ in range(n):
                k, o = dec(o)
                v, o = dec(o)
                d[k] = v
            return d, o
        if 0x90 <= b <= 0x9f:
            n = b & 0x0f
            a = []
            for _ in range(n):
                v, o = dec(o)
                a.append(v)
            return a, o
        if 0xa0 <= b <= 0xbf:
            n = b & 0x1f
            o = need(n, o)
            try:
                return data[o - n:o].decode("utf-8"), o
            except UnicodeDecodeError:
                return data[o - n:o], o
        if b == 0xc0:
            return None, o
        if b == 0xc2:
            return False, o
        if b == 0xc3:
            return True, o
        if b in (0xc4, 0xd9):
            n = data[o]; o += 1
            o = need(n, o)
            raw = data[o - n:o]
            if b == 0xc4:
                return raw, o
            try:
                return raw.decode("utf-8"), o
            except UnicodeDecodeError:
                return raw, o
        if b in (0xc5, 0xda):
            n = struct.unpack_from(">H", data, o)[0]; o += 2
            o = need(n, o)
            raw = data[o - n:o]
            if b == 0xc5:
                return raw, o
            try:
                return raw.decode("utf-8"), o
            except UnicodeDecodeError:
                return raw, o
        if b in (0xc6, 0xdb):
            n = struct.unpack_from(">I", data, o)[0]; o += 4
            o = need(n, o)
            raw = data[o - n:o]
            if b == 0xc6:
                return raw, o
            try:
                return raw.decode("utf-8"), o
            except UnicodeDecodeError:
                return raw, o
        if b in (0xc7, 0xc8, 0xc9):
            n = data[o]; o += 1
            if b == 0xc8:
                n = struct.unpack_from(">H", data, o)[0]; o += 2
            elif b == 0xc9:
                n = struct.unpack_from(">I", data, o)[0]; o += 4
            o += 1  # ext type
            o = need(n, o)
            return data[o - n:o], o
        if b == 0xca:
            return struct.unpack_from(">f", data, o)[0], o + 4
        if b == 0xcb:
            return struct.unpack_from(">d", data, o)[0], o + 8
        if b == 0xcc:
            return data[o], o + 1
        if b == 0xcd:
            return struct.unpack_from(">H", data, o)[0], o + 2
        if b == 0xce:
            return struct.unpack_from(">I", data, o)[0], o + 4
        if b == 0xcf:
            return struct.unpack_from(">Q", data, o)[0], o + 8
        if b == 0xd0:
            return struct.unpack_from(">b", data, o)[0], o + 1
        if b == 0xd1:
            return struct.unpack_from(">h", data, o)[0], o + 2
        if b == 0xd2:
            return struct.unpack_from(">i", data, o)[0], o + 4
        if b == 0xd3:
            return struct.unpack_from(">q", data, o)[0], o + 8
        if 0xd4 <= b <= 0xd8:
            n = (1, 2, 4, 8, 16)[b - 0xd4]
            o += 1 + n
            return None, o
        if b == 0xdc:
            n = struct.unpack_from(">H", data, o)[0]; o += 2
            a = []
            for _ in range(n):
                v, o = dec(o)
                a.append(v)
            return a, o
        if b == 0xdd:
            n = struct.unpack_from(">I", data, o)[0]; o += 4
            a = []
            for _ in range(n):
                v, o = dec(o)
                a.append(v)
            return a, o
        if b == 0xde:
            n = struct.unpack_from(">H", data, o)[0]; o += 2
            d = {}
            for _ in range(n):
                k, o = dec(o)
                v, o = dec(o)
                d[k] = v
            return d, o
        if b == 0xdf:
            n = struct.unpack_from(">I", data, o)[0]; o += 4
            d = {}
            for _ in range(n):
                k, o = dec(o)
                v, o = dec(o)
                d[k] = v
            return d, o
        raise ValueError("不支持的 MessagePack 类型 0x%02x" % b)

    return dec(start)


def _unpack(data):
    return _mp_first(data, 0)[0]


def _to_text(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _text_keys(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[_to_text(k)] = _text_keys(v)
        return out
    if isinstance(obj, list):
        return [_text_keys(v) for v in obj]
    return obj


def _read_len_prefixed(data, off):
    if off + 4 > len(data):
        raise ValueError("角色卡子块截断")
    n = struct.unpack_from("<i", data, off)[0]
    off += 4
    if n < 0 or off + n > len(data):
        raise ValueError("角色卡子块长度异常")
    return data[off:off + n], off + n


def _parse_custom(raw):
    """ChaFileCustom.SaveBytes() 的三段长度前缀 MessagePack。"""
    # 有些版本在 SaveBytes 前还有一个 4 字节块版本号。
    candidates = [0, 4]
    last = None
    for start in candidates:
        try:
            off = start
            face_raw, off = _read_len_prefixed(raw, off)
            body_raw, off = _read_len_prefixed(raw, off)
            hair_raw, off = _read_len_prefixed(raw, off)
            face = _text_keys(_unpack(face_raw))
            body = _text_keys(_unpack(body_raw))
            hair = _text_keys(_unpack(hair_raw))
            if "shapeValueFace" in face and "shapeValueBody" in body:
                return face, body, hair
        except Exception as e:
            last = e
    if last:
        raise last
    raise ValueError("无法解析 Custom 子块")


def _parse_coordinate(raw):
    # 4 字节长度 + msgpack（不同版本也可能直接是 4 字节版本 + msgpack）
    for start in (4, 0, 8):
        try:
            obj = _text_keys(_mp_first(raw, start)[0])
            if isinstance(obj, dict) and "parts" in obj:
                return obj
        except Exception:
            continue
    return {}


def load_card(path):
    """读取一张 HS2 卡，返回 dict（不依赖游戏目录）。"""
    tail = _read_png_tail(path)
    off = 0
    marker, off = _read_block(tail, off)
    # 新版卡第二段是「长度 + msgpack」；部分旧卡直接跟 msgpack。
    index = None
    for pad in range(0, 9):
        try:
            obj, after = _mp_first(tail, off + pad)
            if isinstance(obj, dict) and ("lstInfo" in obj or "lstinfo" in obj):
                index = obj
                off = after
                break
        except Exception:
            continue
    if index is None:
        index_raw, off = _read_block(tail, off)
        index = _unpack(index_raw)
    data_raw, off = _read_block(tail, off)
    info = index.get("lstInfo") or index.get("lstinfo") or []
    blocks = {}
    for it in info:
        try:
            name = _to_text(it.get("name"))
            start = int(it.get("pos", 0))
            size = int(it.get("size", 0))
            # 合并数据块前面有 4 字节头，索引里的 pos 从这 4 字节之后算。
            blocks[name] = data_raw[start + 4:start + 4 + size]
        except Exception:
            continue

    custom = blocks.get("Custom")
    if not custom:
        raise ValueError("角色卡里没有 Custom 数据")
    face, body, hair = _parse_custom(custom)
    coord = _parse_coordinate(blocks.get("Coordinate") or b"")
    param = {}
    if blocks.get("Parameter"):
        for start in (0, 4, 8):
            try:
                p = _text_keys(_unpack(blocks["Parameter"][start:]))
                if isinstance(p, dict):
                    param = p
                    break
            except Exception:
                continue

    name = ""
    for key in ("fullname", "name", "nickname"):
        v = param.get(key)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if not name:
        name = os.path.splitext(os.path.basename(path))[0]

    return {
        "path": path,
        "name": name,
        "face": face,
        "body": body,
        "hair": hair,
        "coordinate": coord,
        "parameter": param,
        "blocks": list(blocks),
        "sex": param.get("sex", 1 if hair else 0),
        "shape_face": list(face.get("shapeValueFace") or []),
        "shape_body": list(body.get("shapeValueBody") or []),
        "skin_color": list(body.get("skinColor") or [1, 1, 1, 1]),
        "skin_gloss": body.get("skinGlossPower"),
    }


def list_cards(game_dir, male=None):
    """列出 UserData/chara 下的角色卡。male=None 返回全部。"""
    base = os.path.join(game_dir, "UserData", "chara")
    roots = []
    if male is None:
        roots = [os.path.join(base, "female"), os.path.join(base, "male")]
    else:
        roots = [os.path.join(base, "male" if male else "female")]
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if fn.lower().endswith(".png"):
                out.append(os.path.join(root, fn))
    return out
