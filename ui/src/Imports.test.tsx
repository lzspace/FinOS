import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Imports } from "./Imports";
import type { DesktopFinanceIPC } from "./bridge";
import type { CapabilityManifest, Envelope } from "./contracts/generated";

const section = {
  section_id: "sec_giro",
  section_type: "CHECKING" as const,
  original_title: "Umsätze Girokonto",
  account_reference: "•••• 4821",
  record_count: 2,
  empty: false,
  import_supported: true,
  warnings: [],
  mapped_account_id: "acc_main",
  import_status: "ANALYZED",
};
const analysis = {
  export_id: "export_july",
  analysis_id: "export_july",
  bank_identifier: "BANK_A",
  report_month: "2026-07",
  period_start: "2026-07-01",
  period_end: "2026-07-31",
  import_profile: "GermanMultiAccountCsvV1",
  profile_version: "1.0.0",
  encoding: "cp1252",
  delimiter: ";",
  source_file_hash: "a".repeat(64),
  file_size: 2048,
  warnings: [],
  sections: [section],
  status: "ANALYZED",
};
const manifest: CapabilityManifest = {
  extension_version: "1.2.0",
  contract_version: "1.3.0",
  store_schema_version: 3,
  schema_version: "1.0.0",
  capabilities: {
    import_capability: true,
    multi_account_import_capability: true,
    balance_reconciliation_capability: true,
    position_reconciliation_capability: true,
    investment_funding_capability: true,
  },
};
const envelope = <T,>(data: T, state: Envelope<T>["state"] = "READY"): Envelope<T> => ({
  schema_version: "1.0.0",
  state,
  projection_sequence: 42,
  event_store_sequence: 42,
  data,
});

function ipcFor(step: number | null, overrides: Partial<DesktopFinanceIPC> = {}) {
  let activeStep = step;
  const commands: Array<[string, Record<string, unknown>]> = [];
  const ipc: DesktopFinanceIPC = {
    selectImportFile: vi.fn(async () => ({ file_reference: "file_token_01", display_name: "Juli.csv" })),
    query: vi.fn(async (name: string) => {
      if (name === "GetImportWizardState") {
        if (activeStep === null) return envelope(null, "EMPTY");
        return envelope({
          export_id: analysis.export_id,
          current_step: activeStep,
          completed_steps: Array.from({ length: activeStep - 1 }, (_, index) => index + 1),
          status: activeStep === 5 ? "COMPLETED" : "ANALYZED",
          can_resume: activeStep < 5,
          analysis,
          requirements: [],
          execution_result: activeStep === 5 ? {
            status: "COMPLETED",
            normalized_transaction_count: 2,
            security_transaction_count: 0,
            section_results: [{ ...section, status: "IMPORTED", local_account_name: "Girokonto", normalized_transaction_count: 2 }],
            relations: [],
          } : null,
        });
      }
      if (name === "GetImportHistory") return envelope({ imports: activeStep === null ? [] : [{
        export_id: analysis.export_id, bank_identifier: "BANK_A", report_month: "2026-07",
        imported_at: "2026-07-24T10:00:00Z", section_count: 1,
        completed_section_count: activeStep === 5 ? 1 : 0, status: activeStep === 5 ? "COMPLETED" : "ANALYZED",
        import_profile: analysis.import_profile, parser_version: "GermanMultiAccountCsvV1@1.0.0",
        source_file_hash: analysis.source_file_hash, resumable: activeStep !== 5,
      }] });
      if (name === "ListAccounts") return envelope({ accounts: [{
        account_id: "acc_main", display_name: "Girokonto", account_type: "CHECKING",
        institution: "BANK_A", currency: "EUR", status: "ACTIVE", include_in_cashflow: true,
        include_in_liquidity: true, include_in_net_worth: true, opened_at: "2025-01-01",
        closed_at: null, masked_reference: "•••• 4821", latest_balance: null,
        available_balance: null, balance_date: null, balance_source: null,
        reconciliation_status: "NOT_RECONCILED", freshness: "CURRENT",
      }] });
      if (name === "GetImportSectionPreview") return envelope({
        ...section, first_booking_date: "2026-07-01", last_booking_date: "2026-07-20",
        amount_sum: "-42.00", duplicate_candidate_count: 0,
        preview: [{ booking_date: "2026-07-01", description: "Miete", amount: "-40.00" }],
      });
      if (name === "GetImportExecutionResult") return envelope({
        status: "COMPLETED", normalized_transaction_count: 2, security_transaction_count: 0,
        section_results: [{ ...section, status: "IMPORTED", local_account_name: "Girokonto", normalized_transaction_count: 2 }],
        relations: [],
      });
      if (name === "GetImportHistoryDetail") return envelope({
        status: "COMPLETED", analysis,
        audit_history: [{ sequence_number: 42, event_type: "ImportSectionCompleted", occurred_at: "2026-07-24T10:00:00Z" }],
      });
      return envelope(null, "EMPTY");
    }),
    command: vi.fn(async (name: string, payload: Record<string, unknown>) => {
      commands.push([name, payload]);
      if (name === "AnalyzeImportFile") {
        activeStep = 1;
        return { schema_version: "1.0.0", status: "COMPLETED", result: analysis };
      }
      return { schema_version: "1.0.0", status: "COMPLETED", result: 1 };
    }),
    ...overrides,
  };
  return { ipc, commands };
}

afterEach(() => {
  cleanup();
  delete window.__FINANCE_IPC__;
});

describe("Importassistent 1.2.0", () => {
  it("zeigt ohne Projektion einen lokalen, dateireferenzbasierten Einstieg", async () => {
    const { ipc } = ipcFor(null);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Neuen Import beginnen" })).toBeInTheDocument();
    expect(screen.getByText(/kurzlebige Dateireferenz/)).toBeInTheDocument();
    expect(screen.queryByText(/Users\//)).not.toBeInTheDocument();
  });

  it("analysiert eine Host-Dateireferenz und zeigt Bank, Monat und Abschnitte", async () => {
    const { ipc, commands } = ipcFor(null);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    fireEvent.click(await screen.findByRole("button", { name: "Lokale CSV auswählen" }));
    expect(await screen.findByText("Umsätze Girokonto")).toBeInTheDocument();
    expect(screen.getByText("2026-07")).toBeInTheDocument();
    expect(commands[0][0]).toBe("AnalyzeImportFile");
    expect(commands[0][1]).toHaveProperty("source_file_reference", "file_token_01");
    expect(commands[0][1]).not.toHaveProperty("source_file_path");
  });

  it("setzt einen Import an der projizierten Kontenzuordnung fort", async () => {
    const { ipc } = ipcFor(2);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Konten zuordnen" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Lokales Konto" })).toHaveValue("acc_main");
    expect(screen.queryByText(/gespeicherte Stelle/i)).not.toBeInTheDocument();
  });

  it("stellt die vollständige Vorschau wieder her und verlangt eine explizite Bestätigung", async () => {
    const { ipc, commands } = ipcFor(4);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Vollständige Importvorschau" })).toBeInTheDocument();
    expect(await screen.findByText("-42.00 EUR")).toBeInTheDocument();
    const execute = screen.getByRole("button", { name: "Import verbindlich ausführen" });
    expect(execute).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(execute).toBeEnabled();
    fireEvent.click(execute);
    await waitFor(() => expect(commands.some(([name]) => name === "ImportMappedSections")).toBe(true));
  });

  it("zeigt das dauerhaft rekonstruierte Ergebnis und die Importhistorie", async () => {
    const { ipc } = ipcFor(5);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Importergebnis" })).toBeInTheDocument();
    expect(screen.getByText("2", { selector: ".result-metrics strong" })).toBeInTheDocument();
    expect(screen.getByText("BANK_A · 2026-07")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("ImportSectionCompleted");
  });

  it("blockiert den Assistenten bei fehlenden Backend-Fähigkeiten", () => {
    const { ipc } = ipcFor(null);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={{ ...manifest, capabilities: {} }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Import nicht kompatibel");
  });
});
