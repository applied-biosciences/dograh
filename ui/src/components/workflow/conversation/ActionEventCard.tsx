"use client";

import { Activity } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { formatConversationValue } from "./utils";

interface ActionEventCardProps {
    action: string;
    status: "success" | "bypassed" | "unavailable" | "error";
    timestamp?: string;
    callId?: string;
    turnId?: number | null;
    details: Record<string, unknown>;
}

const statusClasses = {
    success: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
    bypassed: "border-slate-500/40 text-slate-700 dark:text-slate-300",
    unavailable: "border-amber-500/40 text-amber-700 dark:text-amber-300",
    error: "border-red-500/40 text-red-700 dark:text-red-300",
} as const;

function labelize(value: string) {
    return value.replaceAll("_", " ").toLowerCase();
}

function displayDetails(details: Record<string, unknown>) {
    const preferredKeys = [
        "caller_result",
        "choice",
        "retrieval_status",
        "injection_status",
        "previous_calls_loaded",
        "durable_facts_loaded",
        "previous_calls_available",
        "durable_facts_available",
        "raw_transcripts_injected",
        "storage_source",
    ];
    return preferredKeys
        .filter((key) => details[key] !== undefined)
        .map((key) => [labelize(key), details[key]] as const);
}

export function ActionEventCard({
    action,
    status,
    timestamp,
    callId,
    turnId,
    details,
}: ActionEventCardProps) {
    const rows = displayDetails(details);
    return (
        <div className="flex justify-center">
            <div className="w-full max-w-[92%] rounded-xl border border-violet-500/30 bg-violet-500/10 px-3.5 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                    <Activity className="h-4 w-4 text-violet-600 dark:text-violet-300" />
                    <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-700 dark:text-violet-300">
                        Internal Action
                    </span>
                    <span className="font-mono text-xs font-medium text-foreground">
                        {action}
                    </span>
                    <Badge
                        variant="outline"
                        className={cn("h-5 px-1.5 text-[10px] uppercase tracking-[0.14em]", statusClasses[status])}
                    >
                        {status.toUpperCase()}
                    </Badge>
                </div>
                <div className="mt-2 grid gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2">
                    {turnId !== undefined && turnId !== null ? <span>Turn: {turnId}</span> : <span>Turn: call setup</span>}
                    {timestamp ? <span>Time: {timestamp}</span> : null}
                    {callId ? <span>Call: {callId}</span> : null}
                    {rows.map(([key, value]) => (
                        <span key={key}>
                            <span className="capitalize">{key}:</span>{" "}
                            <span className="text-foreground">{formatConversationValue(value)}</span>
                        </span>
                    ))}
                </div>
                {details.previous_calls || details.durable_fact_categories ? (
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-background/60 p-2 text-[11px] leading-4 text-foreground">
                        {formatConversationValue({
                            previous_calls: details.previous_calls,
                            durable_fact_categories: details.durable_fact_categories,
                        })}
                    </pre>
                ) : null}
            </div>
        </div>
    );
}
