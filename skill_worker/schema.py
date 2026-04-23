"""Pydantic schemas for the skill-worker service.

Two layers:

  - CreatorBriefRich: what the S1-S6 intake form POSTs. Extends the
    old CreatorBrief with free-text self_intro, pasted samples, diary
    answers, and toggles.

  - DraftPersonaV2: the guided_json output schema the LLM is forced to
    fill. 7 layers plus evidence citations.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# -------------------- Intake (what the form POSTs) --------------------

class DiaryAnswer(BaseModel):
    """One free-text answer to a soul-probing prompt (Section 4)."""
    prompt: str
    answer: str


class MaterialText(BaseModel):
    """A pasted or uploaded text blob (Section 3)."""
    source: str = Field(..., description="human label, e.g. 'pasted', 'old-blog-post.md', 'tweet-archive'")
    content: str
    kind: str = Field(default="text", description="text / transcript / tweet")


class MaterialURL(BaseModel):
    """A remote source to fetch server-side (Section 3)."""
    url: str
    kind: str = Field(default="youtube", description="youtube / podcast / rss / plain")
    label: Optional[str] = None


class CreatorBriefRich(BaseModel):
    """The S1-S6 form payload.

    Only creator_slug, display_name, language, and self_intro are required.
    Everything else enriches the grounding but is optional.
    """
    # S1 — basics
    creator_slug: str = Field(..., pattern=r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")
    display_name: str
    language: str = Field(default="en")

    # S2 — self-intro (primary signal when no uploads)
    self_intro: str = Field(
        ...,
        min_length=50,
        description="user's own 200-500 word description of themselves",
    )

    # S3 — material samples (richest signal)
    material_texts: List[MaterialText] = Field(default_factory=list)
    material_urls: List[MaterialURL] = Field(default_factory=list)

    # S4 — diary probe answers (optional, 5 prompts)
    diary: List[DiaryAnswer] = Field(default_factory=list)

    # S5 — boundaries (chips)
    topics_to_avoid: List[str] = Field(default_factory=list)

    # S6 — generation mode hint (auto-resolved server-side based on material presence)
    prefer_grounded: bool = Field(default=True)


# -------------------- Output (what the LLM fills) --------------------

class Layer1Identity(BaseModel):
    full_name: str
    roles: List[str] = Field(default_factory=list)
    background: str
    self_labels: List[str] = Field(default_factory=list)
    recurring_obsessions: List[str] = Field(default_factory=list)


class Layer2Expression(BaseModel):
    pace_and_rhythm: str
    vocabulary: str
    tone: str
    catchphrases: List[str] = Field(default_factory=list)
    tics: List[str] = Field(default_factory=list)
    emoji_habits: str = ""


class Layer5Boundary(BaseModel):
    topic: str
    why_avoid: str
    declining_phrase: str


class EvidenceCitation(BaseModel):
    """A ground-truth reference backing a claim in the persona."""
    claim: str = Field(..., description="one short sentence restating the claim")
    layer: str = Field(..., description="which layer this evidences: layer_1..layer_6")
    chunk_id: str = Field(..., description="source chunk id it's grounded in")
    quote: str = Field(..., description="verbatim excerpt from the chunk, <= 200 chars")


class DraftPersonaV2(BaseModel):
    """7-layer persona with citations. guided_json target."""
    layer_0_hard_rules: List[str] = Field(..., min_length=4)
    layer_1_identity: Layer1Identity
    layer_2_expression: Layer2Expression
    layer_3_decision_logic: List[str] = Field(..., min_length=3)
    layer_4_interpersonal_protocol: List[str] = Field(..., min_length=3)
    layer_5_boundaries: List[Layer5Boundary] = Field(..., min_length=3)
    layer_6_limitations: List[str] = Field(..., min_length=3)
    source_as_of: str
    runtime_shorthand: str = Field(..., min_length=80)
    citations: List[EvidenceCitation] = Field(
        default_factory=list,
        description="optional but encouraged — tie specific claims back to source chunks",
    )
