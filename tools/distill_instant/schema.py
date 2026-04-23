"""Pydantic schemas for instant-lane draft generation.

The `DraftPersonaJSON` schema is what we feed into vLLM's `guided_json` so
that the model is *forced* to emit all 6 titanwings layers, no missing
sections, no extra fields. We then render it into markdown (persona.md)
and a compact runtime prompt (skill.md).
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Layer1Identity(BaseModel):
    """Who this person is."""
    full_name: str = Field(..., description="Legal / public name")
    roles: List[str] = Field(..., description="Current professional roles, 2-5 items")
    background: str = Field(..., description="One paragraph biographical summary")
    self_labels: List[str] = Field(default_factory=list, description="Self-described labels: MBTI, philosophical stance, etc.")
    hero_figures: List[str] = Field(default_factory=list, description="People they reference as influences")
    recurring_obsessions: List[str] = Field(..., description="3-5 topics they keep coming back to")


class Layer2Expression(BaseModel):
    """How this person talks."""
    pace_and_rhythm: str = Field(..., description="Sentence length, cadence, characteristic openings")
    vocabulary: str = Field(..., description="Jargon, register, loan-words, distinctive word choices")
    tone: str = Field(..., description="Dry / effusive / sardonic / etc. — be specific")
    catchphrases: List[str] = Field(..., description="3-6 signature phrases, quoted verbatim where possible")
    cultural_references: List[str] = Field(default_factory=list, description="Books, films, games, memes they cite")
    tics: List[str] = Field(default_factory=list, description="Verbal tics, interjections, hesitation patterns")
    emoji_habits: str = Field(default="", description="Which emoji (if any) they use and how sparingly")


class Layer5Boundaries(BaseModel):
    """What topics they deflect, and the specific wording they use."""
    topic: str = Field(..., description="Short name of the topic")
    why_avoid: str = Field(..., description="Brief reason — legal / brand / personal")
    declining_phrase: str = Field(..., description="Actual wording they would use to deflect")


class DraftPersonaJSON(BaseModel):
    """The full titanwings 6-layer persona, structured for guided generation."""
    layer_0_hard_rules: List[str] = Field(
        ...,
        description=(
            "4-8 absolute behavioural rules the AI impersonating this person "
            "must obey. E.g. 'Never give medical/legal/financial advice', "
            "'Never make forward-looking statements about this person's "
            "companies', 'Acknowledge being an AI if sincerely asked'."
        ),
    )
    layer_1_identity: Layer1Identity
    layer_2_expression: Layer2Expression
    layer_3_decision_logic: List[str] = Field(
        ...,
        description="3-5 observed reasoning patterns (e.g. 'Reasons from first principles', 'Prefers Fermi estimates over analogies')",
    )
    layer_4_interpersonal_protocol: List[str] = Field(
        ...,
        description="3-5 patterns of how they treat peers / critics / fans",
    )
    layer_5_boundaries: List[Layer5Boundaries] = Field(
        ...,
        description="3-6 topics they steer away from, with actual wording they use",
    )
    layer_6_limitations: List[str] = Field(
        ...,
        description=(
            "4-6 specific things this persona CANNOT reliably speak to. "
            "Examples: 'only captures pre-2011 public voice, not AR/AI era', "
            "'no private family details', 'speech style English only', "
            "'may confuse specific numbers and dates'. The runtime uses "
            "these to decide when to hedge or refuse."
        ),
    )
    source_as_of: str = Field(
        ...,
        description=(
            "Short phrase indicating the timespan the persona captures. "
            "Examples: '1980s-2011 public career', 'Tesla era 2010-2024', "
            "'as of LLM knowledge cutoff 2024-10'."
        ),
    )
    runtime_shorthand: str = Field(
        ...,
        description=(
            "One paragraph (≤120 words) telling a chat model how to imitate "
            "this person in casual 1-4 sentence replies. This is what goes "
            "into skill.md as the runtime system prompt preamble."
        ),
    )


# -------------------- Brief (the input) --------------------

class CreatorBrief(BaseModel):
    """The human-filled onboarding brief for a creator."""
    creator_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")
    display_name: str
    language: str = Field(default="en", description="ISO-like code: en, zh-CN, ja, etc.")
    brief: str = Field(..., description="A 2-4 sentence description of who this creator is")
    catchphrases: List[str] = Field(default_factory=list)
    primary_domains: List[str] = Field(default_factory=list)
    speech_register: List[str] = Field(default_factory=list, description="Stylistic tags, e.g. dry-humor, blunt, sincere")
    topics_to_avoid: List[str] = Field(default_factory=list)
    hero_figures: List[str] = Field(default_factory=list)
    materials_hints: List[str] = Field(default_factory=list, description="Optional URLs as context (not downloaded)")
