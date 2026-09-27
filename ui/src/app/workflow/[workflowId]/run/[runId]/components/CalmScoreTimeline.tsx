import { Download } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { downloadTextFile } from '@/lib/files';

export interface CalmScoreTurn {
    turn_id: string | number | null;
    turn_index: number;
    scoring_method: string;
    scored_at: string | null;
    scores: Record<string, number | string | null>;
    confidence: Record<string, number | string | null>;
    trend: Record<string, unknown>;
    significant_changes: Record<string, unknown>;
    /** Present only on the authenticated Run Details / Agent Test contract. */
    engineered_prompt?: string;
}

export interface CalmScoreTimeline {
    session_id: string;
    status: string;
    roles: Array<{
        role: 'sakinah' | 'service_user' | string;
        run_id: number;
        workflow_id: number;
        turns: CalmScoreTurn[];
    }>;
}

/** Export numeric CALM scores without unrelated Run Details content. */
export function scoreHistoryExport(timeline: CalmScoreTimeline) {
    const numericEntries = (values: Record<string, number | string | null>) =>
        Object.fromEntries(
            Object.entries(values).filter(([, value]) => typeof value === 'number' && Number.isFinite(value)),
        );
    return {
        artifact_kind: 'dograh-calm-score-history/v1',
        exported_at: new Date().toISOString(),
        session_id: timeline.session_id,
        status: timeline.status,
        roles: timeline.roles.map((role) => ({
            role: role.role,
            run_id: role.run_id,
            workflow_id: role.workflow_id,
            turns: role.turns.map((turn) => ({
                turn_id: turn.turn_id,
                turn_index: turn.turn_index,
                scoring_method: turn.scoring_method,
                scored_at: turn.scored_at,
                scores: numericEntries(turn.scores),
                confidence: numericEntries(turn.confidence),
            })),
        })),
    };
}

/** Export the owner-authorized Sakinah prompt trace separately from scores. */
export function engineeredPromptHistoryExport(timeline: CalmScoreTimeline) {
    return {
        artifact_kind: 'dograh-sakinah-engineered-prompt-history/v1',
        exported_at: new Date().toISOString(),
        session_id: timeline.session_id,
        status: timeline.status,
        roles: timeline.roles.map((role) => ({
            role: role.role,
            run_id: role.run_id,
            workflow_id: role.workflow_id,
            turns: role.turns
                .filter((turn) => Boolean(turn.engineered_prompt))
                .map((turn) => ({
                    turn_id: turn.turn_id,
                    turn_index: turn.turn_index,
                    scoring_method: turn.scoring_method,
                    scored_at: turn.scored_at,
                    engineered_prompt: turn.engineered_prompt,
                })),
        })).filter((role) => role.turns.length > 0),
    };
}

/** @deprecated Kept as a compatibility alias for existing embedded callers. */
export const scoreOnlyHistoryExport = scoreHistoryExport;

export function downloadScoreOnlyHistory(timeline: CalmScoreTimeline) {
    downloadTextFile(
        JSON.stringify(scoreHistoryExport(timeline), null, 2),
        `calm-score-history-${timeline.session_id}.json`,
        'application/json;charset=utf-8',
    );
}

export function downloadEngineeredPromptHistory(timeline: CalmScoreTimeline) {
    downloadTextFile(
        JSON.stringify(engineeredPromptHistoryExport(timeline), null, 2),
        `sakinah-engineered-prompt-history-${timeline.session_id}.json`,
        'application/json;charset=utf-8',
    );
}

export function CalmScoreTimelineSection({
    timeline,
    title = 'CALM score timeline',
    emptyMessage,
}: {
    timeline: CalmScoreTimeline;
    title?: string;
    emptyMessage?: string;
}) {
    const rolesWithTurns = timeline.roles.filter((role) => role.turns.length > 0);
    const hasPrompts = rolesWithTurns.some((role) => role.turns.some((turn) => Boolean(turn.engineered_prompt)));

    const roleLabel = (role: string) => role === 'sakinah' ? 'Sakinah' : 'Incoming caller / service user';
    const formatValue = (value: number | string | null | undefined) => value == null ? '—' : String(value);
    const trendFor = (turn: CalmScoreTurn, previousTurn: CalmScoreTurn | undefined, dimension: string) => {
        const score = turn.scores[dimension];
        const previousScore = previousTurn?.scores[dimension];
        if (typeof score !== 'number' || typeof previousScore !== 'number') return '—';
        const change = score - previousScore;
        if (change === 0) return '= 0';
        return change > 0 ? `↑ +${change}` : `↓ ${change}`;
    };

    return (
        <Card className="border-border" data-testid="calm-score-timeline">
            <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle className="text-lg">{title}</CardTitle>
                    <div className="flex flex-wrap gap-2">
                        <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => downloadScoreOnlyHistory(timeline)} disabled={rolesWithTurns.length === 0}>
                            <Download className="h-4 w-4" />
                            Download score history
                        </Button>
                        <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => downloadEngineeredPromptHistory(timeline)} disabled={!hasPrompts}>
                            <Download className="h-4 w-4" />
                            Download prompt history
                        </Button>
                    </div>
                </div>
                <p className="text-sm text-muted-foreground">
                    Ongoing authenticated score history for this call. Simulation roles remain on their native runs; ordinary calls keep both role tracks on this run.
                </p>
            </CardHeader>
            <CardContent className="space-y-5">
                {rolesWithTurns.length === 0 && <p className="text-sm text-muted-foreground">{emptyMessage ?? 'Waiting for the first completed CALM score turn.'}</p>}
                {rolesWithTurns.map((role) => (
                    <section key={role.role} className="rounded-lg border border-border bg-muted/10 p-4">
                        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                            <div>
                                <h3 className="font-semibold text-foreground">{roleLabel(role.role)}</h3>
                                <p className="text-xs text-muted-foreground">Run {role.run_id} · {role.turns.length} scored turn{role.turns.length === 1 ? '' : 's'}</p>
                            </div>
                        </div>
                        <div className="space-y-3">
                            {role.turns.map((turn, index) => (
                                <details key={`${role.role}-${turn.turn_id ?? turn.turn_index}`} className="rounded-md border border-border bg-background px-3 py-2" open>
                                    <summary className="cursor-pointer list-none pr-6 text-sm">
                                        <span className="font-medium">Turn {turn.turn_index}</span>
                                        <span className="ml-2 text-muted-foreground">{turn.scoring_method.replaceAll('_', ' ')}</span>
                                        {turn.scored_at && <span className="ml-2 text-xs text-muted-foreground">{turn.scored_at}</span>}
                                    </summary>
                                    <div className="mt-3 overflow-x-auto">
                                        <table className="w-full min-w-[460px] text-left text-xs">
                                            <thead className="border-b border-border text-muted-foreground">
                                                <tr>
                                                    <th className="pb-2 pr-3 font-medium">Dimension</th>
                                                    <th className="pb-2 pr-3 font-medium">Score</th>
                                                    <th className="pb-2 pr-3 font-medium">Confidence</th>
                                                    <th className="pb-2 font-medium">Trend</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {Object.entries(turn.scores).map(([dimension, score]) => (
                                                    <tr key={dimension} className="border-b border-border/60 last:border-0">
                                                        <td className="py-2 pr-3 font-mono text-foreground">{dimension}</td>
                                                        <td className="py-2 pr-3 text-foreground">{formatValue(score)}</td>
                                                        <td className="py-2 pr-3 text-foreground">{formatValue(turn.confidence[dimension])}</td>
                                                        <td className="py-2 text-muted-foreground">{trendFor(turn, role.turns[index - 1], dimension)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    {turn.engineered_prompt && (
                                        <div className="mt-3 rounded-md border border-primary/20 bg-primary/5 p-3">
                                            <p className="mb-2 text-xs font-medium text-foreground">Engineered CALM prompt</p>
                                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">
                                                {turn.engineered_prompt}
                                            </pre>
                                        </div>
                                    )}
                                </details>
                            ))}
                        </div>
                    </section>
                ))}
            </CardContent>
        </Card>
    );
}
