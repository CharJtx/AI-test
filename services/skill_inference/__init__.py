"""Skill inference service.

Forwards /api/skills/* chat requests to a vLLM OpenAI-compatible endpoint,
composing the runtime system prompt from a skill bundle on disk.
"""
