# HS2 AnimKit

把 **Honey Select 2** 的默认角色和动作搬进 Blender：素模（身体 + 头）、游戏骨架、
1000+ 个 Studio 动作，外加一层**不导出的全身 IK / FK 控制层**。
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

**Blender 4.0+（4.5 / 5.2 LTS 都验证过）**

### 方式 A：目录联接（推荐，改代码即时生效）

把源码目录直接挂进 Blender 的插件目录：

```powershell
$src  = "D:\game\HS2\HS2AnimKit\hs2_rig_kit"
$base = "$env:APPDATA\Blender Foundation\Blender\5.2\scripts\addons"
New-Item -ItemType Directory -Path $base -Force
New-Item -ItemType Junction -Path "$base\hs2_rig_kit" -Value $src
```

然后 `编辑 → 偏好设置 → 插件` 里勾选 **HS2 Anim Kit**。
这样不用每次改完重新装 zip；改了源码点面板顶部的刷新按钮即可重载。

### 方式 B：装 zip

1. 用现成的 `HS2RigKit_addon.zip`（v1.0.0，已含 `_vendor`，约 1.8 MB）
2. 编辑 → 偏好设置 → 插件 → 从磁盘安装 → 选 zip → 勾选 "HS2 Anim Kit"

> 两个 Python 版本的二进制都打包了（`_brotli` / `lz4` 同时有 cp311 和 cp313），
> 所以 Blender 4.x（Python 3.11）和 5.x（Python 3.13）都能用。

3. 3D 视图侧栏出现 `HS2` 标签页。面板顶部会显示
   `v2.1 · …/HS2AnimKit` —— 用它确认 Blender 跑的到底是哪一份代码。

---

## 三、工作流

```
加载默认角色   →  素模（身体 + 头）+ 游戏骨架        ≈ 0.2s（有缓存）
扫描动作库     →  第一次 20s，之后读缓存
   下拉框/滑条/◀▶ 选动作 → 停手 0.35s 自动加载，控制器同步跟随
   （不想自动可以关掉「切换后自动加载」）
生成控制器     →  全身 FK + 手脚 IK + 关节补偿
   拖控制器改动作（脚踩地、手放哪、腰怎么扭）
烘焙为纯 FK    →  把 IK/约束结果写进骨骼关键帧
导出 FBX       →  只带变形骨，动画曲线直接可用
```

### 面板结构

```
HS2 动画工具                     ← 主面板
├─ 游戏目录  [放大镜自动找]
├─ 加载默认角色
├─ 状态：骨架 693 骨 · 身体 9594 / 头部 4418
└─ IK：开 / 关  [开][关]          ← 一眼能看到，随时切

动作库                          ← 子面板
├─ [🔍 下拉选择 / 打字搜索]
├─ ◀  swim_Idle_00_L  ▶
├─ [========●=========] 637 / 1261
├─ [重新导入]
└─ 外部动作：VMD / Mixamo FBX

控制器
├─ 生成控制器 / 重建控制器
├─ IK 总开关 [开][关] + 四个逐肢体滑块
├─ ☑ 关节补偿（dam 骨随膝盖/手肘转）
├─ 吸附当前姿势 / 写回 FK / 重新跟随
└─ 显示：控制 / IK / 变形 / 面部 / 其它 / 手指  集合勾选

输出
├─ 烘焙为纯 FK
├─ 导出 FBX
└─ 批量导出
```

**滑条是 0~1 的进度值**，按当前过滤结果按比例映射到动作列表：
拖满整条 = 走完全部动作，不会「动一点就跳到最后一个」。
精细选择用 ◀ ▶ 单步，或直接在过滤框里搜名字。

**关节补偿是勾选框**（在「控制器」面板里），默认关闭——
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
  用「世界旋转增量」搬运，体型差按身高比缩放。
- 不再做贴地（MMDD 没有这个逻辑，VMD 里脚在哪儿就是哪儿）。

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

## 四、为什么 IK 要单独一层「机械骨」

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

## 五、头部

`fo_head_00.unity3d` 里有 `p_cf_head_00` / `p_cf_head_01` 两套头型，
外加眼睛、牙齿、睫毛、泪液。只取 **`o_head`（皮肤，4418 顶点）**。

头部骨骼模板在 `oo_base` 的 `p_cf_head_bone`（92 节点，43 根被网格引用），
游戏里挂在 `cf_J_Head` 下。坑：头部网格的 `m_BindPose` 是相对**头 prefab 根**的，
顶点要先乘 `cf_J_Head` 的世界矩阵，否则头会飘 1.6 米。

---

## 六、导出的 FBX

- 骨骼：693 根变形骨（602 游戏骨 + 91 面部骨），控制骨一根都不带
- 动画：逐帧采样，30fps，不简化
- 轴向：`primary = Y / secondary = X`，和 Unity 一致
- 网格：默认不带（需要时勾「带蒙皮网格」）

**关于「通用」**：骨架的静止姿势（位置 + 单位旋转）和游戏**完全一致**，
导出的局部旋转可以按骨骼名直接套回 HS2 骨架。

---

## 七、目录结构

```
hs2_rig_kit/
├── ops.py         19 个操作符（含 VMD / Mixamo / 批量导出）
├── ui.py          侧栏（主面板 + 动作 / 控制器 / 输出）
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
└── ripper/        纯 Python 解包（unityfs / skeleton / head / mesh / clips / hashes / mathutil）
```

`~/.hs2_animkit/` 里是解包缓存（骨架 / 网格 / 头部，约 2 MB），删掉会自动重建。

---

## 八、自检

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

---

## 九、已知限制

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
- **只支持标准女骨**：`chara_*.ini` 可选，但骨骼长度仍取 HS2 的。
- **Mixamo 路径没有参与这次的 MMDD 对拍**：它走的是另一套世界旋转增量映射。

---

## 十、开源许可 / License

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
