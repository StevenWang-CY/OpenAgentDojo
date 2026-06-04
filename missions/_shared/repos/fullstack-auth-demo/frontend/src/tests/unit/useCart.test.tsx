import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCart, type CartApi } from "../../useCart";

describe("useCart — happy path", () => {
  it("starts with an empty cart", () => {
    const api: CartApi = { addToCart: vi.fn().mockResolvedValue(undefined) };
    const { result } = renderHook(() => useCart(api));
    expect(result.current.items).toEqual([]);
  });

  it("adds an item optimistically when the server accepts it", async () => {
    const api: CartApi = { addToCart: vi.fn().mockResolvedValue(undefined) };
    const { result } = renderHook(() => useCart(api));

    await act(async () => {
      await result.current.addItem("sku-apple");
    });

    expect(api.addToCart).toHaveBeenCalledWith("sku-apple");
    await waitFor(() => {
      expect(result.current.items).toContain("sku-apple");
    });
  });
});
