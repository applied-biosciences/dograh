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

const trajectoryColours = ['#2563eb', '#dc2626', '#059669', '#d97706', '#9333ea', '#0891b2', '#db2777', '#65a30d'];

function CalmTrajectoryGraph({ role, label, turns, category, dimensions }: { role: string; label: string; turns: CalmScoreTurn[]; category: 'safety' | 'emotional'; dimensions: string[] }) {
    if (turns.length === 0 || dimensions.length === 0) return null;
    const width = 920;
    const height = 300;
    const margin = { top: 22, right: 20, bottom: 42, left: 48 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const x = (index: number) => margin.left + (turns.length === 1 ? plotWidth / 2 : (index / (turns.length - 1)) * plotWidth);
    const y = (score: number) => margin.top + plotHeight - (Math.max(0, Math.min(10, score)) / 10) * plotHeight;
    const pointsFor = (dimension: string) => turns.map((turn, index) => {
        const score = turn.scores[dimension];
        return typeof score === 'number' && Number.isFinite(score) ? `${x(index)},${y(score)}` : null;
    }).filter((point): point is string => point !== null).join(' ');

    return (
        <div className="mt-5 rounded-md border border-border bg-background p-3" data-testid={`calm-trajectory-${role}-${category}`}>
            <h4 className="mb-2 text-sm font-semibold">{label} {category} score trajectories</h4>
            <div className="overflow-x-auto">
                <svg viewBox={`0 0 ${width} ${height}`} className="h-auto min-w-[680px] w-full" role="img" aria-label={`${label} CALM score trajectories (${category}): score values 0 to 10 on the Y-axis and scored turns on the X-axis`}>
                    {[0, 2, 4, 6, 8, 10].map((tick) => (
                        <g key={tick}>
                            <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} stroke="currentColor" className="text-border" strokeDasharray="3 3" />
                            <text x={margin.left - 10} y={y(tick) + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">{tick}</text>
                        </g>
                    ))}
                    {turns.map((turn, index) => (
                        <g key={`${turn.turn_id ?? turn.turn_index}-${index}`}>
                            <line x1={x(index)} x2={x(index)} y1={height - margin.bottom} y2={height - margin.bottom + 5} stroke="currentColor" className="text-muted-foreground" />
                            <text x={x(index)} y={height - margin.bottom + 20} textAnchor="middle" className="fill-muted-foreground text-[11px]">{turn.turn_index}</text>
                        </g>
                    ))}
                    <line x1={margin.left} x2={margin.left} y1={margin.top} y2={height - margin.bottom} stroke="currentColor" className="text-muted-foreground" />
                    <line x1={margin.left} x2={width - margin.right} y1={height - margin.bottom} y2={height - margin.bottom} stroke="currentColor" className="text-muted-foreground" />
                    {dimensions.map((dimension, index) => {
                        const points = pointsFor(dimension);
                        if (!points) return null;
                        const colour = trajectoryColours[index % trajectoryColours.length];
                        return <polyline key={dimension} points={points} fill="none" stroke={colour} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />;
                    })}
                </svg>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Score names">
                {dimensions.map((dimension, index) => <span key={dimension} className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: trajectoryColours[index % trajectoryColours.length] }} />line: {dimension}</span>)}
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">Y-axis: score (0–10) · X-axis: turn</p>
        </div>
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
                        <div data-testid={`calm-trajectory-${role.role}`}>
                            {(['safety', 'emotional'] as const).map((category) => {
                                const dimensions = Array.from(new Set(role.turns.flatMap((turn) => Object.keys(turn.scores))))
                                    .filter((dimension) => category === 'safety'
                                        ? dimension.toLowerCase().startsWith('safety')
                                        : !dimension.toLowerCase().startsWith('safety'));
                                return <CalmTrajectoryGraph key={category} role={role.role} label={roleLabel(role.role)} turns={role.turns} category={category} dimensions={dimensions} />;
                            })}
                        </div>
                    </section>
                ))}
            </CardContent>
        </Card>
    );
}
