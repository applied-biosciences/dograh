import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export interface CalmScoreTurn {
    turn_id: string | number | null;
    turn_index: number;
    scoring_method: string;
    scored_at: string | null;
    scores: Record<string, number | string | null>;
    confidence: Record<string, number | string | null>;
    trend: Record<string, unknown>;
    significant_changes: Record<string, unknown>;
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

export function CalmScoreTimelineSection({ timeline }: { timeline: CalmScoreTimeline }) {
    const rolesWithTurns = timeline.roles.filter((role) => role.turns.length > 0);
    if (rolesWithTurns.length === 0) return null;

    const roleLabel = (role: string) => role === 'sakinah' ? 'Sakinah' : 'Incoming caller / service user';
    const formatValue = (value: number | string | null | undefined) => value == null ? '—' : String(value);
    const trendFor = (turn: CalmScoreTurn, dimension: string) => {
        const direct = turn.trend[dimension];
        if (typeof direct === 'string') return direct;
        const parameters = turn.trend.parameters;
        if (parameters && typeof parameters === 'object') {
            const parameter = (parameters as Record<string, unknown>)[dimension];
            if (parameter && typeof parameter === 'object') {
                const direction = (parameter as Record<string, unknown>).direction;
                if (typeof direction === 'string') return direction;
            }
        }
        return '—';
    };

    return (
        <Card className="border-border" data-testid="calm-score-timeline">
            <CardHeader>
                <CardTitle className="text-lg">CALM score timeline</CardTitle>
                <p className="text-sm text-muted-foreground">
                    Ongoing score-only snapshots for this call. Simulation roles remain on their native runs; ordinary calls keep both role tracks on this run.
                </p>
            </CardHeader>
            <CardContent className="space-y-5">
                {rolesWithTurns.map((role) => (
                    <section key={role.role} className="rounded-lg border border-border bg-muted/10 p-4">
                        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                            <div>
                                <h3 className="font-semibold text-foreground">{roleLabel(role.role)}</h3>
                                <p className="text-xs text-muted-foreground">Run {role.run_id} · {role.turns.length} scored turn{role.turns.length === 1 ? '' : 's'}</p>
                            </div>
                        </div>
                        <div className="space-y-3">
                            {role.turns.map((turn) => (
                                <details key={`${role.role}-${turn.turn_id ?? turn.turn_index}`} className="rounded-md border border-border bg-background px-3 py-2" open={role.turns.length === 1}>
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
                                                        <td className="py-2 text-muted-foreground">{trendFor(turn, dimension)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </details>
                            ))}
                        </div>
                    </section>
                ))}
            </CardContent>
        </Card>
    );
}
