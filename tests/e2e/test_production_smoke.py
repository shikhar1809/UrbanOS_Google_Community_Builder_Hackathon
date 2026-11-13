import os
import re

import pytest

expect = pytest.importorskip("playwright.sync_api").expect
SMOKE_URL = os.environ.get("URBANOS_SMOKE_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not SMOKE_URL, reason="Set URBANOS_SMOKE_URL to enable read-only deployment smoke tests")


def test_deployed_desktop_dashboard(browser):
    with browser.new_context(viewport={"width": 1280, "height": 720}, service_workers="block",
                             reduced_motion="reduce", accept_downloads=True) as context:
        context.route("**/*", lambda route: route.continue_() if route.request.method in {"GET", "HEAD"} else route.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(SMOKE_URL, wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Live Incident Desk")).to_be_visible()
        expect(page.locator("#raw-simulated")).to_have_text(re.compile(r"\d"), timeout=20000)
        page.locator("#feed-critical").click()
        expect(page.locator("#feed-critical")).to_have_attribute("aria-pressed", "true")

        page.locator("#nav-analysis").click()
        for name in ("Overview", "Report Patterns", "Review Progress", "Case Desk", "Backlog & Seasonality"):
            page.get_by_role("tab", name=name).click()
            expect(page.get_by_role("tabpanel", name=name)).to_be_visible()
        page.locator("#history-source").select_option("examples")
        expect(page.locator("#history-rows tr")).to_have_count(12)
        page.get_by_role("tab", name="Case Desk").click()
        page.locator("#desk-source").select_option("examples")
        expect(page.locator("#desk-rows")).to_contain_text("comparable closures")
        page.get_by_label("Case reference").fill("#example-01")
        page.get_by_role("button", name="Find case").click()
        expect(page.locator("#desk-rows tr")).to_have_count(1)
        expect(page.locator("#desk-rows")).to_contain_text("EXAMPLE-01")

        page.locator("#nav-insights").click()
        expect(page.get_by_role("heading", name="Evidence Explorer")).to_be_visible()
        expect(page.locator("#insights-window-note")).to_contain_text("simulated", timeout=20000)

        page.locator("#nav-priority").click()
        expect(page.get_by_role("heading", name="Recommended next check")).to_be_visible(timeout=20000)
        with page.expect_download() as event:
            page.get_by_role("button", name="Download Report").click()
        assert "Recommended next check" in event.value.path().read_text(encoding="utf-8")

        page.locator("#nav-coordination").click()
        expect(page.get_by_role("tab", name="Survey Analytics")).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("region", name="Map of recent civic reports in Lucknow")).to_be_visible()
        page.get_by_role("tab", name="Send Survey").click()
        expect(page.get_by_role("button", name="Review and send survey")).to_be_visible()
        assert not errors
