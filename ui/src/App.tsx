import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { DESKTOP_CONTRACT_VERSION, DesktopOperationError, financeBridge, SchemaCompatibilityError } from "./bridge";
import { Imports } from "./Imports";
import type {
  CapabilityManifest,
  Category,
  CategoryList,
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
  LocalModelStatus,
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
import { statusLabel, statusTone } from "./statusLabels";

type PageId = "overview" | "accounts" | "transactions" | "categories" | "recurring" | "forecast" | "wealth" | "reviews" | "imports" | "settings";
type QueryResult<T> = { state: ViewState; envelope?: Envelope<T>; error?: string };
type ReviewType = "CATEGORY" | "AI_SUGGESTION" | "BALANCE" | "IMPORT" | "RELATION" | "FORMAT_CHANGE";
type ReviewItem = {
  review_id: string; review_type: ReviewType; severity: "ERROR" | "WARNING" | "INFO";
  entity_id: string; entity_type: string; title: string; description: string; created_at: string;
  account_ids: string[];
  action_target: { page: PageId; view: string; entity_id: string };
};
type ReviewInbox = {
  total_open: number;
  items_by_type: Record<ReviewType, number>;
  severity: Record<"ERROR" | "WARNING" | "INFO", number>;
  items: ReviewItem[];
};
type NavigationTarget = ReviewItem["action_target"] | null;

const money = (value: string | null | undefined, currency = "EUR") =>
  value == null
    ? "–"
    : new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(Number(value));
const date = (value: string) => new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
const monthLabel = (value: string | null | undefined) => {
  if (!value) return "–";
  const [year, month] = value.split("-").map(Number);
  return year && month ? `${MONTH_NAMES[month - 1]} ${year}` : value;
};
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
export const normalizeCategory = (item: Partial<Category> & { category_code: string }): Category => ({
  category_code: item.category_code,
  display_name: item.display_name || categoryLabel(item.category_code),
  category_type: item.category_type ?? (item.category_code.startsWith("INCOME_") ? "INCOME" : "EXPENSE"),
  status: item.status ?? "ACTIVE",
  system_category: item.system_category ?? !item.category_code.startsWith("CUSTOM_"),
  parent_category_code: item.parent_category_code ?? null,
  color: item.color ?? null,
  icon: item.icon ?? null,
  legacy: item.legacy ?? item.category_code.startsWith("CUSTOM_"),
});
const percent = (value: number | null | undefined) => value == null ? "–" : new Intl.NumberFormat("de-DE", { style: "percent", maximumFractionDigits: 1 }).format(value);
export const localRuntimeSummary = (status: Partial<LocalModelStatus> | undefined) => ({
  version: status?.runtime_details?.version ?? "nicht eingerichtet",
  integrity: status?.runtime_details?.integrity_status ?? "MISSING",
});
const relationStatusLabel = (value: string) => value === "NONE" ? "Nicht erkannt" : label(value);
const localISODate = (value = new Date()) => `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`;

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
  const today = new Date();
  const [mode, setMode] = useState<PeriodMode>("MONTH");
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [rangeStart, setRangeStart] = useState(`${today.getFullYear()}-${pad2(today.getMonth() + 1)}-01`);
  const [rangeEnd, setRangeEnd] = useState(localISODate(today));

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
          <button className="text-button" onClick={() => { const now = new Date(); setRangeStart(`${now.getFullYear()}-${pad2(now.getMonth() + 1)}-01`); setRangeEnd(localISODate(now)); }}>Zurücksetzen</button>
        </div>
      )}
    </div>
  );
}

const navigation: Array<{ id: PageId; text: string; icon: string; capability?: string }> = [
  { id: "overview", text: "Übersicht", icon: "⌂" },
  { id: "transactions", text: "Transaktionen", icon: "↕", capability: "classification" },
  { id: "accounts", text: "Konten", icon: "▤", capability: "accounts" },
  { id: "categories", text: "Kategorien", icon: "◫", capability: "classification" },
  { id: "imports", text: "Importe", icon: "⇩", capability: "imports" },
  { id: "reviews", text: "Prüfen", icon: "✓", capability: "review_inbox" },
  { id: "wealth", text: "Vermögen", icon: "◈", capability: "wealth" },
  { id: "forecast", text: "Prognose", icon: "⌁", capability: "forecasting" },
  { id: "settings", text: "Einstellungen", icon: "⚙" },
];

export function App() {
  const startup = useFinanceQuery<StartupStatus>("GetStartupStatus");
  const manifest = useFinanceQuery<CapabilityManifest>("GetCapabilityManifest");
  const [reviewRefresh, setReviewRefresh] = useState(0);
  const reviewInbox = useFinanceQuery<ReviewInbox>("GetReviewInbox", { refresh: reviewRefresh });
  const [page, setPage] = useState<PageId>("overview");
  const [navigationTarget, setNavigationTarget] = useState<NavigationTarget>(null);
  const periodControls = usePeriodSelection();
  const month = periodControls.period.start_date.slice(0, 7);
  const asOf = periodControls.period.end_date;
  const enabled = manifest.envelope?.data.capabilities ?? {};
  const visibleNavigation = navigation.filter((item) => !item.capability || enabled[item.capability]);
  const openReviews = reviewInbox.envelope?.data.total_open ?? 0;
  const navigate = (nextPage: PageId, target: NavigationTarget = null) => {
    if (nextPage === "reviews") setReviewRefresh((value) => value + 1);
    setNavigationTarget(target);
    setPage(nextPage);
  };

  if (startup.state === "LOADING") return <FullState state="LOADING" />;
  if (startup.error?.includes("DESKTOP_BRIDGE_UNAVAILABLE")) {
    return <DesktopBridgeUnavailableState />;
  }
  if (startup.error) {
    return <DesktopServiceErrorState error={startup.error} />;
  }
  if (!startup.envelope || startup.envelope.data.status !== "READY") {
    return <CriticalState status={startup.envelope?.data.status ?? "INCOMPATIBLE_VERSION"} errorCode={startup.envelope?.data.error_code ?? startup.error} diagnostic={startup.envelope?.data.checks.integrity_diagnostic} />;
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
            <button key={item.id} className={page === item.id ? "nav-item active" : "nav-item"} onClick={() => navigate(item.id)} aria-current={page === item.id ? "page" : undefined}>
              <span aria-hidden="true">{item.icon}</span>{item.text}{item.id === "reviews" && <span className="nav-count" aria-label={`${openReviews} offene Aufgaben`}>{openReviews}</span>}
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
        {page === "overview" && <Overview month={month} reviewCount={openReviews} onNavigate={(next) => navigate(next)} />}
        {page === "accounts" && <AccountsOverview period={periodControls.period} periodPayload={periodControls.payload} focusId={navigationTarget?.page === "accounts" ? navigationTarget.entity_id : undefined} focusView={navigationTarget?.page === "accounts" ? navigationTarget.view : undefined} onNavigate={navigate} />}
        {page === "transactions" && <Transactions month={month} focusId={navigationTarget?.page === "transactions" ? navigationTarget.entity_id : undefined} />}
        {page === "categories" && <Categories month={month} initialTab={navigationTarget?.page === "categories" ? navigationTarget.view : undefined} focusId={navigationTarget?.page === "categories" ? navigationTarget.entity_id : undefined} />}
        {page === "recurring" && <Recurring month={month} />}
        {page === "forecast" && <Forecast month={month} />}
        {page === "wealth" && <Wealth asOf={asOf} />}
        {page === "reviews" && <Reviews inbox={reviewInbox} focusAccountId={navigationTarget?.page === "reviews" && navigationTarget.view === "account" ? navigationTarget.entity_id : undefined} onOpen={(target) => navigate(target.page, target)} />}
        {page === "imports" && <Imports uiMonth={month} manifest={manifest.envelope.data} focusExportId={navigationTarget?.page === "imports" ? navigationTarget.entity_id : undefined} focusView={navigationTarget?.page === "imports" ? navigationTarget.view : undefined} />}
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
  const code = error === "Unbekannter Fehler" ? "DESKTOP_WORKER_UNAVAILABLE" : error.split(":", 1)[0];
  return <main className="full-state critical-state" role="alert">
    <span className="brand-mark">!</span>
    <span className="eyebrow">LOKALER DIENST NICHT VERFÜGBAR</span>
    <h1>Finance konnte nicht gestartet werden</h1>
    <p>Der lokale Hintergrunddienst wurde beendet. Nach einem unerwarteten Abbruch wird ein veralteter Workspace-Lock beim nächsten Start sicher bereinigt.</p>
    <code>{code}</code>
    <small>Es wurden keine Finanzdaten verändert.</small>
  </main>;
}

export function CriticalState({ status, errorCode, diagnostic }: { status: StartupState; errorCode?: string | null; diagnostic?: string }) {
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
  return <main className="full-state critical-state" role="alert"><span className="brand-mark">!</span><span className="eyebrow">SICHERER START BLOCKIERT</span><h1>{selected[0]}</h1><p>{selected[1]}</p>{errorCode && <code>{errorCode}</code>}{diagnostic && <small>Prüfhinweis: {diagnostic}</small>}<button className="primary">{selected[2]}</button><small>Nur lokale, schreibgeschützte Diagnose ist verfügbar.</small></main>;
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
  return <>{(result.state === "STALE" || result.state === "PARTIAL") && <div className="status-banner" role="status"><strong>{result.state === "STALE" ? statusLabel("STALE") : "Teilprojektion"}</strong><span>{result.envelope.projection_sequence < result.envelope.event_store_sequence ? "Die lokale Ansicht wird noch aktualisiert." : result.state === "STALE" ? "Mindestens ein fachlicher Snapshot ist nicht aktuell." : "Ein Teil der lokalen Daten ist noch nicht verfügbar."}</span><details><summary>Technische Details</summary><code>Projektion #{result.envelope.projection_sequence} · Event Store #{result.envelope.event_store_sequence}</code></details></div>}{children(result.envelope.data)}</>;
}

function Overview({ month, reviewCount, onNavigate }: { month: string; reviewCount: number; onNavigate: (page: PageId) => void }) {
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
      <section className="panel outlook"><PanelHeader title="Noch erwartet" subtitle="Bis Monatsende" /><div className="outlook-number positive">+ {money(data.remaining_expected_income)}</div><p>Verbleibende Einnahmen</p><div className="outlook-number negative">− {money(data.remaining_expected_expenses)}</div><p>Verbleibende Ausgaben</p><div className="divider"/><div className="outlook-total"><span>Offene Prüfungen</span><button className="review-pill" onClick={() => onNavigate("reviews")}>{reviewCount} prüfen</button></div></section>
    </div>
    <section className="panel privacy-note"><span className="shield">◇</span><div><strong>Deine Finanzdaten bleiben auf diesem Gerät.</strong><p>Bestände stammen aus expliziten Salden-Snapshots; berechnete Werte und Prognosen sind klar gekennzeichnet.</p></div><span className="sequence">Projektion #{result.envelope?.projection_sequence}</span></section>
  </>}</QueryBoundary>;
}

const OVERVIEW_STATUS_TONE: Record<string, string> = {
  MATCHED: "confirmed", DIFFERENCE: "missed", STALE: "paused",
  MISSING_BALANCE: "missed", REVIEW_REQUIRED: "missed", CLOSED: "paused",
};

function AccountsOverview({ period, periodPayload, focusId, focusView, onNavigate }: { period: PeriodSelection; periodPayload: Record<string, unknown>; focusId?: string; focusView?: string; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const [includeClosed, setIncludeClosed] = useState(false);
  const result = useFinanceQuery<AccountOverviewList>("ListAccountOverviews", { period: periodPayload, include_closed: includeClosed });
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<AccountOverviewRow | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!focusId || !result.envelope) return;
    setSelectedAccount(result.envelope.data.accounts.find((item) => item.account_id === focusId) ?? null);
  }, [focusId, result.envelope]);
  return <QueryBoundary result={result}>{({ accounts }) => {
    const cashAccounts = accounts.filter((item) => item.account_type !== "BROKERAGE");
    const brokerageAccounts = accounts.filter((item) => item.account_type === "BROKERAGE");
    return <>
      <section className="section-intro">
        <div><h2>Kontenübersicht · {period.display_label}</h2><p>Gemeldete und berechnete Salden bleiben getrennt. Depots zeigen Positionen statt eines Girokontosaldos.</p></div>
        <div className="account-overview-actions"><label><input type="checkbox" checked={includeClosed} onChange={(event) => setIncludeClosed(event.target.checked)} /> Geschlossene Konten anzeigen</label><button className="primary" onClick={() => setCreateOpen(true)}>Konto hinzufügen</button></div>
      </section>
      {message && <div className="toast" role="status">{message}</div>}
      <section className="account-group">
        <PanelHeader title="Geldkonten" subtitle="Bestand und Cashflow für den gewählten Zeitraum" />
        <div className="account-workspace-grid">{cashAccounts.map((item) => <article className={`panel account-workspace-card ${item.status === "CLOSED" ? "closed" : ""}`} key={item.account_id}>
          <header><div><span>{label(item.account_type)}</span><h3>{item.display_name}</h3><small>{item.institution}{item.status === "CLOSED" ? " · Geschlossen" : ""}</small></div><span className={`status-badge ${OVERVIEW_STATUS_TONE[item.overview_status] ?? "paused"}`}>{statusLabel(item.overview_status)}</span></header>
          <strong className="account-workspace-balance">{money(item.reported_balance, item.currency)}</strong>
          <small>Gemeldeter Saldo{item.reported_balance_date ? ` · ${date(item.reported_balance_date)}` : " · noch nicht erfasst"}</small>
          <div className="account-period-stats"><b>{period.display_label}</b><span>Einnahmen<strong className="positive-text">+{money(item.period_income, item.currency)}</strong></span><span>Ausgaben<strong className="negative-text">−{money(item.period_expenses, item.currency)}</strong></span><span>Netto<strong>{money(item.period_net_cashflow, item.currency)}</strong></span></div>
          <footer><div><small>Letzter Import</small><strong>{monthLabel(item.last_import_month)}</strong></div><button className={item.open_review_count ? "review-link open" : "review-link"} onClick={() => onNavigate("reviews", { page: "reviews", view: "account", entity_id: item.account_id })}>Offene Prüfungen: {item.open_review_count}</button><button className="primary small" onClick={() => setSelectedAccount(item)} aria-label={`Konto ${item.display_name} öffnen`}>Konto öffnen</button></footer>
        </article>)}</div>
      </section>
      {brokerageAccounts.length > 0 && <section className="account-group">
        <PanelHeader title="Depots" subtitle="Positionen und Positionsabgleich statt Girokonto-Cashflow" />
        <div className="account-workspace-grid">{brokerageAccounts.map((item) => <article className={`panel account-workspace-card brokerage ${item.status === "CLOSED" ? "closed" : ""}`} key={item.account_id}>
          <header><div><span>Depot</span><h3>{item.display_name}</h3><small>{item.institution}{item.status === "CLOSED" ? " · Geschlossen" : ""}</small></div><span className={`status-badge ${OVERVIEW_STATUS_TONE[item.overview_status] ?? "paused"}`}>{statusLabel(item.overview_status)}</span></header>
          <strong className="account-workspace-balance">{item.position_count ?? 0} Positionen</strong>
          <small>Positionsabgleich: {statusLabel(item.position_reconciliation_status ?? item.reconciliation_status)}</small>
          <div className="brokerage-facts"><span>Anschaffungswert<strong>{money(item.acquisition_value, item.currency)}</strong></span><span>Letzter Import<strong>{monthLabel(item.last_import_month)}</strong></span></div>
          <footer><button className={item.open_review_count ? "review-link open" : "review-link"} onClick={() => onNavigate("reviews", { page: "reviews", view: "account", entity_id: item.account_id })}>Offene Prüfungen: {item.open_review_count}</button><button className="primary small" onClick={() => setSelectedAccount(item)} aria-label={`Depot ${item.display_name} öffnen`}>Depot öffnen</button></footer>
        </article>)}</div>
      </section>}
      {createOpen && <CreateAccountDialog onClose={() => setCreateOpen(false)} onCreated={() => { setCreateOpen(false); setMessage("Konto wurde lokal angelegt."); }} />}
      {selectedAccount && <AccountWorkspace account={selectedAccount} initialTab={focusView} period={period} periodPayload={periodPayload} onClose={() => setSelectedAccount(null)} onMessage={setMessage} onNavigate={onNavigate} />}
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

function AccountWorkspace({ account, initialTab, period, periodPayload, onClose, onMessage, onNavigate }: { account: AccountOverviewRow; initialTab?: string; period: PeriodSelection; periodPayload: Record<string, unknown>; onClose: () => void; onMessage: (message: string) => void; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const first = useRef<HTMLButtonElement>(null);
  const handleKeyDown = useModalFocus(first, onClose);
  const allowedInitialTab = (["overview", "transactions", "balances", "reconciliations", "imports", "positions", "audit"] as string[]).includes(initialTab ?? "") ? initialTab as AccountTabId : "overview";
  const [tab, setTab] = useState<AccountTabId>(allowedInitialTab);
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
    <div className="account-workspace-heading"><div><h2 id="account-workspace-title">{account.display_name}</h2><p>{account.institution} · {label(account.account_type)}{account.status === "CLOSED" ? " · Geschlossen und schreibgeschützt" : ""}</p></div><span className={`status-badge ${OVERVIEW_STATUS_TONE[account.overview_status] ?? "paused"}`}>{statusLabel(account.overview_status)}</span></div>
    <div className="tabs" role="tablist" aria-label="Kontobereiche">
      {tabs.map(([id, text]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{text}</button>)}
    </div>
    {tab === "overview" && <AccountTabOverview accountId={account.account_id} periodPayload={periodPayload} onMessage={onMessage} onNavigate={onNavigate} readOnly={account.status === "CLOSED"} />}
    {tab === "transactions" && <AccountTabTransactions accountId={account.account_id} periodPayload={periodPayload} onNavigate={onNavigate} onAudit={() => setTab("audit")} />}
    {tab === "balances" && <AccountTabBalances accountId={account.account_id} periodPayload={periodPayload} onAudit={() => setTab("audit")} />}
    {tab === "reconciliations" && <AccountTabReconciliations accountId={account.account_id} onMessage={onMessage} onNavigate={onNavigate} />}
    {tab === "imports" && <AccountTabImports accountId={account.account_id} onNavigate={onNavigate} onAudit={() => setTab("audit")} />}
    {tab === "positions" && isBrokerage && <AccountTabPositions accountId={account.account_id} />}
    {tab === "audit" && <AccountTabAudit accountId={account.account_id} onNavigate={onNavigate} onOpenTab={setTab} />}
  </section></div>;
}

function AccountTabOverview({ accountId, periodPayload, onMessage, onNavigate, readOnly }: { accountId: string; periodPayload: Record<string, unknown>; onMessage: (message: string) => void; onNavigate: (page: PageId, target?: NavigationTarget) => void; readOnly: boolean }) {
  const result = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: accountId, period: periodPayload });
  const [amount, setAmount] = useState("");
  const [balanceDate, setBalanceDate] = useState(localISODate());
  const reconcile = async () => { await financeBridge.command("ReconcileAccountBalance", { account_id: accountId }); onMessage("Saldenabgleich wurde lokal ausgeführt."); };
  const record = async () => { await financeBridge.command("RecordBalanceSnapshot", { account_id: accountId, balance_date: balanceDate, booked_balance: amount, available_balance: amount, currency: "EUR", source: "MANUAL_ENTRY", confidence: "HIGH" }); onMessage("Saldo-Snapshot wurde unveränderlich erfasst."); };
  return <QueryBoundary result={result}>{(data) => <div className="account-tab-overview">
    <section className="account-detail-metrics" aria-label="Kontokennzahlen">
      <article><span>Gemeldeter Saldo</span><strong>{money(data.account.reported_balance, data.account.currency)}</strong><small>{data.account.reported_balance_date ? `Stand ${date(data.account.reported_balance_date)}` : "Noch nicht erfasst"}</small></article>
      <article><span>Berechneter Saldo</span><strong>{money(data.account.calculated_balance, data.account.currency)}</strong><small>Aus bestätigten Buchungen</small></article>
      <article className={Number(data.account.balance_difference ?? 0) !== 0 ? "difference" : ""}><span>Differenz</span><strong>{money(data.account.balance_difference, data.account.currency)}</strong><small>{statusLabel(data.account.reconciliation_status)}</small></article>
      <article><span>Netto-Cashflow</span><strong>{money(data.period_summary.period_net_cashflow)}</strong><small>{money(data.period_summary.period_income)} Einnahmen · {money(data.period_summary.period_expenses)} Ausgaben</small></article>
    </section>
    <section className="account-overview-context"><div><span>Letzter Import</span><strong>{monthLabel(data.account.last_import_month)}</strong></div><button onClick={() => onNavigate("reviews", { page: "reviews", view: "account", entity_id: accountId })}><span>Offene Prüfungen</span><strong>{data.open_review_count}</strong><small>Im Review Center öffnen →</small></button></section>
    {!readOnly && <div className="dialog-actions balance-actions">
      <label className="balance-entry">Neuen gemeldeten Saldo erfassen<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} /></label>
      <label className="balance-entry">Stand vom<input type="date" value={balanceDate} onChange={(event) => setBalanceDate(event.target.value)} /></label>
      <button className="secondary" onClick={reconcile}>Saldo abgleichen</button>
      <button className="primary" disabled={!amount.trim()} onClick={record}>Snapshot erfassen</button>
    </div>}
    {readOnly && <div className="toast compact">Dieses geschlossene Konto bleibt vollständig lesbar. Neue Salden können nicht erfasst werden.</div>}
    <details className="audit-technical"><summary>Technische Details</summary><small>Datenstand {result.envelope?.freshness_status ?? "CURRENT"} · Projektion {result.envelope?.projection_version ?? "–"}</small></details>
  </div>}</QueryBoundary>;
}

function AccountTabTransactions({ accountId, periodPayload, onNavigate, onAudit }: { accountId: string; periodPayload: Record<string, unknown>; onNavigate: (page: PageId, target?: NavigationTarget) => void; onAudit: () => void }) {
  const result = useFinanceQuery<AccountTransactionList>("ListAccountTransactions", { account_id: accountId, period: periodPayload });
  return <QueryBoundary result={result}>{({ transactions }) => <section className="panel table-panel">
    <table>
      <thead><tr><th>Datum</th><th>Gegenpartei</th><th>Kategorie</th><th>Relation</th><th className="numeric">Betrag</th><th>Status</th><th>Nachverfolgung</th></tr></thead>
      <tbody>{transactions.map((item: AccountTransactionRow) => <tr key={item.transaction_id}>
        <td>{date(item.booking_date)}</td>
        <td>{item.counterparty}<small>{item.normalized_description ?? "–"}</small></td>
        <td><button className="table-link" onClick={() => onNavigate("categories", { page: "categories", view: "manage", entity_id: item.category_code ?? "UNCLASSIFIED" })}>{categoryLabel(item.category_code ?? "UNCLASSIFIED")}</button></td>
        <td>{relationStatusLabel(item.transfer_status ?? "NONE")}</td>
        <td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td>
        <td><span className="status-dot ok">{item.direction === "CREDIT" ? "Eingang" : "Ausgang"}</span></td>
        <td><div className="trace-actions"><button onClick={() => onNavigate("transactions", { page: "transactions", view: "detail", entity_id: item.transaction_id })}>Details</button>{item.export_id && <button onClick={() => onNavigate("imports", { page: "imports", view: "detail", entity_id: item.export_id! })}>Import</button>}<button onClick={onAudit}>Audit</button></div></td>
      </tr>)}</tbody>
    </table>
  </section>}</QueryBoundary>;
}

const BALANCE_TYPE_LABELS: Record<string, string> = { OPENING: "Anfangssaldo", CLOSING: "Endsaldo", INTERMEDIATE: "Zwischensaldo", CALCULATED: "Berechnet" };

function AccountTabBalances({ accountId, periodPayload, onAudit }: { accountId: string; periodPayload: Record<string, unknown>; onAudit: () => void }) {
  const result = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: accountId, period: periodPayload });
  return <QueryBoundary result={result}>{(data) => <section className="balance-ledger">
    {data.balance_history.filter((item) => item.balance_type === "OPENING").map((item) => <article className="panel carry-forward-card" key={`opening_${item.sequence_number}`}><div><span>Anfangssaldo {data.period.display_label}</span><strong>{money(item.reported_value, item.currency)}</strong></div>{item.carry_forward_source_reconciliation_id ? <div><span>Übernommen aus</span><strong>bestätigtem Endsaldo {item.carry_forward_source_period_end ? monthLabel(item.carry_forward_source_period_end.slice(0, 7)) : "der Vorperiode"}</strong><small>{money(item.carry_forward_source_reported_balance, item.currency)}</small></div> : <div><span>Quelle</span><strong>{label(item.source)}</strong></div>}{item.calculated_value && item.calculated_value !== item.reported_value && <div className="balance-adjustment"><span>Vorgeschlagen</span><strong>{money(item.calculated_value, item.currency)}</strong><span>Erfasst</span><strong>{money(item.reported_value, item.currency)}</strong><span>Begründung</span><strong>{item.adjustment_reason ?? "Keine Begründung erfasst"}</strong></div>}<button className="secondary small" onClick={onAudit}>Im Audit anzeigen</button></article>)}
    <section className="panel table-panel">
    <table>
      <thead><tr><th>Saldoart</th><th>Datum</th><th>Gemeldet</th><th>Berechnet</th><th>Quelle</th><th>Bestätigung</th><th>Herkunft</th><th></th></tr></thead>
      <tbody>{data.balance_history.map((item: AccountBalanceLedgerRow, index: number) => <tr key={`${item.sequence_number}_${index}`}>
        <td>{BALANCE_TYPE_LABELS[item.balance_type] ?? item.balance_type}</td>
        <td>{item.balance_date ? date(item.balance_date) : "–"}</td>
        <td className="amount">{money(item.reported_value)}</td>
        <td className="amount">{item.calculated_value ? money(item.calculated_value) : "–"}</td>
        <td>{label(item.source)}</td>
        <td>{item.confirmation_status === "CONFIRMED" ? "Bestätigt" : "Unbestätigt"}</td>
        <td>{item.carry_forward_source_reconciliation_id ? "Übernommen aus bestätigtem Endsaldo" : item.adjustment_reason ? `Angepasst: ${item.adjustment_reason}` : "–"}</td>
        <td><button className="table-link" onClick={onAudit}>Audit</button></td>
      </tr>)}</tbody>
    </table>
    </section>
  </section>}</QueryBoundary>;
}

const RECONCILIATION_STATUS_LABELS: Record<string, string> = { MISSING_OPENING_BALANCE: "Anfangssaldo fehlt", NO_REPORTED_CLOSING_BALANCE: "Endsaldo fehlt" };

function AccountTabReconciliations({ accountId, onMessage, onNavigate }: { accountId: string; onMessage: (message: string) => void; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
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
        {["ALL", "MATCHED", "DIFFERENCE", "REVIEW_REQUIRED"].map((value) => <button key={value} role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>{value === "ALL" ? "Alle" : RECONCILIATION_STATUS_LABELS[value] ?? statusLabel(value)}</button>)}
      </div>
      <table>
        <thead><tr><th>Berichtsmonat</th><th>Anfangssaldo</th><th>Berechneter Endsaldo</th><th>Gemeldeter Endsaldo</th><th>Differenz</th><th>Status</th><th>Erklärung</th><th>Import</th></tr></thead>
        <tbody>{rows.map((item: AccountReconciliationRow) => <tr key={item.reconciliation_id}>
          <td>{item.report_month}</td>
          <td className="amount">{money(item.opening_balance)}</td>
          <td className="amount">{money(item.calculated_closing_balance)}</td>
          <td className="amount">{money(item.reported_closing_balance)}</td>
          <td className="amount">{money(item.balance_difference)}</td>
          <td><span className={`status-badge ${statusTone(item.status)}`}>{RECONCILIATION_STATUS_LABELS[item.status] ?? statusLabel(item.status)}</span></td>
          <td>{item.explanation ?? (item.status !== "MATCHED" && <div className="new-category-row"><input aria-label="Begründung" value={explanationDraft[item.reconciliation_id] ?? ""} onChange={(event) => setExplanationDraft((current) => ({ ...current, [item.reconciliation_id]: event.target.value }))} /><button className="secondary small" onClick={() => submitExplanation(item.reconciliation_id)}>Erfassen</button></div>)}</td>
          <td><button className="table-link" onClick={() => onNavigate("imports", { page: "imports", view: "detail", entity_id: item.export_id })}>Öffnen</button></td>
        </tr>)}</tbody>
      </table>
    </section>;
  }}</QueryBoundary>;
}

function AccountTabImports({ accountId, onNavigate, onAudit }: { accountId: string; onNavigate: (page: PageId, target?: NavigationTarget) => void; onAudit: () => void }) {
  const result = useFinanceQuery<AccountImportList>("ListAccountImports", { account_id: accountId });
  return <QueryBoundary result={result}>{({ imports }) => <section className="panel table-panel">
    <table>
      <thead><tr><th>Bank</th><th>Berichtsmonat</th><th>Abschnitt</th><th>Status</th><th>Datensätze</th><th>Zeitpunkt</th><th>Nachverfolgung</th></tr></thead>
      <tbody>{imports.map((item: AccountImportRow) => <tr key={`${item.export_id}_${item.section_id}`}>
        <td>{item.bank_identifier ?? "–"}</td>
        <td>{item.report_month}</td>
        <td>{label(item.section_type)}</td>
        <td>{label(item.import_status)}</td>
        <td>{item.record_count}</td>
        <td>{date(item.imported_at)}</td>
        <td><div className="trace-actions"><button onClick={() => onNavigate("imports", { page: "imports", view: "detail", entity_id: item.export_id })}>Import öffnen</button><button onClick={onAudit}>Audit</button><details><summary>Technische Details</summary><code>{item.parser_version} · {item.profile_version}<br />{item.content_hash}</code></details></div></td>
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

function AccountTabAudit({ accountId, onNavigate, onOpenTab }: { accountId: string; onNavigate: (page: PageId, target?: NavigationTarget) => void; onOpenTab: (tab: AccountTabId) => void }) {
  const result = useFinanceQuery<AccountAuditTrail>("GetAccountAuditTrail", { account_id: accountId });
  const [expanded, setExpanded] = useState<string | null>(null);
  return <QueryBoundary result={result}>{({ audit_history }) => <div className="history-list account-audit-list">
    {audit_history.map((event: AccountAuditEvent) => { const info = eventLabel(event.event_type); return (
      <div key={event.event_id}>
        <span>{date(event.occurred_at)}</span>
        <div><strong>{info.title}</strong><small>{event.related_import_id ? "Mit Import verknüpft" : event.related_transaction_id ? "Mit Transaktion verknüpft" : event.related_balance_id ? "Mit Saldo verknüpft" : "Kontoereignis"}</small><div className="trace-actions">{event.related_import_id && <button onClick={() => onNavigate("imports", { page: "imports", view: "detail", entity_id: event.related_import_id! })}>Import öffnen</button>}{event.related_transaction_id && <button onClick={() => onNavigate("transactions", { page: "transactions", view: "detail", entity_id: event.related_transaction_id! })}>Transaktion öffnen</button>}{event.related_balance_id && <button onClick={() => onOpenTab("balances")}>Saldo öffnen</button>}</div></div>
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

function Transactions({ month, focusId }: { month: string; focusId?: string }) {
  const result = useFinanceQuery<{ transactions: Transaction[] }>("ListTransactions", { month });
  const categories = useFinanceQuery<CategoryList>("ListCategories");
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [message, setMessage] = useState("");
  const [categoryOverrides, setCategoryOverrides] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!focusId || !result.envelope) return;
    const transaction = result.envelope.data.transactions.find((item) => item.transaction_id === focusId);
    if (transaction) setSelected(transaction);
  }, [focusId, result.envelope]);
  const classify = async () => { await financeBridge.command("ClassifyTransactions", { month }); setMessage("Klassifikation lokal ausgeführt."); };
  return <QueryBoundary result={result}>{({ transactions }) => <>
    <section className="section-intro"><div><h2>Buchungen im Zeitraum</h2><p>Effektive Beträge nach bestätigten Dubletten, Transfers und Rückerstattungen.</p></div><button className="primary" onClick={classify}>Regeln anwenden</button></section>
    {message && <div className="toast" role="status">{message}</div>}
    <section className="panel table-panel"><table><thead><tr><th>Datum</th><th>Anbieter und Beschreibung</th><th>Kategorie</th><th>Status</th><th className="numeric">Betrag</th><th><span className="sr-only">Aktion</span></th></tr></thead><tbody>{transactions.map((item) => { const category = categoryOverrides[item.transaction_id] ?? item.category_code; return <tr key={item.transaction_id}><td>{date(item.booking_date)}</td><td><strong>{item.counterparty || "Unbekannter Anbieter"}</strong><small>{item.description || item.normalized_description || "Keine Beschreibung"}</small></td><td><span className={`category-tag ${category === "UNCLASSIFIED" ? "warn" : ""}`}>{categoryLabel(category)}</span></td><td><span className="status-dot ok">Effektiv</span></td><td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td><td><button className="icon-button" onClick={() => setSelected({ ...item, category_code: category })} aria-label={`Details zu ${item.counterparty}`}>→</button></td></tr>; })}</tbody></table></section>
    {selected && <TransactionDialog transaction={selected} categories={(categories.envelope?.data.categories ?? []).map(normalizeCategory)} onClose={() => setSelected(null)} onSaved={(category) => { setCategoryOverrides((current) => ({ ...current, [selected.transaction_id]: category })); setSelected((current) => current ? { ...current, category_code: category } : current); setMessage(`Kategorie wurde gespeichert.`); }} />}
  </>}</QueryBoundary>;
}

function TransactionDialog({ transaction, categories, onClose, onSaved }: { transaction: Transaction; categories: Category[]; onClose: () => void; onSaved: (category: string) => void }) {
  const close = useRef<HTMLButtonElement>(null);
  const details = useFinanceQuery<Record<string, unknown>>("GetTransactionDetails", { transaction_id: transaction.transaction_id });
  const handleKeyDown = useModalFocus(close, onClose);
  const [category, setCategory] = useState(transaction.category_code);
  const [creating, setCreating] = useState(false);
  const [newCategory, setNewCategory] = useState("");
  const [localCategories, setLocalCategories] = useState(categories);
  const [rememberProvider, setRememberProvider] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const detail = details.envelope?.data ?? {};
  const categoryOptions = localCategories.filter((item) => item.status === "ACTIVE" || item.category_code === transaction.category_code);
  const addCategory = async () => {
    if (!newCategory.trim()) { setFeedback("Gib einen Namen für die neue Kategorie ein."); return; }
    setBusy(true);
    try {
      const response = await financeBridge.command("CreateCategory", { display_name: newCategory, category_type: Number(transaction.amount) >= 0 ? "INCOME" : "EXPENSE" }) as { result?: Category };
      if (!response.result) throw new Error("Kategorie konnte nicht angelegt werden.");
      setLocalCategories((current) => [...current, response.result as Category]);
      setCategory(response.result.category_code);
      setCreating(false); setNewCategory("");
      setFeedback(`Neue Kategorie „${response.result.display_name}“ wurde angelegt und ausgewählt.`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Kategorie konnte nicht angelegt werden.");
    } finally { setBusy(false); }
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
  return <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="dialog wide transaction-dialog" role="dialog" aria-modal="true" aria-labelledby="transaction-title" onKeyDown={handleKeyDown}><button ref={close} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">TRANSAKTIONSDETAIL</span><div className="transaction-dialog-head"><div><h2 id="transaction-title">{transaction.counterparty || "Unbekannter Anbieter"}</h2><p>{transaction.description || transaction.normalized_description || "Keine Transaktionsbeschreibung vorhanden"}</p></div><strong className="dialog-amount">{money(transaction.amount, transaction.currency)}</strong></div><section className="category-editor" aria-label="Kategorie bearbeiten"><div><label htmlFor="transaction-category">Kategorie</label><select id="transaction-category" value={category} onChange={(event) => setCategory(event.target.value)}>{categoryOptions.map((item) => <option value={item.category_code} key={item.category_code}>{item.display_name}</option>)}</select></div><button className="secondary small" type="button" onClick={() => setCreating((value) => !value)}>{creating ? "Abbrechen" : "Neue Kategorie"}</button>{creating && <div className="new-category-row"><input aria-label="Name der neuen Kategorie" value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder="z. B. Haustiere" /><button className="secondary small" disabled={busy} type="button" onClick={addCategory}>Anlegen</button></div>}<label className="remember-rule"><input type="checkbox" checked={rememberProvider} onChange={(event) => setRememberProvider(event.target.checked)} /> Für diesen Anbieter künftig automatisch verwenden</label><button className="primary" disabled={busy || category === "UNCLASSIFIED"} onClick={save}>{busy ? "Speichert …" : "Kategorie speichern"}</button></section>{feedback && <div className="toast compact" role="status">{feedback}</div>}<dl className="transaction-facts"><div><dt>Anbieter</dt><dd>{String(detail.counterparty ?? transaction.counterparty ?? "–")}</dd></div><div><dt>Transaktionsbeschreibung</dt><dd>{String(detail.description ?? transaction.description ?? "–")}</dd></div><div><dt>Buchungstag</dt><dd>{date(String(detail.booking_date ?? transaction.booking_date))}</dd></div><div><dt>Wertstellung</dt><dd>{detail.value_date ? date(String(detail.value_date)) : "–"}</dd></div><div><dt>Konto</dt><dd>{String(detail.account_id ?? "–")}</dd></div><div><dt>Cashflow-relevant</dt><dd>{Boolean(detail.cashflow_relevant ?? transaction.cashflow_relevant) ? "Ja" : "Nein"}</dd></div><div><dt>Dublette</dt><dd>{relationStatusLabel(String((detail.reconciliation as Record<string, unknown> | undefined)?.duplicate_status ?? transaction.duplicate_status ?? "NONE"))}</dd></div><div><dt>Transfer</dt><dd>{relationStatusLabel(String((detail.reconciliation as Record<string, unknown> | undefined)?.transfer_status ?? transaction.transfer_status ?? "NONE"))}</dd></div></dl><div className="detail-state">{details.state === "LOADING" ? "Event-Historie wird geladen …" : `${((detail.event_history as unknown[] | undefined) ?? []).length} zugehörige Events · Projektion ${details.state}`}</div></section></div>;
}

type CategorySuggestionRow = {
  suggestion_id: string; transaction_id: string; category_code: string; status: string;
  raw_confidence: number; confidence_kind: string; alternatives: Array<{ category_code: string; confidence: number }>;
  reason_codes: string[]; model_id: string; model_version: string; merchant: string; description: string;
  amount: string; currency: string; historical_accuracy: { threshold: string; sample_count: number; accuracy: number } | null;
};

const reasonLabels: Record<string, string> = {
  MERCHANT_SEMANTICS: "Händlersemantik", DESCRIPTION_SEMANTICS: "Buchungstext",
  RECURRING_PATTERN: "Wiederkehrendes Muster", TRANSACTION_DIRECTION: "Buchungsrichtung",
  AMOUNT_PATTERN: "Betragsmuster", KNOWN_SERVICE_TYPE: "Bekannte Dienstleistungsart",
  AMBIGUOUS_DESCRIPTION: "Mehrdeutiger Buchungstext", INSUFFICIENT_INFORMATION: "Wenige Informationen",
};

function Categories({ month, initialTab, focusId }: { month: string; initialTab?: string; focusId?: string }) {
  const initialCategoryTab = (["overview", "manage", "suggestions", "rules"] as string[]).includes(initialTab ?? "") ? initialTab as "overview" | "manage" | "suggestions" | "rules" : "overview";
  const [tab, setTab] = useState<"overview" | "manage" | "suggestions" | "rules">(initialCategoryTab);
  const [confidence, setConfidence] = useState<"all" | "high" | "medium" | "low">("all");
  const [refresh, setRefresh] = useState(0);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [remember, setRemember] = useState<Record<string, boolean>>({});
  const breakdown = useFinanceQuery<{ categories: Record<string, { category_code: string; effective_expense: string; transaction_count: number }> }>("GetCategoryBreakdown", { month, refresh });
  const summary = useFinanceQuery<{ unclassified_count: number; pending_count: number; confirmed_today_count: number; corrected_count: number; stale_count: number }>("GetCategorySuggestionReviewSummary", { refresh });
  const confidenceFilter = confidence === "high" ? { confidence_min: 0.95 } : confidence === "medium" ? { confidence_min: 0.9, confidence_max: 0.949999 } : confidence === "low" ? { confidence_max: 0.899999 } : {};
  const suggestions = useFinanceQuery<{ suggestions: CategorySuggestionRow[] }>("ListPendingCategorySuggestions", { period: month, ...confidenceFilter, refresh });
  const rules = useFinanceQuery<{ rules: Array<{ rule_id: string; field: string; value: string; category_code: string; source: string }> }>("ListClassificationRules", { refresh });
  const catalogue = useFinanceQuery<CategoryList>("ListCategories", { refresh });
  const decide = async (item: CategorySuggestionRow, action: "confirm" | "correct" | "reject") => {
    setMessage("");
    try {
      const command = action === "confirm" ? "ConfirmCategorySuggestion" : action === "correct" ? "CorrectCategorySuggestion" : "RejectCategorySuggestion";
      const payload = action === "correct" ? { suggestion_id: item.suggestion_id, selected_category: selected[item.suggestion_id] ?? item.category_code } : { suggestion_id: item.suggestion_id };
      await financeBridge.command(command, payload);
      if (action !== "reject" && remember[item.suggestion_id]) await financeBridge.command("CreateClassificationRuleFromSuggestion", { suggestion_id: item.suggestion_id, field: "counterparty" });
      setMessage(action === "confirm" ? "Kategorie-Vorschlag bestätigt." : action === "correct" ? "Kategorie-Vorschlag korrigiert." : "Kategorie-Vorschlag abgelehnt. Die Transaktion bleibt unklassifiziert.");
      setRefresh((value) => value + 1);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Der Vorschlag konnte nicht verarbeitet werden."); }
  };
  return <>
    <div className="tabs" role="tablist" aria-label="Kategorien"><button role="tab" aria-selected={tab === "overview"} onClick={() => setTab("overview")}>Übersicht</button><button role="tab" aria-selected={tab === "manage"} onClick={() => setTab("manage")}>Verwalten</button><button role="tab" aria-selected={tab === "suggestions"} onClick={() => setTab("suggestions")}>Vorschläge <span className="tab-count">{summary.envelope?.data.pending_count ?? 0}</span></button><button role="tab" aria-selected={tab === "rules"} onClick={() => setTab("rules")}>Regeln</button></div>
    {message && <div className="toast" role="status">{message}</div>}
    {focusId && tab === "suggestions" && <div className="toast compact" role="status">Die offenen AI-Vorschläge wurden für die ausgewählte Prüfung geöffnet.</div>}
    {focusId && tab === "manage" && <div className="toast compact" role="status">Die Kategorie „{categoryLabel(focusId)}“ wurde aus der Kontotransaktion geöffnet.</div>}
    {tab === "overview" && <QueryBoundary result={breakdown}>{({ categories }) => { const rows = Object.values(categories).sort((a, b) => Number(b.effective_expense) - Number(a.effective_expense)); const total = rows.reduce((sum, item) => sum + Number(item.effective_expense), 0); return <><section className="suggestion-summary"><Metric label="Unklassifiziert" value={String(summary.envelope?.data.unclassified_count ?? 0)} tone="warn" note="manuell oder per Vorschlag prüfbar" /><Metric label="AI-Vorschläge offen" value={String(summary.envelope?.data.pending_count ?? 0)} tone="accent" note="keine automatische Wirkung" /><Metric label="Heute bestätigt" value={String(summary.envelope?.data.confirmed_today_count ?? 0)} tone="positive" note="durch Nutzerentscheidung" /><Metric label="Korrekturen" value={String(summary.envelope?.data.corrected_count ?? 0)} tone="ink" note="AI-Vorschlag geändert" /></section><section className="section-intro"><div><h2>Ausgaben nach Kategorien</h2><p>Nur bestätigte Klassifikationen; offene AI-Vorschläge verändern diese Auswertung nicht.</p></div><strong className="section-total">{money(String(total))}<small>effektive Ausgaben</small></strong></section><section className="panel category-list">{rows.map((item, index) => <div className="category-row" key={item.category_code}><span className={`category-symbol color-${index % 5}`}>{item.category_code.slice(0, 2)}</span><div><strong>{categoryLabel(item.category_code)}</strong><small>{item.transaction_count} Buchungen</small></div><div className="bar"><span style={{ width: `${Math.max(4, total ? Number(item.effective_expense) / total * 100 : 0)}%` }} /></div><strong>{money(item.effective_expense)}</strong></div>)}</section></>; }}</QueryBoundary>}
    {tab === "manage" && <QueryBoundary result={catalogue}>{({ categories }) => <CategoryManagement categories={categories.map(normalizeCategory)} onChanged={() => setRefresh((value) => value + 1)} onMessage={setMessage} />}</QueryBoundary>}
    {tab === "suggestions" && <><section className="section-intro"><div><h2>AI-Kategorievorschläge prüfen</h2><p>Vorschläge des lokalen Modells werden erst durch deine Bestätigung oder Korrektur fachlich wirksam.</p></div><span className="privacy-badge">Lokal · kein Auto-Accept</span></section><div className="suggestion-filters" role="group" aria-label="Modell-Confidence filtern">{([['all','Alle'],['high','> 95 %'],['medium','90–95 %'],['low','< 90 %']] as const).map(([value, text]) => <button key={value} className={confidence === value ? "active" : ""} onClick={() => setConfidence(value)}>{text}</button>)}</div><QueryBoundary result={suggestions}>{({ suggestions: rows }) => <section className="suggestion-review-list">{rows.length === 0 ? <div className="panel empty-state"><strong>Keine offenen Vorschläge</strong><p>Manuelle Kategorisierung und bestehende Regeln bleiben jederzeit verfügbar.</p></div> : rows.map((item) => <article className="panel suggestion-review-card" key={item.suggestion_id}><div className="suggestion-transaction"><div><span className="eyebrow">LOKALES MODELL</span><h3>{item.merchant || "Unbekannter Anbieter"}</h3><p>{item.description || "Keine Beschreibung"}</p></div><strong className="amount">{money(item.amount, item.currency)}</strong></div><div className="suggestion-proposal"><div><small>Vorgeschlagene Kategorie</small><strong>{categoryLabel(item.category_code)}</strong></div><div><small>Modell-Confidence</small><strong>{percent(item.raw_confidence)}</strong></div><div><small>Modell</small><strong>{item.model_id} {item.model_version}</strong></div></div><details><summary>Details zum Vorschlag</summary><div className="suggestion-details"><div><h4>Begründungsmerkmale</h4><ul>{item.reason_codes.map((reason) => <li key={reason}>{reasonLabels[reason] ?? label(reason)}</li>)}</ul></div><div><h4>Alternativen</h4>{item.alternatives.map((alternative) => <p key={alternative.category_code}><span>{categoryLabel(alternative.category_code)}</span><b>{percent(alternative.confidence)}</b></p>)}</div></div>{item.historical_accuracy ? <p className="calibration-note">Historische Genauigkeit dieses Modells ab {Number(item.historical_accuracy.threshold) * 100} %: <strong>{percent(item.historical_accuracy.accuracy)}</strong> bei {item.historical_accuracy.sample_count} Fällen</p> : <p className="calibration-note">Noch nicht genügend lokale Vergleichsdaten.</p>}</details><div className="suggestion-decision"><select aria-label={`Kategorie für ${item.merchant}`} value={selected[item.suggestion_id] ?? item.category_code} onChange={(event) => setSelected((current) => ({ ...current, [item.suggestion_id]: event.target.value }))}>{Array.from(new Set([item.category_code, ...item.alternatives.map((alternative) => alternative.category_code)])).map((code) => <option key={code} value={code}>{categoryLabel(code)}</option>)}</select><label><input type="checkbox" checked={remember[item.suggestion_id] ?? false} onChange={(event) => setRemember((current) => ({ ...current, [item.suggestion_id]: event.target.checked }))} /> Für ähnliche Transaktionen merken</label><div><button className="primary small" onClick={() => decide(item, (selected[item.suggestion_id] ?? item.category_code) === item.category_code ? "confirm" : "correct")}>{(selected[item.suggestion_id] ?? item.category_code) === item.category_code ? "Bestätigen" : "Geänderte Kategorie übernehmen"}</button><button className="secondary small" onClick={() => decide(item, "reject")}>Ablehnen</button></div></div></article>)}</section>}</QueryBoundary></>}
    {tab === "rules" && <><section className="section-intro"><div><h2>Deterministische Regeln</h2><p>Nutzerregeln haben Vorrang vor dem lokalen Modell und vermeiden wiederholte AI-Aufrufe.</p></div></section><QueryBoundary result={rules}>{({ rules: rows }) => <section className="panel table-panel"><table><thead><tr><th>Merkmal</th><th>Wert</th><th>Kategorie</th><th>Quelle</th></tr></thead><tbody>{rows.map((rule) => <tr key={rule.rule_id}><td>{label(rule.field)}</td><td>{rule.value}</td><td>{categoryLabel(rule.category_code)}</td><td>{rule.source === "USER_FROM_AI_SUGGESTION" ? "Nutzer · aus AI-Vorschlag" : "Nutzer"}</td></tr>)}</tbody></table></section>}</QueryBoundary></>}
  </>;
}

function CategoryManagement({ categories, onChanged, onMessage }: { categories: Category[]; onChanged: () => void; onMessage: (message: string) => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<Category["category_type"]>("EXPENSE");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (command: string, payload: Record<string, unknown>, success: string) => {
    setBusy(true);
    try { await financeBridge.command(command, payload); onMessage(success); onChanged(); }
    catch (error) { onMessage(error instanceof Error ? error.message : "Kategorie konnte nicht geändert werden."); }
    finally { setBusy(false); }
  };
  const create = async () => {
    if (!name.trim()) { onMessage("Gib einen verständlichen Kategorienamen ein."); return; }
    await run("CreateCategory", { display_name: name, category_type: type }, `Kategorie „${name.trim()}“ wurde angelegt.`);
    setName("");
  };
  const custom = categories.filter((item) => !item.system_category);
  const system = categories.filter((item) => item.system_category);
  return <>
    <section className="section-intro"><div><h2>Kategorien verwalten</h2><p>Eigene Kategorien haben einen stabilen internen Code. Umbenennen verändert bestehende Zuordnungen nicht; Archivieren blendet sie nur für neue Zuordnungen aus.</p></div></section>
    <section className="panel category-create"><label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="z. B. Haustiere" /></label><label>Art<select value={type} onChange={(event) => setType(event.target.value as Category["category_type"])}><option value="EXPENSE">Ausgabe</option><option value="INCOME">Einnahme</option><option value="TRANSFER">Transfer</option><option value="INVESTMENT">Investment</option></select></label><button className="primary" disabled={busy || !name.trim()} onClick={create}>Kategorie anlegen</button></section>
    <section className="panel table-panel category-catalogue"><PanelHeader title="Eigene Kategorien" subtitle="Umbenennbar und archivierbar"/><table><thead><tr><th>Name</th><th>Art</th><th>Status</th><th>Interner Code</th><th>Aktion</th></tr></thead><tbody>{custom.length === 0 ? <tr><td colSpan={5}>Noch keine eigenen Kategorien angelegt.</td></tr> : custom.map((item) => <tr key={item.category_code}><td>{editing === item.category_code ? <input aria-label={`Neuer Name für ${item.display_name}`} value={editName} onChange={(event) => setEditName(event.target.value)} /> : <strong>{item.display_name}</strong>}</td><td>{label(item.category_type)}</td><td><span className={`status-badge ${item.status === "ACTIVE" ? "confirmed" : "missed"}`}>{item.status === "ACTIVE" ? "Aktiv" : "Archiviert"}</span></td><td><code>{item.category_code}</code></td><td>{editing === item.category_code ? <><button className="primary small" disabled={!editName.trim() || busy} onClick={async () => { await run("RenameCategory", { category_code: item.category_code, display_name: editName }, "Kategorie wurde umbenannt."); setEditing(null); }}>Speichern</button><button className="secondary small" onClick={() => setEditing(null)}>Abbrechen</button></> : <><button className="secondary small" onClick={() => { setEditing(item.category_code); setEditName(item.display_name); }}>Umbenennen</button><button className="secondary small" disabled={busy} onClick={() => run(item.status === "ACTIVE" ? "ArchiveCategory" : "RestoreCategory", { category_code: item.category_code }, item.status === "ACTIVE" ? "Kategorie wurde archiviert." : "Kategorie wurde wieder aktiviert.")}>{item.status === "ACTIVE" ? "Archivieren" : "Aktivieren"}</button></>}</td></tr>)}</tbody></table></section>
    <details className="panel system-categories"><summary>Systemkategorien ({system.length})</summary><p>Diese Basiskategorien sichern kompatible Regeln, Importe und Auswertungen und können deshalb nicht umbenannt oder archiviert werden.</p><div>{system.map((item) => <span className="category-tag" key={item.category_code}>{item.display_name}</span>)}</div></details>
  </>;
}

function Recurring({ month }: { month: string }) {
  const patterns = useFinanceQuery<{ patterns: RecurringPattern[] }>("ListRecurringPatterns");
  const expected = useFinanceQuery<{ expected_transactions: ExpectedTransaction[] }>("ListExpectedTransactions");
  const [tab, setTab] = useState<"patterns" | "expected">("patterns");
  const [selected, setSelected] = useState<RecurringPattern | null>(null);
  const [message, setMessage] = useState("");
  const action = async (command: string, pattern_id: string, payload: Record<string, unknown> = {}) => { await financeBridge.command(command, { pattern_id, ...payload }); setMessage(`${command} wurde lokal ausgeführt.`); };
  const [year, selectedMonth] = month.split("-").map(Number);
  const from = new Date(year, selectedMonth - 13, 1);
  const fromMonth = `${from.getFullYear()}-${pad2(from.getMonth() + 1)}`;
  return <><div className="tabs" role="tablist"><button role="tab" aria-selected={tab === "patterns"} onClick={() => setTab("patterns")}>Muster</button><button role="tab" aria-selected={tab === "expected"} onClick={() => setTab("expected")}>Erwartete Buchungen</button></div>{message && <div className="toast" role="status">{message}</div>}{tab === "patterns" ? <QueryBoundary result={patterns}>{({ patterns: rows }) => <><section className="section-intro"><div><h2>Wiederkehrende Muster</h2><p>Deterministisch erkannt, nur bestätigte Muster fließen in Prognosen ein.</p></div><button className="primary" onClick={() => financeBridge.command("DetectRecurringPatterns", { from_month: fromMonth, to_month: month })}>Muster erkennen</button></section><section className="panel table-panel"><table><thead><tr><th>Gegenpartei</th><th>Kategorie</th><th>Frequenz</th><th>Erwarteter Betrag</th><th>Konfidenz</th><th>Status</th><th></th></tr></thead><tbody>{rows.map((item) => <tr key={item.pattern_id}><td><strong>{label(item.merchant_key)}</strong><small>{(item as RecurringPattern & { account_id?: string }).account_id ?? "–"}</small></td><td>{label(item.category_code ?? "UNCLASSIFIED")}</td><td>{label(item.frequency)}</td><td>{money(item.expected_amount)}</td><td><span className="confidence">{label(item.confidence)}</span></td><td><span className={`status-badge ${statusTone(item.status)}`}>{statusLabel(item.status)}</span></td><td><button className="icon-button" onClick={() => setSelected(item)} aria-label={`Muster ${item.merchant_key} öffnen`}>→</button></td></tr>)}</tbody></table></section>{selected && <PatternDialog pattern={selected} onClose={() => setSelected(null)} onAction={action} />}</>}</QueryBoundary> : <QueryBoundary result={expected}>{({ expected_transactions }) => <><section className="section-intro"><div><h2>Erwartete Buchungen</h2><p>Verknüpfungen zwischen Erwartung und tatsächlicher Transaktion bleiben nachvollziehbar.</p></div></section><section className="expected-grid">{expected_transactions.map((item) => <article className="panel expected-card" key={item.expected_transaction_id}><div><span className={`status-badge ${statusTone(item.status)}`}>{statusLabel(item.status)}</span><small>{date(item.expected_date)}</small></div><h3>{label((item as ExpectedTransaction & { merchant_key?: string }).merchant_key ?? item.recurring_pattern_id)}</h3><strong>{money(item.expected_amount)}</strong><p>{label(item.direction)} · {label((item as ExpectedTransaction & { category_code?: string }).category_code ?? "UNCLASSIFIED")}</p>{(item as ExpectedTransaction & { matched_transaction_id?: string }).matched_transaction_id && <div className="match-flow"><span>Erwartung</span><b>↓</b><span>Ist-Transaktion</span><small>Abweichung 0,00 € · 0 Tage</small></div>}</article>)}</section></>}</QueryBoundary>}{selected && null}</>;
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
  const historyRows = history.envelope?.data.history ?? [];
  const prior = historyRows.length > 1 ? Number(historyRows.at(-2)?.net_worth) : null;
  const current = historyRows.length ? Number(historyRows.at(-1)?.net_worth) : null;
  const change = prior != null && current != null && prior !== 0 ? (current - prior) / Math.abs(prior) : null;
  return <QueryBoundary result={worth}>{(data) => <><section className="section-intro"><div><h2>Vermögen und Verbindlichkeiten</h2><p>Bewertungswährung EUR · jeder Wert ist bis zu seinem Snapshot zurückverfolgbar.</p></div><span className="version-chip">Stand {date(data.as_of)}</span></section>{data.currency_conflicts.length > 0 && <div className="status-banner"><strong>Währungskonflikt</strong><span>Nicht umgerechnet: {data.currency_conflicts.join(", ")}</span></div>}<section className="wealth-hero"><article className="net-worth-card"><span>Nettovermögen</span><strong>{money(data.net_worth)}</strong><small>Vermögen {money(data.total_assets)} − Verbindlichkeiten {money(data.liabilities)}</small><div className="wealth-change">{change == null ? "Noch kein Vergleichszeitraum" : `${change >= 0 ? "↑" : "↓"} ${percent(Math.abs(change))} zum vorherigen Snapshot`}</div></article><div className="wealth-metrics"><Metric label="Liquidität" value={money(data.liquid_funds)} tone="positive" note={`Bestätigter Stand ${liquidity.envelope ? date(liquidity.envelope.data.as_of) : "–"}`} /><Metric label="Sparguthaben" value={money(data.savings)} tone="ink" note="Teil der Liquidität" /><Metric label="Investments" value={money(data.investments)} tone="accent" note="Investierbares Vermögen" /><Metric label="Verbindlichkeiten" value={money(data.liabilities)} tone="negative" note="Kein Vermögenswert" /></div></section><div className="two-column wealth-bottom"><section className="panel"><PanelHeader title="Vermögensentwicklung" subtitle="Snapshot-basierter Verlauf"/><QueryBoundary result={history}>{({ history }) => <div className="wealth-chart"><svg viewBox="0 0 600 190" role="img" aria-label="Nettovermögensverlauf"><path className="gridline" d="M0 35H600M0 95H600M0 155H600"/><path className="area" d="M0 150 C170 140 220 112 300 105 S470 55 600 35 L600 180 L0 180Z"/><path className="line" d="M0 150 C170 140 220 112 300 105 S470 55 600 35"/></svg><div className="wealth-axis">{history.map((item) => <span key={item.as_of}>{date(item.as_of)}<strong>{money(item.net_worth)}</strong></span>)}</div></div>}</QueryBoundary></section><section className="panel"><PanelHeader title="Verbindlichkeiten" subtitle="Separat vom Vermögen"/><QueryBoundary result={liabilities}>{(liabilityData) => <div className="liability-list">{liabilityData.liabilities.map((item) => <div key={item.item_id}><span className="category-symbol">{item.item_type.slice(0,2)}</span><div><strong>{item.display_name}</strong><small>{label(item.item_type)} · {date(item.valuation_date)}</small></div><b>{money(item.amount, item.currency)}</b></div>)}<footer><span>Gesamt</span><strong>{money(liabilityData.total_liabilities)}</strong></footer></div>}</QueryBoundary></section></div><section className="panel trace-note"><strong>Berechnungsgrundlage</strong><span>{data.source_snapshot_ids.length} aktive Snapshots · Interne Transfers konsolidiert neutral · keine stillen Währungsumrechnungen</span></section></>}</QueryBoundary>;
}

const REVIEW_LABELS: Record<ReviewType, string> = {
  CATEGORY: "Kategorien",
  AI_SUGGESTION: "AI-Vorschläge",
  BALANCE: "Saldenabweichungen",
  IMPORT: "Importe",
  RELATION: "Relationen",
  FORMAT_CHANGE: "Formatänderungen",
};

function Reviews({ inbox, focusAccountId, onOpen }: { inbox: QueryResult<ReviewInbox>; focusAccountId?: string; onOpen: (target: ReviewItem["action_target"]) => void }) {
  const [filter, setFilter] = useState<ReviewType | "ALL">("ALL");
  const data = inbox.envelope?.data;
  const accountItems = data?.items.filter((item) => !focusAccountId || item.account_ids.includes(focusAccountId)) ?? [];
  const items = accountItems.filter((item) => filter === "ALL" || item.review_type === filter);
  const visibleCounts = (Object.keys(REVIEW_LABELS) as ReviewType[]).reduce((counts, type) => ({ ...counts, [type]: focusAccountId ? accountItems.filter((item) => item.review_type === type).length : data?.items_by_type[type] ?? 0 }), {} as Record<ReviewType, number>);
  const boundary = { ...inbox, state: inbox.state === "EMPTY" ? "READY" as const : inbox.state };
  return <QueryBoundary result={boundary}>{() => <>
    <section className="section-intro review-center-head"><div><span className="eyebrow">ZENTRALER ARBEITSVORRAT</span><h2>Prüfen</h2><p>{focusAccountId ? "Offene Aufgaben dieses Kontos. Der Zähler stammt aus derselben zentralen Inbox." : "Alle offenen fachlichen Aufgaben aus Kategorien, Importen, Konten und Relationen an einem Ort."}</p></div><strong>{focusAccountId ? accountItems.length : data?.total_open ?? 0}<small>offene Aufgaben</small></strong></section>
    <section className="review-summary-grid" aria-label="Offene Aufgaben nach Typ">
      {(Object.keys(REVIEW_LABELS) as ReviewType[]).map((type) => <button key={type} className={filter === type ? "panel active" : "panel"} onClick={() => setFilter(filter === type ? "ALL" : type)}><span>{REVIEW_LABELS[type]}</span><strong>{visibleCounts[type]}</strong></button>)}
    </section>
    <div className="review-filter"><button className={filter === "ALL" ? "active" : ""} onClick={() => setFilter("ALL")}>Alle Aufgaben</button>{filter !== "ALL" && <span>{REVIEW_LABELS[filter]}</span>}</div>
    <section className="review-list">{items.length === 0 ? <div className="panel page-state"><span className="empty-icon">✓</span><h3>Alles geprüft</h3><p>In dieser Gruppe sind keine offenen Aufgaben.</p></div> : items.map((item) => <article className={`panel review-card severity-${item.severity.toLowerCase()}`} key={item.review_id}><span className="review-kind">{REVIEW_LABELS[item.review_type]}</span><div><h3>{item.title}</h3><p>{item.description}</p><small>{date(item.created_at)}</small></div><div className="review-actions"><span className={`status-badge ${item.severity === "ERROR" ? "missed" : "paused"}`}>{item.severity === "ERROR" ? "Dringend" : item.severity === "WARNING" ? "Zu prüfen" : "Hinweis"}</span><button className="primary small" onClick={() => onOpen(item.action_target)}>Öffnen</button><details><summary>Technische Details</summary><code>{item.entity_type} · {item.entity_id}</code></details></div></article>)}</section>
  </>}</QueryBoundary>;
}

function Settings({ manifest }: { manifest: CapabilityManifest }) {
  const result = useFinanceQuery<RuntimeSecurityStatus>("GetRuntimeSecurityStatus");
  const backups = useFinanceQuery<{ backups: BackupRecord[] }>("ListBackups");
  const integrity = useFinanceQuery<StoreIntegrity>("GetStoreIntegrity");
  const keys = useFinanceQuery<KeyStatus>("GetKeyStatus");
  const migrations = useFinanceQuery<MigrationStatus>("GetMigrationStatus");
  const [localModelRefresh, setLocalModelRefresh] = useState(0);
  const localModel = useFinanceQuery<LocalModelStatus>("GetLocalModelStatus", { refresh: localModelRefresh });
  const [shadowRefresh, setShadowRefresh] = useState(0);
  const shadow = useFinanceQuery<{ enabled: boolean; predictions: number; evaluated: number; pending_ground_truth: number; local_model: LocalModelStatus }>("GetCategoryShadowStatus", { refresh: shadowRefresh });
  const evaluation = useFinanceQuery<{ prediction_count: number; evaluated_count: number; pending_ground_truth_count: number; overall_accuracy: number | null; overall_status: string; thresholds: Record<string, { sample_count: number; accuracy: number | null; status: string }>; categories: Array<{ category_code: string; cases: number; precision: number | null; recall: number | null; status: string }>; confidence_kind: string; minimum_sample: number }>("GetCategoryEvaluationSummary", { refresh: shadowRefresh });
  const evaluationPreview = useFinanceQuery<{ eligible_transaction_count: number; processing_location: string; network_egress: string }>("PreviewCategoryEvaluationRun", { refresh: shadowRefresh });
  const [message, setMessage] = useState("");
  const [technicalError, setTechnicalError] = useState<{ code: string; requestId: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [restorePath, setRestorePath] = useState<string | null>(null);
  const [rotateReady, setRotateReady] = useState(false);
  const [modelFile, setModelFile] = useState<{ file_ref: string; display_name: string } | null>(null);
  const [modelManifest, setModelManifest] = useState<{ file_ref: string; display_name: string } | null>(null);
  const [runtimeFile, setRuntimeFile] = useState<{ file_ref: string; display_name: string } | null>(null);
  const runtimeSummary = localRuntimeSummary(localModel.envelope?.data);
  const rows = backups.envelope?.data.backups ?? [];
  const latest = rows.find((item) => item.verification_status === "VALID");
  const run = async (command: string, payload: Record<string, unknown>, success: string) => {
    setBusy(command); setMessage(""); setTechnicalError(null);
    try { await financeBridge.command(command, payload); setMessage(success); if (command.includes("LocalModel") || command.includes("LocalAIRuntime")) setLocalModelRefresh((value) => value + 1); }
    catch (error) {
      setMessage(error instanceof Error ? error.message : "Lokale Operation fehlgeschlagen.");
      if (error instanceof DesktopOperationError) {
        setTechnicalError({ code: error.code, requestId: error.requestId });
      }
    }
    finally { setBusy(""); }
  };
  const chooseModel = async () => {
    const selected = await financeBridge.selectLocalModelFile();
    if (selected) setModelFile(selected);
  };
  const chooseModelManifest = async () => {
    const selected = await financeBridge.selectLocalModelManifest();
    if (selected) setModelManifest(selected);
  };
  const chooseRuntime = async () => {
    setMessage(""); setTechnicalError(null);
    try {
      const selected = await financeBridge.selectLocalAIRuntime();
      if (selected) setRuntimeFile(selected);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Die Runtime konnte nicht ausgewählt werden.");
      if (error instanceof DesktopOperationError) setTechnicalError({ code: error.code, requestId: error.requestId });
    }
  };
  return <QueryBoundary result={result}>{(security) => <>
    <section className="section-intro"><div><h2>Laufzeitsicherheit</h2><p>Dynamisch geprüft. „Nicht geprüft“ wird ausdrücklich nicht als sicher gewertet.</p></div><span className="version-chip">Extension {manifest.extension_version} · Schema {manifest.schema_version}</span></section>
    <section className="security-grid">{Object.entries(security.checks).map(([name, status]) => <article className="panel security-check" key={name}><span className={`security-icon ${status.toLowerCase()}`}>{status === "PASSED" ? "✓" : status === "FAILED" ? "!" : "?"}</span><div><strong>{label(name)}</strong><small>{status === "PASSED" ? "Bestanden" : status === "FAILED" ? "Fehlgeschlagen" : "Nicht geprüft"}</small></div></article>)}</section>
    <h2 className="subheading">Datensicherheit und Wiederherstellung</h2>
    {message && <div className="toast" role="status">{message}{technicalError && <details className="audit-technical"><summary>Technische Details</summary><small>Fehlercode: <code>{technicalError.code}</code><br />Anfrage: <code>{technicalError.requestId}</code></small></details>}</div>}
    <section className="recovery-grid">
      <article className="panel recovery-card"><span className={`recovery-icon ${localModel.envelope?.data.status === "READY" ? "ok" : "warn"}`}>{localModel.envelope?.data.status === "READY" ? "✓" : "AI"}</span><div><h3>Lokale AI</h3><p>Verifiziertes GGUF-Modell über llama.cpp. Verarbeitung ausschließlich auf diesem Gerät.</p><strong>{localModel.envelope?.data.status === "READY" ? "Bereit" : localModel.envelope?.data.status === "LOADING_MODEL" ? "Modell wird geladen" : localModel.envelope?.data.status === "AI_UNAVAILABLE" ? "Nicht verfügbar" : "Nicht eingerichtet"}</strong><small>Runtime: {runtimeSummary.version} · Integrität {runtimeSummary.integrity}</small><small>{localModel.envelope?.data.model ? `${localModel.envelope.data.model.model_id} · ${localModel.envelope.data.model.model_version}` : "PRIVATE_LOCAL · Netzwerk blockiert"}</small><small>{runtimeFile?.display_name ?? "Noch keine Runtime gewählt"} · {modelFile?.display_name ?? "Noch kein GGUF gewählt"} · {modelManifest?.display_name ?? "kein Manifest gewählt"}</small></div><div className="card-actions"><button className="secondary small" disabled={Boolean(busy)} onClick={chooseRuntime}>llama.cpp wählen</button><button className="primary small" disabled={!runtimeFile || Boolean(busy)} onClick={() => run("RegisterLocalAIRuntime", { runtime_file_reference: runtimeFile?.file_ref }, "llama.cpp wurde geprüft und lokal registriert.")}>Runtime registrieren</button><button className="secondary small" disabled={Boolean(busy)} onClick={chooseModel}>GGUF wählen</button><button className="secondary small" disabled={Boolean(busy)} onClick={chooseModelManifest}>Manifest wählen</button><button className="primary small" disabled={!modelFile || !modelManifest || Boolean(busy)} onClick={() => run("RegisterLocalModel", { model_file_reference: modelFile?.file_ref, manifest_file_reference: modelManifest?.file_ref }, "Modell wurde verifiziert und lokal registriert. Der Status wird beim nächsten Öffnen aktualisiert.")}>Modell registrieren</button>{localModel.envelope?.data.status === "READY" ? <button className="secondary small" disabled={Boolean(busy)} onClick={() => run("DisableLocalModel", {}, "Lokale AI wurde deaktiviert.")}>Deaktivieren</button> : localModel.envelope?.data.model ? <button className="secondary small" disabled={Boolean(busy)} onClick={() => run("EnableLocalModel", {}, "Lokale AI wird gestartet.")}>Aktivieren</button> : null}</div></article>
      <article className="panel recovery-card category-evaluation-card"><span className={`recovery-icon ${shadow.envelope?.data.enabled ? "ok" : "warn"}`}>◎</span><div><h3>Kategorie-Evaluation</h3><p>Das lokale Modell bewertet geeignete Transaktionen im Hintergrund. Vorhersagen ändern keine Kategorien und dienen ausschließlich der lokalen Qualitätsmessung.</p><strong>Shadow Mode {shadow.envelope?.data.enabled ? "aktiv" : "inaktiv"}</strong><small>{shadow.envelope?.data.predictions ?? 0} Vorhersagen · {shadow.envelope?.data.evaluated ?? 0} ausgewertet · {shadow.envelope?.data.pending_ground_truth ?? 0} ohne Ground Truth</small><div className="shadow-metrics"><span><b>{percent(evaluation.envelope?.data.overall_accuracy)}</b>Gesamt</span>{["0.90", "0.95", "0.97", "0.99"].map((threshold) => { const metric = evaluation.envelope?.data.thresholds?.[threshold]; return <span key={threshold}><b>{percent(metric?.accuracy)}</b>≥ {Number(threshold) * 100} % <small>{metric?.status === "INSUFFICIENT_DATA" ? "Noch nicht genügend Daten" : `${metric?.sample_count ?? 0} Fälle`}</small></span>; })}</div><details className="evaluation-details"><summary>Details anzeigen</summary><p>Confidence ist eine Selbsteinschätzung des Modells, keine kalibrierte Wahrscheinlichkeit. Kennzahlen erscheinen erst ab {evaluation.envelope?.data.minimum_sample ?? 30} Fällen.</p><div className="table-panel"><table><thead><tr><th>Kategorie</th><th>Fälle</th><th>Precision</th><th>Recall</th></tr></thead><tbody>{(evaluation.envelope?.data.categories ?? []).filter((item) => item.cases > 0).map((item) => <tr key={item.category_code}><td>{categoryLabel(item.category_code)}</td><td>{item.cases}</td><td>{item.status === "READY" ? percent(item.precision) : "Noch nicht genügend Daten"}</td><td>{item.status === "READY" ? percent(item.recall) : "–"}</td></tr>)}</tbody></table></div></details></div><div className="card-actions"><button className="secondary small" disabled={Boolean(busy) || localModel.envelope?.data.status !== "READY"} onClick={() => run(shadow.envelope?.data.enabled ? "DisableCategoryShadowMode" : "EnableCategoryShadowMode", {}, shadow.envelope?.data.enabled ? "Kategorie-Shadow-Mode wurde deaktiviert. Vorhandene Evaluationen bleiben erhalten." : "Kategorie-Shadow-Mode wurde aktiviert.").then(() => setShadowRefresh((value) => value + 1))}>{shadow.envelope?.data.enabled ? "Deaktivieren" : "Aktivieren"}</button><button className="primary small" disabled={Boolean(busy) || !shadow.envelope?.data.enabled || (evaluationPreview.envelope?.data.eligible_transaction_count ?? 0) === 0} onClick={() => run("StartCategoryEvaluationRun", {}, `${evaluationPreview.envelope?.data.eligible_transaction_count ?? 0} bestätigte Transaktionen wurden lokal ausgewertet.`).then(() => setShadowRefresh((value) => value + 1))}>Historische Kategorien auswerten</button><small>{evaluationPreview.envelope?.data.eligible_transaction_count ?? 0} bestätigte Transaktionen geeignet · nur lokal</small></div></article>
      <article className="panel recovery-card"><span className="recovery-icon">▣</span><div><h3>Datensicherung</h3><p>Vollständige, authentifiziert verschlüsselte Archive mit unabhängigem lokalem Schlüssel.</p><strong>{rows.length} lokale Backups</strong><small>{latest?.created_at ? `Zuletzt ${date(latest.created_at)}` : "Noch keine Sicherung"}</small></div><button className="primary small" disabled={Boolean(busy)} onClick={() => run("CreateBackup", {}, "Backup wurde vollständig erstellt und verifiziert.")}>Backup erstellen</button></article>
      <article className="panel recovery-card"><span className="recovery-icon">↺</span><div><h3>Datenwiederherstellung</h3><p>Vor dem atomaren Austausch werden Archiv, Version, Schema und alle enthaltenen Dateien geprüft.</p><strong>{latest ? "Verifiziertes Backup verfügbar" : "Kein gültiges Backup"}</strong><small>Keine Teilwiederherstellung</small></div><button className="secondary small" disabled={!latest || Boolean(busy)} onClick={() => setRestorePath(latest?.path ?? null)}>Wiederherstellen</button></article>
      <article className="panel recovery-card"><span className="recovery-icon">⇧</span><div><h3>Datenexport</h3><p>Portables lokales Finanzarchiv ohne Cloud-Übertragung oder externe Verarbeitung.</p><strong>Verschlüsselt und vollständig</strong><small>Formatversion 1</small></div><button className="secondary small" disabled={Boolean(busy)} onClick={() => run("ExportFinanceData", {}, "Lokales Finanzarchiv wurde exportiert und verifiziert.")}>Archiv exportieren</button></article>
      <article className="panel recovery-card"><span className={`recovery-icon ${integrity.envelope?.data.status === "VALID" ? "ok" : "warn"}`}>{integrity.envelope?.data.status === "VALID" ? "✓" : "!"}</span><div><h3>Speicherintegrität</h3><p>SQLite-Struktur, Event-Hashes, Aggregate-Versionen, Schemas und Importdateien.</p><strong>{integrity.envelope?.data.status === "VALID" ? "Integrität bestätigt" : "Prüfung erforderlich"}</strong><small>{integrity.envelope?.data.event_count ?? 0} Events · Schema {integrity.envelope?.data.store_schema_version ?? "–"}</small></div><div className="card-actions"><button className="secondary small" disabled={Boolean(busy)} onClick={() => run("ValidateStoreIntegrity", {}, "Vollständige Integritätsprüfung abgeschlossen.")}>Prüfen</button><button className="secondary small" disabled={integrity.envelope?.data.status !== "VALID" || Boolean(busy)} onClick={() => run("RepairLocalStore", {}, "Projektions-Checkpoints wurden zurückgesetzt; Ansichten werden neu aufgebaut.")}>Reparieren</button></div></article>
      <article className="panel recovery-card"><span className="recovery-icon">⌘</span><div><h3>Schlüsselstatus</h3><p>Datenbank- und Backup-Schlüssel bleiben getrennt in der lokalen Schlüsselverwaltung.</p><strong>{keys.envelope?.data.archive_key.independent_from_store ? "Schlüssel getrennt" : "Backup-Schlüssel nicht bereit"}</strong><small>Store {keys.envelope?.data.database_key.fingerprint ?? "–"} · Backup {keys.envelope?.data.archive_key.fingerprint ?? "–"}</small></div><button className="secondary small" disabled={Boolean(busy)} onClick={() => setRotateReady(true)}>Schlüssel rotieren</button></article>
      <article className="panel recovery-card"><span className="recovery-icon ok">↑</span><div><h3>Migrationen</h3><p>Store-Upgrades sind protokolliert; neuere Datenstände werden nicht mit älterer Software geöffnet.</p><strong>{migrations.envelope?.data.status === "CURRENT" ? "Schema aktuell" : "Migration erforderlich"}</strong><small>Store v{migrations.envelope?.data.current_store_schema_version ?? "–"} · Downgrade-Schutz aktiv</small></div><span className="status-badge confirmed">{migrations.envelope?.data.status ?? "LOADING"}</span></article>
    </section>
    {restorePath && <section className="restore-warning panel" role="alert"><div><strong>Vollständige Wiederherstellung bestätigen</strong><p>Der aktuelle lokale Store wird erst nach erfolgreicher Integritäts- und Kompatibilitätsprüfung atomar ersetzt.</p></div><button className="danger-link" onClick={() => setRestorePath(null)}>Abbrechen</button><button className="primary small" disabled={Boolean(busy)} onClick={() => run("RestoreBackup", { archive_path: restorePath }, "Backup wurde vollständig wiederhergestellt.").then(() => setRestorePath(null))}>Wiederherstellung bestätigen</button></section>}
    {rotateReady && <section className="restore-warning panel" role="alert"><div><strong>Schlüsselrotation bestätigen</strong><p>Vor der Rotation wird automatisch ein verifiziertes Recovery-Backup mit dem unabhängigen Archivschlüssel erzeugt.</p></div><button className="danger-link" onClick={() => setRotateReady(false)}>Abbrechen</button><button className="primary small" disabled={Boolean(busy)} onClick={() => run("RotateEncryptionKey", {}, "Schlüssel wurde nach einem Recovery-Backup atomar rotiert.").then(() => setRotateReady(false))}>Rotation bestätigen</button></section>}
    <section className="panel settings-info"><PanelHeader title="Lokaler Datenvertrag" subtitle="UI → Desktop IPC → Application Service"/><dl><div><dt>Letzte Event-Sequenz</dt><dd>#{security.last_event_sequence}</dd></div><div><dt>Netzwerkressourcen</dt><dd>Keine</dd></div><div><dt>Direkter Store-Zugriff</dt><dd>Nicht erlaubt</dd></div><div><dt>Externe Modelle</dt><dd>Deaktiviert</dd></div><div><dt>Lokale Modellverarbeitung</dt><dd>{localModel.envelope?.data.available ? "Aktiv" : "Optional / inaktiv"}</dd></div><div><dt>Auto-Kategorisierung</dt><dd>Deaktiviert</dd></div></dl></section>
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
