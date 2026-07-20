import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App, CriticalState } from "./App";
import { mockQuery } from "./mockData";

afterEach(cleanup);

describe("Finance UI contract", () => {
  it("derives navigation from the capability manifest", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Wiederkehrend/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Konten/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Vermögen/ })).toBeInTheDocument();
    expect(screen.queryByText("Steuer")).not.toBeInTheDocument();
    expect(screen.queryByText("Belege")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Hauptnavigation" }))
        .getAllByRole("button")
        .map((button) => button.textContent?.replace("7", "")),
    ).toEqual([
      "⌂Übersicht", "↕Transaktionen", "◫Kategorien", "▤Konten", "◈Vermögen",
      "↻Wiederkehrend", "⌁Prognose", "✓Prüfungen", "⇩Importe", "⚙Einstellungen",
    ]);
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

  it("keeps recovery local and requires explicit restore confirmation", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Einstellungen/ }));

    expect(await screen.findByRole("heading", { name: "Datensicherung" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Datenwiederherstellung" })).toBeInTheDocument();
    expect(screen.getByText("Schlüssel getrennt")).toBeInTheDocument();
    expect(screen.getByText(/Downgrade-Schutz aktiv/)).toBeInTheDocument();
    expect(screen.queryByText("Cloud-Synchronisation")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Wiederherstellen" }));
    expect(
      screen.getByRole("button", { name: "Wiederherstellung bestätigen" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Schlüssel rotieren" }));
    expect(screen.getByRole("button", { name: "Rotation bestätigen" })).toBeInTheDocument();
  });

  it("blocks financial views when bundle integrity failed", () => {
    render(<CriticalState status="BUNDLE_TAMPERED" errorCode="FINANCE_BUNDLE_TAMPERED" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Anwendungspaket verändert");
    expect(screen.getByText("FINANCE_BUNDLE_TAMPERED")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
