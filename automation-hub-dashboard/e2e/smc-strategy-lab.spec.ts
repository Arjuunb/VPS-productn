import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "tablet", width: 900, height: 1100 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`SMC Strategy Lab is readable and operable on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto("/#/smc-strategy-lab");
    await expect(page.getByRole("heading", { name: "SMC Strategy Lab" })).toBeVisible();
    await expect(page.getByLabel("Native SMC chart workspace")).toBeVisible();
    await page.getByRole("button", { name: "Journal 0", exact: true }).click();
    await expect(page.getByText("SMC decision journal")).toBeVisible();
    await page.getByRole("button", { name: "Connection" }).click();
    await expect(page.getByText("SYNCHRONIZED", { exact: true })).toBeVisible();
    await expect(page.getByText("CLOSED-BAR PAPER ENTRIES ELIGIBLE")).toBeVisible();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    expect(overflow).toBe(false);
  });
}

test("SMC Visual Lab and SMC Strategy Lab have separate sidebar routes and page identities", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/smc-visual-lab");

  await expect(page).toHaveURL(/#\/smc-visual-lab$/);
  await expect(page.getByRole("heading", { name: "Native SMC Visual Lab" })).toBeVisible();
  await expect(page.getByText("SMC PAPER ACCOUNT", { exact: true })).toHaveCount(0);

  await page.locator("aside.sidebar").getByRole("button", { name: "SMC Strategy Lab" }).click();
  await expect(page).toHaveURL(/#\/smc-strategy-lab$/);
  await expect(page.getByRole("heading", { name: "SMC Strategy Lab" })).toBeVisible();
  await expect(page.getByText("SMC PAPER ACCOUNT", { exact: true })).toBeVisible();

  await page.locator("aside.sidebar").getByRole("button", { name: "SMC Visual Lab" }).click();
  await expect(page).toHaveURL(/#\/smc-visual-lab$/);
  await expect(page.getByRole("heading", { name: "Native SMC Visual Lab" })).toBeVisible();
});
