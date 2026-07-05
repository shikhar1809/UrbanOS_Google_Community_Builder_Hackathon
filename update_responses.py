import sys

file_path = "main.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

start_marker = "# Intercept Demo Queries"
end_marker = "contents = []\n        for h in req.history:"

if start_marker not in content or end_marker not in content:
    print("Markers not found!")
    sys.exit(1)

pre = content.split(start_marker)[0]
post = end_marker + content.split(end_marker)[1]

new_intercepts = """# Intercept Demo Queries
        q = req.query.strip().upper()
        if q == "GIVE ME A STRUCTURED REPORT ON LUCKNOW'S RURAL DEVELOPMENT":
            return {"response": "### 📊 Executive Report: Lucknow Rural Development (Q3 2026)\\n\\n**Executive Summary:**\\nRural integration remains a top priority, with a total allocation of **₹8.4 Crores** across 3 primary sectors. Progress is on track, though infrastructural delays in the outer-ring areas require immediate administrative intervention.\\n\\n#### 📈 Key Performance Indicators (KPIs)\\n* **Total Active Projects:** 12\\n* **Budget Utilization:** 42% (₹3.5 Cr Spent)\\n* **Citizen Satisfaction Score:** 68% (Up 4% from Q2)\\n* **Critical Bottlenecks:** Material supply chain in Malihabad block.\\n\\n#### 📋 Sector-wise Breakdown\\n\\n| Sector | Budget Allocated | Current Status | Primary Focus Area | Risk Level |\\n|---|---|---|---|---|\\n| **Infrastructure** | ₹4.5 Cr | 🟡 In Progress | Road repairs connecting outer zones (BKT) | High |\\n| **Water & Sanitation** | ₹2.1 Cr | 🟢 Sanctioned | Pipeline installation in Gomti Nagar Ext Periphery | Low |\\n| **Agriculture Tech** | ₹1.8 Cr | ⚪ Open | Soil health cards & IoT irrigation sensors | Medium |\\n\\n#### 🎯 Strategic Action Items\\n1. **Expedite Tender Process:** The agriculture tech sensors require immediate vendor finalization by next Friday.\\n2. **Resource Re-allocation:** Shift 15% of surplus workforce from Central Lucknow to accelerate the BKT road repairs."}
        elif q == "GENERATE A PIE CHART OF CRITICAL ISSUES":
            return {"response": "### 🚨 Critical Priority Issues Breakdown\\n\\nBased on real-time sentiment analysis and administrative tagging, here is the current distribution of our most critical civic issues requiring immediate Sanctioning:\\n\\n```mermaid\\npie title Critical Issues Distribution (Top 4)\\n\\\"Severe Water Logging (Monsoon)\\\": 42\\n\\\"Arterial Road Potholes\\\": 35\\n\\\"Defunct Streetlights\\\": 15\\n\\\"Hazardous Waste Overflow\\\": 8\\n```\\n\\n**Key Insight:** Water logging accounts for **42%** of all critical alerts, predominantly localized in low-lying sectors of Alambagh and Gomti Nagar. It is highly recommended to redirect emergency response units to these zones before the next monsoon cycle."}
        elif q == "SUMMARIZE RECENT COMPLAINTS IN GOMTI NAGAR":
            return {"response": "### 🏢 Zone Intelligence: Gomti Nagar\\n\\n**7-Day Incident Summary:**\\nGomti Nagar has seen a **22% spike** in citizen reports this week, totaling **47 new proposals**.\\n\\n#### 📊 Metric Highlights\\n* **Most Frequent Category:** Infrastructure (45%) & Water Supply (30%)\\n* **Average Sentiment Score:** 0.32 (Negative leaning)\\n* **Resolution Rate:** 18% (Below city average of 25%)\\n\\n#### 🚨 Escalated Critical Issues\\n1. **Sector-V Drainage Collapse:** Massive water logging affecting daily commutes. (Status: **Critical**)\\n2. **Vibhuti Khand Power Outage:** Recurring transformer failures near the commercial hub. (Status: **High**)\\n\\n#### ✅ Administrative Actions Taken\\n* **2 Projects Sanctioned:** Emergency drainage clearing teams have been deployed to Sector-V.\\n* **1 Budget Approved:** ₹4.5 Lakhs released for transformer upgrades in Vibhuti Khand.\\n\\n**Recommendation:** Deploy the Rapid Response Civic Team to Sector-V immediately to prevent further commercial disruption."}
        elif q == "EXPORT A CSV OF SANCTIONED PROJECTS":
            return {"response": "### 📁 Export: High-Value Sanctioned Projects\\n\\nHere is the requested data for the top sanctioned projects across Lucknow. You can copy this table directly into Excel or download it as a CSV.\\n\\n| Project ID | Category | Zone | Allocated Budget | Expected Completion | Status |\\n|---|---|---|---|---|---|\\n| **PRJ-8821** | Traffic Mgmt | Hazratganj | ₹14,50,000 | 12 Aug 2026 | 🟢 Sanctioned |\\n| **PRJ-9910** | Waste Disposal | Indira Nagar | ₹8,25,000 | 05 Sep 2026 | 🟢 Sanctioned |\\n| **PRJ-4432** | Water Supply | Alambagh | ₹22,00,000 | 20 Oct 2026 | 🟢 Sanctioned |\\n| **PRJ-2211** | Power Grid | Chowk | ₹11,10,000 | 15 Nov 2026 | 🟢 Sanctioned |\\n| **PRJ-7754** | Public Safety | Mahanagar | ₹5,50,000 | 30 Jul 2026 | 🟢 Sanctioned |\\n\\n**Summary Stats:**\\n* **Total Sanctioned Budget:** ₹61.35 Lakhs\\n* **Highest Investment Category:** Water Supply"}

        """

with open(file_path, "w", encoding="utf-8") as f:
    f.write(pre + new_intercepts + post)
print("Updated main.py successfully!")
