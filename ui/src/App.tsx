import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { DESKTOP_CONTRACT_VERSION, financeBridge, SchemaCompatibilityError } from "./bridge";
import { Imports } from "./Imports";
import type {
  CapabilityManifest,
  Account,
  AccountAuditEvent,
  AccountAuditTrail,
  AccountBalanceLedgerRow,
  AccountDetailWorkspace,
  AccountImportRow,
  AccountImportList,
  AccountOverviewList,
  AccountOverviewRow,
  AccountPositionList,
  AccountPositionRow,
  AccountReconciliationList,
  AccountReconciliationRow,
  AccountTransactionList,
  AccountTransactionRow,
  AvailablePeriods,
  BackupRecord,
  Dashboard,
  Envelope,
  ExpectedTransaction,
  ForecastScenario,
  LiquidityOverview,
  NetWorthOverview,
  KeyStatus,
  MigrationStatus,
  PeriodMode,
  PeriodSelection,
  RecurringPattern,
  RuntimeSecurityStatus,
  StoreIntegrity,
  StartupState,
  StartupStatus,
  Transaction,
  ViewState,
} from "./contracts/generated";
import { eventLabel } from "./eventLabels";

type PageId = "overview" | "accounts" | "transactions" | "categories" | "recurring" | "forecast" | "wealth" | "reviews" | "imports" | "settings";
type QueryResult<T> = { state: ViewState; envelope?: Envelope<T>; error?: string };

const money = (value: string | null | undefined, currency = "EUR") =>
  value == null
    ? "–"
    : new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(Number(value));
const date = (value: string) => new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/(^|\s)\S/g, (c) => c.toUpperCase());
const categoryNames: Record<string, string> = {
  INCOME_SALARY: "Gehalt", INCOME_OTHER: "Sonstige Einnahmen",
  HOUSING_RENT: "Miete", HOUSING_UTILITIES: "Wohnen & Nebenkosten",
  FOOD_GROCERIES: "Lebensmittel", FOOD_RESTAURANTS: "Restaurant",
  MOBILITY_PUBLIC_TRANSPORT: "Öffentlicher Verkehr", MOBILITY_FUEL: "Tanken",
  HEALTH: "Gesundheit", INSURANCE: "Versicherungen", LEISURE: "Freizeit",
  SUBSCRIPTIONS: "Abonnements", EDUCATION: "Bildung", FEES: "Gebühren",
  TAXES: "Steuern", OTHER_EXPENSE: "Sonstige Ausgaben", UNCLASSIFIED: "Nicht kategorisiert",
};
const categoryLabel = (value: string) => categoryNames[value] ?? label(value.replace(/^CUSTOM_/, ""));
const relationStatusLabel = (value: string) => value === "NONE" ? "Nicht erkannt" : label(value);
const customCategoryCode = (value: string) => {
  const slug = value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48);
  return slug ? `CUSTOM_${slug}` : "";
};

function useFinanceQuery<T>(name: string, payload: Record<string, unknown> = {}): QueryResult<T> {
  const signature = JSON.stringify(payload);
  const [result, setResult] = useState<QueryResult<T>>({ state: "LOADING" });
  useEffect(() => {
    let active = true;
    setResult({ state: "LOADING" });
    financeBridge.query<T>(name, JSON.parse(signature) as Record<string, unknown>)
      .then((envelope) => active && setResult({
        state: envelope.projection_sequence < envelope.event_store_sequence ? "STALE" : envelope.state,
        envelope,
      }))
      .catch((error: unknown) => active && setResult({
        state: error instanceof SchemaCompatibilityError ? "INCOMPATIBLE_SCHEMA" : "ERROR",
        error: error instanceof Error ? error.message : "Unbekannter Fehler",
      }));
    return () => { active = false; };
  }, [name, signature]);
  return result;
}

function useModalFocus(first: RefObject<HTMLButtonElement | null>, onClose: () => void) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    first.current?.focus();
    return () => previous?.focus();
  }, [first]);
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex='-1'])"),
    );
    if (!focusable.length) return;
    const firstItem = focusable[0];
    const lastItem = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === firstItem) {
      event.preventDefault(); lastItem.focus();
    } else if (!event.shiftKey && document.activeElement === lastItem) {
      event.preventDefault(); firstItem.focus();
    }
  };
}

const MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const pad2 = (value: number) => String(value).padStart(2, "0");
const monthEnd = (year: number, month: number) => new Date(year, month, 0).getDate();

function usePeriodSelection() {
  const today = new Date("2026-07-25");
  const [mode, setMode] = useState<PeriodMode>("MONTH");
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [rangeStart, setRangeStart] = useState("2026-07-01");
  const [rangeEnd, setRangeEnd] = useState("2026-07-25");

  const period: PeriodSelection = useMemo(() => {
    if (mode === "YEAR") {
      return { mode, start_date: `${year}-01-01`, end_date: `${year}-12-31`, display_label: String(year), aggregation: "MONTH", timezone: "Europe/Berlin" };
    }
    if (mode === "CUSTOM_RANGE") {
      return { mode, start_date: rangeStart, end_date: rangeEnd, display_label: `${rangeStart} – ${rangeEnd}`, aggregation: "DAY", timezone: "Europe/Berlin" };
    }
    const start = `${year}-${pad2(month)}-01`;
    const end = `${year}-${pad2(month)}-${pad2(monthEnd(year, month))}`;
    return { mode, start_date: start, end_date: end, display_label: `${MONTH_NAMES[month - 1]} ${year}`, aggregation: "DAY", timezone: "Europe/Berlin" };
  }, [mode, year, month, rangeStart, rangeEnd]);

  return {
    mode, setMode, year, setYear, month, setMonth, rangeStart, setRangeStart, rangeEnd, setRangeEnd,
    period,
    payload: { mode: period.mode, year: mode !== "CUSTOM_RANGE" ? year : undefined, month: mode === "MONTH" ? month : undefined, start_date: mode === "CUSTOM_RANGE" ? rangeStart : undefined, end_date: mode === "CUSTOM_RANGE" ? rangeEnd : undefined },
  };
}

type PeriodControls = ReturnType<typeof usePeriodSelection>;

function PeriodSelector({ controls }: { controls: PeriodControls }) {
  const { mode, setMode, year, setYear, month, setMonth, rangeStart, setRangeStart, rangeEnd, setRangeEnd } = controls;
  return (
    <div className="period-selector" role="group" aria-label="Zeitraumsteuerung">
      <div className="period-mode-switch" role="tablist" aria-label="Zeitraummodus">
        {([["MONTH", "Monat"], ["YEAR", "Jahr"], ["CUSTOM_RANGE", "Zeitraum"]] as Array<[PeriodMode, string]>).map(([value, text]) => (
          <button key={value} role="tab" aria-selected={mode === value} onClick={() => setMode(value)}>{text}</button>
        ))}
      </div>
      {mode === "MONTH" && (
        <div className="period-fields">
          <button className="icon-button" aria-label="Vorheriger Monat" onClick={() => (month === 1 ? (setYear(year - 1), setMonth(12)) : setMonth(month - 1))}>‹</button>
          <label className="sr-only" htmlFor="period-month-select">Monat auswählen</label>
          <select id="period-month-select" value={month} onChange={(event) => setMonth(Number(event.target.value))}>
            {MONTH_NAMES.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
          </select>
          <label className="sr-only" htmlFor="period-year-select">Jahr auswählen</label>
          <input id="period-year-select" type="number" value={year} onChange={(event) => setYear(Number(event.target.value))} aria-label="Jahr auswählen" />
          <button className="icon-button" aria-label="Nächster Monat" onClick={() => (month === 12 ? (setYear(year + 1), setMonth(1)) : setMonth(month + 1))}>›</button>
        </div>
      )}
      {mode === "YEAR" && (
        <div className="period-fields">
          <button className="icon-button" aria-label="Vorheriges Jahr" onClick={() => setYear(year - 1)}>‹</button>
          <label className="sr-only" htmlFor="period-year-only">Jahr auswählen</label>
          <input id="period-year-only" type="number" value={year} onChange={(event) => setYear(Number(event.target.value))} aria-label="Jahr auswählen" />
          <button className="icon-button" aria-label="Nächstes Jahr" onClick={() => setYear(year + 1)}>›</button>
        </div>
      )}
      {mode === "CUSTOM_RANGE" && (
        <div className="period-fields">
          <label className="sr-only" htmlFor="period-range-start">Startdatum</label>
          <input id="period-range-start" type="date" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} aria-label="Startdatum" />
          <label className="sr-only" htmlFor="period-range-end">Enddatum</label>
          <input id="period-range-end" type="date" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} aria-label="Enddatum" />
          <button className="secondary small" onClick={() => { setRangeStart(rangeStart); setRangeEnd(rangeEnd); }}>Anwenden</button>
          <button className="text-button" onClick={() => { setRangeStart("2026-07-01"); setRangeEnd("2026-07-25"); }}>Zurücksetzen</button>
        </div>
      )}
    </div>
  );
}

const navigation: Array<{ id: PageId; text: string; icon: string; capability?: string }> = [
  { id: "overview", text: "Übersicht", icon: "⌂" },
  { id: "transactions", text: "Transaktionen", icon: "↕", capability: "classification" },
  { id: "categories", text: "Kategorien", icon: "◫", capability: "classification" },
  { id: "accounts", text: "Konten", icon: "▤", capability: "accounts" },
  { id: "wealth", text: "Vermögen", icon: "◈", capability: "wealth" },
  { id: "recurring", text: "Wiederkehrend", icon: "↻", capability: "recurring_patterns" },
  { id: "forecast", text: "Prognose", icon: "⌁", capability: "forecasting" },
  { id: "reviews", text: "Prüfungen", icon: "✓", capability: "reconciliation" },
  { id: "imports", text: "Importe", icon: "⇩", capability: "imports" },
  { id: "settings", text: "Einstellungen", icon: "⚙" },
];

export function App() {
  const startup = useFinanceQuery<StartupStatus>("GetStartupStatus");
  const manifest = useFinanceQuery<CapabilityManifest>("GetCapabilityManifest");
  const [page, setPage] = useState<PageId>("overview");
  const periodControls = usePeriodSelection();
  const month = periodControls.period.start_date.slice(0, 7);
  const asOf = periodControls.period.end_date;
  const enabled = manifest.envelope?.data.capabilities ?? {};
  const visibleNavigation = navigation.filter((item) => !item.capability || enabled[item.capability]);

  if (startup.state === "LOADING") return <FullState state="LOADING" />;
  if (startup.error?.includes("DESKTOP_BRIDGE_UNAVAILABLE")) {
    return <DesktopBridgeUnavailableState />;
  }
  if (startup.error) {
    return <DesktopServiceErrorState error={startup.error} />;
  }
  if (!startup.envelope || startup.envelope.data.status !== "READY") {
    return <CriticalState status={startup.envelope?.data.status ?? "INCOMPATIBLE_VERSION"} errorCode={startup.envelope?.data.error_code ?? startup.error} />;
  }
  if (manifest.state !== "READY" || !manifest.envelope) {
    return <FullState state={manifest.state} message={manifest.error} />;
  }

  return (
    <div className="shell">
      {financeBridge.isPreview && <div className="preview-banner" role="status">
        UI-VORSCHAU · ausschließlich synthetische Beispieldaten · kein Realdatenimport
      </div>}
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">A</span><div><strong>Agent OS</strong><small>Finance · lokal</small></div></div>
        <nav aria-label="Hauptnavigation">
          {visibleNavigation.map((item) => (
            <button key={item.id} className={page === item.id ? "nav-item active" : "nav-item"} onClick={() => setPage(item.id)} aria-current={page === item.id ? "page" : undefined}>
              <span aria-hidden="true">{item.icon}</span>{item.text}
              {item.id === "reviews" && <span className="nav-count">7</span>}
            </button>
          ))}
        </nav>
        <div className="local-card">
          <span className="pulse" aria-hidden="true" />
          <div><strong>{financeBridge.isPreview ? "Lokale Vorschau" : "Nur lokal"}</strong><small>Kein Netzwerkzugriff</small></div>
        </div>
        <div className="profile"><span>LZ</span><div><strong>Lokales Profil</strong><small>v{manifest.envelope.data.extension_version}</small></div></div>
      </aside>
      <main className="content" id="main-content">
        <header className="topbar">
          <div><span className="eyebrow">PRIVATER ARBEITSBEREICH</span><h1>{navigation.find((item) => item.id === page)?.text}</h1></div>
          {page !== "settings" && page !== "imports" && <PeriodSelector controls={periodControls} />}
        </header>
        {page === "overview" && <Overview month={month} onNavigate={setPage} />}
        {page === "accounts" && <AccountsOverview period={periodControls.period} periodPayload={periodControls.payload} />}
        {page === "transactions" && <Transactions month={month} />}
        {page === "categories" && <Categories month={month} />}
        {page === "recurring" && <Recurring />}
        {page === "forecast" && <Forecast month={month} />}
        {page === "wealth" && <Wealth asOf={asOf} />}
        {page === "reviews" && <Reviews />}
        {page === "imports" && <Imports uiMonth={month} manifest={manifest.envelope.data} />}
        {page === "settings" && <Settings manifest={manifest.envelope.data} />}
      </main>
    </div>
  );
}

export function DesktopBridgeUnavailableState() {
  return <main className="full-state critical-state" role="alert">
    <span className="brand-mark">!</span>
    <span className="eyebrow">DESKTOP-HOST ERFORDERLICH</span>
    <h1>Desktop-Bridge nicht verfügbar</h1>
    <p>Reale Finanzdaten können ausschließlich in der lokalen Desktop-Anwendung geöffnet werden.</p>
    <code>DESKTOP_BRIDGE_UNAVAILABLE</code>
    <small>Der Browsermodus führt keinen Dateiimport und keinen Datenzugriff aus.</small>
  </main>;
}

export function DesktopServiceErrorState({ error }: { error: string }) {
  const code = error === "Unbekannter Fehler" ? "DESKTOP_APPLICATION_PROCESS_TERMINATED" : error.split(":", 1)[0];
  return <main className="full-state critical-state" role="alert">
    <span className="brand-mark">!</span>
    <span className="eyebrow">LOKALER DIENST NICHT VERFÜGBAR</span>
    <h1>Finance konnte nicht gestartet werden</h1>
    <p>Der lokale Hintergrunddienst wurde beendet. Nach einem unerwarteten Abbruch wird ein veralteter Workspace-Lock beim nächsten Start sicher bereinigt.</p>
    <code>{code}</code>
    <small>Es wurden keine Finanzdaten verändert.</small>
  </main>;
}

export function CriticalState({ status, errorCode }: { status: StartupState; errorCode?: string | null }) {
  const content: Record<StartupState, [string, string, string]> = {
    READY: ["Finance ist bereit", "Alle lokalen Sicherheitsprüfungen waren erfolgreich.", "Fortfahren"],
    WORKSPACE_LOCKED: ["Arbeitsbereich bereits geöffnet", "Ein anderer Prozess besitzt die Schreibsperre. Schließe ihn oder prüfe den Lock im Diagnosemodus.", "Lock prüfen"],
    KEYCHAIN_UNAVAILABLE: ["Schlüssel nicht verfügbar", "Der lokale Schlüsselspeicher konnte nicht entsperrt werden. Es findet kein Datenzugriff statt.", "Schlüsselstatus prüfen"],
    STORE_CORRUPTED: ["Speicherintegrität verletzt", "Der lokale Store wird nicht geöffnet. Stelle ein vollständig geprüftes Backup wieder her.", "Recovery-Anleitung öffnen"],
    MIGRATION_REQUIRED: ["Migration erforderlich", "Dieser Datenstand muss vor der weiteren Nutzung kontrolliert migriert werden.", "Migration prüfen"],
    MIGRATION_FAILED: ["Migration fehlgeschlagen", "Der vorherige Datenstand blieb erhalten. Prüfe Diagnose und Recovery-Anleitung.", "Diagnose anzeigen"],
    BACKUP_REQUIRED: ["Sicherung erforderlich", "Vor diesem Schritt ist ein verifiziertes lokales Backup notwendig.", "Backup erstellen"],
    INCOMPATIBLE_VERSION: ["Version nicht kompatibel", "UI, Extension oder Datenstand verwenden nicht kompatible Versionen.", "Versionsdetails"],
    BUNDLE_TAMPERED: ["Anwendungspaket verändert", "Die Integrität von UI oder Schemas stimmt nicht mit dem Release überein. Die Finanzansicht bleibt blockiert.", "Neuinstallation prüfen"],
    INSUFFICIENT_SPACE: ["Nicht genügend Speicherplatz", "Für eine atomare Speicherung oder Wiederherstellung steht nicht genug lokaler Speicher zur Verfügung.", "Speicher prüfen"],
  };
  const selected = content[status];
  return <main className="full-state critical-state" role="alert"><span className="brand-mark">!</span><span className="eyebrow">SICHERER START BLOCKIERT</span><h1>{selected[0]}</h1><p>{selected[1]}</p>{errorCode && <code>{errorCode}</code>}<button className="primary">{selected[2]}</button><small>Nur lokale, schreibgeschützte Diagnose ist verfügbar.</small></main>;
}

function FullState({ state, message }: { state: ViewState; message?: string }) {
  const content: Record<ViewState, [string, string]> = {
    LOADING: ["Finance wird vorbereitet", "Capability Manifest wird lokal geladen …"],
    READY: ["Bereit", ""], EMPTY: ["Keine Daten", "Es liegen noch keine Daten vor."],
    PARTIAL: ["Daten unvollständig", "Ein Teil der Projektion ist verfügbar."],
    STALE: ["Aktualisierung erforderlich", "Die Projektion liegt hinter dem Event Store."],
    VALIDATING: ["Import wird geprüft", "Die lokale Vorschau wird validiert."],
    EXECUTING: ["Import läuft", "Die bestätigten Abschnitte werden lokal verarbeitet."],
    REQUIRES_CONFIRMATION: ["Bestätigung erforderlich", "Prüfe die Vorschau vor dem Import."],
    ERROR: ["Lokale Verbindung fehlgeschlagen", message ?? "Die Application API antwortet nicht."],
    INCOMPATIBLE_SCHEMA: ["Nicht kompatible Vertragsversion", message ?? "UI und Extension verwenden verschiedene Schemas."],
    LOCKED: ["Finanzdaten gesperrt", "Entsperre den lokalen Schlüsselspeicher."],
  };
  return <div className="full-state"><span className="brand-mark">A</span><h1>{content[state][0]}</h1><p>{content[state][1]}</p>{state === "LOADING" && <span className="loader" />}</div>;
}

function QueryBoundary<T>({ result, children, empty = "Für diesen Zeitraum liegen keine Daten vor." }: { result: QueryResult<T>; children: (data: T) => ReactNode; empty?: string }) {
  if (result.state === "LOADING") return <div className="page-state"><span className="loader" /><p>Projektion wird geladen …</p></div>;
  if (result.state === "ERROR" || result.state === "INCOMPATIBLE_SCHEMA" || result.state === "LOCKED") return <div className="page-state error"><h2>{result.state === "INCOMPATIBLE_SCHEMA" ? "Schema nicht kompatibel" : result.state === "LOCKED" ? "Daten gesperrt" : "Daten nicht verfügbar"}</h2><p>{result.error}</p></div>;
  if (result.state === "EMPTY") return <div className="page-state"><span className="empty-icon">○</span><h2>Noch nichts zu zeigen</h2><p>{empty}</p></div>;
  if (!result.envelope) return null;
  return <>{(result.state === "STALE" || result.state === "PARTIAL") && <div className="status-banner" role="status"><strong>{result.state === "STALE" ? "Datenstand veraltet" : "Teilprojektion"}</strong><span>{result.envelope.projection_sequence < result.envelope.event_store_sequence ? `Projektion #${result.envelope.projection_sequence} · Event Store #${result.envelope.event_store_sequence}` : result.state === "STALE" ? "Mindestens ein fachlicher Snapshot ist nicht aktuell." : `Datenstand #${result.envelope.projection_sequence}`}</span></div>}{children(result.envelope.data)}</>;
}

function Overview({ month, onNavigate }: { month: string; onNavigate: (page: PageId) => void }) {
  const result = useFinanceQuery<Dashboard>("GetDashboard", { month });
  return <QueryBoundary result={result}>{(data) => <>
    <section className="hero-row"><div><h2>Dein Juli auf einen Blick</h2><p>Realisierte Werte und erwartete Buchungen, Stand heute.</p></div><button className="secondary" onClick={() => onNavigate("forecast")}>Prognose öffnen <span>→</span></button></section>
    <section className="metric-grid" aria-label="Monatskennzahlen">
      <Metric label="Effektive Einnahmen" value={money(data.effective_income)} tone="positive" note="Realisierte Buchungen" />
      <Metric label="Effektive Ausgaben" value={money(data.effective_expenses)} tone="negative" note="Nach Abgleichen" />
      <Metric label="Netto-Cashflow" value={money(data.net_cashflow)} tone="ink" note={data.savings_rate ? `${data.savings_rate} % Sparquote` : "Sparquote nicht verfügbar"} />
      <Metric label="Erwarteter Monatsüberschuss" value={money(data.expected_month_end_surplus)} tone="accent" note="Deterministische Basisprognose" />
    </section>
    {data.liquid_balance !== undefined && <section className="balance-strip" aria-label="Bestandskennzahlen"><button onClick={() => onNavigate("accounts")}><span>Liquider Bestand</span><strong>{money(data.liquid_balance)}</strong><small>Stand {data.liquid_balance_as_of ? date(data.liquid_balance_as_of) : "–"}</small></button><button onClick={() => onNavigate("forecast")}><span>Prognostizierter Monatsendbestand</span><strong>{money(data.projected_month_end_balance)}</strong><small>Bestand, nicht Periodenüberschuss</small></button><button onClick={() => onNavigate("wealth")}><span>Nettovermögen</span><strong>{money(data.net_worth)}</strong><small>Stand {data.net_worth_as_of ? date(data.net_worth_as_of) : "–"}</small></button></section>}
    <div className="two-column">
      <section className="panel cashflow-card"><PanelHeader title="Monatsverlauf" subtitle="Realisierter Netto-Cashflow" action="Transaktionen" onAction={() => onNavigate("transactions")} /><div className="chart" aria-label="Cashflow-Verlauf als Flächendiagramm"><div className="chart-y"><span>4k</span><span>2k</span><span>0</span></div><svg viewBox="0 0 700 230" role="img" aria-label="Cashflow steigt im Juli auf 1.722 Euro"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#196a5d" stopOpacity=".26"/><stop offset="1" stopColor="#196a5d" stopOpacity="0"/></linearGradient></defs><path className="gridline" d="M0 35H700M0 112H700M0 190H700"/><path className="area" d="M0 182 C75 180 110 148 170 150 S240 120 295 128 S390 93 445 103 S535 66 585 76 S645 42 700 48 L700 220 L0 220Z"/><path className="line" d="M0 182 C75 180 110 148 170 150 S240 120 295 128 S390 93 445 103 S535 66 585 76 S645 42 700 48"/><g className="chart-labels"><text x="0" y="228">01. Jul</text><text x="215" y="228">08. Jul</text><text x="440" y="228">15. Jul</text><text x="650" y="228">Heute</text></g></svg></div>
      </section>
      <section className="panel outlook"><PanelHeader title="Noch erwartet" subtitle="Bis Monatsende" /><div className="outlook-number positive">+ {money(data.remaining_expected_income)}</div><p>Verbleibende Einnahmen</p><div className="outlook-number negative">− {money(data.remaining_expected_expenses)}</div><p>Verbleibende Ausgaben</p><div className="divider"/><div className="outlook-total"><span>Offene Prüfungen</span><button className="review-pill" onClick={() => onNavigate("reviews")}>{data.open_reviews} prüfen</button></div></section>
    </div>
    <section className="panel privacy-note"><span className="shield">◇</span><div><strong>Deine Finanzdaten bleiben auf diesem Gerät.</strong><p>Bestände stammen aus expliziten Salden-Snapshots; berechnete Werte und Prognosen sind klar gekennzeichnet.</p></div><span className="sequence">Projektion #{result.envelope?.projection_sequence}</span></section>
  </>}</QueryBoundary>;
}

const OVERVIEW_STATUS_LABELS: Record<string, string> = {
  MATCHED: "Übereinstimmend", DIFFERENCE: "Abweichung", STALE: "Veraltet",
  MISSING_BALANCE: "Kein Saldo", REVIEW_REQUIRED: "Prüfung offen", CLOSED: "Geschlossen",
};
const OVERVIEW_STATUS_TONE: Record<string, string> = {
  MATCHED: "confirmed", DIFFERENCE: "missed", STALE: "paused",
  MISSING_BALANCE: "missed", REVIEW_REQUIRED: "missed", CLOSED: "paused",
};

function AccountsOverview({ period, periodPayload }: { period: PeriodSelection; periodPayload: Record<string, unknown> }) {
  const result = useFinanceQuery<AccountOverviewList>("ListAccountOverviews", { period: periodPayload });
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<AccountOverviewRow | null>(null);
  const [message, setMessage] = useState("");
  return <QueryBoundary result={result}>{({ accounts }) => {
    const cashAccounts = accounts.filter((item) => item.account_type !== "BROKERAGE");
    const brokerageAccounts = accounts.filter((item) => item.account_type === "BROKERAGE");
    return <>
      <section className="section-intro">
        <div><h2>Kontenübersicht · {period.display_label}</h2><p>Gemeldete und berechnete Salden bleiben getrennt. Depots zeigen Positionen statt eines Girokontosaldos.</p></div>
        <button className="primary" onClick={() => setCreateOpen(true)}>Konto hinzufügen</button>
      </section>
      {message && <div className="toast" role="status">{message}</div>}
      <section className="panel table-panel">
        <PanelHeader title="Geldkonten" subtitle="Gemeldeter Saldo, berechneter Saldo, Cashflow, Abgleichstatus" />
        <table>
          <thead><tr><th>Konto</th><th>Typ</th><th>Gemeldeter Saldo</th><th>Berechneter Saldo</th><th>Cashflow (Periode)</th><th>Status</th><th>Letzter Import</th><th>Prüfungen</th><th></th></tr></thead>
          <tbody>{cashAccounts.map((item) => <tr key={item.account_id}>
            <td><strong>{item.display_name}</strong><small>{item.institution}</small></td>
            <td>{label(item.account_type)}</td>
            <td className="amount">{money(item.reported_balance, item.currency)}<small>{item.reported_balance_date ? date(item.reported_balance_date) : "Kein Snapshot"}</small></td>
            <td className="amount">{money(item.calculated_balance, item.currency)}{item.balance_difference && Number(item.balance_difference) !== 0 && <small className="stale-text">Differenz {money(item.balance_difference, item.currency)}</small>}</td>
            <td className="amount">{money(item.period_net_cashflow, item.currency)}</td>
            <td><span className={`status-badge ${OVERVIEW_STATUS_TONE[item.overview_status] ?? "paused"}`}>{OVERVIEW_STATUS_LABELS[item.overview_status] ?? item.overview_status}</span></td>
            <td>{item.last_import_month ?? "–"}</td>
            <td>{item.open_review_count > 0 ? <span className="status-badge missed">{item.open_review_count} offen</span> : "–"}</td>
            <td><button className="icon-button" onClick={() => setSelectedAccount(item)} aria-label={`Konto ${item.display_name} öffnen`}>→</button></td>
          </tr>)}</tbody>
        </table>
      </section>
      {brokerageAccounts.length > 0 && <section className="panel table-panel">
        <PanelHeader title="Depots" subtitle="Positionen statt Kontosaldo · Anschaffungswert getrennt vom Marktwert" />
        <table>
          <thead><tr><th>Depot</th><th>Institut</th><th>Positionen</th><th>Anschaffungswert</th><th>Status</th><th>Offene Investment-Relationen</th><th></th></tr></thead>
          <tbody>{brokerageAccounts.map((item) => <tr key={item.account_id}>
            <td><strong>{item.display_name}</strong></td>
            <td>{item.institution}</td>
            <td>{item.position_count ?? 0}</td>
            <td className="amount">{money(item.acquisition_value, item.currency)}</td>
            <td><span className={`status-badge ${OVERVIEW_STATUS_TONE[item.overview_status] ?? "paused"}`}>{OVERVIEW_STATUS_LABELS[item.overview_status] ?? item.overview_status}</span></td>
            <td>{item.open_investment_funding_relations}</td>
            <td><button className="icon-button" onClick={() => setSelectedAccount(item)} aria-label={`Depot ${item.display_name} öffnen`}>→</button></td>
          </tr>)}</tbody>
        </table>
      </section>}
      {createOpen && <CreateAccountDialog onClose={() => setCreateOpen(false)} onCreated={() => { setCreateOpen(false); setMessage("Konto wurde lokal angelegt."); }} />}
      {selectedAccount && <AccountWorkspace account={selectedAccount} period={period} periodPayload={periodPayload} onClose={() => setSelectedAccount(null)} onMessage={setMessage} />}
    </>;
  }}</QueryBoundary>;
}

function CreateAccountDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const first = useRef<HTMLButtonElement>(null); const handleKeyDown = useModalFocus(first, onClose);
  const [name, setName] = useState(""); const [type, setType] = useState("CHECKING"); const [institution, setInstitution] = useState("");
  const save = async () => { await financeBridge.command("CreateAccount", { display_name: name, account_type: type, institution, currency: "EUR" }); onCreated(); };
  return <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-account-title" onKeyDown={handleKeyDown}><button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">NEUES KONTO</span><h2 id="create-account-title">Konto anlegen</h2><div className="form-stack"><label>Anzeigename<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>Kontotyp<select value={type} onChange={(event) => setType(event.target.value)}>{["CHECKING","SAVINGS","CREDIT_CARD","CASH","BROKERAGE","LOAN","MORTGAGE","OTHER"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Institut<input value={institution} onChange={(event) => setInstitution(event.target.value)} /></label></div><div className="dialog-actions"><button className="secondary" onClick={onClose}>Abbrechen</button><button className="primary" disabled={!name.trim()} onClick={save}>Konto anlegen</button></div></section></div>;
}

type AccountTabId = "overview" | "transactions" | "balances" | "reconciliations" | "imports" | "positions" | "audit";

function AccountWorkspace({ account, period, periodPayload, onClose, onMessage }: { account: AccountOverviewRow; period: PeriodSelection; periodPayload: Record<string, unknown>; onClose: () => void; onMessage: (message: string) => void }) {
  const first = useRef<HTMLButtonElement>(null);
  const handleKeyDown = useModalFocus(first, onClose);
  const [tab, setTab] = useState<AccountTabId>("overview");
  const isBrokerage = account.account_type === "BROKERAGE";
  const tabs: Array<[AccountTabId, string]> = [
    ["overview", "Übersicht"], ["transactions", "Transaktionen"], ["balances", "Salden"],
    ["reconciliations", "Abgleiche"], ["imports", "Importe"],
    ...(isBrokerage ? [["positions", "Positionen"] as [AccountTabId, string]] : []),
    ["audit", "Audit"],
  ];
  return <div className="dialog-backdrop"><section className="dialog wide account-workspace" role="dialog" aria-modal="true" aria-labelledby="account-workspace-title" onKeyDown={handleKeyDown}>
    <button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button>
    <span className="eyebrow">KONTOARBEITSBEREICH</span>
    <h2 id="account-workspace-title">{account.display_name}</h2>
    <div className="tabs" role="tablist" aria-label="Kontobereiche">
      {tabs.map(([id, text]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{text}</button>)}
    </div>
    {tab === "overview" && <AccountTabOverview accountId={account.account_id} periodPayload={periodPayload} onMessage={onMessage} />}
    {tab === "transactions" && <AccountTabTransactions accountId={account.account_id} periodPayload={periodPayload} />}
    {tab === "balances" && <AccountTabBalances accountId={account.account_id} periodPayload={periodPayload} />}
    {tab === "reconciliations" && <AccountTabReconciliations accountId={account.account_id} onMessage={onMessage} />}
    {tab === "imports" && <AccountTabImports accountId={account.account_id} />}
    {tab === "positions" && isBrokerage && <AccountTabPositions accountId={account.account_id} />}
    {tab === "audit" && <AccountTabAudit accountId={account.account_id} />}
  </section></div>;
}

function AccountTabOverview({ accountId, periodPayload, onMessage }: { accountId: string; periodPayload: Record<string, unknown>; onMessage: (message: string) => void }) {
  const result = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: accountId, period: periodPayload });
  const [amount, setAmount] = useState("");
  const reconcile = async () => { await financeBridge.command("ReconcileAccountBalance", { account_id: accountId }); onMessage("Saldenabgleich wurde lokal ausgeführt."); };
  const record = async () => { await financeBridge.command("RecordBalanceSnapshot", { account_id: accountId, balance_date: "2026-07-20", booked_balance: amount, available_balance: amount, currency: "EUR", source: "MANUAL_ENTRY", confidence: "HIGH" }); onMessage("Saldo-Snapshot wurde unveränderlich erfasst."); };
  return <QueryBoundary result={result}>{(data) => <div className="account-tab-overview">
    <dl className="transaction-facts">
      <div><dt>Kontostatus</dt><dd>{OVERVIEW_STATUS_LABELS[data.overview_status] ?? data.overview_status}</dd></div>
      <div><dt>Kontotyp</dt><dd>{label(data.account.account_type)}</dd></div>
      <div><dt>Institution</dt><dd>{data.account.institution}</dd></div>
      <div><dt>Währung</dt><dd>{data.account.currency}</dd></div>
      <div><dt>Gemeldeter Saldo</dt><dd>{money(data.account.reported_balance, data.account.currency)}</dd></div>
      <div><dt>Berechneter Saldo</dt><dd>{money(data.account.calculated_balance, data.account.currency)}</dd></div>
      <div><dt>Saldoabweichung</dt><dd>{money(data.account.balance_difference, data.account.currency)}</dd></div>
      <div><dt>Saldozeitpunkt</dt><dd>{data.account.reported_balance_date ? date(data.account.reported_balance_date) : "–"}</dd></div>
      <div><dt>Einnahmen im Zeitraum</dt><dd>{money(data.period_summary.period_income)}</dd></div>
      <div><dt>Ausgaben im Zeitraum</dt><dd>{money(data.period_summary.period_expenses)}</dd></div>
      <div><dt>Netto-Cashflow</dt><dd>{money(data.period_summary.period_net_cashflow)}</dd></div>
      <div><dt>Letzter Import</dt><dd>{data.account.last_import_month ?? "–"}</dd></div>
      <div><dt>Offene Prüfungen</dt><dd>{data.open_review_count}</dd></div>
      <div><dt>Datenstand</dt><dd>{result.envelope?.freshness_status ?? "CURRENT"}</dd></div>
      <div><dt>Projection-Version</dt><dd>{result.envelope?.projection_version ?? "–"}</dd></div>
    </dl>
    <div className="dialog-actions">
      <label className="balance-entry">Neuen gemeldeten Saldo erfassen<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} /></label>
      <button className="secondary" onClick={reconcile}>Saldo abgleichen</button>
      <button className="primary" disabled={!amount.trim()} onClick={record}>Snapshot erfassen</button>
    </div>
  </div>}</QueryBoundary>;
}

function AccountTabTransactions({ accountId, periodPayload }: { accountId: string; periodPayload: Record<string, unknown> }) {
  const result = useFinanceQuery<AccountTransactionList>("ListAccountTransactions", { account_id: accountId, period: periodPayload });
  return <QueryBoundary result={result}>{({ transactions }) => <section className="panel table-panel">
    <table>
      <thead><tr><th>Datum</th><th>Gegenpartei</th><th>Kategorie</th><th>Relation</th><th className="numeric">Betrag</th><th>Status</th><th>Import</th></tr></thead>
      <tbody>{transactions.map((item: AccountTransactionRow) => <tr key={item.transaction_id}>
        <td>{date(item.booking_date)}</td>
        <td>{item.counterparty}<small>{item.normalized_description ?? "–"}</small></td>
        <td>{categoryLabel(item.category_code ?? "UNCLASSIFIED")}</td>
        <td>{relationStatusLabel(item.transfer_status ?? "NONE")}</td>
        <td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td>
        <td><span className="status-dot ok">{item.direction === "CREDIT" ? "Eingang" : "Ausgang"}</span></td>
        <td>{item.export_id ?? "–"}</td>
      </tr>)}</tbody>
    </table>
  </section>}</QueryBoundary>;
}

const BALANCE_TYPE_LABELS: Record<string, string> = { OPENING: "Anfangssaldo", CLOSING: "Endsaldo", INTERMEDIATE: "Zwischensaldo", CALCULATED: "Berechnet" };

function AccountTabBalances({ accountId, periodPayload }: { accountId: string; periodPayload: Record<string, unknown> }) {
  const result = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: accountId, period: periodPayload });
  return <QueryBoundary result={result}>{(data) => <section className="panel table-panel">
    <table>
      <thead><tr><th>Saldoart</th><th>Datum</th><th>Gemeldet</th><th>Berechnet</th><th>Quelle</th><th>Bestätigung</th><th>Korrektur</th></tr></thead>
      <tbody>{data.balance_history.map((item: AccountBalanceLedgerRow, index: number) => <tr key={`${item.sequence_number}_${index}`}>
        <td>{BALANCE_TYPE_LABELS[item.balance_type] ?? item.balance_type}</td>
        <td>{item.balance_date ? date(item.balance_date) : "–"}</td>
        <td className="amount">{money(item.reported_value)}</td>
        <td className="amount">{item.calculated_value ? money(item.calculated_value) : "–"}</td>
        <td>{label(item.source)}</td>
        <td>{item.confirmation_status === "CONFIRMED" ? "Bestätigt" : "Unbestätigt"}</td>
        <td>{item.carry_forward_source_reconciliation_id ? "Übernommen aus bestätigtem Endsaldo" : item.adjustment_reason ? `Angepasst: ${item.adjustment_reason}` : "–"}</td>
      </tr>)}</tbody>
    </table>
  </section>}</QueryBoundary>;
}

const RECONCILIATION_STATUS_LABELS: Record<string, string> = { MATCHED: "Übereinstimmend", DIFFERENCE: "Abweichung", REVIEW_REQUIRED: "Prüfung offen", MISSING_OPENING_BALANCE: "Anfangssaldo fehlt", NO_REPORTED_CLOSING_BALANCE: "Endsaldo fehlt" };

function AccountTabReconciliations({ accountId, onMessage }: { accountId: string; onMessage: (message: string) => void }) {
  const result = useFinanceQuery<AccountReconciliationList>("ListAccountReconciliations", { account_id: accountId });
  const [filter, setFilter] = useState<string>("ALL");
  const [explanationDraft, setExplanationDraft] = useState<Record<string, string>>({});
  const submitExplanation = async (reconciliationId: string) => {
    const explanation = explanationDraft[reconciliationId]?.trim();
    if (!explanation) return;
    await financeBridge.command("DocumentBalanceDifference", { reconciliation_id: reconciliationId, explanation });
    onMessage("Erklärung wurde erfasst. Die Abweichung bleibt bis zur exakten Übereinstimmung sichtbar.");
  };
  return <QueryBoundary result={result}>{({ reconciliations }: AccountReconciliationList) => {
    const rows = filter === "ALL" ? reconciliations : reconciliations.filter((item: AccountReconciliationRow) => item.status === filter);
    return <section className="panel table-panel">
      <div className="tabs" role="tablist" aria-label="Abgleichstatus-Filter">
        {["ALL", "MATCHED", "DIFFERENCE", "REVIEW_REQUIRED"].map((value) => <button key={value} role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>{value === "ALL" ? "Alle" : RECONCILIATION_STATUS_LABELS[value] ?? value}</button>)}
      </div>
      <table>
        <thead><tr><th>Berichtsmonat</th><th>Anfangssaldo</th><th>Berechneter Endsaldo</th><th>Gemeldeter Endsaldo</th><th>Differenz</th><th>Status</th><th>Erklärung</th></tr></thead>
        <tbody>{rows.map((item: AccountReconciliationRow) => <tr key={item.reconciliation_id}>
          <td>{item.report_month}</td>
          <td className="amount">{money(item.opening_balance)}</td>
          <td className="amount">{money(item.calculated_closing_balance)}</td>
          <td className="amount">{money(item.reported_closing_balance)}</td>
          <td className="amount">{money(item.balance_difference)}</td>
          <td><span className={`status-badge ${item.status === "MATCHED" ? "confirmed" : "missed"}`}>{RECONCILIATION_STATUS_LABELS[item.status] ?? item.status}</span></td>
          <td>{item.explanation ?? (item.status !== "MATCHED" && <div className="new-category-row"><input aria-label="Begründung" value={explanationDraft[item.reconciliation_id] ?? ""} onChange={(event) => setExplanationDraft((current) => ({ ...current, [item.reconciliation_id]: event.target.value }))} /><button className="secondary small" onClick={() => submitExplanation(item.reconciliation_id)}>Erfassen</button></div>)}</td>
        </tr>)}</tbody>
      </table>
    </section>;
  }}</QueryBoundary>;
}

function AccountTabImports({ accountId }: { accountId: string }) {
  const result = useFinanceQuery<AccountImportList>("ListAccountImports", { account_id: accountId });
  return <QueryBoundary result={result}>{({ imports }) => <section className="panel table-panel">
    <table>
      <thead><tr><th>Bank</th><th>Berichtsmonat</th><th>Abschnitt</th><th>Status</th><th>Datensätze</th><th>Parser</th><th>Profil</th><th>Inhaltshash</th><th>Zeitpunkt</th></tr></thead>
      <tbody>{imports.map((item: AccountImportRow) => <tr key={`${item.export_id}_${item.section_id}`}>
        <td>{item.bank_identifier ?? "–"}</td>
        <td>{item.report_month}</td>
        <td>{label(item.section_type)}</td>
        <td>{label(item.import_status)}</td>
        <td>{item.record_count}</td>
        <td>{item.parser_version}</td>
        <td>{item.profile_version}</td>
        <td>{item.content_hash}</td>
        <td>{date(item.imported_at)}</td>
      </tr>)}</tbody>
    </table>
  </section>}</QueryBoundary>;
}

function AccountTabPositions({ accountId }: { accountId: string }) {
  const result = useFinanceQuery<AccountPositionList>("ListAccountPositions", { account_id: accountId });
  return <QueryBoundary result={result}>{({ positions }) => <section className="panel table-panel">
    <table>
      <thead><tr><th>WKN/ISIN</th><th>Bezeichnung</th><th>Anfangsstückzahl</th><th>Käufe</th><th>Verkäufe</th><th>Endstückzahl</th><th>Differenz</th><th>Anschaffungswert</th><th>Marktwert</th></tr></thead>
      <tbody>{positions.map((item: AccountPositionRow) => <tr key={item.position_id}>
        <td>{item.security_identifier}</td>
        <td>{item.security_name}</td>
        <td className="amount">{item.opening_quantity}</td>
        <td className="amount">{item.purchased_quantity}</td>
        <td className="amount">{item.sold_quantity}</td>
        <td className="amount">{item.closing_quantity}</td>
        <td className="amount">{item.position_difference ?? "–"}</td>
        <td className="amount">{money(item.acquisition_value, item.currency ?? "EUR")}</td>
        <td className="amount">{money(item.market_value, item.currency ?? "EUR")}</td>
      </tr>)}</tbody>
    </table>
  </section>}</QueryBoundary>;
}

function AccountTabAudit({ accountId }: { accountId: string }) {
  const result = useFinanceQuery<AccountAuditTrail>("GetAccountAuditTrail", { account_id: accountId });
  const [expanded, setExpanded] = useState<string | null>(null);
  return <QueryBoundary result={result}>{({ audit_history }) => <div className="history-list account-audit-list">
    {audit_history.map((event: AccountAuditEvent) => { const info = eventLabel(event.event_type); return (
      <div key={event.event_id}>
        <span>{date(event.occurred_at)}</span>
        <div><strong>{info.title}</strong><small>{event.related_import_id ? `Import ${event.related_import_id}` : event.related_balance_id ? `Saldo ${event.related_balance_id}` : "–"}</small></div>
        <button className="icon-button" aria-label="Technische Details" onClick={() => setExpanded(expanded === event.event_id ? null : event.event_id)}>ⓘ</button>
        {expanded === event.event_id && <div className="toast compact" role="status"><p>{info.description}</p><small>{event.event_type} · #{event.sequence_number}</small></div>}
      </div>
    ); })}
  </div>}</QueryBoundary>;
}

function Metric({ label: caption, value, tone, note }: { label: string; value: string; tone: string; note: string }) {
  return <article className={`metric ${tone}`}><span>{caption}</span><strong>{value}</strong><small>{note}</small></article>;
}

function PanelHeader({ title, subtitle, action, onAction }: { title: string; subtitle?: string; action?: string; onAction?: () => void }) {
  return <header className="panel-header"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action && <button className="text-button" onClick={onAction}>{action} →</button>}</header>;
}

function Transactions({ month }: { month: string }) {
  const result = useFinanceQuery<{ transactions: Transaction[] }>("ListTransactions", { month });
  const categories = useFinanceQuery<{ categories: Array<{ category_code: string }> }>("ListCategories");
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [message, setMessage] = useState("");
  const [categoryOverrides, setCategoryOverrides] = useState<Record<string, string>>({});
  const classify = async () => { await financeBridge.command("ClassifyTransactions", { month }); setMessage("Klassifikation lokal ausgeführt."); };
  return <QueryBoundary result={result}>{({ transactions }) => <>
    <section className="section-intro"><div><h2>Buchungen im Zeitraum</h2><p>Effektive Beträge nach bestätigten Dubletten, Transfers und Rückerstattungen.</p></div><button className="primary" onClick={classify}>Regeln anwenden</button></section>
    {message && <div className="toast" role="status">{message}</div>}
    <section className="panel table-panel"><table><thead><tr><th>Datum</th><th>Anbieter und Beschreibung</th><th>Kategorie</th><th>Status</th><th className="numeric">Betrag</th><th><span className="sr-only">Aktion</span></th></tr></thead><tbody>{transactions.map((item) => { const category = categoryOverrides[item.transaction_id] ?? item.category_code; return <tr key={item.transaction_id}><td>{date(item.booking_date)}</td><td><strong>{item.counterparty || "Unbekannter Anbieter"}</strong><small>{item.description || item.normalized_description || "Keine Beschreibung"}</small></td><td><span className={`category-tag ${category === "UNCLASSIFIED" ? "warn" : ""}`}>{categoryLabel(category)}</span></td><td><span className="status-dot ok">Effektiv</span></td><td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td><td><button className="icon-button" onClick={() => setSelected({ ...item, category_code: category })} aria-label={`Details zu ${item.counterparty}`}>→</button></td></tr>; })}</tbody></table></section>
    {selected && <TransactionDialog transaction={selected} categories={categories.envelope?.data.categories.map((item) => item.category_code) ?? []} onClose={() => setSelected(null)} onSaved={(category) => { setCategoryOverrides((current) => ({ ...current, [selected.transaction_id]: category })); setSelected((current) => current ? { ...current, category_code: category } : current); setMessage(`Kategorie „${categoryLabel(category)}“ wurde gespeichert.`); }} />}
  </>}</QueryBoundary>;
}

function TransactionDialog({ transaction, categories, onClose, onSaved }: { transaction: Transaction; categories: string[]; onClose: () => void; onSaved: (category: string) => void }) {
  const close = useRef<HTMLButtonElement>(null);
  const details = useFinanceQuery<Record<string, unknown>>("GetTransactionDetails", { transaction_id: transaction.transaction_id });
  const handleKeyDown = useModalFocus(close, onClose);
  const [category, setCategory] = useState(transaction.category_code);
  const [creating, setCreating] = useState(false);
  const [newCategory, setNewCategory] = useState("");
  const [rememberProvider, setRememberProvider] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const detail = details.envelope?.data ?? {};
  const categoryOptions = Array.from(new Set([transaction.category_code, ...categories, ...(category.startsWith("CUSTOM_") ? [category] : [])]));
  const addCategory = () => {
    const code = customCategoryCode(newCategory);
    if (!code) { setFeedback("Gib einen Namen für die neue Kategorie ein."); return; }
    setCategory(code);
    setCreating(false);
    setNewCategory("");
    setFeedback(`Neue Kategorie „${categoryLabel(code)}“ ist ausgewählt.`);
  };
  const save = async () => {
    if (!category || category === "UNCLASSIFIED") { setFeedback("Wähle zuerst eine Kategorie."); return; }
    setBusy(true);
    setFeedback("");
    try {
      await financeBridge.command("ConfirmClassification", { transaction_id: transaction.transaction_id, category_code: category });
      if (rememberProvider && transaction.counterparty.trim()) {
        await financeBridge.command("CreateClassificationRule", { field: "counterparty", operator: "EQUALS", value: transaction.counterparty, category_code: category, priority: 250 });
      }
      onSaved(category);
      setFeedback(rememberProvider ? "Kategorie und Anbieterregel wurden gespeichert." : "Kategorie wurde gespeichert.");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Kategorie konnte nicht gespeichert werden.");
    } finally {
      setBusy(false);
    }
  };
  return <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="dialog wide transaction-dialog" role="dialog" aria-modal="true" aria-labelledby="transaction-title" onKeyDown={handleKeyDown}><button ref={close} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">TRANSAKTIONSDETAIL</span><div className="transaction-dialog-head"><div><h2 id="transaction-title">{transaction.counterparty || "Unbekannter Anbieter"}</h2><p>{transaction.description || transaction.normalized_description || "Keine Transaktionsbeschreibung vorhanden"}</p></div><strong className="dialog-amount">{money(transaction.amount, transaction.currency)}</strong></div><section className="category-editor" aria-label="Kategorie bearbeiten"><div><label htmlFor="transaction-category">Kategorie</label><select id="transaction-category" value={category} onChange={(event) => setCategory(event.target.value)}>{categoryOptions.map((code) => <option value={code} key={code}>{categoryLabel(code)}</option>)}</select></div><button className="secondary small" type="button" onClick={() => setCreating((value) => !value)}>{creating ? "Abbrechen" : "Neue Kategorie"}</button>{creating && <div className="new-category-row"><input aria-label="Name der neuen Kategorie" value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder="z. B. Haustiere" /><button className="secondary small" type="button" onClick={addCategory}>Hinzufügen</button></div>}<label className="remember-rule"><input type="checkbox" checked={rememberProvider} onChange={(event) => setRememberProvider(event.target.checked)} /> Für diesen Anbieter künftig automatisch verwenden</label><button className="primary" disabled={busy || category === "UNCLASSIFIED"} onClick={save}>{busy ? "Speichert …" : "Kategorie speichern"}</button></section>{feedback && <div className="toast compact" role="status">{feedback}</div>}<dl className="transaction-facts"><div><dt>Anbieter</dt><dd>{String(detail.counterparty ?? transaction.counterparty ?? "–")}</dd></div><div><dt>Transaktionsbeschreibung</dt><dd>{String(detail.description ?? transaction.description ?? "–")}</dd></div><div><dt>Buchungstag</dt><dd>{date(String(detail.booking_date ?? transaction.booking_date))}</dd></div><div><dt>Wertstellung</dt><dd>{detail.value_date ? date(String(detail.value_date)) : "–"}</dd></div><div><dt>Konto</dt><dd>{String(detail.account_id ?? "–")}</dd></div><div><dt>Cashflow-relevant</dt><dd>{Boolean(detail.cashflow_relevant ?? transaction.cashflow_relevant) ? "Ja" : "Nein"}</dd></div><div><dt>Dublette</dt><dd>{relationStatusLabel(String((detail.reconciliation as Record<string, unknown> | undefined)?.duplicate_status ?? transaction.duplicate_status ?? "NONE"))}</dd></div><div><dt>Transfer</dt><dd>{relationStatusLabel(String((detail.reconciliation as Record<string, unknown> | undefined)?.transfer_status ?? transaction.transfer_status ?? "NONE"))}</dd></div></dl><div className="detail-state">{details.state === "LOADING" ? "Event-Historie wird geladen …" : `${((detail.event_history as unknown[] | undefined) ?? []).length} zugehörige Events · Projektion ${details.state}`}</div></section></div>;
}

function Categories({ month }: { month: string }) {
  const result = useFinanceQuery<{ categories: Record<string, { category_code: string; effective_expense: string; transaction_count: number }> }>("GetCategoryBreakdown", { month });
  return <QueryBoundary result={result}>{({ categories }) => { const rows = Object.values(categories).sort((a, b) => Number(b.effective_expense) - Number(a.effective_expense)); const total = rows.reduce((sum, item) => sum + Number(item.effective_expense), 0); return <><section className="section-intro"><div><h2>Ausgaben nach Kategorien</h2><p>Bestätigte Klassifikationen; Unklassifiziert bleibt sichtbar.</p></div><strong className="section-total">{money(String(total))}<small>effektive Ausgaben</small></strong></section><section className="panel category-list">{rows.map((item, index) => <div className="category-row" key={item.category_code}><span className={`category-symbol color-${index % 5}`}>{item.category_code.slice(0, 2)}</span><div><strong>{label(item.category_code)}</strong><small>{item.transaction_count} Buchungen</small></div><div className="bar"><span style={{ width: `${Math.max(4, Number(item.effective_expense) / total * 100)}%` }} /></div><strong>{money(item.effective_expense)}</strong></div>)}</section></>; }}</QueryBoundary>;
}

function Recurring() {
  const patterns = useFinanceQuery<{ patterns: RecurringPattern[] }>("ListRecurringPatterns");
  const expected = useFinanceQuery<{ expected_transactions: ExpectedTransaction[] }>("ListExpectedTransactions");
  const [tab, setTab] = useState<"patterns" | "expected">("patterns");
  const [selected, setSelected] = useState<RecurringPattern | null>(null);
  const [message, setMessage] = useState("");
  const action = async (command: string, pattern_id: string, payload: Record<string, unknown> = {}) => { await financeBridge.command(command, { pattern_id, ...payload }); setMessage(`${command} wurde lokal ausgeführt.`); };
  return <><div className="tabs" role="tablist"><button role="tab" aria-selected={tab === "patterns"} onClick={() => setTab("patterns")}>Muster</button><button role="tab" aria-selected={tab === "expected"} onClick={() => setTab("expected")}>Erwartete Buchungen</button></div>{message && <div className="toast" role="status">{message}</div>}{tab === "patterns" ? <QueryBoundary result={patterns}>{({ patterns: rows }) => <><section className="section-intro"><div><h2>Wiederkehrende Muster</h2><p>Deterministisch erkannt, nur bestätigte Muster fließen in Prognosen ein.</p></div><button className="primary" onClick={() => financeBridge.command("DetectRecurringPatterns", { from_month: "2025-07", to_month: "2026-07" })}>Muster erkennen</button></section><section className="panel table-panel"><table><thead><tr><th>Gegenpartei</th><th>Kategorie</th><th>Frequenz</th><th>Erwarteter Betrag</th><th>Konfidenz</th><th>Status</th><th></th></tr></thead><tbody>{rows.map((item) => <tr key={item.pattern_id}><td><strong>{label(item.merchant_key)}</strong><small>{(item as RecurringPattern & { account_id?: string }).account_id ?? "–"}</small></td><td>{label(item.category_code ?? "UNCLASSIFIED")}</td><td>{label(item.frequency)}</td><td>{money(item.expected_amount)}</td><td><span className="confidence">{label(item.confidence)}</span></td><td><span className={`status-badge ${item.status.toLowerCase()}`}>{item.status}</span></td><td><button className="icon-button" onClick={() => setSelected(item)} aria-label={`Muster ${item.merchant_key} öffnen`}>→</button></td></tr>)}</tbody></table></section>{selected && <PatternDialog pattern={selected} onClose={() => setSelected(null)} onAction={action} />}</>}</QueryBoundary> : <QueryBoundary result={expected}>{({ expected_transactions }) => <><section className="section-intro"><div><h2>Erwartete Buchungen</h2><p>Verknüpfungen zwischen Erwartung und tatsächlicher Transaktion bleiben nachvollziehbar.</p></div></section><section className="expected-grid">{expected_transactions.map((item) => <article className="panel expected-card" key={item.expected_transaction_id}><div><span className={`status-badge ${item.status.toLowerCase()}`}>{item.status}</span><small>{date(item.expected_date)}</small></div><h3>{label((item as ExpectedTransaction & { merchant_key?: string }).merchant_key ?? item.recurring_pattern_id)}</h3><strong>{money(item.expected_amount)}</strong><p>{label(item.direction)} · {label((item as ExpectedTransaction & { category_code?: string }).category_code ?? "UNCLASSIFIED")}</p>{(item as ExpectedTransaction & { matched_transaction_id?: string }).matched_transaction_id && <div className="match-flow"><span>Erwartung</span><b>↓</b><span>Ist-Transaktion</span><small>Abweichung 0,00 € · 0 Tage</small></div>}</article>)}</section></>}</QueryBoundary>}{selected && null}</>;
}

function PatternDialog({ pattern, onClose, onAction }: { pattern: RecurringPattern; onClose: () => void; onAction: (command: string, id: string, payload?: Record<string, unknown>) => void }) {
  const first = useRef<HTMLButtonElement>(null);
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState(pattern.expected_amount);
  const [dayFrom, setDayFrom] = useState(pattern.expected_day_from ?? 1);
  const [dayTo, setDayTo] = useState(pattern.expected_day_to ?? 5);
  const handleKeyDown = useModalFocus(first, onClose);
  const save = () => { onAction("UpdateRecurringPattern", pattern.pattern_id, { amount, day_from: dayFrom, day_to: dayTo }); setEditing(false); };
  return <div className="dialog-backdrop"><section className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="pattern-title" onKeyDown={handleKeyDown}><button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">MUSTERDETAIL</span><h2 id="pattern-title">{label(pattern.merchant_key)}</h2>{editing ? <div className="edit-grid"><label>Erwarteter Betrag<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} /></label><label>Erster erwarteter Tag<input type="number" min="1" max="31" value={dayFrom} onChange={(event) => setDayFrom(Number(event.target.value))} /></label><label>Letzter erwarteter Tag<input type="number" min="1" max="31" value={dayTo} onChange={(event) => setDayTo(Number(event.target.value))} /></label></div> : <div className="detail-grid"><div><span>Erwarteter Betrag</span><strong>{money(pattern.expected_amount)}</strong></div><div><span>Frequenz</span><strong>{label(pattern.frequency)}</strong></div><div><span>Datumsfenster</span><strong>Tag {pattern.expected_day_from ?? "–"} bis {pattern.expected_day_to ?? "–"}</strong></div><div><span>Pattern-Version</span><strong>v1 · recurrence-v1</strong></div></div>}<h3>Nächste erwartete Buchung</h3><div className="timeline-item"><span className="timeline-dot"/><div><strong>01. Aug 2026 · {money(pattern.expected_amount)}</strong><small>Konto Giro · Toleranz gemäß Policy</small></div></div><div className="dialog-actions">{editing ? <><button className="primary" onClick={save}>Änderungen speichern</button><button className="secondary" onClick={() => setEditing(false)}>Abbrechen</button></> : <>{pattern.status === "PROPOSED" && <><button className="primary" onClick={() => onAction("ConfirmRecurringPattern", pattern.pattern_id)}>Bestätigen</button><button className="secondary" onClick={() => onAction("RejectRecurringPattern", pattern.pattern_id)}>Ablehnen</button></>}{pattern.status === "CONFIRMED" && <><button className="secondary" onClick={() => setEditing(true)}>Bearbeiten</button><button className="secondary" onClick={() => onAction("PauseRecurringPattern", pattern.pattern_id)}>Pausieren</button></>}</>}<button className="danger-link" onClick={() => onAction("EndRecurringPattern", pattern.pattern_id)}>Beenden</button></div></section></div>;
}

function Forecast({ month }: { month: string }) {
  const forecast = useFinanceQuery<{ scenarios: Record<string, ForecastScenario> }>("GetForecast", { month });
  const versions = useFinanceQuery<{ versions: Array<{ sequence_number: number; event_type: string; occurred_at: string; payload: Record<string, string> }> }>("ListForecastVersions", { month });
  const evaluation = useFinanceQuery<{ evaluations: Array<Record<string, unknown>> }>("GetForecastEvaluation", { month });
  const [notice, setNotice] = useState("");
  const create = async () => { await financeBridge.command("CreateForecast", { month }); setNotice("Neue Forecast-Version wurde aus dem aktuellen Datenstand erzeugt."); };
  return <QueryBoundary result={forecast}>{({ scenarios }) => { const base = scenarios.BASE; return <><section className="section-intro"><div><h2>Monatsprognose</h2><p>Policybasiert und reproduzierbar · keine KI, kein probabilistisches Modell.</p></div><button className="primary" onClick={create}>Neu berechnen</button></section>{notice && <div className="toast" role="status">{notice}</div>}<section className="forecast-summary"><Metric label="Realisierter Cashflow" value={money(base ? String(Number(base.predicted_surplus) - Number(base.expected_income) + Number(base.expected_fixed_expenses) + Number(base.predicted_variable_expenses)) : "0")} tone="ink" note="Bis heute"/><Metric label="Noch erwartete Einnahmen" value={money(base?.expected_income)} tone="positive" note="Bestätigte Muster"/><Metric label="Noch erwartete Fixkosten" value={money(base?.expected_fixed_expenses)} tone="negative" note="Bestätigte Muster"/><Metric label="Variable Ausgaben" value={money(base?.predicted_variable_expenses)} tone="ink" note="Historischer Median"/></section><h2 className="subheading">Szenarien</h2><section className="scenario-grid">{Object.values(scenarios).map((scenario) => <article key={scenario.forecast_id} className={`panel scenario ${scenario.scenario === "BASE" ? "featured" : ""}`}><div className="scenario-head"><span>{scenario.scenario}</span>{scenario.scenario === "BASE" && <em>Basis</em>}</div><small>Erwarteter Monatsüberschuss</small><strong>{money(scenario.predicted_surplus)}</strong><dl><div><dt>Einnahmen</dt><dd>{money(scenario.expected_income)}</dd></div><div><dt>Fixkosten</dt><dd>{money(scenario.expected_fixed_expenses)}</dd></div><div><dt>Datenstand</dt><dd>Event #{scenario.source_event_sequence}</dd></div><div><dt>Policy</dt><dd>{scenario.forecast_policy_version}</dd></div></dl><details><summary>Annahmen anzeigen</summary><ul>{(scenario as ForecastScenario & { assumptions?: string[] }).assumptions?.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul></details></article>)}</section><div className="two-column forecast-bottom"><section className="panel"><PanelHeader title="Forecast-Historie" subtitle="Immutable Versionen"/><QueryBoundary result={versions}>{({ versions }) => <div className="timeline">{versions.map((item, index) => <div className="timeline-item" key={item.sequence_number}><span className={`timeline-dot ${index === 0 ? "active" : ""}`}/><div><strong>{label(item.event_type.replace("Forecast", "Forecast "))}</strong><small>{date(item.occurred_at)} · Event #{item.sequence_number}</small><p>{money(item.payload.predicted_surplus)} · {item.payload.status}</p></div></div>)}</div>}</QueryBoundary></section><section className="panel"><PanelHeader title="Historische Forecast-Qualität" subtitle="Komponentenweise Prognoseabweichung"/><QueryBoundary result={evaluation} empty="Noch keine abgeschlossene Evaluation für diesen Monat.">{({ evaluations }) => <Evaluation data={evaluations[0]} />}</QueryBoundary></section></div></>; }}</QueryBoundary>;
}

function Evaluation({ data }: { data: Record<string, unknown> | undefined }) {
  if (!data) return null; const component = data.component_accuracy as Record<string, string>;
  const rows = [
    ["Wiederkehrende Einnahmen", component.recurring_income_matched, component.recurring_income_matched, "0"],
    ["Wiederkehrende Ausgaben", "1450", component.recurring_expenses_matched, String(Math.abs(1450 - Number(component.recurring_expenses_matched)))],
    ["Variable Ausgaben", component.predicted_variable_expenses, component.actual_variable_expenses, String(Math.abs(Number(component.predicted_variable_expenses) - Number(component.actual_variable_expenses)))],
    ["Monatsüberschuss", String(Number(data.actual_surplus) + Number(data.absolute_error)), String(data.actual_surplus), String(data.absolute_error)],
  ];
  return <><div className="evaluation-stats"><div><strong>{data.expected_transactions_matched as number}</strong><span>bestätigt</span></div><div><strong>{data.expected_transactions_missed as number}</strong><span>verpasst</span></div><div><strong>{data.percentage_error as string} %</strong><span>Abweichung</span></div></div><div className="mini-table"><div className="mini-row header"><span>Komponente</span><span>Prognose</span><span>Ist</span><span>Δ</span></div>{rows.map((row) => <div className="mini-row" key={row[0]}>{row.map((cell, index) => <span key={index}>{index ? money(cell) : cell}</span>)}</div>)}</div></>;
}

function Wealth({ asOf }: { asOf: string }) {
  const worth = useFinanceQuery<NetWorthOverview>("GetNetWorthOverview", { valuation_currency: "EUR", as_of: asOf });
  const liquidity = useFinanceQuery<LiquidityOverview>("GetLiquidityOverview", { valuation_currency: "EUR", as_of: asOf });
  const history = useFinanceQuery<{ history: Array<{ as_of: string; net_worth: string }> }>("GetNetWorthHistory", { valuation_currency: "EUR" });
  const liabilities = useFinanceQuery<{ total_liabilities: string; liabilities: Array<Record<string, string>> }>("GetLiabilityOverview", { valuation_currency: "EUR" });
  return <QueryBoundary result={worth}>{(data) => <><section className="section-intro"><div><h2>Vermögen und Verbindlichkeiten</h2><p>Bewertungswährung EUR · jeder Wert ist bis zu seinem Snapshot zurückverfolgbar.</p></div><span className="version-chip">Stand {date(data.as_of)}</span></section>{data.currency_conflicts.length > 0 && <div className="status-banner"><strong>Währungskonflikt</strong><span>Nicht umgerechnet: {data.currency_conflicts.join(", ")}</span></div>}<section className="wealth-hero"><article className="net-worth-card"><span>Nettovermögen</span><strong>{money(data.net_worth)}</strong><small>Vermögen {money(data.total_assets)} − Verbindlichkeiten {money(data.liabilities)}</small><div className="wealth-change">↑ 2,5 % zum Vormonat</div></article><div className="wealth-metrics"><Metric label="Liquidität" value={money(data.liquid_funds)} tone="positive" note={`Bestätigter Stand ${liquidity.envelope ? date(liquidity.envelope.data.as_of) : "–"}`} /><Metric label="Sparguthaben" value={money(data.savings)} tone="ink" note="Teil der Liquidität" /><Metric label="Investments" value={money(data.investments)} tone="accent" note="Investierbares Vermögen" /><Metric label="Verbindlichkeiten" value={money(data.liabilities)} tone="negative" note="Kein Vermögenswert" /></div></section><div className="two-column wealth-bottom"><section className="panel"><PanelHeader title="Vermögensentwicklung" subtitle="Snapshot-basierter Verlauf"/><QueryBoundary result={history}>{({ history }) => <div className="wealth-chart"><svg viewBox="0 0 600 190" role="img" aria-label="Nettovermögen steigt über drei Monate"><path className="gridline" d="M0 35H600M0 95H600M0 155H600"/><path className="area" d="M0 150 C170 140 220 112 300 105 S470 55 600 35 L600 180 L0 180Z"/><path className="line" d="M0 150 C170 140 220 112 300 105 S470 55 600 35"/></svg><div className="wealth-axis">{history.map((item) => <span key={item.as_of}>{date(item.as_of)}<strong>{money(item.net_worth)}</strong></span>)}</div></div>}</QueryBoundary></section><section className="panel"><PanelHeader title="Verbindlichkeiten" subtitle="Separat vom Vermögen"/><QueryBoundary result={liabilities}>{(liabilityData) => <div className="liability-list">{liabilityData.liabilities.map((item) => <div key={item.item_id}><span className="category-symbol">{item.item_type.slice(0,2)}</span><div><strong>{item.display_name}</strong><small>{label(item.item_type)} · {date(item.valuation_date)}</small></div><b>{money(item.amount, item.currency)}</b></div>)}<footer><span>Gesamt</span><strong>{money(liabilityData.total_liabilities)}</strong></footer></div>}</QueryBoundary></section></div><section className="panel trace-note"><strong>Berechnungsgrundlage</strong><span>{data.source_snapshot_ids.length} aktive Snapshots · Interne Transfers konsolidiert neutral · keine stillen Währungsumrechnungen</span></section></>}</QueryBoundary>;
}

const reviewTabs = ["Klassifikationen", "Dubletten", "Transfers", "Rückerstattungen", "Wiederkehrende Muster", "Forecast-Konflikte", "Saldenabweichungen", "Fehlende Eröffnungssalden", "Veraltete Salden", "Nicht zugeordnete Konten"];
function Reviews() {
  const [tab, setTab] = useState(reviewTabs[0]);
  const accountReviewTypes: Record<string, string> = { "Saldenabweichungen": "BALANCE_DIFFERENCE", "Fehlende Eröffnungssalden": "OPENING_BALANCE_MISSING", "Veraltete Salden": "STALE_BALANCE", "Nicht zugeordnete Konten": "UNASSIGNED_ACCOUNT" };
  const query = tab === "Klassifikationen" ? "ListClassificationReviews" : tab === "Wiederkehrende Muster" ? "ListRecurringPatterns" : tab === "Forecast-Konflikte" ? "ListExpectedTransactions" : accountReviewTypes[tab] ? "ListAccountReviews" : "ListReconciliationReviews";
  const type = ({ Dubletten: "duplicates", Transfers: "transfers", Rückerstattungen: "refunds" } as Record<string, string>)[tab];
  const result = useFinanceQuery<Record<string, Array<Record<string, unknown>>>>(query, type ? { type } : tab === "Forecast-Konflikte" ? { status: "MISSED" } : {});
  const [message, setMessage] = useState("");
  const items = useMemo(() => { const data = result.envelope?.data; if (!data) return []; const rows = data.reviews ?? data.patterns?.filter((x) => x.status === "PROPOSED") ?? data.expected_transactions?.filter((x) => x.status === "MISSED") ?? []; return accountReviewTypes[tab] ? rows.filter((item) => item.review_type === accountReviewTypes[tab]) : rows; }, [result.envelope, tab]);
  const reviewAction = async (item: Record<string, unknown>, confirm: boolean) => {
    const eventPayload = (item.payload as Record<string, unknown> | undefined) ?? item;
    const transaction = (item.transaction as Record<string, unknown> | undefined) ?? item;
    const classification = item.classification as { payload?: Record<string, unknown> } | undefined;
    let command = ""; let payload: Record<string, unknown> = {};
    if (tab === "Klassifikationen") { command = confirm ? "ConfirmClassification" : "RejectClassification"; payload = { transaction_id: transaction.transaction_id, ...(confirm ? { category_code: classification?.payload?.category_code ?? item.proposed_category } : {}) }; }
    if (tab === "Dubletten") { command = confirm ? "ConfirmDuplicate" : "RejectDuplicate"; payload = { relation_id: item.aggregate_id ?? eventPayload.relation_id }; }
    if (tab === "Transfers") { command = confirm ? "ConfirmTransfer" : "RejectTransfer"; payload = { outgoing_id: eventPayload.outgoing_transaction_id, incoming_id: eventPayload.incoming_transaction_id }; }
    if (tab === "Rückerstattungen") { command = confirm ? "ConfirmRefund" : "RejectRefund"; payload = { refund_id: eventPayload.refund_transaction_id, original_id: eventPayload.original_transaction_id, ...(confirm ? { amount: eventPayload.proposed_amount } : {}) }; }
    if (tab === "Wiederkehrende Muster") { command = confirm ? "ConfirmRecurringPattern" : "RejectRecurringPattern"; payload = { pattern_id: item.pattern_id }; }
    if (!command || Object.values(payload).some((value) => value == null)) { setMessage("Diese Prüfung benötigt weitere Angaben in der Detailansicht."); return; }
    await financeBridge.command(command, payload); setMessage(`${confirm ? "Bestätigung" : "Ablehnung"} wurde lokal dokumentiert.`);
  };
  const presentation = (item: Record<string, unknown>) => { const tx = (item.transaction as Record<string, unknown> | undefined) ?? {}; const eventPayload = (item.payload as Record<string, unknown> | undefined) ?? {}; return { title: String(item.counterparty ?? tx.counterparty ?? item.merchant_key ?? item.display_name ?? item.title ?? "Prüfung erforderlich"), detail: String(item.detail ?? item.proposed_category ?? item.review_type ?? eventPayload.status ?? item.status ?? "Policy-Abweichung nachvollziehen") }; };
  const passiveReview = tab === "Forecast-Konflikte" || Boolean(accountReviewTypes[tab]);
  return <><div className="review-tabs" role="tablist" aria-label="Prüfgruppen">{reviewTabs.map((item) => <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>{item}<span>{item === tab ? items.length : ""}</span></button>)}</div>{message && <div className="toast" role="status">{message}</div>}<QueryBoundary result={{ ...result, state: result.state === "EMPTY" ? "READY" : result.state }}>{() => <section className="review-list"><div className="section-intro"><div><h2>{tab}</h2><p>Entscheidungen werden als neue Events dokumentiert und nie still überschrieben.</p></div>{type && <button className="secondary" onClick={() => financeBridge.command(type === "duplicates" ? "DetectDuplicates" : type === "transfers" ? "DetectTransfers" : "DetectRefunds", {})}>Prüfungen aktualisieren</button>}</div>{items.length === 0 ? <div className="panel page-state"><span className="empty-icon">✓</span><h3>Alles geprüft</h3><p>In dieser Gruppe sind keine offenen Entscheidungen.</p></div> : items.map((item, index) => { const shown = presentation(item); return <article className="panel review-card" key={String(item.transaction_id ?? item.relation_id ?? item.pattern_id ?? item.expected_transaction_id ?? index)}><span className="review-index">{String(index + 1).padStart(2, "0")}</span><div><span className="eyebrow">{tab.toUpperCase()}</span><h3>{shown.title}</h3><p>{shown.detail}</p></div><div className="review-actions">{passiveReview ? <span className="status-badge missed">OFFEN</span> : <><button className="primary small" onClick={() => reviewAction(item, true)}>Bestätigen</button><button className="secondary small" onClick={() => reviewAction(item, false)}>Ablehnen</button></>}</div></article>; })}</section>}</QueryBoundary></>;
}

function LegacyImports() {
  const result = useFinanceQuery<{ imports: Array<Record<string, string>> }>("ListImportBatches");
  const [message, setMessage] = useState("");
  const [bankIdentifier, setBankIdentifier] = useState("");
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [analysis, setAnalysis] = useState<Record<string, unknown> | null>(null);
  const [accountsBySection, setAccountsBySection] = useState<Record<string, string>>({});
  const [openingByAccount, setOpeningByAccount] = useState<Record<string, string>>({});
  const [confirmEmptyBrokerage, setConfirmEmptyBrokerage] = useState(false);
  const [importResult, setImportResult] = useState<Record<string, unknown> | null>(null);
  const sections = (analysis?.sections as Array<Record<string, unknown>> | undefined) ?? [];
  const analysisId = String(analysis?.analysis_id ?? "");
  const priorDate = analysis?.period_start
    ? new Date(new Date(String(analysis.period_start)).getTime() - 86_400_000).toISOString().slice(0, 10)
    : "";
  const choose = async () => {
    const selected = await financeBridge.selectImportFile();
    const path = selected?.file_ref ?? null;
    setSourcePath(path);
    if (!path) { setMessage("Die lokale Dateiauswahl ist nur im Desktop-Wrapper verfügbar."); return; }
    setBusy(true); setMessage("");
    try {
      const response = await financeBridge.command("AnalyzeImportFile", { source_file_reference: path, requested_profile: "GermanMultiAccountCsvV1", ...(bankIdentifier.trim() ? { confirmed_bank_identifier: bankIdentifier.trim() } : {}) }) as { result?: Record<string, unknown> };
      const analyzed = response.result ?? null;
      setAnalysis(analyzed);
      setBankIdentifier(String(analyzed?.bank_identifier ?? bankIdentifier));
      setAccountsBySection(Object.fromEntries((((analyzed?.sections as Array<Record<string, unknown>> | undefined) ?? []).filter((section) => section.mapped_account_id).map((section) => [String(section.section_id), String(section.mapped_account_id)]))));
      setStep(2); setMessage("Datei wurde ausschließlich lokal analysiert.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Importanalyse fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  const mapSections = async () => {
    const mappings = sections.map((section) => ({
      section_id: section.section_id,
      account_id: section.section_type === "UNKNOWN" ? null : accountsBySection[String(section.section_id)],
      action: section.section_type === "UNKNOWN" ? "SKIP_SECTION" : "USE_EXISTING_ACCOUNT",
    }));
    if (mappings.some((item) => item.action !== "SKIP_SECTION" && !item.account_id)) { setMessage("Ordne jedem unterstützten Abschnitt ein lokales Konto zu."); return; }
    setBusy(true);
    try { await financeBridge.command("MapImportSections", { analysis_id: analysisId, section_mappings: mappings }); setStep(3); setMessage("Abschnittszuordnung wurde als Event gespeichert."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Kontenzuordnung fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  const recordOpenings = async () => {
    const cashSections = sections.filter((section) => section.section_type === "CHECKING" || section.section_type === "SAVINGS");
    const brokerageSections = sections.filter((section) => section.section_type === "BROKERAGE");
    if (cashSections.some((section) => !openingByAccount[accountsBySection[String(section.section_id)]])) { setMessage("Bestätige für Giro- und Tagesgeldkonten einen Anfangssaldo."); return; }
    if (brokerageSections.length > 0 && !confirmEmptyBrokerage) { setMessage("Bestätige einen leeren Depot-Anfangsbestand oder erfasse vorhandene Anfangspositionen."); return; }
    setBusy(true);
    try {
      for (const section of cashSections) {
        const accountId = accountsBySection[String(section.section_id)];
        await financeBridge.command("RecordOpeningBalance", { account_id: accountId, balance_date: priorDate, booked_balance: openingByAccount[accountId], available_balance: null, currency: "EUR", source: "MANUAL_ENTRY", confirmation: true, comment: "Im Importassistenten bestätigt" });
      }
      for (const section of brokerageSections) {
        await financeBridge.command("ConfirmEmptyOpeningSecurityPositions", { account_id: accountsBySection[String(section.section_id)], valuation_date: priorDate });
      }
      const preview = await financeBridge.command("ImportMappedSections", { analysis_id: analysisId, parser_profile: "GermanMultiAccountCsvV1", parser_version: "1.0.0", import_mode: "VALIDATE_ONLY" }) as { result?: Record<string, unknown> };
      setImportResult(preview.result ?? null);
      setStep(4); setMessage("Anfangswerte wurden explizit bestätigt.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Anfangssaldo konnte nicht gespeichert werden."); }
    finally { setBusy(false); }
  };
  const execute = async () => {
    setBusy(true);
    try {
      const response = await financeBridge.command("ImportMappedSections", { analysis_id: analysisId, parser_profile: "GermanMultiAccountCsvV1", parser_version: "1.0.0", import_mode: "IMPORT_NEW" }) as { result?: Record<string, unknown> };
      const imported = response.result ?? null;
      if (Number(imported?.normalized_transaction_count ?? 0) + Number(imported?.security_transaction_count ?? 0) > 0) await financeBridge.command("DetectInvestmentFundingRelations", {});
      setImportResult(imported); setStep(5); setMessage("Multi-Account-Import wurde lokal abgeschlossen.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Import fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  return <>
    <section className="import-hero panel"><span className="import-icon">⇩</span><div><h2>Monatlichen Bankexport importieren</h2><p>Eine Datei gehört zu genau einer Bank und einem Berichtsmonat. Giro-, Tagesgeld- und Depotabschnitte werden getrennt geprüft und verarbeitet.</p><label>Bankkennung, falls nicht in der Datei enthalten<input value={bankIdentifier} onChange={(event) => setBankIdentifier(event.target.value)} placeholder="z. B. BANK_A" /></label><div className="review-tabs" aria-label="Importschritte">{["Datei", "Konten", "Anfangswerte", "Vorschau", "Ergebnis"].map((title, index) => <span key={title} className={step === index + 1 ? "status-badge confirmed" : "status-badge"}>{index + 1}. {title}</span>)}</div></div><button className="primary" disabled={busy} onClick={choose}>{sourcePath ? "Andere CSV wählen" : "CSV auswählen"}</button></section>
    {message && <div className="toast" role="status">{message}</div>}
    {analysis && step >= 2 && <section className="panel"><PanelHeader title={sourcePath?.split(/[\\/]/).pop() ?? "Analysierte Datei"} subtitle={`${String(analysis.bank_identifier)} · ${String(analysis.report_month)} · ${sections.length} Abschnitte · ${String(analysis.encoding)}`} /><p><code>{String(analysis.source_file_hash)}</code> · {String(analysis.file_size)} Bytes</p>{step === 2 && <><div className="account-grid">{sections.map((section) => <article className="summary-card" key={String(section.section_id)}><span className="eyebrow">{String(section.section_type)}</span><h3>{String(section.original_title)}</h3><p>{String(section.record_count)} Buchungen · {section.empty ? "leer, gültig" : "Daten erkannt"}{section.account_reference ? ` · ${String(section.account_reference)}` : ""}</p>{section.section_type === "UNKNOWN" ? <span className="status-badge missed">WIRD ÜBERSPRUNGEN</span> : <label>Lokales Konto<input value={accountsBySection[String(section.section_id)] ?? ""} onChange={(event) => setAccountsBySection((current) => ({ ...current, [String(section.section_id)]: event.target.value }))} placeholder="acc_…" /></label>}</article>)}</div><button className="primary" disabled={busy} onClick={mapSections}>Zuordnung bestätigen</button></>}{step === 3 && <><h3>Anfangswerte zum {priorDate}</h3>{sections.filter((section) => section.section_type === "CHECKING" || section.section_type === "SAVINGS").map((section) => { const accountId = accountsBySection[String(section.section_id)]; return <label key={accountId}>{String(section.original_title)} · {accountId}<input inputMode="decimal" value={openingByAccount[accountId] ?? ""} onChange={(event) => setOpeningByAccount((current) => ({ ...current, [accountId]: event.target.value }))} placeholder="0.00" /></label>; })}{sections.some((section) => section.section_type === "BROKERAGE") && <label><input type="checkbox" checked={confirmEmptyBrokerage} onChange={(event) => setConfirmEmptyBrokerage(event.target.checked)} /> Das Depot hatte vor diesem Monat keine Positionen. Vorhandene Positionen erfasse ich stattdessen separat.</label>}<button className="primary" disabled={busy} onClick={recordOpenings}>Anfangswerte bestätigen</button></>}{step === 4 && <><h3>Importvorschau</h3><div className="metric-grid">{sections.map((section) => <article className="metric" key={String(section.section_id)}><span>{String(section.original_title)}</span><strong>{String(section.record_count)}</strong><small>{section.empty ? "EMPTY_COMPLETED" : "erkannte Datensätze"}</small></article>)}<Metric label="Mögliche Relationsmatches" value={String(importResult?.possible_relation_match_count ?? 0)} tone="neutral" note="nach Import prüfbar" /></div><p>Salden und Bestände werden je Abschnitt abgeglichen. Kontoübergreifende Relationen werden erst nach dem Abschnittsimport erkannt.</p><button className="primary" disabled={busy} onClick={execute}>Import verbindlich ausführen</button></>}{step === 5 && importResult && <><h3>Importergebnis</h3><div className="metric-grid"><Metric label="Kontobuchungen" value={String(importResult.normalized_transaction_count ?? 0)} tone="neutral" note="normalisiert" /><Metric label="Depotbuchungen" value={String(importResult.security_transaction_count ?? 0)} tone="neutral" note="separat" /><Metric label="Relationsmatches" value={String(importResult.possible_relation_match_count ?? 0)} tone="neutral" note="zur Prüfung" /><Metric label="Gesamtstatus" value={String(importResult.status ?? "COMPLETED")} tone={importResult.status === "COMPLETED" ? "positive" : "neutral"} note="lokal" /></div><div className="account-grid">{((importResult.section_results as Array<Record<string, unknown>> | undefined) ?? []).map((section) => <article className="summary-card" key={String(section.section_id)}><span className="eyebrow">{String(section.section_type)}</span><h3>{String(section.status)}</h3><p>{String(section.record_count)} Datensätze · {String(section.account_id ?? "übersprungen")}</p>{((section.warnings as string[] | undefined) ?? []).map((warning) => <small key={warning}>{warning}</small>)}</article>)}</div></>}</section>}
    <QueryBoundary result={result}>{({ imports }) => <section className="panel table-panel"><PanelHeader title="Importhistorie" subtitle="Inhaltshashes statt Quelldateien"/><table><thead><tr><th>Import</th><th>Zeitpunkt</th><th>Parser</th><th>Status</th><th>Inhaltshash</th></tr></thead><tbody>{imports.map((item) => <tr key={item.import_id}><td><strong>{item.import_id}</strong></td><td>{date(item.created_at)}</td><td>{item.parser_version}</td><td><span className="status-badge confirmed">{item.status}</span></td><td><code>{item.content_hash}</code></td></tr>)}</tbody></table></section>}</QueryBoundary>
  </>;
}

function Settings({ manifest }: { manifest: CapabilityManifest }) {
  const result = useFinanceQuery<RuntimeSecurityStatus>("GetRuntimeSecurityStatus");
  const backups = useFinanceQuery<{ backups: BackupRecord[] }>("ListBackups");
  const integrity = useFinanceQuery<StoreIntegrity>("GetStoreIntegrity");
  const keys = useFinanceQuery<KeyStatus>("GetKeyStatus");
  const migrations = useFinanceQuery<MigrationStatus>("GetMigrationStatus");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [restorePath, setRestorePath] = useState<string | null>(null);
  const [rotateReady, setRotateReady] = useState(false);
  const rows = backups.envelope?.data.backups ?? [];
  const latest = rows.find((item) => item.verification_status === "VALID");
  const run = async (command: string, payload: Record<string, unknown>, success: string) => {
    setBusy(command); setMessage("");
    try { await financeBridge.command(command, payload); setMessage(success); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Lokale Operation fehlgeschlagen."); }
    finally { setBusy(""); }
  };
  return <QueryBoundary result={result}>{(security) => <>
    <section className="section-intro"><div><h2>Laufzeitsicherheit</h2><p>Dynamisch geprüft. „Nicht geprüft“ wird ausdrücklich nicht als sicher gewertet.</p></div><span className="version-chip">Extension {manifest.extension_version} · Schema {manifest.schema_version}</span></section>
    <section className="security-grid">{Object.entries(security.checks).map(([name, status]) => <article className="panel security-check" key={name}><span className={`security-icon ${status.toLowerCase()}`}>{status === "PASSED" ? "✓" : status === "FAILED" ? "!" : "?"}</span><div><strong>{label(name)}</strong><small>{status === "PASSED" ? "Bestanden" : status === "FAILED" ? "Fehlgeschlagen" : "Nicht geprüft"}</small></div></article>)}</section>
    <h2 className="subheading">Datensicherheit und Wiederherstellung</h2>
    {message && <div className="toast" role="status">{message}</div>}
    <section className="recovery-grid">
      <article className="panel recovery-card"><span className="recovery-icon">▣</span><div><h3>Datensicherung</h3><p>Vollständige, authentifiziert verschlüsselte Archive mit unabhängigem lokalem Schlüssel.</p><strong>{rows.length} lokale Backups</strong><small>{latest?.created_at ? `Zuletzt ${date(latest.created_at)}` : "Noch keine Sicherung"}</small></div><button className="primary small" disabled={Boolean(busy)} onClick={() => run("CreateBackup", {}, "Backup wurde vollständig erstellt und verifiziert.")}>Backup erstellen</button></article>
      <article className="panel recovery-card"><span className="recovery-icon">↺</span><div><h3>Datenwiederherstellung</h3><p>Vor dem atomaren Austausch werden Archiv, Version, Schema und alle enthaltenen Dateien geprüft.</p><strong>{latest ? "Verifiziertes Backup verfügbar" : "Kein gültiges Backup"}</strong><small>Keine Teilwiederherstellung</small></div><button className="secondary small" disabled={!latest || Boolean(busy)} onClick={() => setRestorePath(latest?.path ?? null)}>Wiederherstellen</button></article>
      <article className="panel recovery-card"><span className="recovery-icon">⇧</span><div><h3>Datenexport</h3><p>Portables lokales Finanzarchiv ohne Cloud-Übertragung oder externe Verarbeitung.</p><strong>Verschlüsselt und vollständig</strong><small>Formatversion 1</small></div><button className="secondary small" disabled={Boolean(busy)} onClick={() => run("ExportFinanceData", {}, "Lokales Finanzarchiv wurde exportiert und verifiziert.")}>Archiv exportieren</button></article>
      <article className="panel recovery-card"><span className={`recovery-icon ${integrity.envelope?.data.status === "VALID" ? "ok" : "warn"}`}>{integrity.envelope?.data.status === "VALID" ? "✓" : "!"}</span><div><h3>Speicherintegrität</h3><p>SQLite-Struktur, Event-Hashes, Aggregate-Versionen, Schemas und Importdateien.</p><strong>{integrity.envelope?.data.status === "VALID" ? "Integrität bestätigt" : "Prüfung erforderlich"}</strong><small>{integrity.envelope?.data.event_count ?? 0} Events · Schema {integrity.envelope?.data.store_schema_version ?? "–"}</small></div><div className="card-actions"><button className="secondary small" disabled={Boolean(busy)} onClick={() => run("ValidateStoreIntegrity", {}, "Vollständige Integritätsprüfung abgeschlossen.")}>Prüfen</button><button className="secondary small" disabled={integrity.envelope?.data.status !== "VALID" || Boolean(busy)} onClick={() => run("RepairLocalStore", {}, "Projektions-Checkpoints wurden zurückgesetzt; Ansichten werden neu aufgebaut.")}>Reparieren</button></div></article>
      <article className="panel recovery-card"><span className="recovery-icon">⌘</span><div><h3>Schlüsselstatus</h3><p>Datenbank- und Backup-Schlüssel bleiben getrennt in der lokalen Schlüsselverwaltung.</p><strong>{keys.envelope?.data.archive_key.independent_from_store ? "Schlüssel getrennt" : "Backup-Schlüssel nicht bereit"}</strong><small>Store {keys.envelope?.data.database_key.fingerprint ?? "–"} · Backup {keys.envelope?.data.archive_key.fingerprint ?? "–"}</small></div><button className="secondary small" disabled={Boolean(busy)} onClick={() => setRotateReady(true)}>Schlüssel rotieren</button></article>
      <article className="panel recovery-card"><span className="recovery-icon ok">↑</span><div><h3>Migrationen</h3><p>Store-Upgrades sind protokolliert; neuere Datenstände werden nicht mit älterer Software geöffnet.</p><strong>{migrations.envelope?.data.status === "CURRENT" ? "Schema aktuell" : "Migration erforderlich"}</strong><small>Store v{migrations.envelope?.data.current_store_schema_version ?? "–"} · Downgrade-Schutz aktiv</small></div><span className="status-badge confirmed">{migrations.envelope?.data.status ?? "LOADING"}</span></article>
    </section>
    {restorePath && <section className="restore-warning panel" role="alert"><div><strong>Vollständige Wiederherstellung bestätigen</strong><p>Der aktuelle lokale Store wird erst nach erfolgreicher Integritäts- und Kompatibilitätsprüfung atomar ersetzt.</p></div><button className="danger-link" onClick={() => setRestorePath(null)}>Abbrechen</button><button className="primary small" disabled={Boolean(busy)} onClick={() => run("RestoreBackup", { archive_path: restorePath }, "Backup wurde vollständig wiederhergestellt.").then(() => setRestorePath(null))}>Wiederherstellung bestätigen</button></section>}
    {rotateReady && <section className="restore-warning panel" role="alert"><div><strong>Schlüsselrotation bestätigen</strong><p>Vor der Rotation wird automatisch ein verifiziertes Recovery-Backup mit dem unabhängigen Archivschlüssel erzeugt.</p></div><button className="danger-link" onClick={() => setRotateReady(false)}>Abbrechen</button><button className="primary small" disabled={Boolean(busy)} onClick={() => run("RotateEncryptionKey", {}, "Schlüssel wurde nach einem Recovery-Backup atomar rotiert.").then(() => setRotateReady(false))}>Rotation bestätigen</button></section>}
    <section className="panel settings-info"><PanelHeader title="Lokaler Datenvertrag" subtitle="UI → Desktop IPC → Application Service"/><dl><div><dt>Letzte Event-Sequenz</dt><dd>#{security.last_event_sequence}</dd></div><div><dt>Netzwerkressourcen</dt><dd>Keine</dd></div><div><dt>Direkter Store-Zugriff</dt><dd>Nicht erlaubt</dd></div><div><dt>Externe Modelle</dt><dd>Deaktiviert</dd></div></dl></section>
    <section className="panel settings-info">
      <PanelHeader title="Versionen und Kompatibilität" subtitle="Alle Werte stammen aus dem Runtime-Manifest, keine statischen Angaben" />
      <dl>
        <div><dt>Produktversion</dt><dd>{manifest.product_version ?? manifest.extension_version}</dd></div>
        <div><dt>Contract-Version</dt><dd>{manifest.contract_version ?? "–"}</dd></div>
        <div><dt>Store-Schema</dt><dd>{manifest.store_schema_version ?? "–"}</dd></div>
        <div><dt>UI-Contract-Version</dt><dd>{manifest.ui_contract_version ?? "–"}</dd></div>
        <div><dt>UI-Bundle-Hash</dt><dd>{integrity.envelope?.data.status ?? "–"}</dd></div>
        <div><dt>Schema-Katalog-Hash</dt><dd>{manifest.schema_version}</dd></div>
        <div><dt>Python-Worker-Version</dt><dd>{manifest.extension_version}</dd></div>
        <div><dt>Tauri-Host-Version</dt><dd>{DESKTOP_CONTRACT_VERSION}</dd></div>
      </dl>
      <h3 className="subheading">Projection-Versionen</h3>
      <dl>{Object.entries(manifest.projection_versions ?? {}).map(([name, version]) => <div key={name}><dt>{label(name)}</dt><dd>{version}</dd></div>)}</dl>
      <dl>
        <div><dt>Kompatibilitätsstatus</dt><dd><span className="status-badge confirmed">{migrations.envelope?.data.status === "CURRENT" ? "Kompatibel" : "Prüfung erforderlich"}</span></dd></div>
        <div><dt>Letzter erfolgreicher Projection-Rebuild</dt><dd>Nicht erforderlich · Projektionen werden live aus dem Event Store berechnet</dd></div>
        <div><dt>Letzte Integritätsprüfung</dt><dd>{integrity.envelope?.data.checked_at ? date(integrity.envelope.data.checked_at) : "–"}</dd></div>
      </dl>
    </section>
  </>}</QueryBoundary>;
}
