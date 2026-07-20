import { CONTRACT_SCHEMA_VERSION, type Envelope } from "./contracts/generated";
import { mockQuery } from "./mockData";

export interface DesktopFinanceIPC {
  query(name: string, payload: Record<string, unknown>): Promise<unknown>;
  command(name: string, payload: Record<string, unknown>): Promise<unknown>;
  selectImportFile?(): Promise<string | null>;
}

declare global {
  interface Window {
    __FINANCE_IPC__?: DesktopFinanceIPC;
  }
}

const supportedCommands = new Set([
  "ImportTransactions", "ClassifyTransactions", "ConfirmClassification",
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
  "RepairLocalStore", "ValidateStoreIntegrity",
  "ExportDiagnostics",
]);

export class FinanceBridge {
  readonly isPreview = !window.__FINANCE_IPC__;

  async query<T>(name: string, payload: Record<string, unknown> = {}): Promise<Envelope<T>> {
    const response = window.__FINANCE_IPC__
      ? await window.__FINANCE_IPC__.query(name, payload)
      : mockQuery(name, payload);
    if (!response || typeof response !== "object") throw new Error("Ungültige IPC-Antwort");
    const envelope = response as Envelope<T>;
    if (envelope.schema_version !== CONTRACT_SCHEMA_VERSION) {
      throw new SchemaCompatibilityError(envelope.schema_version);
    }
    return envelope;
  }

  async command(name: string, payload: Record<string, unknown> = {}): Promise<unknown> {
    if (!supportedCommands.has(name)) throw new Error(`Nicht unterstützter Command: ${name}`);
    if (window.__FINANCE_IPC__) return window.__FINANCE_IPC__.command(name, payload);
    await new Promise((resolve) => window.setTimeout(resolve, 180));
    return { schema_version: "1.0.0", status: "COMPLETED", result: 1 };
  }

  async selectImportFile(): Promise<string | null> {
    return window.__FINANCE_IPC__?.selectImportFile?.() ?? null;
  }
}

export class SchemaCompatibilityError extends Error {
  constructor(readonly receivedVersion: string) {
    super(`Schema ${receivedVersion} ist nicht kompatibel mit ${CONTRACT_SCHEMA_VERSION}.`);
  }
}

export const financeBridge = new FinanceBridge();
