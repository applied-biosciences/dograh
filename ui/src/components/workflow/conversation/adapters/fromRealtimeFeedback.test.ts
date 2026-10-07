import { describe, expect, it } from "vitest";

import {
    conversationItemsFromLiveFeedback,
    conversationItemsFromRealtimeFeedbackEvents,
} from "./fromRealtimeFeedback";

describe("Sakinah continuity action feedback", () => {
    const details = {
        retrieval_status: "available",
        previous_calls_loaded: 1,
        durable_facts_loaded: 2,
        raw_transcripts_injected: false,
    };

    it("renders persisted action events as internal action items", () => {
        const [item] = conversationItemsFromRealtimeFeedbackEvents([
            {
                type: "rtf-sakinah-continuity-action",
                timestamp: "2026-10-07T10:00:00.000Z",
                turn: 3,
                payload: {
                    event_type: "sakinah.continuity.action",
                    action: "CONTINUITY_RETRIEVAL",
                    call_id: "run:101",
                    turn_id: 3,
                    status: "success",
                    details,
                    action_event_id: "run:101:3:CONTINUITY_RETRIEVAL",
                },
            },
        ]);

        expect(item).toMatchObject({
            kind: "action-event",
            action: "CONTINUITY_RETRIEVAL",
            status: "success",
            turnId: 3,
            details,
        });
    });

    it("renders live action events without treating them as utterances", () => {
        const items = conversationItemsFromLiveFeedback([
            {
                id: "action-1",
                type: "sakinah-continuity-action",
                text: "CONTINUITY_CONTEXT_INJECTED",
                timestamp: "2026-10-07T10:00:00.000Z",
                action: "CONTINUITY_CONTEXT_INJECTED",
                actionStatus: "bypassed",
                actionDetails: { injection_status: "bypassed" },
                turnId: 2,
            },
        ]);

        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({
            kind: "action-event",
            status: "bypassed",
            turnId: 2,
        });
    });
});
