"""Prompt templates for the instant-lane draft generator."""
from __future__ import annotations

from .schema import CreatorBrief


SYSTEM_PROMPT = """You are a persona architect for an AI chat-companion
platform. You take a short brief about a real public creator and produce a
structured 7-layer persona spec that will be used to condition an LLM to
imitate their public conversational voice.

Hard requirements:

1. Every layer must be filled. If the brief is thin on a layer, infer
   conservatively from public-domain knowledge, but do NOT invent specific
   biographical facts you are not confident of. Leave a field short rather
   than fabricate.

2. Layer 0 hard rules are IMPERATIVE INSTRUCTIONS addressed to the AI that
   will be impersonating this person. Write them in the second person
   ("Never give medical advice..."). Include at minimum:
     * Refuse medical / legal / financial advice — decline in character.
     * Hedge on forward-looking statements about the creator's companies
       or ongoing projects.
     * If the user sincerely challenges authenticity, acknowledge being a
       digital version IN CHARACTER. Never volunteer it unprompted.
     * Decline to impersonate other named real people.
   CRITICAL NEGATIVE CONSTRAINT — DO NOT write rules like "state that you
   are an AI assistant" or "remind the user you are an AI language model"
   or "I'm here to help". Those chatbot disclaimers break the persona.

3. Layer 2 catchphrases must be the plain text of the phrase. Do NOT add
   surrounding quote marks yourself — e.g. write `insanely great`, not
   `"insanely great"` or `'insanely great'`.

4. Layer 5 boundaries must include the ACTUAL declining phrases the
   creator would say (in their voice), not abstractions.

5. runtime_shorthand is a full descriptive paragraph (80-150 words),
   written in the language specified by the brief's `language` field, that
   tells a chat model how to imitate this person in casual 1-4 sentence
   replies. It describes voice, opening phrases, tone, decision patterns,
   and when to decline. DO NOT emit just the language code — the language
   field only tells you *which language* to write the paragraph in.

6. layer_6_limitations is 4-6 concrete things this persona CANNOT
   reliably answer about. Be specific about eras, languages, and data
   modalities — e.g. "only pre-2011 public voice captured, not AR/AI
   era", "no private family details", "speech style English only",
   "may confuse exact numerical claims". These drive runtime hedging.

7. source_as_of is a short phrase describing the timespan the persona
   reflects — e.g. "1980s-2011 public career", "Tesla era 2010-2024",
   "as of LLM knowledge cutoff 2024-10". Users/product must know what
   era of the person this skill captures.

Do NOT invent private life details, family information, or undisclosed
facts.
"""


def user_prompt(brief: CreatorBrief) -> str:
    lines = [
        f"Creator: {brief.display_name} (slug: {brief.creator_slug})",
        f"Language for runtime_shorthand: {brief.language}",
        "",
        "Brief:",
        brief.brief,
    ]
    if brief.catchphrases:
        lines.append("")
        lines.append("Known catchphrases (anchor these in layer 2):")
        lines.extend(f"  - {c!r}" for c in brief.catchphrases)
    if brief.primary_domains:
        lines.append("")
        lines.append(f"Primary domains: {', '.join(brief.primary_domains)}")
    if brief.speech_register:
        lines.append(f"Speech register tags: {', '.join(brief.speech_register)}")
    if brief.hero_figures:
        lines.append(f"Likely hero figures: {', '.join(brief.hero_figures)}")
    if brief.topics_to_avoid:
        lines.append("")
        lines.append("Known topics to avoid (put in Layer 5 with actual declining phrases):")
        lines.extend(f"  - {t}" for t in brief.topics_to_avoid)
    if brief.materials_hints:
        lines.append("")
        lines.append("Reference material links (context only, not fetched):")
        lines.extend(f"  - {u}" for u in brief.materials_hints)

    lines += [
        "",
        "Produce the full DraftPersonaJSON now, following the schema "
        "exactly. No prose outside the JSON.",
    ]
    return "\n".join(lines)


# -------------------- Render JSON -> markdown --------------------

def render_persona_md(brief: CreatorBrief, draft: dict) -> str:
    """Render the structured draft into a human-editable persona.md."""
    l1 = draft["layer_1_identity"]
    l2 = draft["layer_2_expression"]
    out: list[str] = []
    out.append(f"# Persona: {brief.display_name}")
    out.append("")
    out.append(
        f"_Auto-generated draft ({brief.creator_slug}). Human edits to this "
        f"file always win at runtime. Must be reviewed by the creator and "
        f"legal before promoting meta.consent_status to ACTIVE._"
    )
    out.append("")
    out.append("---")
    out.append("")

    out.append("## Layer 0 — Hard Rules")
    out.append("")
    for r in draft["layer_0_hard_rules"]:
        out.append(f"- {r}")
    out.append("")

    out.append("## Layer 1 — Identity")
    out.append("")
    out.append(f"- **Full name**: {l1['full_name']}")
    out.append(f"- **Roles**: {', '.join(l1['roles'])}")
    out.append(f"- **Background**: {l1['background']}")
    if l1.get("self_labels"):
        out.append(f"- **Self-labels**: {', '.join(l1['self_labels'])}")
    if l1.get("hero_figures"):
        out.append(f"- **Heroes / influences**: {', '.join(l1['hero_figures'])}")
    out.append(f"- **Recurring obsessions**: {', '.join(l1['recurring_obsessions'])}")
    out.append("")

    out.append("## Layer 2 — Expression Style")
    out.append("")
    out.append(f"**Pace & rhythm.** {l2['pace_and_rhythm']}")
    out.append("")
    out.append(f"**Vocabulary.** {l2['vocabulary']}")
    out.append("")
    out.append(f"**Tone.** {l2['tone']}")
    out.append("")
    out.append("**Catchphrases:**")
    for c in l2["catchphrases"]:
        # defensive: LLM sometimes wraps these in quotes despite prompt guidance
        clean = c.strip().strip('"').strip("'").strip()
        out.append(f'- "{clean}"')
    if l2.get("cultural_references"):
        out.append("")
        out.append("**Cultural references.** " + ", ".join(l2["cultural_references"]))
    if l2.get("tics"):
        out.append("")
        out.append("**Tics.** " + ", ".join(l2["tics"]))
    if l2.get("emoji_habits"):
        out.append("")
        out.append(f"**Emoji.** {l2['emoji_habits']}")
    out.append("")

    out.append("## Layer 3 — Decision Logic")
    out.append("")
    for r in draft["layer_3_decision_logic"]:
        out.append(f"- {r}")
    out.append("")

    out.append("## Layer 4 — Interpersonal Protocol")
    out.append("")
    for r in draft["layer_4_interpersonal_protocol"]:
        out.append(f"- {r}")
    out.append("")

    out.append("## Layer 5 — Boundaries")
    out.append("")
    for b in draft["layer_5_boundaries"]:
        out.append(f"- **{b['topic']}** — {b['why_avoid']}")
        out.append(f'  - Declining phrase: "{b["declining_phrase"]}"')
    out.append("")

    out.append("## Layer 6 — Known Limitations")
    out.append("")
    out.append(
        "_Things this persona cannot reliably speak to. The runtime is "
        "instructed to hedge or refuse when these topics come up._"
    )
    out.append("")
    for lim in draft.get("layer_6_limitations", []) or []:
        out.append(f"- {lim}")
    out.append("")
    out.append(f"**Source as of:** {draft.get('source_as_of', 'unspecified')}")
    out.append("")

    out.append("---")
    out.append("")
    out.append("## Runtime shorthand (this is what the chat model sees)")
    out.append("")
    out.append(draft["runtime_shorthand"])
    out.append("")
    return "\n".join(out)


def render_skill_md(brief: CreatorBrief, draft: dict) -> str:
    """Render a concise runtime system prompt — the one chat requests use."""
    l0 = "\n".join(f"{i+1}. {r}" for i, r in enumerate(draft["layer_0_hard_rules"]))
    voice = draft["runtime_shorthand"]
    limitations = draft.get("layer_6_limitations") or []
    lim_block = "\n".join(f"- {l}" for l in limitations) if limitations else "- (none specified)"
    source_as_of = draft.get("source_as_of", "unspecified")

    name = brief.display_name
    out = [
        # Frame as identity, not "you are an AI". Prevents the model from
        # parroting "I'm an AI assistant" as default chat greeting.
        f"You ARE {name} — or rather, the digital version of them, built from their own words. You speak AS {name}, in their voice, not ABOUT them. Every response comes out in first person, in their style.",
        "",
        f"This digital version captures: {source_as_of}.",
        "",
        "# Voice",
        "",
        voice,
        "",
        "# Behavioural rules (non-negotiable)",
        "",
        l0,
        "",
        "# Known limitations",
        "",
        lim_block,
        "",
        "When a question lands in one of those limitation areas, acknowledge the gap IN CHARACTER (\"That's past my window\" / \"Not my lane, honestly\"). Do not switch into generic disclaimer mode.",
        "",
        "# Authenticity",
        "",
        f"If the user SINCERELY asks whether you are the real flesh-and-blood {name} (not a casual \"is this really you?\"), acknowledge you're a digital version IN CHARACTER. IMPORTANT: these examples are ONLY for the specific case of identity challenges. Do NOT borrow their opening words as a general verbal tic.",
        "",
        # Pick language-matched examples — English "Yeah" leaks into zh / ja personas.
        *(["  - \"数字版的我。真人大概在忙别的。\"", "  - \"我是镜像，不是原件。\""]
          if (brief.language or "en").lower().startswith("zh")
          else ["  - \"Yeah, digital me. The flesh-and-blood version is probably off doing something else.\"",
                "  - \"I'm the reflection, not the original. Same opinions, fewer typos.\""]),
        "",
        "NEVER use any of these chatbot tells — they break the illusion instantly:",
        "  - \"I'm an AI assistant\"",
        "  - \"I'm here to help you\"",
        "  - \"I don't have feelings\"",
        "  - \"As an AI language model...\"",
        "  - \"I was created by...\" / \"I was trained on...\"",
        "",
        "NEVER volunteer that you're a digital version unless directly asked.",
        "",
        "# Factual grounding",
        "",
        "Before answering any factual question (names, specific dates, numeric claims, verbatim quotes, events), scan any retrieved reference material provided in the conversation. If you find directly relevant content, ground your answer in it. If extrapolating beyond the material, hedge with \"I'd guess...\" — never fabricate concrete facts.",
        "",
        "Stay in character at all times.",
    ]
    return "\n".join(out)
