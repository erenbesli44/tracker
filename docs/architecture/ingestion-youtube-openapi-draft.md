# OpenAPI Draft - `POST /ingestions/youtube`

Status: Draft  
Date: 2026-04-11  
Purpose: API contract for Phase 1A manual ingestion endpoint.

## 1. Endpoint Summary

- Method: `POST`
- Path: `/ingestions/youtube`
- Auth: none (current Phase 1 behavior)
- Idempotency: optional client `request_id` + `video.video_url` de-duplication

This endpoint stores all available data for one influencer video in one request:

1. Person (create or reuse)
2. Video (create or reuse)
3. Transcript (create/update based on flags)
4. Optional summary (create/update based on flags)
5. Optional classification (replace/keep based on flags)

## 2. OpenAPI Operation Draft (YAML)

```yaml
openapi: 3.1.0
info:
  title: Social Media Tracker API
  version: 0.1.0-draft
paths:
  /ingestions/youtube:
    post:
      tags: [ingestion]
      summary: Ingest YouTube video data for one influencer
      description: >
        Stores person, video, transcript, and optional summary/classification
        in one request. Uses create-or-reuse semantics with overwrite flags.
      operationId: ingestYoutubeVideo
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/IngestionYoutubeRequest'
      responses:
        '200':
          description: Stored successfully (created/reused/updated mix)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/IngestionYoutubeResponse'
        '409':
          description: Conflict (existing record + overwrite disabled)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '422':
          description: Validation error (invalid URL, missing required fields, invalid topic_id)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '500':
          description: Unexpected server error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

components:
  schemas:
    IngestionYoutubeRequest:
      type: object
      required: [person, video, transcript]
      properties:
        person:
          $ref: '#/components/schemas/IngestionPersonInput'
        video:
          $ref: '#/components/schemas/IngestionVideoInput'
        transcript:
          $ref: '#/components/schemas/IngestionTranscriptInput'
        summary:
          $ref: '#/components/schemas/IngestionSummaryInput'
        classification:
          $ref: '#/components/schemas/IngestionClassificationInput'
        overwrite:
          $ref: '#/components/schemas/IngestionOverwriteFlags'
        request_id:
          type: string
          maxLength: 100
          description: Optional idempotency key from client.

    IngestionPersonInput:
      type: object
      properties:
        id:
          type: integer
          minimum: 1
        name:
          type: string
          minLength: 1
          maxLength: 255
        platform:
          type: string
          maxLength: 50
          default: youtube
        platform_handle:
          type: string
          maxLength: 255
        bio:
          type: string
      description: Either person.id or person.name is required.

    IngestionVideoInput:
      type: object
      required: [video_url]
      properties:
        video_url:
          type: string
          minLength: 1
          maxLength: 500
        title:
          type: string
          maxLength: 500
        published_at:
          type: string
          format: date-time
        duration:
          type: integer
          minimum: 0

    IngestionTranscriptInput:
      type: object
      required: [raw_text]
      properties:
        raw_text:
          type: string
          minLength: 1
        language:
          type: string
          maxLength: 10
          default: tr

    IngestionSummaryInput:
      type: object
      required: [short_summary]
      properties:
        short_summary:
          type: string
          minLength: 1
        long_summary:
          type: string
        highlights:
          type: array
          items:
            type: string
        language:
          type: string
          maxLength: 10
          default: tr
        source:
          type: string
          enum: [manual, llm]
          default: manual

    IngestionClassificationInput:
      type: object
      required: [topic_mentions]
      properties:
        source:
          type: string
          enum: [manual, llm]
          default: manual
        topic_mentions:
          type: array
          minItems: 1
          items:
            $ref: '#/components/schemas/IngestionTopicMentionInput'

    IngestionTopicMentionInput:
      type: object
      required: [topic_id, summary]
      properties:
        topic_id:
          type: integer
          minimum: 1
        summary:
          type: string
          minLength: 1
        sentiment:
          type: string
          enum: [bullish, bearish, neutral]
        key_levels:
          type: array
          items:
            type: string
        start_time:
          type: string
          maxLength: 20
        end_time:
          type: string
          maxLength: 20
        confidence:
          type: number
          minimum: 0
          maximum: 1
          default: 1

    IngestionOverwriteFlags:
      type: object
      properties:
        transcript:
          type: boolean
          default: false
        summary:
          type: boolean
          default: true
        classification:
          type: boolean
          default: true

    IngestionYoutubeResponse:
      type: object
      required:
        - status
        - person_id
        - video_id
      properties:
        status:
          type: string
          enum: [stored, unchanged]
        person_id:
          type: integer
        video_id:
          type: integer
        transcript_id:
          type: integer
          nullable: true
        summary_id:
          type: integer
          nullable: true
        classification_mentions:
          type: integer
          minimum: 0
        actions:
          type: object
          properties:
            person:
              type: string
              enum: [created, reused, updated]
            video:
              type: string
              enum: [created, reused]
            transcript:
              type: string
              enum: [created, updated, skipped]
            summary:
              type: string
              enum: [created, updated, skipped]
            classification:
              type: string
              enum: [created, replaced, skipped]

    ErrorResponse:
      type: object
      properties:
        detail:
          oneOf:
            - type: string
            - type: array
              items:
                type: object
```

## 3. Server-Side Validation Rules

1. Require `video.video_url`.
2. Require `person.id` or `person.name`.
3. Validate YouTube URL/video id extraction.
4. If `classification.topic_mentions` exists, validate all `topic_id` values.
5. If summary exists, require `short_summary`.
6. If transcript exists and `overwrite.transcript=false`, return `409`.

## 4. Idempotency Rules (Draft)

1. Primary de-dup key: `video.video_url`.
2. Optional `request_id` can be persisted for replay-safe responses.
3. Repeated request with same data should not create duplicate rows.
4. Return `status=unchanged` when all entities are reused and no overwrite occurs.

## 5. Example Payloads

See [llm-phase-development-plan.md](/Users/tcebesli/Documents/self-projects/tracker/docs/architecture/llm-phase-development-plan.md) section "Phase 1A - Manual First Ingestion".

