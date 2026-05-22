import { expect, test } from "@playwright/test";

test.describe("landing page", () => {
  test("renders the hero and links to /missions", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { level: 1, name: /Patches that look right/i })
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Browse missions/i })
    ).toHaveAttribute("href", "/missions");
  });
});
