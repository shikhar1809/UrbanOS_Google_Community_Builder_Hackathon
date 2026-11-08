"""The dashboard has to visibly react to a new report. These guard the wiring, which
failed silently before: nothing errored, the page just stopped moving."""

import json
import re
from pathlib import Path

PAGE = Path("public/index.html").read_text(encoding="utf-8")


def script_of(name):
    start = PAGE.index(f"window.{name} = (function")
    return PAGE[start:PAGE.index("})();", start)]


def test_each_new_report_from_the_feed_reaches_the_live_layer():
    assert "UrbanLive.arrive(data.items)" in script_of("UrbanFeed")


def test_arrivals_refresh_every_dashboard():
    live = script_of("UrbanLive")
    for call in ("mergeLiveReports(items)", "refreshReportDashboards()", "UrbanInsights.refresh()", "fetchRankedProjects()"):
        assert call in live, call
    assert "fetchMessages()" not in live


def test_insights_exposes_its_refresh():
    assert re.search(r"return \{\s*refresh,", PAGE)


def test_a_new_report_does_not_jump_the_map():
    live_map = PAGE[PAGE.index("const LiveReportMap"):PAGE.index("window.updateMapFilters =")]
    assert "setView" not in live_map
    assert "flyTo" not in live_map


def test_citizen_text_reaches_the_map_and_alerts_as_text_not_html():
    live = script_of("UrbanLive")
    assert "innerHTML" not in live
    assert "headline(m)" not in live[live.index("html: '"):live.index("iconSize")]
    assert "label.textContent = headline(m)" in live
    assert "title.textContent = headline(m)" in live


def test_the_homepage_is_not_cached_after_a_deploy():
    """`**/*.html` does not match `/`, so browsers kept the old page for an hour."""
    config = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
    site = next(h for h in config["hosting"] if h.get("site") == "urbanos")
    rule = next(r for r in site["headers"] if r["source"] == "/")
    assert "no-store" in rule["headers"][0]["value"]


def test_motion_has_a_reduced_path():
    assert "@media (prefers-reduced-motion: reduce)" in PAGE
    assert "reducedMotion()" in script_of("UrbanLive")
