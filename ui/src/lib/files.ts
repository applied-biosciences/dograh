import { getSignedUrlApiV1S3SignedUrlGet } from "@/client/sdk.gen";

/**
 * Get a signed URL and download a file
 */
export async function downloadFile(url: string | null) {
    if (!url) return;

    // Open a harmless target synchronously while this click still counts as a
    // user gesture. Safari can block a new window opened only after awaiting
    // the authenticated signed-URL request.
    const target = window.open("about:blank", "_blank");
    try {
        const response = await getSignedUrlApiV1S3SignedUrlGet({
            query: {
                key: url
            },
        });

        if (response.error || !response.data?.url) {
            target?.close();
            return;
        }
        // Artifact signed URLs now use Content-Disposition: attachment. A
        // pre-opened target preserves Safari's user-gesture requirement while
        // letting the storage response control the download filename/type.
        if (target && !target.closed) target.location.href = response.data.url;
        else window.location.assign(response.data.url);
    } catch (error) {
        target?.close();
        console.error('Error downloading file:', error);
    }
}

/** Download an already-authorized attachment URL returned by Run Details. */
export function downloadSignedUrl(url: string | null) {
    if (!url) return;
    const target = window.open(url, "_blank");
    if (!target) window.location.assign(url);
}

/** Download the SQL-backed transcript when its object-store copy is unavailable. */
export function downloadTextFile(text: string, filename: string) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
}

/**
 * Return a signed URL for a given S3 key without triggering a download.
 * Useful for previewing media (audio or transcript) in-browser first.
 */
export async function getSignedUrl(url: string | null, inline: boolean = false): Promise<string | null> {
    if (!url) return null;

    try {
        const response = await getSignedUrlApiV1S3SignedUrlGet({
            query: {
                key: url,
                inline: inline,
            },
        });

        if (response.data?.url) {
            return response.data.url as string;
        }
    } catch (error) {
        console.error('Error getting signed URL:', error);
    }
    return null;
}
