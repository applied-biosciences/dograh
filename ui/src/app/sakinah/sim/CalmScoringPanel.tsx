"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { downloadTextFile } from "@/lib/files";
import { cn } from "@/lib/utils";

export interface CalmScore {
    score: number;
    confidence?: number | null;
    evidence?: string[];
}

export interface CalmTrend {
    current_score?: number | null;
    previous_score?: number | null;
    delta_previous?: number | null;
    direction?: "up" | "down" | "unchanged" | "insufficient_data";
}

export interface CalmAnalysis {
    calm_scores: Record<string, CalmScore>;
    calm_confidence?: Record<string, number>;
    trend?: { parameters?: Record<string, CalmTrend>; interpretations?: string[] };
    utterance_verbatim?: string;
    prompt_sent_to_llm?: string;
    turn_id?: string | number;
}

interface CalmScoringPanelProps {
    analysis: CalmAnalysis | null;
    history?: CalmAnalysis[];
}

const numericEntries = (scores: Record<string, CalmScore>) => Object.fromEntries(
    Object.entries(scores).flatMap(([name, value]) => {
        const score = typeof value === "number" ? value : value.score;
        return typeof score === "number" && Number.isFinite(score) ? [[name, score]] : [];
    }),
);

export function simulationScoreHistoryExport(history: CalmAnalysis[]) {
    return {
        artifact_kind: "dograh-calm-score-history/v1",
        exported_at: new Date().toISOString(),
        turns: history.map((analysis, index) => ({
            turn_id: analysis.turn_id ?? index + 1,
            turn_index: index + 1,
            scores: numericEntries(analysis.calm_scores),
            confidence: analysis.calm_confidence ?? {},
        })),
    };
}

export function simulationPromptHistoryExport(history: CalmAnalysis[]) {
    return {
        artifact_kind: "dograh-sakinah-engineered-prompt-history/v1",
        exported_at: new Date().toISOString(),
        turns: history.flatMap((analysis, index) => analysis.prompt_sent_to_llm ? [{
            turn_id: analysis.turn_id ?? index + 1,
            turn_index: index + 1,
            engineered_prompt: analysis.prompt_sent_to_llm,
        }] : []),
    };
}

function trendDisplay(trend?: CalmTrend) {
    if (!trend || trend.previous_score == null || trend.direction === "insufficient_data") {
        return { symbol: "—", value: "", className: "text-muted-foreground" };
    }
    if (trend.direction === "unchanged" || trend.delta_previous === 0) {
        return { symbol: "=", value: "", className: "text-muted-foreground" };
    }
    const delta = trend.delta_previous ?? 0;
    return {
        symbol: delta > 0 ? "↑" : "↓",
        value: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}`,
        className: "text-primary",
    };
}

export function CalmScoringPanel({ analysis, history = [] }: CalmScoringPanelProps) {
    const scores = analysis?.calm_scores ?? {};
    const trends = analysis?.trend?.parameters ?? {};
    const entries = Object.entries(scores);

    return (
        <section aria-label="CALM scoring panel" className="rounded-xl border bg-card p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">CALM scoring</h2>
                    <p className="text-sm text-muted-foreground">Movement compares each score with the immediately previous turn.</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        disabled={history.length === 0}
                        onClick={() => downloadTextFile(JSON.stringify(simulationScoreHistoryExport(history), null, 2), "calm-score-history.json", "application/json;charset=utf-8")}
                    >
                        <Download className="h-4 w-4" />
                        Download score history
                    </Button>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        disabled={!history.some((item) => Boolean(item.prompt_sent_to_llm))}
                        onClick={() => downloadTextFile(JSON.stringify(simulationPromptHistoryExport(history), null, 2), "sakinah-engineered-prompt-history.json", "application/json;charset=utf-8")}
                    >
                        <Download className="h-4 w-4" />
                        Download prompt history
                    </Button>
                    <span className="text-xs uppercase tracking-[0.15em] text-muted-foreground">Stage 4</span>
                </div>
            </div>
            {entries.length === 0 ? (
                <p className="text-sm text-muted-foreground">Scores will appear after the first service-user turn.</p>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {entries.map(([parameter, rawScore]) => {
                        const score: CalmScore = typeof rawScore === "number" ? { score: rawScore } : rawScore as CalmScore;
                        const confidence = score.confidence ?? analysis?.calm_confidence?.[parameter];
                        const movement = trendDisplay(trends[parameter]);
                        return (
                            <article key={parameter} className="min-w-0 rounded-lg border bg-muted/20 p-3">
                                <p className="truncate text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                                    {parameter.replaceAll("_", " ")}
                                </p>
                                <p className="mt-2 text-2xl font-semibold tabular-nums">{score.score.toFixed(1)} <span className="text-sm font-normal text-muted-foreground">/ 10</span></p>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    Confidence: {confidence == null ? "—" : `${Math.round(confidence * 100)}%`}
                                </p>
                                <p className={cn("mt-2 text-sm font-medium tabular-nums", movement.className)} aria-label={`Previous-turn trend for ${parameter}: ${movement.symbol}${movement.value ? ` ${movement.value}` : ""}`}>
                                    <span aria-hidden="true">{movement.symbol}</span>{movement.value ? ` ${movement.value}` : ""}
                                </p>
                            </article>
                        );
                    })}
                </div>
            )}
            {analysis?.prompt_sent_to_llm ? (
                <details className="mt-4 rounded-lg border bg-muted/20 p-3">
                    <summary className="cursor-pointer text-sm font-medium">
                        Engineered prompt sent to Sakinah
                    </summary>
                    <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-background p-3 text-xs leading-relaxed text-muted-foreground">
                        {analysis.prompt_sent_to_llm}
                    </pre>
                </details>
            ) : null}
        </section>
    );
}
