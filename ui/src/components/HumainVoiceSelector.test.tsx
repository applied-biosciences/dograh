import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { HumainVoiceSelector } from "./HumainVoiceSelector";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/client/client.gen", () => ({ client: { post } }));
vi.mock("@/components/ui/select", () => ({
    Select: ({ value, onValueChange, children, disabled }: { value: string; onValueChange: (value: string) => void; children: ReactNode; disabled?: boolean }) => (
        <select aria-label="HUMAIN account voices" value={value} disabled={disabled} onChange={(event) => onValueChange(event.target.value)}>{children}</select>
    ),
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectValue: () => null,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => <option value={value}>{children}</option>,
}));

describe("HumainVoiceSelector", () => {
    it("loads descriptive account voices and persists the selected profile ID", async () => {
        post.mockResolvedValueOnce({ data: { voices: [
            { voice_id: "profile-42", name: "Maya", description: "British · en" },
            { voice_id: "profile-81", name: "Jon", description: "US · en" },
        ] } });
        const onChange = vi.fn();
        render(<HumainVoiceSelector value="" onChange={onChange} />);

        fireEvent.click(screen.getByRole("button", { name: "Load HUMAIN account voices" }));
        await screen.findByRole("option", { name: "Maya — British · en" });
        expect(post).toHaveBeenCalledWith({ url: "/api/v1/user/configurations/voices/humain", body: {} });

        fireEvent.change(screen.getByRole("combobox", { name: "HUMAIN account voices" }), { target: { value: "profile-42" } });
        expect(onChange).toHaveBeenCalledWith("profile-42");
    });

    it("supports an explicit manual profile-ID override", async () => {
        const onChange = vi.fn();
        render(<HumainVoiceSelector value="" onChange={onChange} />);

        fireEvent.click(screen.getByRole("button", { name: "Enter profile ID" }));
        fireEvent.change(screen.getByRole("textbox", { name: "HUMAIN voice profile ID" }), { target: { value: "manual-profile" } });
        await waitFor(() => expect(onChange).toHaveBeenCalledWith("manual-profile"));
    });
});
