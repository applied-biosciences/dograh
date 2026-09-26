"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { client } from "@/client/client.gen";
import type { VoiceInfo } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

type HumainVoiceCatalog = { voices?: VoiceInfo[] };

interface HumainVoiceSelectorProps {
    value: string;
    onChange: (voiceId: string) => void;
}

/**
 * Account-profile picker for HUMAIN. The browser never receives the saved
 * credential: discovery asks the authenticated API to resolve it server-side.
 * The chosen value is the stable profile ID, rather than the mutable label.
 */
export function HumainVoiceSelector({ value, onChange }: HumainVoiceSelectorProps) {
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await client.post({
                url: "/api/v1/user/configurations/voices/humain",
                body: {},
            }) as { data?: HumainVoiceCatalog; error?: unknown };
            if (response.error || !response.data) {
                setVoices([]);
                setError("Save a HUMAIN API key first, then reload account voices.");
                return;
            }
            setVoices(response.data.voices ?? []);
            if (!value && response.data.voices?.[0]) onChange(response.data.voices[0].voice_id);
        } catch {
            setVoices([]);
            setError("Unable to load HUMAIN voices. Check the saved API key and try again.");
        } finally {
            setLoading(false);
        }
    };

    const hasCurrentValue = value && !voices.some((voice) => voice.voice_id === value);

    return <div className="space-y-2">
        <div className="flex gap-2">
            <Select value={value} onValueChange={onChange}>
                <SelectTrigger className="w-full"><SelectValue placeholder="Load HUMAIN account voices" /></SelectTrigger>
                <SelectContent>
                    {hasCurrentValue ? <SelectItem value={value}>{value}</SelectItem> : null}
                    {voices.map((voice) => <SelectItem key={voice.voice_id} value={voice.voice_id}>
                        {voice.name}{voice.description ? ` — ${voice.description}` : ""}
                    </SelectItem>)}
                </SelectContent>
            </Select>
            <Button type="button" variant="outline" onClick={() => void load()} disabled={loading} aria-label="Load HUMAIN account voices">
                {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            </Button>
        </div>
        <p className="text-xs text-muted-foreground">The selected profile ID is saved with this configuration; API keys are never returned to the browser.</p>
        {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
    </div>;
}
