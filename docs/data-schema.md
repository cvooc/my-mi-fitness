# data/bodyfat.json 数据格式

> 面向数据使用方：字段含义、枚举字典、与云端原始记录的差异。
> 快照由 `fetch_bodyfat.py` 生成，schema 版本 **`mi-fits/bodyfat@1`**（结构变化时升级版本号）。
> 快速上手见 [README](../README.md)；接口与协议见 [protocol.md](protocol.md)。

字段含义依据 APK 3.61.0 的 RN bundle 文案（`res/localizedString/zh-CN.json`）与 DEX
常量（`com.xiaomi.fitness.*.annotation.*`）；取证位置见 §3 末尾。

## 1. 文件结构

`data/bodyfat.json` 的 schema 为 `mi-fits/bodyfat@1`：

| 顶层键 | 类型 | 说明 |
| --- | --- | --- |
| `schema` | string | 固定 `mi-fits/bodyfat@1`；结构变化时升级版本号 |
| `updated_at` | string | 快照生成时间，格式 `%Y-%m-%d %H:%M:%S`（运行环境本地时区，CI 中为 Asia/Shanghai） |
| `updated_at_ts` | number | 同上，epoch 秒 |
| `count` | number | `records` 数组长度 |
| `latest` | object | 最新一条记录的副本（即 `records` 的最后一条），方便直接读「最新体脂」 |
| `records` | array | 全部记录，按 `time` 升序；每条结构见 §2 |

## 2. 单条记录字段

每条记录 = 云端一条体重记录 + 同一次测量返回的体成分。只有体重、没有体成分的记录
（`has_body_composition=false`，如手动录入或未赤脚测量）里，体成分字段可能为 `0` 或缺失。

**标识与时间**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `time` | number | 毫秒 | 测量时间（epoch 毫秒） |
| `time_sec` | number | 秒 | 同一时间的秒级时间戳 |
| `time_text` | string | - | 本地时间 `YYYY-MM-DD HH:mm` |
| `sid` | string | - | 来源设备 id：S800 为 `blt.3.xxx`；`xiaomiwear_app_manually` 表示 App 手动录入 |
| `key` | string | - | 数据类型键，固定 `weight`（体成分随体重记录一起返回） |
| `source` | string | - | 归一化来源，固定 `fitness/weight` |
| `has_body_composition` | boolean | - | 本次是否含完整体成分（`body_fat_rate > 0`） |

**体重与体成分**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `weight` | number | kg | 体重 |
| `bmi` | number | kg/m² | BMI |
| `body_fat_rate` | number | % | 体脂率 |
| `fat_mass` | number | kg | 脂肪量 |
| `fat_free_body` | number | kg | 去脂体重（瘦体重） |
| `muscle_mass` | number | kg | 肌肉量 |
| `muscle_rate` | number | % | 肌肉率 |
| `skeletal_muscle_mass` | number | kg | 骨骼肌量 |
| `limbs_skeletal_muscle_index` | number | kg/m² | 骨骼肌指数（四肢骨骼肌指数，常用于肌少症筛查参考） |
| `protein_mass` | number | kg | 蛋白质量 |
| `protein_rate` | number | % | 蛋白质率 |
| `body_moisture_mass` | number | kg | 体水分量 |
| `moisture_rate` | number | % | 水分率 |
| `bone_mass` | number | kg | 骨盐量（骨量） |
| `bone_rate` | number | % | 骨盐率 |
| `visceral_fat` | number | 等级 | 内脏脂肪等级（不是 kg） |
| `whr` | number | 比例 | 腰臀比 |
| `basal_metabolism` | number | kcal/天 | 基础代谢（BMR） |
| `body_age` | number | 岁 | 身体年龄 |
| `body_score` | number | 分 | 身体得分（0–100） |
| `recommended_calories_intake` | number | kcal/天 | 推荐每日热量摄入 |
| `bpm` | number | 次/分 | 测量时心率；`0` 表示本次未测到 |

**目标与控制建议**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `standard_weight` | number | kg | 标准体重（旧算法，仅部分历史记录有） |
| `standard_weight_v2` | number | kg | 标准体重（新算法，优先使用） |
| `weight_control` | number | kg | 体重目标调整量：正=建议增重，负=建议减重，`0`=维持 |
| `muscle_control` | number | kg | 肌肉目标调整量：正=建议增加，`≤0`=维持 |
| `fat_control` | number | kg | 脂肪目标调整量：负=建议减少，`≥0`=维持 |

**体型与评分口径（枚举，见 §3）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `somatotype` | number | 体型（10 档，-5…4） |
| `body_shape` | number | 身体形态（0…5） |
| `score_standard_type` | number | 评分所用标准（EvaluationType，0…3） |

**节段数据（八电极秤专有）**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `trunk_muscle_mass` / `trunk_fat_mass` | number | kg | 躯干肌肉量 / 脂肪量 |
| `left_upper_limb_muscle_mass` / `left_upper_limb_fat_mass` | number | kg | 左上肢肌肉量 / 脂肪量 |
| `right_upper_limb_muscle_mass` / `right_upper_limb_fat_mass` | number | kg | 右上肢肌肉量 / 脂肪量 |
| `left_lower_limb_muscle_mass` / `left_lower_limb_fat_mass` | number | kg | 左下肢肌肉量 / 脂肪量 |
| `right_lower_limb_muscle_mass` / `right_lower_limb_fat_mass` | number | kg | 右下肢肌肉量 / 脂肪量 |
| `trunk_muscle_rank` / `trunk_fat_rank` | number | 枚举 | 躯干肌肉 / 脂肪等级：-1 偏低、0 正常、1 偏高 |
| `left_upper_limb_muscle_rank` / `left_upper_limb_fat_rank` | number | 枚举 | 左上肢肌肉 / 脂肪等级，同上 |
| `right_upper_limb_muscle_rank` / `right_upper_limb_fat_rank` | number | 枚举 | 右上肢肌肉 / 脂肪等级，同上 |
| `left_lower_limb_muscle_rank` / `left_lower_limb_fat_rank` | number | 枚举 | 左下肢肌肉 / 脂肪等级，同上 |
| `right_lower_limb_muscle_rank` / `right_lower_limb_fat_rank` | number | 枚举 | 右下肢肌肉 / 脂肪等级，同上 |
| `upper_limb_muscle_balance` / `upper_limb_fat_balance` | number | 枚举 | 上肢（左右上肢）均衡度 |
| `lower_limb_muscle_balance` / `lower_limb_fat_balance` | number | 枚举 | 下肢（左右下肢）均衡度 |
| `limbs_muscle_balance` / `limbs_fat_balance` | number | 枚举 | 上下肢均衡度 |

## 3. 枚举字典

所有枚举的整数常量都来自 APK 的 DEX；中文名来自 APK 内 RN bundle 的文案字典。

### `somatotype` 体型

| 值 | 常量 | 中文名（App 文案） |
| --- | --- | --- |
| -5 | `THIN` | 消瘦型 |
| -4 | `SKINNY` | 偏瘦型 |
| -3 | `MASKED_OBESITY` | 隐形肥胖 |
| -2 | `LEAN` | 苗条肌肉型 |
| -1 | `SLENDER` | 苗条型 |
| 0 | `FIT` | 健康型 |
| 1 | `MUSCULAR` | 肌肉型 |
| 2 | `PLUMP` | 偏胖 |
| 3 | `ATHLETIC` | 运动员体型 |
| 4 | `OVERWEIGHT` | 肥胖 |

> DEX 里这些常量以 byte 存储（`THIN=251 … SLENDER=255`，即 -5…-1 的无符号写法），
> JSON 中是带符号整数。App 另有两套 `bodyType.desc.*` 建议文案（普通秤 / S400·电子秤各一套），
> 属于长段文字，本仓库未收录。

### `body_shape` 身体形态

| 值 | 常量 | 中文名 |
| --- | --- | --- |
| 0 | `UNKNOWN` | 未识别 |
| 1 | `APPLE_SHAPE` | 苹果型 |
| 2 | `PEAR_SHAPE` | 梨型 |
| 3 | `HOUR_GLASS_SHAPE` | 沙漏型 |
| 4 | `V_SHAPE` | 倒三角型 |
| 5 | `NEAT_FIGURE` | 匀称型 |

### `score_standard_type` 评分标准

| 值 | 常量 | 含义 |
| --- | --- | --- |
| 0 | `DEFAULT` | 默认 |
| 1 | `TYPE_S400` | S400 体脂秤口径 |
| 2 | `TYPE_WEIGHT_SCALE_STANDARDS` | 普通体重秤口径 |
| 3 | `TYPE_ELECTRONIC_SCALE_STANDARDS` | 电子体脂秤口径（S800 属此类；现有 315 条记录中有体成分的记录均为 3，手动录入记录不含该字段） |

### `*_balance` 均衡度

| 值 | 常量 | 中文名 |
| --- | --- | --- |
| 0 | （默认） | 均衡 |
| 1 | `SLIGHTLY_UNBALANCED` | 轻度不均 |
| 2 | `SEVERE_UNBALANCED` | 严重不均 |

### `*_rank` 节段等级

| 值 | 含义 |
| --- | --- |
| -1 | 偏低（App 用 low 配色） |
| 0 | 正常 |
| 1 | 偏高（App 用 high 配色） |

### 其它取值说明

- `sid`：`blt.3.xxx`（八电极秤设备 id）或 `xiaomiwear_app_manually`（App 手动录入）。
- `key` 固定 `weight`；`source` 固定 `fitness/weight`。
- `time_text` 依赖运行环境时区；CI 已固定 `TZ=Asia/Shanghai`。
- BMI、体脂率等**数值型**字段本身不带分级，App 会按 `score_standard_type` 用不同阈值
  判定「偏低/正常/偏高」，阈值属于展示逻辑，不在 JSON 中。

> 取证：`Lcom/xiaomi/fitness/electronic/scale/annotation/{Somatotype,BodyShape,WeightParamBalanceLevel}`、
> `Lcom/xiaomi/fitness/health/weight/annotation/EvaluationType`（classes11/12.dex）；
> 中文名来自 `health` bundle 的 `health.weight.somatotype.* / bodyShape.* / balance.*`；
> `*_rank` 配色来自 health bundle `he-1866` 的 `-1===l?low:1===l?high:normal`。

## 4. 与原始 Cloud API 的差异

- 云端 fitness 记录的 `value` 是 JSON 字符串，内含 **57 个键**；本仓库额外补了
  `time_sec / time_text / source / has_body_composition`，并把 `time` 统一成毫秒。
- 云端记录没有 `*_level` 字段（App 本地库里另有），所以节段等级只有 `*_rank`。
- `measuring_impedance_1..12`、`accountId`、`account_name` 只在 eco 兜底接口
  （`--api pull/list`）的记录里出现；fitness 记录不含原始阻抗，只返回云端算好的结果字段。
