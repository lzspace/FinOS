import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { mockQuery } from "./mockData";

describe("Finance UI contract", () => {
  it("derives navigation from the capability manifest", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Wiederkehrend/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Konten/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Vermögen/ })).toBeInTheDocument();
    expect(screen.queryByText("Steuer")).not.toBeInTheDocument();
    expect(screen.queryByText("Belege")).not.toBeInTheDocument();
  });

  it("renders account projections and stale balances explicitly", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    expect(await screen.findByRole("heading", { name: "Konten" })).toBeInTheDocument();
    expect(await screen.findByText("Wertpapierdepot")).toBeInTheDocument();
    expect(screen.getAllByText("Veraltet").length).toBeGreaterThan(0);
  });

  it("exposes all three runtime security states without treating unchecked as passed", () => {
    const response = mockQuery("GetRuntimeSecurityStatus");
    const data = response.data as { checks: Record<string, string> };
    expect(data.checks.snapshot_integrity).toBe("PASSED");
    expect(data.checks.keychain_available).toBe("NOT_CHECKED");
    expect(Object.values(data.checks)).not.toContain("SAFE");
  });
});
