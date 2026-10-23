import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DesktopBridgeUnavailableError,
  DesktopContractCompatibilityError,
  DesktopOperationError,
  DesktopResponseError,
  FinanceBridge,
  type DesktopFinanceIPC,
  type DesktopResponse,
} from "./bridge";

const successfulIpc = (): DesktopFinanceIPC => ({
  selectImportFile: vi.fn(async () => null),
  selectLocalModelFile: vi.fn(async () => null),
  selectLocalModelManifest: vi.fn(async () => null),
  selectLocalAIRuntime: vi.fn(async () => null),
  getRuntimeStatus: vi.fn(async (requestId): Promise<DesktopResponse> => ({
    request_id: requestId,
    operation: "runtime_status",
    contract_version: "1.3.0",
    status: "OK",
    result: { network_egress_disabled: true },
  })),
  invoke: vi.fn(async (requestId, operation): Promise<DesktopResponse> => ({
    request_id: requestId,
    operation,
    contract_version: "1.3.0",
    status: "OK",
    result: {
      schema_version: "1.0.0",
      state: "READY",
      projection_sequence: 0,
      event_store_sequence: 0,
      data: { status: "READY" },
    },
  })),
});

afterEach(() => {
  delete window.__FINANCE_IPC__;
});

describe("Desktop bridge boundary", () => {
  it("fails closed when no host injected the bridge", async () => {
    delete window.__FINANCE_IPC__;
    await expect(new FinanceBridge().query("GetStartupStatus"))
      .rejects.toBeInstanceOf(DesktopBridgeUnavailableError);
  });

  it("rejects invalid host responses", async () => {
    const ipc = successfulIpc();
    ipc.invoke = vi.fn(async (): Promise<DesktopResponse> => ({
      request_id: "",
      operation: "query",
      contract_version: "1.3.0",
      status: "OK",
      result: {},
    }));
    window.__FINANCE_IPC__ = ipc;
    await expect(new FinanceBridge().query("GetStartupStatus"))
      .rejects.toBeInstanceOf(DesktopResponseError);
  });

  it("rejects an incompatible desktop contract", async () => {
    const ipc = successfulIpc();
    ipc.invoke = vi.fn(async (requestId): Promise<DesktopResponse> => ({
      request_id: requestId,
      operation: "query",
      contract_version: "2.0.0",
      status: "OK",
      result: {},
    }));
    window.__FINANCE_IPC__ = ipc;
    await expect(new FinanceBridge().query("GetStartupStatus"))
      .rejects.toBeInstanceOf(DesktopContractCompatibilityError);
  });

  it("accepts only opaque file metadata from the host contract", async () => {
    const ipc = successfulIpc();
    ipc.selectImportFile = vi.fn(async () => ({
      file_ref: "file_9c117cd92b034cc6bff4d92e060a55df",
      display_name: "Juli.csv",
      size_bytes: 2048,
    }));
    window.__FINANCE_IPC__ = ipc;
    const selected = await new FinanceBridge().selectImportFile();
    expect(selected).toEqual({
      file_ref: expect.stringMatching(/^file_[a-f0-9]+$/),
      display_name: "Juli.csv",
      size_bytes: 2048,
    });
    expect(JSON.stringify(selected)).not.toContain("/Users/");
  });

  it("preserves stable registration codes and request ids separately from user text", async () => {
    const codes = [
      "LOCAL_MODEL_REFERENCE_EXPIRED", "LOCAL_MODEL_FILE_NOT_FOUND",
      "LOCAL_MODEL_MANIFEST_INVALID", "LOCAL_MODEL_MANIFEST_INCOMPATIBLE",
      "LOCAL_MODEL_HASH_MISMATCH", "LOCAL_MODEL_FORMAT_UNSUPPORTED",
      "LOCAL_MODEL_PURPOSE_UNSUPPORTED", "LOCAL_MODEL_RUNTIME_NOT_FOUND",
      "LOCAL_MODEL_RUNTIME_NOT_EXECUTABLE", "LOCAL_MODEL_RUNTIME_INCOMPATIBLE",
      "LOCAL_MODEL_REGISTRATION_FAILED", "DESKTOP_WORKER_UNAVAILABLE",
      "LOCAL_AI_RUNTIME_NOT_FOUND", "LOCAL_AI_RUNTIME_NOT_EXECUTABLE",
      "LOCAL_AI_RUNTIME_INCOMPATIBLE", "LOCAL_AI_RUNTIME_HASH_MISMATCH",
      "LOCAL_AI_RUNTIME_START_FAILED",
      "DESKTOP_WORKER_INVALID_RESPONSE", "LOCAL_AI_GRAMMAR_INITIALIZATION_FAILED",
      "LOCAL_AI_JSON_PARSE_FAILED", "LOCAL_AI_SCHEMA_VALIDATION_FAILED",
      "LOCAL_AI_SEMANTIC_VALIDATION_FAILED", "LOCAL_AI_TIMEOUT",
      "CATEGORY_SHADOW_DISABLED", "CATEGORY_TRANSACTION_NOT_ELIGIBLE",
      "CATEGORY_TAXONOMY_UNAVAILABLE", "CATEGORY_EVALUATION_ALREADY_RUNNING",
      "CATEGORY_EVALUATION_NOT_FOUND", "CATEGORY_EVALUATION_INCOMPATIBLE_MODEL",
      "CATEGORY_SUGGESTION_NOT_FOUND", "CATEGORY_SUGGESTION_NOT_PENDING",
      "CATEGORY_SUGGESTION_STALE", "CATEGORY_SUGGESTION_CATEGORY_INVALID",
      "CATEGORY_SUGGESTION_DECISION_REQUIRED", "CATEGORY_SUGGESTION_RULE_VALUE_MISSING",
      "CATEGORY_TRANSACTION_ALREADY_CLASSIFIED",
    ];
    for (const code of codes) {
      const ipc = successfulIpc();
      ipc.invoke = vi.fn(async (requestId, operation): Promise<DesktopResponse> => ({
        request_id: requestId,
        operation,
        contract_version: "1.3.0",
        status: "ERROR",
        error: { code, message: "backend technical text" },
      }));
      window.__FINANCE_IPC__ = ipc;
      try {
        await new FinanceBridge().command("RegisterLocalModel", {});
        throw new Error("expected command to fail");
      } catch (error) {
        expect(error).toBeInstanceOf(DesktopOperationError);
        expect((error as DesktopOperationError).code).toBe(code);
        expect((error as DesktopOperationError).requestId).toMatch(/^req_[a-f0-9]{32}$/);
        expect((error as Error).message).not.toContain(code);
        expect((error as Error).message).not.toContain("backend technical text");
      }
    }
  });

  it("maps native runtime picker failures without exposing host details", async () => {
    const ipc = successfulIpc();
    ipc.selectLocalAIRuntime = vi.fn(async () => {
      throw new Error("LOCAL_AI_RUNTIME_NOT_EXECUTABLE");
    });
    window.__FINANCE_IPC__ = ipc;

    await expect(new FinanceBridge().selectLocalAIRuntime()).rejects.toMatchObject({
      code: "LOCAL_AI_RUNTIME_NOT_EXECUTABLE",
      requestId: expect.stringMatching(/^req_/),
    });
  });
});
