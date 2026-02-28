import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI()

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
