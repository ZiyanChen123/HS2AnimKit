# HS2 H 动作 / 道具 数据结构笔记

> 逆向记录，配套 `hs2_rig_kit/ripper/hdb.py`、`ripper/prop.py`。
> 目标：让「加载、改、导出」这三件事都有据可查，不靠猜。

---

## 1. H 动画放在哪

Studio 动作在 `abdata/studio/anime/*.unity3d`（10 个包，1261 个 clip，**全部已收录**）。
H 动画是另一套，按「场景 × 类型 × 番号」拆开：

```
abdata/animator/h/female/<pack>/<type>.unity3d     女角
abdata/animator/h/male/<pack>/<type>.unity3d       男角
abdata/animator/h/h_item/<pack>/<type>.unity3d     道具
abdata/h/h_item/<pack>.unity3d                     道具 prefab
abdata/list/h/animationinfo/<scene>.unity3d        日文名 + 挂点表（含杂项表）
abdata/list/h/hitemobj/<scene>.unity3d             同样的表，只留道具部分
```

`<pack>` 是内容包号：`01 08 10 13 30 34 40 41 50 54 100 mrcoffee`。
`<type>` 是玩法：`houshi`(奉仕) / `aibu`(愛撫) / `sonyu`(挿入) /
`tokushu`(特殊) / `les`(百合) / `3P` / `h2h` / `h2s` …

当前扫出来的量：

| 项 | 数量 |
|---|---|
| 表里的 H 模式（去重） | 287 |
| 有控制器的模式 | 132 |
| 有女角动作 | 129 |
| 有男角动作 | 118 |
| 有道具动作 | 44 |
| 带道具 prefab | 59 |
| AnimatorController | 551 |

## 2. clip 名不能当身份

`animator/h/female/01/houshi.unity3d` 里有 1070 个 clip，但只有二十来个
不同名字（`S_Idle` / `L_WLoop1` / `M_Orgasm_OUT` …）—— 每个控制器都引用其中
一个子集，名字是「阶段名」不是「动作名」。

所以：**动作的身份 = (包, path_id)**，不是名字。加载必须走
`GameAssets.load_ref(bundle, pid)`。（`hs2_rig_kit/ripper/__init__.py`）

## 2.5 S/M/L 不是强中弱，是「身高」；胸部动作是「胸围 5 档」

一开始以为 clip 名里的 `S_Idle` / `M_WLoop1` / `L_WLoop1` 是「强 / 中 / 弱」，
后来顺着用户反馈的「H 动作高度对不齐」去查了游戏怎么路由，结论正好相反：

**（一）S/M/L = 身高，游戏用 1D 混合树连续插值**

- 动作控制器里有一条混合树，参数名就叫 **`height`**，阈值 `[0, 0.5, 1]`，
  子 clip 是 `S_Idle / M_Idle / L_Idle` 这一套；
- 工作室动作 `chair_17_S / chair_17_M / chair_17_L` 走的是**同一条树**
  （`catalog.py` 里能看到它们源 FBX 路径只差一个 `_S/_M/_L` 后缀）；
- 也就是同一个动作做了**三份体型适配**（矮/中/高），播放时按角色的 height 参数
  在相邻两档之间 slerp（位置线性、四元数球面插值）。

**（二）胸部动作 = 胸围 5 档，是独立状态不是混合树**

- 后缀 `BreastS / BreastSM / BreastM / BreastML / BreastL` 对应胸围
  `最小 / 小 / 中 / 大 / 最大`（`BUST_CN`），前缀 `W/W1/W2/S/S1/S2/M/O` 是相位
  （弱/强/绝顶…），例如 `S1BreastL` = 强1·最大胸；
- 这些是**独立状态**，不是混合树——游戏里换胸围会整段换 clip。

**（三）游戏怎么路由（反编译 Assembly-CSharp.dll 的结论）**

- `HScene`/`Controller.SetHeightParameter` 把角色的**身高 shape 值**（`GetShapeBodyValue(0)`，
  范围 0~1）直接塞进 Animator 的 `height` 参数 —— 所以混合树阈值 `[0,0.5,1]`
  就是身高 shape 的 0/0.5/1；
- 身高 shape → 实际身高，走的是捏人对表 `list/customshape.unity3d` 里的
  `cf_anmShapeBody`（源骨骼 `cf_s_height` 的 scale 动画，线性）：
  **shape 0 → scale 0.8，shape 0.5 → 0.9，shape 1 → 1.0**；
- `cf_N_height`（动画骨架里「根→髋」那根）是身高的**目标骨骼**。缩放它 = 整体缩
  「髋以上身体」，实测髋高度 `1.1435 × scale` 严格线性。

**（四）插件怎么复现**

`hdb.split_variants()` 拆出「基名 + 身高/胸围 + 相位」，
`hdb.group_stages()` 把 S/M/L 归成一个「身高组」、胸围 5 档归成一个「胸围组」，
`hdb.group_variants(group)` 给出组里实际存在的档位。UI 上**不做滑条**，改成
**动态下拉**：只有当前阶段真有身高/胸围变体时才显示，点选离散档位：

- **身高**（矮/中/高）：选哪档就播哪档的 clip（不混合），并同步把 `cf_N_height`
  骨骼缩到 `0.8 / 0.9 / 1.0`（比例来自上面的捏人对表）—— 这样 S 档动画配矮身体、
  L 档动画配高身体，手/胯接触点自然对上，视图里看着就合理；
- **胸围**（最小/小/中/大/最大）：只切动画档，**不捏身体**——真正的身体变形要
  解析捏人对表驱动一堆 `cf_J_Mune_*_s_*` 骨骼，比较复杂，暂不深入。

道具的动画包里有 `S_Idle / M_Idle / L_Idle` 这套，道具跟角色走同一档（选 S 档
道具也播 S 档）；但**每件道具的动画是独立控制器**（控制器名带 prop 名，如
`ais_i_45_himolong_armL`），纯挂绳（`p_item_himo_*L2`）没有动画，表里 `animator`
列为空。插件按 `prop.animator` 名逐件找各自的控制器（见 `api._prop_ctrl_for`）。

## 3. 表不止一种：先按表头认表，再谈列号

`list/h/{animationinfo,hitemobj}/*.unity3d` 里塞着好几张 MonoBehaviour，名字
**全都叫 `<type>_<pack>`**，但表头从 12 列到 106 列都有。实测到的有：

**主体位表（41 / 42 / 48 / 49 列）—— 这才是真正的「H 动作清单」**

```
0 日文名   1 ID
2-7   ［男］     ベースassetpath / ベース / assetpath / アニメーション名 / 当たり判定 / 首制御
8-13  ［女］     同上（48 列百合表里 8-13 是［女攻め］、15-20 才是［女］）
14-19 ［二人目］ 同上（49 列 3P 表）
然后：行為系 / 操作系 / 場所 / オフセット / アイテムがいるか / 脱力 / 状態 … 系統 / 複数人数フラグ …
```

每行一个体位，**男女各自用哪个 Animator 控制器直接写在表里**，不用猜编号。

**道具表（28 / 31 / 36 / 37 列）—— 一行一件道具**

```
0 日文名  1 id  2 ポーチ…  3 アイテムID  4 manifest名
5 asset(道具包)  6 オブジェクト  7-8 ベース包/animator  9-10 asset(动画包)/アニメーター
然后 4~5 组「怎么挂」，每组 5 列：
    直接親子付けするか / 誰に親子付 / 親子付先 / くっつけるもの / 親スケールの適応
```

28 列的变体（`aibu_00`）没有「ポーチ…」「アイテムID」「直接親子付け」三列，
manifest 起手就差 3 格、每组只有 4 列 —— **所以列号必须按表头当场算**。

**其它表**（`neck_*` 颈部 / `se_*` 音效 / `siru_*` 汁 / 粒子 / 表情）：
表头里既没有「アニメーション名［」也没有「親子付先」，一律跳过。

> 之前按「列数 ≥ 16 + 固定列号」读，把主体位表当成道具表读，
> 一段动画的道具包、挂点全是错的；而只写在主体位表里的体位直接丢掉 ——
> 这就是「动画缺失」和「道具挂错性别」的根因。现在 `read_h_tables()` 按表头认表。

### 3.1 誰に親子付：**0 = 男、1 = 女**（以前写反了）

四条互相独立的证据：

| 证据 | 谁 | 骨/节点 | 只有 0=男 才说得通 |
|---|---|---|---|
| 男拘束フェラ / 男拘束立位 | 绳子·枷锁 = `0` | `k_f_armlowL_00` | 被绑的是男角 |
| 拘束二穴バイブ | 普通振动棒 `0`、アナルバイブ `1`（直接親子付け） | `k_f_handL_00` / `k_f_ana_00` | `k_f_ana_00` 是女角身上的骨 |
| ワンコバック / ワンコフェラ | 把手 `p_item_motite` = `0`、链子 `p_item_chain` 第二组 = `1` | `k_f_handR_00` / `neckpoint`+`Link_[0]` | 男角牵、女角戴项圈 |
| カメラ撮影オナニー | 摄像机 = `0` | `k_f_handR_00` | 男角手持拍摄 |

`4` = **场景里的固定物**（骨名是 `o_h2_mi_bathRoom06a_00`、`n_shower_pivot`
这种房间物件，不在角色身上），插件归到 `scene`，找不到就让它自由摆放。

### 3.2 一件道具可以写好几组挂点

「怎么挂」那 5 列会重复 4~5 次，语义是**一串挂载**，不是二选一：

```
p_item_himo_h104_armL（男拘束フェラ）
  组0  誰=0(男)  骨=cf_J_Root    くっつけるもの=（空）
  组1  誰=0(男)  骨=k_himo_armL  くっつけるもの=N_ido_himolong_armL   ← 真挂点

p_item_chain（ワンコバック 的牵引绳）
  组0  誰=0(男)  骨=null        くっつけるもの=（空）    ← 占位，跳过
  组1  誰=1(女)  骨=neckpoint   くっつけるもの=Link_[0]  ← 真挂点
```

Blender 里一件道具只能挂一处，`hdb.pick_anchor()` 的挑法：

1. 骨名是占位符的（`null` / `0` / `None`）跳过；
2. **有「くっつけるもの」的那组优先** —— 那是绳子末端、牵引绳各节的真锚点；
3. 都没有就取第一组。

实测：吊るし挿入 的长绳锚点 `N_ido_himolong_armL` 落在骨 `k_himo_armL` 上，
**偏差 0.00000 m**（`k_himo_armL` 在另一件道具 `p_item_himo_armL2` 上）。

同一 `(pack, type, id)` 有多行 = 这段动画挂多件道具（拘束类：左右手臂、左右腿
各一条绳）。同名 prefab 在一张表里写了两行的（MrC 的 `mrc_item_chair08`），
`playable_props()` 只留更有用的那行，免得场景里多出一把椅子。

## 4. 控制器命名 = 连接键，也是「人数/性别」的来源

```
aih_f_17                      奉仕 pack01 番号17 女1
aih_m_17                      同上 男1
aih_i_17_girotinkousoku_kotei 同上 道具（带 slug）
ail_f1_00 / ail_f2_00         百合：女1 + 女2
ai3p_f1_00 / ai3p_f2_00 / ai3p_m_00   3P：女1 + 女2 + 男1
h2_mf2_f2_00                  comp=mf2（1男2女），槽 f2
h2_m2f_m1_00 / h2_m2f_m2_00   comp=m2f（2男1女），槽 m1/m2
```

正则：

```
^(prefix)_(?:(comp: m2f|mf2|2f|2m)_)?(slot: [fmi]\d?)_(id)(?:_(slug))?$
```

- `slot` ∈ `f / f1 / f2 / m / m1 / m2 / i`，**通用槽归一**成 `f→f1`、`m→m1`；
- `comp` 是场景构成描述（`m2f` = 2男1女），槽位还是分开的；
- 于是「这段动作要几个人、什么性别」= 抽出所有 `slot`（去掉 `i`）：

| 类型 | 槽 | 阵容 |
|---|---|---|
| sonyu / houshi / aibu | f1 + m1 | 女1 + 男1 |
| tokushu（单人） | f1 | 女1 |
| les | f1 + f2 | 女1 + 女2 |
| h2_mf2 | f1 + m1（另有 f2） | 1男2女 |
| h2_m2f / 3P | f1 + m1 + m2 | 2男1女 |

番号对上表里的 id 就是同一条 H 动画；**3P 那几张表的 id 和控制器编号不是一回事**
（表里 100 → `h2_m2f_*_00`），靠表里附带的 animator 名反查：

- 3P 表的 7/8 列是 `脖子控制器名 / 包路径`，**顺序和别的表相反**
  （别的表是 `包 / 名字`），按「哪个带路径分隔符」自动判；
- 名字 `neck_h2_m2f_m1_00` 去掉 `neck_` 就是控制器名本体，再解析一次就拿到
  `prefix=h2, comp=m2f, slot=m1, id=00`；
- 用它去包里找同 `(prefix, comp, id)` 的控制器，槽位自然就齐了。

当前统计：287 条表记录 → **151 段可用**（单人 9、男女 118、百合 5、3P 19）。

## 4.5 道具内部的「挂点」

表里第 14 列（くっつけるもの）不是空的只有 3 行，但很关键：

| 道具 | 挂到 | 用自己哪个节点对齐 |
|---|---|---|
| `p_item_showerrope` | `n_rope1` | `Link_[0]` |
| `p_item_cableL` | `n_munesaki_L` | `n_rootL` |
| `p_item_cableR` | `n_munesaki_R` | `n_rootR` |

而且它们的 `parent_bone` **不是角色骨**，是另一件道具（淋浴头 / 机械本体）里的节点。
所以挂载要分两步：先找谁身上有这根骨（角色 → 已装道具），再让内部节点落上去：

```
prop_world @ L = bone_world      →   prop_world = bone_world @ L⁻¹
```

`L` 是那个节点相对道具根的矩阵。实测 `Link_[0]` 的落点误差 **0.00000 m**。

控制器里的 `m_AnimationClips` 给出阶段列表，`m_TOS` 是字符串表
（Studio 动作的 `m_TOS` 里还留着源 FBX 路径，见 `catalog.py`）。

## 5. 道具 prefab 的内部结构

```
p_item_dildo                    ← 顶层 Transform（被挂到角色骨上，局部=单位）
  ├ N_dildo_Angle               ← 角度控制节点
  │  └ J_dildo_root → J_dildo_00 … 06     ← 骨骼链，动画打在这里
  ├ O_dildo_00                  ← SMR（8 骨蒙皮）mesh=O_dildo_00
  └ O_dildo_00_d                ← 另一个 SMR
```

- 没有 Avatar，每件道具一套自己的小骨架；
- 动作路径相对 **prefab 根**（`N_dildo_Angle/J_dildo_root/…`），
  所以解道具动画时要换一套路径表，不能套角色骨架；
- 网格两种：SMR（有骨骼槽位 + 权重）、MeshFilter（静态，挂在一个节点上）；
- 材质上 `_MainTex` / `_Color` 都在，但贴图解码要用 UnityPy 的 `export`，
  它依赖新版 Pillow，当前插件里没打包 —— 只取主色。

## 6. 导入策略（和游戏对齐）

1. 子树里**每个 Transform 都建一根骨**，静止矩阵 = 游戏里的世界矩阵；
2. 网格顶点先乘「节点 → prefab 根」矩阵，再按 10 倍缩放进 Blender；
3. 蒙皮网格按骨骼槽位建顶点组；静态网格 100% 权重压给所属节点的骨；
4. 挂角色：Blender 骨骼父级 + `matrix_parent_inverse`，让
   `matrix_world == 骨矩阵`（游戏里道具根的局部变换就是单位）。

实测：拖动 `k_f_handR_00` 20 cm，道具世界位置同步 20.00 cm；
道具尺寸（dildo）0.085 × 0.104 × 0.210 m，和角色 1.75 m 的比例正确。

## 7. 还没搞定的

- 贴图（见上，需要新 Pillow）；
- 列 15「親スケールの適応」只在角色有缩放时才生效（现在角色都是 1.0，等于空转）；
- `h2h` / `h2s` 这两类「同一对角色换体位」的包还没逐一验证；
- Studio 角色动作**没有日文名**：游戏里的日文名在程序集/Studio 数据里，
  `abdata` 里只找得到英文资源名。现在用源 FBX 路径做分类 + 中文组名代替。

## 8. 给 UE 的导出约定

按物体导出（`ops.export_object`）踩过的坑，记下来免得再踩：

1. 道具挂在角色骨上时，**必须先断父级**，否则角色位移会被烘进根节点；
2. 只有骨骼曲线的物体才把变换归零（有物体级关键帧的不能动）；
3. `primary_bone_axis=Y / secondary_bone_axis=X` 要和导入 UE 的骨架**同一套**，
   否则骨骼局部轴对不上、动画会歪；
4. `axis_forward=-Z / axis_up=Y`（FBX 用 Y-up），UE 导入时自己转 Z-up；
5. `apply_scale_options=FBX_SCALE_ALL`、`global_scale=1.0`，1 Blender 米 = 1 FBX 单位。

回读验证：骨骼局部四元数误差 `0.00e+00`，位移 `0.0000 m`，
角色整体挪到 (3,-2,0.5) 后导出的文件字节数完全一致。
