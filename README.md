# HS2 AnimKit

把 **Honey Select 2** 的默认角色和动作搬进 Blender：素模（身体 + 头，男女都有）、
游戏骨架、1261 个 Studio 动作、239 段 **H 动作**（带日文名）、59 段 H 动作
**连道具一起装**，外加一层**不导出的全身 IK / FK 控制层**。
改完直接导出给游戏/引擎用的 FBX；也支持 **MMD 的 VMD** 和 **Mixamo FBX** 重定向。

不依赖 HS2MeshExporter，也不依赖任何第三方 Unity 工具链——插件内置解包器，
直接读游戏目录里的 assetbundle。

---

## 一、它做了什么

```
abdata/chara/oo_base.unity3d        →  骨架 602 骨 + 身体网格 9594 顶点
abdata/chara/00/fo_head_00.unity3d  →  头部骨骼 91 根 + 头部网格 4418 顶点
abdata/studio/anime/*.unity3d       →  1261 个动作（逐帧解成 Blender 关键帧）
```

骨骼的静止矩阵、网格的绑定矩阵都直接取自游戏原包，**数学上就是同一份数据**，
模型和骨骼天然对齐。

在游戏骨架上再补两层（都不导出）：

- **FK 控制层** `CTRL_*`：每个关节一个控制器，层级照骨架搭；变形骨用
  `Copy Transforms` 跟随，改动作就是拖控制器。
- **IK 机械层** `IKB_*` / `CAR_*`：手脚两段 IK 挂在几何正确的机械骨上，
  再用「搬运骨」把世界增量翻译回游戏骨。

导出时一刀切干净，回来的骨架还是游戏原来那副（693 骨，零残留）。

---

## 二、安装

**Blender 4.0+（4.2 / 4.5 / 5.2 都验证过）**

### 推荐：一条命令挂好（目录联接）

```
python tools\deploy.py            # 给所有找到的 Blender 版本建目录联接
python tools\deploy.py --status   # 看每个版本现在是什么状态
```

然后在 `编辑 → 偏好设置 → 插件` 里勾选 **HS2 Anim Kit**（每个版本勾一次）。

联接的意思是：Blender 的插件目录里只有一个**指向源码的入口**，改完代码点面板上的
「重载插件代码」就生效，不用重装、也不会产生第二份拷贝。

### 或者：装 zip

用 `HS2RigKit_addon.zip`（含 `_vendor`，约 1.9 MB）→ 首选项 → 插件 → 从磁盘安装。
两个 Python 版本的二进制都打包了（`_brotli` / `lz4` 有 cp311 和 cp313），
所以 Blender 4.x（Python 3.11）和 5.x（Python 3.13）都能用。

> **两种方式只选一种。** 已经有联接时再用 zip 装，Blender 会往那条联接里解压，
> 等于直接改你的源码目录 —— 想装 zip 就先用 `--status` 看清状态。

### 插件列表里出现一大堆 "HS2 Anim Kit" 怎么办

以前的 `deploy.py` 是「复制一份」而不是做联接，每次同步都把旧的改名成
`hs2_rig_kit.bak-<时间戳>`。Blender 会把 addons 下**每个**带 `bl_info` 的文件夹
都列出来，于是几十个备份全都变成了同名条目（而且版本号可能差好几个大版本，
很容易改错代码）。

```
python tools\deploy.py --clean    # 删掉所有 .bak-* 备份，安装本身不动
python tools\deploy.py            # 把剩下的实体拷贝换成联接
python tools\deploy.py --status   # 确认：每个版本一行「联接 → (3, 0, 0)」
```

现在已经改成：默认建联接、备份最多留 3 个、复制失败会自动还原备份。

装好之后 3D 视图侧栏出现 `HS2` 标签页，面板最下面一行会显示
`v4.1.0 · …/HS2AnimKit` —— 用它确认 Blender 跑的到底是哪一份代码。

---

## 三、工作流

```
动作   →  点一条 = 演员进场并开始演（第一次自动建索引约 30s，之后秒开）
          换一条 = 清场重装；H 里点阶段 = 只换阶段，演员不动
调整   →  加控制器（FK + 手脚 IK）→ 拖控制器改动作
          镜像 / 变速 / 贴地
导出   →  选中骨架 → 文件 → 导出 → FBX（勾「仅变形骨」）
```

### 面板结构

**动作驱动**：动作是主角，演员是配角。点一条动作 → 需要的人（和道具）自己进场；
换一条 → 清场重装。演员几个、什么性别，由动作本身决定，你不用管。

侧栏只有 **3 个面板 + 1 个命令框**：

```
动作                            ← 顶层面板
├─ [工作室动作] [H 动作]
├─ 工作室：[搜索框] [分类下拉]
│           ┌─────────────────────────────┐
│           │ FightA_cutin_00        女角 │  ← 点一行就演
│           │ chair_17_M             椅子 │
│           │ ...                   1019 个│
│           └─────────────────────────────┘
│           底部：1019 个 · 点一行就演   ↻（重建索引，一般不用点）
├─ H 动作：[人数 全部/单人/男女/百合/3P] [搜索] [道具]
│           模式列表（239 段，右边标「场景 · 道具×N · 阵容」）
│           阶段列表（待机 / 抽插·弱 / 绝顶…，点一行直接切，演员不动）
└─ 状态行：正在演 xxx · 66 帧 · 女

调整
├─ 当前骨架 + 帧数
├─ 控制器：IK 开关 + 四个逐肢体滑块 + 关节校正 + 重打帧 + 吸附/写回
│           [给所有演员加控制器]
├─ 一键处理：[左右镜像] [放回地面] [慢一倍] / [×0.5][×0.75][×1.5]
├─ [手势]                     ← 点了才展开
└─ 一行提示：导出走 Blender 自己的 FBX（勾「仅变形骨」）

外部动作                        ← 折叠，另一种用法
├─ 导入 VMD / Mixamo FBX（没演员会自动放一个）
└─ 手动放人：+ 女角 / + 男角

命令                            ← 折叠，给脚本 / Agent
```

导出不占面板：选中骨架 → `文件 → 导出 → FBX`，勾 **仅变形骨**（Only Deform Bones）
即可 —— 控制器骨都是非变形骨，导出时就滤掉了；动画带约束求值，不用先烘焙。

**为什么不要「先放角色、再选动作装到某个角色」**：那是把工作室的数据结构
（角色槽 / ActiveIK 分组）直接摊给用户了。实际用起来就是「我想看这条动作」，
中间多一步「先放人、记住谁是谁、动作装给谁」纯属负担，还会出现「装错人」。
现在这些人（女1/女2/男1/男2）只在内部用来对齐数据，界面里不出现。

**不需要先「扫描动作库」**：第一次打开「动作」页签时插件会自己建索引
（约 30 秒，带进度条，之后读缓存秒开）。底部那个 ↻ 只是给「加了新动作包」时
手动重建用的。

**为什么不再用下拉框 / 滑条**：1261 个动作塞进下拉框，点开就是一个巨型列表，
滑条又只能盲拖。现在是真列表 —— 可滚、可搜、分类可见、**点一行就直接装上**
（右下角可以关掉「点一下就装」）。

**关节补偿是勾选框**（在「调整」里），默认关闭——
导入游戏动作时保持和游戏完全一致；需要改关节、让膝盖/手肘内侧褶皱跟着动时再打开。

### 控制器一览

| 控制器 | 对应工作室 | 作用 |
|---|---|---|
| `CTRL_root` | ルート | 整体位移/旋转（地面箭头） |
| `STU_Body` | Body effector | 骨盆位置（CTRL_center 的父级） |
| `CTRL_center` | センター | 骨盆旋转（センター） |
| `CTRL_lower` / `CTRL_upper` / `CTRL_spine2` / `CTRL_chest` | FK Body | 下半身 / 上半身 / 上半身2 / 胸 |
| `CTRL_neck` / `CTRL_head` | FK Neck | 脖子 / 头 |
| `CTRL_shoulder` / `arm` / `fore` / `hand` _L/R | FK Arm | 肩 / 大臂 / 小臂 / 手 |
| `CTRL_thigh` / `calf` / `foot` _L/R | FK Leg | 大腿 / 小腿 / 脚 |
| `STU_Shoulder_L/R` | Shoulder effector | 上臂**位置**（机械臂链 + 上臂骨跟随） |
| `STU_ArmChain_L/R` | ArmChain bend goal | 肘部极向 |
| `STU_Hand_L/R` | Hand effector | 手 IK 目标（位置 + 旋转） |
| `STU_Thigh_L/R` | Thigh effector | 大腿**位置**（机械腿链 + 大腿骨跟随） |
| `STU_LegChain_L/R` | LegChain bend goal | 膝盖极向 |
| `STU_Foot_L/R` | Foot effector | 脚 IK 目标（位置 + 旋转） |
| `STU_Toe_L/R` | 足先 | 脚趾 |
| `CTRL_f_*`（可选） | FK Hands | 手指 FK，每指 3 节，默认不生成 |

- 每个手脚控制器上有 **`ik` 属性（0~1）**：0 = 纯 FK，1 = IK。
- 「Studio 分组（ActiveIK）」：Body / 臂 L/R / 腿 L/R 五组，和工作室一致。
- **关节补偿**：打开后 `cf_J_LegKnee_dam`、`cf_J_ArmElbo_dam_01`、`SiriDam`、
  `LegUpDam` 等 dam 骨随主骨转。用「rest 与 dam 相同的 helper + COPY_ROTATION
  influence」实现，逐帧对拍 8 个游戏动作、左右两侧：
  膝盖内侧 ~0.7~1.6°、膝后 ~1.3~3.2°、肘内侧 ~0~1.3°；
  `SiriDam`/`LegUpDam` 关系更复杂，仍有 1.5~28° 误差。
  **VMD 导入时默认打开**（VMD 没有 dam 曲线，导入对话框可关）；导入游戏动作时
  默认保持原样，因为这些骨本来就有烘焙曲线。

  另：工作室里的「关节校正 / Joint Correction」来自 **HS2PE** 插件，反编译后
  只有三组：`Crotch`（SiriDam 0.4 + Kosi02_s 0.25×左右大腿平均）、
  `Left Ankle`、`Right Ankle`（Foot02 = 0.9×Foot01 的 X 角）。本插件已实现
  Crotch 两组；Ankles 需要读 Foot01 的原始 localRotation，Blender 里它由 IK
  约束驱动，直接复制会把脚趾带偏，暂未启用。膝/肘 dam 不在 HS2PE 里，
  是游戏动作里烘焙好的。
- 所有控制器逐帧打在动作上，所以它们**贴在角色身上**跟着跑，不会浮在半空。
- 和真正 FBBIK 的差距：工作室的 Body/Shoulder/Thigh 效应器会通过脊柱
  映射去够目标；这里是「位置效应器 + 父级偏移」的近似，不做整条脊柱反解。

### 外部动作

面板 → 动作 → 外部动作：

- **VMD**：MMD 舞蹈直接导。**默认走「虚拟 MMD 骨架」**（复刻 MMDD 插件：
  在 HS2 骨上搭一棵虚拟 MMD 骨架，VMD 驱动它，再按 `chara_*.ini` 的
  BoneWeight 把世界变换同步回 HS2）。MMD 的层级、IK、扭转骨、腰キャンセル
  全部保留。面板里可选 **24 套角色配置**（`chara_*.ini`）；关掉
  「用虚拟 MMD 骨架」就退回轻量的「直接映射」。
  单位换算 **0.0832 米/MMD 单位**（MMDD 的 AxisScale 0.832 × HS2 缩放 0.1），
  不是旧版约等于的 0.08。
- **VMD 采样已按 MMDD 原样实现**：位置 X/Y/Z 三条贝塞尔 + 旋转曲线，
  VMD 的 IK/可见帧、足 IK 的 `IK_ROOT` 位置、`basePosition`、`positionScale`、
  `BoneRotAxisAdjust` 的 r/a/s 三种模式、`autoBoneAxisAdjust` 派生轴倾、
  `BoneTwistDisperse`、`腰キャンセル`、`D 骨 track` 全部按 `vmdlib.py` 的顺序执行。
- **Mixamo FBX**：带皮肤的 Mixamo 动作，按 `mixamorig:*` 名字自动映射，
  按肢体方向重定向，体型差按身高比缩放；导入时可自动做足部 IK、髋部贴地
  和膝/肘 dam 校正。Mixamo 的 Foot/ToeBase 分别驱动 HS2 的
  `Foot01`/`Toes01`，`Foot02` 只作为踝下校正骨跟随。
- VMD 不做贴地（MMDD 没有这个逻辑，VMD 里脚在哪儿就是哪儿）。

### 工作室手势（21 种）

主面板「手势（工作室 21 种）」：

- 左手/右手各一个下拉框，**选中即预览**（不写关键帧）；
- 选好后点「写入关键帧」（一次写左右两只手当前的选择），
  是否写当前帧由下方开关决定；
- 「还原」清掉手指旋转并恢复手指上的约束；
- 游戏数据里手势没有名字，所以下拉框是「姿势 01」…「姿势 21」。

数据直接来自 `abdata/list/customshape.unity3d` 的 TextAsset
`cf_anmShapeHand`。它存的是 **21 帧 × 30 根手指骨的 localRotation 欧拉角**，
不是 blend shape；插件只写骨骼旋转，不碰任何变形目标。

VMD 虚拟骨架/控制器会在手指上挂 `MMDD_ROT_*`、`HS2_FK_*` 约束；手势应用时
会临时静音这些约束（否则写进去的旋转会被约束盖掉），换动作/重建控制器或
「还原」时自动恢复。

---

## 四、H 动作与 H 道具（v2 新增）

H 动画不在 `studio/anime` 里，游戏把它按「场景 × 类型 × 番号」拆开了：

```
abdata/animator/h/female/<pack>/<type>.unity3d    女角动作
abdata/animator/h/male/<pack>/<type>.unity3d      男角动作
abdata/animator/h/h_item/<pack>/<type>.unity3d    道具自己的动作
abdata/h/h_item/<pack>.unity3d                    道具 prefab（含骨骼/网格）
```

**难点在名字**：动作包里的 clip 全叫 `S_Idle` / `L_WLoop1`，1070 个 clip 只有
二十来个不同的名字 —— 单看名字根本分不清是哪一段 H 动画。

真正的对应关系藏在 `abdata/list/h/{animationinfo,hitemobj}/<scene>.unity3d` 里。
里面塞着两种**同名但不同表头**的表（都叫 `<type>_<pack>`，列数从 28 到 49 都有），
所以插件是**按表头认出是哪一种**，再按表头名取列（不写死列号）：

**① 主体位表（41/42/48/49 列）—— H 动作清单**

| 列 | 含义 | 例 |
|---|---|---|
| 0 / 1 | 日文名 / 番号 | 正常位 / 0 |
| 2-7 | ［男］包 / base / asset / **アニメーション名** | `ais_m_00` |
| 8-13 | ［女］同上（百合表是［女攻め］，另有一段［女］） | `ais_f_00` |
| 14-19 | ［二人目］同上（1男2女 / 2男1女） | `h2_mf2_f2_06` |
| 之后 | 行為系 / 操作系 / **場所** / アイテムがいるか / 系統 … | `0,13,14` / `1` |

每行一个体位，**男女各自的 Animator 名直接写在表里**（`ail_f1_00` → 女1，
`h2_m2f_m2_00` → 男2），不用再猜编号。这也是「动画缺失」的修复点。

**② 道具表（28/31/36/37 列）—— 一行一件道具 + 最多 5 组挂点**

| 列 | 含义 | 例 |
|---|---|---|
| 0 / 1 | 日文名 / 番号 | 拘束フェラ / 17 |
| 4 / 5 / 6 | manifest / 道具包 / prefab | `abdata` / `h/h_item/01.unity3d` / `p_item_girotinkousoku_kotei` |
| 9 / 10 | 道具动画包 / animator | `…/h_item/01/houshi.unity3d` / `aih_i_17_…` |
| 每组 5 列 | 直接親子付け / **誰に親子付** / 親子付先 / くっつけるもの / スケール適応 | `0` / `1` / `k_f_armlowL_00` / `` / `0` |

> **`誰に親子付`：0 = 男、1 = 女**（4 = 场景里的固定物）。
> 老版本写成 0=女 1=男 是反的 —— 男角手里该拿的东西会跑到女角手上，
> 而女角身上那件（比如项圈、アナルバイブ）又会去找男角。证据见
> `docs/HS2H动画与道具.md` §3.1（男拘束フェラ的绳子是 0、`k_f_ana_00` 那行是 1、
> ワンコバック 的牵引绳把手是 0 而项圈是 1）。
>
> 一件道具的那 5 组挂点不是二选一，是一串挂载：先挂到 `cf_J_Root`，再把内部
> 某个节点对准另一根骨。Blender 里一件只能挂一处，所以挑「有くっつけるもの」
> 的那组当主挂点（`hdb.pick_anchor`），实测落点误差 0.00000 m。

**日文名 ↔ 控制器 ↔ clip ↔ 道具 ↔ 挂哪个人的哪根骨** 全都能对上。

插件扫两遍（约 30 秒，之后读 `~/.hs2_animkit/catalog_v9.json`，0.02 秒）：

- `animator/h/**` 里的控制器 + clip；
- `list/h/**` 里的表和日文名。

面板「H 动作」里现在是 **239 段可用**：单人 18、男女 188、百合 10、
1男2女 16、2男1女 7，其中 59 段带道具（合计 88 件，27 件有自己的动画）。

清单直接从游戏的**主体位表**读（`list/h/animationinfo` 里 41/42/48/49 列那种），
每行一个体位、男女各自的 Animator 控制器名都写在表里；同名的老版本只能看到
151 段，是因为只读了「道具表」，没带道具的体位全丢了。
排序是**类型 → 场景 → 番号**（场景就是表里「場所」那组编号：壁 / 机 / 沙发 /
楼梯 / 浴缸 / 淋浴 / 木马…），挑阶段（待机 / 抽插·弱 / 绝顶…）即可。

### 人数和性别是自动的（内部叫「角色槽」，界面里看不到）

H 动作的「几个人、什么性别」是从控制器名字里读出来的，不用猜：

| 控制器名 | 读出来的东西 |
|---|---|
| `aih_f_17` / `aih_m_17` | 女1 / 男1（普通男女） |
| `ail_f1_00` / `ail_f2_00` | 女1 + 女2（百合） |
| `ai3p_f1_00` / `ai3p_m_00` | 女1 + 男1（3P 的一角） |
| `h2_m2f_m1_00` / `h2_m2f_m2_00` | 男1 + 男2（2男1女） |
| `aih_i_17_girotinkousoku` | 道具 |

所以点一条 H 动作时，插件就按这个表把人放好：单人 1 个女角、男女 1+1、
百合 2 女、3P 3 个 —— 你什么都不用选。多人动作换阶段时，所有人（和道具）
会一起切，不会出现「一个人动了另一个人没动」。

（内部还是用「女1/女2/男1/男2」这组槽位来对齐数据，但它们只出现在代码里。）

### 阶段联动

同一段 H 动作的两个（或三个）角色，阶段要一起换才不乱。

面板上的「阶段联动」按钮**只在阵容正确时才解锁** —— 判据就是
`api.sync_state()`：

- 所有角色装的必须是**同一段**动作；
- 需要的槽都得在（漏一个就提示「还差女2」）；
- 每个槽的性别要对（百合 = 女1+女2；3P 的 `m2f` = 女1+男1+男2）。

解锁后点一下，所有角色 + 它们的道具一起切到下拉框里选的阶段
（`api.sync_stage("S_WLoop")`）。没解锁也能单独给某个角色换阶段，
灵活优先。

### 体型：身高 / 胸围

H 动作 clip 名里的 `S / M / L` **不是强中弱，是身高三档**（矮/中/高）；胸部动作
的后缀 `BreastS/SM/M/ML/L` 是**胸围 5 档**。游戏里靠身高 shape 值路由：
`SetHeightParameter` 把身高 shape（0~1）塞进 `height` 混合树，再经捏人对表
`cf_anmShapeBody` 映射成 `cf_N_height` 骨骼的缩放（0.8 / 0.9 / 1.0）。

「动作」面板的阶段列表下方是一个**动态下拉**——只有当前阶段真有身高/胸围变体时
才出现，点选离散档位（没有滑条，滑了没效果的阶段根本不显示）：

- **身高**（矮/中/高）：选哪档就播哪档动画，并同步把 `cf_N_height` 骨骼缩到
  `0.8 / 0.9 / 1.0`，让身体和那档动画的手/胯接触点对上；
- **胸围**（最小/小/中/大/最大）：只切动画档，不捏身体（身体变形要解析捏人对表，
  比较复杂，暂不做）。

道具跟角色走同一档；每件道具的动画是独立控制器（纯挂绳没有动画），插件按数据表
里的 `animator` 名逐件匹配。详见 `docs/HS2H动画与道具.md` §2.5。

### 道具是怎么装进来的

道具包里的每个 prefab 都是一套**自带小骨架**的独立物件：

```
p_item_dildo
  ├ N_dildo_Angle → J_dildo_root → J_dildo_00 … 06     ← 骨骼链
  ├ O_dildo_00      ← SkinnedMeshRenderer（8 骨蒙皮）
  └ O_dildo_00_d
```

插件把子树里**所有 Transform 都建成一根骨**（骨架名 `PROP_<prefab>`），
网格顶点先换算到 prefab 根空间，再按权重绑上去 —— 蒙皮网格用游戏里的骨骼槽位，
静态网格（MeshFilter）压 100% 到所属节点。于是两类网格用同一套逻辑跟着动作走。

挂到角色身上按数据表来：`parent_who` 决定女角还是男角，`parent_bone` 是骨名。
用的是 Blender 的骨骼父级 + 精确的 `matrix_parent_inverse`，道具的局部变换仍然
是单位矩阵 —— **和游戏里的父子关系一模一样**（实测：拖手骨 20 cm，道具跟着走
20.00 cm，位置严格等于骨矩阵）。

道具动作走的是**道具自己的路径表**（`N_dildo_Angle/J_dildo_root/…`），
不能拿角色的骨架路径去解 —— 这是 v2 修掉的一个坑。

限制：贴图没解（UnityPy 的 export 依赖新版 Pillow，插件里不打包），
道具用材质的主色渲染。绳/铐/玩具这类本来就近纯色，素模够看。

---

## 五、给 Agent / 小白用的一句话接口

面板只是壳，所有能力都在 `hs2_rig_kit/api.py` 里，一两行就能干活：

```python
import hs2_rig_kit.api as hs2

hs2.character()                     # 手动放一个女角（一般用不着，play 会自己放）

hs2.play("chair_17_M")              # 播一条工作室动作：演员自动进场
hs2.play_h("正常位", "M_WLoop1")     # 播一段 H：人数/性别/道具全自动
hs2.set_stage("S_WLoop")            # 只换阶段，演员不动
hs2.clear_cast()                    # 清场（换动作时自动做）
hs2.load_prop("p_item_dildo", bone="k_f_handR_00")   # 单独塞个道具
hs2.export_object(hs2.active_character(), r"D:/f1.fbx")  # 单件导出

hs2.mirror(); hs2.retime(2.0); hs2.ground()
hs2.export_fbx(r"D:/out.fbx")
```

镜像/变速/贴地都是「逐帧求值 → 改 → 重写 fcurve」，
所以对任何 Action 都成立（包括 VMD、Mixamo、别人给的 FBX），
不依赖 fcurve 的通道结构，Blender 5 的分层 Action 也照样能用。

- `ground()` —— 只挪 root 骨的位置，姿态一点不改，挪完还能继续 K；
- `retime(2.0)` —— 慢一倍（帧数翻倍），做慢镜/加细节用。

### 文本命令（Agent 首选）

面板「命令」里一句话一条，或者 `hs2.cmd("...")`：

```
character [male]          加载角色
scan                      重新扫描动作库并缓存
list [关键词] [--h]       列出动作（--h 只列 H）
cats                      分类统计
hlist [关键词]            列出 H 模式
stages <模式>             列出某段 H 动作的阶段
load <名字>               装 Studio 动作
h <模式> [阶段]           装 H 动作（女角 + 道具）
prop <prefab> [骨名] [pack]
mirror / speed / ground / rig / info / export <路径>
```

之所以给 Agent 留这条路：Agent 不需要知道 Blender 的 operator、上下文、
面板在哪 —— 一次调用就能拿到可读结果，失败了也是中文原因。

---

## 六、动作库的分类从哪来（不用猜）

Studio 动作名是 `chair_17_M` 这种，看不出「这是椅子上的坐姿」。好在每个动作包的
AnimatorController 里有一张字符串表（`m_TOS`），存着**源 FBX 的原始路径**：

```
Assets/Studio/CreateData/motion/00/female/map_action/chair/chair_17_M.fbx
```

于是分类直接用游戏自己的目录结构：`female / map_action/chair`、`bondage`、
`h / tokushu` ……「动作库」面板多了一个「分类」下拉，配合过滤框用，
1261 个动作就变成几十个一目了然的小组（名字还带中文：椅子 / 座姿 / 拘束 / 站姿…）。

没有源路径的少量动作，退化成按名字前缀归类（`swim_Idle_00_L` → swim）。
Studio 里内嵌的 H 动作（`S_Idle` / `L_WLoop1`）单独归到「H」组。

---

## 七、为什么 IK 要单独一层「机械骨」

HS2 是**纯枢轴骨架**：所有骨静止时局部旋转都是单位四元数，tail 全部朝上。
Blender 的 IK 按骨长 + Y 轴算几何，直接挂上去会把腿看成一条朝后的直线，
实测踝关节偏 **0.35 米**。

改 tail 能修好 IK，但会改变静止朝向，导出的局部旋转就和游戏对不上。所以：

```
IKB_thigh_L   髋 → 膝        ← 形状和真实关节一致，IK 挂这条链
IKB_calf_L    膝 → 踝
CAR_thigh_L   挂在 IKB_thigh_L 下，静止朝向 = 游戏骨
CAR_calf_L    同上
```

游戏骨用 Copy Rotation（世界）跟随 CAR 骨——CAR 的静止朝向和游戏骨一致，
它的世界旋转正好是「机械骨相对自身的增量」，游戏骨拿到就是纯增量。
实测 71 帧动作，IK 开着和纯 FK 的局部矩阵差 **0.00000**；拖脚 10 cm，
脚踝走 **10.000 cm**。

---

## 八、头部

`fo_head_00.unity3d` 里有 `p_cf_head_00` / `p_cf_head_01` 两套头型，
外加眼睛、牙齿、睫毛、泪液。只取 **`o_head`（皮肤，4418 顶点）**。

头部骨骼模板在 `oo_base` 的 `p_cf_head_bone`（92 节点，43 根被网格引用），
游戏里挂在 `cf_J_Head` 下。坑：头部网格的 `m_BindPose` 是相对**头 prefab 根**的，
顶点要先乘 `cf_J_Head` 的世界矩阵，否则头会飘 1.6 米。

---

## 九、导出的 FBX

- 骨骼：693 根变形骨（602 游戏骨 + 91 面部骨），控制骨一根都不带
- 动画：逐帧采样，30fps，不简化
- 轴向：`primary = Y / secondary = X`，和 Unity 一致
- 网格：默认不带（需要时勾「带蒙皮网格」）

**关于「通用」**：骨架的静止姿势（位置 + 单位旋转）和游戏**完全一致**，
导出的局部旋转可以按骨骼名直接套回 HS2 骨架。

---


## 十、给 UE 用：按物体导出独立 FBX

场景里每件东西（道具、角色）都能单独导一个 FBX，**坐标和旋转不会带走别人的**。

用法：3D 视图里选中物体 → 输出面板「导出选中物品（每个一件）」，
或者 `hs2.cmd("exportobj D:/out")`（选中物体后执行）。

四个坑都处理掉了：

1. **父级运动不漏进去**。道具是挂在角色骨头上的，直接导出会把角色的位移/
   旋转烘进根节点，进 UE 就成「物体自己也在动」的双份动画。导出前临时断开
   父级、把物体变换归零，导完还原（角色挪到天边再导，先证过文件字节完全一致）。
2. **物体级动画才保留**。只有纯骨骼动画时才归零；如果这个物体自己有关键帧
   （`location` 之类），就不动它的变换，原样导出。
3. **轴向一致**。默认 `UE / Unity（Y-up）`：FBX 里 Y 朝上，UE 导入时自己转成
   Z-up，正着站着；骨骼轴仍是 `primary Y / secondary X`，和角色骨架、和导入
   UE 的骨架完全同一套，动画能直接对上。
4. **单位**。1 Blender 米 = 1 FBX 单位，UE 默认导入缩放就是 175 cm 的人、
   20 cm 的道具，不用改。

回读验证（`tools/export_check.py`）：导出的 FBX 再导回 Blender，
每根骨的局部四元数和位移**误差 0.00e+00**，根节点在原点、不带物体级曲线。

> 道具的网格和骨架是一起导出的；如果 UE 那边已经有这套骨架，导入时选
> 「导入动画到一个已存在的骨架」即可，曲线按骨骼名对上。

---

## 十一、目录结构

```
hs2_rig_kit/
├── ops.py         操作符 + build_default（加载角色 / 装动作 / 烘焙 / 导出）
├── ops2.py        新面板的操作符（H 动作 / 道具 / 后处理 / 命令）
├── api.py         **一句话接口**：load / h_scene / load_prop / cmd …
├── animlib.py     动作剪辑内核（采样 → 镜像 / 变速 / 贴地）
├── props.py       H 道具 → Blender（建骨 / 蒙皮 / 挂骨 / 道具动画）
├── ui.py          侧栏（角色 / 动作 / 调整 / 输出 / 命令）
├── bdata.py       骨架 + 网格 → Blender
├── anim.py        动作 → Action
├── rig.py         控制层（FK 控制 / 机械骨 IK / 关节补偿 / 打帧）
├── widgets.py     控制器造型
├── vmd.py         VMD 解析 + MMD → HS2 骨骼映射
├── mmdcfg.py      MMDD chara_*.ini 配置解析（虚拟 MMD 骨架用）
├── mmd_configs/   24 套 MMDD chara_*.ini 角色配置（随插件打包）
├── mmdrig.py      虚拟 MMD 骨架（建骨 / IK / 扭转 / 同步回 HS2）
├── retarget.py    Mixamo 重定向
├── compat.py      Blender 4.x legacy / 5.x layered action 兼容层
└── ripper/        纯 Python 解包
    ├── unityfs.py   assetbundle 读取
    ├── catalog.py   统一目录：Studio + H，带分类（源 FBX 路径）
    ├── hdb.py       H 动作表：日文名 / 番号 / 道具 / 挂点
    ├── prop.py      道具 prefab 解包（骨骼 / 网格 / 材质）
    ├── clips.py     动作解码（streamed / dense / constant）
    ├── skeleton.py  mesh.py  head.py  hashes.py  mathutil.py
```

`~/.hs2_animkit/` 里是解包缓存（骨架 / 网格 / 头部，约 2 MB），删掉会自动重建。

---

## 十二、自检

```bash
blender -b --factory-startup -P tools/selftest.py
```

加载 → 导入 → 生成控制器 → 精度检查 → 拖脚 → 换动作 → 吸附 → 烘焙 → 导出 → 回读。
全过打印 `全部通过`。

**VMD 与 MMDD 的逐帧对拍（重要）**：

```bash
blender -b --factory-startup -P tools/mmdd_parity.py
```

它用 `tools/mmdd_ref.py`（照 `vmdlib.py` 重写的独立参考实现）逐骨逐帧对比：
虚拟骨 local/world 旋转、world 位置、同步后的 HS2 骨 world 旋转。
可用环境变量换配置 / 帧率 / VMD：

```powershell
$env:HS2_PARITY_CONFIG="...\chara_default_1.4.ini"
$env:HS2_PARITY_FPS="60"
$env:HS2_PARITY_VMD="D:\...\other.vmd"
```

当前结果（小鹿乱撞.vmd，2020 帧 / 2020 采样）：

```
mean local 0.009°  world 0.006°  pos 0.00000 m
worst local 0.046°  worst world 0.063°  worst pos 0.00003 m
worst HS2  0.050°
```

同一份 VMD 在 **60 fps**（4039 帧）和 **chara_default_1.4.ini** 下也是
同样量级（mean 0.009°，worst 0.046~0.063°）。

**手势数据检查**：

```bash
blender -b --factory-startup -P tools/handpose_check.py
```

确认 21 个手势、每个 30 根手指骨、不同手势确实不同、应用后指尖有位移。

**关节补偿对拍**：

```bash
blender -b --factory-startup -P tools/jointfix_check.py
```

8 个游戏动作 × 左右两侧 × 6 根 dam 骨，逐帧误差 ≤ 0.07°。

**Studio 控制器对拍**：

```bash
blender -b --factory-startup -P tools/studio_check.py
```

检查 13 个目标齐全、Body/Shoulder/Thigh/Hand/Foot 移动 10 cm 时对应骨/整条链
真的动、移动大腿时脚 IK 仍钉住、5 个 ActiveIK 分组开关和 Body 偏移保存/恢复。

渲染检查：

```bash
blender -b --factory-startup -P tools/render_check.py -- 30 anim.png
```

**v2 新增的自检**：

```bash
# API 全流程：加载 → 搜索 → 装 Studio / H 动作 → 镜像 / 变速 / 贴地
# → 命令 → 导出
blender -b --factory-startup -P tools/api_check.py

# 界面：面板离屏绘制、列表填充、点一行就装、操作符、两个旧 bug 回归
blender -b --factory-startup -P tools/panel_check.py

# H 场景：女角 + 男角 + 一堆道具，检查每个道具都精确落在挂点上
blender -b --factory-startup -P tools/hscene_check.py

# 单个道具：尺寸、挂手、道具动画、跟随
blender -b --factory-startup -P tools/prop_check.py

# 角色槽 / 阶段联动：百合、3P、单人、性别不符时不解锁
blender -b --factory-startup -P tools/sync_check.py

# 体型：S/M/L 拆分、身高/胸围分组、混合插值、端到端 height 改姿态
blender -b --factory-startup -P tools/body_check.py

# 按物体导出 FBX：对齐节点、根节点归零、回读逐骨比对、父级运动不漏
blender -b --factory-startup -P tools/export_check.py

# 出一张图看效果（会自动按动作人数补角色）
blender -b --factory-startup -P tools/hrender.py -- "W駅弁"
```

---

## 十三、已知限制

- **表情没做**：面部骨已完整带入（`HS2 面部` 集合），VMD morph 还没映射。
- **裙摆/头发/乳摇**：物理骨不含在动作里，导出后是刚性的。
- **关节补偿是逐帧拟合**：膝盖内侧、膝后、肘内侧已到 0~3°，臀/腿根
  （`SiriDam`/`LegUpDam`）在极端动作仍可能到 5~28°。**手部 dam 骨**
  （`Hand_dam` / `Hand_Wrist_dam`）没有找到同形式的简单关系，暂不驱动。
- **HS2PE Ankles 未启用**：官方公式是 Foot02 = 0.9×Foot01 的 X 角；
  Blender 里 Foot01 由 IK 约束驱动，直接 COPY_ROTATION 会把静止脚趾带偏
  3.6 cm，需要等「求值后 X 角」的驱动/烘焙方案。
- **Studio 控制器不是完整的 FBBIK**：13 个目标、5 个分组、位置效应器
  都做了；但没有复刻 FBBIK 的脊柱映射和全身迭代求解，Shoulder/Thigh
  只是把对应骨/机械链整体平移，不会弯脊柱去够。
- **VMD 足 IK 已按 MMDD 的解析解重写**（`MMDIKSolverLeg` + `MMDIKSolverNoLimit`），
  不再用 Blender IK 约束，极端姿势下与 MMDD 对拍误差 < 0.1°。
- **过场/道具动作**：扫描时按骨骼绑定匹配率过滤，`sao_fishing_*`、`open` 这类
  道具动作会直接从列表里滤掉（扫描时按骨骼绑定匹配率判断）。
- **只支持标准骨**：男女共用同一副骨架（`oo_base`），骨骼长度仍取 HS2 的；
  `chara_*.ini` 只影响 VMD 重建虚拟骨架。
- **Mixamo 路径没有参与这次的 MMDD 对拍**：它走的是另一套世界旋转增量映射。
- **道具贴图没解**：UnityPy 的 `export` 包依赖新版 Pillow（`PIL.Image.Transform`），
  插件里打包的 Pillow 版本不够，所以道具用材质主色 + 素模。要贴图的话
  换个新 Pillow 就能自动生效（`ripper/prop.py` 里已经写好了路径）。
- **道具的「くっつけるもの（自己侧节点）」已按 `prop_world = bone_world @ L⁻¹` 对齐**，
  少数要挂到别的道具节点上的（淋浴绳、拘束线）会先在「角色 + 已装道具」里找骨，
  再让内部节点精确落上去（实测误差 0.00000 m）。
- **H 动作的男角**：部分模式（比如 `拘束玩具攻め`）数据里没有男角控制器，
  这时就算加载了男角也会是 T-pose —— 那是游戏本来就没有这段动作。
- **阶段联动是按「体型分组后的阶段」对齐的**：男女/道具三段各自是独立 clip，
  联动时按同名阶段一起换，道具侧阶段比角色粗（角色 `WLoop1/2`、道具 `WLoop`），
  会先去掉数字后缀做模糊匹配；某一边真没有这个阶段就保持不动（不会报错）。
- **多人场景的「谁抱谁」靠数据表**：3P/百合的槽位（`f1/f2/m1/m2`）是从控制器
  名字推出来的，个别包名不规范时要靠表里附带的 animator 名反查（已经做了）。
  真出现姿态错位，先看面板上「阵容」那行对不对。

---

## 十四、开源许可 / License

本项目整体按 **GPL-3.0** 发布（见 [`LICENSE`](LICENSE)）。

原因：插件把 **UnityPy**（GPL-3.0）原样打包在 `hs2_rig_kit/_vendor` 以便离线读取
assetbundle，GPL-3.0 会传染，所以本项目对外也按 GPL-3.0。其余打包的第三方组件及
各自许可证见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

> 本仓库**不包含任何游戏素材**：`cf_anmShapeHand`、`oo_base.unity3d`、Studio 动作、
> VMD/FBX 等都属于对应游戏的版权/第三方素材，使用插件时需要你在本地游戏目录里读取。
> 插件的功能是把**你自己合法拥有的游戏数据**搬进 Blender，不会一并分发它们。

从源码构建发布 zip：

```powershell
python tools\deploy.py 5.2
# 构建 HS2RigKit_addon.zip（含 _vendor，约 1.8 MB）
python 5.2的python.exe tools\build_zip.py
```
