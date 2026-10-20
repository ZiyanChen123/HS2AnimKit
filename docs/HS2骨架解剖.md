# HS2 骨架解剖 —— 控制器、绑定、VMD 插件的真实机制

这份文档是反编译 `HS2MeshExporter.dll` 与 `HS2_VMDPlayPlugin.dll` 之后的结论汇总，
工具里每一处设计取舍都对应下面某一条。

---

## 1. 角色对象结构

Unity 里一个 HS2 角色（`ChaControl` → `ChaInfo`）大致是：

```
ChaControl (GameObject)
├─ objAnim          只承载 Animator 的空物体
├─ objBodyBone      BodyTop/p_cf_body_bone  ← 骨骼根
├─ objHeadBone      HeadTop/p_cf_head_bone
├─ objHairBone      HairTop/...
└─ (一堆 SkinnedMeshRenderer 挂在 body/face/hair/clothes 节点下)
```

`objAnim` 和 `objBodyBone` 是**两个不同的物体**——这是关键。
Animator 挂在 `objAnim` 上，而骨骼在 `objBodyBone` 里；插件就是靠
`vmdAnimationGo.transform.localPosition/localRotation = objAnim.transform.*`
让 dummy 骨架和真正的骨骼对齐。
（见 `HS2VMDPlayPlugin/VMDAnimationController.cs` 第 97–102 行）

---

## 2. 骨骼命名体系

| 前缀 | 含义 | 例 |
|---|---|---|
| `cf_J_*` | **主骨架**，真正驱动蒙皮 | `cf_J_Hips`、`cf_J_Spine01`、`cf_J_LegUp00_L` |
| `cf_s_*` | 捏人滑块/ABMX 辅助骨（shape） | `cf_s_Mune00_s_L`、`cf_s_LegUp01_h_L` |
| `cf_d_*` | 动态骨（DynamicBone：裙摆/乳摇/头发） | `cf_d_sk_top`、`cf_d_sk_00_00` |
| `cf_a_*` | 饰品骨 | |
| `cf_hit_*` | 碰撞体 | `cf_hit_height` |
| `cf_n_height` / `cf_N_height` | 身高缩放骨（整体 scale） | |
| `k_f_*` | **FK 吸附点**，不是骨骼 | `k_f_handL_00`、`k_f_shoulderR_00` |
| `p_cf_*` | prefab 容器节点 | `p_cf_body_bone` |
| `a_n_*` | 饰品挂点 | `a_n_headside` |

> 名字里的 `J` 大写、`s/d/a` 小写，是 Illusion 一贯的写法。
> `Transform.Find` 区分大小写，所以 `cf_j_root` 和 `cf_J_Root` 是两个东西，
> 网上不少老脚本混用（比如整合包里的 `AutoAdjustSkirtBone.cs` 用的是小写，那是从 HS1/AI 抄过来的）。

---

## 3. 实际骨架层级

由 `HS2_VMDPlayPlugin/VMDAnimationController.cs` 的 `boneNames_base` 提取（这是插件实际
查找的路径，所以一定是游戏里的真实结构，`_dummy_*` 是插件自己插进去的）：

```
cf_J_Root
└ cf_n_height
  └ cf_J_Hips
    ├ cf_J_Spine01
    │ └ cf_J_Spine02
    │   └ cf_J_Spine03
    │     ├ cf_J_Neck → cf_J_Head
    │     ├ cf_J_ShoulderIK_L → cf_J_Shoulder_L → cf_J_ArmUp00_L → cf_J_ArmLow01_L
    │     │   → cf_J_Hand_L → cf_J_Hand_s_L → 五指 × 3 节
    │     └ cf_J_ShoulderIK_R → ...（镜像）
    └ cf_J_Kosi01
      └ cf_J_Kosi02
        ├ cf_J_LegUp00_L → cf_J_LegLow01_L → cf_J_LegLowRoll_L
        │   → cf_J_Foot01_L → cf_J_Foot02_L → cf_J_Toes01_L
        └ cf_J_LegUp00_R → ...（镜像）
```

注意几个和 MMD/标准人形不一样的地方：

- **多了 `cf_J_ShoulderIK_L`**：夹在 Spine03 和 Shoulder 之间，游戏内部用来保持肩关节贴合。
- **多了 `cf_J_Kosi02`**：髋 → 骨盘 → 大腿，比 MMD 的两级多一级。
- **多了 `cf_J_LegLowRoll_L`**：膝盖下方的小滚转骨，约 2–3cm。
- **多了 `cf_J_Foot02_L`**：脚掌 → 脚趾之间还有一节。
- **多了 `cf_J_Hand_s_L`**：手掌中心骨，五根手指都挂在它下面。

这些"多出来的"骨在写 retarget 时最容易踩坑：MMD 的「左ひざ」对应 `cf_J_LegLow01_L`，
而 MMD 的「左足首」对应的 `cf_J_Foot01_L` 并不在 `cf_J_LegLow01_L` 的正下方——
中间还隔着 `cf_J_LegLowRoll_L`。这就是为什么 Blender 的 IK 链末端（骨骼 tail）
**不等于**脚踝位置，工具里 `_foot_ik` 特意放在链末端而不是脚踝。

---

## 4. HS2MeshExporter 到底做了什么

```
Grey.cs        面板、找角色、建输出目录
Fbx.cs         遍历 SkinnedMeshRenderer / MeshRenderer，逐个导出
UnityFbxExporterBinding.cs   P/Invoke 到游戏自带的 UnityFbxExporter_x86_64_v16.dll
TextureExporter.cs          用相机重渲染把贴图抓成 png
```

关键在 `UnityFbxExporterBinding.ScanHierarchy` + `RecursiveAddTransforms`：

```csharp
for (int i = 0; i < bone.childCount; i++) {
    Transform child = bone.GetChild(i);
    if (child.gameObject.activeSelf) {
        IntPtr node = CreateNode(fbxBone, child.name);   // ← 每个节点都建
        result.FbxNodes.Add(child, node);
        RecursiveAddTransforms(child, node, ...);
    }
}
```

而"是不是骨骼"只在这里决定：

```csharp
private static void RecursivelyEnsureLimb(...) {
    MakeNodeSkeleton(intPtr);   // 只有蒙皮 bones[] 里出现的才会被标成 skeleton
}
```

**结论**：FBX 里有两种节点——被打上 skeleton 标记的（→ Blender 里的骨骼）
和没打标记的（→ Blender 里的 Empty，即十字架）。后者在游戏里本来就是纯层级节点，
没有任何形变的意义，**不可能也没必要"转换成控制器"**。

顺带一提：`ScanHierarchy` 里会临时 `new GameObject("SkinnedMeshContainer")` 挂到根下，
导出完不回收——所以每次导出的 FBX 里都多一个叫 `SkinnedMeshContainer` 的空物体，可以直接删。

---

## 5. VMD 插件怎么工作

### 5.1 造一副 dummy 骨架

`LoadAndCreate` 按 `boneNames_base` 的路径在 `vmdAnimation` 物体下建出一整条层级，
然后把每个 dummy 骨对齐到 HS2 真实骨骼的**世界位置**，本地旋转清零；
再在自己下面挂一个 `MR` 子物体，`MR` 的世界旋转 = 该 HS2 骨骼的**静止世界旋转**。

```csharp
val.transform.position = transform.position;          // 对齐位置
val.transform.localRotation = Quaternion.identity;    // 清空
Transform MR = new GameObject("MR").transform;
MR.parent = val.transform;
MR.localPosition = Vector3.zero;
MR.rotation = transform.rotation;                     // 记住静止世界旋转
dictionary[MR.gameObject] = transform.gameObject;
```

### 5.2 每帧把 dummy 的世界旋转抄过去

```csharp
foreach (var kv in map) {                 // kv.Key = MR, kv.Value = 真骨骼
    if (IsMoveBone(kv.Value.transform))   // cf_J_Hips
        kv.Value.transform.SetPositionAndRotation(kv.Key.transform.position,
                                                  kv.Key.transform.rotation);
    else
        kv.Value.transform.localRotation =
            Quaternion.Inverse(parent.rotation) * kv.Key.transform.rotation;
}
```

### 5.3 推导出真正的换算公式

设 `R0(b)` = 骨骼 b 的静止世界旋转，`q(b)` = VMD 里该骨的关键帧旋转（MMD 模型空间）。

MR 的世界旋转 = `dummy旋转 × R0(b)`；而 dummy 沿层级累乘 VMD 旋转，所以

```
Δ(b) = Δ(parent) · q(b)                     （沿层级累乘）
HS2骨骼世界旋转(t) = Δ(b) · R0(b)
```

Blender 里 `pose_bone.rotation_quaternion` 是**骨骼局部**旋转，于是

```
Q(b) = R0(b)⁻¹ · q(b) · R0(b)
```

`apply.py` 就是照这个式子逐帧算的。这也是为什么不需要知道任何"轴向修正表"——
插件里那些 `AddRotationMap(boneName, x, y, z, ...)` 的轴修正，本质上都是在补
MMD 模型和 HS2 角色静止朝向的差异；我们直接用**世界旋转增量**就绕过了这个问题。

### 5.4 坐标系与单位

- MMD 与 Unity 同为左手 Y-up，MMD4Mecanim 导入 PMX 时也不做轴翻转，所以 **VMD 的四元数
  可以直接当 Unity 局部旋转用**。
- MMD → Blender 是 Y-up → Z-up，等价于绕 X 轴 +90° 的基变换：
  ```
  q_blender = R · q_mmd · R⁻¹     R = Quaternion((1,0,0), +90°)
  v_blender = (x, -z, y)
  ```
- 单位：`DebugPMDLoader.cs` 里写着 `bone_position * 0.1f * Settings.GlobalScale.Value`，
  即 **MMD 1 单位 = 0.1 HS2 单位**。工具默认 `--scale 0.1`。

### 5.5 足IK

`CoreVMDAnimationController` 有一套 `ProcessFootIK()`（`CCDIKSolver.cs` 是自带的 CCD 实现），
在应用完 FK 之后再把腿拉向「足ＩＫ」的**绝对坐标**。

所以：
- VMD 的 `足ＩＫ` 位置通道是**模型空间绝对坐标**，不是偏移
- VMD 的 `センター` / `グルーブ` / `全ての親` 位置通道是**偏移**
- 两者在 `apply.py` 里分开处理

工具提供两种模式：`fk`（用 FK，脚部 IK 目标从 FK 推导）和 `vmd`（用 VMD 的足IK 坐标）。

---

## 6. 为什么"控制器"必须在 Blender 里长出来

HS2 Studio 的时间轴（`OCIChar` / `OIBoneData` / `OIRoute`）做的事情：

```
用户拖滑块 → OIBoneData.value (0..1) → 插值到骨骼 localRotation
```

没有约束系统、没有 IK 解算、没有极向量、没有控制器形状。
游戏内 `1` 键切换的那个"IK"是插件（`HS2_AdvIKPlugin`）后加的，不是原生的。

所以：

> FBX 里不存在"控制器的数据"，因为它本来就不存在。
> 能做的只有：**在 Blender 里按 HS2 的骨架结构，重新绑一套**。

本工具绑的东西：

| 需求 | 实现 |
|---|---|
| 整体移动/旋转 | 直接用 `cf_J_Hips`（游戏里唯一能整体位移的骨） |
| 根部 | `cf_J_Root` |
| 腿部 IK | `LegLow01` 挂 IK 约束，链长 2，目标 `_foot_ik_*`，极向量 `_knee_pole_*` |
| 膝盖方向 | 每帧极向量由 FK 膝位推导，保证切 IK 零跳变 |
| 脚掌 | `Foot01` 世界空间 Copy Rotation ← `_foot_ik_*` |
| 脚趾 | `Toes01` 世界空间 Copy Rotation ← `_toe_ik_*` |
| 手臂 | 默认纯 FK（HS2 的手臂本来就靠 `ShoulderIK` 递推），可选加 IK |
| 控制器造型 | `shapes.py` 程序化生成线框齿轮/圆环/菱形/脚印 |

---

## 7. 已知限制

1. **导出前姿势**：FBX 的 rest pose = 导出瞬间的姿势。必须在 Studio 里回到初始姿势再导。
2. **表情**：VMD morph 与 HS2 面部系统不同源，未做自动映射。
3. **物理骨**：`cf_d_*` / `cf_s_*` 不含在 VMD 里，导出后是刚性的。
4. **`vmd` 足IK 模式的膝盖朝向**：VMD 的足IK 旋转决定膝盖平面，工具用固定的前向极向量近似，
   极端动作（劈叉、抬腿过高）可能膝盖方向不理想，切到 `fk` 模式或手动调 `_knee_pole_*` 即可。
5. **命名可能微调**：`cf_J_LegLowRoll_L` 这类滚转骨在小部分 Mod 骨架里可能不存在，
   `prepare_rig` 会按存在与否自动跳过，不会报错。
