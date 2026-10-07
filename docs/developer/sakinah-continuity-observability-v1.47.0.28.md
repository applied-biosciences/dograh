# Sakinah continuity observability (v1.47.0.28)

The v1.47.0.28 continuity events are emitted by the deterministic runtime, not
extracted from LLM output. They reuse the existing `realtime_feedback_events`
envelope and are identified by `type=rtf-sakinah-continuity-action` with
`payload.event_type=sakinah.continuity.action`.

## Runtime locations

- `api/services/pipecat/run_pipeline.py` creates the existing in-memory
  feedback buffer before identity enrichment, emits `CALLER_PROFILE_LOOKUP`,
  and provides the shared event callback to the Sakinah runtime.
- `api/services/sakinah/continuity.py` performs the inbound profile lookup and
  builds the bounded continuity result. It never places raw transcript data in
  the action event.
- `api/services/sakinah/pin_runtime.py` emits `CONTINUITY_CHOICE` only after an
  explicit Continue/New decision, then emits `CONTINUITY_RETRIEVAL` after the
  backend result and `CONTINUITY_CONTEXT_INJECTED` immediately before the
  bounded context is applied to Main Support. Safety bypasses are reported as
  bypassed actions and do not query history.
- `api/services/pipecat/realtime_feedback_events.py` defines the action-event
  envelope. The runtime callback deduplicates on internal call, turn, and
  action identity before streaming and persisting the event.

## Surfaces and privacy

The event is streamed over the existing WebSocket, stored in
`WorkflowRun.logs.realtime_feedback_events`, and retained with its `turn`
correlation. The live and historical conversation adapters render it as an
`INTERNAL ACTION` highlighted box. Exported transcript text uses the same safe
box format. Action details contain bounded counts, categories, redacted
references, and bounded summaries only; raw transcripts are never injected and
`raw_transcripts_injected` is always false for this path.

The Sakinah agent definition was not changed.
