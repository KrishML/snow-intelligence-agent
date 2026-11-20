import { expect, test, type Page } from "@playwright/test";

const CITIES = [
  { city: "London", country: "United Kingdom", airports: ["LHR", "LGW", "STN", "LTN", "LCY"] },
  { city: "Manchester", country: "United Kingdom", airports: ["MAN"] },
];

const resort = (name: string, rank: number, extra = {}) => ({
  rank,
  resort_id: `id-${name}`,
  name,
  country: "Austria",
  region: "Tyrol",
  altitude_base_m: 1500,
  nearest_airports: ["INN"],
  depth_cm: 80,
  temperature_c: -4,
  forecast_stable: true,
  historical_reliability_pct: 70,
  score: { total: 90 - rank, depth: 30, forecast: 25, historical: 17, qualitative: 15 },
  qualitative: { summary: `${name} has great snow`, sentiment: "positive", negative_keywords: [] },
  webcam: { classification: "camera_offline", description: null, captured_at: null, image_url: null },
  ...extra,
});

async function mockApi(page: Page) {
  const prefs = {
    residence_city: "London",
    residence_country: "United Kingdom",
    preferred_airports: ["LHR", "LGW", "STN", "LTN", "LCY"],
    origin_airport: "LHR",
    excluded_airlines: [],
    mode: "sports",
    budget_total_gbp: null,
    email: null,
    telegram_chat_id: null,
  };
  const patches: unknown[] = [];

  await page.route("**/api/user/prefs", async (route) => {
    if (route.request().method() === "PATCH") {
      const patch = route.request().postDataJSON();
      patches.push(patch);
      Object.assign(prefs, patch);
      if (patch.residence_city) {
        const city = CITIES.find((c) => c.city === patch.residence_city)!;
        prefs.preferred_airports = city.airports;
        prefs.origin_airport = city.airports[0];
      }
    }
    await route.fulfill({ json: prefs });
  });
  await page.route("**/api/user/cities", (route) => route.fulfill({ json: CITIES }));
  await page.route("**/api/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/dashboard/weekly", (route) =>
    route.fulfill({
      json: {
        mode: prefs.mode,
        residence_city: prefs.residence_city,
        origin_airports: prefs.preferred_airports,
        budget_total_gbp: null,
        run: { id: "r1", status: "complete", mode: prefs.mode, run_date: "2026-11-20", started_at: null, completed_at: null },
        last_updated: new Date().toISOString(),
        latest_attempt: null,
        partial_failure: false,
        errors: {},
        resorts:
          prefs.mode === "sports"
            ? [resort("Kitzbuhel", 1), resort("Zermatt", 2)]
            : [resort("Hallstatt", 1)],
      },
    }),
  );
  await page.route("**/api/chat/session", (route) => route.fulfill({ json: { session_id: "sess-1" } }));
  await page.route("**/api/chat/session/sess-1/end", (route) => route.fulfill({ json: { status: "closed" } }));
  await page.routeWebSocket("**/api/chat/session/sess-1/messages", (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.content) {
        ws.send(JSON.stringify({ type: "token", content: "Kitzbuhel looks " }));
        ws.send(JSON.stringify({ type: "token", content: "best this week." }));
        ws.send(JSON.stringify({ type: "message_complete" }));
      }
    });
  });
  return { patches };
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("API token").fill("test-token");
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("shows ranked resorts and switches mode and city", async ({ page }) => {
  const { patches } = await mockApi(page);
  await signIn(page);

  await expect(page.getByRole("article", { name: /Kitzbuhel rank 1/ })).toBeVisible();
  await expect(page.getByRole("article", { name: /Zermatt rank 2/ })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Home city" })).toHaveValue("London");
  for (const code of ["LHR", "LGW", "STN", "LTN", "LCY"]) {
    await expect(page.getByLabel(code, { exact: true })).toBeChecked();
  }

  await page.getByRole("button", { name: "Snow tourism" }).click();
  await expect(page.getByRole("article", { name: /Hallstatt rank 1/ })).toBeVisible();
  await expect(page.getByRole("article", { name: /Kitzbuhel/ })).toHaveCount(0);

  await page.getByRole("combobox", { name: "Home city" }).selectOption("Manchester");
  await expect(page.getByLabel("MAN", { exact: true })).toBeChecked();
  await expect(page.getByLabel("LHR", { exact: true })).toHaveCount(0);
  expect(patches).toEqual([{ mode: "tourism" }, { residence_city: "Manchester" }]);
});

test("chat streams a reply", async ({ page }) => {
  await mockApi(page);
  await signIn(page);

  await page.getByLabel("Message").fill("Where is best?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Kitzbuhel looks best this week.")).toBeVisible();
  await expect(page.getByText("Where is best?")).toBeVisible();
});

test("chat renders a draft plan card with itemised totals and no booking claim", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/chat/session/sess-1/messages");
  await page.routeWebSocket("**/api/chat/session/sess-1/messages", (ws) => {
    ws.onMessage(() => {
      ws.send(JSON.stringify({ type: "tool_call", name: "create_plan_artifact" }));
      ws.send(
        JSON.stringify({
          type: "plan_card",
          plan: {
            id: "p1",
            session_id: "sess-1",
            resort_id: "id-Kitzbuhel",
            resort_name: "Kitzbuhel",
            outbound_date: "2026-12-04",
            return_date: "2026-12-06",
            flight_option: {},
            hotel_option: {},
            flight_total_gbp: "120.00",
            hotel_total_gbp: "300.00",
            total_estimated_gbp: "420.00",
            session_notes: null,
            status: "draft",
            created_at: "2026-11-20T00:00:00Z",
          },
        }),
      );
      ws.send(JSON.stringify({ type: "message_complete" }));
    });
  });
  await signIn(page);

  await page.getByLabel("Message").fill("Plan it");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByRole("article", { name: "Trip plan" });
  await expect(card).toContainText("£420.00");
  await expect(card).toContainText("£120.00");
  await expect(card).toContainText("No booking or payment has been made");
});

test("shows an empty state when no run has completed", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/dashboard/weekly", (route) =>
    route.fulfill({
      json: {
        mode: "sports",
        residence_city: "London",
        origin_airports: ["LHR"],
        budget_total_gbp: null,
        run: null,
        last_updated: null,
        latest_attempt: null,
        partial_failure: false,
        errors: {},
        resorts: [],
      },
    }),
  );
  await signIn(page);
  await expect(page.getByText(/No completed winter sports run yet/)).toBeVisible();
});

test("shows an actionable error when the API fails", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/dashboard/weekly", (route) =>
    route.fulfill({ status: 400, json: { detail: "bad request" } }),
  );
  await signIn(page);
  await expect(page.getByRole("alert").filter({ hasText: "Could not load results: bad request" })).toBeVisible();
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("chat is opened from the header and nothing overflows", async ({ page }) => {
    await mockApi(page);
    await signIn(page);

    await expect(page.getByRole("article", { name: /Kitzbuhel/ })).toBeVisible();
    await expect(page.getByLabel("Message")).toHaveCount(0);
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    await expect(page.getByLabel("Message")).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  });
});
