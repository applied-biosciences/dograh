"use client";

import { Check, Copy, Loader2, Plus, Star, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    addAvatarToLibraryApiV1AvatarLibraryPost,
    deleteAvatarFromLibraryApiV1AvatarLibraryAvatarIdDelete,
    getAvatarLibraryApiV1AvatarLibraryGet,
} from "@/client/sdk.gen";
import type { AvatarLibraryEntry } from "@/client/types.gen";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

// Deterministic placeholder gradient per avatar id (no portrait available
// from the SpatialReal public API, so cards get a colored initial instead).
const CARD_GRADIENTS = [
    "from-violet-500 to-purple-700",
    "from-sky-500 to-indigo-700",
    "from-emerald-500 to-teal-700",
    "from-amber-500 to-orange-700",
    "from-rose-500 to-pink-700",
];

function gradientFor(id: string): string {
    let hash = 0;
    for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
    return CARD_GRADIENTS[Math.abs(hash) % CARD_GRADIENTS.length];
}

function AvatarCard({
    title,
    subtitle,
    selected,
    badge,
    gradient,
    onSelect,
    copyId,
    imageId,
    imageUrl,
    onDelete,
}: {
    title: string;
    subtitle?: string;
    selected: boolean;
    badge?: string;
    gradient: string;
    onSelect: () => void;
    copyId?: string;
    imageId?: string;
    imageUrl?: string | null;
    onDelete?: () => void;
}) {
    // Preference: an explicit image URL (user-provided at import), then the
    // bundled portrait at /avatars/<avatar_id>.jpg (built-ins), then the
    // initial-letter placeholder.
    const [imageFailed, setImageFailed] = useState(false);
    const imageSrc = imageUrl || (imageId ? `/avatars/${imageId}.jpg` : null);
    const showImage = Boolean(imageSrc) && !imageFailed;
    return (
        <button
            type="button"
            onClick={onSelect}
            className={cn(
                "relative flex w-36 shrink-0 flex-col overflow-hidden rounded-xl border text-left transition-shadow",
                selected
                    ? "border-primary ring-2 ring-primary"
                    : "border-border hover:shadow-md",
            )}
        >
            <div
                className={cn(
                    "flex h-32 items-center justify-center overflow-hidden bg-gradient-to-br",
                    showImage ? "bg-muted" : gradient,
                )}
            >
                {showImage ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                        src={imageSrc as string}
                        alt={title}
                        className="h-full w-full object-cover object-top"
                        onError={() => setImageFailed(true)}
                    />
                ) : (
                    <span className="text-3xl font-semibold text-white/90">
                        {title.charAt(0).toUpperCase()}
                    </span>
                )}
            </div>
            {selected && (
                <span className="absolute right-2 top-2 rounded-full bg-primary p-1 text-primary-foreground">
                    <Check className="h-3 w-3" />
                </span>
            )}
            {onDelete && (
                <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Delete ${title}`}
                    className={cn(
                        "absolute right-2 rounded-full bg-black/50 p-1.5 text-white/80 transition-colors hover:bg-destructive hover:text-white",
                        selected ? "top-9" : "top-2",
                    )}
                    onClick={(e) => {
                        e.stopPropagation();
                        onDelete();
                    }}
                    onKeyDown={(e) => e.stopPropagation()}
                >
                    <Trash2 className="h-3 w-3" />
                </span>
            )}
            {badge && (
                <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-black/50 px-2 py-0.5 text-[10px] font-medium text-white">
                    <Star className="h-2.5 w-2.5" /> {badge}
                </span>
            )}
            <div className="space-y-1 p-2">
                <div className="truncate text-xs font-medium">{title}</div>
                {copyId ? (
                    <span
                        role="button"
                        tabIndex={0}
                        className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                        title={copyId}
                        onClick={async (e) => {
                            e.stopPropagation();
                            await copyTextToClipboard(copyId);
                            toast.success("Avatar ID copied");
                        }}
                        onKeyDown={(e) => e.stopPropagation()}
                    >
                        <span className="truncate">ID: {copyId.slice(0, 8)}…</span>
                        <Copy className="h-2.5 w-2.5 shrink-0" />
                    </span>
                ) : (
                    <div className="text-[10px] text-muted-foreground">{subtitle}</div>
                )}
            </div>
        </button>
    );
}

/**
 * Visual picker for the per-workflow SpatialReal avatar.
 *
 * `value` mirrors `avatar_configuration.avatar_id`: null means "use the
 * deployment default avatar from the env". The library itself is org-wide;
 * importing an avatar here makes it available to every workflow.
 */
export function AvatarGalleryPicker({
    value,
    onChange,
}: {
    value: string | null;
    onChange: (avatarId: string | null) => void;
}) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [avatars, setAvatars] = useState<AvatarLibraryEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [addOpen, setAddOpen] = useState(false);
    const [newId, setNewId] = useState("");
    const [newName, setNewName] = useState("");
    const [newImageUrl, setNewImageUrl] = useState("");
    const [adding, setAdding] = useState(false);
    const [addError, setAddError] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<AvatarLibraryEntry | null>(null);
    const [deleting, setDeleting] = useState(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        (async () => {
            const response = await getAvatarLibraryApiV1AvatarLibraryGet();
            if (response.error) {
                toast.error(detailFromError(response.error, "Failed to load avatars"));
            } else if (response.data) {
                setAvatars(response.data.avatars);
            }
            setLoading(false);
        })();
    }, [authLoading, user]);

    const handleAdd = async () => {
        setAdding(true);
        setAddError(null);
        const response = await addAvatarToLibraryApiV1AvatarLibraryPost({
            body: {
                avatar_id: newId.trim(),
                name: newName.trim(),
                image_url: newImageUrl.trim() || null,
            },
        });
        setAdding(false);
        if (response.error) {
            setAddError(detailFromError(response.error, "Failed to add avatar"));
            return;
        }
        if (response.data) {
            setAvatars(response.data.avatars);
            // Select the freshly imported avatar — it's what the user came for.
            onChange(newId.trim());
        }
        setAddOpen(false);
        setNewId("");
        setNewName("");
        setNewImageUrl("");
        toast.success("Avatar added to library");
    };

    const handleDelete = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        const response = await deleteAvatarFromLibraryApiV1AvatarLibraryAvatarIdDelete({
            path: { avatar_id: deleteTarget.avatar_id },
        });
        setDeleting(false);
        if (response.error) {
            toast.error(detailFromError(response.error, "Failed to delete avatar"));
            setDeleteTarget(null);
            return;
        }
        if (response.data) {
            setAvatars(response.data.avatars);
        }
        // A workflow pointing at a deleted avatar falls back to Default.
        if (value === deleteTarget.avatar_id) {
            onChange(null);
        }
        toast.success(`"${deleteTarget.name}" removed from library`);
        setDeleteTarget(null);
    };

    if (loading) {
        return (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading avatars…
            </div>
        );
    }

    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-3">
                <AvatarCard
                    title="Default"
                    subtitle="From deployment env"
                    selected={value === null}
                    badge="Default"
                    gradient="from-slate-500 to-slate-700"
                    onSelect={() => onChange(null)}
                />
                {avatars.map((avatar) => (
                    <AvatarCard
                        key={avatar.avatar_id}
                        title={avatar.name}
                        selected={value === avatar.avatar_id}
                        gradient={gradientFor(avatar.avatar_id)}
                        onSelect={() => onChange(avatar.avatar_id)}
                        copyId={avatar.avatar_id}
                        imageId={avatar.avatar_id}
                        imageUrl={avatar.image_url}
                        onDelete={() => setDeleteTarget(avatar)}
                    />
                ))}
                <button
                    type="button"
                    onClick={() => {
                        setAddError(null);
                        setAddOpen(true);
                    }}
                    className="flex h-[10.5rem] w-36 shrink-0 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-muted-foreground transition-colors hover:border-primary hover:text-primary"
                >
                    <Plus className="h-6 w-6" />
                    <span className="text-xs font-medium">Add avatar by ID</span>
                </button>
            </div>
            <p className="text-xs text-muted-foreground">
                Avatars are shared across all agents. Create new ones in SpatialReal
                Studio, then import them here with their ID.
            </p>

            <AlertDialog
                open={deleteTarget !== null}
                onOpenChange={(open) => !open && setDeleteTarget(null)}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            Remove &quot;{deleteTarget?.name}&quot;?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            Agents still set to this avatar will use the default
                            avatar instead.
                            {deleteTarget?.builtin &&
                                " This is a built-in avatar — you can bring it back later by importing its ID again."}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(e) => {
                                e.preventDefault();
                                handleDelete();
                            }}
                            disabled={deleting}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {deleting && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Remove
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <Dialog open={addOpen} onOpenChange={setAddOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Import avatar</DialogTitle>
                        <DialogDescription>
                            Paste the avatar ID from SpatialReal Studio (use the copy
                            button on the avatar card) and give it a name.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5">
                            <Label htmlFor="new-avatar-id">Avatar ID</Label>
                            <Input
                                id="new-avatar-id"
                                placeholder="e.g. d5211078-994b-4346-91e2-fa9c77b71bbe"
                                value={newId}
                                onChange={(e) => setNewId(e.target.value)}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="new-avatar-name">Name</Label>
                            <Input
                                id="new-avatar-name"
                                placeholder="e.g. Omani Male"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="new-avatar-image">
                                Image URL{" "}
                                <span className="text-muted-foreground">(optional)</span>
                            </Label>
                            <Input
                                id="new-avatar-image"
                                placeholder="https://… (right-click the avatar photo in Studio → Copy image address)"
                                value={newImageUrl}
                                onChange={(e) => setNewImageUrl(e.target.value)}
                            />
                            <p className="text-xs text-muted-foreground">
                                Shown on the card. Without it the card shows the
                                avatar&apos;s initial.
                            </p>
                        </div>
                        {addError && (
                            <p className="text-sm text-destructive">{addError}</p>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setAddOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleAdd}
                            disabled={adding || !newId.trim() || !newName.trim()}
                        >
                            {adding && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Verify & add
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
