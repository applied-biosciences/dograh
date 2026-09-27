import { client } from "@/client/client.gen";

export interface CallReplay {
    call_id: string;
    agent_run_id: number;
    recording_signed_url: string | null;
    recording_download_url?: string | null;
    transcript_download_url?: string | null;
    expires_in: number;
    transcript: string | null;
    calm_score: Record<string, unknown>;
    safety_score: Record<string, unknown>;
    clinical_evaluation: Record<string, unknown>;
    recordings: Array<{
        track: string;
        signed_url: string;
        download_url?: string | null;
    }>;
    /** SQL-backed details remain available when a recording object cannot be replayed. */
    unavailable_recordings?: string[];
    utterances: Array<{
        id: string;
        speaker: string;
        sequence_number: number;
        start_ms: number | null;
        end_ms: number | null;
        transcript: string;
    }>;
}

/** Narrow Run Details lookup; the phone is sent in the body, never a URL. */
export async function lookupRunDetails(runId: string, phoneNumber: string): Promise<CallReplay> {
    const response = await client.post<{ 200: CallReplay }>({
        url: "/api/v1/call-history/lookup",
        body: { run_id: runId, phone_number: phoneNumber },
    });
    if (response.error || !response.data) {
        throw new Error("Unable to load run details.");
    }
    return response.data;
}

export async function getCallReplay(callId: string): Promise<CallReplay> {
    const response = await client.get<{ 200: CallReplay }>({
        url: "/api/v1/call-history/{call_id}/replay",
        path: { call_id: callId },
        query: { expires_in: 300 },
    });
    if (response.error || !response.data) {
        throw new Error("Unable to load call replay.");
    }
    return response.data;
}
