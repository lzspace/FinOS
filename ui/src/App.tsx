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
  AccountOverviewStatus,
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
  FinancialTimeSeries,
  ForecastScenario,
  LiquidityOverview,
  LocalModelStatus,
  NetWorthOverview,
  KeyStatus,
  MigrationStatus,
  MonthlyWorkspaceMonth,
  MonthlyWorkspaceStatus,
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

type PageId = "overview" | "workspace" | "accounts" | "transactions" | "categories" | "recurring" | "forecast" | "wealth" | "reviews" | "imports" | "settings";
type WorkspaceTab = "tasks" | "bookings" | "categories";
type QueryResult<T> = { state: ViewState; envelope?: Envelope<T>; error?: string };
type ReviewType = "CATEGORY" | "AI_SUGGESTION" | "BALANCE" | "IMPORT" | "RELATION" | "FORMAT_CHANGE";
type ReviewItem = {
  review_id: string; review_type: ReviewType; severity: "ERROR" | "WARNING" | "INFO";
  entity_id: string; entity_type: string; title: string; description: string; created_at: string;
  account_ids: string[];
  period?: string | null;
  action_target: { page: PageId; view: string; entity_id: string };
};
type ReviewInbox = {
  total_open: number;
  items_by_type: Record<ReviewType, number>;
  severity: Record<"ERROR" | "WARNING" | "INFO", number>;
  items: ReviewItem[];
  next_cursor?: string | null;
  has_more?: boolean;
  page_size?: number;
};
type DashboardView = "CURRENT" | "WORKSPACE";
type AnalysisWindow = 3 | 6 | 12 | "ALL";
type WorkspaceMonthStatus = "COMPLETE" | "REVIEW" | "MISSING" | "OUTSIDE";
type WorkspaceMonth = {
  month: string;
  label: string;
  status: WorkspaceMonthStatus;
  reviews: ReviewItem[];
  isCurrent: boolean;
  details?: MonthlyWorkspaceMonth;
};
type CategoryBreakdown = {
  period: string;
  categories: Record<string, {
    category_code: string;
    gross_expense: string;
    refund_amount: string;
    effective_expense: string;
    transaction_count: number;
  }>;
};
type OpeningBalanceSuggestion = {
  eligible: boolean;
  status: string;
  reason_code: string;
  period_start: string;
  period_end: string;
  suggested_value?: string;
  currency?: string;
  source?: "REPORTED_PRIOR_CLOSING_BALANCE" | "CALCULATED_PRIOR_CLOSING_BALANCE";
  source_period?: string;
  source_account_id?: string;
  source_projection_sequence?: number;
  source_reconciliation_id?: string | null;
  calculation?: {
    opening_balance?: string;
    balance_relevant_movements?: string;
    calculated_closing_balance?: string;
    relevant_transaction_count?: number;
  };
};
type NavigationTarget = (ReviewItem["action_target"] & { period?: string }) | null;

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
export const effectiveAccountOverviewStatus = (
  status: AccountOverviewStatus,
  freshness: AccountOverviewRow["data_freshness"],
  hasRecordedBalance: boolean,
): AccountOverviewStatus => status === "MISSING_BALANCE" && hasRecordedBalance
  ? freshness === "STALE" ? "STALE" : "REVIEW_REQUIRED"
  : status;
const relationStatusLabel = (value: string) => value === "NONE" ? "Nicht erkannt" : label(value);
const localISODate = (value = new Date()) => `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`;
const previousISODate = (value: string) => {
  const point = new Date(`${value}T12:00:00`);
  point.setDate(point.getDate() - 1);
  return localISODate(point);
};
const normalizedDecimal = (value: string) => {
  const compact = value.trim().replaceAll(" ", "");
  return compact.includes(",") ? compact.replaceAll(".", "").replace(",", ".") : compact;
};
const OPENING_SUGGESTION_REASON: Record<string, string> = {
  MISSING_PRIOR_OPENING_BALANCE: "Im Vormonat fehlt ein bestätigter Anfangssaldo.",
  PRIOR_IMPORT_INCOMPLETE: "Der Import des Vormonats ist noch nicht vollständig abgeschlossen.",
  UNRESOLVED_DIFFERENCE: "Im Vormonat besteht noch eine ungeklärte Saldenabweichung.",
  UNRESOLVED_DUPLICATES: "Im Vormonat müssen zunächst mögliche Dubletten geprüft werden.",
  INCONSISTENT_PROJECTION_DATA: "Die Daten des Vormonats sind noch nicht vollständig konsistent.",
  CURRENCY_MISMATCH: "Die Währung des vorherigen Saldos passt nicht zum Konto.",
};

function useFinanceQuery<T>(name: string, payload: Record<string, unknown> = {}, enabled = true, refreshToken = 0): QueryResult<T> {
  const signature = JSON.stringify(payload);
  const [result, setResult] = useState<QueryResult<T>>({ state: "LOADING" });
  useEffect(() => {
    if (!enabled) return;
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
  }, [enabled, name, refreshToken, signature]);
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
const monthPeriod = (year: number, month: number): PeriodSelection => ({
  mode: "MONTH",
  start_date: `${year}-${pad2(month)}-01`,
  end_date: `${year}-${pad2(month)}-${pad2(monthEnd(year, month))}`,
  timezone: "Europe/Berlin",
  display_label: `${MONTH_NAMES[month - 1]} ${year}`,
  aggregation: "DAY",
  comparison_period: null,
});
const shiftMonthKey = (value: string, offset: number) => {
  const [year, month] = value.split("-").map(Number);
  const point = new Date(year, month - 1 + offset, 1, 12);
  return `${point.getFullYear()}-${pad2(point.getMonth() + 1)}`;
};
const nextMonth = (value: string) => shiftMonthKey(value, 1);
const monthKeysBetween = (start: string, end: string) => {
  const months: string[] = [];
  for (let value = start; value <= end && months.length < 240; value = nextMonth(value)) months.push(value);
  return months;
};
const reviewMonth = (item: ReviewItem) => {
  if (item.period) return item.period;
  const explicit = `${item.entity_id} ${item.title} ${item.description}`.match(/\b\d{4}-(?:0[1-9]|1[0-2])\b/)?.[0];
  return explicit ?? item.created_at.slice(0, 7);
};
const MAX_CUSTOM_RANGE_DAYS = 1096;

const parseLocalISODate = (value: string): Date | null => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day] = match.map(Number);
  const parsed = new Date(year, month - 1, day, 12);
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day
    ? parsed
    : null;
};

const customRangeError = (startValue: string, endValue: string): string | null => {
  const start = parseLocalISODate(startValue);
  const end = parseLocalISODate(endValue);
  if (!start || !end) return "Start- und Enddatum müssen vollständig angegeben sein.";
  const span = Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
  if (span < 1) return "Das Enddatum darf nicht vor dem Startdatum liegen.";
  if (span > MAX_CUSTOM_RANGE_DAYS) return "Der Zeitraum darf höchstens 1.096 Tage umfassen.";
  return null;
};

const rangeLabel = (start: string, end: string) => {
  const format = (value: string) => {
    const [year, month, day] = value.split("-");
    return `${day}.${month}.${year}`;
  };
  return `${format(start)} – ${format(end)}`;
};

function usePeriodSelection() {
  const today = new Date();
  const [mode, setMode] = useState<PeriodMode>("MONTH");
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [rangeStart, setRangeStart] = useState(`${today.getFullYear()}-${pad2(today.getMonth() + 1)}-01`);
  const [rangeEnd, setRangeEnd] = useState(localISODate(today));
  const [appliedRangeStart, setAppliedRangeStart] = useState(rangeStart);
  const [appliedRangeEnd, setAppliedRangeEnd] = useState(rangeEnd);
  const rangeValidationError = customRangeError(rangeStart, rangeEnd);

  const applyCustomRange = () => {
    if (rangeValidationError) return;
    setAppliedRangeStart(rangeStart);
    setAppliedRangeEnd(rangeEnd);
  };

  const replaceCustomRange = (start: string, end: string) => {
    if (customRangeError(start, end)) return;
    setRangeStart(start);
    setRangeEnd(end);
    setAppliedRangeStart(start);
    setAppliedRangeEnd(end);
  };

  const shiftCustomRange = (direction: -1 | 1) => {
    const start = parseLocalISODate(appliedRangeStart);
    const end = parseLocalISODate(appliedRangeEnd);
    if (!start || !end) return;
    const span = Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
    if (span < 1 || span > MAX_CUSTOM_RANGE_DAYS) return;
    start.setDate(start.getDate() + direction * span);
    end.setDate(end.getDate() + direction * span);
    replaceCustomRange(localISODate(start), localISODate(end));
  };

  const period: PeriodSelection = useMemo(() => {
    if (mode === "YEAR") {
      return { mode, start_date: `${year}-01-01`, end_date: `${year}-12-31`, display_label: String(year), aggregation: "MONTH", timezone: "Europe/Berlin" };
    }
    if (mode === "CUSTOM_RANGE") {
      return { mode, start_date: appliedRangeStart, end_date: appliedRangeEnd, display_label: rangeLabel(appliedRangeStart, appliedRangeEnd), aggregation: "DAY", timezone: "Europe/Berlin" };
    }
    const start = `${year}-${pad2(month)}-01`;
    const end = `${year}-${pad2(month)}-${pad2(monthEnd(year, month))}`;
    return { mode, start_date: start, end_date: end, display_label: `${MONTH_NAMES[month - 1]} ${year}`, aggregation: "DAY", timezone: "Europe/Berlin" };
  }, [mode, year, month, appliedRangeStart, appliedRangeEnd]);

  return {
    mode, setMode, year, setYear, month, setMonth, rangeStart, setRangeStart, rangeEnd, setRangeEnd,
    period, rangeValidationError, applyCustomRange, replaceCustomRange, shiftCustomRange,
    payload: { mode: period.mode, year: mode !== "CUSTOM_RANGE" ? year : undefined, month: mode === "MONTH" ? month : undefined, start_date: mode === "CUSTOM_RANGE" ? appliedRangeStart : undefined, end_date: mode === "CUSTOM_RANGE" ? appliedRangeEnd : undefined },
  };
}

type PeriodControls = ReturnType<typeof usePeriodSelection>;

function PeriodSelector({ controls }: { controls: PeriodControls }) {
  const { mode, setMode, year, setYear, month, setMonth, rangeStart, setRangeStart, rangeEnd, setRangeEnd, rangeValidationError, applyCustomRange, replaceCustomRange, shiftCustomRange } = controls;
  const updateYear = (value: string) => {
    const next = Number(value);
    if (Number.isInteger(next) && next >= 1 && next <= 9999) setYear(next);
  };
  return (
    <div className="period-selector" role="group" aria-label="Zeitraumsteuerung">
      <div className="period-mode-switch" role="tablist" aria-label="Zeitraummodus">
        {([["MONTH", "Monat"], ["YEAR", "Jahr"], ["CUSTOM_RANGE", "Zeitraum"]] as Array<[PeriodMode, string]>).map(([value, text]) => (
          <button key={value} role="tab" aria-selected={mode === value} onClick={() => setMode(value)}>{text}</button>
        ))}
      </div>
      {mode === "MONTH" && (
        <div className="period-fields">
          <button className="icon-button" aria-label="Vorheriger Monat" disabled={year === 1 && month === 1} onClick={() => (month === 1 ? (setYear(Math.max(1, year - 1)), setMonth(12)) : setMonth(month - 1))}>‹</button>
          <label className="sr-only" htmlFor="period-month-select">Monat auswählen</label>
          <select id="period-month-select" value={month} onChange={(event) => setMonth(Number(event.target.value))}>
            {MONTH_NAMES.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
          </select>
          <label className="sr-only" htmlFor="period-year-select">Jahr auswählen</label>
          <input id="period-year-select" type="number" min="1" max="9999" value={year} onChange={(event) => updateYear(event.target.value)} aria-label="Jahr auswählen" />
          <button className="icon-button" aria-label="Nächster Monat" disabled={year === 9999 && month === 12} onClick={() => (month === 12 ? (setYear(Math.min(9999, year + 1)), setMonth(1)) : setMonth(month + 1))}>›</button>
        </div>
      )}
      {mode === "YEAR" && (
        <div className="period-fields">
          <button className="icon-button" aria-label="Vorheriges Jahr" disabled={year === 1} onClick={() => setYear(Math.max(1, year - 1))}>‹</button>
          <label className="sr-only" htmlFor="period-year-only">Jahr auswählen</label>
          <input id="period-year-only" type="number" min="1" max="9999" value={year} onChange={(event) => updateYear(event.target.value)} aria-label="Jahr auswählen" />
          <button className="icon-button" aria-label="Nächstes Jahr" disabled={year === 9999} onClick={() => setYear(Math.min(9999, year + 1))}>›</button>
        </div>
      )}
      {mode === "CUSTOM_RANGE" && (
        <div className="period-range-control">
          <div className="period-fields">
            <button className="icon-button" aria-label="Vorheriger Zeitraum" onClick={() => shiftCustomRange(-1)}>‹</button>
            <label className="sr-only" htmlFor="period-range-start">Startdatum</label>
            <input id="period-range-start" type="date" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} aria-label="Startdatum" aria-invalid={Boolean(rangeValidationError)} />
            <span aria-hidden="true">–</span>
            <label className="sr-only" htmlFor="period-range-end">Enddatum</label>
            <input id="period-range-end" type="date" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} aria-label="Enddatum" aria-invalid={Boolean(rangeValidationError)} />
            <button className="icon-button" aria-label="Nächster Zeitraum" onClick={() => shiftCustomRange(1)}>›</button>
            <button className="secondary small" disabled={Boolean(rangeValidationError)} onClick={applyCustomRange}>Anwenden</button>
            <button className="text-button" onClick={() => { const now = new Date(); replaceCustomRange(`${now.getFullYear()}-${pad2(now.getMonth() + 1)}-01`, localISODate(now)); }}>Zurücksetzen</button>
          </div>
          {rangeValidationError && <small className="period-error" role="alert">{rangeValidationError}</small>}
        </div>
      )}
    </div>
  );
}

const navigation: Array<{ id: PageId; text: string; icon: string; capability?: string }> = [
  { id: "overview", text: "Übersicht", icon: "⌂" },
  { id: "workspace", text: "Arbeitsbereich", icon: "✓", capability: "review_inbox" },
  { id: "accounts", text: "Konten", icon: "▤", capability: "accounts" },
  { id: "imports", text: "Importe", icon: "⇩", capability: "imports" },
  { id: "wealth", text: "Vermögen", icon: "◈", capability: "wealth" },
  { id: "forecast", text: "Prognose", icon: "⌁", capability: "forecasting" },
  { id: "settings", text: "Einstellungen", icon: "⚙" },
];

export function App() {
  const startup = useFinanceQuery<StartupStatus>("GetStartupStatus");
  const manifest = useFinanceQuery<CapabilityManifest>("GetCapabilityManifest");
  const [reviewRefresh, setReviewRefresh] = useState(0);
  const reviewInbox = useFinanceQuery<ReviewInbox>("GetReviewInbox", { limit: 200, refresh: reviewRefresh });
  const [page, setPage] = useState<PageId>("overview");
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>("tasks");
  const [navigationTarget, setNavigationTarget] = useState<NavigationTarget>(null);
  const periodControls = usePeriodSelection();
  const month = periodControls.period.start_date.slice(0, 7);
  const asOf = periodControls.period.end_date;
  const enabled = manifest.envelope?.data.capabilities ?? {};
  const visibleNavigation = navigation.filter((item) => !item.capability || enabled[item.capability]);
  const openReviews = reviewInbox.envelope?.data.total_open ?? 0;
  const navigate = (nextPage: PageId, target: NavigationTarget = null) => {
    const targetWorkspaceTab = nextPage === "transactions" ? "bookings" : nextPage === "categories" ? "categories" : nextPage === "reviews" ? "tasks" : null;
    if (nextPage === "reviews" || nextPage === "workspace") setReviewRefresh((value) => value + 1);
    const targetPeriod = target?.period ?? (nextPage === "imports" && target?.view === "month" ? target.entity_id : undefined);
    if (targetPeriod && /^\d{4}-\d{2}$/.test(targetPeriod)) {
      const [targetYear, targetMonth] = targetPeriod.split("-").map(Number);
      periodControls.setMode("MONTH");
      periodControls.setYear(targetYear);
      periodControls.setMonth(targetMonth);
    }
    if (targetWorkspaceTab) setWorkspaceTab(targetWorkspaceTab);
    else if (nextPage === "workspace") setWorkspaceTab("tasks");
    setNavigationTarget(target);
    setPage(targetWorkspaceTab ? "workspace" : nextPage);
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
              <span aria-hidden="true">{item.icon}</span>{item.text}{item.id === "workspace" && <span className="nav-count" aria-label={`${openReviews} offene Aufgaben`}>{openReviews}</span>}
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
          {page !== "overview" && page !== "settings" && page !== "imports" && (
            <PeriodSelector controls={periodControls} />
          )}
        </header>
        {page === "overview" && <Overview period={periodControls.period} periodPayload={periodControls.payload} reviewInbox={reviewInbox} onReviewResolved={() => setReviewRefresh((value) => value + 1)} onNavigate={navigate} />}
        {page === "accounts" && <AccountsOverview period={periodControls.period} periodPayload={periodControls.payload} focusId={navigationTarget?.page === "accounts" ? navigationTarget.entity_id : undefined} focusView={navigationTarget?.page === "accounts" ? navigationTarget.view : undefined} onNavigate={navigate} />}
        {page === "workspace" && <UnifiedFinanceWorkspace activeTab={workspaceTab} onTabChange={(tab) => { setWorkspaceTab(tab); setNavigationTarget(null); }} inbox={reviewInbox} month={month} periodPayload={periodControls.payload} navigationTarget={navigationTarget} onNavigate={navigate} />}
        {page === "recurring" && <Recurring month={month} />}
        {page === "forecast" && <Forecast month={month} />}
        {page === "wealth" && <Wealth asOf={asOf} />}
        {page === "imports" && <Imports uiMonth={month} manifest={manifest.envelope.data} focusExportId={navigationTarget?.page === "imports" ? navigationTarget.entity_id : undefined} focusView={navigationTarget?.page === "imports" ? navigationTarget.view : undefined} onOpenReviews={() => navigate("reviews")} />}
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

function QueryBoundary<T>({ result, children, empty = "Für diesen Zeitraum liegen keine Daten vor.", renderEmpty = false }: { result: QueryResult<T>; children: (data: T) => ReactNode; empty?: string; renderEmpty?: boolean }) {
  if (result.state === "LOADING") return <div className="page-state"><span className="loader" /><p>Projektion wird geladen …</p></div>;
  if (result.state === "ERROR" || result.state === "INCOMPATIBLE_SCHEMA" || result.state === "LOCKED") return <div className="page-state error"><h2>{result.state === "INCOMPATIBLE_SCHEMA" ? "Schema nicht kompatibel" : result.state === "LOCKED" ? "Daten gesperrt" : "Daten nicht verfügbar"}</h2><p>{result.error}</p></div>;
  if (result.state === "EMPTY" && !(renderEmpty && result.envelope)) return <div className="page-state"><span className="empty-icon">○</span><h2>Noch nichts zu zeigen</h2><p>{empty}</p></div>;
  if (!result.envelope) return null;
  return <>{(result.state === "STALE" || result.state === "PARTIAL") && <div className="status-banner" role="status"><strong>{result.state === "STALE" ? statusLabel("STALE") : "Teilprojektion"}</strong><span>{result.envelope.projection_sequence < result.envelope.event_store_sequence ? "Die lokale Ansicht wird noch aktualisiert." : result.state === "STALE" ? "Mindestens ein fachlicher Snapshot ist nicht aktuell." : "Ein Teil der lokalen Daten ist noch nicht verfügbar."}</span><details><summary>Technische Details</summary><code>Projektion #{result.envelope.projection_sequence} · Event Store #{result.envelope.event_store_sequence}</code></details></div>}{children(result.envelope.data)}</>;
}

function Overview({ period, reviewInbox, onReviewResolved, onNavigate }: { period: PeriodSelection; periodPayload: Record<string, unknown>; reviewInbox: QueryResult<ReviewInbox>; onReviewResolved: () => void; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const [view, setView] = useState<DashboardView>("CURRENT");
  const [analysisWindow, setAnalysisWindow] = useState<AnalysisWindow>(6);
  const [monthlyRefresh, setMonthlyRefresh] = useState(0);
  const availablePeriods = useFinanceQuery<AvailablePeriods>("GetAvailablePeriods");
  const monthlyWorkspace = useFinanceQuery<MonthlyWorkspaceStatus>("GetMonthlyWorkspaceStatus", { refresh: monthlyRefresh });
  const periodData = availablePeriods.envelope?.data;
  const latestMonth = periodData?.available_months.at(-1) ?? period.start_date.slice(0, 7);
  const [latestYear, latestMonthNumber] = latestMonth.split("-").map(Number);
  const latestPeriod = monthPeriod(latestYear, latestMonthNumber);
  const latestPayload = { mode: "MONTH", year: latestYear, month: latestMonthNumber };
  const analysisStart = (() => {
    if (analysisWindow === "ALL") return periodData?.earliest_date ?? latestPeriod.start_date;
    const point = new Date(latestYear, latestMonthNumber - analysisWindow, 1, 12);
    return localISODate(point);
  })();
  const analysisPayload = {
    mode: "CUSTOM_RANGE",
    start_date: analysisStart,
    end_date: periodData?.latest_date ?? latestPeriod.end_date,
  };
  const result = useFinanceQuery<Dashboard>("GetDashboard", latestPayload);
  const series = useFinanceQuery<FinancialTimeSeries>("GetFinancialTimeSeries", analysisPayload, view === "CURRENT");
  const accounts = useFinanceQuery<AccountOverviewList>("ListAccountOverviews", { period: latestPayload, include_closed: false });
  const categories = useFinanceQuery<CategoryBreakdown>("GetCategoryBreakdown", { month: latestMonth }, view === "CURRENT");
  const recentTransactions = useFinanceQuery<{ transactions: Transaction[] }>("ListTransactions", { ...analysisPayload, limit: 7 }, view === "CURRENT");
  const inbox = reviewInbox.envelope?.data;
  const reviewGroups = inbox ? [
    ["Kategorien", inbox.items_by_type.CATEGORY],
    ["AI-Vorschläge", inbox.items_by_type.AI_SUGGESTION],
    ["Saldenabweichungen", inbox.items_by_type.BALANCE],
    ["Importe", inbox.items_by_type.IMPORT + inbox.items_by_type.FORMAT_CHANGE],
    ["Relationen", inbox.items_by_type.RELATION],
  ].filter(([, count]) => Number(count) > 0) as Array<[string, number]> : [];
  return <>
    <section className="dashboard-titlebar">
      <div><span className="eyebrow">PRIVATER ARBEITSBEREICH</span><h2>{view === "CURRENT" ? "Aktuell" : "Arbeitsbereich"}</h2><p>{view === "CURRENT" ? "Dein Finanzstand auf Basis der zuletzt vollständig erfassten Daten." : "Vervollständige fehlende Finanzdaten und bearbeite offene Prüfungen."}</p></div>
      {view === "CURRENT" && <div className="analysis-window" role="group" aria-label="Analysezeitraum">{([3, 6, 12, "ALL"] as AnalysisWindow[]).map((value) => <button key={value} className={analysisWindow === value ? "active" : ""} onClick={() => setAnalysisWindow(value)}>{value === "ALL" ? "Gesamt" : `${value} Monate`}</button>)}</div>}
    </section>
    <div className="dashboard-segment" role="tablist" aria-label="Dashboardansicht"><button role="tab" aria-selected={view === "CURRENT"} onClick={() => setView("CURRENT")}>Aktuell</button><button role="tab" aria-selected={view === "WORKSPACE"} onClick={() => setView("WORKSPACE")}>Arbeitsbereich{(inbox?.total_open ?? 0) > 0 && <span>{inbox?.total_open}</span>}</button></div>
    {view === "WORKSPACE" ? <DashboardWorkspace monthlyStatus={monthlyWorkspace} inbox={reviewInbox} accounts={accounts} onReviewResolved={() => { setMonthlyRefresh((value) => value + 1); onReviewResolved(); }} onNavigate={onNavigate} /> : <QueryBoundary result={result}>{(data) => <>
    <DashboardFreshness result={accounts} period={latestPeriod} monthly={monthlyWorkspace.envelope?.data} onOpenMissing={() => setView("WORKSPACE")} />
    <div className="dashboard-section-heading"><div><span className="eyebrow">FINANZLAGE</span><h2>Zentrale Kennzahlen</h2></div><small>{latestPeriod.display_label}</small></div>
    <section className="metric-grid dashboard-kpi-grid" aria-label="Zentrale Finanzkennzahlen">
      <Metric label="Einnahmen" value={money(data.effective_income)} tone="positive" note={data.effective_income == null ? "Keine Buchungsdaten" : "Bereinigte Buchungen"} />
      <Metric label="Ausgaben" value={money(data.effective_expenses)} tone="negative" note={data.effective_expenses == null ? "Keine Buchungsdaten" : "Nach Abgleichen"} />
      <Metric label="Netto-Cashflow" value={money(data.net_cashflow)} tone="ink" note={data.net_cashflow == null ? "Keine Buchungsdaten" : "Einnahmen minus Ausgaben"} />
      <Metric label="Liquidität" value={money(data.liquid_balance)} tone="ink" note={data.liquid_balance == null ? "Kein bestätigter Saldo" : `Stand ${date(data.liquid_balance_as_of ?? period.end_date)}`} />
      <Metric label="Nettovermögen" value={money(data.net_worth)} tone="ink" note={data.net_worth == null ? "Keine bewerteten Bestände" : `Stand ${date(data.net_worth_as_of ?? period.end_date)}`} />
    </section>
    <div className="dashboard-grid">
      <section className="panel cashflow-card"><PanelHeader title="Finanzielle Entwicklung" subtitle={`${analysisWindow === "ALL" ? "Gesamter Zeitraum" : `Letzte ${analysisWindow} Monate`} · bis ${monthLabel(latestMonth)}`} action="Transaktionen" onAction={() => onNavigate("transactions")} /><FinancialTimeSeriesChart result={series} /></section>
      <section className="panel dashboard-reviews"><PanelHeader title={`${inbox?.total_open ?? data.open_reviews} Dinge benötigen Aufmerksamkeit`} subtitle="Kurzfassung deiner offenen Aufgaben" />{reviewGroups.length ? <div className="dashboard-review-list">{reviewGroups.map(([title, count]) => <div key={title}><span>{title}</span><strong>{count}</strong></div>)}</div> : <p className="dashboard-review-empty">Aktuell sind keine Aufgaben offen.</p>}<button className="primary" onClick={() => setView("WORKSPACE")}>Arbeitsbereich öffnen</button></section>
    </div>
    <div className="dashboard-lower-grid">
      <DashboardCategories result={categories} period={latestPeriod} onOpen={() => onNavigate("categories")} />
      <DashboardTransactions result={recentTransactions} accounts={accounts} onOpenAll={() => onNavigate("transactions")} onOpen={(transactionId) => onNavigate("transactions", { page: "transactions", view: "detail", entity_id: transactionId })} />
    </div>
    <section className="panel privacy-note"><span className="shield">◇</span><div><strong>Deine Finanzdaten bleiben auf diesem Gerät.</strong><p>Bestände stammen aus expliziten Salden-Snapshots; berechnete Werte und Prognosen sind klar gekennzeichnet.</p></div><span className="sequence">Projektion #{result.envelope?.projection_sequence}</span></section>
  </>}</QueryBoundary>}
  </>;
}

const WORKSPACE_MONTH_STATUS: Record<WorkspaceMonthStatus, { label: string; symbol: string }> = {
  COMPLETE: { label: "Vollständig", symbol: "✓" },
  REVIEW: { label: "Prüfung offen", symbol: "!" },
  MISSING: { label: "Import fehlt", symbol: "×" },
  OUTSIDE: { label: "Noch nicht erfasst", symbol: "○" },
};

function DashboardWorkspace({ monthlyStatus, inbox, accounts, onReviewResolved, onNavigate }: { monthlyStatus: QueryResult<MonthlyWorkspaceStatus>; inbox: QueryResult<ReviewInbox>; accounts: QueryResult<AccountOverviewList>; onReviewResolved: () => void; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const [scope, setScope] = useState<"12" | "YEAR" | "ALL">("12");
  const [activeSuggestionMonth, setActiveSuggestionMonth] = useState<string | null>(null);
  const workspaceData = monthlyStatus.envelope?.data;
  const currentMonth = workspaceData?.current_month ?? localISODate().slice(0, 7);
  const currentYear = Number(currentMonth.slice(0, 4));
  const [timelineYear, setTimelineYear] = useState(currentYear);
  useEffect(() => {
    if (workspaceData?.current_month) setTimelineYear(Number(workspaceData.current_month.slice(0, 4)));
  }, [workspaceData?.current_month]);
  const [filter, setFilter] = useState<ReviewType | "ALL">("ALL");
  const inboxData = inbox.envelope?.data;
  const earliest = workspaceData?.earliest_month ?? currentMonth;
  const workspaceYears = workspaceData?.months.map((item) => Number(item.month.slice(0, 4))) ?? [];
  const yearOptions = Array.from(new Set([...workspaceYears, currentYear])).sort((left, right) => right - left);
  const firstMonth = scope === "12" ? shiftMonthKey(currentMonth, -11) : scope === "YEAR" ? `${timelineYear}-01` : earliest;
  const lastMonth = scope === "YEAR" ? `${timelineYear}-12` : currentMonth;
  const statusByMonth = new Map((workspaceData?.months ?? []).map((item) => [item.month, item]));
  const items = inboxData?.items ?? [];
  const months: WorkspaceMonth[] = monthKeysBetween(firstMonth, lastMonth).map((month) => {
    const reviews = items.filter((item) => reviewMonth(item) === month);
    const details = statusByMonth.get(month);
    const status: WorkspaceMonthStatus = details?.status ?? "OUTSIDE";
    return { month, label: monthLabel(month), status, reviews, isCurrent: month === currentMonth, details };
  });
  const actionableMonths = months.filter((item) => item.status === "MISSING" || item.status === "REVIEW").reverse();
  const completedMonths = months.filter((item) => item.status === "COMPLETE");
  const filteredItems = items.filter((item) => filter === "ALL" || item.review_type === filter);
  const latestComplete = workspaceData?.latest_complete_month;
  const firstMissing = workspaceData?.months.find((item) => item.status === "MISSING")?.month;
  const missingLabel = firstMissing ? `${monthLabel(firstMissing)} – ${monthLabel(currentMonth)}` : "Kein Zeitraum offen";
  const affectedAccounts = new Set(items.flatMap((item) => item.account_ids)).size;
  const openMonth = (item: WorkspaceMonth) => {
    const primary = item.details?.primary_action as ReviewItem["action_target"] | null | undefined;
    if (primary) onNavigate(primary.page, primary);
    else if (item.reviews[0]) onNavigate(item.reviews[0].action_target.page, item.reviews[0].action_target);
    else onNavigate("imports", { page: "imports", view: "month", entity_id: item.month });
  };
  const openMonthReview = (item: WorkspaceMonth, type: ReviewType) => {
    if (type === "CATEGORY") {
      onNavigate("transactions", { page: "transactions", view: "uncategorized", entity_id: "", period: item.month });
      return;
    }
    if (type === "AI_SUGGESTION") {
      onNavigate("categories", { page: "categories", view: "suggestions", entity_id: "", period: item.month });
      return;
    }
    const review = item.reviews.find((candidate) => candidate.review_type === type);
    if (review) onNavigate(review.action_target.page, review.action_target);
    else onNavigate("reviews", { page: "reviews", view: "month", entity_id: item.month, period: item.month });
  };
  const firstAction = actionableMonths[0];
  return <>
    <section className="panel workspace-overview" aria-label="Arbeitsstand im Dashboard">
      <div><span>Finanzdaten vollständig bis</span><strong>{latestComplete ? monthLabel(latestComplete) : "Noch kein vollständiger Monat"}</strong></div>
      <div><span>Fehlender Zeitraum</span><strong>{missingLabel}</strong></div>
      <div><span>Offene Aufgaben</span><strong>{inboxData?.total_open ?? 0}</strong></div>
      <div><span>Betroffene Konten</span><strong>{affectedAccounts || (accounts.envelope?.data.accounts.length ?? 0)}</strong></div>
      {firstAction && <button className="primary" onClick={() => openMonth(firstAction)}>Weiterarbeiten →</button>}
    </section>

    <section className="panel month-timeline-card">
      <header><div><span className="eyebrow">DATENVOLLSTÄNDIGKEIT</span><h2>Monatsübersicht</h2><p>Erkenne sofort, welche Zeiträume vollständig sind und wo noch Aktionen notwendig sind.</p></div><div className="timeline-controls"><div className="timeline-scope" role="group" aria-label="Zeitraum der Monatsübersicht">{(["12", "YEAR", "ALL"] as const).map((value) => <button key={value} className={scope === value ? "active" : ""} onClick={() => setScope(value)}>{value === "12" ? "12 Monate" : value === "YEAR" ? "Jahr" : "Gesamt"}</button>)}</div>{scope === "YEAR" && <label className="timeline-year-picker"><span>Jahr auswählen</span><select aria-label="Jahr der Monatsübersicht" value={timelineYear} onChange={(event) => setTimelineYear(Number(event.target.value))}>{yearOptions.map((year) => <option key={year} value={year}>{year}</option>)}</select></label>}</div></header>
      {monthlyStatus.state === "LOADING" ? <div className="dashboard-card-empty"><span className="loader" /><p>Monate werden geprüft …</p></div> : <div className="month-timeline" role="list" aria-label="Status der Monate">{months.map((item) => <button key={item.month} role="listitem" className={`month-timeline-item ${item.status.toLowerCase()}`} onClick={() => item.status !== "OUTSIDE" && openMonth(item)} disabled={item.status === "OUTSIDE"} title={`${item.label}: ${WORKSPACE_MONTH_STATUS[item.status].label}${item.details?.open_review_count ? ` · ${item.details.open_review_count} offene Aufgaben` : ""}`}><span>{item.label.split(" ")[0].slice(0, 3)}</span><strong>{WORKSPACE_MONTH_STATUS[item.status].symbol}</strong><small>{item.isCurrent ? "Heute" : WORKSPACE_MONTH_STATUS[item.status].label}</small></button>)}</div>}
      <footer className="month-timeline-legend"><span className="complete">✓ Vollständig</span><span className="review">! Prüfung offen</span><span className="missing">× Import fehlt</span><span className="outside">○ Noch nicht erfasst</span></footer>
    </section>

    <div className="workspace-section-heading"><div><span className="eyebrow">MONATLICH WEITERARBEITEN</span><h2>Monate mit offenen Punkten</h2></div></div>
    <section className="workspace-month-grid">{actionableMonths.length === 0 ? <div className="panel page-state workspace-empty"><span className="empty-icon">✓</span><h3>Alle Monate vollständig</h3><p>Für den dargestellten Zeitraum sind keine offenen Monate vorhanden.</p></div> : actionableMonths.slice(0, 6).map((item) => {
      const categoryCount = item.details?.review_counts.CATEGORY ?? 0;
      const suggestionCount = item.details?.review_counts.AI_SUGGESTION ?? 0;
      return <article className={`panel workspace-month-card ${item.status.toLowerCase()} ${activeSuggestionMonth === item.month ? "expanded" : ""}`} key={item.month}>
        <div><span className={`status-badge ${item.status === "MISSING" ? "missed" : "paused"}`}>{WORKSPACE_MONTH_STATUS[item.status].label}</span><h3>{item.label}</h3><p>{item.status === "MISSING" ? `${item.details?.completed_account_count ?? 0} von ${item.details?.expected_account_count ?? 0} erwarteten Konten vollständig.` : `${item.details?.open_review_count ?? item.reviews.length} ${(item.details?.open_review_count ?? item.reviews.length) === 1 ? "offene Aufgabe" : "offene Aufgaben"}`}</p></div>
        {item.details && item.details.open_review_count > 0 && <ul>{(Object.keys(REVIEW_LABELS) as ReviewType[]).map((type) => [type, item.details!.review_counts[type]] as const).filter(([, count]) => count > 0).map(([type, count]) => <li key={type}><span>{REVIEW_LABELS[type]}</span><strong>{count}</strong></li>)}</ul>}
        <div className="workspace-month-actions">
          {categoryCount > 0 && <button className="secondary small" onClick={() => openMonthReview(item, "CATEGORY")}>Kategorien prüfen · {categoryCount}</button>}
          {suggestionCount > 0 && <button className="secondary small" aria-expanded={activeSuggestionMonth === item.month} onClick={() => setActiveSuggestionMonth((current) => current === item.month ? null : item.month)}>Vorschläge direkt prüfen · {suggestionCount}</button>}
          <button className={item.status === "MISSING" ? "primary small" : "text-button"} onClick={() => openMonth(item)}>{item.status === "MISSING" ? "Import starten" : "Alle Aufgaben"} →</button>
        </div>
        {activeSuggestionMonth === item.month && <MonthlySuggestionActions month={item.month} onOpenAll={() => openMonthReview(item, "AI_SUGGESTION")} onResolved={onReviewResolved} />}
      </article>;
    })}</section>

    <div className="workspace-section-heading"><div><span className="eyebrow">AUFGABEN-INBOX</span><h2>Offene Aufgaben</h2></div><button className="secondary small" onClick={() => onNavigate("reviews")}>Alle in „Prüfen“ öffnen</button></div>
    <div className="workspace-filterbar" role="group" aria-label="Aufgaben filtern"><button className={filter === "ALL" ? "active" : ""} onClick={() => setFilter("ALL")}>Alle</button>{(Object.keys(REVIEW_LABELS) as ReviewType[]).map((type) => <button key={type} className={filter === type ? "active" : ""} onClick={() => setFilter(type)}>{REVIEW_LABELS[type]} <span>{inboxData?.items_by_type[type] ?? 0}</span></button>)}</div>
    <section className="workspace-task-grid dashboard-workspace-tasks">{filteredItems.length === 0 ? <div className="panel page-state workspace-empty"><span className="empty-icon">✓</span><h3>Alles erledigt</h3><p>In diesem Bereich sind keine offenen Aufgaben vorhanden.</p></div> : filteredItems.slice(0, 8).map((item) => <article className={`panel workspace-task severity-${item.severity.toLowerCase()}`} key={item.review_id}><div className="workspace-task-status"><span>{REVIEW_LABELS[item.review_type]}</span><span className={`status-badge ${item.severity === "ERROR" ? "missed" : item.severity === "WARNING" ? "paused" : "confirmed"}`}>{item.severity === "ERROR" ? "Dringend" : item.severity === "WARNING" ? "Prüfung offen" : "Hinweis"}</span></div><h3>{item.title}</h3><p>{item.description}</p><small>{monthLabel(reviewMonth(item))}</small><footer><button className={item.severity === "ERROR" ? "primary small" : "secondary small"} onClick={() => onNavigate(item.action_target.page, item.action_target)}>Aufgabe öffnen →</button></footer></article>)}</section>
    {completedMonths.length > 0 && <details className="panel completed-months"><summary>Vollständig abgeschlossene Monate <span>{completedMonths.length}</span></summary><p>{completedMonths.map((item) => item.label).join(" · ")}</p></details>}
  </>;
}

function MonthlySuggestionActions({ month, onResolved, onOpenAll }: { month: string; onResolved: () => void; onOpenAll: () => void }) {
  const [refresh, setRefresh] = useState(0);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const result = useFinanceQuery<{ suggestions: CategorySuggestionRow[] }>("ListPendingCategorySuggestions", { period: month, limit: 20, refresh });
  const rows = (result.envelope?.data.suggestions ?? []).filter((item) => !removed.has(item.suggestion_id));
  const decide = async (item: CategorySuggestionRow, action: "confirm" | "reject") => {
    setBusy(item.suggestion_id);
    setMessage("");
    try {
      await financeBridge.command(action === "confirm" ? "ConfirmCategorySuggestion" : "RejectCategorySuggestion", { suggestion_id: item.suggestion_id });
      setRemoved((current) => new Set([...current, item.suggestion_id]));
      setMessage(action === "confirm" ? `„${categoryLabel(item.category_code)}“ wurde für ${item.merchant || "die Transaktion"} bestätigt.` : `Der Vorschlag für ${item.merchant || "die Transaktion"} wurde abgelehnt.`);
      setRefresh((value) => value + 1);
      onResolved();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Der Vorschlag konnte nicht verarbeitet werden.");
    } finally {
      setBusy(null);
    }
  };
  return <section className="monthly-suggestion-actions" aria-label={`AI-Vorschläge ${monthLabel(month)}`}>
    <header><div><strong>Kategorien direkt bestätigen</strong><small>Die Auswahl wird erst durch deinen Klick wirksam.</small></div><button className="text-button" onClick={onOpenAll}>Erweitert prüfen →</button></header>
    {message && <div className="inline-feedback" role="status">{message}</div>}
    {result.state === "LOADING" ? <div className="monthly-suggestion-loading"><span className="loader" /> Vorschläge werden geladen …</div> : result.state === "ERROR" ? <div className="inline-feedback error">Vorschläge konnten nicht geladen werden.</div> : rows.length === 0 ? <div className="monthly-suggestion-empty">Für {monthLabel(month)} sind keine offenen AI-Vorschläge mehr vorhanden.</div> : <div className="monthly-suggestion-list">{rows.slice(0, 4).map((item) => <article key={item.suggestion_id}>
      <div><strong>{item.merchant || "Unbekannter Anbieter"}</strong><small>{item.description || "Keine Beschreibung"} · {money(item.amount, item.currency)}</small></div>
      <div className="monthly-suggestion-category"><small>Vorschlag</small><strong>{categoryLabel(item.category_code)}</strong></div>
      <div className="monthly-suggestion-buttons"><button className="primary small" disabled={busy === item.suggestion_id} onClick={() => decide(item, "confirm")}>{categoryLabel(item.category_code)} bestätigen</button><button className="secondary small" disabled={busy === item.suggestion_id} onClick={() => decide(item, "reject")}>Ablehnen</button></div>
    </article>)}</div>}
    {rows.length > 4 && <button className="text-button" onClick={onOpenAll}>{rows.length - 4} weitere Vorschläge öffnen →</button>}
  </section>;
}

function DashboardFreshness({ result, period, monthly, onOpenMissing }: { result: QueryResult<AccountOverviewList>; period: PeriodSelection; monthly?: MonthlyWorkspaceStatus; onOpenMissing: () => void }) {
  if (result.state === "LOADING") return <section className="panel dashboard-freshness loading"><span className="loader" /><p>Kontostand wird geprüft …</p></section>;
  const rows = result.envelope?.data.accounts ?? [];
  const current = rows.filter((item) => item.data_freshness === "CURRENT").length;
  const stale = rows.filter((item) => item.data_freshness === "STALE").length;
  const reportedDates = rows.map((item) => item.reported_balance_date).filter((value): value is string => Boolean(value));
  const latest = reportedDates.sort().at(-1);
  const isCurrent = rows.length > 0 && stale === 0 && current === rows.length;
  const isUnavailable = result.state === "ERROR" || result.state === "INCOMPATIBLE_SCHEMA" || !result.envelope;
  const latestMonth = monthly?.latest_data_month;
  const missingMonths = Boolean(monthly?.months.some((item) => item.status === "MISSING"));
  const firstMissingMonth = monthly?.months.find((item) => item.status === "MISSING")?.month;
  return <section className={`panel dashboard-freshness ${missingMonths ? "missing" : isCurrent ? "current" : "attention"}`} aria-label="Datenaktualität">
    <span className="freshness-icon" aria-hidden="true">{isUnavailable ? "!" : isCurrent ? "✓" : "○"}</span>
    <div className="freshness-copy"><div><h2>{isUnavailable ? "Datenstand nicht verfügbar" : latest ? `Kontostände bis ${date(latest)}` : `Noch keine Kontostände für ${period.display_label}`}</h2><span className={`status-badge ${isCurrent ? "confirmed" : "paused"}`}>{isCurrent ? "Aktuell" : stale > 0 ? "Daten nicht aktuell" : "Noch unvollständig"}</span></div><p>{isUnavailable ? "Die Kontoprojektion konnte nicht geladen werden." : rows.length ? `${current} von ${rows.length} aktiven Konten besitzen einen aktuellen fachlichen Stand.` : "Lege ein Konto an oder importiere einen Bankexport, um den Datenstand aufzubauen."}</p></div>
    {!isUnavailable && rows.length > 0 && <div className="freshness-meta"><span>{missingMonths ? "Fehlender Zeitraum" : "Aktuelle Konten"}</span><strong>{missingMonths && firstMissingMonth ? `${monthLabel(firstMissingMonth)} – ${monthLabel(monthly?.current_month)}` : `${current} von ${rows.length}`}</strong>{missingMonths && <button className="text-button" onClick={onOpenMissing}>Fehlende Daten bearbeiten</button>}</div>}
  </section>;
}

function DashboardCategories({ result, period, onOpen }: { result: QueryResult<CategoryBreakdown>; period: PeriodSelection; onOpen: () => void }) {
  const rows = Object.values(result.envelope?.data.categories ?? {})
    .filter((item) => Number(item.effective_expense) > 0)
    .sort((left, right) => Number(right.effective_expense) - Number(left.effective_expense))
    .slice(0, 5);
  const total = rows.reduce((sum, item) => sum + Number(item.effective_expense), 0);
  return <section className="panel dashboard-categories"><PanelHeader title="Wofür du Geld ausgegeben hast" subtitle={period.display_label} action="Alle Kategorien" onAction={onOpen} />
    {period.mode !== "MONTH" ? <div className="dashboard-card-empty"><p>Die fachlich abgeglichene Kategorieauswertung steht derzeit monatsweise bereit.</p><button className="text-button" onClick={onOpen}>Kategorien öffnen</button></div>
      : result.state === "LOADING" ? <div className="dashboard-card-empty"><span className="loader" /><p>Kategorien werden geladen …</p></div>
      : rows.length === 0 ? <div className="dashboard-card-empty"><p>Für diesen Monat liegen noch keine kategorisierten Ausgaben vor.</p></div>
      : <div className="dashboard-category-list">{rows.map((item) => { const share = total > 0 ? Number(item.effective_expense) / total : 0; return <button key={item.category_code} onClick={onOpen}><span><strong>{categoryLabel(item.category_code)}</strong><small>{item.transaction_count} Buchungen</small></span><b>{money(item.effective_expense)}</b><em>{Math.round(share * 100)} %</em><i><span style={{ width: `${Math.max(4, share * 100)}%` }} /></i></button>; })}</div>}
  </section>;
}

function DashboardTransactions({ result, accounts, onOpenAll, onOpen }: { result: QueryResult<{ transactions: Transaction[] }>; accounts: QueryResult<AccountOverviewList>; onOpenAll: () => void; onOpen: (transactionId: string) => void }) {
  const rows = result.envelope?.data.transactions.slice(0, 5) ?? [];
  const accountNames = Object.fromEntries((accounts.envelope?.data.accounts ?? []).map((item) => [item.account_id, item.display_name]));
  return <section className="panel dashboard-transactions"><PanelHeader title="Letzte Transaktionen" subtitle="Im gewählten Zeitraum" action="Alle Transaktionen" onAction={onOpenAll} />
    {result.state === "LOADING" ? <div className="dashboard-card-empty"><span className="loader" /><p>Transaktionen werden geladen …</p></div>
      : rows.length === 0 ? <div className="dashboard-card-empty"><p>Für diesen Zeitraum liegen keine Buchungen vor.</p></div>
      : <div className="dashboard-transaction-list"><div className="dashboard-transaction-head"><span>Datum</span><span>Händler</span><span>Kategorie</span><span>Konto</span><span>Betrag</span></div>{rows.map((item) => <button key={item.transaction_id} onClick={() => onOpen(item.transaction_id)} aria-label={`${item.counterparty || "Transaktion"} öffnen`}><time>{date(item.booking_date)}</time><span><strong>{item.counterparty || "Unbekannter Händler"}</strong><small>{item.description || item.normalized_description || "Keine Beschreibung"}</small></span><span>{categoryLabel(item.category_code)}</span><span>{accountNames[item.account_id ?? ""] ?? item.account_id ?? "–"}</span><b className={Number(item.amount) >= 0 ? "positive-text" : ""}>{money(item.amount, item.currency)}</b></button>)}</div>}
  </section>;
}

function FinancialTimeSeriesChart({ result }: { result: QueryResult<FinancialTimeSeries> }) {
  if (result.state === "LOADING") return <div className="chart-state"><span className="loader" /><p>Verlauf wird geladen …</p></div>;
  if (result.state === "ERROR" || result.state === "INCOMPATIBLE_SCHEMA") return <div className="chart-state error"><p>Der Finanzverlauf ist derzeit nicht verfügbar.</p></div>;
  const data = result.envelope?.data;
  if (!data?.summary.has_data) return <div className="chart-state"><span className="empty-icon">○</span><p>Für diesen Zeitraum liegen keine Buchungsdaten vor.</p></div>;
  const width = 720; const height = 230; const top = 18; const bottom = 35; const plotHeight = height - top - bottom;
  const values = data.points.flatMap((point) => point.has_data ? [Number(point.income), -Number(point.expenses), Number(point.net_cashflow)] : []);
  const maximum = Math.max(1, ...values.map((value) => Math.abs(value)));
  const x = (index: number) => data.points.length === 1 ? width / 2 : index * width / (data.points.length - 1);
  const y = (value: number) => top + plotHeight / 2 - (value / maximum) * (plotHeight / 2 - 8);
  const paths = (selector: (point: FinancialTimeSeries["points"][number]) => string | null, invert = false) => {
    const segments: string[] = []; let current = "";
    data.points.forEach((point, index) => { const raw = selector(point); if (raw == null) { if (current) segments.push(current); current = ""; return; } const value = Number(raw) * (invert ? -1 : 1); current += `${current ? " L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)}`; });
    if (current) segments.push(current); return segments;
  };
  const labels = Array.from(new Set([0, Math.floor((data.points.length - 1) / 2), data.points.length - 1]));
  return <div className="financial-chart">
    <div className="chart-legend"><span className="income">Einnahmen</span><span className="expenses">Ausgaben</span><span className="net">Netto-Cashflow</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Finanzverlauf mit ${data.summary.covered_bucket_count} belegten und ${data.summary.missing_bucket_count} unbelegten Zeitabschnitten`}>
      <path className="gridline" d={`M0 ${y(maximum / 2)}H${width}M0 ${y(0)}H${width}M0 ${y(-maximum / 2)}H${width}`} />
      {data.points.map((point, index) => !point.has_data && <rect key={point.bucket_start} className="chart-gap" x={Math.max(0, x(index) - 3)} y={top} width="6" height={plotHeight} />)}
      {paths((point) => point.income).map((path) => <path key={`income-${path}`} className="series-income" d={path} />)}
      {paths((point) => point.expenses, true).map((path) => <path key={`expenses-${path}`} className="series-expenses" d={path} />)}
      {paths((point) => point.net_cashflow).map((path) => <path key={`net-${path}`} className="series-net" d={path} />)}
      {data.points.map((point, index) => point.has_data && <g key={`points-${point.bucket_start}`}>
        <circle className="point-income" cx={x(index)} cy={y(Number(point.income))} r="3" />
        <circle className="point-expenses" cx={x(index)} cy={y(-Number(point.expenses))} r="3" />
        <circle className="point-net" cx={x(index)} cy={y(Number(point.net_cashflow))} r="3" />
      </g>)}
      <g className="chart-labels">{labels.map((index) => <text key={index} x={x(index)} y={height - 8} textAnchor={index === 0 ? "start" : index === data.points.length - 1 ? "end" : "middle"}>{date(data.points[index].bucket_start)}</text>)}</g>
    </svg>
    <p className="chart-coverage">{data.summary.missing_bucket_count > 0 ? `${data.summary.missing_bucket_count} Zeitabschnitte ohne Buchungsdaten sind als Lücken markiert.` : "Der gewählte Zeitraum ist vollständig mit Buchungsdaten belegt."}</p>
  </div>;
}

const OVERVIEW_STATUS_TONE: Record<string, string> = {
  MATCHED: "confirmed", DIFFERENCE: "missed", STALE: "paused",
  MISSING_BALANCE: "missed", REVIEW_REQUIRED: "missed", CLOSED: "paused",
};

function AccountsOverview({ period, periodPayload, focusId, focusView, onNavigate }: { period: PeriodSelection; periodPayload: Record<string, unknown>; focusId?: string; focusView?: string; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const [includeClosed, setIncludeClosed] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const result = useFinanceQuery<AccountOverviewList>("ListAccountOverviews", { period: periodPayload, include_closed: includeClosed }, true, refreshToken);
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
      {selectedAccount && <AccountWorkspace account={selectedAccount} initialTab={focusView} period={period} onClose={() => setSelectedAccount(null)} onMessage={setMessage} onNavigate={onNavigate} onAccountChanged={() => setRefreshToken((value) => value + 1)} />}
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

function AccountWorkspace({ account, initialTab, period, onClose, onMessage, onNavigate, onAccountChanged }: { account: AccountOverviewRow; initialTab?: string; period: PeriodSelection; onClose: () => void; onMessage: (message: string) => void; onNavigate: (page: PageId, target?: NavigationTarget) => void; onAccountChanged: () => void }) {
  const first = useRef<HTMLButtonElement>(null);
  const handleKeyDown = useModalFocus(first, onClose);
  const allowedInitialTab = (["overview", "transactions", "balances", "reconciliations", "imports", "positions", "audit"] as string[]).includes(initialTab ?? "") ? initialTab as AccountTabId : "overview";
  const [tab, setTab] = useState<AccountTabId>(allowedInitialTab);
  const [workspaceRefresh, setWorkspaceRefresh] = useState(0);
  const initialMonth = period.start_date.slice(0, 7).split("-").map(Number);
  const [workspaceYear, setWorkspaceYear] = useState(initialMonth[0]);
  const [workspaceMonth, setWorkspaceMonth] = useState(initialMonth[1]);
  const workspacePeriod = useMemo(() => monthPeriod(workspaceYear, workspaceMonth), [workspaceMonth, workspaceYear]);
  const workspacePeriodPayload = useMemo(() => ({ mode: "MONTH", year: workspaceYear, month: workspaceMonth }), [workspaceMonth, workspaceYear]);
  const workspace = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: account.account_id, period: workspacePeriodPayload }, true, workspaceRefresh);
  const refreshedAccount = workspace.envelope?.data.account;
  const currentAccount = refreshedAccount ? {
    ...account,
    ...refreshedAccount,
    account_id: account.account_id,
    display_name: account.display_name,
    account_type: account.account_type,
    institution: account.institution,
    currency: account.currency,
  } : account;
  const hasRecordedBalance = Boolean(workspace.envelope?.data.balance_history.some((item) => item.reported_value !== null));
  const displayedOverviewStatus = effectiveAccountOverviewStatus(
    currentAccount.overview_status,
    currentAccount.data_freshness,
    hasRecordedBalance,
  );
  const accountChanged = () => {
    setWorkspaceRefresh((value) => value + 1);
    onAccountChanged();
  };
  const moveWorkspaceMonth = (offset: number) => {
    const point = new Date(workspaceYear, workspaceMonth - 1 + offset, 1, 12);
    setWorkspaceYear(point.getFullYear());
    setWorkspaceMonth(point.getMonth() + 1);
  };
  const isBrokerage = currentAccount.account_type === "BROKERAGE";
  const tabs: Array<[AccountTabId, string]> = [
    ["overview", "Übersicht"], ["transactions", "Transaktionen"], ["balances", "Salden"],
    ["reconciliations", "Abgleiche"], ["imports", "Importe"],
    ...(isBrokerage ? [["positions", "Positionen"] as [AccountTabId, string]] : []),
    ["audit", "Audit"],
  ];
  return <div className="dialog-backdrop"><section className="dialog wide account-workspace" role="dialog" aria-modal="true" aria-labelledby="account-workspace-title" onKeyDown={handleKeyDown}>
    <button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button>
    <span className="eyebrow">KONTOARBEITSBEREICH</span>
    <div className="account-workspace-heading"><div><h2 id="account-workspace-title">{currentAccount.display_name}</h2><p>{currentAccount.institution} · {label(currentAccount.account_type)}{currentAccount.status === "CLOSED" ? " · Geschlossen und schreibgeschützt" : ""}</p></div><span className={`status-badge ${OVERVIEW_STATUS_TONE[displayedOverviewStatus] ?? "paused"}`}>{statusLabel(displayedOverviewStatus)}</span></div>
    <div className="account-month-navigation" role="group" aria-label="Monat im Kontoarbeitsbereich"><button className="secondary small" onClick={() => moveWorkspaceMonth(-1)} aria-label="Vorheriger Monat">‹</button><strong>{workspacePeriod.display_label}</strong><button className="secondary small" onClick={() => moveWorkspaceMonth(1)} aria-label="Nächster Monat">›</button></div>
    <div className="tabs" role="tablist" aria-label="Kontobereiche">
      {tabs.map(([id, text]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{text}</button>)}
    </div>
    {tab === "overview" && <AccountTabOverview accountId={currentAccount.account_id} periodPayload={workspacePeriodPayload} onMessage={onMessage} onNavigate={onNavigate} readOnly={currentAccount.status === "CLOSED"} />}
    {tab === "transactions" && <AccountTabTransactions accountId={currentAccount.account_id} periodPayload={workspacePeriodPayload} onNavigate={onNavigate} onAudit={() => setTab("audit")} />}
    {tab === "balances" && <AccountTabBalances account={currentAccount} period={workspacePeriod} periodPayload={workspacePeriodPayload} onAudit={() => setTab("audit")} onMessage={onMessage} onChanged={accountChanged} />}
    {tab === "reconciliations" && <AccountTabReconciliations accountId={currentAccount.account_id} onMessage={onMessage} onNavigate={onNavigate} />}
    {tab === "imports" && <AccountTabImports accountId={currentAccount.account_id} onNavigate={onNavigate} onAudit={() => setTab("audit")} />}
    {tab === "positions" && isBrokerage && <AccountTabPositions accountId={currentAccount.account_id} />}
    {tab === "audit" && <AccountTabAudit accountId={currentAccount.account_id} onNavigate={onNavigate} onOpenTab={setTab} />}
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
    <section className="account-overview-context"><div><span>Letzter Import</span><strong>{monthLabel(data.account.last_import_month)}</strong></div><button onClick={() => onNavigate("reviews", { page: "reviews", view: "account", entity_id: accountId })}><span>Offene Aufgaben</span><strong>{data.open_review_count}</strong><small>Im Arbeitsbereich öffnen →</small></button></section>
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

function AccountTabBalances({ account, period, periodPayload, onAudit, onMessage, onChanged }: { account: AccountOverviewRow; period: PeriodSelection; periodPayload: Record<string, unknown>; onAudit: () => void; onMessage: (message: string) => void; onChanged: () => void }) {
  const [balanceType, setBalanceType] = useState<"OPENING" | "CLOSING">("OPENING");
  const [balanceDate, setBalanceDate] = useState(previousISODate(period.start_date));
  const [amount, setAmount] = useState("");
  const [feedback, setFeedback] = useState("");
  const [adjustmentReason, setAdjustmentReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [acceptingSuggestion, setAcceptingSuggestion] = useState(false);
  const [suggestionAccepted, setSuggestionAccepted] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [suggestionRefresh, setSuggestionRefresh] = useState(0);
  const result = useFinanceQuery<AccountDetailWorkspace>("GetAccountDetail", { account_id: account.account_id, period: periodPayload }, true, refreshToken);
  const suggestionResult = useFinanceQuery<OpeningBalanceSuggestion>("GetOpeningBalanceSuggestion", { account_id: account.account_id, period_start: period.start_date }, account.status !== "CLOSED", suggestionRefresh);
  const suggestion = suggestionResult.envelope?.data;
  const suggestedValue = suggestion?.eligible ? normalizedDecimal(String(suggestion.suggested_value ?? "")) : "";
  const enteredValue = normalizedDecimal(amount);
  const deviatesFromSuggestion = balanceType === "OPENING" && Boolean(suggestedValue) && Boolean(enteredValue) && enteredValue !== suggestedValue;
  useEffect(() => {
    setBalanceType("OPENING");
    setBalanceDate(previousISODate(period.start_date));
    setAmount("");
    setFeedback("");
    setAdjustmentReason("");
    setSuggestionAccepted(false);
  }, [period.end_date, period.start_date]);
  const changeType = (value: "OPENING" | "CLOSING") => {
    setBalanceType(value);
    setBalanceDate(value === "OPENING" ? previousISODate(period.start_date) : period.end_date);
    setFeedback("");
    setAdjustmentReason("");
  };
  const saveBalance = async () => {
    const bookedBalance = normalizedDecimal(amount);
    if (!bookedBalance || !balanceDate) return;
    if (deviatesFromSuggestion && !adjustmentReason.trim()) {
      setFeedback("Begründe die Abweichung vom automatisch berechneten Anfangssaldo.");
      return;
    }
    setSaving(true);
    setFeedback("");
    try {
      if (balanceType === "OPENING") {
        await financeBridge.command("RecordOpeningBalance", {
          account_id: account.account_id,
          balance_date: balanceDate,
          booked_balance: bookedBalance,
          available_balance: null,
          currency: account.currency,
          source: suggestion?.eligible ? "PREVIOUS_SYSTEM" : "MANUAL_ENTRY",
          confirmation: true,
          comment: suggestion?.eligible
            ? "Automatischen Vorschlag im Kontoarbeitsbereich bestätigt"
            : "Manuell im Kontoarbeitsbereich nachgetragen",
          opening_balance_source: suggestion?.eligible ? suggestion.source : "MANUAL",
          ...(suggestion?.eligible ? {
            suggested_value: String(suggestion.suggested_value),
            carry_forward_source_reconciliation_id: suggestion.source_reconciliation_id ?? null,
            source_period: suggestion.source_period ?? null,
            source_account_id: suggestion.source_account_id ?? account.account_id,
            source_projection_sequence: suggestion.source_projection_sequence ?? null,
          } : {}),
          ...(deviatesFromSuggestion ? { adjustment_reason: adjustmentReason.trim() } : {}),
        });
      } else {
        await financeBridge.command("RecordClosingBalance", {
          account_id: account.account_id,
          balance_date: balanceDate,
          booked_balance: bookedBalance,
          available_balance: null,
          currency: account.currency,
          source: "MANUAL_ENTRY",
          confirmation: true,
        });
      }
      const message = balanceType === "OPENING" ? "Anfangssaldo wurde nachgetragen." : "Endsaldo wurde nachgetragen.";
      setFeedback(message);
      setAmount("");
      setAdjustmentReason("");
      setRefreshToken((value) => value + 1);
      onMessage(message);
      onChanged();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Der Saldo konnte nicht gespeichert werden.");
    } finally {
      setSaving(false);
    }
  };
  const applySuggestion = async () => {
    if (!suggestion?.eligible || suggestion.suggested_value === undefined) return;
    setAcceptingSuggestion(true);
    setFeedback("");
    try {
      await financeBridge.command("RecordOpeningBalance", {
        account_id: account.account_id,
        balance_date: previousISODate(period.start_date),
        booked_balance: String(suggestion.suggested_value),
        available_balance: null,
        currency: suggestion.currency ?? account.currency,
        source: "PREVIOUS_SYSTEM",
        confirmation: true,
        comment: "Automatischen Vorschlag im Kontoarbeitsbereich bestätigt",
        suggested_value: String(suggestion.suggested_value),
        carry_forward_source_reconciliation_id: suggestion.source_reconciliation_id ?? null,
        opening_balance_source: suggestion.source,
        source_period: suggestion.source_period ?? null,
        source_account_id: suggestion.source_account_id ?? account.account_id,
        source_projection_sequence: suggestion.source_projection_sequence ?? null,
      });
      const message = `Anfangssaldo für ${period.display_label} wurde übernommen.`;
      setSuggestionAccepted(true);
      setFeedback(message);
      setRefreshToken((value) => value + 1);
      onMessage(message);
      onChanged();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "Der Vorschlag konnte nicht übernommen werden.");
    } finally {
      setAcceptingSuggestion(false);
    }
  };
  return <QueryBoundary result={result}>{(data) => {
    const openingRows = data.balance_history.filter((item) => item.balance_type === "OPENING");
    const latestOpening = openingRows.reduce<AccountBalanceLedgerRow | null>((latest, item) => !latest || item.sequence_number > latest.sequence_number ? item : latest, null);
    const suggestionAlreadyRecorded = Boolean(suggestion?.eligible && openingRows.some((item) => item.balance_date === previousISODate(period.start_date) && normalizedDecimal(String(item.reported_value ?? "")) === suggestedValue));
    return <section className="balance-ledger">
    {account.status !== "CLOSED" && suggestion?.eligible && <section className="panel automatic-balance-suggestion" aria-label="Automatischer Saldovorschlag">
      <div><span className="eyebrow">AUTOMATISCHER VORSCHLAG</span><h3>{money(suggestion.suggested_value, suggestion.currency ?? account.currency)}</h3><p>Als Anfangssaldo für {period.display_label}, übernommen aus dem {suggestion.source === "CALCULATED_PRIOR_CLOSING_BALANCE" ? "berechneten" : "bestätigten gemeldeten"} Endsaldo {monthLabel(suggestion.source_period)}.</p></div>
      {suggestion.calculation && <dl className="automatic-balance-calculation"><div><dt>Vorheriger Anfangssaldo</dt><dd>{money(suggestion.calculation.opening_balance, account.currency)}</dd></div><div><dt>Relevante Bewegungen</dt><dd>{money(suggestion.calculation.balance_relevant_movements, account.currency)}</dd></div><div><dt>Berechneter Endsaldo</dt><dd>{money(suggestion.calculation.calculated_closing_balance, account.currency)}</dd></div></dl>}
      <div className="automatic-balance-actions"><button className="secondary small" onClick={() => setSuggestionRefresh((value) => value + 1)}>Neu berechnen</button><button className="primary" disabled={acceptingSuggestion || suggestionAccepted || suggestionAlreadyRecorded} onClick={applySuggestion}>{acceptingSuggestion ? "Wird übernommen …" : suggestionAccepted || suggestionAlreadyRecorded ? "Bereits übernommen" : "Vorschlag übernehmen"}</button></div>
      {feedback && <p className="automatic-balance-feedback" role="status">{feedback}</p>}
    </section>}
    {account.status !== "CLOSED" && suggestion && !suggestion.eligible && <section className="panel automatic-balance-unavailable"><div><strong>Automatische Berechnung noch nicht möglich</strong><p>{OPENING_SUGGESTION_REASON[suggestion.reason_code] ?? "Für den Vormonat liegt noch keine sichere Berechnungsgrundlage vor."}</p></div><button className="secondary small" onClick={() => setSuggestionRefresh((value) => value + 1)}>Erneut prüfen</button></section>}
    {account.status !== "CLOSED" && <section className="panel manual-balance-entry" aria-label="Saldo nachtragen">
      <div><h3>Saldo nachtragen</h3><p>Erfasse einen fehlenden Anfangs- oder Endsaldo fachlich passend zum gewählten Zeitraum. Der Eintrag bleibt im Audit nachvollziehbar.</p></div>
      <div className="manual-balance-fields">
        <label className="balance-entry">Saldoart<select aria-label="Saldoart" value={balanceType} onChange={(event) => changeType(event.target.value as "OPENING" | "CLOSING")}><option value="OPENING">Anfangssaldo</option><option value="CLOSING">Endsaldo</option></select></label>
        <label className="balance-entry">Datum<input aria-label="Saldodatum" type="date" value={balanceDate} onChange={(event) => setBalanceDate(event.target.value)} /></label>
        <label className="balance-entry">Betrag in {account.currency}<input aria-label="Saldobetrag" inputMode="decimal" placeholder="0,00" value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
        <button className="primary" disabled={saving || !amount.trim() || !balanceDate} onClick={saveBalance}>{saving ? "Wird gespeichert …" : `${balanceType === "OPENING" ? "Anfangs" : "End"}saldo speichern`}</button>
      </div>
      {deviatesFromSuggestion && <label className="balance-adjustment-reason">Begründung für die Abweichung<textarea required value={adjustmentReason} onChange={(event) => setAdjustmentReason(event.target.value)} /></label>}
      {feedback && <p className="balance-entry-feedback" role="status">{feedback}</p>}
    </section>}
    {account.status === "CLOSED" && <div className="toast compact">Dieses geschlossene Konto bleibt lesbar; Salden können nicht mehr nachgetragen werden.</div>}
    {latestOpening && <article className="panel carry-forward-card"><div><span>Anfangssaldo {data.period.display_label}</span><strong>{money(latestOpening.reported_value, latestOpening.currency)}</strong>{openingRows.length > 1 && <small>{openingRows.length - 1} ältere Erfassung{openingRows.length === 2 ? "" : "en"} im Verlauf</small>}</div>{(latestOpening.opening_balance_source && latestOpening.opening_balance_source !== "MANUAL") || latestOpening.carry_forward_source_reconciliation_id ? <div><span>Übernommen aus</span><strong>{latestOpening.opening_balance_source === "CALCULATED_PRIOR_CLOSING_BALANCE" ? "berechnetem" : "bestätigtem"} Endsaldo {latestOpening.source_period ? monthLabel(latestOpening.source_period) : latestOpening.carry_forward_source_period_end ? monthLabel(latestOpening.carry_forward_source_period_end.slice(0, 7)) : "der Vorperiode"}</strong>{latestOpening.carry_forward_source_reported_balance && <small>{money(latestOpening.carry_forward_source_reported_balance, latestOpening.currency)}</small>}</div> : <div><span>Quelle</span><strong>{label(latestOpening.source)}</strong></div>}{latestOpening.calculated_value && latestOpening.calculated_value !== latestOpening.reported_value && <div className="balance-adjustment"><span>Vorgeschlagen</span><strong>{money(latestOpening.calculated_value, latestOpening.currency)}</strong><span>Erfasst</span><strong>{money(latestOpening.reported_value, latestOpening.currency)}</strong><span>Begründung</span><strong>{latestOpening.adjustment_reason ?? "Keine Begründung erfasst"}</strong></div>}<button className="secondary small" onClick={onAudit}>Im Audit anzeigen</button></article>}
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
        <td>{item.opening_balance_source === "CALCULATED_PRIOR_CLOSING_BALANCE" ? "Übernommen aus berechnetem Endsaldo" : item.opening_balance_source === "REPORTED_PRIOR_CLOSING_BALANCE" || item.carry_forward_source_reconciliation_id ? "Übernommen aus bestätigtem Endsaldo" : item.adjustment_reason ? `Angepasst: ${item.adjustment_reason}` : "–"}</td>
        <td><button className="table-link" onClick={onAudit}>Audit</button></td>
      </tr>)}</tbody>
    </table>
    </section>
  </section>;
  }}</QueryBoundary>;
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

function UnifiedFinanceWorkspace({ activeTab, onTabChange, inbox, month, periodPayload, navigationTarget, onNavigate }: { activeTab: WorkspaceTab; onTabChange: (tab: WorkspaceTab) => void; inbox: QueryResult<ReviewInbox>; month: string; periodPayload: Record<string, unknown>; navigationTarget: NavigationTarget; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const openReviews = inbox.envelope?.data.total_open ?? 0;
  return <section className="unified-finance-workspace">
    <header className="unified-workspace-header">
      <div><span className="eyebrow">GEMEINSAMER ARBEITSBEREICH</span><h2>{activeTab === "tasks" ? "Offene Aufgaben bearbeiten" : activeTab === "bookings" ? "Buchungen durchsuchen" : "Kategorien und Regeln verwalten"}</h2><p>{activeTab === "tasks" ? "Alle fachlichen Prüfungen aus den vorhandenen Projektionen an einem Ort." : activeTab === "bookings" ? "Filtern, prüfen und kategorisieren – ohne den Kontext zu verlieren." : "Struktur, eigene Kategorien und deterministische Regeln zentral pflegen."}</p></div>
      <span className="workspace-privacy">◇ Lokal verarbeitet · keine automatische AI-Übernahme</span>
    </header>
    <nav className="unified-workspace-tabs" aria-label="Bereiche des Arbeitsbereichs">
      <button className={activeTab === "tasks" ? "active" : ""} aria-current={activeTab === "tasks" ? "page" : undefined} onClick={() => onTabChange("tasks")}>Aufgaben{openReviews > 0 && <span>{openReviews}</span>}</button>
      <button className={activeTab === "bookings" ? "active" : ""} aria-current={activeTab === "bookings" ? "page" : undefined} onClick={() => onTabChange("bookings")}>Buchungen</button>
      <button className={activeTab === "categories" ? "active" : ""} aria-current={activeTab === "categories" ? "page" : undefined} onClick={() => onTabChange("categories")}>Kategorien &amp; Regeln</button>
    </nav>
    <div className={`unified-workspace-content mode-${activeTab}`}>
      {activeTab === "tasks" && <Workspace inbox={inbox} focusAccountId={navigationTarget?.page === "reviews" && navigationTarget.view === "account" ? navigationTarget.entity_id : undefined} onOpen={(target) => onNavigate(target.page, target)} />}
      {activeTab === "bookings" && <Transactions month={month} periodPayload={periodPayload} focusId={navigationTarget?.page === "transactions" ? navigationTarget.entity_id : undefined} initialView={navigationTarget?.page === "transactions" ? navigationTarget.view : undefined} onNavigate={onNavigate} />}
      {activeTab === "categories" && <Categories month={month} initialTab={navigationTarget?.page === "categories" ? navigationTarget.view : undefined} focusId={navigationTarget?.page === "categories" && navigationTarget.entity_id ? navigationTarget.entity_id : undefined} initialPeriodScope={navigationTarget?.page === "categories" && Boolean(navigationTarget.period)} />}
    </div>
  </section>;
}

function Transactions({ month, periodPayload, focusId, initialView, onNavigate }: { month: string; periodPayload: Record<string, unknown>; focusId?: string; initialView?: string; onNavigate: (page: PageId, target?: NavigationTarget) => void }) {
  const [refresh, setRefresh] = useState(0);
  const [cursor, setCursor] = useState<string | undefined>();
  const [loadedTransactions, setLoadedTransactions] = useState<Transaction[]>([]);
  const result = useFinanceQuery<{ transactions: Transaction[]; next_cursor?: string | null; has_more?: boolean }>("ListTransactions", { ...periodPayload, cursor, limit: 50, refresh });
  const categories = useFinanceQuery<CategoryList>("ListCategories", { refresh });
  const accounts = useFinanceQuery<{ accounts: Account[] }>("ListAccounts");
  const suggestions = useFinanceQuery<{ suggestions: CategorySuggestionRow[] }>("ListPendingCategorySuggestions", { limit: 200, refresh });
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [categoryOverrides, setCategoryOverrides] = useState<Record<string, string>>({});
  const [filters, setFilters] = useState({ account: "ALL", category: "ALL", direction: "ALL", minimum: "", maximum: "", classification: initialView === "uncategorized" ? "UNCLASSIFIED" : "ALL", ai: initialView === "ai_suggestions" ? "PENDING" : "ALL", importId: "ALL" });
  const [bulkCategory, setBulkCategory] = useState("");
  const catalogue = (categories.envelope?.data.categories ?? []).map(normalizeCategory);
  const accountRows = accounts.envelope?.data.accounts ?? [];
  const accountNames = Object.fromEntries(accountRows.map((item) => [item.account_id, item.display_name]));
  const pendingSuggestions = suggestions.envelope?.data.suggestions ?? [];
  const suggestionByTransaction = new Map(pendingSuggestions.map((item) => [item.transaction_id, item]));
  const periodSignature = JSON.stringify(periodPayload);
  useEffect(() => { setCursor(undefined); setLoadedTransactions([]); }, [periodSignature, refresh]);
  useEffect(() => {
    const page = result.envelope?.data.transactions;
    if (!page) return;
    setLoadedTransactions((current) => cursor
      ? [...current, ...page.filter((item) => !current.some((existing) => existing.transaction_id === item.transaction_id))]
      : page);
  }, [cursor, result.envelope]);
  useEffect(() => {
    if (!focusId || !result.envelope) return;
    const transaction = result.envelope.data.transactions.find((item) => item.transaction_id === focusId);
    if (transaction) setSelected({ ...transaction, category_code: categoryOverrides[transaction.transaction_id] ?? transaction.category_code });
  }, [focusId, result.envelope, categoryOverrides]);
  const updateFilter = (name: keyof typeof filters, value: string) => setFilters((current) => ({ ...current, [name]: value }));
  const classify = async () => { await financeBridge.command("ClassifyTransactions", { month }); setMessage("Regeln wurden angewendet."); setRefresh((value) => value + 1); };
  const setBulkClassification = async () => {
    if (!bulkCategory || selectedIds.size === 0) return;
    for (const transactionId of selectedIds) await financeBridge.command("ConfirmClassification", { transaction_id: transactionId, category_code: bulkCategory });
    setCategoryOverrides((current) => ({ ...current, ...Object.fromEntries(Array.from(selectedIds).map((id) => [id, bulkCategory])) }));
    setMessage(`${selectedIds.size} Transaktionen wurden kategorisiert.`); setSelectedIds(new Set()); setRefresh((value) => value + 1);
  };
  return <QueryBoundary result={result}>{({ transactions: currentPage, next_cursor, has_more }) => {
    const transactions = loadedTransactions.length || cursor ? loadedTransactions : currentPage;
    const importIds = Array.from(new Set(transactions.map((item) => item.export_id).filter(Boolean))) as string[];
    const visible = transactions.filter((item) => {
      const category = categoryOverrides[item.transaction_id] ?? item.category_code;
      const amount = Number(item.amount);
      const minimum = Number(filters.minimum.replace(",", "."));
      const maximum = Number(filters.maximum.replace(",", "."));
      return (filters.account === "ALL" || item.account_id === filters.account)
        && (filters.category === "ALL" || category === filters.category)
        && (filters.direction === "ALL" || (filters.direction === "INCOME" ? amount >= 0 : amount < 0))
        && (!filters.minimum || (!Number.isNaN(minimum) && Math.abs(amount) >= minimum))
        && (!filters.maximum || (!Number.isNaN(maximum) && Math.abs(amount) <= maximum))
        && (filters.classification === "ALL" || (filters.classification === "CLASSIFIED" ? category !== "UNCLASSIFIED" : category === "UNCLASSIFIED"))
        && (filters.ai === "ALL" || (filters.ai === "PENDING" ? suggestionByTransaction.has(item.transaction_id) : !suggestionByTransaction.has(item.transaction_id)))
        && (filters.importId === "ALL" || item.export_id === filters.importId);
    });
    const allVisibleSelected = visible.length > 0 && visible.every((item) => selectedIds.has(item.transaction_id));
    return <>
      <section className="section-intro"><div><h2>Transaktionsarbeitsplatz</h2><p>{visible.length} Buchungen · Filter und Auswahl bleiben beim Öffnen des Detailpanels erhalten.</p></div><button className="secondary" onClick={classify}>Regeln anwenden</button></section>
      {message && <div className="toast" role="status">{message}</div>}
      <section className="panel transaction-filters" aria-label="Transaktionen filtern">
        <label>Konto<select value={filters.account} onChange={(event) => updateFilter("account", event.target.value)}><option value="ALL">Alle Konten</option>{accountRows.map((item) => <option key={item.account_id} value={item.account_id}>{item.display_name}</option>)}</select></label>
        <label>Kategorie<select value={filters.category} onChange={(event) => updateFilter("category", event.target.value)}><option value="ALL">Alle Kategorien</option>{catalogue.map((item) => <option key={item.category_code} value={item.category_code}>{item.display_name}</option>)}</select></label>
        <label>Richtung<select value={filters.direction} onChange={(event) => updateFilter("direction", event.target.value)}><option value="ALL">Einnahmen und Ausgaben</option><option value="INCOME">Einnahmen</option><option value="EXPENSE">Ausgaben</option></select></label>
        <label>Betrag von<input inputMode="decimal" value={filters.minimum} onChange={(event) => updateFilter("minimum", event.target.value)} placeholder="0,00" /></label>
        <label>Betrag bis<input inputMode="decimal" value={filters.maximum} onChange={(event) => updateFilter("maximum", event.target.value)} placeholder="beliebig" /></label>
        <label>Kategorisierung<select value={filters.classification} onChange={(event) => updateFilter("classification", event.target.value)}><option value="ALL">Alle</option><option value="CLASSIFIED">Kategorisiert</option><option value="UNCLASSIFIED">Nicht kategorisiert</option></select></label>
        <label>AI-Vorschlag<select value={filters.ai} onChange={(event) => updateFilter("ai", event.target.value)}><option value="ALL">Alle</option><option value="PENDING">Vorschlag offen</option><option value="NONE">Ohne Vorschlag</option></select></label>
        <label>Import<select value={filters.importId} onChange={(event) => updateFilter("importId", event.target.value)}><option value="ALL">Alle Importe</option>{importIds.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
        <button className="text-button" onClick={() => setFilters({ account: "ALL", category: "ALL", direction: "ALL", minimum: "", maximum: "", classification: "ALL", ai: "ALL", importId: "ALL" })}>Filter zurücksetzen</button>
      </section>
      {selectedIds.size > 0 && <section className="panel bulk-bar" aria-label="Mehrfachauswahl"><strong>{selectedIds.size} ausgewählt</strong><select aria-label="Kategorie für Auswahl" value={bulkCategory} onChange={(event) => setBulkCategory(event.target.value)}><option value="">Kategorie wählen</option>{catalogue.filter((item) => item.status === "ACTIVE").map((item) => <option value={item.category_code} key={item.category_code}>{item.display_name}</option>)}</select><button className="primary small" disabled={!bulkCategory} onClick={setBulkClassification}>Kategorie setzen</button><button className="secondary small" disabled={!Array.from(selectedIds).some((id) => suggestionByTransaction.has(id))} onClick={() => { const id = Array.from(selectedIds).find((candidate) => suggestionByTransaction.has(candidate)); const item = transactions.find((transaction) => transaction.transaction_id === id); if (item) setSelected({ ...item, category_code: categoryOverrides[item.transaction_id] ?? item.category_code }); }}>AI-Vorschläge prüfen</button><small>Relationen und Salden können nicht gesammelt geändert werden.</small></section>}
      <section className="panel table-panel transaction-worktable"><table><thead><tr><th><input aria-label="Alle sichtbaren Transaktionen markieren" type="checkbox" checked={allVisibleSelected} onChange={() => setSelectedIds(allVisibleSelected ? new Set() : new Set(visible.map((item) => item.transaction_id)))} /></th><th>Datum</th><th>Händler</th><th>Beschreibung</th><th>Kategorie</th><th>Konto</th><th className="numeric">Betrag</th><th>Status</th></tr></thead><tbody>{visible.map((item) => { const category = categoryOverrides[item.transaction_id] ?? item.category_code; const suggestion = suggestionByTransaction.get(item.transaction_id); return <tr key={item.transaction_id} tabIndex={0} className={selected?.transaction_id === item.transaction_id ? "active-row" : ""} onKeyDown={(event) => { if (event.key === "Enter") setSelected({ ...item, category_code: category }); if (event.key === " ") { event.preventDefault(); setSelectedIds((current) => { const next = new Set(current); next.has(item.transaction_id) ? next.delete(item.transaction_id) : next.add(item.transaction_id); return next; }); } }} onDoubleClick={() => setSelected({ ...item, category_code: category })}><td><input aria-label={`${item.counterparty} markieren`} type="checkbox" checked={selectedIds.has(item.transaction_id)} onChange={() => setSelectedIds((current) => { const next = new Set(current); next.has(item.transaction_id) ? next.delete(item.transaction_id) : next.add(item.transaction_id); return next; })} /></td><td>{date(item.booking_date)}</td><td><button className="table-link" onClick={() => setSelected({ ...item, category_code: category })}>{item.counterparty || "Unbekannter Händler"}</button></td><td>{item.description || item.normalized_description || "–"}</td><td><span className={`category-tag ${category === "UNCLASSIFIED" ? "warn" : ""}`}>{categoryLabel(category)}</span></td><td>{accountNames[item.account_id ?? ""] ?? item.account_id ?? "–"}</td><td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td><td><span className={`status-badge ${suggestion ? "paused" : category === "UNCLASSIFIED" ? "missed" : "confirmed"}`}>{suggestion ? "AI-Vorschlag offen" : category === "UNCLASSIFIED" ? "Kategorie offen" : "Kategorisiert"}</span></td></tr>; })}</tbody></table>{has_more && next_cursor && <footer className="table-pagination"><button className="secondary" onClick={() => setCursor(next_cursor)}>Weitere 50 laden</button></footer>}</section>
      {selected && <TransactionSidePanel transaction={selected} accountName={accountNames[selected.account_id ?? ""]} suggestion={suggestionByTransaction.get(selected.transaction_id)} categories={catalogue} onClose={() => setSelected(null)} onOpenImport={(exportId) => onNavigate("imports", { page: "imports", view: "detail", entity_id: exportId })} onSaved={(category) => { setCategoryOverrides((current) => ({ ...current, [selected.transaction_id]: category })); setSelected((current) => current ? { ...current, category_code: category } : current); setMessage("Entscheidung wurde gespeichert."); setRefresh((value) => value + 1); }} />}
    </>;
  }}</QueryBoundary>;
}

function TransactionSidePanel({ transaction, accountName, suggestion, categories, onClose, onOpenImport, onSaved }: { transaction: Transaction; accountName?: string; suggestion?: CategorySuggestionRow; categories: Category[]; onClose: () => void; onOpenImport: (exportId: string) => void; onSaved: (category: string) => void }) {
  const close = useRef<HTMLButtonElement>(null);
  const details = useFinanceQuery<Record<string, unknown>>("GetTransactionDetails", { transaction_id: transaction.transaction_id });
  const [category, setCategory] = useState(transaction.category_code);
  const [creating, setCreating] = useState(false);
  const [newCategory, setNewCategory] = useState("");
  const [localCategories, setLocalCategories] = useState(categories);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const detail = details.envelope?.data ?? {};
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; close.current?.focus(); return () => previous?.focus(); }, []);
  const categoryOptions = localCategories.filter((item) => item.status === "ACTIVE" || item.category_code === transaction.category_code);
  const addCategory = async () => {
    if (!newCategory.trim()) { setFeedback("Gib einen Namen für die neue Kategorie ein."); return; }
    setBusy(true);
    try { const response = await financeBridge.command("CreateCategory", { display_name: newCategory, category_type: Number(transaction.amount) >= 0 ? "INCOME" : "EXPENSE" }) as { result?: Category }; if (!response.result) throw new Error("Kategorie konnte nicht angelegt werden."); setLocalCategories((current) => [...current, response.result as Category]); setCategory(response.result.category_code); setCreating(false); setNewCategory(""); }
    catch (error) { setFeedback(error instanceof Error ? error.message : "Kategorie konnte nicht angelegt werden."); }
    finally { setBusy(false); }
  };
  const saveCategory = async (createRule = false) => {
    if (!category || category === "UNCLASSIFIED") { setFeedback("Wähle zuerst eine Kategorie."); return; }
    setBusy(true);
    try { await financeBridge.command("ConfirmClassification", { transaction_id: transaction.transaction_id, category_code: category }); if (createRule && transaction.counterparty.trim()) await financeBridge.command("CreateClassificationRule", { field: "counterparty", operator: "EQUALS", value: transaction.counterparty, category_code: category, priority: 250 }); onSaved(category); setFeedback(createRule ? "Kategorie und Händlerregel wurden gespeichert." : "Kategorie wurde gespeichert."); }
    catch (error) { setFeedback(error instanceof Error ? error.message : "Kategorie konnte nicht gespeichert werden."); }
    finally { setBusy(false); }
  };
  const decideSuggestion = async (action: "confirm" | "correct" | "reject") => {
    if (!suggestion) return;
    setBusy(true);
    try { const command = action === "confirm" ? "ConfirmCategorySuggestion" : action === "correct" ? "CorrectCategorySuggestion" : "RejectCategorySuggestion"; await financeBridge.command(command, action === "correct" ? { suggestion_id: suggestion.suggestion_id, selected_category: category } : { suggestion_id: suggestion.suggestion_id }); onSaved(action === "reject" ? "UNCLASSIFIED" : action === "confirm" ? suggestion.category_code : category); setFeedback(action === "reject" ? "AI-Vorschlag wurde abgelehnt." : "AI-Vorschlag wurde übernommen."); }
    catch (error) { setFeedback(error instanceof Error ? error.message : "AI-Vorschlag konnte nicht verarbeitet werden."); }
    finally { setBusy(false); }
  };
  const reconciliation = (detail.reconciliation as Record<string, unknown> | undefined) ?? {};
  const eventHistory = (detail.event_history as Array<{ event_id?: string; sequence_number?: number; event_type: string; occurred_at: string }> | undefined) ?? [];
  return <aside className="transaction-sidepanel" role="dialog" aria-modal="false" aria-labelledby="transaction-title" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}><button ref={close} className="dialog-close" onClick={onClose} aria-label="Detailpanel schließen">×</button><span className="eyebrow">TRANSAKTION</span><div className="transaction-dialog-head"><div><h2 id="transaction-title">{transaction.counterparty || "Unbekannter Händler"}</h2><p>{transaction.description || transaction.normalized_description || "Keine Beschreibung"}</p></div><strong className="dialog-amount">{money(transaction.amount, transaction.currency)}</strong></div><dl className="transaction-facts compact"><div><dt>Datum</dt><dd>{date(transaction.booking_date)}</dd></div><div><dt>Konto</dt><dd>{accountName ?? transaction.account_id ?? "–"}</dd></div><div><dt>Händler</dt><dd>{transaction.counterparty || "–"}</dd></div><div><dt>Beschreibung</dt><dd>{transaction.description || transaction.normalized_description || "–"}</dd></div></dl><section className="sidepanel-section"><h3>Kategorie</h3><div className="sidepanel-category"><select aria-label="Kategorie" value={category} onChange={(event) => setCategory(event.target.value)}>{categoryOptions.map((item) => <option value={item.category_code} key={item.category_code}>{item.display_name}</option>)}</select><button className="primary small" disabled={busy || category === "UNCLASSIFIED"} onClick={() => saveCategory(false)}>Kategorie ändern</button><button className="secondary small" disabled={busy || category === "UNCLASSIFIED"} onClick={() => saveCategory(true)}>Regel erstellen</button><button className="text-button" onClick={() => setCreating((value) => !value)}>Neue Kategorie</button></div>{creating && <div className="new-category-row"><input aria-label="Name der neuen Kategorie" value={newCategory} onChange={(event) => setNewCategory(event.target.value)} /><button className="secondary small" onClick={addCategory}>Anlegen</button></div>}</section><section className="sidepanel-section ai-assist"><h3>AI-Vorschlag</h3>{suggestion ? <><p><strong>{categoryLabel(suggestion.category_code)}</strong> · {percent(suggestion.raw_confidence)}</p><small>Lokaler Vorschlag – erst deine Entscheidung wirkt fachlich.</small><div className="sidepanel-actions"><button className="primary small" disabled={busy} onClick={() => decideSuggestion("confirm")}>Bestätigen</button><button className="secondary small" disabled={busy || category === suggestion.category_code} onClick={() => decideSuggestion("correct")}>Korrigieren</button><button className="secondary small" disabled={busy} onClick={() => decideSuggestion("reject")}>Ablehnen</button></div></> : <p>Kein offener AI-Vorschlag.</p>}</section>{feedback && <div className="toast compact" role="status">{feedback}</div>}<section className="sidepanel-section"><h3>Relationen</h3><dl className="transaction-facts compact"><div><dt>Dublette</dt><dd>{relationStatusLabel(String(reconciliation.duplicate_status ?? transaction.duplicate_status ?? "NONE"))}</dd></div><div><dt>Transfer</dt><dd>{relationStatusLabel(String(reconciliation.transfer_status ?? transaction.transfer_status ?? "NONE"))}</dd></div><div><dt>Rückerstattung</dt><dd>{relationStatusLabel(String(reconciliation.refund_status ?? transaction.refund_status ?? "NONE"))}</dd></div></dl><small>Sicherheitskritische Relationen können hier nicht gesammelt geändert werden.</small></section><section className="sidepanel-section"><h3>Importquelle</h3>{transaction.export_id ? <button className="secondary small" onClick={() => onOpenImport(transaction.export_id!)}>Import öffnen</button> : <p>Keine Importquelle hinterlegt.</p>}</section><section className="sidepanel-section"><details open><summary>Audit · {eventHistory.length} Ereignisse</summary><div className="sidepanel-audit">{eventHistory.map((event, index) => <div key={event.event_id ?? `${event.sequence_number ?? index}-${event.event_type}`}><strong>{eventLabel(event.event_type).title}</strong><small>{date(event.occurred_at)}</small></div>)}</div></details></section></aside>;
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

function Categories({ month, initialTab, focusId, initialPeriodScope = false }: { month: string; initialTab?: string; focusId?: string; initialPeriodScope?: boolean }) {
  const initialCategoryTab = (["overview", "manage", "suggestions", "rules"] as string[]).includes(initialTab ?? "") ? initialTab as "overview" | "manage" | "suggestions" | "rules" : "overview";
  const [tab, setTab] = useState<"overview" | "manage" | "suggestions" | "rules">(initialCategoryTab);
  const [confidence, setConfidence] = useState<"all" | "high" | "medium" | "low">("all");
  const [suggestionScope, setSuggestionScope] = useState<"all" | "period">(initialPeriodScope ? "period" : "all");
  const [refresh, setRefresh] = useState(0);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [remember, setRemember] = useState<Record<string, boolean>>({});
  const [resolvedSuggestionIds, setResolvedSuggestionIds] = useState<Set<string>>(new Set());
  const breakdown = useFinanceQuery<{ categories: Record<string, { category_code: string; effective_expense: string; transaction_count: number }> }>("GetCategoryBreakdown", { month, refresh }, tab === "overview");
  const summary = useFinanceQuery<{ unclassified_count: number; pending_count: number; confirmed_today_count: number; corrected_count: number; stale_count: number }>("GetCategorySuggestionReviewSummary", { refresh });
  const confidenceFilter = confidence === "high" ? { confidence_min: 0.95 } : confidence === "medium" ? { confidence_min: 0.9, confidence_max: 0.949999 } : confidence === "low" ? { confidence_max: 0.899999 } : {};
  const suggestions = useFinanceQuery<{ suggestions: CategorySuggestionRow[]; has_more?: boolean }>("ListPendingCategorySuggestions", { ...(suggestionScope === "period" ? { period: month } : {}), ...confidenceFilter, limit: 200, refresh }, tab === "suggestions");
  const rules = useFinanceQuery<{ rules: Array<{ rule_id: string; field: string; value: string; category_code: string; source: string }> }>("ListClassificationRules", { refresh }, tab === "rules");
  const catalogue = useFinanceQuery<CategoryList>("ListCategories", { refresh }, tab === "manage");
  const decide = async (item: CategorySuggestionRow, action: "confirm" | "correct" | "reject") => {
    setMessage("");
    let decisionCommitted = false;
    try {
      const command = action === "confirm" ? "ConfirmCategorySuggestion" : action === "correct" ? "CorrectCategorySuggestion" : "RejectCategorySuggestion";
      const payload = action === "correct" ? { suggestion_id: item.suggestion_id, selected_category: selected[item.suggestion_id] ?? item.category_code } : { suggestion_id: item.suggestion_id };
      await financeBridge.command(command, payload);
      decisionCommitted = true;
      setResolvedSuggestionIds((current) => new Set([...current, item.suggestion_id]));
      const decisionMessage = action === "confirm" ? "Kategorie-Vorschlag bestätigt." : action === "correct" ? "Kategorie-Vorschlag korrigiert." : "Kategorie-Vorschlag abgelehnt. Die Transaktion bleibt unklassifiziert.";
      if (action !== "reject" && remember[item.suggestion_id]) {
        try {
          await financeBridge.command("CreateClassificationRuleFromSuggestion", { suggestion_id: item.suggestion_id, field: "counterparty" });
          setMessage(`${decisionMessage} Die Regel für ähnliche Transaktionen wurde angelegt.`);
        } catch (error) {
          const reason = error instanceof Error ? error.message : "Unbekannter Fehler";
          setMessage(`${decisionMessage} Die Regel konnte nicht angelegt werden: ${reason}`);
        }
      } else setMessage(decisionMessage);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Der Vorschlag konnte nicht verarbeitet werden."); }
    finally {
      if (decisionCommitted) {
        setRefresh((value) => value + 1);
        setSelected((current) => { const next = { ...current }; delete next[item.suggestion_id]; return next; });
        setRemember((current) => { const next = { ...current }; delete next[item.suggestion_id]; return next; });
      }
    }
  };
  return <>
    <div className="tabs" role="tablist" aria-label="Kategorien"><button role="tab" aria-selected={tab === "overview"} onClick={() => setTab("overview")}>Übersicht</button><button role="tab" aria-selected={tab === "manage"} onClick={() => setTab("manage")}>Verwalten</button><button role="tab" aria-selected={tab === "suggestions"} onClick={() => setTab("suggestions")}>Vorschläge <span className="tab-count">{summary.envelope?.data.pending_count ?? 0}</span></button><button role="tab" aria-selected={tab === "rules"} onClick={() => setTab("rules")}>Regeln</button></div>
    {message && <div className="toast" role="status">{message}</div>}
    {focusId && tab === "suggestions" && <div className="toast compact" role="status">Die offenen AI-Vorschläge wurden für die ausgewählte Prüfung geöffnet.</div>}
    {focusId && tab === "manage" && <div className="toast compact" role="status">Die Kategorie „{categoryLabel(focusId)}“ wurde aus der Kontotransaktion geöffnet.</div>}
    {tab === "overview" && <QueryBoundary result={breakdown}>{({ categories }) => { const rows = Object.values(categories).sort((a, b) => Number(b.effective_expense) - Number(a.effective_expense)); const total = rows.reduce((sum, item) => sum + Number(item.effective_expense), 0); return <><section className="suggestion-summary"><Metric label="Unklassifiziert" value={String(summary.envelope?.data.unclassified_count ?? 0)} tone="warn" note="manuell oder per Vorschlag prüfbar" /><Metric label="AI-Vorschläge offen" value={String(summary.envelope?.data.pending_count ?? 0)} tone="accent" note="keine automatische Wirkung" /><Metric label="Heute bestätigt" value={String(summary.envelope?.data.confirmed_today_count ?? 0)} tone="positive" note="durch Nutzerentscheidung" /><Metric label="Korrekturen" value={String(summary.envelope?.data.corrected_count ?? 0)} tone="ink" note="AI-Vorschlag geändert" /></section><section className="section-intro"><div><h2>Ausgaben nach Kategorien</h2><p>Nur bestätigte Klassifikationen; offene AI-Vorschläge verändern diese Auswertung nicht.</p></div><strong className="section-total">{money(String(total))}<small>effektive Ausgaben</small></strong></section><section className="panel category-list">{rows.map((item, index) => <div className="category-row" key={item.category_code}><span className={`category-symbol color-${index % 5}`}>{item.category_code.slice(0, 2)}</span><div><strong>{categoryLabel(item.category_code)}</strong><small>{item.transaction_count} Buchungen</small></div><div className="bar"><span style={{ width: `${Math.max(4, total ? Number(item.effective_expense) / total * 100 : 0)}%` }} /></div><strong>{money(item.effective_expense)}</strong></div>)}</section></>; }}</QueryBoundary>}
    {tab === "manage" && <QueryBoundary result={catalogue}>{({ categories }) => <CategoryManagement categories={categories.map(normalizeCategory)} onChanged={() => setRefresh((value) => value + 1)} onMessage={setMessage} />}</QueryBoundary>}
    {tab === "suggestions" && <><section className="section-intro"><div><h2>AI-Kategorievorschläge prüfen</h2><p>Vorschläge des lokalen Modells werden erst durch deine Bestätigung oder Korrektur fachlich wirksam.</p></div><span className="privacy-badge">Lokal · kein Auto-Accept</span></section><div className="suggestion-toolbar"><div className="suggestion-filters" role="group" aria-label="Zeitraum der Vorschläge"><button className={suggestionScope === "all" ? "active" : ""} onClick={() => setSuggestionScope("all")}>Alle offenen ({summary.envelope?.data.pending_count ?? 0})</button><button className={suggestionScope === "period" ? "active" : ""} onClick={() => setSuggestionScope("period")}>Nur {monthLabel(month)}</button></div><div className="suggestion-filters" role="group" aria-label="Modell-Confidence filtern">{([['all','Alle Confidence-Werte'],['high','> 95 %'],['medium','90–95 %'],['low','< 90 %']] as const).map(([value, text]) => <button key={value} className={confidence === value ? "active" : ""} onClick={() => setConfidence(value)}>{text}</button>)}</div></div><QueryBoundary result={suggestions} renderEmpty>{({ suggestions: rows }) => { const visibleRows = rows.filter((item) => !resolvedSuggestionIds.has(item.suggestion_id)); const orderedRows = focusId ? [...visibleRows].sort((left, right) => Number(right.suggestion_id === focusId) - Number(left.suggestion_id === focusId)) : visibleRows; return <section className="suggestion-review-list">{orderedRows.length === 0 ? <div className="panel empty-state"><strong>{suggestionScope === "period" ? `Keine offenen Vorschläge im ${monthLabel(month)}` : "Keine offenen Vorschläge"}</strong><p>{suggestionScope === "period" ? "Wechsle zu „Alle offenen“, um Vorschläge aus anderen Zeiträumen zu prüfen." : "Manuelle Kategorisierung und bestehende Regeln bleiben jederzeit verfügbar."}</p></div> : orderedRows.map((item) => <article className={`panel suggestion-review-card ${item.suggestion_id === focusId ? "focused" : ""}`} key={item.suggestion_id}><div className="suggestion-transaction"><div><span className="eyebrow">LOKALES MODELL</span><h3>{item.merchant || "Unbekannter Anbieter"}</h3><p>{item.description || "Keine Beschreibung"}</p></div><strong className="amount">{money(item.amount, item.currency)}</strong></div><div className="suggestion-proposal"><div><small>Vorgeschlagene Kategorie</small><strong>{categoryLabel(item.category_code)}</strong></div><div><small>Modell-Confidence</small><strong>{percent(item.raw_confidence)}</strong></div><div><small>Modell</small><strong>{item.model_id} {item.model_version}</strong></div></div><details><summary>Details zum Vorschlag</summary><div className="suggestion-details"><div><h4>Begründungsmerkmale</h4><ul>{item.reason_codes.map((reason) => <li key={reason}>{reasonLabels[reason] ?? label(reason)}</li>)}</ul></div><div><h4>Alternativen</h4>{item.alternatives.map((alternative) => <p key={alternative.category_code}><span>{categoryLabel(alternative.category_code)}</span><b>{percent(alternative.confidence)}</b></p>)}</div></div>{item.historical_accuracy ? <p className="calibration-note">Historische Genauigkeit dieses Modells ab {Number(item.historical_accuracy.threshold) * 100} %: <strong>{percent(item.historical_accuracy.accuracy)}</strong> bei {item.historical_accuracy.sample_count} Fällen</p> : <p className="calibration-note">Noch nicht genügend lokale Vergleichsdaten.</p>}</details><div className="suggestion-decision"><select aria-label={`Kategorie für ${item.merchant}`} value={selected[item.suggestion_id] ?? item.category_code} onChange={(event) => setSelected((current) => ({ ...current, [item.suggestion_id]: event.target.value }))}>{Array.from(new Set([item.category_code, ...item.alternatives.map((alternative) => alternative.category_code)])).map((code) => <option key={code} value={code}>{categoryLabel(code)}</option>)}</select><label><input type="checkbox" checked={remember[item.suggestion_id] ?? false} onChange={(event) => setRemember((current) => ({ ...current, [item.suggestion_id]: event.target.checked }))} /> Für ähnliche Transaktionen merken</label><div><button className="primary small" onClick={() => decide(item, (selected[item.suggestion_id] ?? item.category_code) === item.category_code ? "confirm" : "correct")}>{(selected[item.suggestion_id] ?? item.category_code) === item.category_code ? "Bestätigen" : "Geänderte Kategorie übernehmen"}</button><button className="secondary small" onClick={() => decide(item, "reject")}>Ablehnen</button></div></div></article>)}</section>; }}</QueryBoundary></>}
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

function Workspace({ inbox, focusAccountId, onOpen }: { inbox: QueryResult<ReviewInbox>; focusAccountId?: string; onOpen: (target: ReviewItem["action_target"]) => void }) {
  const [filter, setFilter] = useState<ReviewType | "ALL">("ALL");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const data = inbox.envelope?.data;
  const accountItems = data?.items.filter((item) => !focusAccountId || item.account_ids.includes(focusAccountId)) ?? [];
  const items = accountItems.filter((item) => filter === "ALL" || item.review_type === filter);
  const visibleCounts = (Object.keys(REVIEW_LABELS) as ReviewType[]).reduce((counts, type) => ({ ...counts, [type]: focusAccountId ? accountItems.filter((item) => item.review_type === type).length : data?.items_by_type[type] ?? 0 }), {} as Record<ReviewType, number>);
  const totalOpen = focusAccountId ? accountItems.length : data?.total_open ?? 0;
  const affectedAccounts = new Set(accountItems.flatMap((item) => item.account_ids)).size;
  const urgent = accountItems.filter((item) => item.severity === "ERROR").length;
  const boundary = { ...inbox, state: inbox.state === "EMPTY" ? "READY" as const : inbox.state };
  const selected = items.find((item) => item.review_id === selectedId) ?? items[0] ?? null;
  return <QueryBoundary result={boundary}>{() => <>
    <section className={`panel workspace-status ${totalOpen > 0 ? "attention" : "complete"}`} aria-label="Arbeitsstand"><span className="workspace-status-icon" aria-hidden="true">{totalOpen > 0 ? "!" : "✓"}</span><div><span className="eyebrow">ARBEITSSTAND</span><h3>{totalOpen > 0 ? `${totalOpen} Aufgaben benötigen Aufmerksamkeit` : "Alles ist geprüft"}</h3><p>{focusAccountId ? "Gefiltert auf das ausgewählte Konto." : "Die Aufgaben werden direkt aus den vorhandenen Finance-Projektionen aufgebaut."}</p></div><dl className="workspace-status-metrics"><div><dt>Offene Aufgaben</dt><dd>{totalOpen}</dd></div><div><dt>Betroffene Konten</dt><dd>{affectedAccounts}</dd></div><div><dt>Dringend</dt><dd>{urgent}</dd></div></dl></section>
    <div className="workspace-inbox-filters" role="group" aria-label="Aufgaben filtern"><button className={filter === "ALL" ? "active" : ""} onClick={() => setFilter("ALL")}>Alle · {totalOpen}</button>{(Object.keys(REVIEW_LABELS) as ReviewType[]).map((type) => <button key={type} className={filter === type ? "active" : ""} onClick={() => setFilter(filter === type ? "ALL" : type)}>{REVIEW_LABELS[type]} <span>{visibleCounts[type]}</span></button>)}</div>
    <div className={`workspace-inbox-split ${selected ? "detail-open" : ""}`}>
      <section className="panel workspace-inbox-list" aria-label="Offene Aufgaben">
        <header><strong>{items.length} {items.length === 1 ? "offene Aufgabe" : "offene Aufgaben"}</strong><span>{focusAccountId ? "Ausgewähltes Konto" : "Alle Konten"}</span></header>
        {items.length === 0 ? <div className="page-state workspace-empty"><span className="empty-icon">✓</span><h3>Alles erledigt</h3><p>In diesem Bereich sind keine offenen Aufgaben vorhanden.</p></div> : <><div className="workspace-inbox-columns"><span>Typ</span><span>Aufgabe</span><span>Status</span><span>Aktion</span></div>{items.map((item) => <button className={`workspace-inbox-row ${selected?.review_id === item.review_id ? "selected" : ""}`} key={item.review_id} onClick={() => setSelectedId(item.review_id)}><span className={`task-kind ${item.severity.toLowerCase()}`}>{REVIEW_LABELS[item.review_type]}</span><span><strong>{item.title}</strong><small>{item.description}</small></span><span className={`status-badge ${item.severity === "ERROR" ? "missed" : item.severity === "WARNING" ? "paused" : "confirmed"}`}>{item.severity === "ERROR" ? "Dringend" : item.severity === "WARNING" ? "Prüfung offen" : "Hinweis"}</span><b>Öffnen →</b></button>)}</>}
      </section>
      {selected && <aside className="workspace-review-detail" aria-label="Aufgabendetail"><header><div><span className="eyebrow">AUFGABENDETAIL</span><h2>{REVIEW_LABELS[selected.review_type]}</h2></div><button className="dialog-close" onClick={() => setSelectedId(null)} aria-label="Detail schließen">×</button></header><div className="workspace-review-detail-body"><span className={`status-badge ${selected.severity === "ERROR" ? "missed" : selected.severity === "WARNING" ? "paused" : "confirmed"}`}>{selected.severity === "ERROR" ? "Dringend" : selected.severity === "WARNING" ? "Prüfung offen" : "Hinweis"}</span><h3>{selected.title}</h3><p>{selected.description}</p><dl><div><dt>Zeitraum</dt><dd>{monthLabel(reviewMonth(selected))}</dd></div><div><dt>Betroffene Konten</dt><dd>{selected.account_ids.length || "–"}</dd></div><div><dt>Erstellt</dt><dd>{date(selected.created_at)}</dd></div></dl><details><summary>Technische Details</summary><code>{selected.entity_type} · {selected.entity_id}</code></details></div><footer><button className="primary" onClick={() => onOpen(selected.action_target)}>Aufgabe bearbeiten →</button></footer></aside>}
    </div>
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
