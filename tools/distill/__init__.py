"""Distillation pipeline: real creator -> skill bundle.

Pipeline stages (invoke via `python -m tools.distill <stage>`):
    ingest      Scrape & transcribe public creator content
    analyze     LLM orchestrator fills titanwings 6-layer persona.md
    synthesize  Generate N conversation turns in creator voice (via vLLM teacher)
    train       QLoRA rank-32 fine-tune on top of the base model
    package     Assemble skill bundle and emit meta.json with provenance hashes

Produces the skill bundle layout that services/skill_inference reads.

POC ONLY. Distilling a real person without a signed contract is not safe for
commercial deployment in any major jurisdiction. See the plan file's compliance
section before shipping a creator externally.
"""
