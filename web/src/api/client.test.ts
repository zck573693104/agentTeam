import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "./client";

function mockResponse(
  body: unknown,
  init: { ok?: boolean; status?: number; statusText?: string } = {}
): Response {
  const ok = init.ok ?? true;
  const status = init.status ?? 200;
  const statusText = init.statusText ?? "";
  return {
    ok,
    status,
    statusText,
    json: async () => body,
  } as Response;
}

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("returns parsed JSON on ok response", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ ok: true, items: [1, 2, 3] })
    );
    const data = await api<{ ok: boolean; items: number[] }>("/foo");
    expect(data.ok).toBe(true);
    expect(data.items).toEqual([1, 2, 3]);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/foo",
      expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      })
    );
  });

  it("merges custom headers with default Content-Type", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({})
    );
    await api("/foo", {
      headers: { Authorization: "Bearer xyz" },
    });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/foo",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          Authorization: "Bearer xyz",
        }),
      })
    );
  });

  it("throws string detail from non-ok response", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ detail: "boom" }, { ok: false, status: 400 })
    );
    await expect(api("/foo")).rejects.toThrow("boom");
  });

  it("stringifies non-string detail from non-ok response", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse(
        { detail: { reason: "bad", code: 42 } },
        { ok: false, status: 422 }
      )
    );
    await expect(api("/foo")).rejects.toThrow(/reason/);
  });

  it("falls back to HTTP <status> when body has no detail", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({}, { ok: false, status: 500, statusText: "Internal" })
    );
    await expect(api("/foo")).rejects.toThrow("HTTP 500");
  });

  it("falls back to HTTP <status> when body json parse fails", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new SyntaxError("invalid json");
      },
    } as unknown as Response);
    await expect(api("/foo")).rejects.toThrow("Bad Gateway");
  });
});
