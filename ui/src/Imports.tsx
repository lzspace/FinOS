import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { financeBridge, SchemaCompatibilityError, type SelectedImportFile } from "./bridge";
import type { Account, CapabilityManifest, Envelope, ViewState } from "./contracts/generated";
import { EVENT_CATEGORY_ORDER, EVENT_CATEGORY_TITLES, eventLabel, type EventCategory } from "./eventLabels";
import { statusLabel } from "./statusLabels";

const OPENING_BALANCE_REASON_TEXT: Record<string, string> = {
  MISSING_CLOSING_BALANCE: "Der vorherige Monat besitzt keinen abschließend bestätigten Endsaldo.",
  CLOSING_BALANCE_NOT_CONFIRMED: "Der vorherige Monat besitzt keinen abschließend bestätigten Endsaldo.",
  CURRENCY_MISMATCH: "Die Währung hat sich gegenüber dem Vormonat geändert.",
  PRIOR_MONTH_NOT_RECONCILED: "Für den vorherigen Monat liegt noch kein Saldenabgleich vor.",
  UNRESOLVED_DIFFERENCE: "Der vorherige Monat weist eine ungelöste Saldodifferenz auf.",
  PRIOR_IMPORT_INCOMPLETE: "Der Import des vorherigen Monats wurde nicht vollständig abgeschlossen.",
  MISSING_PRIOR_OPENING_BALANCE: "Für den vorherigen Monat fehlt ein bestätigter Anfangssaldo.",
  UNRESOLVED_DUPLICATES: "Im vorherigen Monat müssen mögliche Dubletten zuerst geprüft werden.",
  INCONSISTENT_PROJECTION_DATA: "Die Daten des vorherigen Monats sind nicht vollständig konsistent.",
  ACCOUNT_NOT_FOUND: "Für dieses Konto liegt kein Vormonat vor.",
};

type Json = Record<string, unknown>;
type QueryState<T> = { state: ViewState; data?: T; error?: string };
type FieldErrors = Record<string, string>;
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
  completed_at?: string | null;
  period_start?: string;
  period_end?: string;
  record_count?: number;
  account_count?: number;
  account_names?: string[];
  open_review_count?: number;
};
type MappingField = { source_column: string; target_field: string; confidence: number };
type MappingTransformation = { target_field: string; type: string; parameters: Json };
type EditableImportMapping = {
  section_type: string;
  field_mappings: MappingField[];
  transformations: MappingTransformation[];
  constants: Record<string, string>;
  ignored_columns: string[];
  confidence: number;
  warnings: string[];
};
type MappingValidation = {
  validation_result_id: string;
  status: "VALID" | "VALID_WITH_WARNINGS" | "INVALID";
  rows_total: number;
  rows_valid: number;
  rows_invalid: number;
  errors: string[];
  warnings: string[];
};
type MappingProposal = EditableImportMapping & {
  proposal_id: string;
  analysis_id: string;
  model_id: string;
  model_version: string;
  validation: MappingValidation;
};
type UnknownFormatResult = {
  status: string;
  format_status?: string;
  analysis_id: string;
  ai_used?: boolean;
  ai_error_code?: string;
  source_structure?: { columns: string[]; encoding: string; delimiter: string; section_title: string };
  proposal?: MappingProposal;
  profile?: Json;
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
const importStatus = statusLabel;
const categoryName = (value: string) => ({
  UNCLASSIFIED: "Nicht kategorisiert", FOOD_GROCERIES: "Lebensmittel",
  FOOD_RESTAURANTS: "Restaurant", HOUSING_RENT: "Miete",
  INCOME_SALARY: "Gehalt", SUBSCRIPTIONS: "Abonnements",
}[value] ?? value.replace(/^CUSTOM_/, "").toLowerCase().replaceAll("_", " ").replace(/(^|\s)\S/g, (letter) => letter.toUpperCase()));
const previousDay = (value: string) => {
  const current = new Date(`${value}T00:00:00Z`);
  current.setUTCDate(current.getUTCDate() - 1);
  return current.toISOString().slice(0, 10);
};
const reportMonthLabel = (value: string) => {
  const [year, month] = value.split("-").map(Number);
  return year && month ? new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" }).format(new Date(year, month - 1, 1, 12)) : value;
};
const needsFormatRecovery = (message: string) => [
  "IMPORT_PROFILE_MISMATCH", "IMPORT_DATE_FORMAT_INVALID", "IMPORT_DECIMAL_FORMAT_INVALID",
  "IMPORT_UNKNOWN_SECTION", "IMPORT_FORMAT_UNKNOWN", "IMPORT_FORMAT_CHANGED",
].some((code) => message.includes(code));
const decimalValue = (value: string) => {
  const compact = value.trim().replaceAll(" ", "");
  return compact.includes(",") ? compact.replaceAll(".", "").replace(",", ".") : compact;
};
const isValidDecimal = (value: string) => /^[+-]?(?:\d+(?:[.,]\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?)$/.test(value.trim().replaceAll(" ", "")) && Number.isFinite(Number(decimalValue(value)));
const stableErrorCode = (message: string) => message.match(/\b(?:IMPORT|FINANCE|DESKTOP|LOCAL_AI)_[A-Z0-9_]+\b/)?.[0] ?? null;
const friendlyImportError = (message: string) => {
  const code = stableErrorCode(message);
  const texts: Record<string, string> = {
    IMPORT_ANALYSIS_NOT_FOUND: "Der gespeicherte Importstand wurde nicht gefunden. Aktualisiere die Importhistorie und versuche es erneut.",
    IMPORT_SECTION_MAPPING_MISSING: "Mindestens ein Kontoabschnitt ist noch keinem Konto zugeordnet.",
    IMPORT_OPENING_BALANCE_REQUIRED: "Für mindestens ein Konto fehlt ein bestätigter Anfangssaldo.",
    IMPORT_PERIOD_MISMATCH: "Mindestens eine Buchung liegt außerhalb des erkannten Importzeitraums.",
    IMPORT_PROFILE_MISMATCH: "Das gespeicherte Importprofil passt nicht mehr zur ausgewählten Datei.",
    FINANCE_IMPORT_ACCOUNT_CLOSED: "Mindestens ein Zielkonto war zum Buchungsdatum bereits geschlossen.",
  };
  return { text: code && texts[code] ? texts[code] : "Der Import konnte nicht fortgesetzt werden. Prüfe die markierten Angaben und versuche es erneut.", code };
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

export function Imports({ uiMonth, manifest, focusExportId, focusView, onOpenReviews }: { uiMonth: string; manifest: CapabilityManifest; focusExportId?: string; focusView?: string; onOpenReviews?: () => void }) {
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
  const [openingSuggestions, setOpeningSuggestions] = useState<Record<string, Json>>({});
  const [adjustmentReasons, setAdjustmentReasons] = useState<Record<string, string>>({});
  const [closingBalances, setClosingBalances] = useState<Record<string, string>>({});
  const [emptyPositions, setEmptyPositions] = useState<Record<string, boolean>>({});
  const [positions, setPositions] = useState<Record<string, Json[]>>({});
  const [previews, setPreviews] = useState<Json[]>([]);
  const [validation, setValidation] = useState<Json | null>(null);
  const [previewConfirmed, setPreviewConfirmed] = useState(false);
  const [result, setResult] = useState<Json | null>(null);
  const [detail, setDetail] = useState<Json | null>(null);
  const [newAccountName, setNewAccountName] = useState<Record<string, string>>({});
  const [unknownFormat, setUnknownFormat] = useState<UnknownFormatResult | null>(null);
  const [editableMapping, setEditableMapping] = useState<EditableImportMapping | null>(null);
  const [mappingValidation, setMappingValidation] = useState<MappingValidation | null>(null);
  const [mappingComment, setMappingComment] = useState("");
  const [allowAdditionalColumns, setAllowAdditionalColumns] = useState(false);
  const [openedFocus, setOpenedFocus] = useState<string | null>(null);
  const [happyPath, setHappyPath] = useState(false);
  const [formatRecoveryNeeded, setFormatRecoveryNeeded] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [messageIsError, setMessageIsError] = useState(false);
  const [technicalErrorCode, setTechnicalErrorCode] = useState<string | null>(null);
  const workspaceTop = useRef<HTMLDivElement>(null);

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
      const restoredOpening: Record<string, string> = {};
      const restoredClosing: Record<string, string> = {};
      const restoredPositions: Record<string, Json[]> = {};
      const restoredEmptyPositions: Record<string, boolean> = {};
      for (const requirement of wizardResult.data.requirements) {
        const accountId = String(requirement.account_id ?? "");
        if (!accountId) continue;
        const record = requirement.record as Json | undefined;
        const closingRecord = requirement.closing_record as Json | undefined;
        if (record?.booked_balance != null) restoredOpening[accountId] = String(record.booked_balance);
        if (closingRecord?.booked_balance != null) restoredClosing[accountId] = String(closingRecord.booked_balance);
        if (requirement.requirement_type === "OPENING_SECURITY_POSITIONS") {
          restoredPositions[accountId] = ((requirement.records as Json[] | undefined) ?? []).map((item) => ({
            security_identifier_type: item.security_identifier_type ?? "ISIN",
            security_identifier: item.security_identifier ?? "",
            security_name: item.security_name ?? "",
            opening_quantity: item.quantity ?? "",
            closing_quantity: "",
          }));
          restoredEmptyPositions[accountId] = Boolean(requirement.empty_confirmation);
        }
      }
      setStep(wizardResult.data.current_step);
      setBankIdentifier(wizardResult.data.analysis.bank_identifier);
      setMappings(Object.fromEntries(wizardResult.data.analysis.sections.filter((item) => item.mapped_account_id).map((item) => [item.section_id, String(item.mapped_account_id)])));
      setOpeningBalances(restoredOpening);
      setClosingBalances(restoredClosing);
      setPositions(restoredPositions);
      setEmptyPositions(restoredEmptyPositions);
      setAdjustmentReasons({});
      setFieldErrors({});
      setPreviewConfirmed(false);
      setHappyPath(false);
      let restoredResult = wizardResult.data.execution_result;
      if (wizardResult.data.current_step === 5) {
        const historyDetail = await query<Json | null>("GetImportHistoryDetail", { export_id: wizardResult.data.export_id });
        const reviewSummary = historyDetail.data?.review_summary as Json | undefined;
        if (restoredResult && reviewSummary) restoredResult = { ...restoredResult, review_summary: reviewSummary };
      }
      setResult(restoredResult);
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
    return wizardResult.data ?? null;
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const requirements = (wizard.data?.requirements as Json[] | undefined) ?? [];
    const suggestionsByAccount: Record<string, Json> = {};
    for (const requirement of requirements) {
      if (requirement.requirement_type !== "OPENING_BALANCE" || !requirement.suggested_opening_balance) continue;
      suggestionsByAccount[String(requirement.account_id)] = requirement.suggested_opening_balance as Json;
    }
    setOpeningSuggestions(suggestionsByAccount);
    setOpeningBalances((current) => {
      const updated = { ...current };
      for (const [accountId, suggestion] of Object.entries(suggestionsByAccount)) {
        if (updated[accountId] === undefined && suggestion.eligible) {
          updated[accountId] = String(suggestion.suggested_value ?? "");
        }
      }
      return updated;
    });
  }, [wizard.data]);

  const analysis = wizard.data?.analysis;
  const incompatible = requiredCapabilities.filter((name) => manifest.capabilities[name] !== true);
  const activeAccounts = useMemo(() => accounts.filter((item) => item.status === "ACTIVE"), [accounts]);
  const busy = operation === "VALIDATING" || operation === "EXECUTING";
  const clearFieldError = (key: string) => setFieldErrors((current) => {
    if (!current[key]) return current;
    const updated = { ...current };
    delete updated[key];
    return updated;
  });
  const showValidationErrors = (errors: FieldErrors) => {
    setFieldErrors(errors);
    setMessage("Bitte prüfe die markierten Pflichtfelder.");
    setMessageIsError(true);
    setTechnicalErrorCode(null);
  };

  const run = async (state: ViewState, action: () => Promise<void>, onError?: (message: string) => void) => {
    setOperation(state);
    setMessage("");
    setMessageIsError(false);
    setTechnicalErrorCode(null);
    try {
      await action();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Der lokale Vorgang ist fehlgeschlagen.";
      const friendly = friendlyImportError(errorMessage);
      setMessage(friendly.text);
      setMessageIsError(true);
      setTechnicalErrorCode(friendly.code);
      onError?.(errorMessage);
    } finally {
      setOperation("READY");
    }
  };

  const buildPreview = async (target: Analysis) => {
    const sectionPreviews = await Promise.all(target.sections.map(async (section) => {
      const response = await financeBridge.query<Json>("GetImportSectionPreview", { analysis_id: target.analysis_id, section_id: section.section_id });
      return response.data;
    }));
    const validated = await financeBridge.command("ImportMappedSections", {
      analysis_id: target.analysis_id, parser_profile: target.import_profile,
      parser_version: target.profile_version, import_mode: "VALIDATE_ONLY",
    }) as { result?: Json };
    const validationResult = validated.result ?? null;
    setPreviews(sectionPreviews);
    setValidation(validationResult);
    setPreviewConfirmed(false);
    setStep(4);
    return validationResult;
  };

  const prepareKnownFollowUp = async (target: Analysis): Promise<boolean> => {
    const supported = target.sections.filter((section) => section.import_supported && section.section_type !== "UNKNOWN");
    const deterministic = Boolean(target.import_profile)
      && target.warnings.length === 0
      && supported.length === target.sections.length
      && supported.every((section) => section.warnings.length === 0 && section.mapped_account_id);
    if (!deterministic) return false;

    const automaticMappings = supported.map((section) => ({
      section_id: section.section_id,
      action: "USE_EXISTING_ACCOUNT",
      account_id: section.mapped_account_id,
    }));
    await financeBridge.command("MapImportSections", { analysis_id: target.analysis_id, section_mappings: automaticMappings });
    setMappings(Object.fromEntries(supported.map((section) => [section.section_id, String(section.mapped_account_id)])));

    const mappedWizard = await financeBridge.query<Wizard | null>("GetImportWizardState", { export_id: target.export_id });
    const requirements = mappedWizard.data?.requirements ?? [];
    const unresolved = requirements.filter((requirement) => Boolean(requirement.required) && !Boolean(requirement.satisfied));
    for (const requirement of unresolved) {
      const suggestion = requirement.suggested_opening_balance as Json | undefined;
      if (requirement.requirement_type !== "OPENING_BALANCE" || !suggestion?.eligible || !suggestion.suggested_value) {
        await refresh(target.export_id);
        setStep(3);
        return false;
      }
      const accountId = String(requirement.account_id);
      const value = String(suggestion.suggested_value);
      await financeBridge.command("RecordOpeningBalance", {
        account_id: accountId,
        balance_date: previousDay(target.period_start),
        booked_balance: value,
        available_balance: null,
        currency: String(suggestion.currency ?? "EUR"),
        source: "PREVIOUS_SYSTEM",
        confirmation: true,
        comment: "Anfangssaldo aus der geprüften Vorperiode automatisch übernommen",
        carry_forward_source_reconciliation_id: suggestion.source_reconciliation_id ?? null,
        suggested_value: value,
        opening_balance_source: suggestion.source ?? "REPORTED_PRIOR_CLOSING_BALANCE",
        source_period: suggestion.source_period ?? null,
        source_account_id: suggestion.source_account_id ?? accountId,
        source_projection_sequence: suggestion.source_projection_sequence ?? null,
      });
      setOpeningBalances((current) => ({ ...current, [accountId]: value }));
      setOpeningSuggestions((current) => ({ ...current, [accountId]: suggestion }));
    }

    const validationResult = await buildPreview(target);
    await refresh(target.export_id);
    setStep(4);
    const blockers = ((validationResult?.section_results as Json[] | undefined) ?? [])
      .filter((item) => item.status === "FAILED" || item.status === "REVIEW_REQUIRED");
    return blockers.length === 0;
  };

  const chooseFile = async () => {
    try {
      const selected = await financeBridge.selectImportFile();
      if (!selected) {
        setMessage("Keine Datei ausgewählt. Die Dateiauswahl bleibt vollständig im Desktop-Host.");
        return;
      }
      setSelectedFile(selected);
      setHappyPath(false);
      setFormatRecoveryNeeded(false);
      await run("VALIDATING", async () => {
        const response = await financeBridge.command("AnalyzeImportFile", {
          source_file_reference: selected.file_ref,
          requested_profile: "GermanMultiAccountCsvV1",
          ...(bankIdentifier.trim() ? { confirmed_bank_identifier: bankIdentifier.trim() } : {}),
        }) as { result?: Analysis };
        const analyzed = response.result;
        if (!analyzed) throw new Error("Die Importanalyse lieferte kein Ergebnis.");
        const prepared = await prepareKnownFollowUp(analyzed);
        setHappyPath(prepared);
        if (prepared) {
          setMessage("Format, Konten und Salden wurden erkannt. Die geprüfte Vorschau ist bereit.");
          return;
        }
        await refresh(analyzed.export_id);
        const needsAnalysisReview = analyzed.warnings.length > 0 || analyzed.sections.some((section) => section.warnings.length > 0 || !section.import_supported);
        setFormatRecoveryNeeded(needsAnalysisReview);
        setStep(needsAnalysisReview ? 1 : analyzed.sections.every((section) => section.mapped_account_id) ? 3 : 2);
        setMessage(needsAnalysisReview ? "Die Analyse enthält Hinweise und muss geprüft werden." : "Der Import benötigt noch eine fachliche Zuordnung oder einen Anfangswert.");
      }, (errorMessage) => setFormatRecoveryNeeded(needsFormatRecovery(errorMessage)));
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Die lokale Dateiauswahl ist fehlgeschlagen.";
      setFormatRecoveryNeeded(needsFormatRecovery(errorMessage));
      const friendly = friendlyImportError(errorMessage);
      setMessage(friendly.text);
      setMessageIsError(true);
      setTechnicalErrorCode(friendly.code);
    }
  };

  const chooseUnknownFile = async () => {
    if (!bankIdentifier.trim()) {
      setMessage("Gib zuerst eine Bankkennung an, damit Formatänderungen sicher erkannt werden können.");
      return;
    }
    try {
      const selected = await financeBridge.selectImportFile();
      if (!selected) return;
      setSelectedFile(selected);
      await run("VALIDATING", async () => {
        const response = await financeBridge.command("AnalyzeUnknownImportFormat", {
          source_file_reference: selected.file_ref,
          bank_identifier: bankIdentifier.trim(),
          regenerate: Boolean(unknownFormat),
        }) as { result?: UnknownFormatResult };
        if (!response.result) throw new Error("Die Formatanalyse lieferte kein Ergebnis.");
        setUnknownFormat(response.result);
        const proposal = response.result.proposal;
        if (proposal) {
          const mapping: EditableImportMapping = {
            section_type: proposal.section_type,
            field_mappings: proposal.field_mappings,
            transformations: proposal.transformations,
            constants: proposal.constants,
            ignored_columns: proposal.ignored_columns,
            confidence: proposal.confidence,
            warnings: proposal.warnings,
          };
          setEditableMapping(mapping);
          setMappingValidation(proposal.validation);
        }
        setMessage(response.result.status === "KNOWN_EXACT"
          ? "Importformat erkannt. Das bestätigte Profil wird ohne AI wiederverwendet."
          : response.result.ai_used
            ? "Das lokale Modell hat einen bearbeitbaren Vorschlag erstellt. Noch wurde nichts importiert."
            : "Lokale AI ist nicht verfügbar. Das Mapping kann vollständig manuell erstellt werden.");
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Die lokale Formatanalyse ist fehlgeschlagen.");
    }
  };

  const validateEditedMapping = useCallback(async (mapping: EditableImportMapping) => {
    if (!unknownFormat?.proposal) return;
    try {
      const response = await financeBridge.command("ValidateImportMapping", {
        proposal_id: unknownFormat.proposal.proposal_id,
        mapping,
      }) as { result?: MappingValidation };
      if (response.result) setMappingValidation(response.result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Das Mapping konnte nicht validiert werden.");
    }
  }, [unknownFormat]);

  useEffect(() => {
    if (!editableMapping || !unknownFormat?.proposal) return;
    const timer = window.setTimeout(() => { void validateEditedMapping(editableMapping); }, 350);
    return () => window.clearTimeout(timer);
  }, [editableMapping, unknownFormat, validateEditedMapping]);

  const confirmEditedMapping = async () => {
    if (!editableMapping || !unknownFormat?.proposal || !mappingValidation) return;
    await run("EXECUTING", async () => {
      const response = await financeBridge.command("ConfirmImportMapping", {
        proposal_id: unknownFormat.proposal?.proposal_id,
        final_mapping: editableMapping,
        validation_result_id: mappingValidation.validation_result_id,
        confirmation_comment: mappingComment.trim() || null,
        compatibility_rules: allowAdditionalColumns ? ["ALLOW_ADDITIONAL_COLUMNS"] : [],
      }) as { result?: { profile?: Json } };
      setUnknownFormat((current) => current ? { ...current, status: "KNOWN_EXACT", profile: response.result?.profile } : current);
      setMessage("Mapping bestätigt. Das versionierte Profil wird bei identischen Folgeexporten deterministisch und ohne AI verwendet.");
    });
  };

  const updateEditableMapping = (value: EditableImportMapping) => {
    setEditableMapping(value);
    setMappingValidation(null);
  };

  const rejectEditedMapping = async () => {
    if (!unknownFormat?.proposal) return;
    await run("EXECUTING", async () => {
      await financeBridge.command("RejectImportMapping", {
        proposal_id: unknownFormat.proposal?.proposal_id,
        reason: mappingComment.trim() || null,
      });
      setUnknownFormat(null);
      setEditableMapping(null);
      setMappingValidation(null);
      setMessage("Der Mapping-Vorschlag wurde verworfen. Es wurden keine Finanzdaten importiert.");
    });
  };

  const confirmAnalysis = () => {
    if (!analysis) return;
    if (!bankIdentifier.trim()) {
      showValidationErrors({ bank_identifier: "Gib eine Bankkennung an." });
      return;
    }
    setFieldErrors({});
    setMessageIsError(false);
    setStep(2);
    setMessage("Bank, Zeitraum und erkannte Abschnitte wurden geprüft.");
  };

  const createAccountFor = async (section: Section) => {
    const displayName = newAccountName[section.section_id]?.trim();
    if (!displayName) {
      showValidationErrors({ [`account_name:${section.section_id}`]: "Gib einen Namen für das neue Konto ein." });
      return;
    }
    clearFieldError(`account_name:${section.section_id}`);
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
    const errors: FieldErrors = {};
    const sectionMappings = analysis.sections.map((section) => {
      const skip = section.section_type === "UNKNOWN" || mappings[section.section_id] === "__SKIP__";
      if (skip && !skipConfirmed[section.section_id]) errors[`skip:${section.section_id}`] = "Bestätige ausdrücklich, dass dieser Abschnitt übersprungen werden soll.";
      if (!skip && !mappings[section.section_id]) errors[`mapping:${section.section_id}`] = "Wähle ein bestehendes Konto oder lege ein neues an.";
      return {
        section_id: section.section_id,
        action: skip ? "SKIP_SECTION" : "USE_EXISTING_ACCOUNT",
        account_id: skip ? null : mappings[section.section_id],
      };
    });
    if (Object.keys(errors).length) {
      showValidationErrors(errors);
      return;
    }
    setFieldErrors({});
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
    const errors: FieldErrors = {};
    for (const section of analysis.sections) {
      const accountId = mappings[section.section_id] ?? section.mapped_account_id;
      if (!accountId || accountId === "__SKIP__") continue;
      if (section.section_type === "CHECKING" || section.section_type === "SAVINGS") {
        const opening = openingBalances[accountId]?.trim() ?? "";
        const closing = closingBalances[accountId]?.trim() ?? "";
        if (!opening) errors[`opening:${accountId}`] = "Der Anfangssaldo ist erforderlich.";
        else if (!isValidDecimal(opening)) errors[`opening:${accountId}`] = "Gib einen gültigen Betrag ein, zum Beispiel 1.234,56 oder 1234.56.";
        if (closing && !isValidDecimal(closing)) errors[`closing:${accountId}`] = "Gib einen gültigen Betrag ein oder lasse das optionale Feld leer.";
        const suggestion = openingSuggestions[accountId];
        const suggestedValue = suggestion?.eligible ? String(suggestion.suggested_value ?? "") : "";
        const deviates = Boolean(opening && suggestedValue && decimalValue(opening) !== decimalValue(suggestedValue));
        if (deviates && !adjustmentReasons[accountId]?.trim()) errors[`reason:${accountId}`] = "Eine Begründung für die Abweichung ist erforderlich.";
      }
      if (section.section_type === "BROKERAGE") {
        const rows = positions[accountId] ?? [];
        if (!rows.length && !emptyPositions[accountId]) errors[`positions:${accountId}`] = "Erfasse mindestens eine Anfangsposition oder bestätige den leeren Bestand.";
        rows.forEach((row, index) => {
          if (!String(row.security_identifier ?? "").trim()) errors[`position:${accountId}:${index}:identifier`] = "Die Wertpapierkennung fehlt.";
          if (!String(row.security_name ?? "").trim()) errors[`position:${accountId}:${index}:name`] = "Der Wertpapiername fehlt.";
          const openingQuantity = String(row.opening_quantity ?? "").trim();
          const closingQuantity = String(row.closing_quantity ?? "").trim();
          if (!openingQuantity) errors[`position:${accountId}:${index}:opening`] = "Der Anfangsbestand fehlt.";
          else if (!isValidDecimal(openingQuantity)) errors[`position:${accountId}:${index}:opening`] = "Gib einen gültigen Bestand ein.";
          if (closingQuantity && !isValidDecimal(closingQuantity)) errors[`position:${accountId}:${index}:closing`] = "Gib einen gültigen Bestand ein oder lasse das Feld leer.";
        });
      }
    }
    if (Object.keys(errors).length) {
      showValidationErrors(errors);
      return;
    }
    setFieldErrors({});
    await run("EXECUTING", async () => {
      for (const section of analysis.sections) {
        const accountId = mappings[section.section_id];
        if (!accountId || accountId === "__SKIP__") continue;
        if (section.section_type === "CHECKING" || section.section_type === "SAVINGS") {
          if (!openingBalances[accountId]) throw new Error(`Der bestätigte Anfangssaldo für „${section.original_title}“ fehlt.`);
          const suggestion = openingSuggestions[accountId];
          const suggestedValue = suggestion?.eligible ? String(suggestion.suggested_value) : undefined;
          const deviatesFromSuggestion = suggestedValue !== undefined && decimalValue(suggestedValue) !== decimalValue(openingBalances[accountId]);
          const reason = adjustmentReasons[accountId]?.trim();
          if (deviatesFromSuggestion && !reason) {
            throw new Error(`Für die Abweichung vom automatisch übernommenen Anfangssaldo bei „${section.original_title}“ ist eine Begründung erforderlich.`);
          }
          await financeBridge.command("RecordOpeningBalance", {
            account_id: accountId, balance_date: previousDay(analysis.period_start),
            booked_balance: decimalValue(openingBalances[accountId]), available_balance: null, currency: "EUR",
            source: "MANUAL_ENTRY", confirmation: true, comment: "Im Importassistenten bestätigt",
            ...(suggestedValue !== undefined ? {
              suggested_value: suggestedValue,
              carry_forward_source_reconciliation_id: suggestion?.source_reconciliation_id ?? null,
              opening_balance_source: suggestion?.source ?? "REPORTED_PRIOR_CLOSING_BALANCE",
              source_period: suggestion?.source_period ?? null,
              source_account_id: suggestion?.source_account_id ?? accountId,
              source_projection_sequence: suggestion?.source_projection_sequence ?? null,
            } : {}),
            ...(suggestedValue === undefined ? { opening_balance_source: "MANUAL" } : {}),
            ...(deviatesFromSuggestion ? { adjustment_reason: reason } : {}),
          });
          if (closingBalances[accountId]?.trim()) {
            await financeBridge.command("RecordClosingBalance", {
              account_id: accountId, balance_date: analysis.period_end,
              booked_balance: decimalValue(closingBalances[accountId]), available_balance: null, currency: "EUR",
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
              security_name: row.security_name, quantity: decimalValue(String(row.opening_quantity)), valuation_price: "0",
              price_currency: "EUR", market_value: "0", valuation_source: "MANUAL_ENTRY", confirmation: true,
            });
            if (row.closing_quantity !== "") {
              await financeBridge.command("RecordClosingSecurityPosition", {
                account_id: accountId, valuation_date: analysis.period_end,
                security_identifier_type: row.security_identifier_type, security_identifier: row.security_identifier,
                security_name: row.security_name, quantity: decimalValue(String(row.closing_quantity)), confirmation: true,
              });
            }
          }
        }
      }
      await buildPreview(analysis);
      setHappyPath(false);
      setMessage("Anfangswerte wurden gespeichert; die vollständige Vorschau ist bereit.");
    });
  };

  const executeImport = async (explicitConfirmation = previewConfirmed) => {
    if (!analysis || !explicitConfirmation) {
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
      const [execution, historyDetail] = await Promise.all([
        query<Json | null>("GetImportExecutionResult", { export_id: analysis.export_id }),
        query<Json | null>("GetImportHistoryDetail", { export_id: analysis.export_id }),
      ]);
      const baseResult = execution.data ?? response.result ?? null;
      const reviewSummary = historyDetail.data?.review_summary as Json | undefined;
      setResult(baseResult && reviewSummary ? { ...baseResult, review_summary: reviewSummary } : baseResult);
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
    await run("VALIDATING", async () => {
      const restored = await refresh(row.export_id);
      if (!restored) throw new Error("IMPORT_ANALYSIS_NOT_FOUND");
      if (!restored.can_resume || restored.status === "FAILED") throw new Error("IMPORT_RESUME_NOT_ALLOWED");
      if (restored.current_step === 4) await buildPreview(restored.analysis);
      setDetail(null);
      setHappyPath(false);
      setMessageIsError(false);
      setMessage(`Import ${row.report_month} wurde an der gespeicherten Stelle geöffnet.`);
      workspaceTop.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    });
  };

  useEffect(() => {
    if (!focusExportId || openedFocus === focusExportId || !history.data) return;
    if (focusView === "format_mapping") {
      setOpenedFocus(focusExportId);
      void (async () => {
        const proposalResponse = await query<MappingProposal>("GetImportMappingProposal", { proposal_id: focusExportId });
        const proposal = proposalResponse.data;
        if (!proposal) return;
        const formatResponse = await query<Json>("GetFormatChangeDetails", { analysis_id: proposal.analysis_id });
        const fingerprint = (formatResponse.data?.fingerprint as Json | undefined) ?? {};
        const sourceColumns = Array.from(new Set([
          ...proposal.field_mappings.map((item) => item.source_column),
          ...proposal.ignored_columns,
        ]));
        setUnknownFormat({
          status: "PROPOSAL_READY",
          format_status: String(formatResponse.data?.status ?? "FORMAT_CHANGED"),
          analysis_id: proposal.analysis_id,
          ai_used: proposal.model_id !== "manual",
          source_structure: {
            columns: sourceColumns,
            encoding: String(fingerprint.encoding ?? "utf-8"),
            delimiter: String(fingerprint.delimiter ?? ";"),
            section_title: "Importabschnitt",
          },
          proposal,
        });
        setEditableMapping({
          section_type: proposal.section_type,
          field_mappings: proposal.field_mappings,
          transformations: proposal.transformations,
          constants: proposal.constants,
          ignored_columns: proposal.ignored_columns,
          confidence: proposal.confidence,
          warnings: proposal.warnings,
        });
        setMappingValidation(proposal.validation);
      })();
      return;
    }
    const row = history.data.imports.find((item) => item.export_id === focusExportId);
    if (!row) return;
    setOpenedFocus(focusExportId);
    void openHistory(row);
  }, [focusExportId, focusView, history.data, openedFocus]);

  if (incompatible.length || manifest.contract_version && !["1.3.0", "1.4.0", "1.5.0"].includes(manifest.contract_version)) {
    return <section className="panel import-blocker" role="alert"><h2>Import nicht kompatibel</h2><p>Die installierte lokale Extension stellt den vollständigen 1.5.0-Importvertrag nicht bereit.</p><code>{incompatible.join(", ") || `Vertrag ${manifest.contract_version}`}</code></section>;
  }

  return <div className="imports-workspace" ref={workspaceTop}>
    <section className="import-hero panel">
      <span className="import-icon" aria-hidden="true">⇩</span>
      <div><h2>Monatlichen Bankexport importieren</h2><p>Eine lokale Datei, eine Bank, ein Berichtsmonat – mehrere getrennt geprüfte Kontoabschnitte.</p></div>
      <div className="import-hero-actions"><button className="primary" disabled={busy} onClick={chooseFile}>{selectedFile ? "Andere CSV wählen" : "CSV auswählen"}</button>{(analysis || unknownFormat) && (formatRecoveryNeeded || unknownFormat) && <button className="secondary" disabled={busy || !bankIdentifier.trim()} onClick={chooseUnknownFile}>{unknownFormat ? "Vorschlag neu erzeugen" : "Formatproblem lösen"}</button>}</div>
    </section>
    <ol className="import-stepper" aria-label="Importfortschritt">
      {steps.map((title, index) => <li key={title} className={step === index + 1 ? "active" : step > index + 1 ? "complete" : ""} aria-current={step === index + 1 ? "step" : undefined}><span>{step > index + 1 ? "✓" : index + 1}</span><strong>{title}</strong>{index < steps.length - 1 && <i aria-hidden="true" />}</li>)}
    </ol>
    {operation !== "READY" && <div className="status-banner" role="status"><strong>{operation === "VALIDATING" ? "Lokale Prüfung läuft" : "Import wird verarbeitet"}</strong><span>Bitte diesen Arbeitsbereich geöffnet lassen.</span></div>}
    {message && <div className={`toast${messageIsError ? " error" : ""}`} role={messageIsError ? "alert" : "status"} aria-live="polite"><span>{message}</span>{technicalErrorCode && <details><summary>Technische Details</summary><code>{technicalErrorCode}</code></details>}</div>}
    {unknownFormat && <ImportMappingEditor result={unknownFormat} mapping={editableMapping} validation={mappingValidation} comment={mappingComment} onComment={setMappingComment} allowAdditionalColumns={allowAdditionalColumns} onAllowAdditionalColumns={setAllowAdditionalColumns} onMapping={updateEditableMapping} onValidate={() => editableMapping && validateEditedMapping(editableMapping)} onConfirm={confirmEditedMapping} onReject={rejectEditedMapping} busy={busy} />}
    {wizard.state === "LOADING" && <section className="panel page-state"><span className="loader" /><p>Gespeicherten Importstand laden …</p></section>}
    {(wizard.state === "ERROR" || wizard.state === "INCOMPATIBLE_SCHEMA") && <section className="panel page-state error" role="alert"><h2>Importprojektion nicht verfügbar</h2><p>{wizard.error}</p><button className="secondary" onClick={() => refresh()}>Erneut versuchen</button></section>}
    {!analysis && !unknownFormat && wizard.state !== "LOADING" && <section className="panel import-empty"><h2>Neuen Import beginnen</h2><p>Wähle einen monatlichen CSV-Export. React erhält nur eine kurzlebige Dateireferenz, niemals den lokalen Pfad.</p><label>Bankkennung, falls nicht im Export enthalten<input value={bankIdentifier} onChange={(event) => setBankIdentifier(event.target.value)} placeholder="z. B. BANK_A" /></label><div className="empty-actions"><button className="primary" onClick={chooseFile}>Lokale CSV auswählen</button>{formatRecoveryNeeded && <button className="secondary" disabled={!bankIdentifier.trim()} onClick={chooseUnknownFile}>Formatproblem lösen</button>}</div></section>}
    {analysis && <>
      {uiMonth !== analysis.report_month && <div className="status-banner"><strong>Abweichender Auswertungsmonat</strong><span>UI: {uiMonth} · Importdatei: {analysis.report_month}. Importiert wird ausschließlich der Dateizeitraum.</span></div>}
      {step === 1 && <AnalysisStep analysis={analysis} fileName={selectedFile?.display_name} bankIdentifier={bankIdentifier} error={fieldErrors.bank_identifier} onBankIdentifier={(value) => { setBankIdentifier(value); clearFieldError("bank_identifier"); }} onConfirm={confirmAnalysis} />}
      {step === 2 && <MappingStep analysis={analysis} accounts={activeAccounts} mappings={mappings} skips={skipConfirmed} names={newAccountName} errors={fieldErrors} onMap={(id, value) => { setMappings((current) => ({ ...current, [id]: value })); clearFieldError(`mapping:${id}`); clearFieldError(`skip:${id}`); }} onSkip={(id, value) => { setSkipConfirmed((current) => ({ ...current, [id]: value })); clearFieldError(`skip:${id}`); }} onName={(id, value) => { setNewAccountName((current) => ({ ...current, [id]: value })); clearFieldError(`account_name:${id}`); }} onCreate={createAccountFor} onSave={saveMappings} busy={busy} />}
      {step === 3 && <ValuesStep analysis={analysis} mappings={mappings} opening={openingBalances} closing={closingBalances} suggestions={openingSuggestions} reasons={adjustmentReasons} emptyPositions={emptyPositions} positions={positions} errors={fieldErrors} onOpening={(id, value) => { setOpeningBalances((current) => ({ ...current, [id]: value })); clearFieldError(`opening:${id}`); }} onClosing={(id, value) => { setClosingBalances((current) => ({ ...current, [id]: value })); clearFieldError(`closing:${id}`); }} onReason={(id, value) => { setAdjustmentReasons((current) => ({ ...current, [id]: value })); clearFieldError(`reason:${id}`); }} onEmpty={(id, value) => { setEmptyPositions((current) => ({ ...current, [id]: value })); clearFieldError(`positions:${id}`); }} onAddPosition={addPosition} onPosition={(id, index, field, value) => { editPosition(id, index, field, value); const suffix = field === "security_identifier" ? "identifier" : field === "security_name" ? "name" : field === "opening_quantity" ? "opening" : field === "closing_quantity" ? "closing" : field; clearFieldError(`position:${id}:${index}:${suffix}`); }} onSave={saveInitialValues} busy={busy} />}
      {step === 4 && happyPath && <HappyPathPreview analysis={analysis} previews={previews} validation={validation} requirements={wizard.data?.requirements ?? []} onExecute={() => void executeImport(true)} busy={busy} />}
      {step === 4 && !happyPath && <PreviewStep analysis={analysis} previews={previews} validation={validation} confirmed={previewConfirmed} onConfirmed={setPreviewConfirmed} onExecute={() => void executeImport()} busy={busy} />}
      {step === 5 && <ResultStep analysis={analysis} result={result} onReconcile={reconcile} onRelation={relationAction} onOpenReviews={onOpenReviews} busy={busy} />}
    </>}
    <History result={history} onOpen={openHistory} onResume={resume} />
    {detail && <HistoryDetail detail={detail} onClose={() => setDetail(null)} />}
  </div>;
}

const mappingTargets = [
  ["booking_date", "Buchungsdatum"], ["value_date", "Wertstellung"],
  ["transaction_type_raw", "Vorgang"], ["counterparty", "Anbieter / Gegenpartei"],
  ["description", "Beschreibung"], ["amount", "Betrag"], ["currency", "Währung"],
] as const;

function ImportMappingEditor({ result, mapping, validation, comment, onComment, allowAdditionalColumns, onAllowAdditionalColumns, onMapping, onValidate, onConfirm, onReject, busy }: {
  result: UnknownFormatResult;
  mapping: EditableImportMapping | null;
  validation: MappingValidation | null;
  comment: string;
  onComment: (value: string) => void;
  allowAdditionalColumns: boolean;
  onAllowAdditionalColumns: (value: boolean) => void;
  onMapping: (value: EditableImportMapping) => void;
  onValidate: () => void;
  onConfirm: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  if (result.status === "KNOWN_EXACT" || result.status === "KNOWN_COMPATIBLE") {
    return <section className="panel mapping-editor recognized-format"><span className="mapping-status success">✓</span><div><span className="eyebrow">IMPORTFORMAT ERKANNT</span><h2>{String(result.profile?.name ?? "Bestätigtes Importprofil")}</h2><p>Dieses Profil wird deterministisch verwendet. Die lokale AI wurde nicht aufgerufen.</p><small>Profilversion {String(result.profile?.profile_version ?? "–")} · Quelle {String(result.profile?.source ?? "USER_CONFIRMED")}</small></div></section>;
  }
  if (!mapping || !result.proposal) return null;
  const columns = result.source_structure?.columns ?? Array.from(new Set([
    ...mapping.field_mappings.map((item) => item.source_column), ...mapping.ignored_columns,
  ]));
  const updateColumn = (source: string, target: string) => {
    const fields = mapping.field_mappings.filter((item) => item.source_column !== source);
    const ignored = mapping.ignored_columns.filter((item) => item !== source);
    if (!target) {
      onMapping({ ...mapping, field_mappings: fields, transformations: mapping.transformations, ignored_columns: [...ignored, source] });
      return;
    }
    const nextFields = [...fields, { source_column: source, target_field: target, confidence: 1 }];
    const hasTransformation = mapping.transformations.some((item) => item.target_field === target);
    const nextTransformations = hasTransformation ? mapping.transformations : [
      ...mapping.transformations,
      ...(target === "booking_date" || target === "value_date" ? [{ target_field: target, type: "PARSE_DATE", parameters: { format: "DD.MM.YYYY" } }] : []),
      ...(target === "amount" ? [{ target_field: target, type: "PARSE_DECIMAL", parameters: { decimal_separator: ",", thousands_separator: "." } }] : []),
    ];
    onMapping({ ...mapping, field_mappings: nextFields, transformations: nextTransformations, ignored_columns: ignored });
  };
  const updateTransformation = (target: string, value: string) => {
    const remaining = mapping.transformations.filter((item) => item.target_field !== target);
    if (!value) return onMapping({ ...mapping, transformations: remaining });
    const [type, preset] = value.split(":");
    const parameters: Json = type === "PARSE_DATE" ? { format: preset }
      : type === "PARSE_DECIMAL" ? { decimal_separator: preset === "DE" ? "," : ".", thousands_separator: preset === "DE" ? "." : "," }
        : {};
    onMapping({ ...mapping, transformations: [...remaining, { target_field: target, type, parameters }] });
  };
  const valid = validation?.status === "VALID" || validation?.status === "VALID_WITH_WARNINGS";
  const warningConfirmationMissing = validation?.status === "VALID_WITH_WARNINGS" && !comment.trim();
  return <section className="panel mapping-editor">
    <header><div><span className="eyebrow">LOKALER MAPPING-ASSISTENT</span><h2>{result.format_status === "FORMAT_CHANGED" ? "Geändertes Importformat prüfen" : "Spaltenzuordnung prüfen"}</h2><p>{result.ai_used ? "Vorschlag des lokalen Modells – vollständig bearbeitbar und noch nicht bestätigt." : "Manuelles Mapping – Finance bleibt auch ohne lokales Modell verfügbar."}</p></div><span className={`mapping-status ${validation?.status === "VALID" ? "success" : validation?.status === "INVALID" ? "error" : "review"}`}>{validation?.status ?? "PRÜFUNG"}</span></header>
    <div className="mapping-meta"><span>{result.source_structure?.section_title ?? "Tabellarischer Abschnitt"}</span><span>{result.source_structure?.encoding} · Trennzeichen „{result.source_structure?.delimiter}“</span><span>Modell: {result.ai_used ? `${result.proposal.model_id} ${result.proposal.model_version}` : "nicht verwendet"}</span></div>
    <div className="mapping-options"><label>Abschnittstyp<select value={mapping.section_type} onChange={(event) => onMapping({ ...mapping, section_type: event.target.value })}><option value="CHECKING">Girokonto</option><option value="SAVINGS">Sparkonto</option><option value="BROKERAGE">Depot</option><option value="UNKNOWN">Unbekannt</option></select></label><label>Konstante Währung<input maxLength={3} placeholder="z. B. EUR" value={mapping.constants.currency ?? ""} onChange={(event) => onMapping({ ...mapping, constants: event.target.value ? { ...mapping.constants, currency: event.target.value.toUpperCase() } : {} })} /></label></div>
    <div className="mapping-table" role="table" aria-label="Importzuordnung">
      <div className="mapping-table-head" role="row"><span>Quellspalte</span><span>Zielfeld</span><span>Transformation</span><span>Status</span></div>
      {columns.map((source) => { const field = mapping.field_mappings.find((item) => item.source_column === source); const transform = mapping.transformations.find((item) => item.target_field === field?.target_field); const transformValue = transform?.type === "PARSE_DATE" ? `${transform.type}:${String(transform.parameters.format)}` : transform?.type === "PARSE_DECIMAL" ? `${transform.type}:${transform.parameters.decimal_separator === "," ? "DE" : "EN"}` : transform?.type ?? ""; return <div className="mapping-table-row" role="row" key={source}><strong>{source}</strong><select aria-label={`Zielfeld für ${source}`} value={field?.target_field ?? ""} onChange={(event) => updateColumn(source, event.target.value)}><option value="">Spalte ignorieren</option>{mappingTargets.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><select aria-label={`Transformation für ${source}`} disabled={!field} value={transformValue} onChange={(event) => field && updateTransformation(field.target_field, event.target.value)}><option value="">Keine</option><option value="TRIM">Leerzeichen entfernen</option><option value="PARSE_DATE:DD.MM.YY">Datum DD.MM.YY</option><option value="PARSE_DATE:DD.MM.YYYY">Datum DD.MM.YYYY</option><option value="PARSE_DATE:YYYY-MM-DD">Datum YYYY-MM-DD</option><option value="PARSE_DECIMAL:DE">Dezimalzahl deutsch</option><option value="PARSE_DECIMAL:EN">Dezimalzahl international</option><option value="NORMALIZE_CURRENCY">Währung normalisieren</option></select><span className={field ? "row-valid" : "row-ignored"}>{field ? "zugeordnet" : "ignoriert"}</span></div>; })}
    </div>
    <div className={`mapping-validation ${validation?.status === "INVALID" ? "error" : "success"}`}><strong>{validation ? `${validation.rows_valid} / ${validation.rows_total} Datensätzen gültig` : "Validierung ausstehend"}</strong>{validation && <span>{validation.rows_invalid} fehlerhaft</span>}{validation?.errors.map((error) => <code key={error}>{error}</code>)}</div>
    <label className="mapping-comment">Bestätigungskommentar {validation?.status === "VALID_WITH_WARNINGS" ? "(bei Warnungen erforderlich)" : "(optional)"}<textarea value={comment} onChange={(event) => onComment(event.target.value)} /></label>
    <label className="mapping-compatibility"><input type="checkbox" checked={allowAdditionalColumns} onChange={(event) => onAllowAdditionalColumns(event.target.checked)} /> Künftig zusätzliche, nicht zugeordnete Spalten nach vollständigem Dry Run zulassen. Strukturänderungen werden weiterhin auditiert.</label>
    <footer><div><button className="secondary" disabled={busy} onClick={() => onMapping({ section_type: result.proposal!.section_type, field_mappings: result.proposal!.field_mappings, transformations: result.proposal!.transformations, constants: result.proposal!.constants, ignored_columns: result.proposal!.ignored_columns, confidence: result.proposal!.confidence, warnings: result.proposal!.warnings })}>Vorschlag zurücksetzen</button><button className="secondary" disabled={busy} onClick={onValidate}>Erneut validieren</button></div><div><button className="danger-link" disabled={busy} onClick={onReject}>Verwerfen</button><button className="primary" disabled={busy || !valid || warningConfirmationMissing} onClick={onConfirm}>Mapping bestätigen und Profil anlegen</button></div></footer>
  </section>;
}

function AnalysisStep({ analysis, fileName, bankIdentifier, error, onBankIdentifier, onConfirm }: { analysis: Analysis; fileName?: string; bankIdentifier: string; error?: string; onBankIdentifier: (value: string) => void; onConfirm: () => void }) {
  return <section className="panel import-stage">
    <header><span className="eyebrow">SCHRITT 1</span><h2>Datei analysieren</h2><p>Prüfe Bank, Monat, Parserprofil und Abschnittsstruktur.</p></header>
    <dl className="import-facts">
      <div className="fact-file"><dt>Datei</dt><dd>{fileName ?? "Gespeicherte Dateireferenz"}</dd></div>
      <div className="fact-bank"><dt>Bank</dt><dd><input value={bankIdentifier} onChange={(event) => onBankIdentifier(event.target.value)} aria-label="Bankkennung" aria-invalid={Boolean(error)} aria-describedby={error ? "bank-identifier-error" : undefined} />{error && <small className="field-error" id="bank-identifier-error" role="alert">{error}</small>}</dd></div>
      <div className="fact-month"><dt>Berichtsmonat</dt><dd>{analysis.report_month}</dd></div>
      <div className="fact-period"><dt>Zeitraum</dt><dd>{analysis.period_start} – {analysis.period_end}</dd></div>
      <div className="fact-profile"><dt>Parserprofil</dt><dd>{analysis.import_profile}<small>Version {analysis.profile_version}</small></dd></div>
      <div className="fact-format"><dt>Dateiformat</dt><dd>{analysis.encoding}<small>Trennzeichen „{analysis.delimiter}“</small></dd></div>
      <div className="fact-content"><dt>Dateiinhalt</dt><dd><code>{shortHash(analysis.source_file_hash)}</code><small>{analysis.file_size.toLocaleString("de-DE")} Bytes</small></dd></div>
    </dl>
    <div className="section-overview"><div><h3>Erkannte Kontoabschnitte</h3><p>{analysis.sections.length} Abschnitt{analysis.sections.length === 1 ? "" : "e"} wurden getrennt geprüft.</p></div><div className="section-cards">{analysis.sections.map((section) => <article key={section.section_id}><div><strong>{section.original_title}</strong><span className="status-badge">{section.section_type}</span></div><p><b>{section.record_count}</b> Datensätze · {section.empty ? "leer und gültig" : "Daten erkannt"}</p>{section.account_reference && <small>{section.account_reference}</small>}{section.warnings.map((warning) => <small className="warning" key={warning}>{warning}</small>)}</article>)}</div></div>
    <footer><button className="primary" onClick={onConfirm}>Bank und Monat bestätigen</button></footer>
  </section>;
}

function MappingStep({ analysis, accounts, mappings, skips, names, errors, onMap, onSkip, onName, onCreate, onSave, busy }: { analysis: Analysis; accounts: Account[]; mappings: Record<string, string>; skips: Record<string, boolean>; names: Record<string, string>; errors: FieldErrors; onMap: (id: string, value: string) => void; onSkip: (id: string, value: boolean) => void; onName: (id: string, value: string) => void; onCreate: (section: Section) => void; onSave: () => void; busy: boolean }) {
  return <section className="panel import-stage">
    <header><span className="eyebrow">SCHRITT 2</span><h2>Konten zuordnen</h2><p>Ordne jeden erkannten Abschnitt einem bestehenden Konto zu oder lege direkt ein neues an.</p></header>
    <div className="mapping-list">{analysis.sections.map((section) => {
      const compatible = accounts.filter((account) => account.account_type === section.section_type);
      const skipping = mappings[section.section_id] === "__SKIP__" || section.section_type === "UNKNOWN";
      return <article key={section.section_id}>
        <div className="mapping-section"><span className="status-badge">{section.section_type}</span><strong>{section.original_title}</strong><small>{section.account_reference ?? "Keine Kontoreferenz im Export"}</small></div>
        <div className="mapping-controls">
          <label>Bestehendes Konto<select value={section.section_type === "UNKNOWN" ? "__SKIP__" : mappings[section.section_id] ?? ""} onChange={(event) => onMap(section.section_id, event.target.value)} aria-invalid={Boolean(errors[`mapping:${section.section_id}`])}><option value="">Konto auswählen …</option>{compatible.map((account) => <option value={account.account_id} key={account.account_id}>{account.display_name} · {account.masked_reference ?? account.institution}</option>)}<option value="__SKIP__">Abschnitt bewusst überspringen</option></select>{errors[`mapping:${section.section_id}`] && <small className="field-error" role="alert">{errors[`mapping:${section.section_id}`]}</small>}</label>
          {skipping ? <label className="danger-confirm"><span><input type="checkbox" checked={Boolean(skips[section.section_id])} onChange={(event) => onSkip(section.section_id, event.target.checked)} aria-invalid={Boolean(errors[`skip:${section.section_id}`])} /> Überspringen ausdrücklich bestätigen</span>{errors[`skip:${section.section_id}`] && <small className="field-error" role="alert">{errors[`skip:${section.section_id}`]}</small>}</label> : <div className="inline-create"><label>Oder neues Konto anlegen<input placeholder="Kontoname, z. B. Girokonto" value={names[section.section_id] ?? ""} onChange={(event) => onName(section.section_id, event.target.value)} aria-invalid={Boolean(errors[`account_name:${section.section_id}`])} />{errors[`account_name:${section.section_id}`] && <small className="field-error" role="alert">{errors[`account_name:${section.section_id}`]}</small>}</label><button className="secondary small" onClick={() => onCreate(section)}>Konto anlegen</button></div>}
        </div>
      </article>;
    })}</div>
    <footer><button className="primary" disabled={busy} onClick={onSave}>Zuordnungen speichern</button></footer>
  </section>;
}

function ValuesStep({ analysis, mappings, opening, closing, suggestions, reasons, emptyPositions, positions, errors, onOpening, onClosing, onReason, onEmpty, onAddPosition, onPosition, onSave, busy }: { analysis: Analysis; mappings: Record<string, string>; opening: Record<string, string>; closing: Record<string, string>; suggestions: Record<string, Json>; reasons: Record<string, string>; emptyPositions: Record<string, boolean>; positions: Record<string, Json[]>; errors: FieldErrors; onOpening: (id: string, value: string) => void; onClosing: (id: string, value: string) => void; onReason: (id: string, value: string) => void; onEmpty: (id: string, value: boolean) => void; onAddPosition: (id: string) => void; onPosition: (id: string, index: number, field: string, value: string) => void; onSave: () => void; busy: boolean }) {
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 3</span><h2>Anfangswerte prüfen</h2><p>Salden und Depotbestände werden je Konto getrennt bestätigt.</p></header><div className="values-list">{analysis.sections.map((section) => {
    const accountId = mappings[section.section_id] ?? section.mapped_account_id ?? "";
    if (!accountId || accountId === "__SKIP__") return null;
    if (section.section_type !== "BROKERAGE") {
      const suggestion = suggestions[accountId];
      const eligible = Boolean(suggestion?.eligible);
      const suggestedValue = eligible ? String(suggestion.suggested_value) : undefined;
      const deviates = suggestedValue !== undefined && decimalValue(suggestedValue) !== decimalValue(opening[accountId] ?? "");
      return <article key={section.section_id}>
        <h3>{section.original_title}</h3>
        <div className="field-grid">
          <label>Anfangssaldo am {previousDay(analysis.period_start)} <span className="required-mark">Pflichtfeld</span><input inputMode="decimal" value={opening[accountId] ?? ""} onChange={(event) => onOpening(accountId, event.target.value)} aria-invalid={Boolean(errors[`opening:${accountId}`])} />{errors[`opening:${accountId}`] && <small className="field-error" role="alert">{errors[`opening:${accountId}`]}</small>}</label>
          <label>Gemeldeter Endsaldo am {analysis.period_end} <span className="optional-mark">optional</span><input inputMode="decimal" value={closing[accountId] ?? ""} onChange={(event) => onClosing(accountId, event.target.value)} placeholder="z. B. 1.234,56" aria-invalid={Boolean(errors[`closing:${accountId}`])} />{errors[`closing:${accountId}`] && <small className="field-error" role="alert">{errors[`closing:${accountId}`]}</small>}</label>
        </div>
        {eligible ? <div className="opening-balance-hint success"><strong>Automatisch ermittelt</strong><br /><small>Quelle: {suggestion.source === "CALCULATED_PRIOR_CLOSING_BALANCE" ? "Berechneter" : "Bestätigter gemeldeter"} Endsaldo {String(suggestion.source_period ?? suggestion.period_end ?? "")}</small>{Boolean(suggestion.calculation) && <details><summary>Berechnung anzeigen</summary><dl className="balance-calculation"><div><dt>Anfangssaldo</dt><dd>{String((suggestion.calculation as Json).opening_balance ?? "–")}</dd></div><div><dt>Saldenbewegungen</dt><dd>{String((suggestion.calculation as Json).balance_relevant_movements ?? "–")}</dd></div><div><dt>Berechneter Endsaldo</dt><dd>{String((suggestion.calculation as Json).calculated_closing_balance ?? "–")}</dd></div></dl></details>}</div>
          : suggestion && <p className="opening-balance-hint warning">Der Anfangssaldo konnte nicht automatisch übernommen werden.<br /><small>{OPENING_BALANCE_REASON_TEXT[String(suggestion.reason_code ?? "")] ?? "Der vorherige Monat besitzt keinen abschließend bestätigten Endsaldo."}</small></p>}
        {deviates && <label className="danger-confirm">Begründung für die Abweichung vom übernommenen Anfangssaldo <span className="required-mark">Pflichtfeld</span><textarea required value={reasons[accountId] ?? ""} onChange={(event) => onReason(accountId, event.target.value)} aria-invalid={Boolean(errors[`reason:${accountId}`])} />{errors[`reason:${accountId}`] && <small className="field-error" role="alert">{errors[`reason:${accountId}`]}</small>}</label>}
      </article>;
    }
    const rows = positions[accountId] ?? [];
    return <article key={section.section_id}><h3>{section.original_title}</h3><label className="danger-confirm"><span><input type="checkbox" checked={Boolean(emptyPositions[accountId])} onChange={(event) => onEmpty(accountId, event.target.checked)} /> Vor dem Berichtsmonat waren ausdrücklich keine Positionen vorhanden.</span>{errors[`positions:${accountId}`] && <small className="field-error" role="alert">{errors[`positions:${accountId}`]}</small>}</label>{rows.map((row, index) => <div className="position-entry" key={`${accountId}-${index}`}><div className="position-row"><select value={String(row.security_identifier_type)} onChange={(event) => onPosition(accountId, index, "security_identifier_type", event.target.value)}><option>ISIN</option><option>WKN</option><option>OTHER</option></select><input aria-label="Wertpapierkennung" placeholder="Kennung (Pflichtfeld)" value={String(row.security_identifier)} onChange={(event) => onPosition(accountId, index, "security_identifier", event.target.value)} aria-invalid={Boolean(errors[`position:${accountId}:${index}:identifier`])} /><input aria-label="Wertpapiername" placeholder="Name (Pflichtfeld)" value={String(row.security_name)} onChange={(event) => onPosition(accountId, index, "security_name", event.target.value)} aria-invalid={Boolean(errors[`position:${accountId}:${index}:name`])} /><input aria-label="Anfangsbestand" placeholder="Anfang (Pflichtfeld)" inputMode="decimal" value={String(row.opening_quantity)} onChange={(event) => onPosition(accountId, index, "opening_quantity", event.target.value)} aria-invalid={Boolean(errors[`position:${accountId}:${index}:opening`])} /><input aria-label="Endbestand" placeholder="Ende (optional)" inputMode="decimal" value={String(row.closing_quantity)} onChange={(event) => onPosition(accountId, index, "closing_quantity", event.target.value)} aria-invalid={Boolean(errors[`position:${accountId}:${index}:closing`])} /></div>{Object.entries(errors).filter(([key]) => key.startsWith(`position:${accountId}:${index}:`)).map(([key, value]) => <small className="field-error" role="alert" key={key}>{value}</small>)}</div>)}<button className="secondary small" onClick={() => onAddPosition(accountId)}>Position hinzufügen</button></article>;
  })}</div><footer><button className="primary" disabled={busy} onClick={onSave}>Anfangswerte bestätigen und Vorschau erstellen</button></footer></section>;
}

function HappyPathPreview({ analysis, previews, validation, requirements, onExecute, busy }: { analysis: Analysis; previews: Json[]; validation: Json | null; requirements: Json[]; onExecute: () => void; busy: boolean }) {
  const records = analysis.sections.reduce((sum, section) => sum + section.record_count, 0);
  const accountIds = new Set(analysis.sections.map((section) => section.mapped_account_id).filter(Boolean));
  const carried = requirements.filter((item) => Boolean((item.suggested_opening_balance as Json | undefined)?.eligible)).length;
  const validationRows = (validation?.section_results as Json[] | undefined) ?? [];
  return <section className="panel import-stage happy-import">
    <header><span className="eyebrow">BEREIT ZUM IMPORT</span><h2>{analysis.bank_identifier}</h2><p>{reportMonthLabel(analysis.report_month)}</p></header>
    <div className="happy-import-summary" aria-label="Importzusammenfassung">
      <div><strong>{accountIds.size}</strong><span>Konten erkannt</span></div>
      <div><strong>{records}</strong><span>Buchungen</span></div>
      <div><strong>✓</strong><span>Salden vollständig</span></div>
    </div>
    {carried > 0 && <p className="carry-forward-note">{carried === 1 ? "Ein Anfangssaldo wurde" : `${carried} Anfangssalden wurden`} aus den geprüften Endsalden der Vorperiode übernommen.</p>}
    <div className="happy-ready"><span aria-hidden="true">✓</span><div><strong>Alles bereit.</strong><small>Die Vorschau wurde lokal geprüft. Der Import beginnt erst nach deiner Bestätigung.</small></div></div>
    <div className="happy-import-actions"><button className="primary" disabled={busy} onClick={onExecute}>Importieren</button></div>
    <details className="import-technical-details"><summary>Details anzeigen</summary>
      <dl className="technical-facts">
        <div><dt>Fingerprint</dt><dd><code>{shortHash(analysis.source_file_hash)}</code></dd></div>
        <div><dt>Parser</dt><dd>{analysis.import_profile} · {analysis.profile_version}</dd></div>
        <div><dt>Format</dt><dd>{analysis.encoding} · Trennzeichen „{analysis.delimiter}“</dd></div>
        <div><dt>Validierung</dt><dd>{String(validation?.status ?? "VALID")} · {validationRows.length || analysis.sections.length} Abschnitte</dd></div>
      </dl>
      <div className="happy-section-list">{analysis.sections.map((section) => { const preview = previews.find((item) => item.section_id === section.section_id); return <article key={section.section_id}><div><strong>{section.original_title}</strong><small>{section.mapped_account_id} · {section.section_type}</small></div><span>{section.record_count} Buchungen</span><small>{String(preview?.first_booking_date ?? analysis.period_start)} – {String(preview?.last_booking_date ?? analysis.period_end)}</small></article>; })}</div>
      <p className="technical-note">{accountIds.size} eindeutige Kontozuordnungen · Hash und Mapping bleiben im Audit nachvollziehbar.</p>
    </details>
  </section>;
}

function PreviewStep({ analysis, previews, validation, confirmed, onConfirmed, onExecute, busy }: { analysis: Analysis; previews: Json[]; validation: Json | null; confirmed: boolean; onConfirmed: (value: boolean) => void; onExecute: () => void; busy: boolean }) {
  const blockers = ((validation?.section_results as Json[] | undefined) ?? []).filter((item) => item.status === "FAILED" || item.status === "REVIEW_REQUIRED");
  return <section className="panel import-stage"><header><span className="eyebrow">SCHRITT 4</span><h2>Vollständige Importvorschau</h2><p>Noch wurden keine Buchungen importiert. Prüfe jeden Abschnitt vor der verbindlichen Ausführung.</p></header>{blockers.length > 0 && <div className="status-banner" role="alert"><strong>{blockers.length} Blocker oder Prüfhinweise</strong><span>Der Import bleibt bis zur Klärung gesperrt.</span></div>}<div className="preview-list">{analysis.sections.map((section) => { const preview = previews.find((item) => item.section_id === section.section_id) ?? section as unknown as Json; const rows = (preview.preview as Json[] | undefined) ?? []; return <article key={section.section_id}><header><div><strong>{section.original_title}</strong><small>{section.section_type}</small></div><span className="status-badge">{section.empty ? "EMPTY_COMPLETED" : `${section.record_count} DATENSÄTZE`}</span></header><dl><div><dt>Erste Buchung</dt><dd>{String(preview.first_booking_date ?? "–")}</dd></div><div><dt>Letzte Buchung</dt><dd>{String(preview.last_booking_date ?? "–")}</dd></div><div><dt>Summe</dt><dd>{String(preview.amount_sum ?? "0")} EUR</dd></div><div><dt>Dublettenkandidaten</dt><dd>{String(preview.duplicate_candidate_count ?? 0)}</dd></div></dl>{rows.length > 0 && <div className="preview-rows">{rows.slice(0, 5).map((row, index) => <code key={index}>{String(row.booking_date ?? row.trade_date ?? "")} · {String(row.description ?? row.security_name ?? "")} · {String(row.amount ?? row.settlement_amount ?? "")}</code>)}</div>}{section.warnings.map((warning) => <small className="warning" key={warning}>{warning}</small>)}</article>; })}</div><label className="final-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => onConfirmed(event.target.checked)} /> Ich habe Bank, Zeitraum, Kontenzuordnung, Anfangswerte, Summen und Warnungen geprüft. Der Import darf jetzt als neue Events ausgeführt werden.</label><footer><button className="primary" disabled={busy || !confirmed || blockers.some((item) => item.status === "FAILED")} onClick={onExecute}>Import verbindlich ausführen</button></footer></section>;
}

function ResultStep({ analysis, result, onReconcile, onRelation, onOpenReviews, busy }: { analysis: Analysis; result: Json | null; onReconcile: (section: Section) => void; onRelation: (id: string, action: "Confirm" | "Reject" | "Break") => void; onOpenReviews?: () => void; busy: boolean }) {
  const sections = (result?.section_results as Json[] | undefined) ?? [];
  const relations = (result?.relations as Json[] | undefined) ?? [];
  const balanceReconciliations = (result?.balance_reconciliations as Json[] | undefined) ?? [];
  const positionReconciliations = (result?.position_reconciliations as Json[] | undefined) ?? [];
  const status = String(result?.status ?? analysis.status);
  const completed = status === "COMPLETED";
  const transactionCount = Number(result?.normalized_transaction_count ?? 0) + Number(result?.security_transaction_count ?? 0);
  const accountCount = new Set(sections.map((item) => item.account_id).filter(Boolean)).size || analysis.sections.filter((section) => section.mapped_account_id).length;
  const calculatedReviewCount = relations.filter((item) => item.status === "PROPOSED").length
    + balanceReconciliations.filter((item) => item.status !== "MATCHED").length
    + positionReconciliations.filter((item) => item.status !== "MATCHED").length
    + sections.filter((item) => item.status === "REVIEW_REQUIRED" || item.status === "FAILED").length;
  const reviewCount = Number((result?.review_summary as Json | undefined)?.total_open_count ?? calculatedReviewCount);
  return <section className="panel import-stage">
    <header><span className="eyebrow">IMPORTERGEBNIS</span><h2>{completed ? "Import abgeschlossen" : "Import verarbeitet"}</h2><p>{transactionCount} Buchungen importiert<br />{accountCount} {accountCount === 1 ? "Konto" : "Konten"} verarbeitet</p></header>
    <div className={`result-status ${reviewCount === 0 && completed ? "success" : "review"}`}><span aria-hidden="true">{reviewCount === 0 && completed ? "✓" : "!"}</span><div><strong>{reviewCount === 0 ? "Keine weitere Prüfung erforderlich" : `${reviewCount} ${reviewCount === 1 ? "Punkt benötigt" : "Punkte benötigen"} Prüfung`}</strong><small>Der Import und alle Entscheidungen bleiben vollständig auditierbar.</small></div></div>
    {reviewCount > 0 && onOpenReviews && <div className="result-primary-action"><button className="primary" onClick={onOpenReviews}>Arbeitsbereich öffnen</button></div>}
    <details className="import-technical-details result-details"><summary>Importdetails</summary>
      <div className="result-metrics"><div><span>Kontobuchungen</span><strong>{String(result?.normalized_transaction_count ?? 0)}</strong><small>normalisiert importiert</small></div><div><span>Depotbuchungen</span><strong>{String(result?.security_transaction_count ?? 0)}</strong><small>Wertpapierbewegungen</small></div><div><span>Relationsvorschläge</span><strong>{relations.length}</strong><small>erkannt</small></div><div><span>Kontoabschnitte</span><strong>{analysis.sections.length}</strong><small>getrennt verarbeitet</small></div></div>
      <div className="result-sections">{analysis.sections.map((section) => {
      const row = sections.find((item) => item.section_id === section.section_id);
      const reconciliation = row?.balance_reconciliation as Json | undefined ?? row?.position_reconciliation as Json | undefined;
      const count = String(row?.normalized_transaction_count ?? row?.security_transaction_count ?? row?.record_count ?? 0);
      return <article key={section.section_id}>
        <div className="result-section-title"><div><strong>{section.original_title}</strong><small>{String(row?.local_account_name ?? row?.account_id ?? section.mapped_account_id ?? "übersprungen")}</small></div><span className={`status-badge ${String(row?.status ?? section.import_status).toLowerCase()}`}>{importStatus(String(row?.status ?? section.import_status))}</span></div>
        <div className="result-section-meta"><span><b>{count}</b> verarbeitete Datensätze</span><span>{analysis.period_start} – {analysis.period_end}</span></div>
        {reconciliation ? <div className="reconciliation-summary"><span>Abgleich</span><strong>{importStatus(String(reconciliation.status))}</strong><small>Abweichung {String(reconciliation.balance_difference ?? "positionsbezogen")}</small></div> : section.section_type !== "UNKNOWN" && <button className="secondary small" disabled={busy} onClick={() => onReconcile(section)}>Endsaldo abgleichen</button>}
      </article>;
      })}</div>
      {relations.length > 0 && <><h3 className="subheading">Wertpapier-Finanzierungen</h3><div className="relation-list">{relations.map((relation) => <article key={String(relation.relation_id)}><div><strong>{String(relation.amount)} {String(relation.currency)}</strong><small>{String(relation.booking_date ?? "–")} → {String(relation.security_identifier ?? "Wertpapier")}</small><p>{String(relation.match_reason ?? "Deterministischer Relationsmatch")}</p></div><span className="status-badge">{importStatus(String(relation.status))}</span><div>{relation.status === "PROPOSED" && <><button className="primary small" onClick={() => onRelation(String(relation.relation_id), "Confirm")}>Bestätigen</button><button className="secondary small" onClick={() => onRelation(String(relation.relation_id), "Reject")}>Ablehnen</button></>}{relation.status === "CONFIRMED" && <button className="danger-link" onClick={() => onRelation(String(relation.relation_id), "Break")}>Verknüpfung lösen</button>}</div></article>)}</div></>}
      <p className="technical-note">Technischer Status: {status} · Export {analysis.export_id}</p>
    </details>
  </section>;
}

function History({ result, onOpen, onResume }: { result: QueryState<{ imports: HistoryRow[] }>; onOpen: (row: HistoryRow) => void; onResume: (row: HistoryRow) => void }) {
  const rows = result.data?.imports ?? [];
  return <section className="panel import-history"><header><div><span className="eyebrow">DAUERHAFTE PROJEKTION</span><h2>Importhistorie</h2><p>Jeder Export bleibt mit Zeitraum, Konten, Abschnitten und erzeugten Buchungen nachvollziehbar.</p></div><span>{rows.length} Exporte</span></header>{result.state === "LOADING" ? <div className="page-state"><span className="loader" /></div> : rows.length === 0 ? <div className="page-state"><h3>Noch keine Importe</h3><p>Analysierte Dateien erscheinen hier ohne lokalen Dateipfad.</p></div> : <div className="history-table">{rows.map((row) => <article key={row.export_id}><div><strong>{row.bank_identifier} · {row.report_month}</strong><small>{row.period_start && row.period_end ? `${row.period_start} – ${row.period_end}` : new Date(row.imported_at).toLocaleString("de-DE")}</small><small>{row.account_names?.length ? row.account_names.join(" · ") : row.import_profile}</small></div><span><b>{row.record_count ?? "–"}</b><small>Buchungen in {row.completed_section_count}/{row.section_count} Abschnitten</small></span><span className={`status-badge ${row.status.toLowerCase()}`}>{importStatus(row.status)}</span><span className={row.open_review_count ? "history-review warning" : "history-review"}><b>{row.open_review_count ?? 0}</b><small>offene Prüfungen</small></span><div><button className="secondary small" onClick={() => onOpen(row)}>Details</button>{row.resumable && <button className="primary small" onClick={() => onResume(row)}>Fortsetzen</button>}</div></article>)}</div>}</section>;
}

function actorLabel(entry: Json): string {
  const eventType = String(entry.event_type);
  const payload = (entry.payload as Json | undefined) ?? {};
  if (["ImportFileAnalyzed", "ImportSectionCompleted", "EmptyImportSectionProcessed", "ImportedPeriodBalanceReconciled", "ImportedSecurityPositionsReconciled", "ImportBatchStarted", "ImportBatchCompleted"].includes(eventType)) {
    return "System (automatisch)";
  }
  if (payload.source === "CALCULATED") return "System (automatisch berechnet)";
  return "Sie";
}

function businessImpact(entry: Json): string {
  const eventType = String(entry.event_type);
  const payload = (entry.payload as Json | undefined) ?? {};
  if (eventType === "OpeningBalanceRecorded" || eventType === "ClosingBalanceRecorded") {
    return `${eventType === "OpeningBalanceRecorded" ? "Anfangssaldo" : "Endsaldo"} ${String(payload.booked_balance ?? "")} ${String(payload.currency ?? "")} zum ${String(payload.balance_date ?? "")} gespeichert.`;
  }
  if (eventType === "OpeningBalanceCarryForwardAdjusted") {
    return `Anfangssaldo von ${String(payload.previous_value ?? "")} auf ${String(payload.value ?? "")} geändert. Begründung: „${String(payload.reason ?? "")}“.`;
  }
  if (eventType === "ImportedPeriodBalanceReconciled") {
    return `Abgleichstatus ${String(payload.status ?? "")}${payload.balance_difference && payload.balance_difference !== "0" && payload.balance_difference !== "0.00" ? `, Abweichung ${String(payload.balance_difference)}` : ""}.`;
  }
  return eventLabel(eventType).description;
}

function AuditEntry({ entry }: { entry: Json }) {
  const [infoOpen, setInfoOpen] = useState(false);
  const label = eventLabel(String(entry.event_type));
  const payload = (entry.payload as Json | undefined) ?? {};
  return <div className="audit-entry">
    <div className="audit-entry-head">
      <code>#{String(entry.sequence_number)}</code>
      <strong>{label.title}</strong>
      <small>{new Date(String(entry.occurred_at)).toLocaleString("de-DE")}</small>
      <button type="button" className="info-button" aria-label="Kurzbeschreibung anzeigen" aria-expanded={infoOpen} onClick={() => setInfoOpen((value) => !value)}>i</button>
    </div>
    {infoOpen && <p className="audit-description">{label.description}</p>}
    <details className="audit-detail">
      <summary>Details</summary>
      <dl className="audit-facts">
        <div><dt>Konto</dt><dd>{String(entry.account_id ?? payload.account_id ?? "–")}</dd></div>
        <div><dt>Ausgelöst von</dt><dd>{actorLabel(entry)}</dd></div>
        <div><dt>Fachliche Auswirkung</dt><dd>{businessImpact(entry)}</dd></div>
      </dl>
      <details className="audit-technical">
        <summary>Technische Details</summary>
        <dl className="audit-facts">
          <div><dt>Eventtyp</dt><dd><code>{String(entry.event_type)}</code></dd></div>
          <div><dt>Event-ID</dt><dd><code>{String(entry.event_id ?? "–")}</code></dd></div>
          <div><dt>Sequenz</dt><dd>#{String(entry.sequence_number)}</dd></div>
          <div><dt>Aggregate-ID</dt><dd><code>{String(entry.aggregate_type ?? "–")}/{String(entry.aggregate_id ?? "–")}</code></dd></div>
          <div><dt>Correlation-ID</dt><dd><code>{String(entry.correlation_id ?? "–")}</code></dd></div>
          <div><dt>Contract-Version</dt><dd>{String(entry.schema_version ?? "–")}</dd></div>
          <div><dt>Zeitpunkt</dt><dd>{String(entry.occurred_at ?? "–")}</dd></div>
        </dl>
      </details>
    </details>
  </div>;
}

function HistoryDetail({ detail, onClose }: { detail: Json; onClose: () => void }) {
  const [tab, setTab] = useState<"overview" | "transactions" | "audit">("overview");
  const [sectionFilter, setSectionFilter] = useState("ALL");
  const analysis = detail.analysis as Analysis | undefined;
  const execution = (detail.execution_result as Json | undefined) ?? {};
  const transactions = (detail.transactions as Json[] | undefined) ?? [];
  const securities = (detail.security_transactions as Json[] | undefined) ?? [];
  const review = (detail.review_summary as Json | undefined) ?? {};
  const sectionResults = (execution.section_results as Json[] | undefined) ?? [];
  const visibleTransactions = sectionFilter === "ALL" ? transactions : transactions.filter((item) => item.section_id === sectionFilter);
  const visibleSecurities = sectionFilter === "ALL" ? securities : securities.filter((item) => item.section_id === sectionFilter);
  const audit = (detail.audit_history as Json[] | undefined) ?? [];
  const hasSequenceGaps = audit.some((entry, index) => index > 0 && Number(entry.sequence_number) - Number(audit[index - 1].sequence_number) > 1);
  const grouped = EVENT_CATEGORY_ORDER.map((category) => ({
    category,
    entries: audit.filter((entry) => eventLabel(String(entry.event_type)).category === category),
  })).filter((group) => group.entries.length > 0);
  return <div className="dialog-backdrop"><section className="dialog wide import-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="history-title"><button className="dialog-close" onClick={onClose} aria-label="Dialog schließen">×</button><span className="eyebrow">IMPORTDETAIL</span><div className="import-detail-head"><div><h2 id="history-title">{analysis?.bank_identifier} · {analysis?.report_month}</h2><p>{analysis?.period_start} – {analysis?.period_end} · Hash {shortHash(analysis?.source_file_hash ?? "")}</p></div><span className={`status-badge ${String(detail.status).toLowerCase()}`}>{importStatus(String(detail.status))}</span></div>
    <div className="tabs" role="tablist" aria-label="Importdetails"><button role="tab" aria-selected={tab === "overview"} onClick={() => setTab("overview")}>Übersicht</button><button role="tab" aria-selected={tab === "transactions"} onClick={() => setTab("transactions")}>Importierte Buchungen <span className="tab-count">{transactions.length + securities.length}</span></button><button role="tab" aria-selected={tab === "audit"} onClick={() => setTab("audit")}>Auditverlauf</button></div>
    {tab === "overview" && <><section className="import-detail-metrics"><div><span>Kontobuchungen</span><strong>{transactions.length}</strong></div><div><span>Depotbuchungen</span><strong>{securities.length}</strong></div><div><span>Abschnitte</span><strong>{analysis?.sections.length ?? 0}</strong></div><div className={Number(review.total_open_count ?? 0) ? "warning" : ""}><span>Offene Prüfungen</span><strong>{String(review.total_open_count ?? 0)}</strong></div></section>{Number(review.total_open_count ?? 0) > 0 && <section className="import-review-summary"><strong>Noch zu prüfen</strong><span>{String(review.unclassified_transaction_count ?? 0)} nicht kategorisiert</span><span>{String(review.open_relation_count ?? 0)} Relationsvorschläge</span><span>{String(review.balance_review_count ?? 0)} Saldenabweichungen</span><span>{String(review.position_review_count ?? 0)} Bestandsabweichungen</span></section>}<section className="import-detail-sections">{(analysis?.sections ?? []).map((section) => { const result = sectionResults.find((item) => item.section_id === section.section_id); const reconciliation = (result?.balance_reconciliation ?? result?.position_reconciliation) as Json | undefined; return <article key={section.section_id}><div><strong>{section.original_title}</strong><small>{String(result?.local_account_name ?? result?.account_id ?? section.mapped_account_id ?? "Kein Konto")}</small></div><dl><div><dt>Typ</dt><dd>{section.section_type}</dd></div><div><dt>Datensätze</dt><dd>{String(result?.normalized_transaction_count ?? result?.security_transaction_count ?? section.record_count)}</dd></div><div><dt>Status</dt><dd>{importStatus(String(result?.status ?? section.import_status))}</dd></div><div><dt>Abgleich</dt><dd>{reconciliation ? importStatus(String(reconciliation.status)) : "Noch nicht durchgeführt"}</dd></div></dl><button className="secondary small" onClick={() => { setSectionFilter(section.section_id); setTab("transactions"); }}>Buchungen anzeigen</button></article>; })}</section></>}
    {tab === "transactions" && <><div className="import-transaction-toolbar"><label>Abschnitt<select aria-label="Abschnitt filtern" value={sectionFilter} onChange={(event) => setSectionFilter(event.target.value)}><option value="ALL">Alle Abschnitte</option>{(analysis?.sections ?? []).map((section) => <option key={section.section_id} value={section.section_id}>{section.original_title}</option>)}</select></label><span>{visibleTransactions.length + visibleSecurities.length} Einträge</span></div>{visibleTransactions.length > 0 && <section className="panel table-panel import-transactions"><table><thead><tr><th>Datum</th><th>Konto</th><th>Anbieter / Beschreibung</th><th>Kategorie</th><th className="numeric">Betrag</th></tr></thead><tbody>{visibleTransactions.map((item) => <tr key={String(item.transaction_id)}><td>{String(item.booking_date)}</td><td>{String(item.account_name ?? item.account_id)}</td><td><strong>{String(item.counterparty || "Unbekannter Anbieter")}</strong><small>{String(item.description || "Keine Beschreibung")}</small></td><td><span className={item.category_code === "UNCLASSIFIED" ? "category-tag warn" : "category-tag"}>{categoryName(String(item.category_code))}</span></td><td className="numeric amount">{String(item.amount)} {String(item.currency)}</td></tr>)}</tbody></table></section>}{visibleSecurities.length > 0 && <section className="panel table-panel import-transactions"><h3>Wertpapierbewegungen</h3><table><thead><tr><th>Datum</th><th>Depot</th><th>Wertpapier</th><th>Vorgang</th><th className="numeric">Menge</th></tr></thead><tbody>{visibleSecurities.map((item) => <tr key={String(item.transaction_id)}><td>{String(item.trade_date ?? item.booking_date ?? "–")}</td><td>{String(item.account_name ?? item.account_id)}</td><td><strong>{String(item.security_name ?? item.security_identifier ?? "–")}</strong><small>{String(item.security_identifier ?? "")}</small></td><td>{importStatus(String(item.transaction_type ?? item.security_transaction_type ?? "–"))}</td><td className="numeric">{String(item.quantity ?? "–")}</td></tr>)}</tbody></table></section>}{visibleTransactions.length === 0 && visibleSecurities.length === 0 && <p className="page-state">Für diesen Abschnitt wurden keine Buchungen übernommen.</p>}</>}
    {tab === "audit" && <><h3>Auditverlauf</h3>
      {audit.length > 0 && <p className="audit-sequence-note">Die Nummer neben jedem Ereignis (z. B. „#{String(audit[audit.length - 1].sequence_number)}“) ist die globale Event-Sequenz des gesamten Datenbestands.{hasSequenceGaps && " Lücken zwischen den Nummern sind normal: Dazwischenliegende Events gehören zu anderen Konten oder Verarbeitungsschritten und werden hier nicht angezeigt."}</p>}
      {grouped.map((group) => <div className="audit-group" key={group.category}><h4>{EVENT_CATEGORY_TITLES[group.category as EventCategory]}</h4><div className="audit-list">{group.entries.map((entry) => <AuditEntry entry={entry} key={String(entry.event_id ?? entry.sequence_number)} />)}</div></div>)}
      {audit.length === 0 && <p className="page-state">Für diesen Import liegen noch keine Auditereignisse vor.</p>}</>}
  </section></div>;
}
