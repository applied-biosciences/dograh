export function s3StatusLabel(status?: string): string {
    if (status === 'verified') return 'AWS S3 backup ✓';
    if (status === 'pending') return 'Copying to S3…';
    if (status === 'failed') return 'S3 copy failed !';
    const label = status?.replaceAll('_', ' ') ?? 'unknown';
    const icon = status === 'not_configured' || status === 'not_expected' ? '–' : '!';
    return `${label} ${icon}`;
}
