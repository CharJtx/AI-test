import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()

app = FastAPI()


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.add_middleware(NoCacheStaticMiddleware)

DATA_DIR = Path("data")
PRESETS_FILE = DATA_DIR / "presets.json"
WORLDBOOKS_FILE = DATA_DIR / "worldbooks.json"
CHARACTERS_FILE = DATA_DIR / "characters.json"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def get_api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _load_json(path: Path) -> list[dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_presets() -> list[dict]:
    return _load_json(PRESETS_FILE)


def save_presets(presets: list[dict]):
    _save_json(PRESETS_FILE, presets)


def load_worldbooks() -> list[dict]:
    return _load_json(WORLDBOOKS_FILE)


def save_worldbooks(books: list[dict]):
    _save_json(WORLDBOOKS_FILE, books)


def load_characters() -> list[dict]:
    return _load_json(CHARACTERS_FILE)


def save_characters(chars: list[dict]):
    _save_json(CHARACTERS_FILE, chars)


def _next_id(items: list[dict]) -> int:
    return max((item["id"] for item in items), default=0) + 1


CHAR_FIELDS = [
    "name", "description", "personality", "scenario",
    "first_mes", "mes_example", "system_prompt",
    "creator_notes", "tags",
]


def _normalize_char(data: dict) -> dict:
    """Extract standard character card fields from various formats."""
    # TavernAI V2: fields nested under "data"
    src = data.get("data", data) if isinstance(data.get("data"), dict) else data

    char = {}
    for f in CHAR_FIELDS:
        char[f] = src.get(f, "")
    if not char["tags"]:
        char["tags"] = []
    if isinstance(char["tags"], str):
        char["tags"] = [t.strip() for t in char["tags"].split(",") if t.strip()]

    # SillyTavern aliases
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


# ── Model list ──────────────────────────────────────────────

@app.get("/api/models")
async def list_models():
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


# ── AI Character Generation ─────────────────────────────────

CHAR_GEN_SYSTEM = """You are an expert character card designer for AI roleplay systems.
Given user-provided keywords/concepts, generate a COMPLETE character card in JSON format.

Requirements:
1. All text fields should be rich, detailed, and psychologically nuanced.
2. The character_book must contain 15-25 entries covering: core identity, relationships, psychology, environment, behaviors, speech patterns, and intimate dynamics.
3. Each character_book entry needs relevant trigger keywords.
4. Use {{char}} for the character's name and {{user}} for the user in all text fields.
5. The mes_example should contain 2-3 realistic example exchanges using <START> separators.
6. Write ALL content in the SAME language as the user's keywords input.

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

    # Strip markdown code fences if present
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

    chars = load_characters()
    char_data["id"] = _next_id(chars)
    chars.append(char_data)
    save_characters(chars)
    return {"character": char_data}


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

Return ONLY valid JSON with the same structure as a standard character card (name, description, personality, scenario, first_mes, mes_example, system_prompt, creator_notes, tags, character_book with entries)."""


import base64  # noqa: E402


@app.post("/api/characters/generate-from-image")
async def generate_character_from_image(
    image: UploadFile = File(...),
    extra: str = Form(""),
    model: str = Form("google/gemini-2.5-flash-preview"),
):
    img_bytes = await image.read()
    content_type = image.content_type or "image/jpeg"
    b64 = base64.b64encode(img_bytes).decode()
    data_url = f"data:{content_type};base64,{b64}"

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

    chars = load_characters()
    char_data["id"] = _next_id(chars)
    chars.append(char_data)
    save_characters(chars)
    return {"character": char_data}


# ── Chat completion (streaming) ─────────────────────────────

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    model = body["model"]
    messages = body["messages"]
    params = body.get("params", {})

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


# ── Presets CRUD ────────────────────────────────────────────

@app.get("/api/presets")
async def get_presets():
    return {"presets": load_presets()}


@app.post("/api/presets")
async def create_preset(request: Request):
    preset = await request.json()
    presets = load_presets()
    preset["id"] = _next_id(presets)
    presets.append(preset)
    save_presets(presets)
    return {"preset": preset}


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: int, request: Request):
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
    presets = load_presets()
    presets = [p for p in presets if p["id"] != preset_id]
    save_presets(presets)
    return {"ok": True}


# ── Worldbooks CRUD ─────────────────────────────────────────

@app.get("/api/worldbooks")
async def get_worldbooks():
    return {"worldbooks": load_worldbooks()}


@app.post("/api/worldbooks")
async def create_worldbook(request: Request):
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
    books = load_worldbooks()
    books = [b for b in books if b["id"] != book_id]
    save_worldbooks(books)
    return {"ok": True}


@app.post("/api/worldbooks/import")
async def import_worldbook(file: UploadFile = File(...)):
    """Import a worldbook from a JSON file. Supports both native format and SillyTavern format."""
    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

    books = load_worldbooks()
    new_id = _next_id(books)

    # Detect SillyTavern lorebook format (has "entries" as dict with numeric keys)
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
        # Array of entries
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
        # Native format
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


# ── Characters CRUD ─────────────────────────────────────────

@app.get("/api/characters")
async def get_characters():
    return {"characters": load_characters()}


@app.post("/api/characters")
async def create_character(request: Request):
    char = await request.json()
    chars = load_characters()
    char["id"] = _next_id(chars)
    chars.append(char)
    save_characters(chars)
    return {"character": char}


@app.put("/api/characters/{char_id}")
async def update_character(char_id: int, request: Request):
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
    chars = load_characters()
    chars = [c for c in chars if c["id"] != char_id]
    save_characters(chars)
    return {"ok": True}


@app.post("/api/characters/import")
async def import_character(file: UploadFile = File(...)):
    """Import a character card from JSON. Supports native, TavernAI V2, and SillyTavern formats."""
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


# ── Playground scenes ────────────────────────────────────────

SCENES_DIR = Path("playground/scenes")


@app.get("/api/playground/scenes")
async def list_playground_scenes():
    """List available scene directories under playground/scenes/."""
    scenes = []
    if SCENES_DIR.exists():
        for p in sorted(SCENES_DIR.iterdir()):
            if p.is_dir():
                scenes.append(p.name)
    return {"scenes": scenes}


@app.post("/api/playground/scenes")
async def create_playground_scene(request: Request):
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
    data_file = SCENES_DIR / name / "scene-data.json"
    if data_file.exists():
        return JSONResponse(json.loads(data_file.read_text(encoding="utf-8")))
    return JSONResponse(None)


@app.put("/api/playground/scenes/{name}/data")
async def save_scene_data(name: str, request: Request):
    scene_dir = SCENES_DIR / name
    scene_dir.mkdir(parents=True, exist_ok=True)
    data = await request.json()
    (scene_dir / "scene-data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


@app.get("/api/playground/scenes/{name}/resources")
async def list_scene_resources(name: str):
    scene_dir = SCENES_DIR / name
    files = []
    if scene_dir.exists():
        for f in sorted(scene_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".mp4", ".webm", ".mov", ".avi"):
                files.append({"name": f.name, "size": f.stat().st_size})
    return {"files": files}


@app.post("/api/playground/scenes/{name}/upload")
async def upload_scene_resource(name: str, file: UploadFile = File(...)):
    scene_dir = SCENES_DIR / name
    scene_dir.mkdir(parents=True, exist_ok=True)
    dest = scene_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"name": file.filename, "size": len(content)}


@app.delete("/api/playground/scenes/{name}/resources")
async def delete_scene_resource(name: str, filename: str = Query(...)):
    target = SCENES_DIR / name / filename
    if target.exists():
        target.unlink()
        return {"ok": True}
    return JSONResponse({"error": "file not found"}, status_code=404)


# ── Serve frontend ──────────────────────────────────────────

app.mount("/playground", StaticFiles(directory="playground", html=True), name="playground")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
