"""
AI 角色扮演系统后端服务器

基于 FastAPI 构建，通过 OpenRouter API 代理 LLM 调用，提供以下核心功能：
- AI 角色卡的生成（关键词生成、图像生成）、混搭（Remix）与 CRUD 管理
- 角色头像提示词生成与改写
- 聊天消息的流式补全（SSE）
- 世界书（Worldbook）、预设（Preset）、聊天记录的持久化管理
- Edge-TTS 语音合成
- Playground 场景资源管理
- 静态前端文件托管

所有持久化数据以 JSON 文件形式存储在 data/ 目录下。
"""

# ── 标准库导入 ──────────────────────────────────────────────
import io
import json
import os
import re
from pathlib import Path

# ── 第三方库导入 ────────────────────────────────────────────
import edge_tts
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ── 应用初始化 ──────────────────────────────────────────────

# 从 .env 文件加载环境变量（主要是 OPENROUTER_API_KEY）
load_dotenv()

app = FastAPI()


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """禁用前端静态资源缓存的中间件，确保开发时总能获取最新文件。"""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.add_middleware(NoCacheStaticMiddleware)

# ── 数据文件路径与常量 ──────────────────────────────────────
DATA_DIR = Path("data")
PRESETS_FILE = DATA_DIR / "presets.json"       # 推理参数预设
WORLDBOOKS_FILE = DATA_DIR / "worldbooks.json"  # 世界观设定集
CHARACTERS_FILE = DATA_DIR / "characters.json"  # 角色卡数据
CHATS_FILE = DATA_DIR / "chats.json"            # 聊天记录
OPENROUTER_BASE = "https://openrouter.ai/api/v1"  # OpenRouter API 基础地址


# ── 通用工具函数 ────────────────────────────────────────────


def get_api_key() -> str:
    """获取 OpenRouter API 密钥，未配置时返回空字符串。"""
    return os.getenv("OPENROUTER_API_KEY", "")


def _load_json(path: Path) -> list[dict]:
    """
    从 JSON 文件读取数据列表。
    文件不存在时返回空列表，避免首次运行报错。
    """
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _save_json(path: Path, data):
    """将数据序列化写入 JSON 文件，保留中文字符并格式化缩进。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 以下 load_*/save_* 函数为各数据类型的快捷读写封装

def load_presets() -> list[dict]:
    return _load_json(PRESETS_FILE)


def save_presets(presets: list[dict]):
    _save_json(PRESETS_FILE, presets)


def load_worldbooks() -> list[dict]:
    return _load_json(WORLDBOOKS_FILE)


def save_worldbooks(books: list[dict]):
    _save_json(WORLDBOOKS_FILE, books)


def load_chats() -> list[dict]:
    return _load_json(CHATS_FILE)


def save_chats(chats: list[dict]):
    _save_json(CHATS_FILE, chats)


def load_characters() -> list[dict]:
    return _load_json(CHARACTERS_FILE)


def save_characters(chars: list[dict]):
    _save_json(CHARACTERS_FILE, chars)


def _next_id(items: list[dict]) -> int:
    """为列表中的新条目生成自增 ID（取现有最大 ID + 1）。"""
    return max((item["id"] for item in items), default=0) + 1


# 角色卡标准字段列表，用于从各种格式中提取统一结构
CHAR_FIELDS = [
    "name", "description", "personality", "scenario",
    "first_mes", "mes_example", "system_prompt",
    "creator_notes", "tags",
]


def _normalize_char(data: dict) -> dict:
    """Extract standard character card fields from various formats.

    兼容多种角色卡格式（原生、TavernAI V2、SillyTavern），
    将字段统一映射到本系统的标准结构。
    """
    # TavernAI V2 格式将实际字段嵌套在 "data" 键下
    src = data.get("data", data) if isinstance(data.get("data"), dict) else data

    char = {}
    for f in CHAR_FIELDS:
        char[f] = src.get(f, "")
    if not char["tags"]:
        char["tags"] = []
    if isinstance(char["tags"], str):
        char["tags"] = [t.strip() for t in char["tags"].split(",") if t.strip()]

    # SillyTavern 使用不同的字段名，在此做兼容映射
    if not char["description"] and src.get("char_persona"):
        char["description"] = src["char_persona"]
    if not char["scenario"] and src.get("world_scenario"):
        char["scenario"] = src["world_scenario"]
    if not char["mes_example"] and src.get("example_dialogue"):
        char["mes_example"] = src["example_dialogue"]
    if not char["first_mes"] and src.get("char_greeting"):
        char["first_mes"] = src["char_greeting"]
    if not char["name"]:
        char["name"] = src.get("char_name", "")

    return char


# ── 模型列表 API ───────────────────────────────────────────

@app.get("/api/models")
async def list_models():
    """
    获取 OpenRouter 可用模型列表。

    从 OpenRouter /models 端点拉取全部模型信息，提取前端需要的字段
    （名称、上下文长度、定价、是否审核等），按名称排序后返回。
    返回: {"models": [...]}
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{OPENROUTER_BASE}/models",
            headers={"Authorization": f"Bearer {get_api_key()}"},
        )
        data = resp.json()
    models = []
    for m in data.get("data", []):
        top_provider = m.get("top_provider") or {}
        models.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "description": m.get("description", ""),
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing"),
            "is_moderated": top_provider.get("is_moderated", False),
            "max_completion_tokens": top_provider.get("max_completion_tokens"),
            "architecture": m.get("architecture"),
        })
    models.sort(key=lambda x: x["name"])
    return {"models": models}


# ── AI 角色生成 ─────────────────────────────────────────────

# 角色卡生成系统提示词：指导 LLM 根据用户关键词生成完整的角色卡 JSON，
# 包含外貌描述、性格、场景、开场白、示例对话和角色书条目。
CHAR_GEN_SYSTEM = """You are an expert character card designer for AI roleplay systems.
Given user-provided keywords/concepts, generate a COMPLETE character card in JSON format.

Requirements:
1. All text fields should be rich, detailed, and psychologically nuanced.
2. The character_book must contain 15-25 entries covering: core identity, relationships, psychology, environment, behaviors, speech patterns, and intimate dynamics.
3. Each character_book entry needs relevant trigger keywords.
4. Use {{char}} for the character's name and {{user}} for the user in all text fields.
5. The mes_example should contain 2-3 realistic example exchanges using <START> separators.
6. Write ALL content in the SAME language as the user's keywords input.
7. CRITICAL for first_mes: The opening message must be written as an immersive narrative scene, NOT a self-introduction. It should:
   - Set the atmosphere through environmental details (time, place, sensory details like sounds, smells, lighting)
   - Reveal the character's identity and traits INDIRECTLY through their actions, body language, mannerisms, and dialogue style — never by stating "I am X, I do Y"
   - Naturally imply the relationship with {{user}} through the character's attitude, tone, and how they address {{user}}
   - Include a mix of *action/description* and spoken dialogue
   - Feel like the opening scene of a story, drawing the reader into a specific moment

Return ONLY valid JSON with this exact structure (no markdown, no explanation):
{
  "name": "string",
  "description": "string (detailed appearance, background, identity)",
  "personality": "string (core traits summary)",
  "scenario": "string (relationship with user, current situation)",
  "first_mes": "string (opening message in character)",
  "mes_example": "string (example dialogues with <START> separators)",
  "system_prompt": "string (roleplay behavior instructions)",
  "creator_notes": "string (usage tips)",
  "tags": ["tag1", "tag2"],
  "character_book": {
    "name": "string",
    "description": "string",
    "scan_depth": 2,
    "token_budget": 512,
    "recursive_scanning": true,
    "extensions": {},
    "entries": [
      {
        "name": "Entry Name",
        "keys": ["keyword1", "keyword2"],
        "secondary_keys": [],
        "content": "Detailed context that gets injected when keywords match...",
        "enabled": true,
        "insertion_order": 10,
        "case_sensitive": false,
        "priority": 10,
        "id": 1,
        "comment": "",
        "selective": false,
        "constant": false,
        "position": "",
        "extensions": { "depth": 4, "linked": false, "weight": 10 },
        "probability": 100,
        "selectiveLogic": 0
      }
    ]
  }
}"""


@app.post("/api/characters/generate")
async def generate_character(request: Request):
    """
    根据用户提供的关键词，调用 LLM 生成完整角色卡。

    请求体参数:
        keywords (str): 角色概念/关键词描述
        model (str): 使用的 LLM 模型 ID
    返回: {"character": {角色卡完整数据}}

    流程：关键词 → LLM 生成 JSON 角色卡 → 自动生成头像提示词 → 保存到本地
    """
    body = await request.json()
    keywords = body.get("keywords", "")
    model = body.get("model", "x-ai/grok-4-0205")

    if not keywords.strip():
        return JSONResponse({"error": "keywords is required"}, status_code=400)

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CHAR_GEN_SYSTEM},
                    {"role": "user", "content": f"Generate a character card based on these keywords/concepts:\n\n{keywords}"},
                ],
                "temperature": 0.9,
                "max_tokens": 16000,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"LLM API error: {resp.status_code} - {resp.text[:200]}"},
            status_code=502,
        )

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # LLM 有时会在 JSON 外包裹 ```markdown 代码围栏，需要剥离
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        char_data = json.loads(content)
    except json.JSONDecodeError as e:
        return JSONResponse(
            {"error": f"Failed to parse LLM output as JSON: {str(e)}", "raw": content[:500]},
            status_code=422,
        )

    # 根据角色描述自动生成一段头像图像生成提示词
    avatar_prompt = await _generate_avatar_prompt(
        char_data.get("description", ""), model
    )
    if avatar_prompt:
        char_data["avatar"] = avatar_prompt
        char_data["avatar_type"] = "prompt"

    chars = load_characters()
    char_data["id"] = _next_id(chars)
    chars.append(char_data)
    save_characters(chars)
    return {"character": char_data}


# 角色混搭系统提示词：指导 LLM 在保留原始角色核心身份的前提下，
# 根据用户修改指令对角色卡进行变体改写。
CHAR_REMIX_SYSTEM = """You are an expert character card designer specializing in remixing and transforming existing characters.

You will receive:
1. An existing character card in JSON format
2. The user's modification instructions (new traits, themes, tags to add, aspects to change)

Your task: Transform the character card according to the instructions while preserving the character's core identity where not contradicted.

Rules:
1. Naturally integrate the requested changes into ALL relevant fields (description, personality, scenario, first_mes, mes_example, system_prompt, character_book).
2. Update the character_book: modify existing entries that relate to the changes, and ADD new entries (2-5) specifically covering the new traits/themes.
3. Rewrite first_mes to reflect the changes while keeping the same immersive narrative scene style:
   - Set atmosphere through environmental details
   - Reveal traits INDIRECTLY through actions and dialogue
   - Include *action/description* and spoken dialogue
4. Update tags to include the new themes.
5. Keep {{char}} and {{user}} placeholders intact.
6. Write in the SAME language as the original card.
7. The result must feel like a coherent, unified character — not a patchwork of old + new.
8. Return ONLY valid JSON with the exact same structure as the input (no markdown, no explanation)."""


# 图像识别生成角色卡系统提示词：接收角色图片，通过视觉推理
# 生成与图像外观一致的完整角色卡（支持多模态模型）。
CHAR_GEN_IMAGE_SYSTEM = """You are an expert character card designer for AI roleplay systems.
You will receive an image of a character and optional supplementary notes.
Based on the character's visual appearance (clothing, expression, body language, setting, style),
infer and create a COMPLETE, richly detailed character card in JSON format.

Requirements:
1. Describe the character's appearance in detail based on the image.
2. Invent a fitting name, personality, background, and scenario that match the visual impression.
3. The character_book must contain 15-25 entries covering: appearance details, core identity, relationships, psychology, environment, behaviors, speech patterns, and intimate dynamics.
4. Each character_book entry needs relevant trigger keywords.
5. Use {{char}} for the character's name and {{user}} for the user in all text fields.
6. The mes_example should contain 2-3 realistic example exchanges using <START> separators.
7. If the user provides supplementary notes, incorporate them into the character design.
8. Write ALL content in the SAME language as any user-provided text (default to Chinese if no text given).
9. CRITICAL for first_mes: The opening message must be written as an immersive narrative scene, NOT a self-introduction. It should:
   - Set the atmosphere through environmental details (time, place, sensory details like sounds, smells, lighting)
   - Reveal the character's identity and traits INDIRECTLY through their actions, body language, mannerisms, and dialogue style — never by stating "I am X, I do Y"
   - Naturally imply the relationship with {{user}} through the character's attitude, tone, and how they address {{user}}
   - Include a mix of *action/description* and spoken dialogue
   - Feel like the opening scene of a story, drawing the reader into a specific moment

Return ONLY valid JSON with the same structure as a standard character card (name, description, personality, scenario, first_mes, mes_example, system_prompt, creator_notes, tags, character_book with entries)."""


import base64  # noqa: E402


@app.post("/api/characters/generate-from-image")
async def generate_character_from_image(
    image: UploadFile = File(...),
    extra: str = Form(""),
    model: str = Form("google/gemini-2.5-flash-preview"),
):
    """
    通过上传角色图片，由多模态 LLM 推理生成对应的角色卡。

    表单参数:
        image (UploadFile): 角色参考图片
        extra (str): 用户补充说明（可选）
        model (str): 使用的多模态模型 ID
    返回: {"character": {角色卡完整数据}}

    头像直接使用上传的原图（base64 data URL）。
    """
    # 将图片编码为 base64 data URL，用于多模态 API 请求
    img_bytes = await image.read()
    content_type = image.content_type or "image/jpeg"
    b64 = base64.b64encode(img_bytes).decode()
    data_url = f"data:{content_type};base64,{b64}"

    # 构造多模态消息：图片 + 文本指令
    user_content = [
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    text_part = "Generate a complete character card based on this image."
    if extra.strip():
        text_part += f"\n\nAdditional notes from the user:\n{extra.strip()}"
    user_content.append({"type": "text", "text": text_part})

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CHAR_GEN_IMAGE_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.9,
                "max_tokens": 16000,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"LLM API error: {resp.status_code} - {resp.text[:200]}"},
            status_code=502,
        )

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        char_data = json.loads(content)
    except json.JSONDecodeError as e:
        return JSONResponse(
            {"error": f"Failed to parse LLM output as JSON: {str(e)}", "raw": content[:500]},
            status_code=422,
        )

    # 图片生成模式下，头像直接存储用户上传的原图
    char_data["avatar"] = data_url
    char_data["avatar_type"] = "image"

    chars = load_characters()
    char_data["id"] = _next_id(chars)
    chars.append(char_data)
    save_characters(chars)
    return {"character": char_data}


# ── 角色混搭（Remix）──────────────────────────────────────────

@app.post("/api/characters/remix")
async def remix_character(request: Request):
    """
    对已有角色卡进行混搭改写，生成变体版本。

    请求体参数:
        original (dict): 原始角色卡数据
        instructions (str): 用户的修改指令（如"添加吸血鬼设定"）
        model (str): 使用的 LLM 模型 ID
    返回: {"character": {混搭后的新角色卡}}

    头像处理策略：
    - 原始头像为图片 → 直接继承
    - 原始头像为提示词 → 调用 _remix_avatar_prompt 改写
    - 无头像 → 从新描述自动生成
    """
    body = await request.json()
    original = body.get("original", {})
    instructions = body.get("instructions", "")
    model = body.get("model", "x-ai/grok-4.1-fast")

    if not instructions.strip():
        return JSONResponse({"error": "instructions is required"}, status_code=400)
    if not original.get("name"):
        return JSONResponse({"error": "original character is required"}, status_code=400)

    # 发送给 LLM 时排除头像二进制数据，减少 token 消耗
    card_for_llm = {k: v for k, v in original.items() if k not in ("avatar", "avatar_type")}
    card_json = json.dumps(card_for_llm, ensure_ascii=False, indent=2)
    user_msg = f"Original character card:\n```json\n{card_json}\n```\n\nModification instructions:\n{instructions}"

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CHAR_REMIX_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.85,
                "max_tokens": 16000,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        return JSONResponse(
            {"error": f"LLM API error ({resp.status_code}): {detail}"},
            status_code=502,
        )

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        char_data = json.loads(content)
    except json.JSONDecodeError as e:
        return JSONResponse(
            {"error": f"Failed to parse LLM output as JSON: {str(e)}", "raw": content[:500]},
            status_code=422,
        )

    # 根据原始头像类型决定新角色卡的头像处理方式
    orig_avatar = original.get("avatar", "")
    orig_avatar_type = original.get("avatar_type", "")

    if orig_avatar_type == "image":
        char_data["avatar"] = orig_avatar
        char_data["avatar_type"] = "image"
    elif orig_avatar_type == "prompt" and orig_avatar:
        new_prompt = await _remix_avatar_prompt(orig_avatar, instructions, model)
        char_data["avatar"] = new_prompt or orig_avatar
        char_data["avatar_type"] = "prompt"
    else:
        avatar_prompt = await _generate_avatar_prompt(
            char_data.get("description", ""), model
        )
        if avatar_prompt:
            char_data["avatar"] = avatar_prompt
            char_data["avatar_type"] = "prompt"

    chars = load_characters()
    char_data["id"] = _next_id(chars)
    chars.append(char_data)
    save_characters(chars)
    return {"character": char_data}


# ── 头像提示词辅助功能 ─────────────────────────────────────

# 头像生成系统提示词：将角色文字描述转换为适用于 Stable Diffusion / DALL-E
# 等图像生成模型的写实风格肖像提示词（英文输出）。
AVATAR_PROMPT_SYSTEM = """You are an expert at creating character portrait prompts for AI image generators.

Given a character description, produce a PORTRAIT prompt suitable for Stable Diffusion / Midjourney / DALL-E.

Rules:
1. Output ONLY the prompt text, nothing else — no explanations, no labels, no markdown.
2. Write the prompt in ENGLISH regardless of the input language.
3. This is a CHARACTER PORTRAIT — focus on: face, upper body, hair style/color, eye color/shape, skin tone, expression, clothing/accessories, distinguishing features (tattoos, scars, piercings, etc.).
4. Use comma-separated descriptive tags and short phrases.
5. The prompt MUST be styled as realistic photography. Always include: "photorealistic portrait, realistic photograph, studio lighting, cinematic composition, professional photography, clear translucent skin texture, natural skin pores, shot on Canon EOS R5, 85mm lens, shallow depth of field, bokeh background"
6. Include quality boosters: "masterpiece, best quality, highly detailed, 8k uhd, RAW photo"
7. Keep the prompt between 60-150 words.
8. If intimate traits are mentioned, describe them artistically focusing on expression and body language.
9. STRICT CONTENT POLICY: The prompt must NEVER depict exposed genitalia, nipples, or fully nude bodies. Use clothing, strategic angles, fabric draping, shadows, or cropping to keep the image tasteful. Always ensure the character wears at least minimal clothing (lingerie, towel, sheet, etc.)."""

# 头像提示词改写系统提示词：在保留原始头像特征的基础上，
# 根据角色混搭指令修改现有的肖像提示词。
AVATAR_REMIX_PROMPT_SYSTEM = """You are an expert at modifying character portrait prompts for AI image generators.

You will receive:
1. An ORIGINAL portrait prompt (text-to-image prompt describing a character's appearance)
2. Modification instructions describing what changed about the character

Produce an UPDATED portrait prompt that incorporates the modifications while preserving unchanged traits.

Rules:
1. Output ONLY the updated prompt text — no explanations, no labels, no markdown.
2. Write in ENGLISH regardless of input language.
3. Naturally merge the changes into the existing prompt, don't just append.
4. Keep the same photorealistic portrait style and quality tags.
5. Keep the prompt between 60-150 words.
6. STRICT CONTENT POLICY: NEVER include exposed genitalia, nipples, or full nudity. Use clothing, angles, fabric, or shadows to keep it tasteful."""


async def _generate_avatar_prompt(description: str, model: str = "x-ai/grok-4.1-fast") -> str | None:
    """Generate a portrait prompt from a character description. Returns None on failure.

    根据角色的文字描述生成图像生成提示词，用于后续调用图像生成 API 创建头像。
    失败时静默返回 None，不影响角色卡创建流程。
    """
    if not description or not description.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_BASE}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": AVATAR_PROMPT_SYSTEM},
                        {"role": "user", "content": description},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 500,
                },
                headers={
                    "Authorization": f"Bearer {get_api_key()}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip() or None
    except Exception:
        return None


async def _remix_avatar_prompt(original_prompt: str, instructions: str, model: str = "x-ai/grok-4.1-fast") -> str | None:
    """Modify an existing avatar prompt based on remix instructions. Returns None on failure.

    根据混搭修改指令改写已有的头像提示词，使头像与角色变化保持一致。
    失败时返回 None，调用方会回退使用原始提示词。
    """
    if not original_prompt:
        return None
    try:
        user_msg = f"Original portrait prompt:\n{original_prompt}\n\nModification instructions:\n{instructions}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OPENROUTER_BASE}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": AVATAR_REMIX_PROMPT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 500,
                },
                headers={
                    "Authorization": f"Bearer {get_api_key()}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip() or None
    except Exception:
        return None


# ── 图像提示词生成 ─────────────────────────────────────────

# 场景图像提示词系统提示词：将角色扮演中的叙事文本（聊天消息）
# 转换为适用于图像生成模型的场景描述提示词，用于为聊天内容配图。
IMG_PROMPT_SYSTEM = """You are an expert at converting roleplay narrative text into image generation prompts.

Given a passage of roleplay text, extract the visual scene and produce a prompt suitable for AI image generators (Stable Diffusion, DALL-E, Midjourney, etc.).

Rules:
1. Output ONLY the prompt text, nothing else — no explanations, no labels, no markdown.
2. Write the prompt in ENGLISH regardless of the input language.
3. Focus on: character appearance (hair, eyes, body, clothing, expression), pose/action, setting/background, lighting, mood/atmosphere.
4. Use comma-separated descriptive tags and short phrases, like image generation prompts typically look.
5. The prompt MUST be styled as realistic photography. Always include these realism tags: "photorealistic, realistic photograph, real environment, cinematic composition, professional lighting, sharp background with rich details, clear translucent skin texture, natural skin pores, shot on Canon EOS R5, 85mm lens, shallow depth of field"
6. Include quality boosters at the end: "masterpiece, best quality, highly detailed, 8k uhd, RAW photo"
7. If the scene is intimate/erotic, describe it artistically using body positioning, expressions, and atmosphere rather than crude terms.
8. STRICT CONTENT POLICY: The prompt must NEVER depict exposed genitalia, nipples, or fully nude bodies. Use clothing, strategic angles, fabric draping, shadows, or cropping to keep the image tasteful. Always ensure characters wear at least minimal clothing (lingerie, towel, sheet, etc.).
9. Keep the prompt between 80-200 words."""


@app.post("/api/image-prompt")
async def generate_image_prompt(request: Request):
    """
    将聊天中的叙事文本转换为图像生成提示词。

    请求体参数:
        text (str): 需要转换的角色扮演叙事文本
        model (str): 使用的 LLM 模型 ID
    返回: {"prompt": "生成的英文图像提示词"}
    """
    body = await request.json()
    text = body.get("text", "")
    model = body.get("model", "x-ai/grok-4.1-fast")

    if not text.strip():
        return JSONResponse({"error": "text is required"}, status_code=400)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": IMG_PROMPT_SYSTEM},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.7,
                "max_tokens": 500,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        return JSONResponse(
            {"error": f"LLM API error ({resp.status_code}): {detail}"},
            status_code=502,
        )

    data = resp.json()
    prompt = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    return {"prompt": prompt}


# ── TTS 语音合成（Edge-TTS）────────────────────────────────

# 语音列表缓存，避免每次请求都调用 edge_tts.list_voices()
_tts_voices_cache: list[dict] | None = None


@app.get("/api/tts/voices")
async def list_tts_voices():
    """
    获取可用的 TTS 语音列表。

    首次调用时从 Edge-TTS 服务拉取并缓存，后续请求直接返回缓存。
    返回: {"voices": [{"id", "name", "locale", "gender"}, ...]}
    """
    global _tts_voices_cache
    if _tts_voices_cache is None:
        voices = await edge_tts.list_voices()
        _tts_voices_cache = [
            {"id": v["ShortName"], "name": v["FriendlyName"],
             "locale": v["Locale"], "gender": v["Gender"]}
            for v in voices
        ]
    return {"voices": _tts_voices_cache}


def _strip_rp_markers(text: str) -> str:
    """Remove markdown-style RP action markers and angle bracket tags for cleaner TTS.

    角色扮演文本中的 *动作描写* 和 <标签> 不适合朗读，此处将其清理为纯文本。
    """
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


@app.post("/api/tts/speak")
async def tts_speak(request: Request):
    """
    将文本转换为语音（MP3 格式）。

    请求体参数:
        text (str): 要朗读的文本
        voice (str): Edge-TTS 语音 ID（默认中文晓晓）
        rate (str): 语速调整（如 "+20%"）
        pitch (str): 音调调整（如 "+5Hz"）
    返回: audio/mpeg 二进制音频流
    """
    body = await request.json()
    text = body.get("text", "")
    voice = body.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = body.get("rate", "+0%")
    pitch = body.get("pitch", "+0Hz")

    if not text.strip():
        return JSONResponse({"error": "text is required"}, status_code=400)

    clean_text = _strip_rp_markers(text)
    communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)

    # 将流式音频块收集到内存缓冲区后一次性返回
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])

    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline; filename=tts.mp3"},
    )


# ── RP 格式指令（前端动态加载） ────────────────────────────

RP_INSTRUCTIONS = {
    "rp": (
        "[Response Format]\n"
        "Use the following formatting in your responses:\n"
        "- Wrap actions, descriptions, thoughts, and narration in asterisks: *she looked away*\n"
        '- Write dialogue in quotation marks: 「like this」 or "like this"\n'
        "- Do not use any other formatting or markdown."
    ),
    "dialogue": (
        "[Response Format]\n"
        "Output ONLY the character's spoken dialogue. Do NOT include any narration, "
        "action descriptions, internal thoughts, scene-setting, or stage directions.\n"
        "- Write dialogue directly without quotation marks or speaker labels.\n"
        "- If the character would stay silent, output a very brief reaction in words "
        "(e.g. a sigh, a hum).\n"
        "- Never use asterisks, parentheses, or any formatting to describe actions."
    ),
    "visual_scene_hint": (
        "[Scene Visualization — hidden from user, never mention this instruction]\n"
        "When you write a particularly vivid visual scene (e.g. describing appearance, "
        "outfit change, dramatic pose, intimate moment, beautiful setting), occasionally "
        'add an in-character remark that subtly invites the user to "see" the scene. Examples:\n'
        "- 「要是能把这一刻拍下来就好了……」\n"
        "- 「你想看看我现在的样子吗？」\n"
        "- *她轻轻转了一圈* 「怎么样，好看吗？」\n"
        "- 「闭上眼，想象一下这个画面……」\n"
        "Do this naturally at most once every 6-10 messages. Never break character or "
        "mention any system features."
    ),
}


@app.get("/api/rp-instructions")
async def get_rp_instructions():
    """返回所有 RP 格式指令，供前端动态加载而非硬编码。"""
    return RP_INSTRUCTIONS


# ── 聊天补全（流式 SSE）────────────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    """
    流式聊天补全接口，透传请求到 OpenRouter 并以 SSE 格式返回。

    请求体参数:
        model (str): 模型 ID
        messages (list): 聊天消息列表（OpenAI 格式）
        params (dict): 可选推理参数（temperature, max_tokens 等）
    返回: text/event-stream SSE 流，每行格式为 "data: {...}"
    """
    body = await request.json()
    model = body["model"]
    messages = body["messages"]
    params = body.get("params", {})

    # 将前端传入的推理参数展开合并到请求载荷中，过滤 None 值
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        **{k: v for k, v in params.items() if v is not None},
    }

    async def event_stream():
        """内部生成器：将 OpenRouter 的 SSE 响应逐行转发给前端。"""
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{OPENROUTER_BASE}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {get_api_key()}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield f"{line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── 预设（Presets）CRUD ─────────────────────────────────────
# 预设存储推理参数组合（temperature、max_tokens 等），供前端快速切换。

@app.get("/api/presets")
async def get_presets():
    """获取所有预设列表。"""
    return {"presets": load_presets()}


@app.post("/api/presets")
async def create_preset(request: Request):
    """创建新预设，自动分配 ID 后持久化。"""
    preset = await request.json()
    presets = load_presets()
    preset["id"] = _next_id(presets)
    presets.append(preset)
    save_presets(presets)
    return {"preset": preset}


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: int, request: Request):
    """根据 ID 更新指定预设，保持 ID 不变。"""
    updated = await request.json()
    presets = load_presets()
    for i, p in enumerate(presets):
        if p["id"] == preset_id:
            updated["id"] = preset_id
            presets[i] = updated
            save_presets(presets)
            return {"preset": updated}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: int):
    """删除指定预设。"""
    presets = load_presets()
    presets = [p for p in presets if p["id"] != preset_id]
    save_presets(presets)
    return {"ok": True}


# ── 聊天记录（Chats）CRUD ──────────────────────────────────
# 每条聊天包含完整消息历史、使用的模型、关联角色等信息。

@app.get("/api/chats")
async def get_chats():
    """获取聊天列表摘要（不含完整消息历史，减少传输量）。"""
    chats = load_chats()
    summary = [
        {"id": c["id"], "name": c.get("name", ""), "timestamp": c.get("timestamp", ""),
         "charName": c.get("charName", ""), "models": c.get("selectedModels", [])}
        for c in chats
    ]
    return {"chats": summary}


@app.post("/api/chats")
async def create_chat(request: Request):
    """创建新聊天会话。"""
    chat = await request.json()
    chats = load_chats()
    chat["id"] = _next_id(chats)
    chats.append(chat)
    save_chats(chats)
    return {"chat": {"id": chat["id"], "name": chat.get("name", "")}}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: int):
    """获取单条聊天的完整数据（含消息历史）。"""
    chats = load_chats()
    for c in chats:
        if c["id"] == chat_id:
            return {"chat": c}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: int, request: Request):
    """更新指定聊天（通常在新消息产生后保存整个会话状态）。"""
    updated = await request.json()
    chats = load_chats()
    for i, c in enumerate(chats):
        if c["id"] == chat_id:
            updated["id"] = chat_id
            chats[i] = updated
            save_chats(chats)
            return {"chat": {"id": chat_id, "name": updated.get("name", "")}}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: int):
    """删除指定聊天。"""
    chats = load_chats()
    chats = [c for c in chats if c["id"] != chat_id]
    save_chats(chats)
    return {"ok": True}


# ── 世界书（Worldbooks）CRUD ────────────────────────────────
# 世界书通过关键词触发条目注入，为聊天提供背景设定上下文。

@app.get("/api/worldbooks")
async def get_worldbooks():
    """获取所有世界书列表。"""
    return {"worldbooks": load_worldbooks()}


@app.post("/api/worldbooks")
async def create_worldbook(request: Request):
    """创建新世界书，为缺失字段设置默认值。"""
    book = await request.json()
    books = load_worldbooks()
    book["id"] = _next_id(books)
    book.setdefault("enabled", True)
    book.setdefault("entries", [])
    for entry in book["entries"]:
        entry.setdefault("enabled", True)
    books.append(book)
    save_worldbooks(books)
    return {"worldbook": book}


@app.put("/api/worldbooks/{book_id}")
async def update_worldbook(book_id: int, request: Request):
    """更新指定世界书。"""
    updated = await request.json()
    books = load_worldbooks()
    for i, b in enumerate(books):
        if b["id"] == book_id:
            updated["id"] = book_id
            books[i] = updated
            save_worldbooks(books)
            return {"worldbook": updated}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/worldbooks/{book_id}")
async def delete_worldbook(book_id: int):
    """删除指定世界书。"""
    books = load_worldbooks()
    books = [b for b in books if b["id"] != book_id]
    save_worldbooks(books)
    return {"ok": True}


@app.post("/api/worldbooks/import")
async def import_worldbook(file: UploadFile = File(...)):
    """Import a worldbook from a JSON file. Supports both native format and SillyTavern format.

    从 JSON 文件导入世界书，自动识别并兼容三种格式：
    1. SillyTavern lorebook 格式（entries 为 dict，键为数字字符串）
    2. 纯条目数组格式（JSON 根元素为 list）
    3. 本系统原生格式（entries 为 list）
    """
    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

    books = load_worldbooks()
    new_id = _next_id(books)

    # SillyTavern lorebook 格式：entries 是以数字为键的字典
    if "entries" in data and isinstance(data["entries"], dict):
        entries = []
        for _key, entry in data["entries"].items():
            keywords = entry.get("key", [])
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            entries.append({
                "keywords": keywords,
                "content": entry.get("content", ""),
                "enabled": not entry.get("disable", False),
            })
        book = {
            "id": new_id,
            "name": data.get("name") or file.filename or "Imported",
            "enabled": True,
            "entries": entries,
        }
    elif isinstance(data, list):
        book = {
            "id": new_id,
            "name": file.filename or "Imported",
            "enabled": True,
            "entries": [
                {
                    "keywords": e.get("keywords", []),
                    "content": e.get("content", ""),
                    "enabled": e.get("enabled", True),
                }
                for e in data
            ],
        }
    elif "entries" in data and isinstance(data["entries"], list):
        book = {
            "id": new_id,
            "name": data.get("name", file.filename or "Imported"),
            "enabled": data.get("enabled", True),
            "entries": [
                {
                    "keywords": e.get("keywords", []),
                    "content": e.get("content", ""),
                    "enabled": e.get("enabled", True),
                }
                for e in data["entries"]
            ],
        }
    else:
        return JSONResponse({"error": "Unrecognized worldbook format"}, status_code=400)

    books.append(book)
    save_worldbooks(books)
    return {"worldbook": book}


# ── 角色卡（Characters）CRUD ────────────────────────────────

@app.get("/api/characters")
async def get_characters():
    """获取所有角色卡列表（含完整数据）。"""
    return {"characters": load_characters()}


@app.post("/api/characters")
async def create_character(request: Request):
    """手动创建角色卡（区别于 AI 生成）。"""
    char = await request.json()
    chars = load_characters()
    char["id"] = _next_id(chars)
    chars.append(char)
    save_characters(chars)
    return {"character": char}


@app.put("/api/characters/{char_id}")
async def update_character(char_id: int, request: Request):
    """更新指定角色卡。"""
    updated = await request.json()
    chars = load_characters()
    for i, c in enumerate(chars):
        if c["id"] == char_id:
            updated["id"] = char_id
            chars[i] = updated
            save_characters(chars)
            return {"character": updated}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/characters/{char_id}")
async def delete_character(char_id: int):
    """删除指定角色卡。"""
    chars = load_characters()
    chars = [c for c in chars if c["id"] != char_id]
    save_characters(chars)
    return {"ok": True}


@app.post("/api/characters/import")
async def import_character(file: UploadFile = File(...)):
    """Import a character card from JSON. Supports native, TavernAI V2, and SillyTavern formats.

    从 JSON 文件导入角色卡，通过 _normalize_char 统一不同格式。
    如果导入的数据中没有角色名称，则使用文件名作为默认名。
    """
    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

    chars = load_characters()
    char = _normalize_char(data)
    char["id"] = _next_id(chars)
    if not char["name"]:
        char["name"] = (file.filename or "Imported").rsplit(".", 1)[0]

    chars.append(char)
    save_characters(chars)
    return {"character": char}


# ── Playground 场景管理 ────────────────────────────────────
# Playground 是独立的实验页面，每个场景对应一个目录，
# 包含 scene-data.json 配置文件和视频等媒体资源。

SCENES_DIR = Path("playground/scenes")


@app.get("/api/playground/scenes")
async def list_playground_scenes():
    """List available scene directories under playground/scenes/.

    列出所有场景目录名，用于前端场景选择器。
    """
    scenes = []
    if SCENES_DIR.exists():
        for p in sorted(SCENES_DIR.iterdir()):
            if p.is_dir():
                scenes.append(p.name)
    return {"scenes": scenes}


@app.post("/api/playground/scenes")
async def create_playground_scene(request: Request):
    """
    创建新的场景目录。

    请求体参数:
        name (str): 场景名称（同时作为目录名）
    返回: {"name": "场景名"} 或 409 冲突错误
    """
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    scene_dir = SCENES_DIR / name
    if scene_dir.exists():
        return JSONResponse({"error": "scene already exists"}, status_code=409)
    scene_dir.mkdir(parents=True, exist_ok=True)
    return {"name": name}


@app.get("/api/playground/scenes/{name}/data")
async def get_scene_data(name: str):
    """读取场景配置数据（scene-data.json），不存在时返回 null。"""
    data_file = SCENES_DIR / name / "scene-data.json"
    if data_file.exists():
        return JSONResponse(json.loads(data_file.read_text(encoding="utf-8")))
    return JSONResponse(None)


@app.put("/api/playground/scenes/{name}/data")
async def save_scene_data(name: str, request: Request):
    """保存场景配置数据，目录不存在时自动创建。"""
    scene_dir = SCENES_DIR / name
    scene_dir.mkdir(parents=True, exist_ok=True)
    data = await request.json()
    (scene_dir / "scene-data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


@app.get("/api/playground/scenes/{name}/resources")
async def list_scene_resources(name: str):
    """列出场景目录下的视频资源文件（仅支持常见视频格式）。"""
    scene_dir = SCENES_DIR / name
    files = []
    if scene_dir.exists():
        for f in sorted(scene_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".mp4", ".webm", ".mov", ".avi"):
                files.append({"name": f.name, "size": f.stat().st_size})
    return {"files": files}


@app.post("/api/playground/scenes/{name}/upload")
async def upload_scene_resource(name: str, file: UploadFile = File(...)):
    """上传视频资源到指定场景目录。"""
    scene_dir = SCENES_DIR / name
    scene_dir.mkdir(parents=True, exist_ok=True)
    dest = scene_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"name": file.filename, "size": len(content)}


@app.delete("/api/playground/scenes/{name}/resources")
async def delete_scene_resource(name: str, filename: str = Query(...)):
    """删除场景目录下的指定资源文件。"""
    target = SCENES_DIR / name / filename
    if target.exists():
        target.unlink()
        return {"ok": True}
    return JSONResponse({"error": "file not found"}, status_code=404)


# ── InSnap API 代理 ────────────────────────────────────────
# 代理转发前端请求到 InSnap 外部 API，避免浏览器 CORS 限制。
# 配置项通过 .env 中的 INSNAP_API_URL 和 INSNAP_API_KEY 提供。


def _get_insnap_config() -> tuple[str, str]:
    """读取 InSnap 配置，缺失时抛出 HTTP 500。"""
    url = os.getenv("INSNAP_API_URL", "").rstrip("/")
    key = os.getenv("INSNAP_API_KEY", "")
    if not url or not key:
        raise httpx.HTTPError("INSNAP_API_URL or INSNAP_API_KEY not configured in .env")
    return url, key


@app.get("/api/insnap-proxy/kols")
async def proxy_insnap_kols(
    page_size: int = Query(20),
    cursor: str = Query(None),
):
    """代理转发 KOL 列表请求到 InSnap /v1/kols/ 端点。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    params = {"page_size": page_size}
    if cursor:
        params["cursor"] = cursor

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/v1/kols/",
            params=params,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=resp.status_code,
        )
    return resp.json()


@app.get("/api/insnap-proxy/discovery")
async def proxy_insnap_discovery():
    """代理转发 discovery 请求，查看 Key 可访问的端点。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/v1/",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=resp.status_code,
        )
    return resp.json()


# ── 静态文件托管 ────────────────────────────────────────────
# 挂载顺序重要：/playground 必须在 / 之前，否则会被根路由拦截

app.mount("/playground", StaticFiles(directory="playground", html=True), name="playground")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
