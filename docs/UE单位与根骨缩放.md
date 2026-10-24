# UE 单位与根骨缩放

## 症状

插件导出的 FBX 进 UE 之后：

- 角色大小正常（175cm 的人还是 175cm）
- 但 Skeleton 里 `p_cf_anim` 的缩放是 **100**
- 把两个骨架设为「兼容骨架 / Compatible Skeleton」、互相套动画时动画错乱
  （位移被放大、姿态被压扁）

## 原因

`p_cf_anim` 的缩放不是插件写进去的，是 **UE 导入时算出来的**。

1. Blender 场景用米，UE 用厘米。Blender 的 FBX 导出器默认把「米 → 厘米」的
   100 倍记在**文件单位**上：`GlobalSettings.UnitScaleFactor = 100`，几何数据
   仍然写成米（髋骨 1.1435）。
2. UE 5.x 的导入器（Interchange + ufbx，`LoadOpts.target_unit_meters = 0.01`）
   会把文件换算到厘米，也就是整体 ×100。
3. 这次换算没有「地方放」，最后落在**骨架的根骨**上。UE 建参考姿势时：
   - 根骨的变换取 BindPose 里的**全局矩阵**（没有 BindPose 就取节点的全局变换）
   - 再乘上导入变换 `BuildFbxMatrixForImportTransform`（`Import Uniform Scale` 也在这里）
   - 见 `UnrealEd/Private/Fbx/FbxSkeletalMeshImport.cpp` 里 `ImportBones()` 对
     `GlobalsPerLink[0]` 和 `RootTransform` 的处理

   于是根骨拿到 `1 × 100 = 100`，子骨因为挂在根骨下面，世界变换刚好恢复到厘米，
   所以「看起来是对的大小，但根骨缩放是 100」。

顺带说明：**手动填 `Import Uniform Scale = 100` 会更糟**（实测 100 → 根骨 10000，
人变成 175 米）。那个值也是乘在根骨上的。

## 修法

导出的 FBX 里不要留任何需要 UE 换算的东西 —— 直接把数据写成厘米：

| | 改前 | 改后 |
| --- | --- | --- |
| 文件单位 `UnitScaleFactor` | 100（米） | **1（厘米）** |
| 骨头/顶点数值 | 米（1.1435） | 厘米（114.35） |
| 骨架节点缩放 | 1 | 1 |
| BindPose 里根骨缩放 | 1 | 1 |
| UE 导入后 `p_cf_anim` 缩放 | **100** | **1** |

实现见 `hs2_rig_kit/ueunit.py`：导出期间把 rest 骨、姿势位移、蒙皮顶点临时
×100（骨架物体本身带缩放时先把它收进数据，例如工作室 FBX 角色是 0.1），
`UnitScaleFactor` 写成精确的 1，导出结束再把场景原样还原（存的是原值，不是
乘完再除，避免浮点误差累积）。

覆盖的导出路径：

- 导出角色 FBX（含/不含网格）
- 导出当前角色动画
- 按物体导出（道具、衣服）

Unity 仍在米制，走 `axis != "ue"` 的路径，不受影响。

## 验证

UE 5.8 无头导入（`AssetImportTask` 默认选项）实测：

| 文件 | 高度 | `p_cf_anim` 缩放 | `cf_J_Hips` |
| --- | --- | --- | --- |
| 改前（米，unit 100） | 175.13 | 100.0000 | 114.350 |
| 改后（厘米，unit 1） | 175.13 | **1.0000000** | 114.350 |

两个文件的骨头世界坐标完全一致（差异 < 1e-6），只有根骨缩放不同；把动画 FBX
导到已有骨架上播放，髋骨也落在 102.9cm（和网格一致）。

Blender 侧的自查脚本：`tools/test_ue_export.py`。

## 已经导入过 UE 的资产怎么办

根骨缩放是**导入时**写进参考骨架的，改 FBX 不会自动改已有资产：

1. 用 5.0.3 重新导出骨架和全部动画 FBX
2. UE 里删掉旧的 Skeleton / SkeletalMesh / AnimSequence（或重新导入覆盖）
3. 导入时 `Import Uniform Scale` 保持 **1**
4. 确认 `p_cf_anim` 的缩放是 1，再做兼容骨架

## 别用「文件 → 导出 → FBX」

Blender 自带的导出（默认 `Apply Scalings: All Local`）会把「米 → 厘米」的 100 倍
记在**骨架物体节点**上，自己也是单位 1：

| 场景里的骨架缩放 | Blender 手动导出后 armature 节点缩放 | UE 里 `p_cf_anim` |
| --- | --- | --- |
| 1（默认角色） | 100 | **100** |
| 0.1（工作室 FBX 角色） | 10 | **10** |

所以从插件里导：`调整 → 编辑对象 → 导出当前角色动画`、`导出 FBX`（勾「带蒙皮网格」）、
`按物体导出`，这几条路径都已经把数据换成厘米了。

如果改完的模型想只导 rest 姿势（不带动作），在 `5. 导出 FBX` 里把 **带上当前动作**
取消勾选 —— 否则无论有没有动作，导出器都会烤一条「静止姿势」的动画进去。
