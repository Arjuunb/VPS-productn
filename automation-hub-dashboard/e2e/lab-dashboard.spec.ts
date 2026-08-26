import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("dashboard reports independent Price Action and SMC account scopes", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/dashboard");

  const pa = page.getByTestId("dashboard-price-action-lab");
  const smc = page.getByTestId("dashboard-smc-strategy-lab");
  await expect(pa).toContainText("Price Action Bot");
  await expect(pa).toContainText("9,932.58 / 9,935.17 USDT");
  await expect(pa).toContainText("PRICE_ACTION_VISUAL_LAB_ONLY");
  await expect(pa).toContainText("POSITION_OPEN");

  await expect(smc).toContainText("SMC Bot");
  await expect(smc).toContainText("10,000 / 10,000 USDT");
  await expect(smc).toContainText("SMC_STRATEGY_LAB_ONLY");
  await expect(smc).toContainText("COMPLETED_CANDLE_RECONCILIATION");
  await expect(smc).toContainText("BLOCKED");

  await pa.getByRole("button", { name: "Open Lab" }).click();
  await expect(page).toHaveURL(/#\/price-action-lab$/);
});
