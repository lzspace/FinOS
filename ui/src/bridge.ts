import { CONTRACT_SCHEMA_VERSION, type Envelope } from "./contracts/generated";
import { mockQuery } from "./mockData";

export const DESKTOP_CONTRACT_VERSION = "1.3.0";

export type DesktopOperation = "query" | "command" | "runtime_status";
export type DesktopResponse<T = unknown> = {
  request_id: string;
  operation: DesktopOperation;
  contract_version: string;
  status: "OK" | "ERROR";
  result?: T;
  error?: { code: string; message: string };
};

export interface SelectedImportFile {
  file_ref: string;
  display_name: string;
  size_bytes: number;
}

export interface DesktopFinanceIPC {
  selectImportFile(): Promise<SelectedImportFile | null>;
  selectLocalModelFile(): Promise<SelectedImportFile | null>;
  selectLocalModelManifest(): Promise<SelectedImportFile | null>;
  selectLocalAIRuntime(): Promise<SelectedImportFile | null>;
  invoke(requestId: string, operation: "query" | "command", payload: Record<string, unknown>): Promise<DesktopResponse>;
  getRuntimeStatus(requestId: string): Promise<DesktopResponse>;
  /** Present only on the synthetic Vite development bridge. */
  readonly preview?: boolean;
}

declare global {
  interface Window {
    __FINANCE_IPC__?: DesktopFinanceIPC;
  }
}

const supportedCommands = new Set([
  "ImportTransactions", "AnalyzeImportFile", "AnalyzeUnknownImportFormat",
  "ValidateImportMapping", "ConfirmImportMapping", "RejectImportMapping", "MapImportSections",
  "ImportMappedSections", "RecordOpeningBalance", "RecordClosingBalance",
  "RecordOpeningSecurityPosition", "ConfirmEmptyOpeningSecurityPositions",
  "RecordClosingSecurityPosition", "ReconcileImportedPeriodBalance",
  "ReconcileImportedSecurityPositions", "ReconcileImportedPeriodPositions",
  "CorrectSecurityPositionSnapshot", "DocumentBalanceDifference",
  "DetectInvestmentFundingRelations", "ConfirmInvestmentFundingRelation",
  "RejectInvestmentFundingRelation", "BreakInvestmentFundingRelation",
  "ClassifyTransactions", "ConfirmClassification",
  "RejectClassification", "CreateClassificationRule", "CreateCategory",
  "RenameCategory", "ArchiveCategory", "RestoreCategory", "DetectDuplicates",
  "ConfirmDuplicate", "RejectDuplicate", "DetectTransfers", "ConfirmTransfer",
  "RejectTransfer", "BreakTransferMatch", "DetectRefunds", "ConfirmRefund",
  "RejectRefund", "DetectRecurringPatterns", "ConfirmRecurringPattern",
  "RejectRecurringPattern", "UpdateRecurringPattern", "PauseRecurringPattern",
  "EndRecurringPattern", "CreateForecast", "EvaluateForecast",
  "CreateAccount", "UpdateAccount", "CloseAccount", "RecordBalanceSnapshot",
  "CorrectBalanceSnapshot", "ReconcileAccountBalance", "CreateAssetSnapshot",
  "CorrectAssetSnapshot", "CreateLiabilitySnapshot", "CorrectLiabilitySnapshot",
  "CreateBackup", "VerifyBackup", "RestoreBackup", "DeleteBackup",
  "ExportFinanceData", "ImportFinanceArchive", "RotateEncryptionKey",
  "RepairLocalStore", "ValidateStoreIntegrity", "ExportDiagnostics",
  "RegisterLocalAIRuntime", "RegisterLocalModel", "EnableLocalModel", "DisableLocalModel",
  "EnableCategoryShadowMode", "DisableCategoryShadowMode",
  "StartCategoryEvaluationRun", "PauseCategoryEvaluationRun",
  "ResumeCategoryEvaluationRun", "CancelCategoryEvaluationRun",
  "ConfirmCategorySuggestion", "CorrectCategorySuggestion",
  "RejectCategorySuggestion", "CreateClassificationRuleFromSuggestion",
]);

let previewRequest = 0;
const QUERY_CACHE_TTL_MS = 30_000;
type QueryCacheEntry = { expiresAt: number; value: Envelope<unknown> };
const queryCache = new Map<string, QueryCacheEntry>();
const pendingQueries = new Map<string, Promise<Envelope<unknown>>>();
let cacheOwner: DesktopFinanceIPC | null = null;

function bindCacheTo(ipc: DesktopFinanceIPC): void {
  if (cacheOwner === ipc) return;
  cacheOwner = ipc;
  queryCache.clear();
  pendingQueries.clear();
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonicalize(item)]),
  );
}

function queryKey(name: string, payload: Record<string, unknown>): string {
  return `${name}:${JSON.stringify(canonicalize(payload))}`;
}

function queryGroup(name: string): string {
  if (/Category|Classification/.test(name)) return "CATEGORY";
  if (/Transaction/.test(name)) return "TRANSACTIONS";
  if (/Account|Balance|Position|Liquidity|NetWorth|Asset|Liabilit/.test(name)) return "ACCOUNT";
  if (/Import|Format|Funding/.test(name)) return "IMPORT";
  if (/Review/.test(name)) return "REVIEW";
  if (/Dashboard|FinancialTimeSeries|Forecast|Recurring|Expected/.test(name)) return "DASHBOARD";
  if (/Backup|StoreIntegrity|KeyStatus|Migration|RuntimeSecurity/.test(name)) return "SECURITY";
  if (/LocalModel|LocalAI|Shadow|Evaluation/.test(name)) return "LOCAL_AI";
  return "GENERAL";
}

function invalidationGroups(command: string): Set<string> {
  if (/Category|Classification/.test(command)) return new Set(["CATEGORY", "TRANSACTIONS", "REVIEW", "DASHBOARD"]);
  if (/Import|Opening|Closing|Reconcile|Funding|Duplicate|Transfer|Refund/.test(command)) return new Set(["IMPORT", "TRANSACTIONS", "ACCOUNT", "REVIEW", "DASHBOARD"]);
  if (/Account|Balance|Position|Asset|Liability/.test(command)) return new Set(["ACCOUNT", "REVIEW", "DASHBOARD"]);
  if (/Backup|Store|Key|Migration|Repair|Restore/.test(command)) return new Set(["SECURITY"]);
  if (/LocalModel|LocalAI|Shadow|Evaluation/.test(command)) return new Set(["LOCAL_AI", "CATEGORY", "REVIEW"]);
  if (/Forecast|Recurring/.test(command)) return new Set(["DASHBOARD"]);
  return new Set(["GENERAL"]);
}

function invalidateQueryGroups(groups: Set<string>): void {
  for (const key of queryCache.keys()) {
    const name = key.slice(0, key.indexOf(":"));
    if (groups.has(queryGroup(name))) queryCache.delete(key);
  }
}

function previewResponse(requestId: string, operation: DesktopOperation, result: unknown): DesktopResponse {
  return {
    request_id: requestId,
    operation,
    contract_version: DESKTOP_CONTRACT_VERSION,
    status: "OK",
    result,
  };
}

/**
 * Vite's browser server is a visibly marked, synthetic-only UI preview. This
 * branch is removed by the production build.
 */
export function installDevelopmentPreviewBridge(): void {
  if (!import.meta.env.DEV || window.__FINANCE_IPC__) return;
  const bridge: DesktopFinanceIPC = {
    preview: true,
    async selectImportFile() {
      return null;
    },
    async selectLocalModelFile() {
      return null;
    },
    async selectLocalModelManifest() {
      return null;
    },
    async selectLocalAIRuntime() {
      return null;
    },
    async invoke(
      requestId: string,
      operation: "query" | "command",
      payload: Record<string, unknown>,
    ) {
      if (operation === "query") {
        return previewResponse(requestId, "query", mockQuery(String(payload.name), asPayload(payload.payload)));
      }
      await new Promise((resolve) => window.setTimeout(resolve, 20));
      if (payload.name === "CreateCategory") {
        const commandPayload = asPayload(payload.payload);
        const displayName = String(commandPayload.display_name ?? "Neue Kategorie");
        const slug = displayName.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48);
        return previewResponse(requestId, "command", {
          schema_version: "1.0.0", status: "COMPLETED",
          result: { category_code: `CUSTOM_${slug}`, display_name: displayName, category_type: commandPayload.category_type ?? "EXPENSE", status: "ACTIVE", system_category: false, parent_category_code: null, color: null, icon: null, legacy: false },
        });
      }
      return previewResponse(requestId, "command", {
        schema_version: "1.0.0",
        status: "COMPLETED",
        result: 1,
      });
    },
    async getRuntimeStatus(requestId: string) {
      return previewResponse(requestId, "runtime_status", {
        preview: true,
        synthetic_fixtures_only: true,
        network_egress_disabled: true,
      });
    },
  };
  window.__FINANCE_IPC__ = Object.freeze(bridge);
}

function asPayload(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function unwrapDesktopResponse<T>(
  response: DesktopResponse,
  expectedOperation: DesktopOperation,
  expectedRequestId: string,
): T {
  if (!response || typeof response !== "object") throw new DesktopResponseError();
  if (response.contract_version !== DESKTOP_CONTRACT_VERSION) {
    throw new DesktopContractCompatibilityError(response.contract_version);
  }
  if (response.operation !== expectedOperation || response.request_id !== expectedRequestId) {
    throw new DesktopResponseError();
  }
  if (response.status === "ERROR") {
    throw new DesktopOperationError(
      response.error?.code ?? "DESKTOP_OPERATION_FAILED",
      userMessage(response.error?.code ?? "DESKTOP_OPERATION_FAILED"),
      response.request_id,
    );
  }
  if (response.status !== "OK" || !Object.hasOwn(response, "result")) {
    throw new DesktopResponseError();
  }
  return response.result as T;
}

function requestId(): string {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "");
  previewRequest += 1;
  return `req_${random ?? previewRequest.toString(16).padStart(32, "0")}`;
}

function userMessage(code: string): string {
  const messages: Record<string, string> = {
    LOCAL_MODEL_REFERENCE_EXPIRED: "Die Dateiauswahl ist abgelaufen. Bitte wähle Modell und Manifest erneut aus.",
    LOCAL_MODEL_FILE_NOT_FOUND: "Eine ausgewählte Modelldatei ist nicht mehr vorhanden.",
    LOCAL_MODEL_MANIFEST_INVALID: "Das Modellmanifest ist ungültig oder beschädigt.",
    LOCAL_MODEL_MANIFEST_INCOMPATIBLE: "Das Modellmanifest ist mit dieser Anwendung nicht kompatibel.",
    LOCAL_MODEL_HASH_MISMATCH: "Die GGUF-Datei stimmt nicht mit dem Hash im Manifest überein.",
    LOCAL_MODEL_FORMAT_UNSUPPORTED: "Das gewählte Modellformat wird nicht unterstützt.",
    LOCAL_MODEL_PURPOSE_UNSUPPORTED: "Das Modell ist nicht für die unterstützten lokalen Aufgaben freigegeben.",
    LOCAL_MODEL_RUNTIME_NOT_FOUND: "Die lokale AI-Laufzeit wurde in der Anwendung nicht gefunden.",
    LOCAL_MODEL_RUNTIME_NOT_EXECUTABLE: "Die lokale AI-Laufzeit kann nicht ausgeführt werden.",
    LOCAL_MODEL_RUNTIME_INCOMPATIBLE: "Die lokale AI-Laufzeit ist nicht kompatibel.",
    LOCAL_AI_RUNTIME_NOT_FOUND: "Die llama.cpp-Laufzeit wurde nicht gefunden. Bitte wähle sie erneut aus.",
    LOCAL_AI_RUNTIME_NOT_EXECUTABLE: "Die gewählte llama.cpp-Datei ist nicht ausführbar.",
    LOCAL_AI_RUNTIME_INCOMPATIBLE: "Die gewählte llama.cpp-Version ist nicht kompatibel.",
    LOCAL_AI_RUNTIME_HASH_MISMATCH: "Die llama.cpp-Laufzeit wurde seit der Registrierung verändert.",
    LOCAL_AI_RUNTIME_START_FAILED: "Die llama.cpp-Laufzeit konnte nicht sicher gestartet werden.",
    LOCAL_MODEL_REGISTRATION_FAILED: "Das lokale Modell konnte nicht dauerhaft registriert werden.",
    LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED: "Die kontrollierte JSON-Ausgabe konnte nicht initialisiert werden.",
    LOCAL_AI_INVALID_OUTPUT: "Die lokale AI hat eine ungültige Antwort erzeugt.",
    LOCAL_AI_JSON_PARSE_FAILED: "Die lokale AI hat kein gültiges JSON erzeugt.",
    LOCAL_AI_SCHEMA_VALIDATION_FAILED: "Die lokale AI-Antwort entspricht nicht dem erwarteten Schema.",
    LOCAL_AI_SEMANTIC_VALIDATION_FAILED: "Die lokale AI-Antwort ist fachlich nicht gültig.",
    LOCAL_AI_TIMEOUT: "Die lokale AI hat das Zeitlimit überschritten.",
    CATEGORY_SHADOW_DISABLED: "Der Kategorie-Shadow-Mode ist deaktiviert.",
    CATEGORY_TRANSACTION_NOT_ELIGIBLE: "Diese Transaktion ist für die lokale Kategorie-Evaluation nicht geeignet.",
    CATEGORY_TAXONOMY_UNAVAILABLE: "Die aktive Kategorie-Taxonomie ist nicht verfügbar.",
    CATEGORY_EVALUATION_ALREADY_RUNNING: "Es läuft bereits eine historische Kategorie-Evaluation.",
    CATEGORY_EVALUATION_NOT_FOUND: "Der Evaluationslauf wurde nicht gefunden.",
    CATEGORY_EVALUATION_INCOMPATIBLE_MODEL: "Der Evaluationslauf gehört zu einer anderen Modell- oder Policy-Version.",
    CATEGORY_SUGGESTION_NOT_FOUND: "Der Kategorie-Vorschlag wurde nicht gefunden.",
    CATEGORY_SUGGESTION_NOT_PENDING: "Der Kategorie-Vorschlag wurde bereits geprüft.",
    CATEGORY_SUGGESTION_STALE: "Der Kategorie-Vorschlag ist nicht mehr aktuell und kann nicht bestätigt werden.",
    CATEGORY_SUGGESTION_CATEGORY_INVALID: "Die gewählte Kategorie ist für diese Transaktion nicht zulässig.",
    CATEGORY_SUGGESTION_DECISION_REQUIRED: "Erst nach einer Bestätigung oder Korrektur kann eine Regel erstellt werden.",
    CATEGORY_SUGGESTION_RULE_VALUE_MISSING: "Für diese Transaktion kann keine passende Regel erstellt werden.",
    CATEGORY_TRANSACTION_ALREADY_CLASSIFIED: "Die Transaktion ist bereits durch eine vorrangige Entscheidung kategorisiert.",
    QUERY_CURSOR_INVALID: "Diese Listenseite kann nicht fortgesetzt werden. Bitte lade die Liste neu.",
    QUERY_CURSOR_STALE: "Die Daten haben sich geändert. Die Liste wird ab der ersten Seite neu geladen.",
    QUERY_LIMIT_INVALID: "Die angeforderte Seitengröße ist ungültig.",
    DESKTOP_WORKER_UNAVAILABLE: "Der lokale Hintergrunddienst ist nicht verfügbar.",
    DESKTOP_WORKER_INVALID_RESPONSE: "Der lokale Hintergrunddienst hat ungültig geantwortet.",
  };
  return messages[code] ?? "Der lokale Vorgang konnte nicht ausgeführt werden.";
}

installDevelopmentPreviewBridge();

export class FinanceBridge {
  get isPreview(): boolean {
    return window.__FINANCE_IPC__?.preview === true;
  }

  get isAvailable(): boolean {
    return Boolean(window.__FINANCE_IPC__);
  }

  private ipc(): DesktopFinanceIPC {
    if (!window.__FINANCE_IPC__) throw new DesktopBridgeUnavailableError();
    return window.__FINANCE_IPC__;
  }

  async query<T>(name: string, payload: Record<string, unknown> = {}): Promise<Envelope<T>> {
    const ipc = this.ipc();
    bindCacheTo(ipc);
    const requestPayload = { ...payload };
    const force = Object.hasOwn(requestPayload, "refresh");
    delete requestPayload.refresh;
    const key = queryKey(name, requestPayload);
    const cached = queryCache.get(key);
    if (!force && cached && cached.expiresAt > Date.now()) return cached.value as Envelope<T>;
    const pending = pendingQueries.get(key);
    if (pending) return pending as Promise<Envelope<T>>;
    const operation = (async () => {
      const id = requestId();
      const response = await ipc.invoke(id, "query", { name, payload: requestPayload });
      const envelope = unwrapDesktopResponse<Envelope<T>>(response, "query", id);
      if (!envelope || typeof envelope !== "object") throw new DesktopResponseError();
      if (envelope.schema_version !== CONTRACT_SCHEMA_VERSION) {
        throw new SchemaCompatibilityError(envelope.schema_version);
      }
      queryCache.set(key, { expiresAt: Date.now() + QUERY_CACHE_TTL_MS, value: envelope });
      return envelope;
    })();
    pendingQueries.set(key, operation as Promise<Envelope<unknown>>);
    try {
      return await operation;
    } finally {
      pendingQueries.delete(key);
    }
  }

  async command(name: string, payload: Record<string, unknown> = {}): Promise<unknown> {
    if (!supportedCommands.has(name)) throw new Error(`Nicht unterstützter Command: ${name}`);
    const id = requestId();
    const ipc = this.ipc();
    bindCacheTo(ipc);
    const response = await ipc.invoke(id, "command", { name, payload });
    const result = unwrapDesktopResponse(response, "command", id);
    invalidateQueryGroups(invalidationGroups(name));
    return result;
  }

  async selectImportFile(): Promise<SelectedImportFile | null> {
    return this.ipc().selectImportFile();
  }

  async selectLocalModelFile(): Promise<SelectedImportFile | null> {
    return this.ipc().selectLocalModelFile();
  }

  async selectLocalModelManifest(): Promise<SelectedImportFile | null> {
    return this.ipc().selectLocalModelManifest();
  }

  async selectLocalAIRuntime(): Promise<SelectedImportFile | null> {
    const id = requestId();
    try {
      return await this.ipc().selectLocalAIRuntime();
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      const code = /^LOCAL_AI_RUNTIME_[A-Z0-9_]+$/.test(raw)
        ? raw
        : "DESKTOP_OPERATION_FAILED";
      throw new DesktopOperationError(code, userMessage(code), id);
    }
  }

  async getRuntimeStatus<T = unknown>(): Promise<T> {
    const id = requestId();
    return unwrapDesktopResponse<T>(await this.ipc().getRuntimeStatus(id), "runtime_status", id);
  }
}

export class DesktopBridgeUnavailableError extends Error {
  readonly code = "DESKTOP_BRIDGE_UNAVAILABLE";
  constructor() {
    super("DESKTOP_BRIDGE_UNAVAILABLE");
  }
}

export class DesktopResponseError extends Error {
  readonly code = "DESKTOP_WORKER_INVALID_RESPONSE";
  constructor() {
    super("DESKTOP_WORKER_INVALID_RESPONSE");
  }
}

export class DesktopContractCompatibilityError extends Error {
  readonly code = "DESKTOP_CONTRACT_INCOMPATIBLE";
  constructor(readonly receivedVersion: string) {
    super(`Desktop-Vertrag ${receivedVersion || "unbekannt"} ist nicht kompatibel.`);
  }
}

export class DesktopOperationError extends Error {
  constructor(readonly code: string, message: string, readonly requestId: string) {
    super(message);
  }
}

export class SchemaCompatibilityError extends Error {
  constructor(readonly receivedVersion: string) {
    super(`Schema ${receivedVersion} ist nicht kompatibel mit ${CONTRACT_SCHEMA_VERSION}.`);
  }
}

export const financeBridge = new FinanceBridge();
