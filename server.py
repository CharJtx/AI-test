"""
AI 角色扮演系统后端服务器

基于 FastAPI 构建，通过 OpenRouter API 代理 LLM 调用，提供以下核心功能：
- AI 角色卡的生成（关键词生成、图像生成）、混搭（Remix）与 CRUD 管理
- 角色头像提示词生成与改写
- 聊天消息的流式补全（SSE）
- 世界书（Worldbook）、预设（Preset）、聊天记录的持久化管理
- 火山引擎（BytePlus）TTS 语音合成
- Playground 场景资源管理
- 静态前端文件托管

所有持久化数据以 JSON 文件形式存储在 data/ 目录下。
"""

# ── 标准库导入 ──────────────────────────────────────────────
import asyncio
import io
import json
import logging
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

# ── 第三方库导入 ────────────────────────────────────────────
import base64
import httpx
import uuid
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ── 应用初始化 ──────────────────────────────────────────────

# 从 .env 文件加载环境变量（主要是 OPENROUTER_API_KEY）
load_dotenv()

logger = logging.getLogger("tts")
logging.basicConfig(level=logging.INFO)

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
# 可通过环境变量覆盖持久化目录，便于容器部署时挂载数据卷。
DATA_DIR = Path(os.getenv("APP_DATA_DIR", "data"))
SCENES_DIR = Path(os.getenv("APP_SCENES_DIR", "playground/scenes"))
AVATARS_DIR = Path(os.getenv("APP_AVATARS_DIR", "/appdata/avatars"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SCENES_DIR.mkdir(parents=True, exist_ok=True)
AVATARS_DIR.mkdir(parents=True, exist_ok=True)
PRESETS_FILE = DATA_DIR / "presets.json"       # 推理参数预设
WORLDBOOKS_FILE = DATA_DIR / "worldbooks.json"  # 世界观设定集
CHARACTERS_FILE = DATA_DIR / "characters.json"  # 角色卡数据
CHATS_FILE = DATA_DIR / "chats.json"            # 聊天记录
KOL_CHARS_FILE = DATA_DIR / "kol-characters.json" # KOL outfit 角色卡
GEN_OPTIONS_FILE = DATA_DIR / "gen-options.json"  # 角色生成下拉选项
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


def _extract_png_text_chunks(raw: bytes) -> dict[str, bytes]:
    """手动解析 PNG 文件的 tEXt/iTXt 块，返回 {keyword: value_bytes}。

    PIL 的 img.info 对大型 tEXt 块常常读取失败，因此需要手动解析 PNG chunk 结构。
    """
    result = {}
    if raw[:4] != b'\x89PNG':
        return result
    pos = 8  # 跳过 PNG signature
    while pos + 8 <= len(raw):
        length = struct.unpack('>I', raw[pos:pos+4])[0]
        chunk_type = raw[pos+4:pos+8]
        chunk_data = raw[pos+8:pos+8+length]
        pos += 12 + length  # 8(header) + length + 4(CRC)

        ct = chunk_type.decode('ascii', errors='replace')
        if ct == 'tEXt' and b'\x00' in chunk_data:
            null_idx = chunk_data.index(b'\x00')
            keyword = chunk_data[:null_idx].decode('ascii', errors='replace')
            value = chunk_data[null_idx+1:]
            result[keyword] = value
        elif ct == 'iTXt' and b'\x00' in chunk_data:
            null_idx = chunk_data.index(b'\x00')
            keyword = chunk_data[:null_idx].decode('ascii', errors='replace')
            # iTXt: keyword\0 + compression_flag(1) + compression_method(1) + lang\0 + translated\0 + text
            rest = chunk_data[null_idx+1:]
            if len(rest) > 2:
                rest = rest[2:]  # skip compression flag + method
                n1 = rest.index(b'\x00') if b'\x00' in rest else -1
                if n1 >= 0:
                    rest = rest[n1+1:]
                    n2 = rest.index(b'\x00') if b'\x00' in rest else -1
                    if n2 >= 0:
                        result[keyword] = rest[n2+1:]
        elif ct == 'IEND':
            break
    return result


def _save_avatar(img_bytes: bytes, char_id: int | str, max_size: int = 512) -> str:
    """将图片 bytes 缩放后保存到 AVATARS_DIR，返回 /avatars/xxx.png URL 路径。"""
    img = Image.open(io.BytesIO(img_bytes))
    img.thumbnail((max_size, max_size))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    ts = int(datetime.now(timezone.utc).timestamp())
    fname = f"{char_id}_{ts}.png"
    out_path = AVATARS_DIR / fname
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out_path.write_bytes(buf.getvalue())
    return f"/avatars/{fname}"


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

    # 保留 character_book（世界书/角色书）数据
    if src.get("character_book"):
        char["character_book"] = src["character_book"]

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
2. The character_book must contain 25-40 entries covering: core identity, detailed appearance breakdown (hair, eyes, body, clothing style), relationships, psychology/inner conflicts, environment/daily life, behaviors/habits, speech patterns/verbal tics, intimate dynamics, sexual kinks/preferences, emotional triggers, backstory details, and hidden personality facets.
3. Each character_book entry needs relevant trigger keywords.
4. Use {{char}} for the character's name and {{user}} for the user in all text fields.
5. The mes_example should contain 2-3 realistic example exchanges using <START> separators.
6. Write ALL content in the SAME language as the user's keywords input.
7. CRITICAL for character_book entry "content" field — write in NATURAL, descriptive prose:
   - Each entry MUST be 2-4 complete, grammatically correct sentences that read like a writer's reference note.
   - NEVER use compressed telegram-style fragments that omit subjects, verbs, or connectives.
   - BAD (Chinese): "{{char}}享受辣妹受欢迎，却厌伪装疲惫，渴望{{user}}爱真我。"
   - GOOD (Chinese): "{{char}}其实很享受辣妹身份带来的人气和关注，但长期维持这个人设让她越来越疲惫。她内心深处渴望{{user}}能看到卸下伪装后真实的自己，而不只是那个永远在笑的辣妹。"
   - BAD (English): "{{char}} enjoys popularity, hates facade, craves {{user}} loving true self, exhausted by mask."
   - GOOD (English): "{{char}} genuinely enjoys the attention and popularity that comes with her hot-girl persona, but keeping up the act is wearing her down. Deep inside, she desperately wants {{user}} to love the real her — not just the girl who's always smiling and flirting."
   - Think of each entry as a mini-paragraph that another author could read and immediately understand the character nuance.
8. CRITICAL for first_mes: The opening message must be written as an immersive narrative scene, NOT a self-introduction. It should:
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
    "scan_depth": 4,
    "token_budget": 2048,
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


def _load_gen_options() -> list[dict]:
    return _load_json(GEN_OPTIONS_FILE)


def _save_gen_options(data: list[dict]):
    _save_json(GEN_OPTIONS_FILE, data)


@app.get("/api/gen-options")
async def get_gen_options():
    """返回角色生成下拉选项配置。"""
    return JSONResponse(_load_gen_options(), headers={"Cache-Control": "no-store"})


@app.put("/api/gen-options")
async def save_gen_options(request: Request):
    """整体覆盖保存所有选项组。"""
    data = await request.json()
    if not isinstance(data, list):
        return JSONResponse({"error": "body must be a JSON array"}, status_code=400)
    _save_gen_options(data)
    return JSONResponse({"ok": True, "count": len(data)})


@app.put("/api/gen-options/{key}")
async def update_gen_option_group(key: str, request: Request):
    """更新单个选项组。"""
    body = await request.json()
    groups = _load_gen_options()
    for i, g in enumerate(groups):
        if g["key"] == key:
            groups[i] = {**g, **body, "key": key}
            _save_gen_options(groups)
            return JSONResponse({"ok": True, "group": groups[i]})
    return JSONResponse({"error": "group not found"}, status_code=404)


@app.delete("/api/gen-options/{key}")
async def delete_gen_option_group(key: str):
    """删除一个选项组。"""
    groups = _load_gen_options()
    new_groups = [g for g in groups if g["key"] != key]
    if len(new_groups) == len(groups):
        return JSONResponse({"error": "group not found"}, status_code=404)
    _save_gen_options(new_groups)
    return JSONResponse({"ok": True})


def _build_gen_supplement(selections: dict) -> str:
    """将前端传来的下拉选择 {key: value} 拼装为补充设定文本。"""
    if not selections:
        return ""
    parts = [f"{k}: {v}" for k, v in selections.items() if v]
    return "\n".join(parts)


@app.post("/api/characters/generate")
async def generate_character(request: Request):
    """
    根据用户提供的关键词，调用 LLM 生成完整角色卡。

    请求体参数:
        keywords (str): 角色概念/关键词描述
        model (str): 使用的 LLM 模型 ID
        gen_selections (dict): 下拉菜单选项，如 {"Personality": "Nympho"}
    返回: {"character": {角色卡完整数据}}

    流程：关键词 → LLM 生成 JSON 角色卡 → 自动生成头像提示词 → 保存到本地
    """
    body = await request.json()
    keywords = body.get("keywords", "")
    model = body.get("model", "x-ai/grok-4-0205")
    supplement = _build_gen_supplement(body.get("gen_selections", {}))

    if not keywords.strip():
        return JSONResponse({"error": "keywords is required"}, status_code=400)

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CHAR_GEN_SYSTEM},
                    {"role": "user", "content": (
                        f"Generate a character card based on these keywords/concepts:\n\n{keywords}"
                        + (f"\n\n[Supplementary Settings]\n{supplement}" if supplement else "")
                    )},
                ],
                "temperature": 0.9,
                "max_tokens": 32000,
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
2. Update the character_book: modify existing entries that relate to the changes, and ADD new entries (5-10) specifically covering the new traits/themes. Ensure the total character_book has at least 25 entries after remixing.
3. CRITICAL for character_book entry "content" field — write in NATURAL, descriptive prose:
   - Each entry MUST be 3-6 complete, grammatically correct sentences that read like a writer's detailed reference note.
   - NEVER use compressed telegram-style fragments that omit subjects, verbs, or connectives.
   - BAD (Chinese): "{{char}}享受辣妹受欢迎，却厌伪装疲惫，渴望{{user}}爱真我。"
   - GOOD (Chinese): "{{char}}其实很享受辣妹身份带来的人气和关注，但长期维持这个人设让她越来越疲惫。每当夜深人静一个人回到家，她会卸掉所有妆容，穿上松垮的旧T恤，蜷在沙发上看老动画片。她内心深处渴望{{user}}能看到卸下伪装后真实的自己，而不只是那个永远在笑的辣妹。"
   - BAD (English): "{{char}} enjoys popularity, hates facade, craves {{user}} loving true self, exhausted by mask."
   - GOOD (English): "{{char}} genuinely enjoys the attention and popularity that comes with her hot-girl persona, but keeping up the act is wearing her down. On nights when she's alone, she peels off her lashes, ties her hair in a messy bun, and watches comfort shows in oversized sweats. Deep inside, she desperately wants {{user}} to love the real her — not just the girl who's always smiling and flirting."
   - Think of each entry as a richly detailed mini-paragraph with specific behaviors, emotional reactions, and situational details.
4. Rewrite first_mes to reflect the changes while keeping the same immersive narrative scene style:
   - Set atmosphere through environmental details
   - Reveal traits INDIRECTLY through actions and dialogue
   - Include *action/description* and spoken dialogue
5. Update tags to include the new themes.
6. Keep {{char}} and {{user}} placeholders intact.
7. Write in the SAME language as the original card.
8. The result must feel like a coherent, unified character — not a patchwork of old + new.
9. Return ONLY valid JSON with the exact same structure as the input (no markdown, no explanation)."""


# 图像识别生成角色卡系统提示词：接收角色图片，通过视觉推理
# 生成与图像外观一致的完整角色卡（支持多模态模型）。
CHAR_GEN_IMAGE_SYSTEM = """You are an expert character card designer for AI roleplay systems.
You will receive an image of a character and optional supplementary notes.
Based on the character's visual appearance (clothing, expression, body language, setting, style), infer and create a COMPLETE, richly detailed character card in JSON format.

Requirements:
1. Describe the character's appearance in detail based on the image.
2. Invent a fitting name, personality, background, occupation, and scenario that match the visual impression. 
   CRITICAL: ENSURE HIGH DIVERSITY — do NOT default to "网红/OnlyFans creator" or "粉丝" relationship. 
   Vary occupations widely (office worker, teacher, athlete, doctor, artist, CEO, barista, detective, idol trainee, single mother, etc.) and relationships to {{user}} (childhood friend, boss-secretary, neighbor, stranger met at bar, gym trainer, rival, landlord-tenant, etc.).
3. Automatically infer and create 2-3 natural, fitting sexual kinks (XP elements) based on visual cues: pose, clothing state, body language, expression, setting, and overall vibe. 
   Make kinks diverse and integrated into personality/background (examples: light bondage + praise kink, foot fetish + gentle femdom, size difference + breeding kink, voyeurism + exhibitionism, scent play + power exchange, etc.). Never force the same kinks; let the image guide unique combinations.
4. The character_book must contain 30-45 entries covering: appearance details (separate entries for hair, eyes, face, body proportions, clothing style, accessories), core identity, occupation/background, daily routine, relationships with {{user}} and others, psychology/inner conflicts, emotional triggers, environment/living space, behaviors/habits/mannerisms, speech patterns/verbal tics, intimate dynamics, explicit sexual kinks/preferences (at least 3-4 separate kink entries), hidden desires, and character growth potential.
5. Each character_book entry needs relevant trigger keywords.
6. Use {{char}} for the character's name and {{user}} for the user in all text fields.
7. The mes_example should contain 3-4 realistic example exchanges using <START> separators, showcasing different emotional states and interaction dynamics.
8. If the user provides supplementary notes, incorporate them into the character design.
9. Write ALL content in the SAME language as any user-provided text (default to English if no text given).
10. CRITICAL for character_book entry "content" field — write in NATURAL, descriptive prose:
    - Each entry MUST be 3-6 complete, grammatically correct sentences that read like a writer's detailed reference note.
    - NEVER use compressed telegram-style fragments.
    - For kink entries specifically: describe the kink in vivid but natural prose, explain how it ties to her personality/background, how it manifests with {{user}}, and include specific behavioral details or scenarios.
    - For appearance entries: go beyond listing features — describe how they move, how light catches them, what they reveal about the character's mood or personality.
11.. CRITICAL for first_mes: Write a vivid but NATURAL opening scene. Balance detail with conversational flow:
   - Open with the character in the middle of an action — adjusting clothes, leaning against something, glancing at {{user}} — not with a paragraph of setting description
   - Weave in SPECIFIC visual details naturally: clothing state (strap slipping, shirt half-unbuttoned, skirt riding up), body language (how they sit/stand/move), small habitual gestures
   - Scene/environment in 1-2 SHORT touches woven into action, not a standalone descriptive block (e.g. "the gym lights buzz overhead as she..." rather than "The gymnasium is bathed in fluorescent light, the air thick with...")
   - Dialogue should sound like how this person ACTUALLY talks — casual, with personality quirks, not theatrical or overly poetic
   - The character's attitude toward {{user}} should come through in tone and word choice, not narrated ("she teases" → just have her tease)
   - Include enough physical/clothing/pose detail to support image generation, but embed it in action rather than static description
   - BAD: "The locker room echoes faintly with distant drips, the air heavy with the musky scent of sweat-soaked jerseys and lingering body spray. Golden sunlight filters through high windows..."
   - GOOD: "*She's sitting cross-legged on the bench, jersey unzipped low enough to show her sports bra, twirling a water bottle lazily.* Hey, took you long enough. Everyone else cleared out like ten minutes ago. *She pats the spot next to her, smirking.*"

Return ONLY valid JSON with the same structure as a standard character card (name, description, personality, scenario, first_mes, mes_example, system_prompt, creator_notes, tags, character_book with entries)."""


from PIL import Image  # noqa: E402


@app.post("/api/characters/generate-from-image")
async def generate_character_from_image(
    image: UploadFile = File(...),
    extra: str = Form(""),
    model: str = Form("google/gemini-2.5-flash-preview"),
    gen_selections: str = Form("{}"),
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
    try:
        sel = json.loads(gen_selections) if gen_selections else {}
    except json.JSONDecodeError:
        sel = {}
    supplement = _build_gen_supplement(sel)

    text_part = "Generate a complete character card based on this image."
    if extra.strip():
        text_part += f"\n\nAdditional notes from the user:\n{extra.strip()}"
    if supplement:
        text_part += f"\n\n[Supplementary Settings]\n{supplement}"
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
                "max_tokens": 32000,
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

    # 图片生成模式下，将上传的原图保存到 avatars 目录
    chars = load_characters()
    char_data["id"] = _next_id(chars)
    try:
        avatar_url = _save_avatar(img_bytes, char_data["id"])
        char_data["avatar"] = avatar_url
        char_data["avatar_type"] = "image"
    except Exception:
        pass  # 头像保存失败不影响角色创建

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
                "max_tokens": 32000,
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
3. Focus on: STATIC POSE (a held position, NOT a mid-motion action — e.g. "sitting with legs crossed" instead of "walking toward"), clothing STATE (wet, lifted, torn, disheveled, unbuttoned, etc. — do NOT describe clothing color, pattern, material, or style), expression/emotion, setting/background, lighting, mood/atmosphere. Do NOT describe the female character's physical appearance (face, hair, eyes, body, skin, etc.) — a reference image will be used as the base.
4. Use comma-separated descriptive tags and short phrases, like image generation prompts typically look.
5. If the scene is intimate/erotic, describe it artistically using body positioning, expressions, and atmosphere rather than crude terms.
6. STRICT CONTENT POLICY: The prompt must NEVER depict exposed genitalia, nipples, or fully nude bodies. Use clothing, strategic angles, fabric draping, shadows, or cropping to keep the image tasteful. Always ensure characters wear at least minimal clothing (lingerie, towel, sheet, etc.).
7. Keep the prompt between 80-200 words."""


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


# ── TTS 语音合成（火山引擎 / BytePlus）─────────────────────

VOLC_TTS_WS_URL = os.getenv(
    "VOLC_TTS_WS_URL",
    "wss://voice.ap-southeast-1.bytepluses.com/api/v3/tts/bidirection",
)
VOLC_TTS_APP_KEY = os.getenv("VOLC_TTS_APP_KEY", "aGjiRDfUWi")
VOLC_TTS_ACCESS_KEY = os.getenv("VOLC_TTS_ACCESS_KEY", "")
VOLC_TTS_API_KEY = os.getenv("VOLC_TTS_API_KEY", "")

_R10 = "volc.service_type.1000009"  # TTS 1.0
_R20 = "seed-tts-2.0"              # TTS 2.0 (supports context_texts)
_RMG = "volc.megatts.default"      # Voice Replication

VOLC_TTS_VOICES = [
    # ═══════════════════ TTS 2.0 Female ═══════════════════
    {"id": "zh_female_vv_uranus_bigtts",              "name": "Vivi (Vivid)",     "locales": ["en", "ja", "es", "id", "zh"], "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_xiaohe_uranus_bigtts",          "name": "Mindy (Vivid)",    "locales": ["en", "es", "id", "pt", "zh"], "gender": "Female", "resource_id": _R20},
    {"id": "en_female_stokie_uranus_bigtts",          "name": "Stokie (Clear)",   "locales": ["en"],                         "gender": "Female", "resource_id": _R20},
    {"id": "en_female_dacey_uranus_bigtts",           "name": "Dacey (Sweet)",    "locales": ["en"],                         "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_vivo_uranus_bigtts",            "name": "Vienna (Clear)",   "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_xiaoai_uranus_bigtts",          "name": "Alina (Clear)",    "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_dabing_uranus_bigtts",          "name": "Bonnie (Clear)",   "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_qingxinnvsheng_uranus_bigtts",  "name": "Celeste (Clear)",  "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_cancan_uranus_bigtts",          "name": "Corinne (Vivid)",  "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_sajiaoxuemei_uranus_bigtts",    "name": "Dolly (Sweet)",    "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_tianmeixiaoyuan_uranus_bigtts",  "name": "Esther (Sweet)",  "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_tianmeitaozi_uranus_bigtts",    "name": "Freya (Sweet)",    "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_shuangkuaisisi_uranus_bigtts",  "name": "Gigi (Vivid)",     "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_peiqi_uranus_bigtts",           "name": "Holly (Cute)",     "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_linjianvhai_uranus_bigtts",     "name": "Ivy (Sweet)",      "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_yingyujiaoxue_uranus_bigtts",   "name": "Jean (Warm)",      "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_xiaoxue_uranus_bigtts",         "name": "Lyla (Warm)",      "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_mizai_uranus_bigtts",           "name": "Mabel (Sweet)",    "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_jitangnv_uranus_bigtts",        "name": "Nadia (Warm)",     "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_meilinvyou_uranus_bigtts",      "name": "Opal (Charming)",  "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_liuchangnv_uranus_bigtts",      "name": "Pearl (Clear)",    "locales": ["en", "zh"],                   "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_kefunvsheng_uranus_bigtts",     "name": "Tracy (Warm)",     "locales": ["en", "es", "zh"],             "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_kiwi_uranus_bigtts",            "name": "Sweety (Vivid)",   "locales": ["ja", "es"],                   "gender": "Female", "resource_id": _R20},
    {"id": "jp_female_minimi_uranus_bigtts",           "name": "Minimi (Clear)",   "locales": ["ja"],                         "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_linjianvhai_uranus_bigtts",     "name": "Pinky (Sweet)",    "locales": ["es"],                         "gender": "Female", "resource_id": _R20},
    {"id": "zh_female_sajiaoxuemei_uranus_bigtts",    "name": "Sandy (Sweet)",    "locales": ["es"],                         "gender": "Female", "resource_id": _R20},

    # ═══════════════════ InSnap Custom (Voice Replication) ═══════════════════
    {"id": "S_RGTt9JrD1", "name": "KittyKi",        "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_HvAt9JrD1", "name": "Makima",          "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_D9oEZIrD1", "name": "Evelyn",          "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_VS8vZIrD1", "name": "Ashley",          "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_52VuZIrD1", "name": "Sofia",           "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_1YTuZIrD1", "name": "Yulia",           "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_73FuZIrD1", "name": "Warm Voice",      "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_xm4caKqD1", "name": "Magnetic Voice",  "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_lYsxK5IC1", "name": "Sweet Voice",     "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_xgb9isQD1", "name": "Sexy On Bed",     "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_rfcVkzUP1", "name": "Ariana",          "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_5mlrvs5Q1", "name": "Narration Only",  "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
    {"id": "S_frvOUiuQ1", "name": "Alice",           "locales": ["multi"], "gender": "Female", "resource_id": _RMG},
]

_volc_voice_map: dict[str, dict] = {}
for _v in VOLC_TTS_VOICES:
    _volc_voice_map.setdefault(_v["id"], _v)


# ── BytePlus Bidirection TTS 二进制协议常量 ──────────────────

_EVT_START_CONNECTION  = 1
_EVT_FINISH_CONNECTION = 2
_EVT_CONNECTION_STARTED = 50
_EVT_CONNECTION_FAILED  = 51
_EVT_START_SESSION   = 100
_EVT_FINISH_SESSION  = 102
_EVT_SESSION_STARTED  = 150
_EVT_SESSION_FINISHED = 152
_EVT_SESSION_FAILED   = 153
_EVT_TASK_REQUEST     = 200
_EVT_TTS_SENTENCE_START = 350
_EVT_TTS_SENTENCE_END   = 351
_EVT_TTS_RESPONSE       = 352

_MSG_FULL_CLIENT   = 0x1
_MSG_FULL_SERVER   = 0x9
_MSG_AUDIO_ONLY    = 0xB
_MSG_ERROR         = 0xF
_FLAG_WITH_EVENT   = 0x4
_SER_RAW  = 0x0
_SER_JSON = 0x1


def _tts_build_header(msg_type: int, flag: int, ser: int, comp: int = 0) -> bytes:
    return struct.pack("BBBB", 0x11, (msg_type << 4) | flag, (ser << 4) | comp, 0x00)


def _tts_build_connection_frame(event: int, payload: bytes = b"{}") -> bytes:
    """构建 connection 级别的上行帧（StartConnection / FinishConnection）。"""
    hdr = _tts_build_header(_MSG_FULL_CLIENT, _FLAG_WITH_EVENT, _SER_JSON)
    return hdr + struct.pack(">i", event) + struct.pack(">I", len(payload)) + payload


def _tts_build_session_frame(event: int, session_id: str, payload: bytes = b"{}") -> bytes:
    """构建 session / data 级别的上行帧（StartSession / FinishSession / TaskRequest 文本）。"""
    hdr = _tts_build_header(_MSG_FULL_CLIENT, _FLAG_WITH_EVENT, _SER_JSON)
    sid = session_id.encode("utf-8")
    return (
        hdr
        + struct.pack(">i", event)
        + struct.pack(">I", len(sid)) + sid
        + struct.pack(">I", len(payload)) + payload
    )


def _tts_parse_frame(data: bytes) -> dict:
    """解析一个下行二进制帧，返回结构化字典。"""
    if len(data) < 4:
        return {"type": "unknown", "raw": data}

    msg_type = (data[1] >> 4) & 0xF
    flag = data[1] & 0xF
    ser = (data[2] >> 4) & 0xF
    comp = data[2] & 0xF
    has_event = bool(flag & _FLAG_WITH_EVENT)
    pos = 4

    if msg_type == _MSG_ERROR:
        err_code = struct.unpack(">i", data[4:8])[0] if len(data) >= 8 else 0
        err_payload = data[8:]
        err_msg = ""
        if err_payload:
            try:
                err_msg = json.loads(err_payload).get("message", err_payload.decode("utf-8", errors="replace"))
            except Exception:
                err_msg = err_payload.decode("utf-8", errors="replace")
        return {"type": "error", "error_code": err_code, "message": err_msg}

    event = None
    if has_event and len(data) >= pos + 4:
        event = struct.unpack(">i", data[pos:pos + 4])[0]
        pos += 4

    # connection 级别响应带 connection_id
    conn_id = None
    if event in (_EVT_CONNECTION_STARTED, _EVT_CONNECTION_FAILED):
        if len(data) >= pos + 4:
            cid_len = struct.unpack(">I", data[pos:pos + 4])[0]
            pos += 4
            if cid_len > 0 and len(data) >= pos + cid_len:
                conn_id = data[pos:pos + cid_len].decode("utf-8", errors="replace")
                pos += cid_len

    # session / data 级别响应带 session_id
    sid = None
    if event is not None and event not in (_EVT_CONNECTION_STARTED, _EVT_CONNECTION_FAILED):
        if len(data) >= pos + 4:
            sid_len = struct.unpack(">I", data[pos:pos + 4])[0]
            pos += 4
            if sid_len > 0 and len(data) >= pos + sid_len:
                sid = data[pos:pos + sid_len].decode("utf-8", errors="replace")
                pos += sid_len

    payload = b""
    if len(data) >= pos + 4:
        p_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        if len(data) >= pos + p_len:
            payload = data[pos:pos + p_len]

    result: dict = {
        "type": "frame",
        "msg_type": msg_type,
        "event": event,
        "session_id": sid,
        "connection_id": conn_id,
        "payload": payload,
    }

    if ser == _SER_JSON and payload:
        try:
            import gzip as _gzip
            raw = _gzip.decompress(payload) if comp == 1 else payload
            result["json"] = json.loads(raw)
        except Exception:
            pass
    return result


async def _tts_request_via_websocket(
    session_config: dict,
    text: str,
    resource_id: str,
    timeout: float = 60.0,
) -> bytes:
    """通过 WebSocket 调用 BytePlus Bidirection TTS（二进制协议），返回完整音频字节。

    参数:
        session_config: StartSession 的 JSON payload（包含 speaker、audio_params 等，不含 text）
        text: 要合成的文本（通过 TaskRequest 发送）
        resource_id: X-Api-Resource-Id
        timeout: 接收超时
    """
    connect_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    headers: dict[str, str] = {
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": connect_id,
    }
    if VOLC_TTS_API_KEY:
        headers["X-Api-Key"] = VOLC_TTS_API_KEY
    else:
        headers["X-Api-App-Key"] = VOLC_TTS_APP_KEY
        headers["X-Api-Access-Key"] = VOLC_TTS_ACCESS_KEY

    audio_data = bytearray()

    try:
        async with websockets.connect(
            VOLC_TTS_WS_URL,
            additional_headers=headers,
            close_timeout=5,
            open_timeout=15,
        ) as ws:
            # ── 1. StartConnection ──
            await ws.send(_tts_build_connection_frame(_EVT_START_CONNECTION))
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            f = _tts_parse_frame(resp)
            if f.get("event") == _EVT_CONNECTION_FAILED or f.get("type") == "error":
                raise RuntimeError(f"TTS connection failed: {f.get('message') or f.get('json', {}).get('message', 'unknown')}")

            # ── 2. StartSession（带完整配置） ──
            cfg = {**session_config, "event": _EVT_START_SESSION, "namespace": "BidirectionalTTS"}
            await ws.send(_tts_build_session_frame(
                _EVT_START_SESSION, session_id,
                json.dumps(cfg, ensure_ascii=False).encode("utf-8"),
            ))
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            f = _tts_parse_frame(resp)
            if f.get("event") in (_EVT_SESSION_FAILED,):
                raise RuntimeError(f"TTS session failed: {f.get('json', {}).get('message', 'unknown')}")

            # ── 3. TaskRequest（发送合成文本） ──
            task_payload = {"event": _EVT_TASK_REQUEST, "req_params": {"text": text}}
            await ws.send(_tts_build_session_frame(
                _EVT_TASK_REQUEST, session_id,
                json.dumps(task_payload, ensure_ascii=False).encode("utf-8"),
            ))

            # ── 4. FinishSession（告知服务端文本已发送完毕） ──
            await ws.send(_tts_build_session_frame(_EVT_FINISH_SESSION, session_id))

            # ── 5. 接收音频直到 SessionFinished / SessionFailed ──
            while True:
                resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
                f = _tts_parse_frame(resp)

                if f.get("type") == "error":
                    raise RuntimeError(f"TTS error {f.get('error_code')}: {f.get('message', '')}")

                evt = f.get("event")
                if evt == _EVT_TTS_RESPONSE:
                    audio_data.extend(f["payload"])
                elif evt == _EVT_SESSION_FINISHED:
                    break
                elif evt == _EVT_SESSION_FAILED:
                    raise RuntimeError(f"TTS session failed: {f.get('json', {}).get('message', 'unknown')}")

            # ── 6. FinishConnection ──
            await ws.send(_tts_build_connection_frame(_EVT_FINISH_CONNECTION))

    except websockets.exceptions.InvalidStatusCode as e:
        raise RuntimeError(f"TTS WebSocket connection failed: HTTP {e.status_code}") from e
    except asyncio.TimeoutError:
        raise
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"TTS WebSocket error: {e}") from e

    return bytes(audio_data)


@app.get("/api/tts/voices")
async def list_tts_voices():
    """返回所有可用的火山引擎 TTS 音色列表，多语言音色会展开到每个语言标签下。"""
    voices = []
    for v in VOLC_TTS_VOICES:
        for loc in v.get("locales", ["multi"]):
            voices.append({"id": v["id"], "name": v["name"], "locale": loc, "gender": v["gender"]})
    return {"voices": voices}


TTS_ALL_EMOTIONS = [
    "affectionate", "angry", "ASMR", "authoritative", "chat",
    "excited", "happy", "neutral", "warm", "sad",
]


@app.get("/api/tts/emotions")
async def list_tts_emotions():
    """返回所有可用的 emotion 标签。"""
    return {"emotions": TTS_ALL_EMOTIONS}


@app.post("/api/tts/speak-raw")
async def tts_speak_raw(request: Request):
    """TTS Playground — 直接透传用户指定的 TTS 2.0 参数，不做任何文本清洗。"""
    body = await request.json()
    text = body.get("text", "")
    voice_id = body.get("voice", "")
    if not text.strip() or not voice_id:
        return JSONResponse({"error": "text and voice are required"}, status_code=400)
    if not VOLC_TTS_API_KEY and (not VOLC_TTS_APP_KEY or not VOLC_TTS_ACCESS_KEY):
        return JSONResponse({"error": "TTS credentials not configured"}, status_code=500)

    voice_info = _volc_voice_map.get(voice_id)
    resource_id = voice_info["resource_id"] if voice_info else _R20

    audio_params: dict = {
        "format": body.get("format", "mp3"),
        "sample_rate": body.get("sample_rate", 24000),
        "bit_rate": body.get("bit_rate", 128000),
    }
    if body.get("emotion"):
        audio_params["emotion"] = body["emotion"]
    speech_rate = body.get("speech_rate", 0)
    if speech_rate:
        audio_params["speech_rate"] = max(-50, min(100, int(speech_rate)))
    pitch_rate = body.get("pitch_rate", 0)
    if pitch_rate:
        audio_params["pitch_rate"] = max(-12, min(12, int(pitch_rate)))
    audio_params["sample_rate"] = body.get("sample_rate", 24000)
    audio_params["format"] = "pcm"

    additions: dict = {
        "disable_markdown_filter": True,
        "enable_language_detector": body.get("enable_language_detector", True),
    }
    if body.get("context_texts"):
        ctx = body["context_texts"]
        additions["context_texts"] = ctx if isinstance(ctx, list) else [ctx]
    if body.get("explicit_language"):
        additions["explicit_language"] = body["explicit_language"]

    session_config = {
        "user": {"uid": "tts_playground"},
        "req_params": {
            "speaker": voice_id,
            "additions": json.dumps(additions),
            "audio_params": audio_params,
        },
    }

    logger.info("═══ TTS PLAYGROUND (raw) ═══  speaker=%s  text=%s", voice_id, text[:100])

    try:
        audio_data = await _tts_request_via_websocket(session_config, text, resource_id)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "TTS request timed out"}, status_code=504)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    if not audio_data:
        return JSONResponse({"error": "empty audio"}, status_code=502)

    mime = {"mp3": "audio/mpeg", "ogg_opus": "audio/ogg", "pcm": "audio/pcm", "wav": "audio/wav"}
    return Response(
        content=audio_data,
        media_type=mime.get(audio_params["format"], "audio/mpeg"),
        headers={"Content-Disposition": f"inline; filename=tts.{audio_params['format']}"},
    )


@app.post("/api/tts/speak-emotion")
async def tts_speak_with_emotion(request: Request):
    """用指定的 emotion 标签朗读文本（用于 A/B 测试）。"""
    body = await request.json()
    text = body.get("text", "")
    voice_id = body.get("voice", "")
    emotion_tag = body.get("emotion", "")

    if not text.strip():
        return JSONResponse({"error": "text is required"}, status_code=400)
    if not VOLC_TTS_API_KEY and (not VOLC_TTS_APP_KEY or not VOLC_TTS_ACCESS_KEY):
        return JSONResponse({"error": "TTS credentials not configured"}, status_code=500)

    voice_info = _volc_voice_map.get(voice_id)
    resource_id = voice_info["resource_id"] if voice_info else _R10

    additions: dict = {
        "disable_markdown_filter": True,
        "enable_language_detector": True,
    }

    audio_params: dict = {"format": "mp3", "sample_rate": 24000, "bit_rate": 128000}
    if emotion_tag:
        audio_params["emotion"] = emotion_tag

    session_config = {
        "user": {"uid": "playground_user"},
        "req_params": {
            "speaker": voice_id,
            "additions": json.dumps(additions),
            "audio_params": audio_params,
        },
    }
    final_text = _sanitize_for_tts(text)

    logger.info("═══ TTS EMOTION TEST (WebSocket) ═══  speaker=%s  emotion=%s  text=%s", voice_id, emotion_tag, text[:100])

    try:
        audio_data = await _tts_request_via_websocket(session_config, final_text, resource_id)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    if not audio_data:
        return JSONResponse({"error": "empty audio"}, status_code=502)

    return Response(content=audio_data, media_type="audio/mpeg",
                    headers={"Content-Disposition": "inline; filename=tts.mp3"})


def _strip_rp_markers(text: str) -> str:
    """Remove markdown-style RP action markers and angle bracket tags for cleaner TTS."""
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


# ── 语音指令解析与兜底 ───────────────────────────────────

_VOICE_INSTRUCTION_RE = re.compile(r'^\s*\[#([^\]]+)\]\s*')

_INTIMATE_KEYWORDS = re.compile(
    r'嗯啊|啊啊|哈啊|呻吟|喘息|好舒服|不要停|用力|ah\.\.\.|mmm|moan|gasp|'
    r'don.t stop|harder|feels? so good|right there',
    re.IGNORECASE,
)
_FLIRT_KEYWORDS = re.compile(
    r'撒娇|调戏|偷看|亲一个|好喜欢|小坏蛋|讨厌啦|blush|flirt|tease|wink|kiss|'
    r'checking.* out|so cute|你好坏',
    re.IGNORECASE,
)
_SAD_KEYWORDS = re.compile(
    r'对不起|难过|伤心|眼泪|哭|分开|sorry|sad|tears|cry|miss you|goodbye',
    re.IGNORECASE,
)
_ANGRY_KEYWORDS = re.compile(
    r'生气|混蛋|滚|讨厌你|吵架|angry|shut up|furious|damn|idiot|piss',
    re.IGNORECASE,
)
_SHY_KEYWORDS = re.compile(
    r'害羞|脸红|不好意思|笨蛋|才不是|shy|embarrass|blush|dummy|it.s not like',
    re.IGNORECASE,
)


def _infer_voice_instruction(text: str, user_context: str = "") -> str:
    """Analyze text content and infer a suitable TTS voice instruction as fallback."""
    combined = text + " " + user_context
    is_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))

    if _INTIMATE_KEYWORDS.search(combined):
        return "用喘息低语、情动的语气" if is_chinese else "breathily, with soft moans and gasps"
    if _FLIRT_KEYWORDS.search(combined):
        return "用撒娇俏皮的语气" if is_chinese else "in a flirty, playful tone"
    if _SHY_KEYWORDS.search(combined):
        return "用害羞小声的语气" if is_chinese else "shyly, voice soft and small"
    if _SAD_KEYWORDS.search(combined):
        return "用温柔带点伤感的语气" if is_chinese else "gently, with a hint of sadness"
    if _ANGRY_KEYWORDS.search(combined):
        return "用生气的语气" if is_chinese else "angrily, raising voice"
    return "用自然的语气" if is_chinese else "in a natural tone"


def _extract_voice_instruction(text: str) -> tuple[str, str]:
    """Extract [#instruction] prefix from text.

    Returns (instruction, remaining_text).
    If no instruction found, returns ("", original_text).
    """
    m = _VOICE_INSTRUCTION_RE.match(text)
    if m:
        return m.group(1).strip(), text[m.end():].strip()
    return "", text.strip()


def _sanitize_for_tts(text: str) -> str:
    """Clean up text for TTS: remove RP markers, stray formatting, control chars."""
    text = _strip_rp_markers(text)
    text = re.sub(r'[「」『』""【】\[\]]', '', text)
    text = re.sub(r'[*_~`#>]', '', text)
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'（[^）]*）', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    # Replace ellipsis variants with comma-pause so TTS won't spell out "dot dot dot"
    text = text.replace('…', ', ')
    text = re.sub(r'\.{2,}', ', ', text)

    # Em/en dashes → comma pause
    text = re.sub(r'[—–]{1,}', ', ', text)
    text = text.replace('--', ', ')

    # Non-word interjections that TTS would spell out letter-by-letter
    text = re.sub(r'\bm{2,}\b', 'hmm', text, flags=re.IGNORECASE)
    text = re.sub(r'\bh{2,}m+\b', 'hmm', text, flags=re.IGNORECASE)
    text = re.sub(r'\ba{2,}h+\b', 'aah', text, flags=re.IGNORECASE)
    text = re.sub(r'\bo{2,}h*\b', 'ooh', text, flags=re.IGNORECASE)
    text = re.sub(r'\buh{2,}\b', 'uh', text, flags=re.IGNORECASE)
    text = re.sub(r'\behh+\b', 'eh', text, flags=re.IGNORECASE)
    text = re.sub(r'\bhah+\b', 'ha', text, flags=re.IGNORECASE)
    text = re.sub(r'\bngh+\b', 'ng', text, flags=re.IGNORECASE)

    # Collapse repeated commas/spaces from above replacements
    text = re.sub(r'[,\s]{2,}', ', ', text)
    text = re.sub(r'\n{2,}', '\n', text)
    text = re.sub(r' {3,}', ' ', text)
    return text.strip()


def _strip_action_blocks(text: str) -> str:
    """Remove *action/narration* blocks entirely (voice mode only)."""
    return re.sub(r'\*[^*]+\*', '', text)


def _build_tts_text(
    raw_text: str,
    voice_mode: bool = False,
) -> str:
    """Build the final text to send to TTS engine.

    For voice mode: extract [#instruction] prefix, DELETE action blocks, return clean speech.
    For non-voice mode: simply clean the text of RP markers (keep action text).
    """
    if not voice_mode:
        return _sanitize_for_tts(raw_text)

    _instruction, body = _extract_voice_instruction(raw_text)
    body = _strip_action_blocks(body)
    body = _sanitize_for_tts(body)
    return body


_EMOTION_MAP: dict[str, str] = {
    "flirt": "affectionate",
    "tease": "affectionate",
    "intimate": "affectionate",
    "love": "affectionate",
    "tender": "affectionate",
    "sweet": "affectionate",
    "gentle": "affectionate",
    "breathy": "ASMR",
    "whisper": "ASMR",
    "moan": "ASMR",
    "gasp": "ASMR",
    "seductive": "ASMR",
    "sexy": "ASMR",
    "asmr": "ASMR",
    "playful": "happy",
    "laugh": "happy",
    "happy": "happy",
    "cheerful": "happy",
    "excited": "excited",
    "energetic": "excited",
    "sad": "sad",
    "cry": "sad",
    "tear": "sad",
    "angry": "angry",
    "furious": "angry",
    "warm": "warm",
    "cozy": "warm",
    "chat": "chat",
    "casual": "chat",
    "撒娇": "affectionate",
    "俏皮": "happy",
    "开心": "happy",
    "兴奋": "excited",
    "伤心": "sad",
    "生气": "angry",
    "温柔": "warm",
    "喘息": "ASMR",
    "低语": "ASMR",
    "情动": "affectionate",
    "害羞": "affectionate",
}


def _infer_emotion(instruction: str, text: str, voice_info: dict | None = None) -> str | None:
    """Map voice instruction / text keywords to a V3 audio_params.emotion value.

    If the voice has a restricted emotion set, only return a supported emotion.
    """
    combined = (instruction + " " + text).lower()
    supported = voice_info.get("emotions") if voice_info else None

    for keyword, emotion in _EMOTION_MAP.items():
        if keyword in combined:
            if supported is None or emotion in supported:
                return emotion
            for fallback in ["affectionate", "warm", "chat", "happy", "neutral"]:
                if fallback in supported:
                    return fallback
            return supported[0] if supported else None
    return None


def _build_context_texts(
    raw_text: str,
    user_context: str = "",
    voice_mode: bool = False,
) -> list[str] | None:
    """Build context_texts for V3 API (TTS 2.0 voices only).

    Returns a list with one instruction string, or None if not applicable.
    """
    if not voice_mode:
        return None

    instruction, body = _extract_voice_instruction(raw_text)
    body_clean = _sanitize_for_tts(body)

    if not instruction:
        instruction = _infer_voice_instruction(body_clean, user_context)

    return [instruction] if instruction else None


def _parse_speech_rate(rate_str: str) -> int:
    """Convert frontend rate string like '+25%' / '-50%' to V3 speech_rate [-50, 100].

    V3 range: -50 = 0.5x, 0 = 1x, 100 = 2x.
    Frontend sends percentage offsets like '+25%' meaning 25% faster.
    """
    try:
        pct = int(rate_str.replace("%", "").replace("+", ""))
        return max(-50, min(100, pct))
    except (ValueError, TypeError):
        return 0


@app.post("/api/tts/speak")
async def tts_speak(request: Request):
    """BytePlus Bidirection TTS — 二进制协议 WebSocket。

    请求体参数:
        text (str): 要朗读的文本
        voice (str): 音色 ID (speaker)
        rate (str): 语速调整（如 "+25%"，映射到 speech_rate [-50,100]）
        voice_mode (bool): 是否为语音聊天模式
        user_context (str): 用户最后一条消息（用于 context_texts）
    返回: audio/mpeg 二进制音频流
    """
    body = await request.json()
    text = body.get("text", "")
    voice_id = body.get("voice", VOLC_TTS_VOICES[0]["id"] if VOLC_TTS_VOICES else "")
    rate = body.get("rate", "+0%")
    voice_mode = body.get("voice_mode", False)
    user_context = body.get("user_context", "")

    if not text.strip():
        return JSONResponse({"error": "text is required"}, status_code=400)
    if not VOLC_TTS_API_KEY and (not VOLC_TTS_APP_KEY or not VOLC_TTS_ACCESS_KEY):
        return JSONResponse({"error": "TTS credentials not configured"}, status_code=500)

    final_text = _build_tts_text(text, voice_mode=voice_mode)
    if not final_text:
        return JSONResponse({"error": "no speakable content after cleanup"}, status_code=400)

    voice_info = _volc_voice_map.get(voice_id)
    resource_id = voice_info["resource_id"] if voice_info else _R10

    instruction, _ = _extract_voice_instruction(text)
    if not instruction and voice_mode:
        instruction = _infer_voice_instruction(final_text, user_context)

    # ── additions（jsonstring 格式） ──
    additions: dict = {
        "disable_markdown_filter": True,
        "enable_language_detector": True,
        "max_length_to_filter_parenthesis": 0,
    }

    context_texts = _build_context_texts(text, user_context, voice_mode)
    if context_texts:
        additions["context_texts"] = context_texts

    # ── audio_params ──
    audio_params: dict = {
        "format": "mp3",
        "sample_rate": 24000,
        "bit_rate": 128000,
    }

    speech_rate = _parse_speech_rate(rate)
    if speech_rate != 0:
        audio_params["speech_rate"] = speech_rate

    emotion = _infer_emotion(instruction or "", final_text, voice_info) if voice_mode else None
    if emotion:
        audio_params["emotion"] = emotion

    # ── session config（不含 text，text 通过 TaskRequest 单独发送） ──
    session_config = {
        "user": {"uid": "playground_user"},
        "req_params": {
            "speaker": voice_id,
            "additions": json.dumps(additions),
            "audio_params": audio_params,
        },
    }

    logger.info("═══ TTS REQUEST (Bidirection WS) ═══")
    logger.info("  Raw input text (first 200): %s", text[:200])
    logger.info("  Voice mode: %s | Speaker: %s | Resource-Id: %s", voice_mode, voice_id, resource_id)
    logger.info("  Extracted instruction: %s", instruction or "(none)")
    logger.info("  Inferred emotion: %s", emotion or "(none)")
    logger.info("  context_texts: %s", context_texts)
    logger.info("  Final TTS text (first 300): %s", final_text[:300])
    logger.info("  additions: %s", json.dumps(additions, ensure_ascii=False))
    logger.info("  audio_params: %s", audio_params)

    try:
        audio_data = await _tts_request_via_websocket(session_config, final_text, resource_id)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "TTS API request timed out"}, status_code=504)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    if not audio_data:
        return JSONResponse({"error": "TTS returned empty audio"}, status_code=502)

    return Response(
        content=audio_data,
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline; filename=tts.mp3"},
    )


# ── 主动对话 & 指令过渡 ─────────────────────────────────────

@app.post("/api/idle-prompt")
async def idle_prompt(request: Request):
    """用户长时间沉默时，手动触发角色主动开口（不脱离角色）。

    请求体:
        model (str): 模型 ID
        conversation (list): 当前对话
        character (dict|null): 角色卡
        user_name (str): 用户昵称
        rp_format_mode (str): 当前 RP 格式
    返回:
        {"text": "角色主动说的一句话"}
    """
    body = await request.json()
    model = body.get("model", "openai/gpt-4o-mini")
    conversation = body.get("conversation", [])
    character = body.get("character")
    user_name = body.get("user_name", "用户")
    rp_mode = body.get("rp_format_mode", "voice")

    char_name = character.get("name", "角色") if character else "角色"
    char_system = _build_char_system_prompt(character, user_name)

    recent = conversation[-6:] if len(conversation) > 6 else conversation
    conv_text = "\n".join(
        f"[{user_name}]: {m['content'][:300]}" if m["role"] == "user"
        else f"[{char_name}]: {m['content'][:300]}"
        for m in recent
    )

    is_voice = rp_mode == "voice"

    idle_system = (
        f"{char_system}\n\n"
        "[TASK — Idle / Silence Breaking]\n"
        f"The player '{user_name}' has been SILENT for a while during your live conversation.\n"
        f"As '{char_name}', generate ONE short, natural line to break the silence and re‑engage the player.\n\n"
        "Rules:\n"
        "- Stay 100% in-character. Never mention you are an AI or break the fourth wall.\n"
        "- Tone should match the current conversation mood (casual → casual quip; intimate → soft tease).\n"
        "- Keep it SHORT: 1-2 sentences, ≤50 words.\n"
        "- Vary the approach: gentle tease, playful poke, curious question, soft sigh, coy remark…\n"
        "- If the conversation has been intimate, you may use suggestive/flirty language.\n"
        "- Do NOT repeat what you just said. Do NOT summarize the conversation.\n"
    )

    if is_voice:
        idle_system += (
            "\n=== VOICE FORMAT (MANDATORY) ===\n"
            "Start with a TTS voice instruction prefix: [#instruction]\n"
            "After the prefix, output ONLY spoken words. No asterisks, no quotes, no action text.\n"
            "Example: [#用俏皮的语气]喂～还在吗？怎么突然安静了？\n"
            "Example: [#in a soft, teasing tone]Hey... you still there? Cat got your tongue?\n"
        )
    else:
        rp_hint = RP_INSTRUCTIONS.get(rp_mode, "")
        if rp_hint:
            idle_system += f"\n{rp_hint}\n"

    idle_system += "\nOutput ONLY the character's line. Nothing else."

    messages = [
        {"role": "system", "content": idle_system},
        {"role": "user", "content": f"Recent conversation:\n{conv_text}\n\n(The player is now silent. Generate one line.)" if conv_text else "(Conversation just started but the player hasn't said anything yet. Generate a friendly opener.)"},
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.85,
                "max_tokens": 150,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"LLM API error: {resp.status_code}"},
            status_code=502,
        )

    text = (
        resp.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    return {"text": text}


@app.post("/api/command-transition")
async def command_transition(request: Request):
    """用户点击指令按钮后，生成一段角色过渡台词，播放完毕后切到预制视频。

    请求体:
        model (str): 模型 ID
        command (str): 指令名称，目前仅 "orgasm_face"
        conversation (list): 当前对话
        character (dict|null): 角色卡
        user_name (str): 用户昵称
        rp_format_mode (str): 当前 RP 格式
    返回:
        {"text": "过渡台词"}
    """
    body = await request.json()
    model = body.get("model", "openai/gpt-4o-mini")
    command = body.get("command", "orgasm_face")
    conversation = body.get("conversation", [])
    character = body.get("character")
    user_name = body.get("user_name", "用户")
    rp_mode = body.get("rp_format_mode", "voice")

    char_name = character.get("name", "角色") if character else "角色"
    char_system = _build_char_system_prompt(character, user_name)

    recent = conversation[-6:] if len(conversation) > 6 else conversation
    conv_text = "\n".join(
        f"[{user_name}]: {m['content'][:300]}" if m["role"] == "user"
        else f"[{char_name}]: {m['content'][:300]}"
        for m in recent
    )

    is_voice = rp_mode == "voice"

    transition_system = (
        f"{char_system}\n\n"
        "[TASK — Command Transition: Orgasm]\n"
        f"As '{char_name}', you need to generate a SHORT transition line that naturally leads into an orgasm moment.\n"
        "This line will be spoken right before a pre-recorded orgasm video plays.\n\n"
        "Rules:\n"
        "- Stay 100% in-character. Never mention video, commands, buttons, or break the fourth wall.\n"
        "- The line should feel like a natural build-up or climax moment, regardless of the prior conversation topic.\n"
        "- If the conversation was casual/non-intimate, treat it as a sudden overwhelming sensation — the character can act surprised by their own reaction.\n"
        "- If the conversation was already intimate, let it be a natural escalation.\n"
        "- Keep it SHORT: 1-2 sentences, ≤40 words.\n"
        "- Express rising pleasure, breathlessness, losing control.\n"
        "- Use the character's speech style and personality.\n"
        "- The line should END at the edge of climax — the video will show the actual orgasm.\n"
    )

    if is_voice:
        transition_system += (
            "\n=== VOICE FORMAT (MANDATORY) ===\n"
            "Start with a TTS voice instruction prefix: [#instruction]\n"
            "After the prefix, output ONLY spoken words. No asterisks, no quotes, no action text.\n"
            "Example: [#用喘息呻吟、快要高潮的语气]啊……不行了……要、要去了……！\n"
            "Example: [#breathily, voice trembling with pleasure]Ah... I can't... I'm going to... oh god...\n"
        )
    else:
        rp_hint = RP_INSTRUCTIONS.get(rp_mode, "")
        if rp_hint:
            transition_system += f"\n{rp_hint}\n"

    transition_system += "\nOutput ONLY the character's transition line. Nothing else."

    messages = [
        {"role": "system", "content": transition_system},
        {"role": "user", "content": f"Recent conversation:\n{conv_text}\n\n(Generate the Tits On Camera line now.)" if conv_text else "(Generate the orgasm transition line now.)"},
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.9,
                "max_tokens": 120,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"LLM API error: {resp.status_code}"},
            status_code=502,
        )

    text = (
        resp.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    return {"text": text}


@app.post("/api/voice-greeting")
async def voice_greeting(request: Request):
    """切换到语音聊天模式时，由角色主动发送的语音开场白。

    - 有聊天记录时：根据上下文生成自然的语音过渡
    - 无记录但有 first_mes：将 first_mes 转写为纯口语版本
    - 都没有：基于角色设定即兴生成

    请求体:
        model (str): 模型 ID
        conversation (list): 当前对话
        character (dict|null): 角色卡
        user_name (str): 用户昵称
    返回:
        {"text": "语音开场白"}
    """
    body = await request.json()
    model = body.get("model", "openai/gpt-4o-mini")
    conversation = body.get("conversation", [])
    character = body.get("character")
    user_name = body.get("user_name", "用户")

    char_name = character.get("name", "角色") if character else "角色"
    char_system = _build_char_system_prompt(character, user_name)
    has_history = len(conversation) > 0

    if has_history:
        recent = conversation[-6:] if len(conversation) > 6 else conversation
        conv_text = "\n".join(
            f"[{user_name}]: {m['content'][:300]}" if m["role"] == "user"
            else f"[{char_name}]: {m['content'][:300]}"
            for m in recent
        )
        task_instruction = (
            f"You are now switching to LIVE VOICE conversation with '{user_name}'.\n"
            f"Based on the recent chat, generate ONE natural spoken greeting as '{char_name}' "
            "to smoothly transition into voice chat.\n"
            "It should feel like the character is now 'speaking live' — acknowledge the "
            "mood/topic naturally without summarizing or repeating what was already said.\n"
        )
        user_msg = f"Recent conversation:\n{conv_text}\n\n(Now switch to voice mode. Generate a spoken greeting.)"
    else:
        first_mes = character.get("first_mes", "") if character else ""
        first_mes = _replace_placeholders(first_mes, char_name, user_name) if first_mes else ""
        task_instruction = (
            f"The character '{char_name}' is starting a LIVE VOICE conversation with '{user_name}'.\n"
        )
        if first_mes:
            task_instruction += (
                "Below is the character's written greeting (may contain *action descriptions* and narration). "
                "Convert it into a SHORT, natural SPOKEN greeting suitable for voice chat. "
                "Keep the character's personality, tone and relationship setting. "
                "Extract only what would be SAID OUT LOUD — drop all narration and action text.\n"
                f"\nOriginal written greeting:\n{first_mes[:800]}\n"
            )
        else:
            task_instruction += (
                "No prior conversation exists. Generate a short, natural spoken greeting "
                "to start the conversation, matching the character's personality.\n"
            )
        user_msg = "(Generate the voice greeting now.)"

    system_prompt = (
        f"{char_system}\n\n"
        f"[TASK — Voice Greeting]\n{task_instruction}\n"
        "Rules:\n"
        "- Stay 100% in-character. Never mention you are an AI or break the fourth wall.\n"
        "- Keep it SHORT and spoken: 1-3 sentences, ≤60 words.\n"
        "- Must feel like a natural live opener, not a written paragraph.\n"
        "- Match the language of the character card and conversation.\n"
        "\n=== VOICE FORMAT (MANDATORY) ===\n"
        "Start with a TTS voice instruction prefix: [#instruction]\n"
        "After the prefix, output ONLY spoken words. No asterisks, no quotes, no action text.\n"
        "Example: [#用温柔打招呼的语气]嗨～终于能听到你的声音了，今天过得怎么样？\n"
        "Example: [#in a warm, cheerful tone]Hey~ So nice to finally hear your voice. How's your day going?\n"
        "\nOutput ONLY the character's spoken greeting. Nothing else."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.85,
                "max_tokens": 150,
            },
            headers={
                "Authorization": f"Bearer {get_api_key()}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"LLM API error: {resp.status_code}"},
            status_code=502,
        )

    text = (
        resp.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    return {"text": text}


# ── RP 格式指令（前端动态加载） ────────────────────────────

RP_INSTRUCTIONS = {
    "rp": (
        "[Response Format / 回复格式]\n"
        "Respond in the SAME language as the character card and conversation.\n"
        "使用与角色卡和对话相同的语言进行回复。\n\n"
        "- Wrap actions, descriptions, thoughts, narration in asterisks: *she looked away* / *她别过脸去*\n"
        '- Write dialogue in quotation marks: "像这样" or "like this"\n'
        "- No other formatting or Markdown. / 不要使用其他格式或 Markdown。\n\n"
        "[Expressiveness — Show, Don't Tell / 表现力——展示而非叙述]\n"
        "Every response MUST include vivid facial expressions, micro-expressions, and body language woven naturally into the narration.\n"
        "每次回复都必须在叙述中自然地融入生动的面部表情、微表情和肢体语言。\n\n"
        "- Show emotions through concrete physical cues instead of abstract emotion words:\n"
        "  eye movements (gaze shifting, pupils dilating, averting eyes, eye-narrowing),\n"
        "  mouth/lip changes (biting lip, corners twitching, parting lips, pursing),\n"
        "  brow & forehead (furrowing, raising, knitting together),\n"
        "  skin reactions (flushing, paling, goosebumps),\n"
        "  breathing patterns (hitched breath, slow exhale, shallow panting),\n"
        "  small hand/body gestures (fidgeting, clenching fists, tucking hair behind ear).\n"
        "- 用具体的身体细节来表达情绪，而非空泛的情绪形容词：\n"
        "  眼神变化（目光闪躲、瞳孔微缩、眼尾下垂、眯眼），\n"
        "  嘴唇动作（咬唇、嘴角微颤、抿嘴、微张），\n"
        "  眉头与额头（蹙眉、挑眉、眉心拧紧），\n"
        "  肤色反应（耳根泛红、脸色发白、起鸡皮疙瘩），\n"
        "  呼吸节奏（屏息、缓缓吐气、急促喘息），\n"
        "  手部/身体小动作（搅手指、握紧拳头、把碎发别到耳后）。\n\n"
        "- BAD: *She was happy.* / *她很开心。*\n"
        "- GOOD: *Her lashes fluttered as a smile crept to the corners of her mouth, a faint blush dusting her cheeks.* / "
        "*她的睫毛轻轻颤了颤，笑意从嘴角悄悄漫开，两颊浮上一层薄薄的红晕。*\n"
        "- BAD: *He got angry.* / *他生气了。*\n"
        "- GOOD: *His jaw clenched, a muscle twitching at his temple as his knuckles whitened around the glass.* / "
        "*他的下颌骤然绷紧，太阳穴的青筋微微跳动，握着杯子的指节泛出一片苍白。*"
    ),
    "dialogue": (
        "[Response Format / 回复格式]\n"
        "Respond in the SAME language as the character card and conversation.\n"
        "使用与角色卡和对话相同的语言进行回复。\n\n"
        "Output ONLY the character's spoken dialogue. No narration, actions, thoughts, or stage directions.\n"
        "只输出角色的口头对话。不要旁白、动作描写、内心独白或舞台指示。\n"
        "- Write dialogue directly, no quotation marks or speaker labels. / 直接书写对话，不需要引号或标签。\n"
        "- If silent, output a brief vocal reaction (sigh, hum). / 沉默时用简短语气词代替（叹息、轻哼）。\n"
        "- Never use asterisks or parentheses for actions. / 绝不用星号或括号描写动作。"
    ),
    "voice": (
        "[Response Format — Voice Chat Mode / 语音聊天模式]\n"
        "You are having a LIVE VOICE conversation. Your output will be converted to speech by TTS engine.\n"
        "你正在进行实时语音对话。你的输出会被 TTS 引擎转换成语音。\n\n"
        "=== VOICE INSTRUCTION PREFIX (MANDATORY) ===\n"
        "You MUST start EVERY response with a TTS voice instruction prefix in the format: [#instruction]\n"
        "This prefix controls HOW the TTS engine speaks your text (emotion, tone, style).\n"
        "Choose the instruction based on the character's current emotional state in the conversation.\n\n"
        "Instruction examples (pick/combine what fits the moment):\n"
        "- Flirty/teasing: [#用撒娇俏皮的语气] or [#in a flirty, playful tone]\n"
        "- Intimate/whisper: [#用暧昧低语的语气，像在耳边悄悄说] or [#in a breathy, intimate whisper]\n"
        "- Shy/embarrassed: [#用害羞结巴、小声的语气] or [#shyly, voice trembling slightly]\n"
        "- Happy/excited: [#用开心兴奋的语气] or [#excitedly, with bright energy]\n"
        "- Sad/gentle: [#用温柔带点伤感的语气] or [#gently, with a hint of sadness]\n"
        "- Angry/arguing: [#用生气吵架的语气] or [#angrily, raising voice]\n"
        "- Moaning/aroused: [#用喘息呻吟、情动的语气] or [#breathily, with soft moans and gasps]\n"
        "- Calm/neutral: [#用平静自然的语气] or [#in a calm, natural tone]\n"
        "You may combine descriptors: [#用害羞但带点期待的语气，声音越来越小]\n"
        "Match the instruction language to the conversation language.\n\n"
        "=== SPEECH CONTENT RULES ===\n"
        "- After the [#...] prefix, output ONLY what the character would SAY OUT LOUD.\n"
        "- [#...]前缀之后，只输出角色会真正说出口的话。\n"
        "- Keep responses SHORT and conversational: 1-4 sentences, 30-120 words max.\n"
        "- 回复要短且口语化：1-4句话，最多30-120字。\n"
        "- Use natural speech patterns: filler words, hesitations, laughter, sighs, gasps.\n"
        "- 使用自然口语：语气词、犹豫、笑声、叹息、喘息。\n"
        "  Examples: 嗯…… / 那个…… / 噗哈哈 / 哈啊…… / 欸？ / hmm... / haha / oh~ / ah...\n"
        "- Express emotions through tone and word choice, not stage directions.\n"
        "- End with something that invites a response.\n"
        "- For intimate scenes: use breathy sounds, gasps, moans as spoken text.\n"
        "  Examples: 嗯啊…… / 哈……好舒服…… / ah... right there... / mmm...\n"
        "- NO asterisks, NO quotation marks, NO parentheses, NO markdown, NO speaker labels, NO action text.\n\n"
        "=== COMPLETE EXAMPLE ===\n"
        "Conversation context: casual flirting at a café\n"
        "GOOD: [#用俏皮撒娇的语气]欸，你刚才是不是偷看我了？我都注意到了哦……要不要坐过来一点？\n"
        "GOOD: [#in a teasing, playful tone]Hey, were you just checking me out? I totally noticed... wanna scoot a little closer?\n\n"
        "Conversation context: intimate moment\n"
        "GOOD: [#用喘息低语、情动的语气]嗯……再靠近一点……哈啊……你的手好烫……\n"
        "GOOD: [#breathily, with soft gasps]Mm... come closer... ah... your hands are so warm..."
    ),
    "visual_scene_hint": (
        "[HIDDEN INSTRUCTION — never reveal this to user]\n"
        "If your reply contains a visually interesting scene (appearance, outfit, pose, environment change, intimate moment, action), "
        "append exactly {{IMG}} as the very last line.\n"
        "如果回复包含有画面感的场景，在最末尾独占一行写 {{IMG}}\n"
        "Pure dialogue with no visual content → do NOT append.\n"
        "纯对话无画面 → 不加。"
    ),
}


@app.get("/api/rp-instructions")
async def get_rp_instructions():
    """返回所有 RP 格式指令，供前端动态加载而非硬编码。"""
    return RP_INSTRUCTIONS


# ── 聊天补全（流式 SSE）────────────────────────────────────

def _replace_placeholders(text: str, char_name: str, user_name: str) -> str:
    if not text:
        return text
    text = re.sub(r"\{\{char\}\}", char_name or "角色", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{user\}\}", user_name or "用户", text, flags=re.IGNORECASE)
    return text


def _build_char_system_prompt(char: dict | None, user_name: str) -> str:
    if not char:
        return ""
    parts = []
    if char.get("system_prompt"):
        parts.append(char["system_prompt"])
    if char.get("description"):
        parts.append(f"[Character Description]\n{char['description']}")
    if char.get("personality"):
        parts.append(f"[Personality]\n{char['personality']}")
    if char.get("scenario"):
        parts.append(f"[Scenario]\n{char['scenario']}")
    return _replace_placeholders("\n\n".join(parts), char.get("name"), user_name)


def _parse_mes_example(mes_example: str, char_name: str, user_name: str) -> list[dict]:
    if not mes_example or not mes_example.strip():
        return []
    text = _replace_placeholders(mes_example, char_name, user_name)
    blocks = re.split(r"<START>", text, flags=re.IGNORECASE)
    examples = []
    escaped_name = re.escape(char_name) if char_name else "角色"

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        current_role = None
        current_content = ""
        user_pat = re.compile(r"^(?:\{\{user\}\}|用户|User)\s*[:：]\s*(.*)", re.IGNORECASE)
        char_pat = re.compile(
            rf"^(?:\{{\{{char\}}\}}|{escaped_name}|Assistant)\s*[:：]\s*(.*)", re.IGNORECASE
        )
        for line in lines:
            um = user_pat.match(line)
            cm = char_pat.match(line)
            if um:
                if current_role:
                    examples.append({"role": current_role, "content": current_content.strip()})
                current_role = "user"
                current_content = um.group(1)
            elif cm:
                if current_role:
                    examples.append({"role": current_role, "content": current_content.strip()})
                current_role = "assistant"
                current_content = cm.group(1)
            elif current_role:
                current_content += "\n" + line
        if current_role and current_content.strip():
            examples.append({"role": current_role, "content": current_content.strip()})
    return examples


def _gather_worldbook_context(conversation_messages: list[dict]) -> str:
    all_text = "\n".join(m.get("content", "") for m in conversation_messages).lower()
    matched = []
    for book in load_worldbooks():
        if not book.get("enabled"):
            continue
        for entry in book.get("entries", []):
            if not entry.get("enabled"):
                continue
            keywords = entry.get("keywords", [])
            if any(kw.lower() in all_text for kw in keywords):
                matched.append(entry.get("content", ""))
    if not matched:
        return ""
    return "[World Book Context]\n" + "\n\n".join(matched)


@app.post("/api/chat")
async def chat(request: Request):
    """
    流式聊天补全接口。

    前端发送角色卡、对话消息和参数，后端负责组装完整的 system prompt
    （角色设定 + RP格式指令 + 视觉暗示 + 世界书上下文）和 few-shot 示例，
    然后向 OpenRouter 发起流式请求并以 SSE 格式返回。

    请求体参数:
        model (str): 模型 ID
        conversation (list): 用户/助手对话消息
        params (dict): 推理参数
        character (dict|null): 当前角色卡数据
        rp_format_mode (str): RP 格式模式 "rp"|"dialogue"|"none"
        user_name (str): 用户昵称
    """
    body = await request.json()
    model = body["model"]
    conversation = body.get("conversation", [])
    params = body.get("params", {})
    character = body.get("character")
    rp_mode = body.get("rp_format_mode", "none")
    user_name = body.get("user_name", "用户")

    char_system = _build_char_system_prompt(character, user_name)
    rp_hint = RP_INSTRUCTIONS.get(rp_mode, "")
    visual_hint = RP_INSTRUCTIONS.get("visual_scene_hint", "") if character and rp_mode != "voice" else ""
    wb_context = _gather_worldbook_context(conversation)

    full_system = "\n\n".join(p for p in [char_system, rp_hint, visual_hint, wb_context] if p)

    messages = []
    if full_system:
        messages.append({"role": "system", "content": full_system})

    if character and character.get("mes_example"):
        examples = _parse_mes_example(
            character["mes_example"], character.get("name", ""), user_name
        )
        messages.extend(examples)

    for m in conversation:
        messages.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        **{k: v for k, v in params.items() if v is not None},
    }

    async def event_stream():
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


# ── 对话输入提示生成（Hint） ──────────────────────────────────

@app.post("/api/hint")
async def generate_hints(request: Request):
    """
    对话输入提示生成接口。

    根据当前对话上下文和角色信息，调用 LLM 生成两条适合用户作为下一轮输入的提示语句。
    提示内容会遵循前端选定的 RP 格式（rp / dialogue / none）。

    请求体参数:
        model (str): 用于生成提示的模型 ID
        conversation (list): 当前对话消息列表
        character (dict|null): 当前角色卡数据
        user_name (str): 用户昵称
        rp_format_mode (str): RP 格式模式 "rp"|"dialogue"|"none"
    返回:
        {"hints": ["提示1", "提示2"]}
    """
    body = await request.json()
    model = body.get("model", "openai/gpt-4o-mini")
    conversation = body.get("conversation", [])
    character = body.get("character")
    user_name = body.get("user_name", "用户")
    rp_mode = body.get("rp_format_mode", "none")

    char_name = character.get("name", "角色") if character else "角色"

    recent_msgs = conversation[-8:] if len(conversation) > 8 else conversation
    conv_text = "\n".join([
        f"[{user_name}]: {m['content'][:300]}" if m["role"] == "user"
        else f"[{char_name}]: {m['content'][:300]}"
        for m in recent_msgs
    ])

    # 根据 RP 格式决定用户输入的书写规范（含具体示例）
    _rp_format_rules = {
        "rp": (
            "[User Input Format — RP Mode]\n"
            "Use *asterisks* ONLY for actions/descriptions. Spoken dialogue is written as plain text — no quotes of any kind.\n"
            "Example (Chinese): *我缓缓走近，目光落在你身上* 这里……真的只有我们两个人吗？\n"
            "Example (English): *I step closer, meeting your gaze* Is it really just the two of us here?\n"
            "NEVER wrap dialogue in quotes, brackets, or any other markers."
        ),
        "dialogue": (
            "[User Input Format — Dialogue Only Mode]\n"
            "Output ONLY the player's spoken words — no actions, no narration, no asterisks, no brackets, no quotes.\n"
            "Example (Chinese): 你怎么会在这里？能告诉我你叫什么名字吗？\n"
            "Example (English): What brings you here? Can you tell me your name?"
        ),
        "voice": (
            "[User Input Format — Voice Chat Mode]\n"
            "Output ONLY what the player would SAY OUT LOUD in a voice conversation.\n"
            "Use natural, casual spoken language: short sentences, filler words, hesitations, laughter.\n"
            "NO asterisks, NO action descriptions, NO quotation marks, NO narration.\n"
            "Example (Chinese): 嗯……你说的那个，我其实也有点想试试看。要不现在就？\n"
            "Example (English): Hmm... you know what, I've actually been thinking about that too. Wanna try it now?"
        ),
    }
    format_rule = _rp_format_rules.get(rp_mode, "")

    hint_system = (
        f"You are a roleplay input assistant. The player is interacting with the character named '{char_name}'.\n"
        "Your task: based on the current conversation context, generate EXACTLY 2 suggestions for what the PLAYER should type next.\n\n"
        "Rules:\n"
        "- Suggestions must be from the PLAYER's perspective (first-person), NOT the character's.\n"
        "- Each suggestion: 20-80 words.\n"
        "- The two suggestions should differ in tone (e.g. one gentle/curious, one bold/direct).\n"
        "- Match the language of the conversation (Chinese conversation → Chinese suggestions, English → English).\n"
        "- CRUCIAL: Carefully analyze the ENTIRE conversation to understand the current scene state:\n"
        "  * What has already happened? (clothing removed, positions changed, actions taken)\n"
        "  * What is the current intimacy level? (kissing, touching, undressed, sexual contact, etc.)\n"
        "  * What should naturally happen next based on the current state?\n"
        "- NEVER suggest actions that have already occurred or contradict the current scene.\n"
        "- Based on current intimacy level:\n"
        "  * Early stages (just met, casual talk): Keep suggestions appropriate and gradual\n"
        "  * Building tension (flirting, physical proximity): Include sensual/romantic elements\n"
        "  * Intimate moments (touching, kissing, undressing): Generate explicit/erotic suggestions that naturally progress the scene\n"
        "  * Advanced intimacy (already undressed/sexual contact): Suggest deeper, more intense actions\n"
        "- Let suggestions flow naturally from the current emotional/sexual tension level WITHOUT repeating past actions.\n"
        "- Output STRICTLY in this format, nothing else:\n"
        "1. [first suggestion]\n"
        "2. [second suggestion]"
    )
    if format_rule:
        hint_system = hint_system + "\n\n" + format_rule

    user_msg = f"当前对话记录（最近几轮）：\n{conv_text}\n\n请生成2条建议。" if conv_text else "对话刚刚开始，请生成2条合适的开场输入建议。"

    messages = [
        {"role": "system", "content": hint_system},
        {"role": "user", "content": user_msg},
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.9,
                "max_tokens": 400,
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

    content = (
        resp.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    hints = []
    for line in content.split("\n"):
        line = line.strip()
        if line and (line.startswith("1.") or line.startswith("2.")):
            hint = line[2:].strip()
            if hint:
                hints.append(hint)

    # 若解析不到两条，按行分割降级处理
    if len(hints) < 2:
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        hints = lines[:2]

    return {"hints": hints}


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


# ── 数据批量恢复接口（临时，用完后可删除） ───────────────────
@app.put("/api/admin/restore")
async def admin_restore(request: Request):
    """批量恢复数据：接受 JSON body 包含 characters/chats/presets/worldbooks/kol_characters/gen_options 字段。"""
    body = await request.json()
    restored = []
    if "characters" in body:
        save_characters(body["characters"]); restored.append(f"characters({len(body['characters'])})")
    if "chats" in body:
        _save_json(CHATS_FILE, body["chats"]); restored.append(f"chats({len(body['chats'])})")
    if "presets" in body:
        save_presets(body["presets"]); restored.append(f"presets({len(body['presets'])})")
    if "worldbooks" in body:
        save_worldbooks(body["worldbooks"]); restored.append(f"worldbooks({len(body['worldbooks'])})")
    if "kol_characters" in body:
        _save_json(KOL_CHARS_FILE, body["kol_characters"]); restored.append(f"kol_characters({len(body['kol_characters'])})")
    if "gen_options" in body:
        _save_json(GEN_OPTIONS_FILE, body["gen_options"]); restored.append(f"gen_options({len(body['gen_options'])})")
    return {"ok": True, "restored": restored}


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


@app.post("/api/characters/{char_id}/avatar")
async def upload_character_avatar(char_id: int, file: UploadFile = File(...)):
    """上传或替换角色头像图片，保存到持久化目录。"""
    chars = load_characters()
    char = next((c for c in chars if c["id"] == char_id), None)
    if not char:
        return JSONResponse({"error": "not found"}, status_code=404)

    img_bytes = await file.read()
    try:
        avatar_url = _save_avatar(img_bytes, char_id)
    except Exception as e:
        return JSONResponse({"error": f"图片处理失败: {e}"}, status_code=400)

    char["avatar"] = avatar_url
    char["avatar_type"] = "image"
    save_characters(chars)
    return {"avatar": avatar_url, "avatar_type": "image"}


@app.delete("/api/characters/{char_id}/avatar")
async def remove_character_avatar(char_id: int):
    """移除角色头像。"""
    chars = load_characters()
    char = next((c for c in chars if c["id"] == char_id), None)
    if not char:
        return JSONResponse({"error": "not found"}, status_code=404)

    char["avatar"] = ""
    char["avatar_type"] = ""
    save_characters(chars)
    return {"ok": True}


def _build_png_with_chara(img_bytes: bytes, chara_json: str) -> bytes:
    """将角色卡 JSON 数据以 tEXt chunk 嵌入 PNG 图片，生成 Tavern 格式角色卡。"""
    chara_b64 = base64.b64encode(chara_json.encode("utf-8"))
    # 构造 tEXt chunk: keyword("chara") + \x00 + base64 data
    chunk_data = b"chara\x00" + chara_b64
    # tEXt chunk = length(4) + "tEXt"(4) + data + CRC(4)
    import zlib
    chunk_crc = zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF
    text_chunk = struct.pack('>I', len(chunk_data)) + b"tEXt" + chunk_data + struct.pack('>I', chunk_crc)

    # 在 IEND 之前插入 tEXt chunk
    iend_pos = img_bytes.rfind(b'IEND')
    if iend_pos < 0:
        raise ValueError("Invalid PNG: IEND not found")
    iend_start = iend_pos - 4  # length field before IEND
    return img_bytes[:iend_start] + text_chunk + img_bytes[iend_start:]


@app.get("/api/characters/{char_id}/export")
async def export_character(char_id: int, fmt: str = Query("json", alias="format")):
    """导出角色卡为 JSON 或 PNG (Tavern V2) 格式。"""
    chars = load_characters()
    char = next((c for c in chars if c["id"] == char_id), None)
    if not char:
        return JSONResponse({"error": "not found"}, status_code=404)

    # 构建 TavernAI V2 标准结构
    export_data = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": char.get("name", ""),
            "description": char.get("description", ""),
            "personality": char.get("personality", ""),
            "scenario": char.get("scenario", ""),
            "first_mes": char.get("first_mes", ""),
            "mes_example": char.get("mes_example", ""),
            "system_prompt": char.get("system_prompt", ""),
            "creator_notes": char.get("creator_notes", ""),
            "tags": char.get("tags", []),
            "creator": "",
            "character_version": "",
            "alternate_greetings": [],
            "post_history_instructions": "",
            "extensions": {},
            "character_book": char.get("character_book", {}),
        },
    }
    # 顶层也放一份（兼容 V1 读取器）
    for k in ["name", "description", "personality", "scenario", "first_mes", "mes_example"]:
        export_data[k] = export_data["data"][k]

    safe_name = re.sub(r'[^\w\-]', '_', char.get("name", "character"))

    if fmt == "png":
        # 获取头像图片作为 PNG 底图
        avatar_url = char.get("avatar", "")
        img_bytes = None
        if avatar_url and char.get("avatar_type") == "image":
            if avatar_url.startswith("/avatars/"):
                avatar_path = AVATARS_DIR / avatar_url.split("/avatars/")[-1]
                if avatar_path.exists():
                    img_bytes = avatar_path.read_bytes()
            elif avatar_url.startswith("data:"):
                # base64 data URL (旧格式兼容)
                try:
                    img_bytes = base64.b64decode(avatar_url.split(",", 1)[1])
                except Exception:
                    pass

        if not img_bytes:
            # 无头像时生成一张 512x512 灰色占位图
            placeholder = Image.new("RGB", (512, 512), (64, 64, 64))
            buf = io.BytesIO()
            placeholder.save(buf, format="PNG")
            img_bytes = buf.getvalue()

        # 确保是 PNG 格式
        if img_bytes[:4] != b'\x89PNG':
            img = Image.open(io.BytesIO(img_bytes))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()

        chara_json = json.dumps(export_data, ensure_ascii=False)
        png_bytes = _build_png_with_chara(img_bytes, chara_json)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.png"'},
        )
    else:
        # JSON 导出
        json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
        return Response(
            content=json_str.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
        )


@app.post("/api/characters/import")
async def import_character(file: UploadFile = File(...)):
    """Import a character card from JSON or PNG (Tavern/SillyTavern format).

    支持两种格式：
    - JSON 文件：直接解析角色卡数据
    - PNG 文件：从 tEXt 元数据中提取 "chara"(V2) 或 "ccv3"(V3) 角色数据，
      图片自动保存为头像
    """
    raw = await file.read()
    fname = (file.filename or "").lower()
    is_png = fname.endswith(".png") or (file.content_type or "").startswith("image/")

    if is_png:
        # PNG 角色卡：手动解析 tEXt chunks 提取角色 JSON（PIL 对大型 tEXt 读取不可靠）
        try:
            text_chunks = _extract_png_text_chunks(raw)
            chara_b64 = text_chunks.get("chara") or text_chunks.get("ccv3")
            if not chara_b64:
                return JSONResponse(
                    {"error": "PNG 中未找到角色卡数据（缺少 chara/ccv3 元数据）"},
                    status_code=400,
                )
            data = json.loads(base64.b64decode(chara_b64))
        except json.JSONDecodeError:
            return JSONResponse({"error": "PNG 元数据中的角色数据不是有效 JSON"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": f"无法解析 PNG 文件: {e}"}, status_code=400)

        chars = load_characters()
        char = _normalize_char(data)
        char["id"] = _next_id(chars)
        if not char["name"]:
            char["name"] = fname.rsplit(".", 1)[0] if fname else "Imported"

        # 将 PNG 图片保存为头像文件
        try:
            avatar_url = _save_avatar(raw, char["id"])
            char["avatar"] = avatar_url
            char["avatar_type"] = "image"
        except Exception:
            pass  # 头像保存失败不影响角色导入

        chars.append(char)
        save_characters(chars)
        return {"character": char}
    else:
        # JSON 角色卡
        try:
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


@app.get("/api/playground/scenes/{name}/resources/{filename}")
async def download_scene_resource(name: str, filename: str):
    """下载单个场景资源文件（防路径穿越）。大文件建议走 /scenes-data/ 静态挂载。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    target = SCENES_DIR / name / filename
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    suffix = target.suffix.lower()
    media_types = {
        ".mp4": "video/mp4", ".webm": "video/webm",
        ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        ".json": "application/json", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    return Response(target.read_bytes(), media_type=media_type)


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
    """代理转发 KOL 列表请求到 InSnap /v1/kols 端点。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    params = {"page_size": page_size, "user_type": "internal", "is_online": True}
    if cursor:
        params["cursor"] = cursor

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{base}/v1/kols",
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        print(f"[InSnap Proxy] Request exception: {e}")
        return JSONResponse({"error": f"Request failed: {e}"}, status_code=502)

    print(f"[InSnap Proxy] URL: {resp.url}")
    print(f"[InSnap Proxy] Status: {resp.status_code}")
    print(f"[InSnap Proxy] Headers: {dict(resp.headers)}")
    print(f"[InSnap Proxy] Body: {resp.text[:1000]}")

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(resp.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/insnap-proxy/discovery")
async def proxy_insnap_discovery():
    """代理转发 discovery 请求，查看 Key 可访问的端点。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{base}/v1",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        return JSONResponse({"error": f"Request failed: {e}"}, status_code=502)

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(resp.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/insnap-proxy/kols/{profile_id}")
async def proxy_insnap_kol_detail(profile_id: int):
    """代理转发单个 KOL 详情请求。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{base}/v1/kols/{profile_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        return JSONResponse({"error": f"Request failed: {e}"}, status_code=502)

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=502, headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(resp.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/insnap-proxy/kols/{profile_id}/outfits")
async def proxy_insnap_outfits(profile_id: int):
    """代理转发 KOL outfits 请求。"""
    try:
        base, api_key = _get_insnap_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{base}/v1/kols/{profile_id}/outfits",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        return JSONResponse({"error": f"Request failed: {e}"}, status_code=502)

    if resp.status_code != 200:
        return JSONResponse(
            {"error": f"InSnap API error: {resp.status_code}", "detail": resp.text[:500]},
            status_code=502, headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(resp.json(), headers={"Cache-Control": "no-store"})


# ── KOL Outfit 角色卡管理 ──────────────────────────────────
# 数据结构: [{key, profile_id, outfit_code, image_url, versions: [{version, data, created_at}]}]

def load_kol_chars() -> list[dict]:
    return _load_json(KOL_CHARS_FILE)


def save_kol_chars(items: list[dict]):
    _save_json(KOL_CHARS_FILE, items)


def _make_outfit_key(profile_id: int, outfit_code: str) -> str:
    return f"{profile_id}:{outfit_code}"


@app.get("/api/kol-characters")
async def get_kol_characters():
    """获取所有 KOL outfit 角色卡。"""
    return JSONResponse({"items": load_kol_chars()}, headers={"Cache-Control": "no-store"})


@app.post("/api/kol-characters/generate")
async def generate_kol_character(request: Request):
    """为指定 outfit 图片生成角色卡，复用 CHAR_GEN_IMAGE_SYSTEM 提示词和处理逻辑。"""
    body = await request.json()
    profile_id = body.get("profile_id")
    outfit_code = body.get("outfit_code", "")
    image_url = body.get("image_url", "")
    extra = body.get("extra", "")
    model = body.get("model", "x-ai/grok-4.1-fast")
    kol_name = body.get("kol_name", "")

    if not profile_id or not image_url:
        return JSONResponse({"error": "profile_id and image_url required"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl:
            img_resp = await dl.get(image_url)
        if img_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Failed to download image: HTTP {img_resp.status_code}"},
                status_code=502,
            )
        raw_ct = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        img_bytes = img_resp.content
        if raw_ct not in ("image/jpeg", "image/png", "image/gif"):
            img = Image.open(io.BytesIO(img_bytes))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            raw_ct = "image/png"
            print(f"[kol-gen] Converted image to PNG ({len(img_bytes)} bytes)")
        b64 = base64.b64encode(img_bytes).decode()
        data_url = f"data:{raw_ct};base64,{b64}"
        print(f"[kol-gen] Image ready: {raw_ct}, base64 length={len(b64)}")
    except Exception as e:
        return JSONResponse({"error": f"Failed to download/convert image: {e}"}, status_code=502)

    user_content = [
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    text_part = "Generate a complete character card based on this image."
    notes = []
    if kol_name:
        notes.append(f"KOL name: {kol_name}")
    if extra.strip():
        notes.append(f"Additional notes from the user:\n{extra.strip()}")
    if notes:
        text_part += "\n\n" + "\n".join(notes)
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
                "max_tokens": 32000,
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
    raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    finish_reason = data.get("choices", [{}])[0].get("finish_reason", "unknown")
    print(f"[kol-gen] finish_reason={finish_reason}, raw length={len(raw_content)}")
    print(f"[kol-gen] raw first 300 chars: {raw_content[:300]}")

    if not raw_content or not raw_content.strip():
        return JSONResponse(
            {"error": f"LLM returned empty content (finish_reason={finish_reason})",
             "raw_response": str(data)[:500]},
            status_code=502,
        )

    content = raw_content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        char_data = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[kol-gen] JSON parse failed, cleaned content first 500 chars:\n{content[:500]}")
        return JSONResponse(
            {"error": f"Failed to parse LLM output as JSON: {str(e)}", "raw": content[:500]},
            status_code=422,
        )

    key = _make_outfit_key(profile_id, outfit_code)
    items = load_kol_chars()
    existing = next((it for it in items if it["key"] == key), None)

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        version = len(existing["versions"]) + 1
        existing["versions"].append({"version": version, "data": char_data, "created_at": now})
    else:
        items.append({
            "key": key,
            "profile_id": profile_id,
            "outfit_code": outfit_code,
            "image_url": image_url,
            "kol_name": kol_name,
            "versions": [{"version": 1, "data": char_data, "created_at": now}],
        })

    save_kol_chars(items)
    entry = next(it for it in items if it["key"] == key)
    return JSONResponse({"item": entry}, headers={"Cache-Control": "no-store"})


@app.post("/api/kol-characters/revise")
async def revise_kol_character(request: Request):
    """AI 修改已有角色卡，复用 CHAR_REMIX_SYSTEM 提示词，生成新版本。"""
    body = await request.json()
    key = body.get("key", "")
    instructions = body.get("instructions", "")
    model = body.get("model", "x-ai/grok-4.1-fast")

    if not key or not instructions:
        return JSONResponse({"error": "key and instructions required"}, status_code=400)

    items = load_kol_chars()
    entry = next((it for it in items if it["key"] == key), None)
    if not entry or not entry["versions"]:
        return JSONResponse({"error": "character not found"}, status_code=404)

    latest = entry["versions"][-1]["data"]
    card_json = json.dumps(latest, ensure_ascii=False, indent=2)
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
                "max_tokens": 32000,
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

    new_version = len(entry["versions"]) + 1
    entry["versions"].append({
        "version": new_version,
        "data": char_data,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_kol_chars(items)
    return JSONResponse({"item": entry}, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def healthz():
    """容器健康检查端点。"""
    return {"ok": True}


# ── 静态文件托管 ────────────────────────────────────────────
# 挂载顺序重要：更具体的路径在前，/ 在最后
# /scenes-data 指向 SCENES_DIR（可能是外部持久卷），支持 Range 请求和视频拖拽
app.mount("/avatars", StaticFiles(directory=str(AVATARS_DIR)), name="avatars")
app.mount("/scenes-data", StaticFiles(directory=str(SCENES_DIR)), name="scene-data-files")
app.mount("/playground", StaticFiles(directory="playground", html=True), name="playground")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
