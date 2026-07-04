import sys

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# Replace filtering logic mock
old_filter = """                        // 3. Filter Location (Mock hashing)
                        if (locFilter !== 'all') {
                            let rawLoc = (msg.extracted_location || msg.latitude || "Unknown").toString();
                            let hash = 0;
                            for (let i = 0; i < rawLoc.length; i++) hash = Math.imul(31, hash) + rawLoc.charCodeAt(i) | 0;
                            let zones = ['north', 'south', 'east', 'west', 'central'];
                            let assignedZone = zones[Math.abs(hash) % 5];
                            if (locFilter !== assignedZone) return false;
                        }"""

new_filter = """                        // 3. Filter Location (Native)
                        if (locFilter !== 'all') {
                            let assignedZone = (msg.constituency_zone || 'central').toLowerCase();
                            if (locFilter !== assignedZone) return false;
                        }"""
html = html.replace(old_filter, new_filter)

# Replace budget mock
old_budget = """                    // Estimate Budget (Mock logic based on priority)
                    let estBudget = 500000; // base 5 Lakhs
                    if (prio === "critical") estBudget = 5000000;
                    else if (prio === "high") estBudget = 2500000;
                    else if (prio === "medium") estBudget = 1000000;"""

new_budget = """                    // Estimate Budget (Native)
                    let estBudget = msg.estimated_budget || 0; // 0 for legacy messages without budget"""
html = html.replace(old_budget, new_budget)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
