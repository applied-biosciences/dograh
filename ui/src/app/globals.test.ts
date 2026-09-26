import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("global day theme", () => {
    it("uses the requested day background while leaving the dark token intact", () => {
        const css = readFileSync(new URL("./globals.css", import.meta.url), "utf8");
        expect(css).toMatch(/:root\s*\{[\s\S]*--background:\s*#E194F7;/);
        expect(css).toMatch(/\.dark\s*\{[\s\S]*--background:\s*oklch\(0\.145 0 0\);/);
    });
});
