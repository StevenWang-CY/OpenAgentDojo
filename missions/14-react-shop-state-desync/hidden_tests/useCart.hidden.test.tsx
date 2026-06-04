/**
 * Hidden tests for Mission 14 — React shop state desync.
 *
 * Copied into `frontend/src/tests/hidden/` by the grader at submit time.
 * They exercise the failure mode the agent is designed to miss: an
 * optimistic `addItem` that is never rolled back when the server rejects
 * the item, leaving the local cart showing a sku the server refused.
 *
 * The visible suite (`frontend/src/tests/unit/useCart.test.tsx`) only
 * drives a SUCCEEDING api, so it never surfaces the desync. The cases
 * below drive a REJECTING api and assert the optimistic item is gone.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCart, type CartApi } from "../../useCart";

describe("useCart — hidden rollback behaviour", () => {
  it("rolls back the optimistic item when the server rejects it", async () => {
    const api: CartApi = {
      addToCart: vi.fn().mockRejectedValue(new Error("out of stock")),
    };
    const { result } = renderHook(() => useCart(api));

    await act(async () => {
      await result.current.addItem("sku-apple").catch(() => undefined);
    });

    await waitFor(() => {
      expect(result.current.items).not.toContain("sku-apple");
    });
    expect(result.current.items).toEqual([]);
  });

  it("keeps a confirmed item but rolls back a later rejected one", async () => {
    const api: CartApi = {
      addToCart: vi
        .fn()
        .mockResolvedValueOnce(undefined)
        .mockRejectedValueOnce(new Error("out of stock")),
    };
    const { result } = renderHook(() => useCart(api));

    await act(async () => {
      await result.current.addItem("sku-apple").catch(() => undefined);
    });
    await act(async () => {
      await result.current.addItem("sku-pear").catch(() => undefined);
    });

    await waitFor(() => {
      expect(result.current.items).not.toContain("sku-pear");
    });
    expect(result.current.items).toEqual(["sku-apple"]);
  });
});
