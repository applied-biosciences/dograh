import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CalmScoreTimelineSection } from './components/CalmScoreTimeline';

describe('CalmScoreTimelineSection', () => {
    it('renders detailed, paired score-only turns at the run summary bottom', () => {
        render(
            <CalmScoreTimelineSection
                timeline={{
                    session_id: 'session-1',
                    status: 'running',
                    roles: [
                        {
                            role: 'sakinah',
                            run_id: 11,
                            workflow_id: 1,
                            turns: [
                                { turn_id: 's-1', turn_index: 1, scoring_method: 'llm_evaluation', scored_at: '2026-09-27T10:00:00+00:00', scores: { 'response_quality.empathy': 7 }, confidence: { 'response_quality.empathy': 8 }, trend: {}, significant_changes: {} },
                                { turn_id: 's-2', turn_index: 2, scoring_method: 'llm_evaluation', scored_at: '2026-09-27T10:00:01+00:00', scores: { 'response_quality.empathy': 9 }, confidence: { 'response_quality.empathy': 8 }, trend: {}, significant_changes: {} },
                            ],
                        },
                        {
                            role: 'service_user',
                            run_id: 12,
                            workflow_id: 2,
                            turns: [
                                { turn_id: 'u-1', turn_index: 1, scoring_method: 'rule_based_calm', scored_at: '2026-09-27T10:00:01+00:00', scores: { anxiety: 6 }, confidence: { anxiety: 7 }, trend: {}, significant_changes: {} },
                                { turn_id: 'u-2', turn_index: 2, scoring_method: 'rule_based_calm', scored_at: '2026-09-27T10:00:02+00:00', scores: { anxiety: 4 }, confidence: { anxiety: 7 }, trend: {}, significant_changes: {} },
                                { turn_id: 'u-3', turn_index: 3, scoring_method: 'rule_based_calm', scored_at: '2026-09-27T10:00:03+00:00', scores: { anxiety: 4 }, confidence: { anxiety: 7 }, trend: {}, significant_changes: {} },
                            ],
                        },
                    ],
                }}
            />,
        );

        expect(screen.getByTestId('calm-score-timeline')).toBeTruthy();
        expect(screen.getByText('Sakinah')).toBeTruthy();
        expect(screen.getByText('Incoming caller / service user')).toBeTruthy();
        expect(screen.getAllByText('response_quality.empathy')).toHaveLength(2);
        expect(screen.getAllByText('anxiety')).toHaveLength(3);
        expect(screen.getByText('↑ +2')).toBeTruthy();
        expect(screen.getByText('↓ -2')).toBeTruthy();
        expect(screen.getByText('= 0')).toBeTruthy();
        expect(screen.getByTestId('calm-trajectory-sakinah').textContent).toContain('line: response_quality.empathy');
        expect(screen.getByTestId('calm-trajectory-service_user').textContent).toContain('line: anxiety');
        expect(screen.getByLabelText(/Sakinah CALM score trajectories.*Y-axis.*X-axis/i)).toBeTruthy();
        expect(screen.getByLabelText(/Incoming caller.*CALM score trajectories.*Y-axis.*X-axis/i)).toBeTruthy();
        expect(screen.queryByText(/prompt_sent_to_llm/i)).toBeNull();
    });
});
