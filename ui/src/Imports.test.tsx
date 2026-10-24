import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function ipcFor(
  step: number | null,
  overrides: Partial<DesktopFinanceIPC> = {},
  options: { requirements?: Record<string, unknown>[]; auditHistory?: Record<string, unknown>[] } = {},
) {
  let activeStep = step;
  let automaticBalancesSatisfied = false;
  const commands: Array<[string, Record<string, unknown>]> = [];
  const ipc: DesktopFinanceIPC = {
    selectImportFile: vi.fn(async () => ({ file_ref: "file_token_01", display_name: "Juli.csv", size_bytes: 2048 })),
    selectLocalModelFile: vi.fn(async () => null),
    selectLocalModelManifest: vi.fn(async () => null),
    selectLocalAIRuntime: vi.fn(async () => null),
    getRuntimeStatus: vi.fn(async (requestId) => ({
      request_id: requestId, operation: "runtime_status" as const, contract_version: "1.3.0",
      status: "OK" as const, result: { preview: false },
    })),
    invoke: vi.fn(async (requestId: string, operation: "query" | "command", request: Record<string, unknown>) => {
      const name = String(request.name);
      const payload = (request.payload ?? {}) as Record<string, unknown>;
      if (operation === "command") {
        commands.push([name, payload]);
        if (name === "AnalyzeImportFile") {
          activeStep = 1;
          return {
            request_id: requestId, operation, contract_version: "1.3.0", status: "OK",
            result: { schema_version: "1.0.0", status: "COMPLETED", result: analysis },
          } as const;
        }
        if (name === "MapImportSections") {
          activeStep = (options.requirements ?? []).some((item) => item.required && !item.satisfied) ? 3 : 4;
        }
        if (name === "RecordOpeningBalance") {
          automaticBalancesSatisfied = true;
          activeStep = 4;
        }
        if (name === "ImportMappedSections") {
          if (payload.import_mode === "IMPORT_NEW") activeStep = 5;
          const execution = payload.import_mode === "VALIDATE_ONLY" ? {
            status: "VALID", section_results: [{ section_id: section.section_id, status: "VALID" }],
          } : {
            status: "COMPLETED", normalized_transaction_count: 2, security_transaction_count: 0,
            section_results: [{ ...section, status: "IMPORTED", account_id: "acc_main", local_account_name: "Girokonto", normalized_transaction_count: 2 }],
            relations: [], balance_reconciliations: [], position_reconciliations: [],
          };
          return {
            request_id: requestId, operation, contract_version: "1.3.0", status: "OK",
            result: { schema_version: "1.0.0", status: "COMPLETED", result: execution },
          } as const;
        }
        return {
          request_id: requestId, operation, contract_version: "1.3.0", status: "OK",
          result: { schema_version: "1.0.0", status: "COMPLETED", result: 1 },
        } as const;
      }
      if (name === "GetImportWizardState") {
        if (activeStep === null) return desktopQuery(requestId, envelope(null, "EMPTY"));
        return desktopQuery(requestId, envelope({
          export_id: analysis.export_id,
          current_step: activeStep,
          completed_steps: Array.from({ length: activeStep - 1 }, (_, index) => index + 1),
          status: activeStep === 5 ? "COMPLETED" : "ANALYZED",
          can_resume: activeStep < 5,
          analysis,
          requirements: (options.requirements ?? []).map((item) => automaticBalancesSatisfied ? { ...item, satisfied: true } : item),
          execution_result: activeStep === 5 ? {
            status: "COMPLETED",
            normalized_transaction_count: 2,
            security_transaction_count: 0,
            section_results: [{ ...section, status: "IMPORTED", local_account_name: "Girokonto", normalized_transaction_count: 2 }],
            relations: [],
          } : null,
        }));
      }
      if (name === "GetImportHistory") return desktopQuery(requestId, envelope({ imports: activeStep === null ? [] : [{
        export_id: analysis.export_id, bank_identifier: "BANK_A", report_month: "2026-07",
        imported_at: "2026-07-24T10:00:00Z", completed_at: "2026-07-24T10:05:00Z",
        period_start: "2026-07-01", period_end: "2026-07-31", section_count: 1,
        completed_section_count: activeStep === 5 ? 1 : 0, status: activeStep === 5 ? "COMPLETED" : "ANALYZED",
        record_count: 2, account_count: 1, account_names: ["Girokonto"], open_review_count: 1,
        import_profile: analysis.import_profile, parser_version: "GermanMultiAccountCsvV1@1.0.0",
        source_file_hash: analysis.source_file_hash, resumable: activeStep !== 5,
      }] }));
      if (name === "ListAccounts") return desktopQuery(requestId, envelope({ accounts: [{
        account_id: "acc_main", display_name: "Girokonto", account_type: "CHECKING",
        institution: "BANK_A", currency: "EUR", status: "ACTIVE", include_in_cashflow: true,
        include_in_liquidity: true, include_in_net_worth: true, opened_at: "2025-01-01",
        closed_at: null, masked_reference: "•••• 4821", latest_balance: null,
        available_balance: null, balance_date: null, balance_source: null,
        reconciliation_status: "NOT_RECONCILED", freshness: "CURRENT",
      }] }));
      if (name === "GetImportSectionPreview") return desktopQuery(requestId, envelope({
        ...section, first_booking_date: "2026-07-01", last_booking_date: "2026-07-20",
        amount_sum: "-42.00", duplicate_candidate_count: 0,
        preview: [{ booking_date: "2026-07-01", description: "Miete", amount: "-40.00" }],
      }));
      if (name === "GetImportExecutionResult") return desktopQuery(requestId, envelope({
        status: "COMPLETED", normalized_transaction_count: 2, security_transaction_count: 0,
        section_results: [{ ...section, status: "IMPORTED", local_account_name: "Girokonto", normalized_transaction_count: 2 }],
        relations: [],
      }));
      if (name === "GetImportHistoryDetail") return desktopQuery(requestId, envelope({
        status: "COMPLETED", analysis,
        execution_result: {
          status: "COMPLETED",
          section_results: [{
            ...section, status: "IMPORTED", local_account_name: "Girokonto",
            normalized_transaction_count: 2,
            balance_reconciliation: { status: "MATCHED" },
          }],
        },
        transactions: [
          {
            transaction_id: "txn_market", section_id: "sec_giro", account_id: "acc_main",
            account_name: "Girokonto", booking_date: "2026-07-20", counterparty: "Markthalle",
            description: "Lebensmittel", amount: "-42.00", currency: "EUR", direction: "DEBIT",
            category_code: "FOOD_GROCERIES", classification_status: "CONFIRMED",
          },
          {
            transaction_id: "txn_rent", section_id: "sec_giro", account_id: "acc_main",
            account_name: "Girokonto", booking_date: "2026-07-01", counterparty: "Vermieter",
            description: "Monatsmiete", amount: "-900.00", currency: "EUR", direction: "DEBIT",
            category_code: "UNCLASSIFIED", classification_status: "UNCLASSIFIED",
          },
        ],
        security_transactions: [],
        review_summary: {
          unclassified_transaction_count: 1, open_relation_count: 0,
          balance_review_count: 0, position_review_count: 0, total_open_count: 1,
        },
        audit_history: options.auditHistory ?? [{ sequence_number: 42, event_type: "ImportSectionCompleted", occurred_at: "2026-07-24T10:00:00Z" }],
      }));
      return desktopQuery(requestId, envelope(null, "EMPTY"));
    }),
    ...overrides,
  };
  return { ipc, commands };
}

function desktopQuery(requestId: string, result: unknown) {
  return {
    request_id: requestId,
    operation: "query" as const,
    contract_version: "1.3.0",
    status: "OK" as const,
    result,
  };
}

afterEach(() => {
  cleanup();
  delete window.__FINANCE_IPC__;
});

describe("Importassistent 1.2.0", () => {
  it("bearbeitet und bestätigt einen lokal erzeugten Mapping-Vorschlag", async () => {
    const { ipc, commands } = ipcFor(null);
    const originalInvoke = ipc.invoke.bind(ipc);
    ipc.invoke = vi.fn(async (requestId, operation, request) => {
      const name = String(request.name);
      if (operation === "command" && name === "AnalyzeImportFile") {
        throw new Error("IMPORT_FORMAT_UNKNOWN");
      }
      if (operation === "command" && name === "AnalyzeUnknownImportFormat") {
        commands.push([name, (request.payload ?? {}) as Record<string, unknown>]);
        return {
          request_id: requestId, operation, contract_version: "1.3.0", status: "OK",
          result: { schema_version: "1.0.0", status: "COMPLETED", result: {
            status: "PROPOSAL_READY", format_status: "UNKNOWN", analysis_id: "unknown_1", ai_used: true,
            source_structure: { columns: ["Datum", "Betrag", "Währung"], encoding: "utf-8", delimiter: ";", section_title: "Umsätze" },
            proposal: {
              proposal_id: "mapping_proposal_1", analysis_id: "unknown_1", model_id: "local-test", model_version: "1.0.0",
              section_type: "CHECKING", confidence: 0.98, warnings: [], ignored_columns: [], constants: {},
              field_mappings: [
                { source_column: "Datum", target_field: "booking_date", confidence: 0.99 },
                { source_column: "Betrag", target_field: "amount", confidence: 0.99 },
                { source_column: "Währung", target_field: "currency", confidence: 0.99 },
              ],
              transformations: [
                { target_field: "booking_date", type: "PARSE_DATE", parameters: { format: "DD.MM.YYYY" } },
                { target_field: "amount", type: "PARSE_DECIMAL", parameters: { decimal_separator: ",", thousands_separator: "." } },
              ],
              validation: { validation_result_id: "validation_1", status: "VALID", rows_total: 2, rows_valid: 2, rows_invalid: 0, errors: [], warnings: [] },
            },
          } },
        } as const;
      }
      if (operation === "command" && name === "ValidateImportMapping") {
        commands.push([name, (request.payload ?? {}) as Record<string, unknown>]);
        return { request_id: requestId, operation, contract_version: "1.3.0", status: "OK", result: { schema_version: "1.0.0", status: "COMPLETED", result: { validation_result_id: "validation_1", status: "VALID", rows_total: 2, rows_valid: 2, rows_invalid: 0, errors: [], warnings: [] } } } as const;
      }
      if (operation === "command" && name === "ConfirmImportMapping") {
        commands.push([name, (request.payload ?? {}) as Record<string, unknown>]);
        return { request_id: requestId, operation, contract_version: "1.3.0", status: "OK", result: { schema_version: "1.0.0", status: "COMPLETED", result: { profile: { name: "BANK_A – Checking", profile_version: "1.0.0", source: "USER_CONFIRMED_AI" } } } } as const;
      }
      return originalInvoke(requestId, operation, request);
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={{ ...manifest, contract_version: "1.5.0" }} />);
    fireEvent.change(await screen.findByPlaceholderText("z. B. BANK_A"), { target: { value: "BANK_A" } });
    expect(screen.queryByRole("button", { name: "Formatproblem lösen" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lokale CSV auswählen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Formatproblem lösen" }));
    expect(await screen.findByRole("heading", { name: "Spaltenzuordnung prüfen" })).toBeInTheDocument();
    expect(screen.getByText("2 / 2 Datensätzen gültig")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mapping bestätigen und Profil anlegen" }));
    expect(await screen.findByRole("heading", { name: "BANK_A – Checking" })).toBeInTheDocument();
    expect(commands.some(([name]) => name === "ConfirmImportMapping")).toBe(true);
  });

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
    expect(await screen.findByRole("heading", { name: "BANK_A" })).toBeInTheDocument();
    expect(screen.getByText("Juli 2026")).toBeInTheDocument();
    expect(screen.getByText("Alles bereit.")).toBeInTheDocument();
    expect(screen.getByText("Konten erkannt").previousSibling).toHaveTextContent("1");
    expect(commands[0][0]).toBe("AnalyzeImportFile");
    expect(commands[0][1]).toHaveProperty("source_file_reference", "file_token_01");
    expect(commands[0][1]).not.toHaveProperty("source_file_path");
    expect(commands.some(([name]) => name === "MapImportSections")).toBe(true);
    expect(commands.some(([name, payload]) => name === "ImportMappedSections" && payload.import_mode === "VALIDATE_ONLY")).toBe(true);

    fireEvent.click(screen.getByText("Details anzeigen"));
    expect(screen.getByText("Fingerprint")).toBeInTheDocument();
    expect(screen.getByText("Parser")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Importieren" }));
    expect(await screen.findByRole("heading", { name: "Import abgeschlossen" })).toBeInTheDocument();
    expect(commands.some(([name, payload]) => name === "ImportMappedSections" && payload.import_mode === "IMPORT_NEW")).toBe(true);
  });

  it("übernimmt einen sicheren Carry-forward im bekannten Folgeformat ohne Zusatzschritt", async () => {
    const { ipc, commands } = ipcFor(null, {}, {
      requirements: [{
        account_id: "acc_main", section_id: section.section_id, requirement_type: "OPENING_BALANCE",
        required: true, satisfied: false, record: null,
        suggested_opening_balance: {
          eligible: true, reason_code: "ELIGIBLE", period_start: "2026-06-01", period_end: "2026-06-30",
          suggested_value: "3145.24", currency: "EUR",
          source_closing_balance_event_id: "evt_closing", source_reconciliation_id: "recon_june",
        },
      }],
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);

    fireEvent.click(await screen.findByRole("button", { name: "Lokale CSV auswählen" }));

    expect(await screen.findByText("Alles bereit.")).toBeInTheDocument();
    expect(screen.getByText(/Anfangssaldo wurde aus bestätigten Endsalden/)).toBeInTheDocument();
    const recorded = commands.find(([name]) => name === "RecordOpeningBalance")?.[1];
    expect(recorded).toMatchObject({
      account_id: "acc_main", booked_balance: "3145.24",
      carry_forward_source_reconciliation_id: "recon_june", source: "PREVIOUS_SYSTEM",
    });
  });

  it("zeigt einen Fehler der nativen Dateiauswahl anstatt die UI abzubrechen", async () => {
    const { ipc } = ipcFor(null, {
      selectImportFile: vi.fn(async () => {
        throw new Error("FINANCE_IMPORT_FILE_CLOUD_PATH");
      }),
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);

    fireEvent.click(await screen.findByRole("button", { name: "Lokale CSV auswählen" }));

    expect(await screen.findByText("FINANCE_IMPORT_FILE_CLOUD_PATH")).toBeInTheDocument();
  });

  it("setzt einen Import an der projizierten Kontenzuordnung fort", async () => {
    const { ipc } = ipcFor(2);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Konten zuordnen" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Bestehendes Konto" })).toHaveValue("acc_main");
    expect(screen.queryByText(/gespeicherte Stelle/i)).not.toBeInTheDocument();
  });

  it("übernimmt den bestätigten Endsaldo des Vormonats automatisch als Anfangssaldo", async () => {
    const { ipc } = ipcFor(3, {}, {
      requirements: [{
        account_id: "acc_main", section_id: section.section_id, requirement_type: "OPENING_BALANCE",
        required: false, satisfied: true, record: null,
        suggested_opening_balance: {
          eligible: true, reason_code: "ELIGIBLE", period_start: "2026-06-01", period_end: "2026-06-30",
          suggested_value: "3145.24", currency: "EUR",
          source_closing_balance_event_id: "evt_closing", source_reconciliation_id: "recon_june",
        },
      }],
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByRole("heading", { name: "Anfangswerte prüfen" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText(/Anfangssaldo am/)).toHaveValue("3145.24"));
    expect(screen.getByText(/Automatisch aus dem bestätigten Endsaldo des Vormonats übernommen/)).toBeInTheDocument();
    expect(screen.getByText(/Quelle: Bestätigter Endsaldo/)).toHaveTextContent("2026-06-30");
  });

  it("zeigt einen Hinweis, wenn der Anfangssaldo nicht automatisch übernommen werden kann", async () => {
    const { ipc } = ipcFor(3, {}, {
      requirements: [{
        account_id: "acc_main", section_id: section.section_id, requirement_type: "OPENING_BALANCE",
        required: false, satisfied: true, record: null,
        suggested_opening_balance: {
          eligible: false, reason_code: "UNRESOLVED_DIFFERENCE", period_start: "2026-06-01", period_end: "2026-06-30",
        },
      }],
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    expect(await screen.findByText(/Der Anfangssaldo konnte nicht automatisch übernommen werden/)).toBeInTheDocument();
    expect(screen.getByText(/ungelöste Saldodifferenz/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Anfangssaldo am/)).toHaveValue("");
  });

  it("verlangt eine Begründung, wenn der übernommene Anfangssaldo geändert wird", async () => {
    const { ipc, commands } = ipcFor(3, {}, {
      requirements: [{
        account_id: "acc_main", section_id: section.section_id, requirement_type: "OPENING_BALANCE",
        required: false, satisfied: true, record: null,
        suggested_opening_balance: {
          eligible: true, reason_code: "ELIGIBLE", period_start: "2026-06-01", period_end: "2026-06-30",
          suggested_value: "3145.24", currency: "EUR",
          source_closing_balance_event_id: "evt_closing", source_reconciliation_id: "recon_june",
        },
      }],
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    const openingInput = await screen.findByLabelText(/Anfangssaldo am/);
    fireEvent.change(openingInput, { target: { value: "3000.00" } });
    const save = screen.getByRole("button", { name: "Anfangswerte bestätigen und Vorschau erstellen" });
    fireEvent.click(save);
    expect(await screen.findByText(/Begründung.*erforderlich/)).toBeInTheDocument();
    expect(commands.some(([name]) => name === "RecordOpeningBalance")).toBe(false);

    fireEvent.change(screen.getByLabelText(/Begründung für die Abweichung/), {
      target: { value: "Bankkorrektur nach Rücklastschrift" },
    });
    fireEvent.click(save);
    await waitFor(() => expect(commands.some(([name]) => name === "RecordOpeningBalance")).toBe(true));
    const recorded = commands.find(([name]) => name === "RecordOpeningBalance")?.[1];
    expect(recorded).toMatchObject({
      booked_balance: "3000.00",
      suggested_value: "3145.24",
      adjustment_reason: "Bankkorrektur nach Rücklastschrift",
    });
  });

  it("übersetzt technische Eventnamen im Auditverlauf und bietet eine Fallback-Darstellung für unbekannte Events", async () => {
    const { ipc } = ipcFor(5, {}, {
      auditHistory: [
        { sequence_number: 41, event_type: "OpeningBalanceRecorded", occurred_at: "2026-07-24T09:00:00Z", account_id: "acc_main", payload: { booked_balance: "3145.24", currency: "EUR", balance_date: "2026-06-30" } },
        { sequence_number: 140, event_type: "SomeFutureEvent", occurred_at: "2026-07-24T10:00:00Z" },
      ],
    });
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={manifest} />);
    fireEvent.click(await screen.findByRole("button", { name: "Details" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Auditverlauf" }));
    expect(dialog).toHaveTextContent("Anfangssaldo erfasst");
    expect(dialog).toHaveTextContent("Unbekanntes Ereignis");
    expect(dialog).toHaveTextContent("SomeFutureEvent");
    expect(dialog).toHaveTextContent("globale Event-Sequenz");
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
    expect(await screen.findByRole("heading", { name: "Import abgeschlossen" })).toBeInTheDocument();
    expect(screen.getByText(/2 Buchungen importiert/)).toBeInTheDocument();
    expect(screen.getByText(/1 Konto verarbeitet/)).toBeInTheDocument();
    expect(screen.getByText("1 Punkt benötigt Prüfung")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Importdetails"));
    expect(screen.getByText("2", { selector: ".result-metrics strong" })).toBeInTheDocument();
    expect(screen.getByText("BANK_A · 2026-07")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Offene Prüfungen");
    expect(dialog).toHaveTextContent("Girokonto");
    fireEvent.click(within(dialog).getByRole("tab", { name: /Importierte Buchungen/ }));
    expect(dialog).toHaveTextContent("Markthalle");
    expect(dialog).toHaveTextContent("Lebensmittel");
    expect(dialog).toHaveTextContent("Lebensmittel");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Auditverlauf" }));
    expect(dialog).toHaveTextContent("ImportSectionCompleted");
  });

  it("blockiert den Assistenten bei fehlenden Backend-Fähigkeiten", () => {
    const { ipc } = ipcFor(null);
    window.__FINANCE_IPC__ = ipc;
    render(<Imports uiMonth="2026-07" manifest={{ ...manifest, capabilities: {} }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Import nicht kompatibel");
  });
});
