# 工作室 FBX 角色：骨架修正与动作对接

面向 HS2MeshExporter 导出的角色 FBX。实现在 `hs2_rig_kit/fbxrig.py`，
入口是 `ops.import_character_fbx()`；UI 在 `HS2 → 动作 → 编辑对象`。

## 1. 导出数据长什么样

| 项 | 值 |
| --- | --- |
| 骨架节点 | `p_cf_anim` → `cf_J_Root` → `cf_N_height` → …，共 256 根（BindPose 节点 300 个） |
| 网格 | `o_body_cf` / `o_head` / `o_eyebase_L,R` / `o_eyelashes` / `o_eyeshadow` / `o_namida` / `o_tang` / `o_tooth` |
| 坐标 | Y-up，单位声明为 cm，实际数值 1 单位 = 0.1m |
| BindPose | 每根骨一个全局矩阵，**带角色体型缩放**（这套角色是 0.9 附近，个别骨 0.83~0.97 非均匀） |
| BindPose 姿势 | 手臂下垂约 45° 的 A-pose，手指微弯 |
| 场景姿势 | 与 studio 里摆的姿势无关，导出固定是上面这个 A-pose |

## 2. 骨骼错位的根因

Blender 自带 FBX 导入器建 rest 骨时用的是：

```python
bone_matrix = parent_matrix @ self.get_bind_matrix().normalized()
```

`normalized()` 会把 3×3 部分归一化，**丢掉 BindPose 的缩放**。于是骨头整体比
蒙皮大 `1/0.9 ≈ 1.11`：

| | 手臂骨 z | 手臂网格 z |
| --- | --- | --- |
| 源 FBX BindPose | 1.069 m | 1.304 ~ 1.351 m（同一条手臂，肘部低于肩部） |
| Blender 导入后 | 1.188 m | —— |

看起来很严重（差了 0.15m），其实只是**所有骨头一起被放大了 11%**。动画绕着放大后
的枢轴转，网格就被拉成带状。

## 3. 修法：直接按 BindPose 重建 rest

```python
targets[name] = bind_pose_global_matrix[name]     # 不转基！
```

Blender 把轴转换（Y-up → Z-up）挂在**骨架物体**的 `matrix_world` 上，
edit bone 的矩阵就是 FBX 的全局矩阵，所以直接照搬、不要再转一次基
（转了会整体歪 90°，这是第一版踩过的坑）。

- `head` ← BindPose 平移
- 朝向 ← BindPose 旋转的第 2 列（Y 轴）
- `roll` ← `align_roll(BindPose 第 3 列)`
- 骨长 ← 到最近子骨 BindPose 位置的距离（跟着角色比例走，不用导出器的长度）
- 全部 `use_connect = False`，否则改子骨 head 会被父骨 tail 拽回去

丢掉缩放不影响形变：骨骼形变只跟"骨头位置 + 旋转"有关，位置摆对就完全对齐。
（严格说非均匀缩放的骨会有几个百分点偏差，视觉上看不出来。）

## 4. 姿势对齐

工作室的 bind 是 A-pose，插件默认骨架 `p_cf_anim` 是**手臂平举的 T-pose**，
手指也是伸直的。直接把 A-pose 的角色丢进动作里会出现"没被动作驱动的骨
停在 A-pose"的情况。

所以导入时把每根骨的世界朝向直接抄成默认骨架的朝向（`clip=None` 的
`fbxrig.apply`），她的站姿、手型就和插件默认角色一模一样。

关键点：**朝向要用 Unity 空间的原始矩阵**（`_frame_worlds` 不做 `mat_to_blender`）。
因为 FBX 骨的 rest 就活在 Unity 那种 Y-up 空间里，轴转换由骨架物体负责。
用 Blender 空间的矩阵会整体差 90°（第二版踩过的坑）。

## 5. 套动作

游戏动作是「骨骼世界朝向的绝对值」。每帧每根骨：

```python
qt = Q_game(bone)                      # 目标世界朝向（Unity 空间原始矩阵）
pos = 父骨姿势 @ (rest 局部偏移)        # 骨头位置由链决定，保持角色自己的骨长
basis = A^-1 @ (world[parent]^-1 @ M)  # 反解成 matrix_basis
```

- 朝向绝对、位置刚性 —— 不会出现"骨长按默认骨架算"导致的拉伸
- 有位移轨道的骨（`pos` track）用**父骨空间的局部增量**，不是世界坐标。
  直接用世界坐标会把手指从手上撕下来：那 97 根骨的位置轨道是静止的，
  换成绝对位置等于把它们钉在 bind 位置，而手已经转走了
- 根位移按身高比 `k` 缩放（`头骨-髋骨` 距离之比），脚不会离地
- 只写 `rotation_quaternion` + 需要位移的 `location`，`scale` 不动

## 6. L/R 镜像

工作室导出把 X 轴镜像了：FBX 里 `cf_J_Hand_L` 落在 **+X**，游戏里
`cf_J_Hand_L` 在 **-X**（Blender 里角色朝 +Y，左手在 -X）。

按名字硬套会让手臂横穿身体，所以按**世界 X 位置**配对：+X 上的骨拿游戏 +X 的
数据。角色整体是镜像的，这样动作方向和游戏一致。

## 7. 验收

```bash
blender -b --factory-startup --python tools/test_fbx_rig.py -- <角色.fbx>
```

用插件默认角色跑同一批动作当参照，比较网格边长伸缩比：

| 动作 | FBX 角色 p99/max/>1.5% | 默认角色 p99/max/>1.5% |
| --- | --- | --- |
| mc_f_move_07_L | 1.11 / 2.29 / 0.05% | 1.18 / 1.49 / 0.00% |
| action_44 | 1.20 / 6.04 / 0.25% | 1.29 / 4.18 / 0.26% |
| adv_action_05_01 | 1.26 / 5.62 / 0.24% | 1.28 / 2.55 / 0.31% |
| sleep_09 | 1.44 / 6.55 / 0.84% | 1.45 / 2.99 / 0.83% |
| f_custom_pose_03 | 1.36 / 2.44 / 0.50% | 1.42 / 3.02 / 0.70% |
| action_56_R_loop | 1.32 / 5.37 / 0.38% | 1.30 / 2.42 / 0.22% |

中位伸缩比 1.000，p99 与默认角色同档。少数几倍拉伸在默认角色上同样存在，
来自游戏动作本身（IK 驱动的关节），不是重定向引入的。

骨头-蒙皮距离：中位 2.7cm（含胸部/会阴等远离骨头的小区域）。

## 8. 不做什么

- 不做重新绑定（rebind）到默认骨架，也不复制默认骨架的 rest
- 不移动单根骨"看起来对上"
- 不把角色网格镜像回来（会翻法线；而且动作按 X 位置配对后已经一致）
