# Model Playground

一个基于 OpenRouter 的模型测试平台，支持多模型并排对比和 Prompt 调教。

## 功能

- **多模型并排对比** - 同一个 prompt 同时发给多个模型，实时对比输出
- **流式输出** - 实时看到模型的生成过程
- **System Prompt 编辑** - 自由设定角色、场景、规则
- **参数调节** - Temperature、Top P、Max Tokens 等参数可调
- **预设管理** - 保存/加载常用的 prompt + 参数 + 模型组合
- **Markdown 渲染** - 自动渲染模型输出的 Markdown 格式

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

编辑 `.env` 文件，填入你的 OpenRouter API Key：

```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx
```

### 3. 启动服务

```bash
python -m uvicorn server:app --reload --port 8000
```

### 4. 打开浏览器

访问 http://localhost:8000

## 使用方法

1. 在左侧选择一个或多个模型
2. 编写 System Prompt（可选，用于设定角色/场景）
3. 调整参数（Temperature 越高创意越强，越低越精确）
4. 在底部输入框发送消息，Ctrl+Enter 快速发送
5. 可以把常用配置保存为预设，方便下次使用

## 项目结构

```
├── .env                # API Key 配置
├── server.py           # FastAPI 后端
├── requirements.txt    # Python 依赖
├── static/
│   ├── index.html      # 页面结构
│   ├── style.css       # 样式
│   └── app.js          # 前端逻辑
└── data/
    └── presets.json     # 预设数据
```
