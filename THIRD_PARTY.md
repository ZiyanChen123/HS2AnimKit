# 第三方组件 / Third-party components

本插件把以下第三方 Python 库**原样打包**在 `hs2_rig_kit/_vendor` 里，以便离线
（无需连网装依赖）直接加载游戏 assetbundle。它们都保留各自许可证，各自版权归其
作者所有；请在本插件的 GPL-3.0 之外同时遵守这些组件的许可证条款。

The following third-party libraries are bundled unchanged under
`hs2_rig_kit/_vendor` so the addon can load game assetbundles offline without a
network install step. They retain their own licenses, owned by their respective
authors; in addition to this project's GPL-3.0, please honour the terms of each.

| 组件 / Component        | 版本 / Version | 许可证 / License |
|-------------------------|-----------------|------------------|
| UnityPy                 | 1.25.4          | GPL-3.0          |
| Pillow (PIL)            | vendored        | HPND (PIL)       |
| attrs                   | vendored        | MIT              |
| fsspec                  | vendored        | BSD-3-Clause     |
| brotli                  | 1.2.0           | MIT              |
| lz4                     | 4.4.5           | BSD-3-Clause     |

> 因为 `_vendor` 里包含 GPL-3.0 的 UnityPy，本插件整体对外按
> **GPL-3.0** 发布。