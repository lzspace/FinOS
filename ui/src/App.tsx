import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { financeBridge, SchemaCompatibilityError } from "./bridge";
import type {
  CapabilityManifest,
  Account,
  BackupRecord,
  Dashboard,
  Envelope,
  ExpectedTransaction,
  ForecastScenario,
  LiquidityOverview,
  NetWorthOverview,
  KeyStatus,
  MigrationStatus,
  RecurringPattern,
  RuntimeSecurityStatus,
  StoreIntegrity,
  StartupState,
  StartupStatus,
  Transaction,
  ViewState,
} from "./contracts/generated";

type PageId = "overview" | "accounts" | "transactions" | "categories" | "recurring" | "forecast" | "wealth" | "reviews" | "imports" | "settings";
type QueryResult<T> = { state: ViewState; envelope?: Envelope<T>; error?: string };

const money = (value: string | null | undefined, currency = "EUR") =>
  value == null
    ? "–"
    : new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(Number(value));
const date = (value: string) => new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/(^|\s)\S/g, (c) => c.toUpperCase());

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
  const [month, setMonth] = useState("2026-07");
  const enabled = manifest.envelope?.data.capabilities ?? {};
  const visibleNavigation = navigation.filter((item) => !item.capability || enabled[item.capability]);

  if (startup.state === "LOADING") return <FullState state="LOADING" />;
  if (!startup.envelope || startup.envelope.data.status !== "READY") {
    return <CriticalState status={startup.envelope?.data.status ?? "INCOMPATIBLE_VERSION"} errorCode={startup.envelope?.data.error_code ?? startup.error} />;
  }
  if (manifest.state !== "READY" || !manifest.envelope) {
    return <FullState state={manifest.state} message={manifest.error} />;
  }

  return (
    <div className="shell">
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
          {page !== "settings" && <label className="month-picker">Monat<input type="month" value={month} onChange={(event) => setMonth(event.target.value)} aria-label="Auswertungsmonat" /></label>}
        </header>
        {page === "overview" && <Overview month={month} onNavigate={setPage} />}
        {page === "accounts" && <Accounts />}
        {page === "transactions" && <Transactions month={month} />}
        {page === "categories" && <Categories month={month} />}
        {page === "recurring" && <Recurring />}
        {page === "forecast" && <Forecast month={month} />}
        {page === "wealth" && <Wealth />}
        {page === "reviews" && <Reviews />}
        {page === "imports" && <Imports />}
        {page === "settings" && <Settings manifest={manifest.envelope.data} />}
      </main>
    </div>
  );
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

function Accounts() {
  const result = useFinanceQuery<{ accounts: Account[] }>("ListAccounts", { as_of: "2026-07-20" });
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<Account | null>(null);
  const [message, setMessage] = useState("");
  return <QueryBoundary result={result}>{({ accounts }) => <><section className="section-intro"><div><h2>Konten und bestätigte Salden</h2><p>Transaktionen und Salden sind getrennte Fakten. Berechnete Werte bleiben als solche gekennzeichnet.</p></div><button className="primary" onClick={() => setCreateOpen(true)}>Konto hinzufügen</button></section>{message && <div className="toast" role="status">{message}</div>}<section className="account-summary">{accounts.filter((item) => item.include_in_liquidity && item.latest_balance).map((item) => <article className="panel account-tile" key={item.account_id}><span className="account-type">{item.account_type.slice(0, 2)}</span><div><small>{item.account_type} · {item.institution}</small><h3>{item.display_name}</h3><strong>{money(item.available_balance ?? item.latest_balance, item.currency)}</strong><p>{item.masked_reference ?? "Keine Referenz"}</p></div><span className={`freshness ${item.freshness.toLowerCase()}`}>{item.freshness === "STALE" ? "Veraltet" : "Aktuell"}</span></article>)}</section><section className="panel table-panel"><table><thead><tr><th>Konto</th><th>Typ</th><th>Letzter bestätigter Saldo</th><th>Saldo-Datum</th><th>Abgleich</th><th>Relevanz</th><th></th></tr></thead><tbody>{accounts.map((item) => <tr key={item.account_id}><td><strong>{item.display_name}</strong><small>{item.institution} · {item.masked_reference ?? "–"}</small></td><td>{label(item.account_type)}</td><td className="amount">{money(item.latest_balance, item.currency)}<small>{item.balance_source ? label(item.balance_source) : "Kein Snapshot"}</small></td><td>{item.balance_date ? date(item.balance_date) : "–"}<small className={item.freshness === "STALE" ? "stale-text" : ""}>{item.freshness === "STALE" ? "Veraltet" : "Aktuell"}</small></td><td><span className={`status-badge ${item.reconciliation_status === "MATCHED" ? "confirmed" : item.reconciliation_status === "REVIEW_REQUIRED" ? "missed" : "paused"}`}>{item.reconciliation_status}</span></td><td><span className="relevance">{item.include_in_cashflow ? "Cashflow" : ""} {item.include_in_net_worth ? "Vermögen" : ""}</span></td><td><button className="icon-button" onClick={() => setSelected(item)} aria-label={`Konto ${item.display_name} öffnen`}>→</button></td></tr>)}</tbody></table></section>{createOpen && <CreateAccountDialog onClose={() => setCreateOpen(false)} onCreated={() => { setCreateOpen(false); setMessage("Konto wurde lokal angelegt."); }} />}{selected && <AccountDialog account={selected} onClose={() => setSelected(null)} onMessage={setMessage} />}</>}</QueryBoundary>;
}

function CreateAccountDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const first = useRef<HTMLButtonElement>(null); const handleKeyDown = useModalFocus(first, onClose);
  const [name, setName] = useState(""); const [type, setType] = useState("CHECKING"); const [institution, setInstitution] = useState("");
  const save = async () => { await financeBridge.command("CreateAccount", { display_name: name, account_type: type, institution, currency: "EUR" }); onCreated(); };
  return <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-account-title" onKeyDown={handleKeyDown}><button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">NEUES KONTO</span><h2 id="create-account-title">Konto anlegen</h2><div className="form-stack"><label>Anzeigename<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>Kontotyp<select value={type} onChange={(event) => setType(event.target.value)}>{["CHECKING","SAVINGS","CREDIT_CARD","CASH","BROKERAGE","LOAN","MORTGAGE","OTHER"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Institut<input value={institution} onChange={(event) => setInstitution(event.target.value)} /></label></div><div className="dialog-actions"><button className="secondary" onClick={onClose}>Abbrechen</button><button className="primary" disabled={!name.trim()} onClick={save}>Konto anlegen</button></div></section></div>;
}

function AccountDialog({ account, onClose, onMessage }: { account: Account; onClose: () => void; onMessage: (message: string) => void }) {
  const first = useRef<HTMLButtonElement>(null); const handleKeyDown = useModalFocus(first, onClose);
  const details = useFinanceQuery<{ balance_history: Array<Record<string, string>>; reconciliation: Record<string, string> | null }>("GetAccount", { account_id: account.account_id });
  const [amount, setAmount] = useState(account.latest_balance ?? "");
  const record = async () => { await financeBridge.command("RecordBalanceSnapshot", { account_id: account.account_id, balance_date: "2026-07-20", booked_balance: amount, available_balance: amount, currency: account.currency, source: "MANUAL_ENTRY", confidence: "HIGH" }); onMessage("Saldo-Snapshot wurde unveränderlich erfasst."); onClose(); };
  const reconcile = async () => { await financeBridge.command("ReconcileAccountBalance", { account_id: account.account_id }); onMessage("Saldenabgleich wurde lokal ausgeführt."); onClose(); };
  return <div className="dialog-backdrop"><section className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="account-title" onKeyDown={handleKeyDown}><button ref={first} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">KONTODETAIL</span><h2 id="account-title">{account.display_name}</h2><div className="account-balance"><span>Letzter bestätigter Saldo</span><strong>{money(account.latest_balance, account.currency)}</strong><small>{account.balance_date ? date(account.balance_date) : "Kein Snapshot"} · {account.balance_source ? label(account.balance_source) : "–"}</small></div><label className="balance-entry">Neuen gemeldeten Saldo erfassen<input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} /></label><div className="history-list"><h3>Snapshot-Historie</h3>{details.envelope?.data.balance_history?.map((item) => <div key={item.snapshot_id}><span>{date(item.balance_date)}</span><strong>{money(item.booked_balance, item.currency)}</strong><small>{label(item.source)}</small></div>)}</div><div className="dialog-actions"><button className="secondary" onClick={reconcile}>Saldo abgleichen</button><button className="primary" onClick={record}>Snapshot erfassen</button></div></section></div>;
}

function Metric({ label: caption, value, tone, note }: { label: string; value: string; tone: string; note: string }) {
  return <article className={`metric ${tone}`}><span>{caption}</span><strong>{value}</strong><small>{note}</small></article>;
}

function PanelHeader({ title, subtitle, action, onAction }: { title: string; subtitle?: string; action?: string; onAction?: () => void }) {
  return <header className="panel-header"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action && <button className="text-button" onClick={onAction}>{action} →</button>}</header>;
}

function Transactions({ month }: { month: string }) {
  const result = useFinanceQuery<{ transactions: Transaction[] }>("ListTransactions", { month });
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [message, setMessage] = useState("");
  const classify = async () => { await financeBridge.command("ClassifyTransactions", { month }); setMessage("Klassifikation lokal ausgeführt."); };
  return <QueryBoundary result={result}>{({ transactions }) => <>
    <section className="section-intro"><div><h2>Buchungen im Zeitraum</h2><p>Effektive Beträge nach bestätigten Dubletten, Transfers und Rückerstattungen.</p></div><button className="primary" onClick={classify}>Regeln anwenden</button></section>
    {message && <div className="toast" role="status">{message}</div>}
    <section className="panel table-panel"><table><thead><tr><th>Datum</th><th>Gegenpartei</th><th>Kategorie</th><th>Status</th><th className="numeric">Betrag</th><th><span className="sr-only">Aktion</span></th></tr></thead><tbody>{transactions.map((item) => <tr key={item.transaction_id}><td>{date(item.booking_date)}</td><td><strong>{item.counterparty}</strong><small>{item.description}</small></td><td><span className={`category-tag ${item.category_code === "UNCLASSIFIED" ? "warn" : ""}`}>{label(item.category_code)}</span></td><td><span className="status-dot ok">Effektiv</span></td><td className={`numeric amount ${Number(item.amount) >= 0 ? "positive-text" : ""}`}>{money(item.amount, item.currency)}</td><td><button className="icon-button" onClick={() => setSelected(item)} aria-label={`Details zu ${item.counterparty}`}>→</button></td></tr>)}</tbody></table></section>
    {selected && <TransactionDialog transaction={selected} onClose={() => setSelected(null)} />}
  </>}</QueryBoundary>;
}

function TransactionDialog({ transaction, onClose }: { transaction: Transaction; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  const details = useFinanceQuery<Record<string, unknown>>("GetTransactionDetails", { transaction_id: transaction.transaction_id });
  const handleKeyDown = useModalFocus(close, onClose);
  return <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="transaction-title" onKeyDown={handleKeyDown}><button ref={close} className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">TRANSAKTIONSDETAIL</span><h2 id="transaction-title">{transaction.counterparty}</h2><strong className="dialog-amount">{money(transaction.amount)}</strong><dl><div><dt>Buchungstag</dt><dd>{date(transaction.booking_date)}</dd></div><div><dt>Kategorie</dt><dd>{label(transaction.category_code)}</dd></div><div><dt>Beschreibung</dt><dd>{transaction.description}</dd></div><div><dt>Cashflow-relevant</dt><dd>{transaction.cashflow_relevant ? "Ja" : "Nein"}</dd></div></dl><div className="detail-state">{details.state === "LOADING" ? "Event-Historie wird geladen …" : `Query-Projektion · ${details.state}`}</div></section></div>;
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

function Wealth() {
  const worth = useFinanceQuery<NetWorthOverview>("GetNetWorthOverview", { valuation_currency: "EUR", as_of: "2026-07-20" });
  const liquidity = useFinanceQuery<LiquidityOverview>("GetLiquidityOverview", { valuation_currency: "EUR", as_of: "2026-07-20" });
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

function Imports() {
  const result = useFinanceQuery<{ imports: Array<Record<string, string>> }>("ListImportBatches");
  const [message, setMessage] = useState("");
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("acc_01");
  const choose = async () => { const path = await financeBridge.selectImportFile(); setSourcePath(path); setMessage(path ? "CSV wurde lokal ausgewählt. Prüfe das Zielkonto und bestätige den Import." : "Die lokale Dateiauswahl ist nur im Desktop-Wrapper verfügbar."); };
  const runImport = async () => { if (!sourcePath) return; await financeBridge.command("ImportTransactions", { source_file_path: sourcePath, account_id: accountId }); setMessage("CSV wurde lokal importiert und normalisiert."); setSourcePath(null); };
  return <><section className="import-hero panel"><span className="import-icon">⇩</span><div><h2>CSV lokal importieren</h2><p>Originaldateien werden verschlüsselt gespeichert. Die UI erhält nur einen kontrollierten Dateipfad über Desktop IPC.</p>{sourcePath && <div className="import-confirm"><span>Ausgewählte lokale Datei</span><strong>{sourcePath.split(/[\\/]/).pop()}</strong><label>Zielkonto<input value={accountId} onChange={(event) => setAccountId(event.target.value)} /></label><button className="primary" onClick={runImport}>Import bestätigen</button></div>}</div><button className="primary" onClick={choose}>CSV auswählen</button></section>{message && <div className="toast" role="status">{message}</div>}<QueryBoundary result={result}>{({ imports }) => <section className="panel table-panel"><PanelHeader title="Importhistorie" subtitle="Inhaltshashes statt Quelldateien"/><table><thead><tr><th>Import</th><th>Zeitpunkt</th><th>Parser</th><th>Status</th><th>Inhaltshash</th></tr></thead><tbody>{imports.map((item) => <tr key={item.import_id}><td><strong>{item.import_id}</strong></td><td>{date(item.created_at)}</td><td>{item.parser_version}</td><td><span className="status-badge confirmed">{item.status}</span></td><td><code>{item.content_hash}</code></td></tr>)}</tbody></table></section>}</QueryBoundary></>;
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
  </>}</QueryBoundary>;
}
