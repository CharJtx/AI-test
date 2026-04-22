# 人物 Skill 系统 — Handoff

Status: **Stage 1.5 usable** (prompt-only Musk skill end-to-end). Stage 2 (LoRA) scaffolded, not yet run.

## What's live right now

- **vLLM 推理服务**: `vllm/vllm-openai:v0.10.2` 容器 (`skill-vllm`) 在 upaiserver303 上运行，暴露 `http://localhost:8080/v1/*`，公网映射 `https://llm.insnaplive.com/v1/*`。
- **基座模型**: `Meta-Llama-3.1-70B-Instruct-AWQ-INT4` (37 GB), TP=4 on GPUs 0/1/4/5, `--enforce-eager`, `--max-model-len 8192`, `--gpu-memory-utilization 0.70`.
- **LoRA 热插**: **暂未启用** — vLLM 0.10.2 的 `--enable-lora` 与 AWQ 量化组合在 `_create_lora_modules` 初始化时 OOM / 崩溃。见下面 Stage 2 注意事项。
- **第一个 skill**: [`skills/elon-musk/`](../../skills/elon-musk/) — prompt-only，基于 titanwings 6 层 schema 手写，可直接聊。
- **`services/skill_inference/`** FastAPI router 已挂入 `server.py`，前端可走 `/api/skills/*`。

## 端到端快速检查

```bash
# 1. vLLM 直连
curl https://llm.insnaplive.com/v1/models

# 2. server.py 挂载后的 skill 列表（需要先启动 server.py）
curl http://localhost:<server_py_port>/api/skills

# 3. 跟 Musk skill 聊
curl -N http://localhost:<server_py_port>/api/skills/elon-musk/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"how would you fix climate change?"}]}'
```

## 文件结构

```
services/skill_inference/
  __init__.py
  client.py       # vLLM OpenAI-compatible 异步 client
  router.py       # FastAPI router: GET /, GET /{id}, POST /{id}/chat (SSE)
  skill_store.py  # skills/ 文件系统读取层
  README.md       # 本文

skills/
  elon-musk/
    meta.json     # 元信息 + 授权状态（当前 NOT_OBTAINED）
    persona.md    # titanwings 6 层人设
    skill.md      # 运行时 system prompt (精简版)

tools/distill/
  __main__.py     # CLI: python -m tools.distill <stage>
  ingest.py       # Stage 1: yt-dlp + WhisperX (脚手架，手动跑)
  analyze.py      # Stage 2: 调我们自己 vLLM 生成 persona.md
  synthesize.py   # Stage 3: 合成 ~30k ShareGPT turns
  train.py        # Stage 4: 生成 Axolotl QLoRA 配置 + 运行指令
  package.py      # Stage 5: 组装 skill 包 + 刷新 meta.json
```

## Fast-lane pipeline（推荐，单人 ~30 分钟）

取代原 Stage 2（QLoRA 训练）。走 RAG，不改模型权重。

**设计原则**：重依赖（faster-whisper、sentence-transformers、sqlite-vec、torch）全部走**本地自建镜像**，版本 pin 死。host 上只需要 Python 3.10+ 和 httpx——这些在 upaiserver303 默认就有。

### 一键跑（推荐）

```bash
# 在 upaiserver303，从 repo 根目录：
bash tools/distill_fast/run_fast_lane.sh elon-musk
```

这会按顺序：
1. 构建 `skill-transcribe:v1` docker 镜像（首次 3-5 分钟），然后 yt-dlp + faster-whisper large-v3 on GPU 2
2. Python stdlib 把转录切 ~120 词 chunks
3. 调 `llm.insnaplive.com` 一次生成 titanwings 6 层 `persona.md`
4. 构建 `skill-embed:v1` 镜像（首次 5-8 分钟），bge-m3 向量化 + sqlite-vec 落盘 on GPU 3

**环境变量可覆盖**（可选）：
```bash
TRANSCRIBE_GPU=3 EMBED_GPU=6 \
  bash tools/distill_fast/run_fast_lane.sh elon-musk
```

### 分步跑（调试用）

```bash
python3 -m tools.distill_fast transcribe --creator elon-musk --source skills/elon-musk/sources.yaml --device cuda:2
bash work/ingest/elon-musk/run_transcribe.sh        # 真正执行 docker

python3 -m tools.distill_fast chunk   --creator elon-musk
python3 -m tools.distill_fast persona --creator elon-musk
python3 -m tools.distill_fast embed   --creator elon-musk
bash work/ingest/elon-musk/run_embed.sh              # 真正执行 docker
```

### 产物

```
skills/elon-musk/
  knowledge/
    chunks.jsonl         # 文本块 + provenance
    index.db             # sqlite + vec0 向量索引
    manifest.json        # embed_model / dim / n_chunks
  persona.generated.md   # 若已有手写 persona.md，生成版与之并列（手写优先）
work/ingest/elon-musk/
  audio/                 # yt-dlp 下载的 wav（可删，只要 transcripts 保留）
  transcripts/           # faster-whisper 输出的 json
  docker/                # 镜像构建上下文（bundle 迁移时一并复制）
  sources.yaml           # 拷贝进来的源清单
  run_transcribe.sh      # 自动生成的执行脚本
  run_embed.sh
```

### 镜像是怎么保证稳定的

- `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`（官方，长期存在）
- `faster-whisper==1.0.3` / `torch==2.4.1` / `sentence-transformers==3.3.1` / `sqlite-vec==0.1.6` 全部 pin 死
- 不依赖 ghcr.io 第三方维护的 Whisper 镜像（上一版用 `ghcr.io/guillaumekln/faster-whisper` 已经被作者弃用）
- HF 缓存映射到 docker named volume `hf_cache`，多个创作者共用模型下载不重复

### 何时该升级镜像

改 pip 版本 → 改 tag `skill-transcribe:v1` → `v2` → 重跑即可。旧镜像留着做回滚。

**运行时自动走 RAG**：只要 `knowledge/index.db` 存在，`/api/skills/{id}/chat` 每次收到用户消息就向量检索 top-5 chunks，作为 "Reference material" 注入 system prompt。手写的 `skill.md` / `persona.md` 始终生效，RAG 只是 additive。

想临时禁用 RAG：删掉 `index.db`，或在 `services/skill_inference/router.py` 里 `DEFAULT_RAG_K = 0`。

## Slow-lane（QLoRA 训练）尚未完成的事

1. **vLLM LoRA + AWQ 兼容性**：0.10.2 上崩。两种路径：
   - 升级到 vLLM ≥ 0.11，需要宿主机驱动 ≥ 575 (CUDA 12.9+) — 当前驱动 570。
   - 改用 GPTQ 量化或 FP16（需要 8×4090，不是 4 卡）。
   - 或用 LoRAX（Predibase）服务 LoRA 那层，vLLM 只服务 base。
2. **数据管线**：`tools/distill/ingest.py` 目前仅打印需要手动执行的 docker 命令。要真跑需要给它加 runner。
3. **语料授权**：`meta.json` 里 `consent_status` 硬编码 `NOT_OBTAINED — internal POC only`。在与真人签约之前**绝不允许**外发。
4. **训练执行**：axolotl 配置已生成好，但训练要 3-6h GPU 时间，另开一轮做。

## 合规红线（国际商用前必填）

见 [plan 文件](../../.claude/plans/synchronous-skipping-clock.md) 的合规部分。本 skill 目前处于 "内部技术 POC" 状态：
- ✅ 可以跑、测、调参、做技术验证
- ❌ 不可上架 App Store / Google Play
- ❌ 不可通过 Stripe / PayPal 收钱
- ❌ 不可对欧盟 / 英国 / 美国多州用户开放

外发前必须完成：创作者授权合同、AI 披露 UI、年龄验证、地理围栏、删除权 SLA、DPIA 等（共 14 项 launch-blocker）。

## 运维

```bash
# 重启 vLLM
ssh upaiserver303 'bash /tmp/launch_vllm.sh'

# 停
ssh upaiserver303 'docker stop skill-vllm'

# 恢复 violet_lotus（老的 llama.cpp 服务）
ssh upaiserver303 'docker stop skill-vllm && docker start violet-lotus'

# 看日志
ssh upaiserver303 'docker logs --tail 50 skill-vllm'
```

## 已知问题 / 下一步

- [ ] LoRA 热插尚未跑通（阻塞 Stage 2 训练产出的直接使用）
- [ ] 前端 WhatsApp/WeChat 气泡 UI 未建（现在只能通过 API 测，没有浏览器界面）
- [ ] `tools/distill/ingest.py` 的实际执行 runner（当前仅打印命令）
- [ ] 合规栈（年龄验证 / 水印 / 地理围栏 / 合同模版）全部未做
- [ ] 声音层（TTS 复用现有 Triton `spark_tts`）未接线
