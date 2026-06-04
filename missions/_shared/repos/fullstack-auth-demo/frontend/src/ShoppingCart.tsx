import { useCart, type CartApi } from "./useCart";

interface ShoppingCartProps {
  api: CartApi;
  skus?: string[];
}

/**
 * A minimal cart surface: a row of "Add" buttons and the current cart
 * contents. State (including the optimistic update) lives in `useCart`.
 */
export function ShoppingCart({
  api,
  skus = ["sku-apple", "sku-pear"],
}: ShoppingCartProps) {
  const { items, error, addItem } = useCart(api);

  return (
    <section aria-label="Shopping cart">
      <h2>Cart</h2>
      <div>
        {skus.map((sku) => (
          <button key={sku} type="button" onClick={() => void addItem(sku)}>
            Add {sku}
          </button>
        ))}
      </div>
      <ul aria-label="Cart items">
        {items.map((sku, index) => (
          <li key={`${sku}-${index}`}>{sku}</li>
        ))}
      </ul>
      {error && (
        <p role="alert" style={{ color: "crimson" }}>
          {error}
        </p>
      )}
    </section>
  );
}
