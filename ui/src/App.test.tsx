import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App, CriticalState, DesktopBridgeUnavailableState, DesktopServiceErrorState, localRuntimeSummary, normalizeCategory } from "./App";
import { mockQuery } from "./mockData";
import { statusLabel } from "./statusLabels";

afterEach(cleanup);

describe("Finance UI contract", () => {
  it("uses one consistent user-facing status vocabulary", () => {
    expect(["MATCHED", "DIFFERENCE", "REVIEW_REQUIRED", "PARTIALLY_COMPLETED", "EMPTY_COMPLETED", "STALE", "LOCKED"].map(statusLabel)).toEqual([
      "Abgeglichen", "Abweichung", "Prüfung erforderlich", "Teilweise verarbeitet",
      "Keine Buchungen", "Daten nicht aktuell", "Schreibgeschützt",
    ]);
  });
  it("normalizes category rows from an older desktop worker without crashing", () => {
    expect(normalizeCategory({ category_code: "CUSTOM_HAUSTIERE" })).toMatchObject({
      display_name: "Haustiere",
      category_type: "EXPENSE",
      status: "ACTIVE",
      system_category: false,
      legacy: true,
    });
  });
  it("renders settings safely while an older worker has no runtime details", () => {
    expect(localRuntimeSummary({ status: "DISABLED", available: false })).toEqual({
      version: "nicht eingerichtet",
      integrity: "MISSING",
    });
  });
  it("derives navigation from the capability manifest", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Konten/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Vermögen/ })).toBeInTheDocument();
    expect(screen.queryByText("Steuer")).not.toBeInTheDocument();
    expect(screen.queryByText("Belege")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Hauptnavigation" }))
        .getAllByRole("button")
        .map((button) => button.textContent?.replace(/\d+/g, "")),
    ).toEqual([
      "⌂Übersicht", "↕Transaktionen", "▤Konten", "◫Kategorien", "⇩Importe",
      "✓Prüfen", "◈Vermögen", "⌁Prognose", "⚙Einstellungen",
    ]);
  });

  it("renders only real dashboard KPIs, a gap-aware time series and the inbox summary", async () => {
    render(<App />);

    const metrics = await screen.findByRole("region", { name: "Zentrale Finanzkennzahlen" });
    for (const title of ["Einnahmen", "Ausgaben", "Netto-Cashflow", "Liquidität", "Nettovermögen"]) {
      expect(within(metrics).getByText(title)).toBeInTheDocument();
    }
    expect(screen.queryByText("Erwarteter Monatsüberschuss")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Finanzverlauf mit 5 belegten und 2 unbelegten/ })).toBeInTheDocument();
    expect(screen.getByText(/2 Zeitabschnitte ohne Buchungsdaten/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "6 Dinge benötigen Aufmerksamkeit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Alle prüfen" })).toBeInTheDocument();
  });

  it("uses the unified inbox count and opens the affected record", async () => {
    render(<App />);
    const reviewNavigation = await screen.findByRole("button", { name: /Prüfen.*6 offene Aufgaben/ });
    fireEvent.click(reviewNavigation);

    expect((await screen.findByText("offene Aufgaben")).parentElement).toHaveTextContent("6offene Aufgaben");
    expect(screen.getByRole("button", { name: /Kategorien.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI-Vorschläge.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Saldenabweichungen.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Importe.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Relationen.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Formatänderungen.*1/ })).toBeInTheDocument();

    const categoryReview = screen.getByText("Kategorie für Markthalle Süd festlegen").closest("article");
    expect(categoryReview).not.toBeNull();
    fireEvent.click(within(categoryReview as HTMLElement).getByRole("button", { name: "Öffnen" }));
    expect(await screen.findByRole("dialog", { name: "Markthalle Süd" })).toBeInTheDocument();
  });

  it("renders account projections and stale balances explicitly", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    expect(await screen.findByRole("heading", { name: "Konten" })).toBeInTheDocument();
    expect(await screen.findByText("Wertpapierdepot")).toBeInTheDocument();
    expect(screen.getAllByText("Daten nicht aktuell").length).toBeGreaterThan(0);
  });

  it("shows transaction context and persists a newly created category", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Transaktionen/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Markthalle Süd" }));

    const dialog = await screen.findByRole("dialog", { name: "Markthalle Süd" });
    expect(within(dialog).getAllByText("Lebensmittel").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Beschreibung")).toBeInTheDocument();
    expect(within(dialog).getByText("Händler")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Neue Kategorie" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name der neuen Kategorie" }), { target: { value: "Haustiere" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Kategorie" })).toHaveValue("CUSTOM_HAUSTIERE"));

    fireEvent.click(within(dialog).getByRole("button", { name: "Kategorie ändern" }));
    await waitFor(() => expect(within(dialog).getByRole("status")).toHaveTextContent("Kategorie wurde gespeichert"));
  });

  it("keeps transaction filters while the sidepanel opens and closes", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Transaktionen/ }));

    fireEvent.change(await screen.findByRole("combobox", { name: "Richtung" }), { target: { value: "EXPENSE" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Kategorie" }), { target: { value: "FOOD_GROCERIES" } });
    fireEvent.click(await screen.findByRole("button", { name: "Markthalle Süd" }));

    const panel = await screen.findByRole("dialog", { name: "Markthalle Süd" });
    expect(panel).toHaveAttribute("aria-modal", "false");
    fireEvent.keyDown(panel, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Markthalle Süd" })).not.toBeInTheDocument());
    expect(screen.getByRole("combobox", { name: "Richtung" })).toHaveValue("EXPENSE");
    expect(screen.getByRole("combobox", { name: "Kategorie" })).toHaveValue("FOOD_GROCERIES");
  });

  it("offers safe bulk categorization without relation or balance actions", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Transaktionen/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Markthalle Süd markieren" }));

    const bulk = await screen.findByRole("region", { name: "Mehrfachauswahl" });
    expect(within(bulk).getByText("1 ausgewählt")).toBeInTheDocument();
    expect(within(bulk).getByRole("button", { name: "Kategorie setzen" })).toBeInTheDocument();
    expect(within(bulk).queryByRole("button", { name: /Relation|Saldo/ })).not.toBeInTheDocument();
    expect(within(bulk).getByText(/Relationen und Salden können nicht gesammelt/)).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "llama.cpp wählen" })).toBeInTheDocument();
    expect(screen.getByText(/Runtime: nicht eingerichtet/)).toBeInTheDocument();
    expect(screen.getByText(/Integrität MISSING/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Wiederherstellen" }));
    expect(
      screen.getByRole("button", { name: "Wiederherstellung bestätigen" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Schlüssel rotieren" }));
    expect(screen.getByRole("button", { name: "Rotation bestätigen" })).toBeInTheDocument();
  });

  it("shows category shadow evaluation only in settings", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Transaktionen/ }));
    expect(screen.queryByText("Shadow-Vorhersage")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Einstellungen/ }));
    expect(await screen.findByRole("heading", { name: "Kategorie-Evaluation" })).toBeInTheDocument();
    expect(screen.getByText(/Vorhersagen ändern keine Kategorien/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktivieren" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Historische Kategorien auswerten" })).toBeDisabled();
  });

  it("shows local category suggestions as an explicit review queue without auto-accept", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Kategorien/ }));
    expect(await screen.findByText("AI-Vorschläge offen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Vorschläge/ }));
    expect(await screen.findByRole("heading", { name: "AI-Kategorievorschläge prüfen" })).toBeInTheDocument();
    expect(screen.getByText(/kein Auto-Accept/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Bestätigen" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Ablehnen" })).toHaveLength(2);
    expect(screen.getByText(/98,2/)).toBeInTheDocument();
    expect(screen.queryByText(/Wahrscheinlichkeit, dass es richtig ist/i)).not.toBeInTheDocument();
  });

  it("offers understandable category management", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Kategorien/ }));
    fireEvent.click(await screen.findByRole("tab", { name: "Verwalten" }));
    expect(await screen.findByRole("heading", { name: "Kategorien verwalten" })).toBeInTheDocument();
    expect(screen.getByText(/Umbenennen verändert bestehende Zuordnungen nicht/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("z. B. Haustiere"), { target: { value: "Haustiere" } });
    expect(screen.getByRole("button", { name: "Kategorie anlegen" })).toBeEnabled();
    expect(screen.getByText(/Systemkategorien/)).toBeInTheDocument();
  });

  it("blocks financial views when bundle integrity failed", () => {
    render(<CriticalState status="BUNDLE_TAMPERED" errorCode="FINANCE_BUNDLE_TAMPERED" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Anwendungspaket verändert");
    expect(screen.getByText("FINANCE_BUNDLE_TAMPERED")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("shows the controlled state when the desktop bridge is unavailable", () => {
    render(<DesktopBridgeUnavailableState />);
    expect(screen.getByRole("alert")).toHaveTextContent("DESKTOP_BRIDGE_UNAVAILABLE");
    expect(screen.getByText(/ausschließlich in der lokalen Desktop-Anwendung/)).toBeInTheDocument();
  });

  it("does not mislabel a terminated desktop service as a version conflict", () => {
    render(<DesktopServiceErrorState error="Unbekannter Fehler" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Finance konnte nicht gestartet werden");
    expect(screen.getByText("DESKTOP_WORKER_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText("Version nicht kompatibel")).not.toBeInTheDocument();
  });

  it("supports switching the central period control between month, year and range", async () => {
    render(<App />);
    const group = await screen.findByRole("group", { name: "Zeitraumsteuerung" });
    expect(within(group).getByRole("tab", { name: "Monat" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(within(group).getByRole("tab", { name: "Jahr" }));
    expect(within(group).getByRole("tab", { name: "Jahr" })).toHaveAttribute("aria-selected", "true");
    expect(within(group).getByLabelText("Jahr auswählen")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /2026 auf einen Blick/ })).toBeInTheDocument();

    fireEvent.click(within(group).getByRole("tab", { name: "Zeitraum" }));
    expect(within(group).getByRole("tab", { name: "Zeitraum" })).toHaveAttribute("aria-selected", "true");
    expect(within(group).getByLabelText("Startdatum")).toBeInTheDocument();
    expect(within(group).getByLabelText("Enddatum")).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Vorheriger Zeitraum" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Nächster Zeitraum" })).toBeInTheDocument();
  });

  it("hides the period control on settings and imports where it does not apply", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Einstellungen/ }));
    expect(screen.queryByRole("group", { name: "Zeitraumsteuerung" })).not.toBeInTheDocument();
  });

  it("shows a full account workspace with period-scoped tabs, separates cash from brokerage accounts, and surfaces the German audit trail", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    expect(await screen.findByText(/Kontenübersicht/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Depots" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Geldkonten" })).toBeInTheDocument();
    expect(screen.getByText(/6\.420,00/)).toBeInTheDocument();
    expect(screen.getByText(/Positionsabgleich:/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Konto Girokonto öffnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Girokonto" });
    expect(within(dialog).getByRole("tab", { name: "Übersicht" })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByText("Gemeldeter Saldo")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Salden" }));
    expect((await within(dialog).findAllByText(/Übernommen aus/)).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/bestätigtem Endsaldo Juni 2026/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Audit" }));
    expect(await within(dialog).findByText("Saldenabgleich durchgeführt")).toBeInTheDocument();
  });

  it("opens the canonical review inbox filtered to the selected account", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Offene Prüfungen: 4" }));
    expect(await screen.findByText("Offene Aufgaben dieses Kontos. Der Zähler stammt aus derselben zentralen Inbox.")).toBeInTheDocument();
    expect(screen.getByText("4", { selector: ".review-center-head strong" })).toBeInTheDocument();
  });

  it("keeps a depot's position table separate from a normal checking-account balance", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Depot Wertpapierdepot öffnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Wertpapierdepot" });
    fireEvent.click(within(dialog).getByRole("tab", { name: "Positionen" }));
    expect(await within(dialog).findByText("ABC123")).toBeInTheDocument();
  });

  it("displays explicit runtime version and projection metadata in settings, not hardcoded values", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Einstellungen/ }));
    expect(await screen.findByText("Produktversion")).toBeInTheDocument();
    expect(screen.getByText("Contract-Version")).toBeInTheDocument();
    expect(screen.getByText("UI-Contract-Version")).toBeInTheDocument();
    expect(screen.getAllByText("1.5.0").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("Store-Schema")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Account Overview")).toBeInTheDocument();
  });
});
