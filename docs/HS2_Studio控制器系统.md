# HS2 Studio 控制器系统（反编译笔记）

> 对象：`StudioNEOV2/Managed/Assembly-CSharp.dll`（反编译产物在
> `_ref/hs2game/studio/Assembly-CSharp.decompiled.cs`），以及
> `HoneySelect2_Data/Managed/Assembly-CSharp-firstpass.dll`（RootMotion Final IK）。
>
> 目的：搞清工作室里「一个角色到底有哪些可拖的控制器、它们驱动什么」，
> 而不是照 MMD/MMDD 的层级去猜。

## 1. 结论

工作室的姿势系统不是 MMD 那套虚拟骨，也不是自写 IK，而是：

```
FullBodyBipedIK (Final IK)
  ├─ 9 个 IKEffector
  ├─ 5 条 FBIKChain（Body + 左右臂 + 左右腿）
  └─ 4 个 IKMappingLimb + 1 个 IKMappingSpine + 头部 mapping
```

工作室在 `AddObjectAssist.InitIKTarget`（`studio/...cs:107082`）把
**13 个 GuideObject** 绑到这些 effector / bendGoal 上：

| # | 工作室目标 | 绑定对象 | 类型 | 驱动骨 |
|---|---|---|---|---|
| 0 | Body | `solver.bodyEffector` | 位置 | pelvis（`cf_J_Hips`） |
| 1 | LeftShoulder | `solver.leftShoulderEffector` | 位置 | `references.leftUpperArm`（`cf_J_ArmUp00_L`） |
| 2 | LeftArmChain | `solver.leftArmChain.bendConstraint` | bendGoal | `references.leftForearm`（`cf_J_ArmLow01_L`） |
| 3 | LeftHand | `solver.leftHandEffector` | 位置 + 旋转 | `references.leftHand`（`cf_J_Hand_L`） |
| 4 | RightShoulder | `solver.rightShoulderEffector` | 位置 | `cf_J_ArmUp00_R` |
| 5 | RightArmChain | `solver.rightArmChain.bendConstraint` | bendGoal | `cf_J_ArmLow01_R` |
| 6 | RightHand | `solver.rightHandEffector` | 位置 + 旋转 | `cf_J_Hand_R` |
| 7 | LeftThigh | `solver.leftThighEffector` | 位置 | `cf_J_LegUp00_L` |
| 8 | LeftLegChain | `solver.leftLegChain.bendConstraint` | bendGoal | `cf_J_LegLow01_L` |
| 9 | LeftFoot | `solver.leftFootEffector` | 位置 + 旋转 | `cf_J_Foot01_L` |
| 10 | RightThigh | `solver.rightThighEffector` | 位置 | `cf_J_LegUp00_R` |
| 11 | RightLegChain | `solver.rightLegChain.bendConstraint` | bendGoal | `cf_J_LegLow01_R` |
| 12 | RightFoot | `solver.rightFootEffector` | 位置 + 旋转 | `cf_J_Foot01_R` |

`AddIKTarget` 的重载决定了权重（`studio/...cs:107318`）：

```csharp
// IKEffector
_effector.positionWeight = 1f;
_effector.rotationWeight = (_usedRot ? 1f : 0f);
_effector.target = iKInfo.targetObject;

// FBIKChain
_chain.bendConstraint.weight = 1f;
_chain.bendConstraint.bendGoal = iKInfo.targetObject;
```

即：**只有 Hand / Foot 有旋转权重**，Body / Shoulder / Thigh 是纯位置，
ArmChain / LegChain 是 bendGoal（极向）。这解释了为什么工作室里拖手/脚
会带着转手腕/脚踝，而拖肩/大腿只改变位置。

## 2. IK 分组 / ActiveIK

`OICharInfo`（`studio/...cs:113588`）：

```csharp
public bool enableIK;
public bool[] activeIK = new bool[5] { true, true, true, true, true };
public bool enableFK;
public bool[] activeFK = new bool[7] { false, true, false, true, false, false, false };
```

`ActiveIK` 的 5 组（`OCIChar.ActiveIK`，`studio/...cs:110415`）：

```
0 Body      → BoneGroup.Body
1 RightArm  → RightArm | RightHand
2 LeftArm   → LeftArm  | LeftHand
3 RightLeg  → RightLeg | RightFoot
4 LeftLeg   → LeftLeg  | LeftFoot
```

`ActiveIKGroup` 直接对 `listIKTarget` 里的 GuideObject 做 `active` 开关，
并在 `IKCtrl.CopyBone` 时按组复制。

`FK` 的 7 组（`FKCtrl.parts`，`studio/...cs:105266`）：

```
Hair, Neck, Breast, Body, RightHand, LeftHand, Skirt
```

`FKCtrl.InitBones`（`studio/...cs:105295`）从 `Info.dicBoneInfo` 生成
每根骨的 GuideObject；`FKCtrl.TargetInfo.Update`（`:105253`）每帧：

```csharp
transform.localRotation = Quaternion.Euler(changeAmount.rot);
syncBoneInfo.LocalRotation = transform.localRotation;
```

也就是说 **FK 控制器直接写 localRotation**，不是 world 增量。
`SyncBoneInfo` 用于把同一个 `changeAmount` 同步到多根骨（例如左右手指/
头发镜像），所以同名控制会同时驱动几根骨。

## 3. GuideObject / LocalIK

`AddIKTarget` 里创建目标时（`studio/...cs:107370`）：

```csharp
GameObject gameObject = new GameObject(_target.name + "(work)");
gameObject.transform.SetParent(_ociChar.charInfo.transform);
guideObject.mode = GuideObject.Mode.LocalIK;
guideObject.enableRot = _usedRot;
```

即目标物体是角色 transform 的子物体，位置/旋转是**角色局部**的；
`GuideMove.MoveCalc.TYPE3` 决定拖动时的轴向/吸附。Blender 里我们的
`STU_*` 控制器是骨架空间的，语义等价但少掉 GuideObject 的吸附/轴向约束。

## 4. 和 H 场景 MotionIK 的区别

`MotionIK`（`studio/...cs:40825`）确实也操作 `FullBodyBipedIK`，但它是
**HScene** 的按 Animator 状态名 + 帧号查表补偿：

- `HScene`（`studio/...cs:61217`）构造 4 个 MotionIK；
- `MotionIKData`（`studio/...cs:41519`）含 50 根 `FrameCorrect` 骨 +
  13 根 `IKCorrect` 骨 + shape 混合；
- Studio 加载角色时 `AddObjectAssist.DisableComponent`（`studio/...cs:107249`）
  会把角色 `objAnim` 上的所有 `BaseProcess` 全关掉。

所以：

* **Studio 摆姿势不走 MotionIK / FrameCorrect**；
* 游戏动作里的 `*_dam` 骨是**动画数据里烘好的关键帧**，不是运行时算的；
* 想还原 `dam` 骨只能从动作数据反推关系（本插件 v3.0 用逐帧 Slerp 拟合）。

## 5. 对 Blender 插件的映射

| 工作室 | 插件 |
|---|---|
| Body effector | `STU_Body`（`CTRL_center` 的父级，平移骨盆） |
| Shoulder effector | `STU_Shoulder_L/R`（机械臂链 + 上臂骨的父级 + COPY_LOCATION） |
| ArmChain bendGoal | `STU_ArmChain_L/R`（肘极向） |
| Hand effector | `STU_Hand_L/R`（IK 目标，位置+旋转） |
| Thigh effector | `STU_Thigh_L/R`（机械腿链 + 大腿骨的父级 + COPY_LOCATION） |
| LegChain bendGoal | `STU_LegChain_L/R`（膝极向） |
| Foot effector | `STU_Foot_L/R`（IK 目标，位置+旋转） |
| ActiveIK 5 组 | 面板「Studio 分组（ActiveIK）」 |
| FK Body/Neck/Hands | `CTRL_*`（现有 FK 控制层） |
| FK Hair/Breast/Skirt | 尚未做（只有物理骨，没生成控制器） |

**没有复刻**：FBBIK 的 spine mapping（`IKMappingSpine`，3 次迭代）、
`FBIKChain` 的 reach/push/pushParent、`ChildConstraint`、hand/foot 的
`planeBone1/2/3` 旋转平面。所以 Body/Shoulder/Thigh 是位置效应器近似，
不会为了够目标去弯脊柱。

## 6. 关键行号索引

| 内容 | 行号 |
|---|---|
| `AddObjectAssist.InitIKTarget` | 107082 |
| `AddIKTarget`（effector / bendGoal / 目标物体） | 107318–107393 |
| `OICharInfo`（IK/FK 分组默认值） | 113588 |
| `OCIChar.ActiveIK` / `ActiveIKGroup` | 110415 / 110432 |
| `FKCtrl`（7 组、localRotation、SyncBoneInfo） | 105172 |
| `IKCtrl`（CopyBone/CopyBoneRotation） | 105469 |
| `DisableComponent`（Studio 关掉 BaseProcess） | 107249 |
| `MotionIK` / `MotionIKData` | 40825 / 41519 |
| `FrameCorrect` / `IKCorrect` | 287403 / 287441 |
| `FullBodyBipedIK` | firstpass 23431 |
| `IKSolverFullBodyBiped.SetToReferences` | firstpass 28244 |
