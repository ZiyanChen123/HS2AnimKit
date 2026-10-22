"""Texture decoding and channel conversion for the clothing exporter.

Unity ships DXT1/DXT5/BC7 blocks.  Instead of depending on Pillow (the main
HS2 add-on deliberately does not vendor a working Pillow), we wrap the first
mip in a DDS container and let Blender decode it.  The resulting image can be
saved as PNG and, for normal maps, rewritten in place.
"""

import os
import struct
import tempfile

import bpy

from .ripper import unityfs


def _dds_header(width, height, fmt):
    """Build a DDS header for the formats used by HS2 clothing."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<I", hdr, 4, 124)
    struct.pack_into("<I", hdr, 8, flags)
    struct.pack_into("<I", hdr, 12, height)
    struct.pack_into("<I", hdr, 16, width)
    struct.pack_into("<I", hdr, 28, 1)
    struct.pack_into("<I", hdr, 76, 32)
    struct.pack_into("<I", hdr, 80, 0x4)
    struct.pack_into("<I", hdr, 108, 0x1000)

    if fmt == 25:                       # BC7 -> DDS DX10
        struct.pack_into("<I", hdr, 20, max(1, (width + 3) // 4 * 16))
        hdr[84:88] = b"DX10"
        dx = bytearray(20)
        struct.pack_into("<I", dx, 0, 98)   # DXGI_FORMAT_BC7_UNORM
        struct.pack_into("<I", dx, 4, 3)    # TEXTURE2D
        struct.pack_into("<I", dx, 8, 0)
        struct.pack_into("<I", dx, 12, 1)
        struct.pack_into("<I", dx, 16, 0)
        return bytes(hdr) + bytes(dx)

    if fmt == 10:                       # DXT1
        struct.pack_into("<I", hdr, 20, (width + 3) // 4 * 8)
        hdr[84:88] = b"DXT1"
    elif fmt == 12:                     # DXT5
        struct.pack_into("<I", hdr, 20, (width + 3) // 4 * 16)
        hdr[84:88] = b"DXT5"
    else:
        raise NotImplementedError("unsupported compressed format %s" % fmt)
    return bytes(hdr)


def _copy_image(img, name="_hs2_tex"):
    """把已解码的图像复制成「生成图像」，彻底断开对临时 DDS 文件的依赖。"""
    import numpy as np
    w, h = img.size
    _ = img.pixels[0]
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    out = bpy.data.images.new(name, width=w, height=h, alpha=True)
    try:
        out.colorspace_settings.name = img.colorspace_settings.name
    except Exception:
        pass
    out.pixels.foreach_set(buf)
    out.update()
    out.pack()
    return out


def _load_texture(game_dir, bundle_path, tex, bundle=None):
    """Decode a Texture2D into a Blender image; caller owns it.

    ``bundle`` 可以传入已经打开的 Bundle；批量导出时所有贴图共用一次解包。
    """
    b = bundle
    own = b is None
    if own:
        b = unityfs.Bundle(bundle_path)
    try:
        obj = b.objects.get(tex.get("pid"))
        if obj is None:
            return None, None
        t = obj.read()
        raw = t.get_image_data()
        fmt = int(t.m_TextureFormat)
        w, h = int(t.m_Width), int(t.m_Height)
        if fmt in (10, 12, 25):
            data = _dds_header(w, h, fmt) + bytes(raw)
            tmp_dir = os.path.join(
                tempfile.gettempdir(), "hs2_animkit")
            os.makedirs(tmp_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".dds", dir=tmp_dir)
            os.close(fd)
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                src = bpy.data.images.load(tmp, check_existing=False)
                try:
                    # 复制成生成图像再删临时文件：只 pack 的话 Blender 仍会
                    # 按原路径去读 DDS，文件一删材质就变白。
                    img = _copy_image(src)
                finally:
                    bpy.data.images.remove(src)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return img, None

        if fmt in (3, 4):
            return _raw_image(raw, w, h, 3 if fmt == 3 else 4), None
        return None, "unsupported texture format %s (%s)" % (fmt, tex.get("name"))
    except Exception as e:
        return None, "%s: %s" % (tex.get("name"), e)
    finally:
        if own:
            try:
                b.env = None
            except Exception:
                pass


def _raw_image(raw, w, h, channels):
    """Build an image from uncompressed Unity bytes (first mip only)."""
    import numpy as np
    n = w * h * channels
    if len(raw) < n:
        raise RuntimeError("short uncompressed texture data")
    a = np.frombuffer(raw[:n], dtype=np.uint8)
    if channels == 3:
        rgb = a.reshape(h, w, 3).astype(np.float32) / 255.0
        rgba = np.ones((h, w, 4), dtype=np.float32)
        rgba[:, :, :3] = rgb
    else:
        rgba = a.reshape(h, w, 4).astype(np.float32) / 255.0
    # Unity stores the first row at the top; Blender's pixel buffer starts
    # at the bottom-left, so flip the rows before handing it over.
    rgba = rgba[::-1]
    img = bpy.data.images.new("_hs2c_tex", width=w, height=h, alpha=True)
    img.pixels.foreach_set(rgba.ravel())
    img.pack()
    return img


def _image_pixels(img):
    import numpy as np
    # Accessing one element forces Blender to decode/load a packed DDS before
    # foreach_get; otherwise the buffer can come back as all zeroes.
    _ = img.pixels[0]
    n = img.size[0] * img.size[1] * 4
    buf = np.empty(n, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(img.size[1], img.size[0], 4)


def _normalize_normal(img, flip_green=False):
    """Convert Unity DXT5nm / RGB normal data to a standard tangent normal."""
    import numpy as np
    a = _image_pixels(img).copy()
    r, g, b, alpha = a[:, :, 0], a[:, :, 1], a[:, :, 2], a[:, :, 3]
    r_std = float(r.std())
    a_std = float(alpha.std())
    # HS2's cf_*_n maps commonly store X in A, Y in G, with R constant.
    if r_std < 0.01 and a_std > 0.01:
        x = alpha * 2.0 - 1.0
        y = g * 2.0 - 1.0
    elif b.std() > 0.02:
        x = r * 2.0 - 1.0
        y = g * 2.0 - 1.0
    else:
        x = alpha * 2.0 - 1.0
        y = g * 2.0 - 1.0
    if flip_green:
        y = -y
    z = np.sqrt(np.maximum(0.0, 1.0 - x * x - y * y))
    a[:, :, 0] = (x + 1.0) * 0.5
    a[:, :, 1] = (y + 1.0) * 0.5
    a[:, :, 2] = (z + 1.0) * 0.5
    a[:, :, 3] = 1.0
    img.pixels.foreach_set(a.ravel())
    img.update()


def _save_png(img, out_path, role):
    """Copy the decoded pixels into a generated image before writing PNG."""
    import numpy as np
    w, h = img.size
    if w <= 0 or h <= 0:
        raise RuntimeError("decoded image has no pixels")
    _ = img.pixels[0]
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    out = bpy.data.images.new(
        "_hs2c_png", width=w, height=h, alpha=True)
    try:
        # Set the colour space before filling the buffer: changing it after
        # update() makes Blender re-load an empty file and zeroes the pixels.
        try:
            out.colorspace_settings.name = (
                "Non-Color" if "normal" in role else "sRGB")
        except Exception:
            pass
        out.pixels.foreach_set(buf)
        out.update()
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out.filepath_raw = out_path
        out.file_format = "PNG"
        out.save()
    finally:
        try:
            bpy.data.images.remove(out)
        except Exception:
            pass


def _tint_image(img, tint):
    """把 _Color 乘进 basecolor 的 RGB；A 通道保持为遮罩。"""
    import numpy as np
    _ = img.pixels[0]
    n = img.size[0] * img.size[1] * 4
    buf = np.empty(n, dtype=np.float32)
    img.pixels.foreach_get(buf)
    a = buf.reshape(-1, 4)
    a[:, 0] *= float(tint[0])
    a[:, 1] *= float(tint[1])
    a[:, 2] *= float(tint[2])
    img.pixels.foreach_set(buf)
    img.update()


def export_texture(game_dir, bundle_path, tex, out_path, role,
                   flip_green=False, bundle=None, tint=None):
    """Decode one texture and write a PNG.  Returns (path, warning)."""
    img, err = _load_texture(game_dir, bundle_path, tex, bundle=bundle)
    if img is None:
        return None, err or "texture not found"
    try:
        if "normal" in role:
            _normalize_normal(img, flip_green=flip_green)
        elif role == "basecolor" and tint:
            _tint_image(img, tint)
        _save_png(img, out_path, role)
        return out_path, None
    except Exception as e:
        return None, "%s: %s" % (tex.get("name"), e)
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
