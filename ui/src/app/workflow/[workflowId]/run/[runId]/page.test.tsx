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
                            turns: [{
                                turn_id: 's-1',
                                turn_index: 1,
                                scoring_method: 'llm_evaluation',
                                scored_at: '2026-09-27T10:00:00+00:00',
                                scores: { 'response_quality.empathy': 9 },
                                confidence: { 'response_quality.empathy': 8 },
                                trend: { 'response_quality.empathy': 'stable' },
                                significant_changes: {},
                            }],
                        },
                        {
                            role: 'service_user',
                            run_id: 12,
                            workflow_id: 2,
                            turns: [{
                                turn_id: 'u-1',
                                turn_index: 1,
                                scoring_method: 'rule_based_calm',
                                scored_at: '2026-09-27T10:00:01+00:00',
                                scores: { anxiety: 6 },
                                confidence: { anxiety: 7 },
                                trend: { parameters: { anxiety: { direction: 'worsening' } } },
                                significant_changes: {},
                            }],
                        },
                    ],
                }}
            />,
        );

        expect(screen.getByTestId('calm-score-timeline')).toBeTruthy();
        expect(screen.getByText('Sakinah')).toBeTruthy();
        expect(screen.getByText('Incoming service-user bot')).toBeTruthy();
        expect(screen.getByText('response_quality.empathy')).toBeTruthy();
        expect(screen.getByText('anxiety')).toBeTruthy();
        expect(screen.getByText('worsening')).toBeTruthy();
        expect(screen.queryByText(/prompt_sent_to_llm/i)).toBeNull();
    });
});
