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
Transcript may include time markers like [MM:SS-MM:SS]; use them to separate topic regions.
Some videos open with a short teaser/fragman (a preview montage of what will be discussed).
Ignore that opening fragment — do not classify or summarize it. Analyze only the substantive content.

SENTENCE-COMPLETENESS RULE (applies to EVERY free-text summary field in this
schema — `summary.short`, `summary.detailed`, and every `topic_segments[*].summary`):
Each summary MUST end with a fully completed sentence that terminates with proper
punctuation (. ! ? or …). Never leave the final sentence dangling, mid-clause,
or mid-word. If you are running out of room, drop an entire sentence rather than
truncate one. Prefer fewer, complete sentences over more, partial ones.

══════════════════════════════════════════════════════════════
CHANNEL CONTEXT
══════════════════════════════════════════════════════════════
Channel: {{channel_name}}
Channel Primary Focus: {{channel_primary_topic}}

This channel typically covers {{channel_primary_topic}}. Include every subtopic
with confidence >= 0.40.

══════════════════════════════════════════════════════════════
TOPIC TAXONOMY (use these canonical slugs only)
══════════════════════════════════════════════════════════════
Main topics: ekonomi, siyaset, spor, teknoloji

Economy/finance subtopics — assign one of these when the transcript discusses
any of the terms listed after the colon (these are coverage hints, not an
exhaustive list; the slug applies to the whole topic area):

  bist-turk-piyasalari  : BIST, Borsa İstanbul, XU100, XU030, BIST 50, hisse senedi,
                          endeks, banka/sanayi hisseleri, yerli piyasa, yerli borsa
  altin                 : ons altın, gram altın, çeyrek/yarım/tam/cumhuriyet altını,
                          gold, XAU, altın fiyatı, altın yatırımı
  gumus                 : ons gümüş, silver, XAG, gümüş yatırımı
  doviz-kur             : USD/TRY, EUR/TRY, GBP/TRY, EUR/USD, DXY, dolar, euro,
                          sterlin, döviz, kur, parite, döviz kuru
  kripto-paralar        : Bitcoin, BTC, Ethereum, ETH, altcoin, stablecoin, USDT,
                          kripto, kripto para, kripto borsası, Binance, Coinbase
  amerikan-piyasalari   : Dow Jones, S&P 500, Nasdaq, Wall Street, ABD borsaları,
                          US futures, Magnificent 7, Amerikan endeksleri, Tesla/Apple/
                          Nvidia gibi ABD hisseleri
  petrol-enerji         : Brent, WTI, ham petrol, petrol, doğalgaz, LNG, OPEC,
                          OPEC+, rafineri, enerji fiyatları, benzin, motorin
  faiz-para-politikasi  : TCMB/Fed/ECB/BoJ/BoE faizi, politika faizi, mevduat
                          faizi, repo, tahvil faizi, para politikası, FOMC, PPK
  enflasyon             : TÜFE, ÜFE, çekirdek enflasyon, ENAG, manşet enflasyon,
                          fiyat artışı, sepet, enflasyon beklentisi

Politics subtopics:
  ic-siyaset            : TBMM, seçim, parti, muhalefet, iktidar, kabine, bakanlık,
                          yasalar, Türkiye iç politikası
  jeopolitik            : savaş, ateşkes, İran/ABD/Rusya/Ukrayna/İsrail/Çin,
                          NATO, AB, BM, diplomatik kriz, yaptırım, dış politika

══════════════════════════════════════════════════════════════
CLASSIFICATION RULES (strict)
══════════════════════════════════════════════════════════════
1. MATCH BROADLY: assign a subtopic whenever the transcript discusses ANY of
   the terms listed in that subtopic's coverage hint above. Do not require an
   exact slug-name match. Example: if the speaker discusses Nasdaq or Wall
   Street, use `amerikan-piyasalari`.

2. CONFIDENCE THRESHOLDS:
   - >= 0.70  : Strong match — the speaker clearly discusses this subtopic
                with specific data, opinions, or analysis.
   - 0.40-0.69: Moderate match — the subtopic is mentioned with some
                substance but not the primary focus.
   - 0.30-0.39: Weak match — brief mention or passing reference. Only
                include when the subtopic is in the channel's expected list.
   - < 0.30   : Do NOT include.

3. Use canonical subtopic slugs listed above. Do NOT invent new slugs and
   do NOT use "other:*".

4. Do NOT suppress a non-expected subtopic. If the transcript clearly
   discusses a subtopic (confidence >= 0.40) include it, even if the
   channel's expected list does not mention it.

5. Capture EVERY numeric level the speaker names — price targets, support/
   resistance, percentages, durations, index levels. Put them in `key_levels`
   as short strings (e.g. "60 dolar", "%20", "3-6 ay", "4.700").

6. IGNORE TEASERS: Many videos start with a short fragman (teaser/preview montage of upcoming
   content). These are typically the first 30–90 seconds and consist of rapid, disjointed
   sentence fragments previewing later discussion. Do NOT create topic_segments from teaser
   content. Only classify substantive, developed discussion.

7. Maximum 8 topic_segments per video. Prioritize by confidence descending.

8. start_time and end_time MUST be a single MM:SS or HH:MM:SS value (not a
   range with a dash). Use the first transcript marker where the topic is
   clearly introduced for start_time, and the last where it is still active
   for end_time.

══════════════════════════════════════════════════════════════
JSON SCHEMA
══════════════════════════════════════════════════════════════
{
  "language": "string (ISO 639-1)",
  "summary": {
    "short": "1-3 sentence summary",
    "detailed": "detailed paragraph summary"
  },
  "key_points": ["key takeaway or insight (plain string, max 15 words)"],
  "primary_topic": {
    "label": "one main topic from taxonomy",
    "confidence": 0.0
  },
  "topic_segments": [
    {
      "subtopic": "canonical subtopic slug from taxonomy",
      "is_expected": true,
      "summary": "3-5 sentence summary of what the speaker says",
      "evidence": "short quote or paraphrase from transcript",
      "key_levels": ["every numeric level mentioned, as short strings"],
      "start_time": "MM:SS (single value, no range)",
      "end_time": "MM:SS (single value, no range)",
      "stance": "positive | negative | cautious | neutral | mixed",
      "confidence": 0.0
    }
  ]
}

══════════════════════════════════════════════════════════════
FIELD INSTRUCTIONS
══════════════════════════════════════════════════════════════
- summary.short: concise 1-3 sentence overview. Every sentence must be fully
  completed with terminal punctuation. No dangling final sentence.
- summary.detailed: comprehensive paragraph covering main points. Must end with
  a fully completed sentence and terminal punctuation.
- key_points: list of up to 5 plain strings (not objects), each a short
  bullet-style takeaway, max 15 words.
- primary_topic.label: the single dominant focus of the transcript.
- primary_topic.confidence: how strongly the transcript matches this topic.
- topic_segments: one entry per distinct subtopic discussed.
  - subtopic: use one canonical taxonomy slug only.
  - is_expected: true if subtopic is in the channel's expected list, false otherwise.
  - summary: 3-5 sentences capturing speaker's full opinion, reasoning, key
    price levels, and outlook. Every sentence must be fully completed with
    terminal punctuation — never cut off mid-sentence. If you cannot finish a
    sentence, drop it entirely and end with the previous completed one.
  - key_levels: include EVERY price/target/support/resistance/percentage/
    duration mentioned. Short strings. Never empty when the speaker cites
    numbers for this topic.
  - start_time/end_time: SINGLE MM:SS value each, never a dash-separated range.
  - stance: for finance: positive=bullish, negative=bearish, cautious=aware of
    downside but not selling, neutral=no directional view, mixed=both sides.
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
  "key_points": ["string"]
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

ECONOMIC_THESIS_PROMPT_TEMPLATE = """\
You are a senior economic-content analyst for a Turkish-language media tracking platform.

Analyze the transcript below and extract the speaker's MAIN ECONOMIC THESIS.
Focus on the speaker's most important economic opinion, central macro view, and the key takeaway that followers should understand.

Base analysis ONLY on transcript content.
Return valid JSON only — no markdown, no explanation.
All free-text fields must be in {{output_language}}.

IMPORTANT:
- Ignore sponsor messages, advertisements, platform promotions, discount campaigns, referral messages, "katıl" calls, and repetitive intro/outro sections unless they directly affect the speaker's economic thesis.
- Do NOT simply list all mentioned topics.
- Your main goal is to identify:
  1. the speaker's single most important economic idea in this video,
  2. the main reasoning behind it,
  3. the practical implication for followers/investors,
  4. the strongest risk or warning emphasized by the speaker.
- Prefer depth over coverage.
- If many assets/topics are mentioned, determine which ones are central to the speaker's macro thesis and which are secondary examples.
- Distinguish between:
  - root cause,
  - economic transmission mechanism,
  - conclusion,
  - investment/public takeaway.

══════════════════════════════════════════════════════════════
ECONOMIC ANALYSIS RULES
══════════════════════════════════════════════════════════════
1. Find the CORE THESIS:
   What is the speaker mainly trying to say about the current economy?

2. Identify the MAIN DRIVER:
   What does the speaker present as the main cause of the current economic outlook?
   Examples:
   - geopolitical conflict
   - inflation shock
   - central bank policy
   - fiscal stress
   - currency pressure
   - credit risk
   - recession risk
   - energy prices
   - supply shock

3. Identify the ECONOMIC CHAIN:
   Capture the speaker's causal logic in this form:
   cause → transmission → macro effect → market effect → takeaway

4. Identify FOLLOWER TAKEAWAY:
   What is the most important practical message for followers?
   Examples:
   - avoid risk
   - hold cash
   - stay in deposits
   - be cautious on stocks
   - inflation risk is underestimated
   - policy easing is unlikely

5. Importance rule:
   If the transcript mentions many assets (gold, bitcoin, BIST, dollar, etc.),
   do NOT treat all equally.
   Select the one(s) that are central to the speaker's real thesis.
   Mentions used only as examples should be secondary.

6. Confidence:
   Be strict.
   High confidence only if the speaker clearly repeats, supports, and builds the argument.

══════════════════════════════════════════════════════════════
JSON SCHEMA
══════════════════════════════════════════════════════════════
{
  "language": "string (ISO 639-1)",
  "economic_thesis": {
    "title": "very short thesis title",
    "statement": "1-3 sentence statement of the speaker's main economic opinion",
    "confidence": 0.0
  },
  "main_view_for_followers": {
    "summary": "the most important takeaway followers should understand",
    "action_bias": "risk_off | risk_on | cautious | neutral | mixed",
    "confidence": 0.0
  },
  "macro_outlook": {
    "direction": "positive | negative | mixed | uncertain",
    "summary": "speaker's overall macro view"
  },
  "primary_driver": {
    "label": "main economic driver",
    "summary": "why this is the main driver according to the speaker"
  },
  "economic_chain": {
    "cause": "root cause described by the speaker",
    "transmission": [
      {"step": "how the shock spreads through the economy"}
    ],
    "macro_effect": [
      {"effect": "impact on inflation / growth / rates / risk sentiment / etc."}
    ],
    "market_effect": [
      {"effect": "impact on stocks / gold / bitcoin / FX / bonds / etc."}
    ],
    "final_takeaway": "the speaker's end conclusion"
  },
  "top_insights": [
    {
      "insight": "important economic insight",
      "importance": 0.0
    }
  ],
  "key_warnings": [
    {
      "warning": "main warning or risk emphasized by the speaker",
      "severity": "low | medium | high"
    }
  ],
  "important_for_followers": [
    {
      "point": "practical takeaway for followers/investors"
    }
  ],
  "supporting_evidence": [
    {
      "evidence": "short quote or close paraphrase from transcript"
    }
  ],
  "secondary_topics": [
    {
      "topic": "secondary topic or asset mention",
      "role": "example | consequence | supporting argument | recommendation"
    }
  ]
}

══════════════════════════════════════════════════════════════
FIELD INSTRUCTIONS
══════════════════════════════════════════════════════════════
- economic_thesis.title:
  Very short label capturing the thesis.
  Example: "Riskli varlıklarda erken iyimserlik"

- economic_thesis.statement:
  The speaker's single most important economic idea in this video.

- main_view_for_followers.summary:
  What should followers remember after watching this video?

- action_bias:
  Use:
  - risk_off   → speaker advises defensive positioning
  - risk_on    → speaker supports taking more risk
  - cautious   → balanced but careful
  - neutral    → no clear positioning
  - mixed      → mixed advice

- macro_outlook.direction:
  Overall economic tone.

- primary_driver.label:
  Use a short plain label such as:
  geopolitical_conflict, inflation_shock, energy_prices, central_bank_policy,
  fiscal_risk, currency_pressure, credit_stress, recession_risk, stagflation_risk

- economic_chain.transmission:
  Break the reasoning into short causal steps.

- top_insights:
  Up to 5 only. Prioritize strongest and most central insights.

- key_warnings:
  Include only the strongest warnings.

- secondary_topics:
  Include topics mentioned but NOT central to the thesis.

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

Main topics: ekonomi, siyaset, spor, teknoloji
Economy subtopics: bist-turk-piyasalari, altin, gumus, doviz-kur, kripto-paralar,
                   amerikan-piyasalari, petrol-enerji, faiz-para-politikasi, enflasyon
Politics subtopics: ic-siyaset, jeopolitik

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

# ---------------------------------------------------------------------------
# Market inference prompt — daily consolidated market view.
# Called once per topic (e.g. "bitcoin", "dolar-tl") with weighted sources.
# ---------------------------------------------------------------------------

MARKET_INFERENCE_PROMPT_TEMPLATE = """\
Sen Türkçe yayın takip platformu için makroekonomik çıkarım uzmanısın.
Aşağıda belirtilen konu hakkında verilen kaynaklardan piyasa beklentisini çıkar.
Yanıtını SADECE geçerli bir JSON nesnesi olarak ver — markdown, açıklama, başlık yok.
Tüm serbest metin alanları Türkçe olmalı.

CÜMLE TAMAMLANMA KURALI:
`summary` ve her `contributions[*].note` alanı her zaman nokta, ünlem veya soru işaretiyle
biten tam bir cümleyle bitmelidir. Cümleyi asla yarım bırakma; uzunsa son cümleyi at, kesme.

YÖN VE GÜVEN BANTLARI:
- Kaynaklarda net konsensüs (pek çok kaynak aynı yön) → direction=up|down, confidence 0.75-0.95
- Karışık sinyal ama hafif ağırlık farkı → direction=mixed, confidence 0.50-0.70
- Yön yok ama tutarlı yatay beklenti → direction=sideways, confidence 0.70+
- confidence < 0.50 ile direction=up veya direction=down GEÇERSİZDİR; mixed veya sideways kullan.

AĞIRLIK KURALI:
[agirlik] değeri yüksek kaynaklar düşük ağırlıklı kaynakları eziyor; düşükler sadece güveni düşürebilir.

KONU
====
Konu Anahtari : {{topic_key}}
Konu Basligi  : {{topic_label}}

{{prev_section}}

YENI GELEN KAYNAKLAR
====================
(Sadece bu yeni kaynakları değerlendir. Önceki çıkarımı başlangıç noktası olarak kullan.)

{{sources_block}}

CIKTI JSON SEMASI
=================
{
  "direction": "up|down|sideways|mixed",
  "confidence": 0.0,
  "summary": "2-3 Türkçe tam cümle. Piyasa beklentisini net açıkla.",
  "tags": ["etiket1", "etiket2"],
  "changed_from_prev": false,
  "change_reason": null,
  "contributions": [
    {"video_id": 0, "note": "Bu kaynağın katkısı 1 cümle."}
  ]
}

changed_from_prev: Önceki çıkarım yoksa false. Yön veya güven 0.10+ değiştiyse true.
change_reason: changed_from_prev=true ise doldur, aksi halde null.
contributions: Bu çıkarımda kullanılan kaynakları listele.
"""


# ---------------------------------------------------------------------------
# Instrument outlook — "what does THIS speaker expect THIS instrument to do next?"
# One call per video, covering every finance mention in it. Output feeds the
# "kim ne bekliyor" pages; a wrong arrow under a real name is the worst failure,
# so the rules favour "none" whenever the speaker does not commit.
# ---------------------------------------------------------------------------

OUTLOOK_PROMPT_VERSION = "outlook-v3"

OUTLOOK_PROMPT_TEMPLATE = """Sen bir finans içerik analistisin. Görevin: bir YouTube videosunda KONUŞMACININ, verilen her finansal araç için GELECEKTE FİYATIN hangi yöne gideceğini bekleyip beklemediğini tespit etmek.

Video: {{video_title}}
Kanal / konuşmacı: {{speaker_name}}
Yayın tarihi: {{published_at}}

Her madde için bir finansal araç, o bölümün özeti ve transkriptten ilgili kesit verilir:

{{items}}

KARAR KURALLARI (sırayla uygula):
1. Sadece İLERİYE DÖNÜK beklenti sayılır. Geçmiş veya bugünkü fiyat hareketini aktarmak beklenti DEĞİLDİR.
   "Endeks bugün %1 düştü", "altın rekor kırdı", "dolar 49 liraya geldi" → outlook="none".
2. Aracın FİYAT YÖNÜ sorulur, ekonominin genel durumu değil.
   "Rezervler eriyor, ekonomi kötü" tek başına Dolar/TL için yön değildir → "none".
   "Kur 80 liraya çıkabilir", "TL değer kaybetmeye devam edecek" → Dolar/TL için "up".
   Borsa için "olumlu bakıyorum, alım fırsatı, yatırım yapılabilir" → "up"; "uzak durun, düşüş sürer" → "down".
3. Araç tanımına uy. Dolar/TL maddesinde yalnızca USD/TRY yönü sayılır; EUR/USD paritesi veya dolar endeksi (DXY) hakkındaki görüş Dolar/TL görüşü değildir. BIST 100 maddesinde tek bir hisse hakkındaki görüş endeks görüşü değildir.
4. outlook değerleri:
   "up"   = fiyatın yükseleceğini bekliyor
   "down" = fiyatın düşeceğini bekliyor
   "flat" = açıkça yatay / dar bant seyir bekliyor
   "none" = ileriye dönük yön belirtmiyor, sadece bilgi/rakam/seviye aktarıyor veya konuya değinip geçiyor
5. explicit=true: konuşmacının ANA SENARYOSU olarak bir yön söylemesi.
   - Doğrudan ifade: "yükselecek", "hedefim 5000", "düşüş bekliyorum", "alım fırsatı", "satış zamanı", "yukarı yönlü devamını bekliyorum".
   - Destek koşuluyla verilen teknik ana senaryo da explicit=true: "4300 üzerinde kaldıkça yükseliş bekliyorum", "80 bin üstünde kaldığı sürece hedef 92 bin". Bu, analistin beklediği yöndür.
   - Güçlü uyarı biçimi de explicit=true: "doları 80 liradan görürseniz şaşırmayın", "2027'de Brent 50 dolara düşerse şaşırmayın".
   explicit=false: yönün kendisi ana senaryo değil, sadece bir ihtimal olarak söylenmişse:
   - Alternatif / risk senaryosu: "4300 kırılırsa 4100'e düşebilir", "faiz uzun süre yüksek kalırsa terse dönebilir".
   - Dış bir OLAYA bağlı "eğer ... olursa/uzarsa" ifadeleri ("savaş uzarsa petrol yükselir", "faizler artarsa hisseler etkilenir"), konuşmacı o olayın gerçekleşmesini beklediğini AYRICA söylemiyorsa.
   Şu anki durumu anlatmak tahmin değildir → "none": "yatay seyrediyor", "denge arayışında", "dar bantta sıkıştı" (ileriye dönük "devam edecek" demiyorsa).
   - Kaçamak: "her şey olabilir", "yukarı da aşağı da gidebilir", "dikkatli olmak lazım".
   Sadece destek/direnç seviyesi saymak yön değildir → "none".
   outlook="none" ise explicit=false olmalıdır.
6. Başkasının görüşünü aktarmak ("Goldman 5000 dolar bekliyor") konuşmacının görüşü DEĞİLDİR; konuşmacı açıkça katılıyorsa sayılır.
7. Aynı araç için zıt yönler varsa konuşmacının vardığı sonucu / ana mesajı seç. Kısa vade ve uzun vade farklıysa ana vurguyu seç ve horizon alanına yaz. Eşit ağırlıkta zıt yönler → explicit=false.
8. quote: konuşmacının bu beklentiyi dile getirdiği cümle, transkript kesitinden KELİMESİ KELİMESİNE, en fazla 200 karakter. Kesitte uygun cümle yoksa "". Uydurma.
9. horizon: konuşmacı bir zaman belirttiyse kısa Türkçe ifade ("bu hafta", "yıl sonuna kadar", "orta vade"); belirtmediyse "".

ÖRNEKLER:
- Dolar/TL | "Bir sabah uyandığınızda doları 80 lira civarında görürseniz kimse şaşırmasın" → {"outlook":"up","explicit":true}
- Dolar/TL | "Merkez Bankası rezervleri erimeye devam ediyor" (yön yok) → {"outlook":"none","explicit":false}
- Dolar/TL | "EUR/USD paritesinde yükseliş bekleniyor, euro değer kazanacak" → {"outlook":"none","explicit":false}
- BIST 100 | "Endeks %1,04 düştü, işlem hacmi son 4 ayın en düşüğü" → {"outlook":"none","explicit":false}
- BIST 100 | "14.720 desteği üzerinde kalındığı sürece yukarı yönlü hareketin sürmesini bekliyorum" → {"outlook":"up","explicit":true}
- Altın | "Altın yatırımı önermiyorum, çok yukarı hareket beklemiyorum" → {"outlook":"flat","explicit":true}
- Altın | "4300 kırılırsa 4100'e kadar düşebilir" (ana senaryo değil) → {"outlook":"down","explicit":false}
- Gümüş | "Bu hafta 70-71 dolara kadar toparlanma bekliyorum" → {"outlook":"up","explicit":true}

ÇIKTI: Yalnızca geçerli JSON, açıklama veya markdown yok:
{"items":[{"mention_id":<sayı>,"outlook":"up|down|flat|none","explicit":true|false,"horizon":"","quote":"","reason":"en fazla 1 cümle Türkçe gerekçe"}]}
Girdideki her mention_id için tam bir öğe döndür."""
