# InteractivePlayer 交互式视频播放器

基于状态机驱动的网页交互式视频播放器组件。通过 JSON 配置定义状态、视频资源和点击区域跳转逻辑，实现类似互动视频/视觉小说的交互体验。

---

## 快速开始

### 1. 启动服务

```bash
python -m uvicorn server:app --reload --port 8000
```

### 2. 访问播放器

- 场景列表页：`http://127.0.0.1:8000/playground/`
- 直接打开某场景：`http://127.0.0.1:8000/playground/?scene=demo`

### 3. 调试模式

按键盘 **`D`** 键切换调试模式，可以看到：
- 当前状态名称、视频资源、是否循环
- 所有跳转规则
- 网格区域高亮（绿/蓝/黄对应不同跳转目标）

---

## 文件结构

```
playground/
├── index.html            # 入口页面（场景选择 + 播放器）
├── player.js             # InteractivePlayer 组件类（通用，不含业务数据）
├── player.css            # 播放器样式
├── README.md             # 本文档
└── scenes/               # 所有场景目录
    └── demo/             # 一个场景 = 一个独立目录
        ├── scene-data.json       # 场景配置
        ├── 状态1.mp4 ~ 状态8.mp4  # 状态视频
        └── 过渡1.mp4 ~ 过渡3.mp4  # 过渡视频
```

**每个场景是一个自包含的目录**，包含 `scene-data.json` 和所有引用的视频文件。添加新场景只需在 `scenes/` 下新建目录。

---

## 数据结构规范 (`scene-data.json`)

完整的 JSON 配置由四个顶层字段组成：

```json
{
  "config":       { ... },
  "resources":    { ... },
  "states":       { ... },
  "initialState": "state1"
}
```

### `config` — 全局配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `config.grid.cols` | `number` | 点击区域列数 |
| `config.grid.rows` | `number` | 点击区域行数 |

```json
"config": {
  "grid": { "cols": 5, "rows": 2 }
}
```

画面会被等分为 `cols × rows` 个矩形点击区域。坐标从左上角 `(0, 0)` 开始，行号自上而下递增，列号自左向右递增。

### `resources` — 资源映射表

键值对形式，将资源 ID 映射到实际的视频文件名。

```json
"resources": {
  "state1": "状态1.mp4",
  "transition1": "过渡1.mp4"
}
```

- 资源 ID 是内部引用名，可以自定义，只要在 `states` 中引用时一致即可
- 文件名是相对于页面路径的视频文件名
- 所有资源在初始化时会被并行预加载

### `states` — 状态定义

每个状态是一个键值对，键为状态 ID，值为状态对象：

```json
"state2": {
  "video": "state2",
  "loop": true,
  "next": null,
  "on_click": [
    { "regions": { "rows": [1], "cols": [0,1,2,3,4] }, "target": "state3" },
    { "regions": { "rows": [0], "cols": [0,1,2,3,4] }, "target": "transition1" }
  ]
}
```

#### 状态字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `video` | `string` | 是 | 引用的资源 ID（对应 `resources` 中的键） |
| `loop` | `boolean` | 是 | `true` = 视频循环播放等待交互；`false` = 播放一次后自动跳转 |
| `next` | `string \| null` | 否 | 非循环状态播放结束后自动跳转的目标状态 ID |
| `on_click` | `array` | 否 | 点击区域跳转规则数组（按顺序匹配，首个命中的规则生效） |

#### `on_click` 规则

每条规则包含两个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `regions` | `object \| "*"` | 触发区域定义，`"*"` 表示全部区域（兜底规则） |
| `target` | `string` | 点击后跳转到的目标状态 ID |

`regions` 为对象时的结构：

```json
{
  "rows": [0, 1],    // 匹配的行号数组（0 起始）
  "cols": [3, 4]     // 匹配的列号数组（0 起始）
}
```

匹配逻辑：**行号在 `rows` 中 AND 列号在 `cols` 中** 时命中。

**规则优先级**：`on_click` 数组按顺序遍历，**第一条匹配的规则生效**。因此可以将精确规则放前面、`"*"` 通配放末尾作为兜底：

```json
"on_click": [
  { "regions": { "rows": [1], "cols": [2] }, "target": "state8" },
  { "regions": "*", "target": "state2" }
]
```

上例表示：点击第 2 行第 3 列 → state8，点击其他任何位置 → state2。

### `initialState` — 初始状态

```json
"initialState": "state1"
```

播放器启动后进入的第一个状态 ID。

---

## 状态机运行逻辑

```
                   ┌─────────────────────────┐
                   │     enterState(id)       │
                   └────────────┬─────────────┘
                                │
                   ┌────────────▼─────────────┐
                   │  停止当前视频，切换到新视频  │
                   │  currentTime = 0, play()  │
                   └────────────┬─────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
              loop = true              loop = false
                    │                       │
          ┌────────▼────────┐     ┌────────▼────────┐
          │ 视频循环播放      │     │ 视频播放一次     │
          │ 等待用户点击      │     │ 播放结束触发      │
          │                  │     │ video.onended    │
          └────────┬────────┘     └────────┬────────┘
                   │                       │
          用户点击某个区域           自动调用 enterState(next)
                   │
          遍历 on_click 规则
          首个匹配的 target
                   │
          enterState(target)
```

**两种状态类型：**

| 类型 | loop | 行为 |
|------|------|------|
| **等待状态** | `true` | 视频循环播放，直到用户点击匹配区域触发跳转 |
| **过渡状态** | `false` | 视频播放一次，结束后自动跳转到 `next` 指定的状态 |

---

## InteractivePlayer API

### 构造函数

```javascript
const player = new InteractivePlayer(container, config);
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `container` | `HTMLElement` | 播放器挂载的 DOM 容器 |
| `config` | `object` | 符合上述规范的场景配置对象 |

### 方法

| 方法 | 说明 |
|------|------|
| `async init()` | 初始化播放器：构建 DOM、预加载全部视频、显示开始界面 |
| `enterState(stateId)` | 手动切换到指定状态（也可从外部调用） |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `currentStateId` | `string \| null` | 当前状态 ID |
| `currentVideo` | `HTMLVideoElement \| null` | 当前播放的 video 元素 |
| `debug` | `boolean` | 是否处于调试模式 |
| `transitioning` | `boolean` | 是否正在切换状态（防止重复触发） |

### 使用示例

```html
<div id="player"></div>
<script src="player.js"></script>
<script>
  const sceneName = 'demo';
  const baseUrl = `scenes/${sceneName}/`;

  fetch(baseUrl + 'scene-data.json')
    .then(r => r.json())
    .then(data => {
      const player = new InteractivePlayer(
        document.getElementById('player'),
        data,
        { baseUrl }
      );
      player.init();
    });
</script>
```

构造函数第三个参数 `opts.baseUrl` 会作为前缀拼接到所有资源文件名之前，用于支持场景目录与播放器页面分离的部署方式。

---

## 当前场景状态转换图

```
开始
 │
 ▼
状态1 ──(播完)──▶ 状态2 ◀─────────────────────────┐
                   │  ▲                             │
          行0点击   │  │  播完                       │
           │       │  │                             │
           ▼       │  │                             │
         过渡1     │ 状态3 ◀── 行1点击               │
           │       │                                │
           ▼       │                                │
         状态4     │                                │
        ┌──┴──┐    │                                │
  列0-2 │     │ 列3-4                               │
        ▼     ▼    │                                │
      状态6  过渡2  │                                │
        │     │    │                                │
        │     ▼    │                                │
        │   状态5  │                                │
        │     │    │                                │
        │  [1,2]点击                                │
        │     ▼    │                                │
        │   过渡3  │                                │
        │     │    │                                │
        │     ▼    │                                │
        │   状态7 ◀┼─── 状态8 ◀── [1,2]点击          │
        │     │    │      ▲                         │
        │     │    │      │(播完回状态7)              │
        │   其他点击┼──────┘                          │
        │          │                                │
        └──────────┴────────────────────────────────┘
                (播完回状态2)
```

### 交互区域定义（5列 × 2行网格）

```
     列0    列1    列2    列3    列4
    ┌──────┬──────┬──────┬──────┬──────┐
行0 │      │      │      │      │      │
    ├──────┼──────┼──────┼──────┼──────┤
行1 │      │      │      │      │      │
    └──────┴──────┴──────┴──────┴──────┘
```

### 各状态点击区域映射

| 状态 | 区域 | 目标 |
|------|------|------|
| 状态2 | 行1（整行） | → 状态3 |
| 状态2 | 行0（整行） | → 过渡1 |
| 状态4 | 列0~列2（所有行） | → 状态6 |
| 状态4 | 列3~列4（所有行） | → 过渡2 |
| 状态5 | 行1 × 列2（单格） | → 过渡3 |
| 状态7 | 行1 × 列2（单格） | → 状态8 |
| 状态7 | 其余所有区域 | → 状态2 |

---

## 扩展指南

### 添加新场景

1. 在 `playground/scenes/` 下创建新目录（例如 `scenes/chapter2/`）
2. 将视频文件放入该目录
3. 创建 `scene-data.json`，定义 `resources`、`states`、`initialState`
4. 访问 `?scene=chapter2` 即可加载

场景列表页（`/playground/`）会自动扫描 `scenes/` 下所有包含 `scene-data.json` 的子目录并展示。

### 在已有场景中添加新状态

1. 将视频文件放入场景目录
2. 在 `resources` 中注册资源 ID 和文件名
3. 在 `states` 中添加状态定义
4. 通过 `next` 或其他状态的 `on_click` 将其连入状态图

### 修改网格布局

修改 `config.grid` 即可改变点击区域划分：

```json
"config": {
  "grid": { "cols": 3, "rows": 3 }
}
```

然后相应更新 `on_click` 中的行列号。

### 复杂区域匹配示例

**整行匹配：**
```json
{ "rows": [0], "cols": [0,1,2,3,4] }
```

**整列匹配：**
```json
{ "rows": [0,1], "cols": [2] }
```

**单个格子：**
```json
{ "rows": [1], "cols": [2] }
```

**L 形区域（多行多列组合）：**
```json
{ "rows": [0,1], "cols": [0,1,2] }
```

**通配（所有区域）：**
```json
"*"
```

---

## 技术细节

### 预加载策略

- 初始化时并行预加载全部视频资源（`preload="auto"`）
- 加载界面显示进度条和计数（`已加载 / 总数`）
- 若某个视频在切换时尚未就绪，会显示 mini loading 等待 `canplay` 事件

### 视频切换

- 所有视频元素在初始化时创建并加入 DOM（隐藏）
- 切换状态时：旧视频 `pause()` 并移除 `.active`，新视频 `currentTime = 0` 并添加 `.active`
- 使用 `video.onended` 事件检测非循环视频播放结束
- `transitioning` 标志防止快速点击导致的重复状态切换

### 网格定位

- 网格覆盖层根据视频实际显示尺寸动态定位（处理 `object-fit: contain` 的 letterbox 情况）
- 窗口 resize 时自动重新计算
- 点击事件坐标通过网格 cell 的 `data-row` / `data-col` 属性获取，无需手动换算

### 浏览器兼容

- 使用 `playsInline` 属性支持移动端内联播放
- 通过"点击开始"覆盖层满足浏览器自动播放策略要求（需要用户交互才能触发首次播放）
