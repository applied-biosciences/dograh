"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { client } from "@/client/client.gen";
import type { VoiceInfo } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
    const [query, setQuery] = useState("");
    const [manual, setManual] = useState(false);

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
            if ((response.data.voices ?? []).length === 0) {
                setError("No HUMAIN voice profiles are available for this account.");
            }
        } catch {
            setVoices([]);
            setError("Unable to load HUMAIN voices. Check the saved API key and try again.");
        } finally {
            setLoading(false);
        }
    };

    const hasCurrentValue = value && !voices.some((voice) => voice.voice_id === value);
    const filteredVoices = voices.filter((voice) => {
        const search = query.trim().toLowerCase();
        if (!search) return true;
        return [voice.name, voice.voice_id, voice.description, voice.language, voice.accent]
            .filter(Boolean)
            .some((part) => part?.toLowerCase().includes(search));
    });

    return <div className="space-y-2">
        {manual ? (
            <Input
                value={value}
                onChange={(event) => onChange(event.target.value)}
                placeholder="Enter HUMAIN voice profile ID"
                aria-label="HUMAIN voice profile ID"
            />
        ) : <>
            <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search HUMAIN account voices"
                aria-label="Search HUMAIN account voices"
                disabled={loading || voices.length === 0}
            />
            <div className="flex gap-2">
                <Select value={value} onValueChange={onChange} disabled={loading || voices.length === 0}>
                    <SelectTrigger className="w-full"><SelectValue placeholder={loading ? "Loading HUMAIN voices…" : "Load HUMAIN account voices"} /></SelectTrigger>
                    <SelectContent>
                        {hasCurrentValue ? <SelectItem value={value}>{value}</SelectItem> : null}
                        {filteredVoices.map((voice) => <SelectItem key={voice.voice_id} value={voice.voice_id}>
                            {voice.name}{voice.description ? ` — ${voice.description}` : ""}
                        </SelectItem>)}
                    </SelectContent>
                </Select>
                <Button type="button" variant="outline" onClick={() => void load()} disabled={loading} aria-label="Load HUMAIN account voices">
                    {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                </Button>
            </div>
            {voices.length > 0 && filteredVoices.length === 0 ? <p className="text-xs text-muted-foreground">No voices match that search.</p> : null}
        </>}
        <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">The selected profile ID is saved with this configuration; API keys are never returned to the browser.</p>
            <Button type="button" variant="link" size="sm" className="shrink-0 px-0" onClick={() => setManual((current) => !current)}>
                {manual ? "Choose from account" : "Enter profile ID"}
            </Button>
        </div>
        {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
    </div>;
}
