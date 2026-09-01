import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const STORAGE_KEY = "tradelogx:nexus-pet:v1";

test("Nexus pet migrates legacy choices and exposes the premium companion roster", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(({ key }) => {
    window.localStorage.setItem(key, JSON.stringify({ pet: "fireball", size: "large" }));
  }, { key: STORAGE_KEY });

  await page.goto("/#/overview");

  const pet = page.getByRole("button", { name: /Volt, Nexus pet:/ });
  await expect(pet).toBeVisible();
  await expect(pet.locator(".nexus-pet-laptop")).toBeVisible();
  await expect(pet.locator(".nexus-pet-laptop-chart")).toBeVisible();

  await pet.click();
  await page.getByRole("button", { name: "Open pet settings" }).click();

  const settings = page.getByRole("dialog", { name: "Nexus pet settings" });
  const roster = settings.getByRole("radiogroup", { name: "Pick a Nexus pet" });
  const expectedNames = ["Sprig", "Pulse", "Orbit", "Glint", "Echo", "Nova", "Volt", "Kiro"];
  await expect(roster.getByRole("radio")).toHaveCount(expectedNames.length);
  for (const name of expectedNames) {
    await expect(roster.getByRole("radio", { name: new RegExp(`^${name}`) })).toBeVisible();
  }

  await roster.getByRole("radio", { name: /^Sprig/ }).click();
  await page.getByRole("button", { name: "Close pet settings" }).click();

  const sprig = page.getByRole("button", { name: /Sprig, Nexus pet:/ });
  await sprig.hover();
  await expect(sprig.locator(".nexus-pet-smile")).toHaveCSS("opacity", "0.9");
  await expect(sprig.locator("xpath=ancestor::div[contains(@class, 'nexus-pet-root')]")).toHaveAttribute("data-hovered", "true");

  const stored = await page.evaluate((key) => window.localStorage.getItem(key), STORAGE_KEY);
  expect(JSON.parse(stored ?? "null")).toEqual({ pet: "sprig", size: "large" });
});
