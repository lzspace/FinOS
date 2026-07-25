import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DesktopBridgeUnavailableError,
  DesktopContractCompatibilityError,
  DesktopResponseError,
  FinanceBridge,
  type DesktopFinanceIPC,
  type DesktopResponse,
} from "./bridge";

const successfulIpc = (): DesktopFinanceIPC => ({
  selectImportFile: vi.fn(async () => null),
  getRuntimeStatus: vi.fn(async (): Promise<DesktopResponse> => ({
    request_id: "req_status",
    operation: "runtime_status",
    contract_version: "1.3.0",
    status: "OK",
    result: { network_egress_disabled: true },
  })),
  invoke: vi.fn(async (operation): Promise<DesktopResponse> => ({
    request_id: "req_1",
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
    ipc.invoke = vi.fn(async (): Promise<DesktopResponse> => ({
      request_id: "req_1",
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
});
