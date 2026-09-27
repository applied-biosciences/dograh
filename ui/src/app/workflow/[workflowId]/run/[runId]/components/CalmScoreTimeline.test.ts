import { describe, expect, it } from 'vitest';

import { scoreOnlyHistoryExport, type CalmScoreTimeline } from './CalmScoreTimeline';

describe('scoreOnlyHistoryExport', () => {
    it('keeps numeric scores and the authenticated engineered prompt trace', () => {
        const timeline: CalmScoreTimeline = {
            session_id: 'run-99', status: 'running', roles: [{
                role: 'sakinah', run_id: 99, workflow_id: 7, turns: [{
                    turn_id: 'sakinah-1', turn_index: 1, scoring_method: 'llm_evaluation', scored_at: '2026-09-27T12:00:00Z',
                    scores: { empathy: 8, unsafe_text: 'never export me' }, confidence: { empathy: 9, unsafe_text: 'nor me' }, trend: {}, significant_changes: {}, engineered_prompt: 'authorized prompt trace',
                }],
            }],
        };
        const serialized = JSON.stringify(scoreOnlyHistoryExport(timeline));
        expect(serialized).toContain('"empathy":8');
        expect(serialized).not.toContain('never export me');
        expect(serialized).not.toContain('nor me');
        expect(serialized).toContain('authorized prompt trace');
    });
});
