import re
from datetime import datetime, timezone

import pytest
from twilio.request_validator import RequestValidator

import jobs
import main
from tests.dashboard_fixtures import RECIPIENT, report

expect = pytest.importorskip("playwright.sync_api").expect


@pytest.mark.parametrize(("width", "height"), [(1440, 810), (1280, 600)])
def test_every_dashboard_view_and_its_desktop_controls(desktop, width, height):
    page = desktop(width, height)
    expect(page.get_by_role("heading", name="Live Incident Desk")).to_be_visible()
    expect(page.locator("#raw-simulated")).to_have_text("1")
    page.locator("#feed-critical").click()
    expect(page.locator("#raw-feed-container .feed-item")).to_have_count(1)
    expect(page.locator("#raw-feed-container")).to_contain_text("QA-OPEN")
    expect(page.locator("#feed-critical")).to_have_attribute("aria-pressed", "true")

    page.locator("#nav-analysis").click()
    expect(page.locator("#analysis-coverage")).to_contain_text("3 reports in feed · 1 simulated")
    overview = page.get_by_role("tab", name="Overview")
    expect(overview).to_have_attribute("aria-selected", "true")
    overview.focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tabpanel", name="Report Patterns")).to_be_visible()
    page.keyboard.press("End")
    expect(page.get_by_role("tabpanel", name="Backlog & Seasonality")).to_be_visible()
    page.get_by_role("tab", name="Review Progress").click()
    expect(page.get_by_role("tabpanel", name="Review Progress")).to_be_visible()
    expect(page.get_by_role("columnheader", name="Open critical")).to_be_visible()
    expect(page.locator("#pipe-rate")).to_have_text("33%")

    page.locator("#nav-insights").click()
    expect(page.get_by_role("heading", name="Evidence Explorer")).to_be_visible()
    expect(page.locator("#insights-window-note")).to_contain_text("1 simulated")
    expect(page.get_by_text("Evidence map", exact=True)).to_be_in_viewport()

    page.locator("#nav-priority").click()
    for label, count in (("Live demo", "1–1 of 1"), ("Signed intake", "No open recommendations"),
                         ("Unverified history", "1–1 of 1"), ("Sourced examples", "1–2 of 2")):
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.locator("#plan-rank-scope")).to_contain_text(count)
    expect(page.get_by_role("heading", name="Recommended next check")).to_be_visible()
    page.locator(".plan-map-pin[title*='Vineet']").click()
    expect(page.get_by_role("heading", name="Verify school access in Vineet Khand 5")).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Download Report").click()
    assert download.value.suggested_filename == "urbanos-recommendations-examples.txt"
    assert "Recommended next check" in download.value.path().read_text(encoding="utf-8")

    page.locator("#nav-coordination").click()
    expect(page.get_by_role("tab", name="Survey Analytics")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_text("1 · 100%", exact=True)).to_be_visible()
    expect(page.get_by_text("0 · 0%", exact=True)).to_have_count(2)
    expect(page.get_by_role("region", name="Map of recent civic reports in Lucknow")).to_be_in_viewport()
    page.get_by_role("tab", name="Send Survey").click()
    expect(page.locator("#aud-count")).to_have_text("1")
    expect(page.get_by_role("button", name="Review and send survey")).to_be_in_viewport()
    page.locator("#nav-analysis").click()
    page.locator("#nav-coordination").click()
    expect(page.get_by_role("tab", name="Survey Analytics")).to_have_attribute("aria-selected", "true")
    assert page.evaluate("document.scrollingElement.scrollWidth === innerWidth")


def test_live_intake_updates_the_queue_and_analysis(desktop, dashboard):
    page = desktop()
    expect(page.locator("#raw-critical")).to_have_text("1")
    dashboard.db.data["messages"]["arriving"] = report(
        "QA-NEW", simulated=True, **{jobs.LIVE_UPDATED_FIELD: datetime.now(timezone.utc)},
    )
    expect(page.locator("#raw-critical")).to_have_text("2", timeout=10000)
    expect(page.locator("#raw-feed-container")).to_contain_text("QA-NEW")
    page.locator("#nav-analysis").click()
    expect(page.locator("#analysis-coverage")).to_contain_text("4 reports in feed · 2 simulated")


def test_signed_whatsapp_report_reaches_raw_feed_and_planning(desktop, dashboard, monkeypatch):
    page = desktop()
    validator = RequestValidator("local-test-signature-secret")
    monkeypatch.setattr(main, "twilio_validator", validator)
    monkeypatch.setattr(main, "TWILIO_VALIDATE_SIGNATURE", True)
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setenv("TRIAGE_BACKEND", "inline")
    monkeypatch.setattr(main.firestore, "SERVER_TIMESTAMP", datetime.now(timezone.utc))
    url = dashboard.url + "/webhook/whatsapp"

    steps = [
        {"Body": "new report"},
        {"Body": "Sewage is overflowing onto the road in Gomti Nagar."},
        {"Body": "", "Latitude": "26.8534", "Longitude": "80.9981"},
        {"Body": "skip"},
    ]
    assert page.request.post(url, form={"From": RECIPIENT, "Body": "new report"}).status == 403
    for index, step in enumerate(steps):
        form = {"From": RECIPIENT, "MessageSid": f"SM-local-{index}", **step}
        headers = {"X-Twilio-Signature": validator.compute_signature(url, form)}
        receipt = page.request.post(url, form=form, headers=headers)
        assert receipt.ok, receipt.text()
    replay = page.request.post(url, form=form, headers=headers)
    assert replay.text() == receipt.text()
    [filed] = [row for row in dashboard.db.data["messages"].values() if row.get("intake_origin") == main.REVIEW.SIGNED_INTAKE]
    assert f"#{filed['reference_id']}" in receipt.text()
    expect(page.locator("#raw-feed-container")).to_contain_text(filed["reference_id"], timeout=10000)
    page.get_by_role("button", name="Open case desk").click()
    page.locator("#desk-source").select_option("signed")
    expect(page.locator("#desk-rows")).to_contain_text(filed["reference_id"])
    expect(page.locator("#desk-rows")).to_contain_text("Text · GPS pin")
    expect(page.locator("#desk-rows button[data-desk-action]")).to_have_count(0)
    page.locator("#nav-priority").click()
    page.get_by_role("button", name="Signed intake", exact=True).click()
    expect(page.locator("#plan-rank-scope")).to_contain_text("1–1 of 1 recommendations")
    page.get_by_text("Supporting reports", exact=False).click()
    page.get_by_role("button", name=f"Open {filed['reference_id']} in case desk").click()
    expect(page.get_by_role("tabpanel", name="Case Desk")).to_be_visible()
    expect(page.get_by_label("Case reference")).to_have_value(filed["reference_id"])
    expect(page.locator("#desk-rows")).to_contain_text(filed["reference_id"])
    expect(page.locator("#desk-rows button[data-desk-action]")).to_have_count(0)


def test_case_desk_handoff_resolution_and_history(desktop, dashboard):
    page = desktop()
    page.locator("#nav-priority").click()
    page.get_by_role("button", name="Live demo", exact=True).click()
    page.get_by_text("Supporting reports", exact=False).click()
    page.route("**/api/operations/queue?*", lambda route: route.abort(), times=1)
    page.get_by_role("button", name="Open QA-DEMO in case desk").click()
    desk = page.get_by_role("tabpanel", name="Case Desk")
    expect(desk.get_by_text("Insufficient history")).to_be_visible()
    desk.get_by_role("button", name="Acknowledge", exact=True).click()
    expect(desk.get_by_text("Case acknowledged by the prototype desk.")).to_be_visible()
    desk.get_by_role("button", name="Mark resolved", exact=True).click()
    expect(desk.get_by_text("Closure recorded.")).to_be_visible()
    expect(page.locator("#desk-rows")).to_contain_text("Resolved")
    desk.get_by_role("button", name="Clear reference").click()
    page.locator("#desk-stage").select_option("resolved")
    expect(page.locator("#desk-rows")).to_contain_text("QA-DEMO")
    assert dashboard.db.data["messages"]["demo"]["resolved_at"]

    page.locator("#desk-stage").select_option("all")
    page.locator("#desk-source").select_option("examples")
    example = desk.get_by_role("row").filter(has_text="EXAMPLE-01")
    expect(example).to_contain_text("from filing")
    expect(example).to_contain_text("comparable closures")
    expect(example.get_by_role("button")).to_have_count(0)
    page.get_by_role("tab", name="Backlog & Seasonality").click()
    page.locator("#history-source").select_option("examples")
    expect(page.get_by_role("columnheader", name="Tracked backlog")).to_be_visible()
    expect(page.locator("#history-rows tr")).to_have_count(12)
    expect(page.locator("#history-rows")).to_contain_text("0%")


def test_insights_clears_previous_evidence_when_the_next_question_matches_nothing(desktop):
    page = desktop()
    page.route("**/api/analytics?*", lambda route: route.abort(), times=1)
    page.locator("#nav-insights").click()
    expect(page.locator("#insights-window-note")).to_contain_text("1 simulated")
    page.wait_for_function("window.insightsMap && window.liveMessagesData?.length === 3")
    question = page.get_by_role("textbox", name="Ask about Lucknow reports")
    question.fill("Show the trend of complaints over time")
    question.press("Enter")
    page.wait_for_function("window._insightsChartInstance && window.insightsMarkersLayer.getLayers().length > 0")
    page.evaluate("window.insightsMarkersLayer.getLayers()[0].openPopup()")
    expect(page.get_by_role("button", name=re.compile(r"Open QA-.* in Raw Feed"))).to_be_visible()

    question.fill("How many reports were filed in Ward 999?")
    question.press("Enter")
    expect(page.locator("#ai-chat-history")).to_contain_text("No reports match")
    expect(page.locator("#insights-map-count")).to_have_text("0 pinned")
    assert page.evaluate("window._insightsChartInstance === null")


def compose_survey(page):
    page.locator("#nav-coordination").click()
    page.get_by_role("tab", name="Send Survey").click()
    page.locator("#survey-q").fill("How do you rate road repairs?")
    page.locator("#survey-opt").fill("Good, Poor")


def test_cancelling_survey_confirmation_preserves_the_active_survey(desktop, dashboard):
    page = desktop()
    compose_survey(page)
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Review and send survey").click()
    expect(page.locator("#survey-send-status")).to_have_text("Send cancelled.")
    assert list(dashboard.db.data["surveys"]) == ["qa"]
    assert dashboard.db.data["surveys"]["qa"]["is_active"]
    assert dashboard.deliveries == []


def test_detailed_survey_report_displays_markup_as_text(desktop, dashboard):
    option = '<img src=x onerror="window.surveyInjected=1">'
    dashboard.db.data["surveys"]["qa"]["options"] = "Good," + option
    dashboard.db.data["survey_responses"]["r1"]["selected_option"] = option
    page = desktop()
    page.locator("#nav-coordination").click()
    page.get_by_role("button", name="View detailed report").click()
    dialog = page.get_by_role("dialog", name="Survey response report")
    expect(dialog.get_by_role("cell", name=option, exact=True)).to_be_visible()
    expect(dialog.locator("img")).to_have_count(0)
    assert page.evaluate("window.surveyInjected === undefined")
    dialog.get_by_role("button", name="Close survey report").click()
    expect(dialog).to_be_hidden()


def test_confirmed_survey_sends_once_to_the_eligible_recipient(desktop, dashboard):
    dashboard.db.data.setdefault("config", {})["whatsapp_allowlist"] = {"phones": [RECIPIENT]}
    page = desktop()
    compose_survey(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Review and send survey").click()
    expect(page.locator("#survey-send-status")).to_have_text("Sent to the sandbox · 1 real WhatsApp.")
    assert [delivery["to"] for delivery in dashboard.deliveries] == [RECIPIENT]
    assert len(dashboard.db.data["sim_inbox"]) == 1, "the sandbox chat gets every broadcast"
    assert not dashboard.db.data["surveys"]["qa"]["is_active"]
    assert [survey["question"] for survey in dashboard.db.data["surveys"].values() if survey["is_active"]] == [
        "How do you rate road repairs?",
    ]


def test_an_unapproved_recipient_gets_no_real_whatsapp_but_the_sandbox_does(desktop, dashboard):
    page = desktop()
    compose_survey(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Review and send survey").click()
    expect(page.locator("#survey-send-status")).to_have_text("Sent to the sandbox · 0 real WhatsApp.")
    assert dashboard.deliveries == []
    assert len(dashboard.db.data["sim_inbox"]) == 1


def test_survey_activation_failure_preserves_the_draft_and_sends_nothing(desktop, dashboard):
    page = desktop()
    compose_survey(page)
    page.route("**/surveys/*/activate", lambda route: route.fulfill(status=503, json={"detail": "Unavailable"}))
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Review and send survey").click()
    expect(page.locator("#survey-send-status")).to_contain_text("Survey was saved inactive.")
    assert dashboard.deliveries == []
    assert dashboard.db.data["surveys"]["qa"]["is_active"]
    assert len(dashboard.db.data["surveys"]) == 2
