import { useCallback, useEffect, useMemo, useState } from "react";
import { financeBridge, SchemaCompatibilityError, type SelectedImportFile } from "./bridge";
import type { Account, CapabilityManifest, Envelope, ViewState } from "./contracts/generated";

type Json = Record<string, unknown>;
type QueryState<T> = { state: ViewState; data?: T; error?: string };
type Section = {
  section_id: string;
  section_type: "CHECKING" | "SAVINGS" | "BROKERAGE" | "UNKNOWN";
  original_title: string;
  account_reference: string | null;
  record_count: number;
  empty: boolean;
  import_supported: boolean;
  warnings: string[];
  mapped_account_id: string | null;
  import_status: string;
};
type Analysis = {
  export_id: string;
  analysis_id: string;
  bank_identifier: string;
  report_month: string;
  period_start: string;
  period_end: string;
  import_profile: string;
  profile_version: string;
  encoding: string;
  delimiter: string;
  source_file_hash: string;
  file_size: number;
  warnings: string[];
  sections: Section[];
  status: string;
};
type Wizard = {
  export_id: string;
  current_step: number;
  completed_steps: number[];
  status: string;
  can_resume: boolean;
  analysis: Analysis;
  requirements: Json[];
  execution_result: Json | null;
};
type HistoryRow = {
  export_id: string;
  bank_identifier: string;
  report_month: string;
  imported_at: string;
  section_count: number;
  completed_section_count: number;
  status: string;
  import_profile: string;
  parser_version: string;
  source_file_hash: string;
  resumable: boolean;
};

const steps = ["Datei analysieren", "Konten zuordnen", "Anfangswerte", "Vorschau", "Ergebnis"];
const requiredCapabilities = [
  "import_capability",
  "multi_account_import_capability",
  "balance_reconciliation_capability",
  "position_reconciliation_capability",
  "investment_funding_capability",
];
const shortHash = (value: string) => value ? `${value.slice(0, 12)}…${value.slice(-8)}` : "–";
const previousDay = (value: string) => {
  const current = new Date(`${value}T00:00:00Z`);
  current.setUTCDate(current.getUTCDate() - 1);
  return current.toISOString().slice(0, 10);
};

async function query<T>(name: string, payload: Json = {}): Promise<QueryState<T>> {
  try {
    const response = await financeBridge.query<T>(name, payload);
    const state = response.projection_sequence < response.event_store_sequence ? "STALE" : response.state;
    return { state, data: response.data };
  } catch (error) {
    return {
      state: error instanceof SchemaCompatibilityError ? "INCOMPATIBLE_SCHEMA" : "ERROR",
      error: error instanceof Error ? error.message : "Unbekannter lokaler Fehler",
    };
  }
}

export function Imports({ uiMonth, manifest }: { uiMonth: string; manifest: CapabilityManifest }) {
  const [wizard, setWizard] = useState<QueryState<Wizard | null>>({ state: "LOADING" });
  const [history, setHistory] = useState<QueryState<{ imports: HistoryRow[] }>>({ state: "LOADING" });
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedFile, setSelectedFile] = useState<SelectedImportFile | null>(null);
  const [step, setStep] = useState(1);
  const [message, setMessage] = useState("");
  const [operation, setOperation] = useState<ViewState>("READY");
  const [bankIdentifier, setBankIdentifier] = useState("");
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [skipConfirmed, setSkipConfirmed] = useState<Record<string, boolean>>({});
  const [openingBalances, setOpeningBalances] = useState<Record<string, string>>({});
  const [closingBalances, setClosingBalances] = useState<Record<string, string>>({});
  const [emptyPositions, setEmptyPositions] = useState<Record<string, boolean>>({});
  const [positions, setPositions] = useState<Record<string, Json[]>>({});
  const [previews, setPreviews] = useState<Json[]>([]);
  const [validation, setValidation] = useState<Json | null>(null);
  const [previewConfirmed, setPreviewConfirmed] = useState(false);
  const [result, setResult] = useState<Json | null>(null);
  const [detail, setDetail] = useState<Json | null>(null);
  const [newAccountName, setNewAccountName] = useState<Record<string, string>>({});

  const refresh = useCallback(async (exportId?: string) => {
    const [wizardResult, historyResult, accountResult] = await Promise.all([
      query<Wizard | null>("GetImportWizardState", exportId ? { export_id: exportId } : {}),
      query<{ imports: HistoryRow[] }>("GetImportHistory"),
      query<{ accounts: Account[] }>("ListAccounts"),
    ]);
    setWizard(wizardResult);
    setHistory(historyResult);
    setAccounts(accountResult.data?.accounts ?? []);
    if (wizardResult.data) {
      setStep(wizardResult.data.current_step);
      setBankIdentifier(wizardResult.data.analysis.bank_identifier);
      setMappings(Object.fromEntries(wizardResult.data.analysis.sections.filter((item) => item.mapped_account_id).map((item) => [item.section_id, String(item.mapped_account_id)])));
      setResult(wizardResult.data.execution_result);
      if (wizardResult.data.current_step >= 4) {
        const restoredPreviews = await Promise.all(
          wizardResult.data.analysis.sections.map(async (section) => {
            const response = await query<Json>("GetImportSectionPreview", {
              analysis_id: wizardResult.data?.analysis.analysis_id,
              section_id: section.section_id,
            });
            return response.data ?? {};
          }),
        );
        setPreviews(restoredPreviews);
      }
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const analysis = wizard.data?.analysis;
  const incompatible = requiredCapabilities.filter((name) => manifest.capabilities[name] !== true);
  const activeAccounts = useMemo(() => accounts.filter((item) => item.status === "ACTIVE"), [accounts]);
  const busy = operation === "VALIDATING" || operation === "EXECUTING";

  const run = async (state: ViewState, action: () => Promise<void>) => {
    setOperation(state);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Der lokale Vorgang ist fehlgeschlagen.");
    } finally {
      setOperation("READY");
    }
  };

  const chooseFile = async () => {
    const selected = await financeBridge.selectImportFile();
    if (!selected) {
      setMessage("Keine Datei ausgewählt. Die Dateiauswahl bleibt vollständig im Desktop-Host.");
      return;
    }
    setSelectedFile(selected);
    await run("VALIDATING", async () => {
      const response = await financeBridge.command("AnalyzeImportFile", {
        source_file_reference: selected.file_reference,
        requested_profile: "GermanMultiAccountCsvV1",
        ...(bankIdentifier.trim() ? { confirmed_bank_identifier: bankIdentifier.trim() } : {}),
      }) as { result?: Analysis };
      const analyzed = response.result;
      if (!analyzed) throw new Error("Die Importanalyse lieferte kein Ergebnis.");
      await refresh(analyzed.export_id);
      setStep(1);
      setMessage("Datei wurde lokal analysiert. Bank und Berichtsmonat müssen vor der Zuordnung geprüft werden.");
    });
  };

  const confirmAnalysis = () => {
    if (!analysis) return;
    setStep(2);
    setMessage("Bank, Zeitraum und erkannte Abschnitte wurden geprüft.");
  };

  const createAccountFor = async (section: Section) => {
    const displayName = newAccountName[section.section_id]?.trim();
    if (!displayName) {
      setMessage("Gib zuerst einen Anzeigenamen für das neue Konto ein.");
      return;
    }
    await run("EXECUTING", async () => {
      const response = await financeBridge.command("CreateAccount", {
        display_name: displayName,
        account_type: section.section_type,
        institution: analysis?.bank_identifier ?? "Unbekannte Bank",
        currency: "EUR",
        account_reference: section.account_reference,
        include_in_cashflow: section.section_type === "CHECKING",
        include_in_liquidity: section.section_type !== "BROKERAGE",
        include_in_net_worth: true,
      }) as { result?: string };
      if (!response.result) throw new Error("Das Konto konnte nicht angelegt werden.");
      setMappings((current) => ({ ...current, [section.section_id]: String(response.result) }));
      const refreshed = await query<{ accounts: Account[] }>("ListAccounts");
      setAccounts(refreshed.data?.accounts ?? []);
      setMessage("Konto wurde lokal angelegt und für diesen Abschnitt vorausgewählt.");
    });
  };

  const saveMappings = async () => {
    if (!analysis) return;
    const sectionMappings = analysis.sections.map((section) => {
      const skip = section.section_type === "UNKNOWN" || mappings[section.section_id] === "__SKIP__";
      if (skip && !skipConfirmed[section.section_id]) throw new Error(`Das Überspringen von „${section.original_title}“ muss ausdrücklich bestätigt werden.`);
      if (!skip && !mappings[section.section_id]) throw new Error(`Für „${section.original_title}“ fehlt ein lokales Konto.`);
      return {
        section_id: section.section_id,
        action: skip ? "SKIP_SECTION" : "USE_EXISTING_ACCOUNT",
        account_id: skip ? null : mappings[section.section_id],
      };
    });
    await run("EXECUTING", async () => {
      await financeBridge.command("MapImportSections", { analysis_id: analysis.analysis_id, section_mappings: sectionMappings });
      await refresh(analysis.export_id);
      setStep(3);
      setMessage("Alle Abschnittszuordnungen wurden dauerhaft gespeichert.");
    });
  };

  const addPosition = (accountId: string) => {
    setPositions((current) => ({
      ...current,
      [accountId]: [...(current[accountId] ?? []), { security_identifier_type: "ISIN", security_identifier: "", security_name: "", opening_quantity: "", closing_quantity: "" }],
    }));
  };
  const editPosition = (accountId: string, index: number, field: string, value: string) => {
    setPositions((current) => ({
      ...current,
      [accountId]: (current[accountId] ?? []).map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row),
    }));
  };

  const saveInitialValues = async () => {
    if (!analysis) return;
    await run("EXECUTING", async () => {
      for (const section of analysis.sections) {
        const accountId = mappings[section.section_id];
        if (!accountId || accountId === "__SKIP__") continue;
        if (section.section_type === "CHECKING" || section.section_type === "SAVINGS") {
          if (!openingBalances[accountId]) throw new Error(`Der bestätigte Anfangssaldo für „${section.original_title}“ fehlt.`);
          await financeBridge.command("RecordOpeningBalance", {
            account_id: accountId, balance_date: previousDay(analysis.period_start),
            booked_balance: openingBalances[accountId], available_balance: null, currency: "EUR",
            source: "MANUAL_ENTRY", confirmation: true, comment: "Im Importassistenten bestätigt",
          });
          if (closingBalances[accountId]) {
            await financeBridge.command("RecordClosingBalance", {
              account_id: accountId, balance_date: analysis.period_end,
              booked_balance: closingBalances[accountId], available_balance: null, currency: "EUR",
              source: "BANK_EXPORT", confirmation: true,
            });
          }
        }
        if (section.section_type === "BROKERAGE") {
          const rows = positions[accountId] ?? [];
          if (!rows.length && !emptyPositions[accountId]) throw new Error(`Erfasse Anfangspositionen für „${section.original_title}“ oder bestätige den leeren Bestand.`);
          if (emptyPositions[accountId]) {
            await financeBridge.command("ConfirmEmptyOpeningSecurityPositions", { account_id: accountId, valuation_date: previousDay(analysis.period_start) });
          }
          for (const row of rows) {
            if (!row.security_identifier || !row.security_name || !row.opening_quantity) throw new Error("Eine Depotposition ist unvollständig.");
            await financeBridge.command("RecordOpeningSecurityPosition", {
              account_id: accountId, valuation_date: previousDay(analysis.period_start),
              security_identifier_type: row.security_identifier_type, security_identifier: row.security_identifier,
              security_name: row.security_name, quantity: row.opening_quantity, valuation_price: "0",
              price_currency: "EUR", market_value: "0", valuation_source: "MANUAL_ENTRY", confirmation: true,
            });
            if (row.closing_quantity !== "") {
              await financeBridge.command("RecordClosingSecurityPosition", {
                account_id: accountId, valuation_date: analysis.period_end,
                security_identifier_type: row.security_identifier_type, security_identifier: row.security_identifier,
                security_name: row.security_name, quantity: row.closing_quantity, confirmation: true,
              });
            }
          }
        }
      }
      const sectionPreviews = await Promise.all(analysis.sections.map(async (section) => {
        const response = await financeBridge.query<Json>("GetImportSectionPreview", { analysis_id: analysis.analysis_id, section_id: section.section_id });
        return response.data;
      }));
      const validated = await financeBridge.command("ImportMappedSections", {
        analysis_id: analysis.analysis_id, parser_profile: analysis.import_profile,
        parser_version: analysis.profile_version, import_mode: "VALIDATE_ONLY",
      }) as { result?: Json };
      setPreviews(sectionPreviews);
      setValidation(validated.result ?? null);
      setPreviewConfirmed(false);
      setStep(4);
      setMessage("Anfangswerte wurden gespeichert; die vollständige Vorschau ist bereit.");
    });
  };

  const executeImport = async () => {
    if (!analysis || !previewConfirmed) {
      setMessage("Bestätige die geprüfte Vorschau ausdrücklich.");
      return;
    }
    await run("EXECUTING", async () => {
      const response = await financeBridge.command("ImportMappedSections", {
        analysis_id: analysis.analysis_id, parser_profile: analysis.import_profile,
        parser_version: analysis.profile_version, import_mode: "IMPORT_NEW",
      }) as { result?: Json };
      await financeBridge.command("DetectInvestmentFundingRelations", {});
      setResult(response.result ?? null);
      await refresh(analysis.export_id);
      const execution = await query<Json | null>("GetImportExecutionResult", { export_id: analysis.export_id });
      setResult(execution.data ?? response.result ?? null);
      setStep(5);
      setMessage("Der bestätigte Import wurde lokal abgeschlossen.");
    });
  };

  const relationAction = async (relationId: string, action: "Confirm" | "Reject" | "Break") => {
    await run("EXECUTING", async () => {
      await financeBridge.command(`${action}InvestmentFundingRelation`, { relation_id: relationId });
      if (analysis) {
        const response = await query<Json | null>("GetImportExecutionResult", { export_id: analysis.export_id });
        setResult(response.data ?? null);
      }
    });
  };

  const reconcile = async (section: Section) => {
    if (!analysis) return;
    const accountId = mappings[section.section_id] ?? section.mapped_account_id;
    if (!accountId) return;
    await run("EXECUTING", async () => {
      await financeBridge.command(section.section_type === "BROKERAGE" ? "ReconcileImportedPeriodPositions" : "ReconcileImportedPeriodBalance", {
        account_id: accountId, section_id: section.section_id,
        period_start: analysis.period_start, period_end: analysis.period_end,
      });
      const response = await query<Json | null>("GetImportExecutionResult", { export_id: analysis.export_id });
      setResult(response.data ?? null);
    });
  };

  const openHistory = async (row: HistoryRow) => {
    const response = await query<Json | null>("GetImportHistoryDetail", { export_id: row.export_id });
    setDetail(response.data ?? null);
  };
  const resume = async (row: HistoryRow) => {
    await refresh(row.export_id);
    setDetail(null);
    setMessage(`Import ${row.report_month} wird an der gespeicherten Stelle fortgesetzt.`);
  };

  if (incompatible.length || manifest.contract_version && manifest.contract_version !== "1.3.0") {
    return <section className="panel import-blocker" role="alert"><h2>Import nicht kompatibel</h2><p>Die installierte lokale Extension stellt den vollständigen 1.2.0-Importvertrag nicht bereit.</p><code>{incompatible.join(", ") || `Vertrag ${manifest.contract_version}`}</code></section>;
  }

  return <div className="imports-workspace">
    <section className="import-hero panel">
      <span className="import-icon" aria-hidden="true">⇩</span>
      <div><h2>Monatlichen Bankexport importieren</h2><p>Eine lokale Datei, eine Bank, ein Berichtsmonat – mehrere getrennt geprüfte Kontoabschnitte.</p></div>
      <button className="primary" disabled={busy} onClick={chooseFile}>{selectedFile ? "Andere CSV wählen" : "CSV auswählen"}</button>
    </section>
    <ol className="import-stepper" aria-label="Importfortschritt">
      {steps.map((title, index) => <li key={title} className={step === index + 1 ? "active" : step > index + 1 ? "complete" : ""} aria-current={step === index + 1 ? "step" : undefined}><span>{step > index + 1 ? "✓" : index + 1}</span><strong>{title}</strong></li>)}
    </ol>
    {operation !== "READY" && <div className="status-banner" role="status"><strong>{operation === "VALIDATING" ? "Lokale Prüfung läuft" : "Import wird verarbeitet"}</strong><span>Bitte diesen Arbeitsbereich geöffnet lassen.</span></div>}
    {message && <div className="toast" role="status" aria-live="polite">{message}</div>}
    {wizard.state === "LOADING" && <section className="panel page-state"><span className="loader" /><p>Gespeicherten Importstand laden …</p></section>}
    {(wizard.state === "ERROR" || wizard.state === "INCOMPATIBLE_SCHEMA") && <section className="panel page-state error" role="alert"><h2>Importprojektion nicht verfügbar</h2><p>{wizard.error}</p><button className="secondary" onClick={() => refresh()}>Erneut versuchen</button></section>}
    {!analysis && wizard.state !== "LOADING" && <section className="panel import-empty"><h2>Neuen Import beginnen</h2><p>Wähle einen monatlichen CSV-Export. React erhält nur eine kurzlebige Dateireferenz, niemals den lokalen Pfad.</p><label>Bankkennung, falls nicht im Export enthalten<input value={bankIdentifier} onChange={(event) => setBankIdentifier(event.target.value)} placeholder="z. B. BANK_A" /></label><button className="primary" onClick={chooseFile}>Lokale CSV auswählen</button></section>}
    {analysis && <>
      {uiMonth !== analysis.report_month && <div className="status-banner"><strong>Abweichender Auswertungsmonat</strong><span>UI: {uiMonth} · Importdatei: {analysis.report_month}. Importiert wird ausschließlich der Dateizeitraum.</span></div>}
      {step === 1 && <AnalysisStep analysis={analysis} fileName={selectedFile?.display_name} bankIdentifier={bankIdentifier} onBankIdentifier={setBankIdentifier} onConfirm={confirmAnalysis} />}
      {step === 2 && <MappingStep analysis={analysis} accounts={activeAccounts} mappings={mappings} skips={skipConfirmed} names={newAccountName} onMap={(id, value) => setMappings((current) => ({ ...current, [id]: value }))} onSkip={(id, value) => setSkipConfirmed((current) => ({ ...current, [id]: value }))} onName={(id, value) => setNewAccountName((current) => ({ ...current, [id]: value }))} onCreate={createAccountFor} onSave={saveMappings} busy={busy} />}
      {step === 3 && <ValuesStep analysis={analysis} mappings={mappings} opening={openingBalances} closing={closingBalances} emptyPositions={emptyPositions} positions={positions} onOpening={(id, value) => setOpeningBalances((current) => ({ ...current, [id]: value }))} onClosing={(id, value) => setClosingBalances((current) => ({ ...current, [id]: value }))} onEmpty={(id, value) => setEmptyPositions((current) => ({ ...current, [id]: value }))} onAddPosition={addPosition} onPosition={editPosition} onSave={saveInitialValues} busy={busy} />}
      {step === 4 && <PreviewStep analysis={analysis} previews={previews} validation={validation} confirmed={previewConfirmed} onConfirmed={setPreviewConfirmed} onExecute={executeImport} busy={busy} />}
      {step === 5 && <ResultStep analysis={analysis} result={result} onReconcile={reconcile} onRelation={relationAction} busy={busy} />}
    </>}
    <History result={history} onOpen={openHistory} onResume={resume} />
    {detail && <HistoryDetail detail={detail} onClose={() => setDetail(null)} />}
  </div>;
}

function AnalysisStep({ analysis, fileName, bankIdentifier, onBankIdentifier, onConfirm }: { analysis: Analysis; fileName?: string; bankIdentifier: string; onBankIdentifier: (value: string) => void; onConfirm: () => void }) {
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 1</span><h2>Datei analysieren</h2><p>Prüfe Bank, Monat, Parserprofil und Abschnittsstruktur.</p></header><dl className="import-facts"><div><dt>Datei</dt><dd>{fileName ?? "Gespeicherte Dateireferenz"}</dd></div><div><dt>Bank</dt><dd><input value={bankIdentifier} onChange={(event) => onBankIdentifier(event.target.value)} aria-label="Bankkennung" /></dd></div><div><dt>Berichtsmonat</dt><dd>{analysis.report_month}</dd></div><div><dt>Zeitraum</dt><dd>{analysis.period_start} – {analysis.period_end}</dd></div><div><dt>Profil</dt><dd>{analysis.import_profile} · {analysis.profile_version}</dd></div><div><dt>Format</dt><dd>{analysis.encoding} · „{analysis.delimiter}“</dd></div><div><dt>Inhalt</dt><dd><code>{shortHash(analysis.source_file_hash)}</code> · {analysis.file_size} Bytes</dd></div></dl><div className="section-cards">{analysis.sections.map((section) => <article key={section.section_id}><strong>{section.original_title}</strong><span>{section.section_type}</span><p>{section.record_count} Datensätze · {section.empty ? "leer und gültig" : "Daten erkannt"}</p>{section.account_reference && <small>{section.account_reference}</small>}{section.warnings.map((warning) => <small className="warning" key={warning}>{warning}</small>)}</article>)}</div><footer><button className="primary" disabled={!bankIdentifier.trim()} onClick={onConfirm}>Bank und Monat bestätigen</button></footer></section>;
}

function MappingStep({ analysis, accounts, mappings, skips, names, onMap, onSkip, onName, onCreate, onSave, busy }: { analysis: Analysis; accounts: Account[]; mappings: Record<string, string>; skips: Record<string, boolean>; names: Record<string, string>; onMap: (id: string, value: string) => void; onSkip: (id: string, value: boolean) => void; onName: (id: string, value: string) => void; onCreate: (section: Section) => void; onSave: () => void; busy: boolean }) {
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 2</span><h2>Konten zuordnen</h2><p>Gespeicherte Bindungen sind vorausgefüllt, bleiben aber sichtbar und änderbar.</p></header><div className="mapping-list">{analysis.sections.map((section) => { const compatible = accounts.filter((account) => account.account_type === section.section_type); const skipping = mappings[section.section_id] === "__SKIP__" || section.section_type === "UNKNOWN"; return <article key={section.section_id}><div><strong>{section.original_title}</strong><small>{section.section_type} · {section.account_reference ?? "keine Kontoreferenz"}</small></div><label>Lokales Konto<select value={section.section_type === "UNKNOWN" ? "__SKIP__" : mappings[section.section_id] ?? ""} onChange={(event) => onMap(section.section_id, event.target.value)}><option value="">Bitte wählen</option>{compatible.map((account) => <option value={account.account_id} key={account.account_id}>{account.display_name} · {account.masked_reference ?? account.institution}</option>)}<option value="__SKIP__">Abschnitt bewusst überspringen</option></select></label>{skipping ? <label className="danger-confirm"><input type="checkbox" checked={Boolean(skips[section.section_id])} onChange={(event) => onSkip(section.section_id, event.target.checked)} /> Überspringen ausdrücklich bestätigen</label> : <div className="inline-create"><input placeholder="Neues Konto: Anzeigename" value={names[section.section_id] ?? ""} onChange={(event) => onName(section.section_id, event.target.value)} /><button className="secondary small" onClick={() => onCreate(section)}>Neu anlegen</button></div>}</article>; })}</div><footer><button className="primary" disabled={busy} onClick={onSave}>Zuordnungen speichern</button></footer></section>;
}

function ValuesStep({ analysis, mappings, opening, closing, emptyPositions, positions, onOpening, onClosing, onEmpty, onAddPosition, onPosition, onSave, busy }: { analysis: Analysis; mappings: Record<string, string>; opening: Record<string, string>; closing: Record<string, string>; emptyPositions: Record<string, boolean>; positions: Record<string, Json[]>; onOpening: (id: string, value: string) => void; onClosing: (id: string, value: string) => void; onEmpty: (id: string, value: boolean) => void; onAddPosition: (id: string) => void; onPosition: (id: string, index: number, field: string, value: string) => void; onSave: () => void; busy: boolean }) {
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 3</span><h2>Anfangswerte prüfen</h2><p>Salden und Depotbestände werden je Konto getrennt bestätigt.</p></header><div className="values-list">{analysis.sections.map((section) => { const accountId = mappings[section.section_id] ?? section.mapped_account_id ?? ""; if (!accountId || accountId === "__SKIP__") return null; if (section.section_type !== "BROKERAGE") return <article key={section.section_id}><h3>{section.original_title}</h3><div className="field-grid"><label>Anfangssaldo am {previousDay(analysis.period_start)}<input inputMode="decimal" value={opening[accountId] ?? ""} onChange={(event) => onOpening(accountId, event.target.value)} /></label><label>Gemeldeter Endsaldo am {analysis.period_end}<input inputMode="decimal" value={closing[accountId] ?? ""} onChange={(event) => onClosing(accountId, event.target.value)} placeholder="optional, für Abgleich" /></label></div></article>; const rows = positions[accountId] ?? []; return <article key={section.section_id}><h3>{section.original_title}</h3><label className="danger-confirm"><input type="checkbox" checked={Boolean(emptyPositions[accountId])} onChange={(event) => onEmpty(accountId, event.target.checked)} /> Vor dem Berichtsmonat waren ausdrücklich keine Positionen vorhanden.</label>{rows.map((row, index) => <div className="position-row" key={`${accountId}-${index}`}><select value={String(row.security_identifier_type)} onChange={(event) => onPosition(accountId, index, "security_identifier_type", event.target.value)}><option>ISIN</option><option>WKN</option><option>OTHER</option></select><input aria-label="Wertpapierkennung" placeholder="Kennung" value={String(row.security_identifier)} onChange={(event) => onPosition(accountId, index, "security_identifier", event.target.value)} /><input aria-label="Wertpapiername" placeholder="Name" value={String(row.security_name)} onChange={(event) => onPosition(accountId, index, "security_name", event.target.value)} /><input aria-label="Anfangsbestand" placeholder="Anfang" inputMode="decimal" value={String(row.opening_quantity)} onChange={(event) => onPosition(accountId, index, "opening_quantity", event.target.value)} /><input aria-label="Endbestand" placeholder="Ende" inputMode="decimal" value={String(row.closing_quantity)} onChange={(event) => onPosition(accountId, index, "closing_quantity", event.target.value)} /></div>)}<button className="secondary small" onClick={() => onAddPosition(accountId)}>Position hinzufügen</button></article>; })}</div><footer><button className="primary" disabled={busy} onClick={onSave}>Anfangswerte bestätigen und Vorschau erstellen</button></footer></section>;
}

function PreviewStep({ analysis, previews, validation, confirmed, onConfirmed, onExecute, busy }: { analysis: Analysis; previews: Json[]; validation: Json | null; confirmed: boolean; onConfirmed: (value: boolean) => void; onExecute: () => void; busy: boolean }) {
  const blockers = ((validation?.section_results as Json[] | undefined) ?? []).filter((item) => item.status === "FAILED" || item.status === "REVIEW_REQUIRED");
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 4</span><h2>Vollständige Importvorschau</h2><p>Noch wurden keine Buchungen importiert. Prüfe jeden Abschnitt vor der verbindlichen Ausführung.</p></header>{blockers.length > 0 && <div className="status-banner" role="alert"><strong>{blockers.length} Blocker oder Prüfhinweise</strong><span>Der Import bleibt bis zur Klärung gesperrt.</span></div>}<div className="preview-list">{analysis.sections.map((section) => { const preview = previews.find((item) => item.section_id === section.section_id) ?? section as unknown as Json; const rows = (preview.preview as Json[] | undefined) ?? []; return <article key={section.section_id}><header><div><strong>{section.original_title}</strong><small>{section.section_type}</small></div><span className="status-badge">{section.empty ? "EMPTY_COMPLETED" : `${section.record_count} DATENSÄTZE`}</span></header><dl><div><dt>Erste Buchung</dt><dd>{String(preview.first_booking_date ?? "–")}</dd></div><div><dt>Letzte Buchung</dt><dd>{String(preview.last_booking_date ?? "–")}</dd></div><div><dt>Summe</dt><dd>{String(preview.amount_sum ?? "0")} EUR</dd></div><div><dt>Dublettenkandidaten</dt><dd>{String(preview.duplicate_candidate_count ?? 0)}</dd></div></dl>{rows.length > 0 && <div className="preview-rows">{rows.slice(0, 5).map((row, index) => <code key={index}>{String(row.booking_date ?? row.trade_date ?? "")} · {String(row.description ?? row.security_name ?? "")} · {String(row.amount ?? row.settlement_amount ?? "")}</code>)}</div>}{section.warnings.map((warning) => <small className="warning" key={warning}>{warning}</small>)}</article>; })}</div><label className="final-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => onConfirmed(event.target.checked)} /> Ich habe Bank, Zeitraum, Kontenzuordnung, Anfangswerte, Summen und Warnungen geprüft. Der Import darf jetzt als neue Events ausgeführt werden.</label><footer><button className="primary" disabled={busy || !confirmed || blockers.some((item) => item.status === "FAILED")} onClick={onExecute}>Import verbindlich ausführen</button></footer></section>;
}

function ResultStep({ analysis, result, onReconcile, onRelation, busy }: { analysis: Analysis; result: Json | null; onReconcile: (section: Section) => void; onRelation: (id: string, action: "Confirm" | "Reject" | "Break") => void; busy: boolean }) {
  const sections = (result?.section_results as Json[] | undefined) ?? [];
  const relations = (result?.relations as Json[] | undefined) ?? [];
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 5</span><h2>Importergebnis</h2><p>Ergebnisse, Relationen und Abgleiche bleiben nach einem Neustart vollständig rekonstruierbar.</p></header><div className="result-metrics"><div><span>Gesamtstatus</span><strong>{String(result?.status ?? analysis.status)}</strong></div><div><span>Kontobuchungen</span><strong>{String(result?.normalized_transaction_count ?? 0)}</strong></div><div><span>Depotbuchungen</span><strong>{String(result?.security_transaction_count ?? 0)}</strong></div><div><span>Relationsvorschläge</span><strong>{relations.length}</strong></div></div><div className="result-sections">{analysis.sections.map((section) => { const row = sections.find((item) => item.section_id === section.section_id); const reconciliation = row?.balance_reconciliation as Json | undefined ?? row?.position_reconciliation as Json | undefined; return <article key={section.section_id}><div><strong>{section.original_title}</strong><small>{String(row?.local_account_name ?? row?.account_id ?? section.mapped_account_id ?? "übersprungen")}</small></div><span className={`status-badge ${String(row?.status ?? section.import_status).toLowerCase()}`}>{String(row?.status ?? section.import_status)}</span>{row && <p>{String(row.normalized_transaction_count ?? row.security_transaction_count ?? row.record_count ?? 0)} verarbeitete Datensätze</p>}{reconciliation ? <p>Abgleich: <strong>{String(reconciliation.status)}</strong> · Abweichung {String(reconciliation.balance_difference ?? "positionsbezogen")}</p> : section.section_type !== "UNKNOWN" && <button className="secondary small" disabled={busy} onClick={() => onReconcile(section)}>Abgleich ausführen</button>}</article>; })}</div>{relations.length > 0 && <><h3 className="subheading">Wertpapier-Finanzierungen</h3><div className="relation-list">{relations.map((relation) => <article key={String(relation.relation_id)}><div><strong>{String(relation.amount)} {String(relation.currency)}</strong><small>{String(relation.booking_date ?? "–")} → {String(relation.security_identifier ?? "Wertpapier")}</small><p>{String(relation.match_reason ?? "Deterministischer Relationsmatch")}</p></div><span className="status-badge">{String(relation.status)}</span><div>{relation.status === "PROPOSED" && <><button className="primary small" onClick={() => onRelation(String(relation.relation_id), "Confirm")}>Bestätigen</button><button className="secondary small" onClick={() => onRelation(String(relation.relation_id), "Reject")}>Ablehnen</button></>}{relation.status === "CONFIRMED" && <button className="danger-link" onClick={() => onRelation(String(relation.relation_id), "Break")}>Verknüpfung lösen</button>}</div></article>)}</div></>}</section>;
}

function History({ result, onOpen, onResume }: { result: QueryState<{ imports: HistoryRow[] }>; onOpen: (row: HistoryRow) => void; onResume: (row: HistoryRow) => void }) {
  const rows = result.data?.imports ?? [];
  return <section className="panel import-history"><header><div><span className="eyebrow">DAUERHAFTE PROJEKTION</span><h2>Importhistorie</h2></div><span>{rows.length} Exporte</span></header>{result.state === "LOADING" ? <div className="page-state"><span className="loader" /></div> : rows.length === 0 ? <div className="page-state"><h3>Noch keine Importe</h3><p>Analysierte Dateien erscheinen hier ohne lokalen Dateipfad.</p></div> : <div className="history-table">{rows.map((row) => <article key={row.export_id}><div><strong>{row.bank_identifier} · {row.report_month}</strong><small>{new Date(row.imported_at).toLocaleString("de-DE")} · {row.import_profile}</small></div><span>{row.completed_section_count}/{row.section_count} Abschnitte</span><span className="status-badge">{row.status}</span><code>{shortHash(row.source_file_hash)}</code><div><button className="secondary small" onClick={() => onOpen(row)}>Details</button>{row.resumable && <button className="primary small" onClick={() => onResume(row)}>Fortsetzen</button>}</div></article>)}</div>}</section>;
}

function HistoryDetail({ detail, onClose }: { detail: Json; onClose: () => void }) {
  const analysis = detail.analysis as Analysis | undefined;
  const audit = (detail.audit_history as Json[] | undefined) ?? [];
  return <div className="dialog-backdrop"><section className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="history-title"><button className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">IMPORTDETAIL</span><h2 id="history-title">{analysis?.bank_identifier} · {analysis?.report_month}</h2><p>Status {String(detail.status)} · {analysis?.sections.length ?? 0} Abschnitte · Hash {shortHash(analysis?.source_file_hash ?? "")}</p><h3>Auditverlauf</h3><div className="audit-list">{audit.map((event) => <div key={String(event.sequence_number)}><code>#{String(event.sequence_number)}</code><strong>{String(event.event_type)}</strong><small>{new Date(String(event.occurred_at)).toLocaleString("de-DE")}</small></div>)}</div></section></div>;
}
