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
  invoke(operation: "query" | "command", payload: Record<string, unknown>): Promise<DesktopResponse>;
  getRuntimeStatus(): Promise<DesktopResponse>;
  /** Present only on the synthetic Vite development bridge. */
  readonly preview?: boolean;
}

declare global {
  interface Window {
    __FINANCE_IPC__?: DesktopFinanceIPC;
  }
}

const supportedCommands = new Set([
  "ImportTransactions", "AnalyzeImportFile", "MapImportSections",
  "ImportMappedSections", "RecordOpeningBalance", "RecordClosingBalance",
  "RecordOpeningSecurityPosition", "ConfirmEmptyOpeningSecurityPositions",
  "RecordClosingSecurityPosition", "ReconcileImportedPeriodBalance",
  "ReconcileImportedSecurityPositions", "ReconcileImportedPeriodPositions",
  "CorrectSecurityPositionSnapshot", "DocumentBalanceDifference",
  "DetectInvestmentFundingRelations", "ConfirmInvestmentFundingRelation",
  "RejectInvestmentFundingRelation", "BreakInvestmentFundingRelation",
  "ClassifyTransactions", "ConfirmClassification",
  "RejectClassification", "CreateClassificationRule", "DetectDuplicates",
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
]);

let previewRequest = 0;

function previewResponse(operation: DesktopOperation, result: unknown): DesktopResponse {
  previewRequest += 1;
  return {
    request_id: `preview_${previewRequest}`,
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
    async invoke(
      operation: "query" | "command",
      payload: Record<string, unknown>,
    ) {
      if (operation === "query") {
        return previewResponse("query", mockQuery(String(payload.name), asPayload(payload.payload)));
      }
      await new Promise((resolve) => window.setTimeout(resolve, 20));
      return previewResponse("command", {
        schema_version: "1.0.0",
        status: "COMPLETED",
        result: 1,
      });
    },
    async getRuntimeStatus() {
      return previewResponse("runtime_status", {
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
): T {
  if (!response || typeof response !== "object") throw new DesktopResponseError();
  if (response.contract_version !== DESKTOP_CONTRACT_VERSION) {
    throw new DesktopContractCompatibilityError(response.contract_version);
  }
  if (response.operation !== expectedOperation || !response.request_id) {
    throw new DesktopResponseError();
  }
  if (response.status === "ERROR") {
    throw new DesktopOperationError(
      response.error?.code ?? "DESKTOP_OPERATION_FAILED",
      response.error?.message ?? "Der lokale Desktop-Vorgang ist fehlgeschlagen.",
    );
  }
  if (response.status !== "OK" || !Object.hasOwn(response, "result")) {
    throw new DesktopResponseError();
  }
  return response.result as T;
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
    const response = await this.ipc().invoke("query", { name, payload });
    const envelope = unwrapDesktopResponse<Envelope<T>>(response, "query");
    if (!envelope || typeof envelope !== "object") throw new DesktopResponseError();
    if (envelope.schema_version !== CONTRACT_SCHEMA_VERSION) {
      throw new SchemaCompatibilityError(envelope.schema_version);
    }
    return envelope;
  }

  async command(name: string, payload: Record<string, unknown> = {}): Promise<unknown> {
    if (!supportedCommands.has(name)) throw new Error(`Nicht unterstützter Command: ${name}`);
    const response = await this.ipc().invoke("command", { name, payload });
    return unwrapDesktopResponse(response, "command");
  }

  async selectImportFile(): Promise<SelectedImportFile | null> {
    return this.ipc().selectImportFile();
  }

  async getRuntimeStatus<T = unknown>(): Promise<T> {
    return unwrapDesktopResponse<T>(await this.ipc().getRuntimeStatus(), "runtime_status");
  }
}

export class DesktopBridgeUnavailableError extends Error {
  readonly code = "DESKTOP_BRIDGE_UNAVAILABLE";
  constructor() {
    super("DESKTOP_BRIDGE_UNAVAILABLE");
  }
}

export class DesktopResponseError extends Error {
  readonly code = "DESKTOP_RESPONSE_INVALID";
  constructor() {
    super("DESKTOP_RESPONSE_INVALID");
  }
}

export class DesktopContractCompatibilityError extends Error {
  readonly code = "DESKTOP_CONTRACT_INCOMPATIBLE";
  constructor(readonly receivedVersion: string) {
    super(`Desktop-Vertrag ${receivedVersion || "unbekannt"} ist nicht kompatibel.`);
  }
}

export class DesktopOperationError extends Error {
  constructor(readonly code: string, message: string) {
    super(`${code}: ${message}`);
  }
}

export class SchemaCompatibilityError extends Error {
  constructor(readonly receivedVersion: string) {
    super(`Schema ${receivedVersion} ist nicht kompatibel mit ${CONTRACT_SCHEMA_VERSION}.`);
  }
}

export const financeBridge = new FinanceBridge();
