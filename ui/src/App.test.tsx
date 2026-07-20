import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { mockQuery } from "./mockData";

describe("Finance UI contract", () => {
  it("derives navigation from the capability manifest", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Wiederkehrend/ })).toBeInTheDocument();
    expect(screen.queryByText("Vermögen")).not.toBeInTheDocument();
    expect(screen.queryByText("Steuer")).not.toBeInTheDocument();
    expect(screen.queryByText("Belege")).not.toBeInTheDocument();
  });

  it("exposes all three runtime security states without treating unchecked as passed", () => {
    const response = mockQuery("GetRuntimeSecurityStatus");
    const data = response.data as { checks: Record<string, string> };
    expect(data.checks.snapshot_integrity).toBe("PASSED");
    expect(data.checks.keychain_available).toBe("NOT_CHECKED");
    expect(Object.values(data.checks)).not.toContain("SAFE");
  });
});
