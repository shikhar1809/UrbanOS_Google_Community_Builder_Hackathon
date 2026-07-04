import sys

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Replace the UI buttons
old_ui = """<div class="flex gap-3">
<button class="w-9 h-9 rounded-full border border-zinc-700 flex items-center justify-center text-zinc-400 hover:text-white hover:border-zinc-500 transition"><span class="material-symbols-outlined text-[18px]">calendar_today</span></button>
<button class="w-9 h-9 rounded-full border border-zinc-700 flex items-center justify-center text-zinc-400 hover:text-white hover:border-zinc-500 transition"><span class="material-symbols-outlined text-[18px]">filter_list</span></button>
</div>"""

new_ui = """<div class="flex gap-3 items-center">
    <select id="filter-type" class="bg-[#262626] border border-zinc-700 text-zinc-300 text-xs rounded-full px-3 py-1.5 focus:outline-none focus:border-zinc-500 cursor-pointer" onchange="fetchData()">
        <option value="all">All Sectors</option>
        <option value="construction">Construction</option>
        <option value="land">Land Dev</option>
        <option value="infra">Infrastructure</option>
        <option value="utility">Utility</option>
    </select>
    <select id="filter-medium" class="bg-[#262626] border border-zinc-700 text-zinc-300 text-xs rounded-full px-3 py-1.5 focus:outline-none focus:border-zinc-500 cursor-pointer" onchange="fetchData()">
        <option value="all">All Media</option>
        <option value="text">Text Only</option>
        <option value="image">With Photos</option>
        <option value="audio">With Voice Notes</option>
    </select>
    <select id="filter-location" class="bg-[#262626] border border-zinc-700 text-zinc-300 text-xs rounded-full px-3 py-1.5 focus:outline-none focus:border-zinc-500 cursor-pointer" onchange="fetchData()">
        <option value="all">All Locations</option>
        <option value="north">North Zone</option>
        <option value="south">South Zone</option>
        <option value="east">East Zone</option>
        <option value="west">West Zone</option>
        <option value="central">Central</option>
    </select>
</div>"""

if old_ui in html:
    html = html.replace(old_ui, new_ui)
else:
    print("Could not find old UI buttons.")

# 2. Add filtering logic in fetchData
target_js = "if(tbody && data && data.length > 0) {"
if target_js in html:
    filter_js = """if(tbody && data && data.length > 0) {
                    // --- APPLY FILTERS ---
                    const typeFilter = document.getElementById('filter-type')?.value || 'all';
                    const mediumFilter = document.getElementById('filter-medium')?.value || 'all';
                    const locFilter = document.getElementById('filter-location')?.value || 'all';
                    
                    data = data.filter(msg => {
                        // 1. Filter Type
                        let cat = (msg.category || "").toLowerCase();
                        if (typeFilter === 'construction' && !cat.includes('construction')) return false;
                        if (typeFilter === 'land' && !cat.includes('land')) return false;
                        if (typeFilter === 'infra' && !cat.includes('infrastruct') && !cat.includes('upgrade')) return false;
                        if (typeFilter === 'utility' && !cat.includes('utility')) return false;
                        
                        // 2. Filter Medium
                        let mediaType = (msg.media_content_type || "").toLowerCase();
                        let isVoice = mediaType.includes("audio");
                        let isPhoto = mediaType.includes("image");
                        if (mediumFilter === 'text' && (isVoice || isPhoto)) return false;
                        if (mediumFilter === 'image' && !isPhoto) return false;
                        if (mediumFilter === 'audio' && !isVoice) return false;
                        
                        // 3. Filter Location (Mock hashing)
                        if (locFilter !== 'all') {
                            let rawLoc = (msg.extracted_location || msg.latitude || "Unknown").toString();
                            let hash = 0;
                            for (let i = 0; i < rawLoc.length; i++) hash = Math.imul(31, hash) + rawLoc.charCodeAt(i) | 0;
                            let zones = ['north', 'south', 'east', 'west', 'central'];
                            let assignedZone = zones[Math.abs(hash) % 5];
                            if (locFilter !== assignedZone) return false;
                        }
                        
                        return true;
                    });
                    
                    // Update main count display so user knows filtering happened
                    const headerCount = document.getElementById('total-reports');
                    if (headerCount) headerCount.innerText = data.length;
"""
    if "// --- APPLY FILTERS ---" not in html:
        html = html.replace(target_js, filter_js)
else:
    print("Could not find JS target.")

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
