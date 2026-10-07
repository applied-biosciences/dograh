import { describe, expect, it } from 'vitest';

import { s3StatusLabel } from './storageStatus';

describe('Run Details S3 status', () => {
    it.each([
        ['verified', 'AWS S3 backup ✓'],
        ['pending', 'Copying to S3…'],
        ['failed', 'S3 copy failed !'],
        ['not_configured', 'not configured –'],
        ['not_expected', 'not expected –'],
        ['unknown', 'unknown !'],
        [undefined, 'unknown !'],
    ])('renders %s as %s', (status, expected) => {
        expect(s3StatusLabel(status)).toBe(expected);
    });
});
