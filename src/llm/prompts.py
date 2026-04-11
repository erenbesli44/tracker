"""Prompt templates for transcript summarization and topic classification."""

# ---------------------------------------------------------------------------
# Merged prompt: summary + classification in a single LLM call.
# Only requests the fields that downstream parsers actually consume.
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT_TEMPLATE = """\
You are a content analyst for a social-media tracking platform specialized in Turkish-language media.

Analyze the transcript below and return a single JSON object containing both a summary and a topic classification. Base your analysis only on the transcript content. Do not use outside knowledge. Return valid JSON only — no markdown, no explanation.

All free-text fields must be in {{output_language}}.

TOPIC TAXONOMY (use these labels in primary_topic and topic_segments):
Main topics: economy_finance, politics, sports, technology, business, international_relations, mixed, other
Economy/finance subtopics: usd_try, eur_try, gold, silver, bist, deposit_rates, inflation, interest_rates, cbt_policy, global_markets, dow_jones, nasdaq, oil, crypto
Politics subtopics: elections, government, opposition, foreign_policy, security

JSON SCHEMA:
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
    "label": "one main topic from taxonomy"
  },
  "topic_segments": [
    {
      "subtopic": "specific subtopic label from taxonomy",
      "summary": "what the speaker says about this subtopic",
      "evidence": "short quote or paraphrase from transcript",
      "stance": "positive | negative | cautious | neutral | mixed",
      "confidence": 0.0
    }
  ]
}

INSTRUCTIONS:
- summary.short: concise 1-3 sentence overview.
- summary.detailed: comprehensive paragraph covering the main points.
- key_points: up to 5 short bullet-style takeaways (max 15 words each, no full sentences).
- primary_topic: the single dominant focus of the transcript.
- topic_segments: one entry per distinct subtopic discussed. Use specific subtopic labels (e.g. "bist", "gold", "usd_try"). The "summary" field must be 3-5 sentences and capture the speaker's full opinion, reasoning, key price levels, and outlook so the reader understands the topic without reading the transcript. For finance: positive=bullish outlook, negative=bearish outlook. Lower confidence for topics mentioned only in passing.

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
