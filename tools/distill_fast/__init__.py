"""Fast-lane distillation: transcribe + persona + RAG. No LoRA training.

Target: take a creator from raw public material to a deployable skill in
roughly 15-30 minutes (vs 24-36h for the slow-lane QLoRA route under
tools/distill/).

Pipeline:
    1. transcribe   audio -> text via faster-whisper on 1x 4090 (GPU 2/3)
    2. chunk        transcripts + tweets -> semantic chunks .jsonl
    3. persona_gen  chunks sample -> titanwings persona.md via vLLM teacher
    4. embed        chunks -> sqlite-vec index using bge-m3
    5. (runtime)    router retrieves top-k chunks per user message, injects
                    as "Reference material" into system prompt

Output layout on disk:
    skills/{id}/
        meta.json
        persona.md
        skill.md
        knowledge/
            chunks.jsonl          # raw chunks with provenance
            index.db              # sqlite + vec0 virtual table
            manifest.json         # build info, source hashes
"""
