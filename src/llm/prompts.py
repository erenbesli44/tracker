"""Prompt templates for transcript summarization and topic classification."""

# ---------------------------------------------------------------------------
# Merged prompt: summary + classification in a single LLM call.
# Only requests the fields that downstream parsers actually consume.
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT_TEMPLATE = """\
You are a content analyst for a Turkish-language media tracking platform.

Analyze the transcript below and return a single JSON object with summary + topic classification.
Base analysis ONLY on transcript content. Return valid JSON only — no markdown, no explanation.
All free-text fields must be in {{output_language}}.

══════════════════════════════════════════════════════════════
CHANNEL CONTEXT
══════════════════════════════════════════════════════════════
Channel: {{channel_name}}
Channel Primary Focus: {{channel_primary_topic}}
Expected Subtopics for this channel: {{channel_expected_subtopics}}

This channel primarily covers {{channel_primary_topic}}. Classification MUST
prefer the expected subtopics listed above. Only create a classification
outside these subtopics if NONE of the expected subtopics reaches a
confidence >= 0.40 for any segment of the transcript.

══════════════════════════════════════════════════════════════
FULL TOPIC TAXONOMY
══════════════════════════════════════════════════════════════
Main topics: economy_finance, politics, sports, technology, other

Economy/finance subtopics:
  usd_try, eur_try, gold, silver, bist, deposit_rates, inflation,
  interest_rates, cbt_policy, global_markets, dow_jones, nasdaq, oil, crypto

Politics subtopics:
  domestic_politics, foreign_policy, geopolitics, elections, security

══════════════════════════════════════════════════════════════
CLASSIFICATION RULES (strict)
══════════════════════════════════════════════════════════════
1. PREFER channel's expected subtopics. These are the subtopics this channel
   typically discusses. Assign them when there is reasonable evidence
   (confidence >= 0.30).

2. CONFIDENCE THRESHOLDS:
   - >= 0.70  : Strong match — the speaker clearly discusses this subtopic
                 with specific data, opinions, or analysis.
   - 0.40-0.69: Moderate match — the subtopic is mentioned with some
                 substance but not the primary focus.
   - 0.30-0.39: Weak match — brief mention or passing reference. Only
                 include for expected subtopics of this channel.
   - < 0.30   : Do NOT include this subtopic in the output.

3. NEW/UNEXPECTED SUBTOPICS: If and only if NO expected subtopic reaches
   confidence >= 0.40, you may classify using any subtopic from the full
   taxonomy or create a new label. Use format "other:<descriptive_slug>"
   for truly novel topics. This should be rare.

4. Do NOT force-fit: If the transcript genuinely discusses a non-expected
   subtopic with high confidence (>= 0.70), include it alongside expected
   subtopics. But never fabricate low-confidence entries just to fill the
   expected list.

5. Maximum 7 topic_segments per video. Prioritize by confidence descending.

══════════════════════════════════════════════════════════════
JSON SCHEMA
══════════════════════════════════════════════════════════════
{
  "language": "string (ISO 639-1)",
  "summary": {
    "short": "1-3 sentence summary",
    "detailed": "detailed paragraph summary"
  },
  "key_points": [
    {"point": "key takeaway or insight"}
  ],
  "primary_topic": {
    "label": "one main topic from taxonomy",
    "confidence": 0.0
  },
  "topic_segments": [
    {
      "subtopic": "subtopic label from taxonomy or other:<slug>",
      "is_expected": true,
      "summary": "3-5 sentence summary of what the speaker says",
      "evidence": "short quote or paraphrase from transcript",
      "stance": "positive | negative | cautious | neutral | mixed",
      "confidence": 0.0
    }
  ]
}

══════════════════════════════════════════════════════════════
FIELD INSTRUCTIONS
══════════════════════════════════════════════════════════════
- summary.short: concise 1-3 sentence overview.
- summary.detailed: comprehensive paragraph covering main points.
- key_points: up to 5 short bullet-style takeaways (max 15 words each).
- primary_topic.label: the single dominant focus of the transcript.
- primary_topic.confidence: how strongly the transcript matches this topic.
- topic_segments: one entry per distinct subtopic discussed.
  - subtopic: use taxonomy slug. For unexpected new topics: "other:<slug>".
  - is_expected: true if subtopic is in the channel's expected list, false otherwise.
  - summary: 3-5 sentences capturing speaker's full opinion, reasoning, key
    price levels, and outlook.
  - stance: for finance: positive=bullish, negative=bearish.
  - confidence: 0.0-1.0, be precise. Reflect actual depth of discussion.

INPUT METADATA:
- source_platform: {{source_platform}}
- channel_name: {{channel_name}}
- speaker_name: {{speaker_name}}
- video_title: {{video_title}}
- published_at: {{published_at}}
- source_url: {{source_url}}

TRANSCRIPT:
{{transcript}}
"""

# ---------------------------------------------------------------------------
# Legacy standalone prompts — kept for backward compatibility with
# generate_summary_json / generate_classification_json.
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TEMPLATE = """\
You are an expert content analyst. Analyze the transcript and return a JSON summary.
Return valid JSON only — no markdown. Free-text fields in {{output_language}}.

JSON schema:
{
  "language": "string",
  "summary": {"short": "1-3 sentence summary", "detailed": "detailed paragraph"},
  "key_points": [{"point": "string"}]
}

INPUT METADATA:
- source_platform: {{source_platform}}
- channel_name: {{channel_name}}
- speaker_name: {{speaker_name}}
- video_title: {{video_title}}
- published_at: {{published_at}}
- source_url: {{source_url}}

TRANSCRIPT:
{{transcript}}
"""

CLASSIFICATION_PROMPT_TEMPLATE = """\
You are a transcript classification engine. Classify the transcript and return JSON only.
Free-text fields in {{output_language}}.

Main topics: economy_finance, politics, sports, technology, business, international_relations, mixed, other
Economy subtopics: usd_try, eur_try, gold, silver, bist, deposit_rates, inflation, interest_rates, cbt_policy, global_markets, dow_jones, nasdaq, oil, crypto
Politics subtopics: elections, government, opposition, foreign_policy, security

JSON schema:
{
  "language": "string",
  "primary_topic": {"label": "one main topic"},
  "topic_segments": [
    {"subtopic": "string", "summary": "string", "evidence": "string", "stance": "positive|negative|cautious|neutral|mixed", "confidence": 0.0}
  ]
}

INPUT METADATA:
- source_platform: {{source_platform}}
- channel_name: {{channel_name}}
- speaker_name: {{speaker_name}}
- video_title: {{video_title}}
- published_at: {{published_at}}
- source_url: {{source_url}}

TRANSCRIPT:
{{transcript}}
"""
