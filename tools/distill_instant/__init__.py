"""Instant-lane distillation: creator brief -> draft skill bundle in <5 min.

Input: skills/_briefs/{slug}.yaml (minimal human-filled form)
Output: skills/{slug}-draft/{meta.json, persona.md, skill.md}

Process:
    1. Load brief YAML
    2. Call vLLM (Qwen3-14B-AWQ by default) with guided_json to force
       titanwings 6-layer structured output
    3. Render structured JSON -> persona.md (full) + skill.md (runtime)
    4. Write meta.json with consent_status="DRAFT — awaiting contract"

Output is a DRAFT. It is NOT servable until:
    - creator reviews / edits
    - consent contract is signed
    - meta.consent_status is manually promoted to "ACTIVE"

Runtime serving (services/skill_inference/router.py) currently loads any
skill directory; a future guard should refuse to serve skills with DRAFT
status. TODO once we have a second real creator in the system.
"""
