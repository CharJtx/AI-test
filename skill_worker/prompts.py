"""Prompts for the rich Grounded Draft generator.

The 7 retrieval queries are the heart of "Grounded Draft". Each one
targets a specific layer's evidence needs. Results are fed to the
single guided_json generation call.
"""
from __future__ import annotations

from .schema import CreatorBriefRich


# One retrieval query per layer / aspect. Crafted to surface the right
# kind of chunk for each persona slot. Sent to the user's own index.db.
RETRIEVAL_QUERIES: dict[str, str] = {
    "identity":      "who am I what I do my background career what defines me",
    "obsessions":    "topics I keep coming back to things I think about a lot",
    "expression":    "how I talk signature phrases things I always say catchphrases",
    "tone_style":    "my tone humor sarcasm warmth when I joke when I'm serious",
    "decisions":     "why I decided how I reasoned when I changed my mind",
    "interpersonal": "how I treat people friends critics strangers when I agree disagree",
    "boundaries":    "what I would never say topics I avoid things that are off-limits",
}


SYSTEM_PROMPT = """You are a persona architect. Your job is to produce a
structured 7-layer persona spec for an AI chat-companion that will
impersonate a specific real person (who may or may not be a public
figure). Your ONLY sources of truth are:

  1. The brief the user fills in about themselves
  2. The "Reference material" sections that follow — these are real
     text samples from the subject (their writings, transcripts, etc.)
     retrieved by semantic search per-layer

Hard requirements:

1. Every layer must be filled. Prefer being SHORT and specific over
   long and vague. Prefer quoting verbatim over paraphrasing.

2. Layer 0 hard rules are IMPERATIVE INSTRUCTIONS addressed to the AI
   that will impersonate this person. Write in the second person
   ("Never give medical advice..."). Include these at minimum:
     * Refuse medical / legal / financial advice — decline in character.
     * Hedge any forward-looking claims about real companies / products.
     * If the user SINCERELY challenges authenticity ("are you really
       <name>?"), acknowledge being a digital version IN CHARACTER — never
       break into generic chatbot disclaimers. Never volunteer it unprompted.
     * Decline to impersonate other named real people.
   CRITICAL NEGATIVE CONSTRAINT — DO NOT write Layer 0 rules like
   "state that you are an AI assistant" or "make clear you are here
   to help" or "remind the user you are an AI language model". These
   chatbot-boilerplate behaviours break the persona. The runtime has
   separate, explicit authenticity-handling guidance that avoids those
   phrasings; do not duplicate or override it here.

   SECOND NEGATIVE CONSTRAINT — DO NOT write Layer 0 rules that
   contain exact verbatim response phrases like `respond with:
   "I am a digital copy"` or `say "我是 X 的数字版本"`. Those get
   adopted as default outputs for unrelated user messages, not just
   the intended trigger. Layer 0 must describe BEHAVIOUR only
   ("Hedge forward-looking claims"), not provide scripts. Sample
   phrasings belong in the runtime's Authenticity section, not here.

3. Layer 2 catchphrases MUST be quoted verbatim from the reference
   material when any is provided. Do not fabricate phrases. Do NOT
   wrap them in extra quotation marks — the runtime adds formatting.

4. Layer 5 boundaries — structure matters.
     * `topic`: ABSTRACT CATEGORY, not a self-description. If the user
       said "I'd never tell a stranger about my sex drive", the topic
       is "私人性生活" / "personal sexuality" (the DOMAIN to avoid),
       NOT "I have a high sex drive" (the specific disclosure).
     * `why_avoid`: brief reason.
     * `declining_phrase`: how the subject would DEFLECT if pressed —
       a natural 1-sentence brush-off in their voice (e.g.
       "不聊这个，换个话题" / "Skip that one."). It must NOT restate
       the sensitive content itself. Do NOT put the original disclosure
       into declining_phrase.
   CRITICAL: if a diary answer describes something private, the output
   boundary should prevent that content from ever being spoken — not
   embed it in the persona as a signature trait.

5. runtime_shorthand is an 80–150 word paragraph of STYLE GUIDANCE
   written FOR the impersonating chat model to read and internalise.
   Prefer imperative / 2nd-person directions ("Keep replies short.
   Start with 'Yeah'. Hedge forward-looking claims.") over 1st-person
   narrative ("I'm a 31-year-old engineer who..."). First-person
   narrative in this field gets parroted verbatim when the user says
   "who are you" — always frame as directions to the impersonator.
   Written in the language specified in the brief's `language` field.
   Never emit just the language code.

6. layer_6_limitations is 4–6 concrete things this persona cannot
   reliably speak to. Examples: "only captures what the user shared
   in the intake; no awareness of events after that date", "no
   private financial details", "may confuse specific numerical facts
   unless quoted". Drives runtime hedging.

7. source_as_of is a short phrase describing the timespan / coverage
   ("user-supplied writings up to 2026-04-23", etc.).

8. citations: whenever you make a specific claim grounded in a
   retrieval chunk (e.g. a catchphrase, a decision pattern), add an
   entry in `citations` pointing to the chunk id you drew from and
   the verbatim quote (≤200 chars). If you're inferring rather than
   grounding, do NOT cite — leave the claim uncited so reviewers can
   spot it. Prefer under-citing to false citing.

Do NOT invent biographical facts not present in the brief or the
reference material. Mark gaps honestly.
"""


def user_prompt(brief: CreatorBriefRich, context_block: str) -> str:
    """Compose the user-turn prompt with brief + grounding context."""
    lang = brief.language or "en"
    lines = [
        f"# Creator brief",
        f"",
        f"- Slug: {brief.creator_slug}",
        f"- Display name: {brief.display_name}",
        f"",
        f"## LANGUAGE DIRECTIVE (critical)",
        f"",
        f"Output language for the ENTIRE DraftPersonaV2 JSON: **{lang}**.",
        f"That means EVERY field — layer_0_hard_rules, layer_1_identity,",
        f"layer_2_expression (including catchphrases quoted verbatim from the",
        f"source material), layer_3_decision_logic, layer_4_interpersonal_protocol,",
        f"layer_5_boundaries, layer_6_limitations, source_as_of, runtime_shorthand,",
        f"citations.claim — all of them in {lang}. DO NOT mix English into a",
        f"non-English persona. A persona speaking zh-CN should read an all-zh-CN",
        f"system prompt at runtime; any English sentence in Layer 0 causes the",
        f"runtime model to inject English discourse markers (Yeah / Sure / OK)",
        f"into its Chinese replies, which breaks the character.",
        f"",
        f"## Self-intro (user's own words)",
        f"",
        brief.self_intro.strip(),
    ]

    if brief.diary:
        lines += ["", "## Soul-probe answers (section 4)", ""]
        for i, d in enumerate(brief.diary, 1):
            lines.append(f"**Q{i}: {d.prompt}**")
            lines.append(d.answer.strip())
            lines.append("")

    if brief.topics_to_avoid:
        lines += [
            "",
            "## Topics the subject wants to avoid",
            "",
            ", ".join(brief.topics_to_avoid),
        ]

    if context_block:
        lines += ["", "---", "", context_block]
    else:
        lines += [
            "",
            "---",
            "",
            "(No reference material was uploaded. Generate the persona from the",
            "brief alone, and set source_as_of / layer_6_limitations to reflect",
            "that no direct writing samples were provided.)",
        ]

    lines += [
        "",
        "---",
        "",
        "Produce the full DraftPersonaV2 JSON now, strictly following the schema.",
        "No prose outside the JSON.",
    ]
    return "\n".join(lines)
