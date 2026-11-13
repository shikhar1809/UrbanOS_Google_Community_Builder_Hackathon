"""Build an explainable review shortlist from one report provenance at a time."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from statistics import median
import re

import h3

from demo_locations import DEMO_SITES
from sim import is_simulated_sender


SIGNED_INTAKE = "signed_twilio"
PRIORITY_WEIGHTS = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
GROUP_RADIUS_METRES = 150
GROUP_WINDOW = timedelta(hours=72)
PLAN_URL = "https://lmc.up.nic.in/pdf/Final%20CDP%20-%20Voleme%20II.pdf"
DEMO_SITES_BY_NAME = {site.name.casefold(): site for site in DEMO_SITES}
DEMO_SITE_PATTERN = re.compile(
    r"\b(?:" + "|".join(map(re.escape, sorted(DEMO_SITES_BY_NAME, key=len, reverse=True))) + r")\b", re.I,
)
# Approximate locality anchors from the DPS campus map and the LMC Chinhat 1
# property survey. These identify the example areas rather than project sites.
# https://dpsjankipuram.com/contact-us
# https://lmc.up.nic.in/pdf/Z4_chinhat1_PTMS_GDB_DATA_22nd_MAY_2024_PDF.pdf
EXAMPLE_LOCATIONS = {
    "jankipuram-sector-6": (26.940, 80.941),
    "vineet-khand-5": (26.846, 81.011),
}

SCHOOL_EXAMPLES = (
    ("jankipuram-sector-6", "Jankipuram Sector 6",
     "A representative reported that the settlement had no government school in the locality.",
     ("High", "High"), "Verify current government-school access and travel routes before scoping any work."),
    ("vineet-khand-5", "Vineet Khand 5",
     "A representative described a long walk to nearby schools and a lack of capacity at those schools.",
     ("Medium", "Low", "Low"), "Check current walking routes, enrolment and capacity at nearby schools."),
)


def origin(report: dict) -> str:
    if report["simulated"] or is_simulated_sender(report["sender"]):
        return "demo"
    return "verified" if report["intake_origin"] == SIGNED_INTAKE else "legacy"


def site_key(report: dict, scope: str) -> tuple[str, str, str]:
    lat, lng = report["latitude"], report["longitude"]
    location = str(report["location"])
    if scope == "demo":
        # The generator places both its text and GPS reports along the same known site.
        match = DEMO_SITE_PATTERN.search(location)
        if match:
            key = match.group().casefold()
            return "demo_site", key, DEMO_SITES_BY_NAME[key].name
    if (report["location_source"] == "gps_pin" and lat is not None and lng is not None
            and -90 <= lat <= 90 and -180 <= lng <= 180):
        return "gps_area", h3.latlng_to_cell(lat, lng, 9), ""
    return "unconfirmed", report["id"], "Site needs confirmation"


def intake_formats(report: dict) -> list[str]:
    if isinstance(report["intake_formats"], list):
        recorded = [form for form in report["intake_formats"] if form in ("Text", "Voice", "Photo", "GPS pin")]
        if recorded:
            return recorded
    forms = []
    media = str(report["media_type"] or "").lower()
    if media.startswith("audio/"):
        forms.append("Voice")
    if media.startswith("image/") or len(report["media_urls"]) > 1:
        forms.append("Photo")
    if report["body"] and not media.startswith("audio/"):
        forms.append("Text")
    if report["location_source"] == "gps_pin":
        forms.append("GPS pin")
    return forms or ["Unrecorded"]


def supporting_report(report: dict, scope: str) -> dict:
    return {
        "reference": report["reference_id"],
        "summary": report["summary"] or report["category"],
        "priority": report["priority"],
        "reported_at": report["ts_iso"],
        "formats": intake_formats(report),
        "triage_source": report["triage_source"],
        "open_in_feed": scope == "demo",
    }


def _score(reports: list[dict]) -> tuple[int, dict, int]:
    highest = {}
    uncounted = 0
    for report in reports:
        sender = re.sub(r"^whatsapp:", "", report["sender"] or "", flags=re.I).strip()
        priority = report["priority"].title()
        if not sender:
            uncounted += 1
            continue
        weight = PRIORITY_WEIGHTS.get(priority, 1)
        if weight > highest.get(sender, 0):
            highest[sender] = weight
    by_weight = Counter(highest.values())
    return sum(highest.values()), {name: by_weight[weight] for name, weight in PRIORITY_WEIGHTS.items()}, uncounted


def _case_examples() -> list[dict]:
    projects = []
    for slug, site, finding, priorities, next_check in SCHOOL_EXAMPLES:
        latitude, longitude = EXAMPLE_LOCATIONS[slug]
        projects.append({
            "id": "school-" + slug, "title": f"Verify school access in {site}", "site": site,
            "location_basis": "published_area", "category": "Education",
            "report_count": len(priorities), "unique_reporters": len(priorities), "uncounted_reports": 0,
            "score": sum(PRIORITY_WEIGHTS[p] for p in priorities),
            "priority_counts": {name: priorities.count(name) for name in PRIORITY_WEIGHTS},
            "budget_estimate": None, "next_check": next_check,
            "map_location": {"latitude": latitude, "longitude": longitude},
            "evidence": {
                "title": "Lucknow City Development Plan, Volume II",
                "publisher": "Lucknow Municipal Corporation",
                "published": "January 2015",
                "observed": "Stakeholder consultation on 24 February 2014",
                "page": "Printed page 45",
                "area": site, "finding": finding, "url": PLAN_URL,
                "limitation": "Historical stakeholder evidence. Current school access needs field verification.",
            },
            "reports": [
                {"reference": f"EX-{slug[:3].upper()}-{index}", "summary": f"Example school-access submission from {site}",
                 "priority": priority, "formats": [("Text", "Voice", "Photo")[index - 1]],
                 "triage_source": "example", "reported_at": None, "open_in_feed": False}
                for index, priority in enumerate(priorities, 1)
            ],
        })
    return projects


def _recommendation(tag: str, site: tuple, reports: list[dict], anchor_id: str = "") -> dict:
    method, key, name = site
    first = reports[0]
    score, priorities, uncounted = _score(reports)
    estimates = [report["estimated_budget"] for report in reports if report["estimated_budget"] > 0]
    location = None
    if method == "demo_site":
        demo = DEMO_SITES_BY_NAME[key]
        location = tuple((start + end) / 2 for start, end in zip(demo.start, demo.end))
    elif method == "gps_area":
        location = first["latitude"], first["longitude"]
    identity = f"{tag}|{method}|{key}"
    if anchor_id:
        identity += f"|{anchor_id}"
    return {
        "id": sha256(identity.encode()).hexdigest()[:24],
        "title": first["summary"] or first["category"],
        "category": first["category"], "site": name or "Nearby GPS pins",
        "location_basis": method, "score": score, "priority_counts": priorities,
        "report_count": len(reports), "unique_reporters": sum(priorities.values()), "uncounted_reports": uncounted,
        "budget_estimate": int(median(estimates)) if estimates else None,
        "map_location": ({"latitude": round(location[0], 5), "longitude": round(location[1], 5)}
                         if location else None),
        "evidence": None,
        "grouping": {"radius_metres": GROUP_RADIUS_METRES, "window_hours": GROUP_WINDOW // timedelta(hours=1)}
                    if method == "gps_area" else None,
        "next_check": ("Confirm the exact site before combining this report with others." if method == "unconfirmed"
                       else "Confirm that these reports refer to the same asset and check current infrastructure conditions."),
        "reports": reports,
    }


def recommendations(records: list[dict], scope: str) -> tuple[list[dict], int]:
    if scope == "examples":
        projects = _case_examples()
        return projects, sum(p["report_count"] for p in projects)

    eligible = [report for report in records if origin(report) == scope
                and report["status_bucket"] not in {"completed", "sanctioned"}]
    clusters = defaultdict(list)
    gps_anchors = defaultdict(list)
    for report in sorted(eligible, key=lambda row: (row["filed_at"] or datetime.max, str(row["id"]))):
        tag = report["semantic_tag"] or report["category"]
        site = site_key(report, scope)
        if site[0] != "gps_area":
            clusters[(tag, site, "")].append(report)
            continue

        cell = site[1]
        group = (tag, site, str(report["id"]))
        filed = report["filed_at"]
        if filed is not None:
            point = (report["latitude"], report["longitude"])
            candidates = []
            for neighbor in h3.grid_disk(cell, 1):
                for key in reversed(gps_anchors.get((tag, neighbor), ())):
                    anchor = clusters[key][0]
                    if filed - anchor["filed_at"] > GROUP_WINDOW:
                        break
                    distance = h3.great_circle_distance(point, (anchor["latitude"], anchor["longitude"]), unit="m")
                    if distance <= GROUP_RADIUS_METRES:
                        candidates.append((distance, key))
            # Match the first report in a group so a chain of adjacent pins cannot stretch it across the city.
            if candidates:
                _, group = min(candidates)
            else:
                gps_anchors[(tag, cell)].append(group)
        clusters[group].append(report)

    projects = [_recommendation(tag, site, members, anchor) for (tag, site, anchor), members in clusters.items()]
    projects.sort(key=lambda project: (-project["score"], -project["unique_reporters"], project["id"]))
    return projects, sum(project["report_count"] for project in projects)
