import sys

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Update rankedProjects logic
old_ranking = """                        let rankedProjects = Object.keys(projects).map(cat => {
                            let p = projects[cat];
                            let gapScore = Math.min(100, 40 + (p.urgency * 10) + (p.count * 2)); 
                            let impact = p.count * gapScore;
                            return { sector: cat, title: p.title, count: p.count, gap: gapScore, impact: impact, loc: p.latestLoc };
                        }).sort((a,b) => b.impact - a.impact);"""

new_ranking = """                        let rankedProjects = Object.keys(projects).map(cat => {
                            let p = projects[cat];
                            let gapScore = Math.min(100, 40 + (p.urgency * 10) + (p.count * 2)); 
                            let impact = p.count * gapScore;
                            let severityText = p.urgency > 2 ? "critical" : (p.urgency > 0 ? "moderate" : "minor");
                            let rationale = `Ranked ${p.count > 5 ? 'High' : 'Normal'} priority due to a ${severityText} ${cat.toLowerCase()} infrastructure gap (${Math.round(gapScore)}/100) affecting ${p.count} recent grievance reports in ${p.latestLoc}.`;
                            return { sector: cat, title: p.title, count: p.count, gap: gapScore, impact: impact, loc: p.latestLoc, rationale: rationale };
                        }).sort((a,b) => b.impact - a.impact);"""

html = html.replace(old_ranking, new_ranking)

# 2. Update the HTML row mapping
old_row = """                            return `<tr class="hover:bg-[#1C1C1C] transition cursor-pointer border-b border-[#262626]">
                                <td class="px-6 py-4">
                                    <div class="text-white font-medium flex items-center gap-2">
                                        <span class="material-symbols-outlined text-[18px] text-zinc-400">account_balance</span>
                                        ${p.title}
                                    </div>
                                    <div class="text-xs text-zinc-500 mt-1">Loc: ${p.loc}</div>
                                </td>"""

new_row = """                            return `<tr class="hover:bg-[#1C1C1C] transition cursor-pointer border-b border-[#262626]">
                                <td class="px-6 py-4 whitespace-normal min-w-[300px]">
                                    <div class="text-white font-medium flex items-center gap-2 mb-1">
                                        <span class="material-symbols-outlined text-[18px] text-zinc-400">account_balance</span>
                                        ${p.title}
                                    </div>
                                    <div class="text-[11px] text-amber-500 font-medium mb-1 italic whitespace-normal max-w-sm">Rationale: ${p.rationale}</div>
                                    <div class="text-xs text-zinc-500">Loc: ${p.loc}</div>
                                </td>"""
                                
html = html.replace(old_row, new_row)

# 3. Add Hex Grid Logic
hex_logic = """
        let hexLayerGroup = null;
        function drawLucknowHexGrid(mapObj, msgData) {
            if (typeof turf === 'undefined' || !mapObj) return;
            if (hexLayerGroup) { mapObj.removeLayer(hexLayerGroup); }
            
            // Bounding box for Lucknow
            const bbox = [80.85, 26.75, 81.05, 26.95];
            const cellSide = 2.5; // kilometers
            const options = {units: 'kilometers'};
            let hexGrid = turf.hexGrid(bbox, cellSide, options);
            
            // Name the hexes and count data
            let zones = ["Gomti Nagar", "Hazratganj", "Alambagh", "Indira Nagar", "Chowk", "Aminabad", "Aashiana", "Kapurthala", "Vikas Nagar", "Mahanagar", "Rajajipuram"];
            
            // Assign random names to hexes
            hexGrid.features.forEach((f, i) => {
                f.properties = f.properties || {};
                f.properties.name = zones[i % zones.length];
                f.properties.count = 0;
            });
            
            // Count messages in hexes
            msgData.forEach(msg => {
                if (msg.latitude && msg.longitude) {
                    let pt = turf.point([msg.longitude, msg.latitude]);
                    for(let i=0; i<hexGrid.features.length; i++) {
                        if(turf.booleanPointInPolygon(pt, hexGrid.features[i])) {
                            hexGrid.features[i].properties.count++;
                            break;
                        }
                    }
                }
            });
            
            // Draw on map
            hexLayerGroup = L.geoJSON(hexGrid, {
                style: function(feature) {
                    let count = feature.properties.count;
                    let fillColor = count > 5 ? '#ef4444' : (count > 2 ? '#f59e0b' : '#3b82f6');
                    let fillOpacity = count > 0 ? 0.6 : 0.1;
                    return {
                        color: "#ffffff",
                        weight: 1,
                        fillColor: fillColor,
                        fillOpacity: fillOpacity
                    };
                },
                onEachFeature: function(feature, layer) {
                    let popupContent = `<div class='text-black font-sans'><b>Zone: ${feature.properties.name}</b><br/>Reports: ${feature.properties.count}</div>`;
                    layer.bindPopup(popupContent);
                }
            }).addTo(mapObj);
        }
"""

if "drawLucknowHexGrid" not in html:
    html = html.replace('        function applyLucknowGridAndLock(mapObj, lock) {', hex_logic + '\n        function applyLucknowGridAndLock(mapObj, lock) {')

# 4. Call drawLucknowHexGrid inside fetchData after filters
fetch_call = "if (window.priorityMap) drawLucknowHexGrid(window.priorityMap, data);"
if fetch_call not in html:
    html = html.replace(
        "const headerCount = document.getElementById('total-reports');",
        fetch_call + "\n                    const headerCount = document.getElementById('total-reports');"
    )

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
