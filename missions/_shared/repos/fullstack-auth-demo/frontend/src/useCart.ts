import { useCallback, useRef, useState } from "react";

/**
 * The server-side cart API the hook talks to. `addToCart` resolves when
 * the server has accepted the item and rejects when it refuses it (out of
 * stock, rate-limited, network error, …).
 */
export interface CartApi {
  addToCart(sku: string): Promise<void>;
}

export interface UseCartResult {
  items: string[];
  error: string | null;
  addItem(sku: string): Promise<void>;
}

/**
 * Cart state with an optimistic `addItem`.
 *
 * `addItem` appends the sku immediately so the UI feels instant, then
 * confirms with the server. `itemsRef` mirrors the latest committed
 * `items` so callbacks always read the current cart without a stale
 * closure.
 */
export function useCart(api: CartApi): UseCartResult {
  const [items, setItems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const itemsRef = useRef<string[]>(items);
  itemsRef.current = items;

  const addItem = useCallback(
    async (sku: string) => {
      setError(null);
      // Snapshot the pre-update cart for reference.
      const snapshot = itemsRef.current;
      // Optimistic update: show the item right away.
      const next = [...snapshot, sku];
      itemsRef.current = next;
      setItems(next);
      // Confirm with the server. If this rejects we do nothing, so the
      // optimistically-added item stays in the cart even though the server
      // refused it — the local cart now disagrees with the server.
      await api.addToCart(sku);
    },
    [api],
  );

  return { items, error, addItem };
}
