import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useFetch } from "./useFetch";

// Mock the api module so we don't hit real fetch.
vi.mock("../api/client", () => ({
  api: vi.fn(),
}));

import { api } from "../api/client";

describe("useFetch", () => {
  beforeEach(() => {
    // Default: never resolves. Tests that need a real value re-mock per case.
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(() => new Promise(() => {}));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with loading=true when path is set", () => {
    const { result } = renderHook(() => useFetch("/foo"));
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(true); // path truthy → initial loading true
    expect(result.current.error).toBeNull();
  });

  it("starts with loading=false when path is null", () => {
    const { result } = renderHook(() => useFetch(null));
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("sets data and clears loading on success", async () => {
    vi.mocked(api).mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useFetch<{ ok: boolean }>("/foo"));
    // Wait a tick for promise to settle.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data).toEqual({ ok: true });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("sets error and clears loading on failure", async () => {
    vi.mocked(api).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useFetch("/foo"));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe("boom");
  });

  it("stringifies non-Error rejects", async () => {
    vi.mocked(api).mockRejectedValue("plain string");
    const { result } = renderHook(() => useFetch("/foo"));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.error).toBe("plain string");
  });

  it("refetch re-invokes api", async () => {
    vi.mocked(api).mockResolvedValue({ n: 1 });
    const { result } = renderHook(() => useFetch<{ n: number }>("/foo"));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api).toHaveBeenCalledTimes(1);

    vi.mocked(api).mockResolvedValue({ n: 2 });
    act(() => {
      result.current.refetch();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ n: 2 });
  });

  it("aborts request after timeoutMs and silently swallows AbortError", async () => {
    vi.useFakeTimers();
    // Mock api to register an abort listener and reject with AbortError when aborted.
    vi.mocked(api).mockImplementation((_path: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const { result } = renderHook(() => useFetch("/slow", 1000));

    // Before timeout: still loading.
    expect(result.current.loading).toBe(true);

    // Advance past the timeout.
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    // After timeout: loading false, no error set (AbortError swallowed silently).
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.data).toBeNull();
  });

  it("passes signal to api", async () => {
    vi.mocked(api).mockResolvedValue({});
    renderHook(() => useFetch("/foo"));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api).toHaveBeenCalledWith(
      "/foo",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
  });

  it("aborts in-flight request on unmount", async () => {
    vi.useFakeTimers();
    const abortSpy = vi.spyOn(AbortController.prototype, "abort");
    vi.mocked(api).mockImplementation((_path: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const { unmount } = renderHook(() => useFetch("/foo", 10_000));
    unmount();
    expect(abortSpy).toHaveBeenCalled();
    abortSpy.mockRestore();
  });
});
