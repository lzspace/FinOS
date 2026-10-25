import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, CriticalState, DesktopBridgeUnavailableState, DesktopServiceErrorState, effectiveAccountOverviewStatus, localRuntimeSummary, normalizeCategory } from "./App";
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
  it("never labels an account with a recorded balance as missing its balance", () => {
    expect(effectiveAccountOverviewStatus("MISSING_BALANCE", "CURRENT", true)).toBe("REVIEW_REQUIRED");
    expect(effectiveAccountOverviewStatus("MISSING_BALANCE", "STALE", true)).toBe("STALE");
    expect(effectiveAccountOverviewStatus("MISSING_BALANCE", "CURRENT", false)).toBe("MISSING_BALANCE");
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

  it("renders the real dashboard workspace and opens a recent transaction without mock-only cards", async () => {
    render(<App />);

    const metrics = await screen.findByRole("region", { name: "Zentrale Finanzkennzahlen" });
    for (const title of ["Einnahmen", "Ausgaben", "Netto-Cashflow", "Liquidität", "Nettovermögen"]) {
      expect(within(metrics).getByText(title)).toBeInTheDocument();
    }
    expect(screen.queryByText("Erwarteter Monatsüberschuss")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Finanzverlauf mit 5 belegten und 2 unbelegten/ })).toBeInTheDocument();
    expect(screen.getByText(/2 Zeitabschnitte ohne Buchungsdaten/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "6 Dinge benötigen Aufmerksamkeit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arbeitsbereich öffnen" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Datenaktualität" })).toHaveTextContent("3 von 4");
    expect(screen.getByRole("heading", { name: "Wofür du Geld ausgegeben hast" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Letzte Transaktionen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Miete.*1\.180,00.*69 %/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Markthalle Süd öffnen" }));
    expect(await screen.findByRole("dialog", { name: "Markthalle Süd" })).toBeInTheDocument();
  });

  it("uses the unified inbox count and opens the affected record", async () => {
    render(<App />);
    const reviewNavigation = await screen.findByRole("button", { name: /Prüfen.*6 offene Aufgaben/ });
    fireEvent.click(reviewNavigation);

    expect(await screen.findByRole("region", { name: "Arbeitsstand" })).toHaveTextContent("6 Aufgaben benötigen Aufmerksamkeit");
    expect(screen.getByRole("button", { name: /Kategorien.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI-Vorschläge.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Saldenabweichungen.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Importe.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Relationen.*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Formatänderungen.*1/ })).toBeInTheDocument();

    const categoryReview = screen.getByText("Kategorie für Markthalle Süd festlegen").closest("article");
    expect(categoryReview).not.toBeNull();
    fireEvent.click(within(categoryReview as HTMLElement).getByRole("button", { name: /Aufgabe öffnen/ }));
    expect(await screen.findByRole("dialog", { name: "Markthalle Süd" })).toBeInTheDocument();
  });

  it("integrates the workspace and actionable months into the dashboard", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("tab", { name: /Arbeitsbereich/ }));
    expect(await screen.findByRole("region", { name: "Arbeitsstand im Dashboard" })).toHaveTextContent("6");
    expect(screen.getByRole("heading", { name: "Monatsübersicht" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Status der Monate" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Monate mit offenen Punkten" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Offene Aufgaben" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Alle in „Prüfen“ öffnen/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Jahr" }));
    const year = screen.getByRole("combobox", { name: "Jahr der Monatsübersicht" });
    expect(within(year).getByRole("option", { name: "2024" })).toBeInTheDocument();
    expect(within(year).getByRole("option", { name: "2025" })).toBeInTheDocument();
    expect(within(year).getByRole("option", { name: "2026" })).toBeInTheDocument();
    fireEvent.change(year, { target: { value: "2024" } });
    expect(year).toHaveValue("2024");
    expect(screen.getByTitle(/November 2024/)).toBeInTheDocument();
  });

  it("loads category tab data only when the tab becomes visible", async () => {
    const original = window.__FINANCE_IPC__!;
    const invoke = vi.fn(original.invoke.bind(original));
    window.__FINANCE_IPC__ = { ...original, invoke };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Kategorien/ }));
    await screen.findByRole("heading", { name: "Ausgaben nach Kategorien" });
    const initialQueries = invoke.mock.calls.map((call) => String(call[2].name));
    expect(initialQueries).toContain("GetCategoryBreakdown");
    expect(initialQueries).toContain("GetCategorySuggestionReviewSummary");
    expect(initialQueries).not.toContain("ListPendingCategorySuggestions");
    expect(initialQueries).not.toContain("ListClassificationRules");
    expect(initialQueries).not.toContain("ListCategories");

    fireEvent.click(screen.getByRole("tab", { name: /Vorschläge/ }));
    await waitFor(() => expect(invoke.mock.calls.map((call) => String(call[2].name)))
      .toContain("ListPendingCategorySuggestions"));
    window.__FINANCE_IPC__ = original;
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

  it("shows all open AI suggestions by default and makes the selected month an explicit filter", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Kategorien/ }));
    fireEvent.click(await screen.findByRole("tab", { name: /Vorschläge/ }));

    expect(await screen.findByRole("button", { name: "Alle offenen (2)" })).toHaveClass("active");
    expect(screen.getByText("Café Morgen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Nur Oktober 2026/ }));

    expect(await screen.findByText("Keine offenen Vorschläge im Oktober 2026")).toBeInTheDocument();
    expect(screen.getByText(/Wechsle zu „Alle offenen“/)).toBeInTheDocument();
    expect(screen.queryByText("Für diesen Zeitraum liegen keine Daten vor.")).not.toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    const group = await screen.findByRole("group", { name: "Zeitraumsteuerung" });
    expect(within(group).getByRole("tab", { name: "Monat" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(within(group).getByRole("tab", { name: "Jahr" }));
    expect(within(group).getByRole("tab", { name: "Jahr" })).toHaveAttribute("aria-selected", "true");
    expect(within(group).getByLabelText("Jahr auswählen")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Kontenübersicht · 2026/ })).toBeInTheDocument();

    fireEvent.click(within(group).getByRole("tab", { name: "Zeitraum" }));
    expect(within(group).getByRole("tab", { name: "Zeitraum" })).toHaveAttribute("aria-selected", "true");
    expect(within(group).getByLabelText("Startdatum")).toBeInTheDocument();
    expect(within(group).getByLabelText("Enddatum")).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Vorheriger Zeitraum" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Nächster Zeitraum" })).toBeInTheDocument();
  });

  it("keeps an incomplete custom range out of finance queries until it is valid and applied", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    const group = await screen.findByRole("group", { name: "Zeitraumsteuerung" });
    fireEvent.click(within(group).getByRole("tab", { name: "Zeitraum" }));

    const start = within(group).getByLabelText("Startdatum");
    const end = within(group).getByLabelText("Enddatum");
    fireEvent.change(start, { target: { value: "" } });

    expect(within(group).getByRole("alert")).toHaveTextContent("vollständig angegeben");
    expect(within(group).getByRole("button", { name: "Anwenden" })).toBeDisabled();
    expect(await screen.findByRole("heading", { name: /Kontenübersicht/ })).toBeInTheDocument();

    fireEvent.change(start, { target: { value: "2026-10-20" } });
    fireEvent.change(end, { target: { value: "2026-10-01" } });
    expect(within(group).getByRole("alert")).toHaveTextContent("nicht vor dem Startdatum");

    fireEvent.change(start, { target: { value: "2020-01-01" } });
    fireEvent.change(end, { target: { value: "2026-10-22" } });
    expect(within(group).getByRole("alert")).toHaveTextContent("höchstens 1.096 Tage");

    fireEvent.change(start, { target: { value: "2026-10-01" } });
    fireEvent.change(end, { target: { value: "2026-10-22" } });
    fireEvent.click(within(group).getByRole("button", { name: "Anwenden" }));

    expect(await screen.findByRole("heading", { name: "Kontenübersicht · 01.10.2026 – 22.10.2026" })).toBeInTheDocument();
  });

  it("moves an applied custom range atomically", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    const group = await screen.findByRole("group", { name: "Zeitraumsteuerung" });
    fireEvent.click(within(group).getByRole("tab", { name: "Zeitraum" }));
    fireEvent.change(within(group).getByLabelText("Startdatum"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(group).getByLabelText("Enddatum"), { target: { value: "2026-10-22" } });
    fireEvent.click(within(group).getByRole("button", { name: "Anwenden" }));
    fireEvent.click(within(group).getByRole("button", { name: "Vorheriger Zeitraum" }));

    expect(await screen.findByRole("heading", { name: "Kontenübersicht · 09.09.2026 – 30.09.2026" })).toBeInTheDocument();
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

  it("allows opening and closing balances to be added from the account balance tab", async () => {
    const original = window.__FINANCE_IPC__!;
    const invoke = vi.fn(original.invoke.bind(original));
    window.__FINANCE_IPC__ = { ...original, invoke };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Konto Girokonto öffnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Girokonto" });
    fireEvent.click(within(dialog).getByRole("tab", { name: "Salden" }));
    const entry = await within(dialog).findByRole("region", { name: "Saldo nachtragen" });
    const suggestion = await within(dialog).findByRole("region", { name: "Automatischer Saldovorschlag" });
    expect(within(suggestion).getAllByText(/5\.200,00/).length).toBeGreaterThan(0);
    expect(within(suggestion).getByText(/bestätigten gemeldeten Endsaldo Juni 2026/)).toBeInTheDocument();
    fireEvent.click(within(suggestion).getByRole("button", { name: "Vorschlag übernehmen" }));
    await waitFor(() => expect(invoke.mock.calls.some((call) =>
      call[1] === "command"
      && call[2].name === "RecordOpeningBalance"
      && (call[2].payload as Record<string, unknown>).booked_balance === "5200.00"
      && (call[2].payload as Record<string, unknown>).opening_balance_source === "REPORTED_PRIOR_CLOSING_BALANCE",
    )).toBe(true));
    expect(await within(suggestion).findByRole("status")).toHaveTextContent("Anfangssaldo für Oktober 2026 wurde übernommen");
    expect(within(suggestion).getByRole("button", { name: "Bereits übernommen" })).toBeDisabled();

    fireEvent.change(within(entry).getByRole("textbox", { name: "Saldobetrag" }), { target: { value: "1.234,56" } });
    fireEvent.change(within(entry).getByLabelText("Saldodatum"), { target: { value: "2026-06-30" } });
    fireEvent.change(within(entry).getByRole("textbox", { name: "Begründung für die Abweichung" }), { target: { value: "Bestätigter Kontoauszug" } });
    fireEvent.click(within(entry).getByRole("button", { name: "Anfangssaldo speichern" }));
    await waitFor(() => expect(invoke.mock.calls.some((call) =>
      call[1] === "command"
      && call[2].name === "RecordOpeningBalance"
      && (call[2].payload as Record<string, unknown>).booked_balance === "1234.56"
      && (call[2].payload as Record<string, unknown>).opening_balance_source === "REPORTED_PRIOR_CLOSING_BALANCE"
      && (call[2].payload as Record<string, unknown>).suggested_value === "5200.00"
      && (call[2].payload as Record<string, unknown>).adjustment_reason === "Bestätigter Kontoauszug",
    )).toBe(true));
    expect(await within(entry).findByRole("status")).toHaveTextContent("Anfangssaldo wurde nachgetragen");

    fireEvent.change(within(entry).getByRole("combobox", { name: "Saldoart" }), { target: { value: "CLOSING" } });
    fireEvent.change(within(entry).getByRole("textbox", { name: "Saldobetrag" }), { target: { value: "2.000,00" } });
    fireEvent.change(within(entry).getByLabelText("Saldodatum"), { target: { value: "2026-07-31" } });
    fireEvent.click(within(entry).getByRole("button", { name: "Endsaldo speichern" }));
    await waitFor(() => expect(invoke.mock.calls.some((call) =>
      call[1] === "command"
      && call[2].name === "RecordClosingBalance"
      && (call[2].payload as Record<string, unknown>).booked_balance === "2000.00",
    )).toBe(true));

    window.__FINANCE_IPC__ = original;
  });

  it("opens the canonical review inbox filtered to the selected account", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Offene Prüfungen: 4" }));
    expect(await screen.findByText("Offene Aufgaben dieses Kontos. Der Zähler stammt aus derselben zentralen Inbox.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Arbeitsstand" })).getByText("4", { selector: "dd" })).toBeInTheDocument();
  });

  it("keeps a depot's position table separate from a normal checking-account balance", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Depot Wertpapierdepot öffnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Wertpapierdepot" });
    fireEvent.click(within(dialog).getByRole("tab", { name: "Positionen" }));
    expect(await within(dialog).findByText("ABC123")).toBeInTheDocument();
  });

  it("navigates between months inside the account workspace", async () => {
    const original = window.__FINANCE_IPC__!;
    const invoke = vi.fn(original.invoke.bind(original));
    window.__FINANCE_IPC__ = { ...original, invoke };
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Konten/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Konto Girokonto öffnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Girokonto" });
    const navigation = within(dialog).getByRole("group", { name: "Monat im Kontoarbeitsbereich" });
    expect(within(navigation).getByText("Oktober 2026")).toBeInTheDocument();
    fireEvent.click(within(navigation).getByRole("button", { name: "Vorheriger Monat" }));
    expect(within(navigation).getByText("September 2026")).toBeInTheDocument();
    await waitFor(() => expect(invoke.mock.calls.some((call) =>
      call[1] === "query"
      && call[2].name === "GetAccountDetail"
      && ((call[2].payload as Record<string, unknown>).period as Record<string, unknown>).month === 9,
    )).toBe(true));
    window.__FINANCE_IPC__ = original;
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
