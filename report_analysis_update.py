import re

def update_ui():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Update HTML structure of the card
    old_card_html = """<h3 class="serif text-xl text-white">Predictive Hotspot Analysis</h3>
</div>
<div class="p-6 text-zinc-400 text-sm"><span id="pred-text">Awaiting data for predictive generation...</span><div class="mt-8 grid grid-cols-3 gap-6">
<div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
<div class="text-xs text-zinc-500">Predicted Volume</div>
<div class="text-2xl text-white serif mt-1" id="pred-vol">0</div>
</div>
<div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
<div class="text-xs text-zinc-500">Resource Readiness</div>
<div class="text-2xl text-red-400 serif mt-1" id="pred-readiness">--</div>
</div>
<div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
<div class="text-xs text-zinc-500">Estimated SLA breach</div>
<div class="text-2xl text-white serif mt-1" id="pred-sla">0%</div>
</div>
</div>
</div>"""

    new_card_html = """<h3 class="serif text-xl text-white">Reports Analysis</h3>
</div>
<div class="p-6 text-zinc-400 text-sm">
    <div class="mb-3 text-zinc-300 font-medium">Categorized by Medium</div>
    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text Only</div>
            <div class="text-xl text-white serif mt-1" id="type-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text+Photo</div>
            <div class="text-xl text-white serif mt-1" id="type-photo-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice+Text</div>
            <div class="text-xl text-white serif mt-1" id="type-voice-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice Only</div>
            <div class="text-xl text-white serif mt-1" id="type-voice">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">All Media</div>
            <div class="text-xl text-white serif mt-1" id="type-all">0</div>
        </div>
    </div>
    
    <div class="mb-3 text-zinc-300 font-medium">Data Tracking & Integrity</div>
    <div class="grid grid-cols-2 gap-4">
        <div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A] flex justify-between items-center">
            <span class="text-zinc-400">Location Tracked</span>
            <span class="text-xl text-green-400 font-medium serif" id="track-loc">0%</span>
        </div>
        <div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A] flex justify-between items-center">
            <span class="text-zinc-400">Photo Attached</span>
            <span class="text-xl text-blue-400 font-medium serif" id="track-photo">0%</span>
        </div>
    </div>
</div>"""

    html = html.replace(old_card_html, new_card_html)

    # 2. Update JS logic
    old_js = """                    // -- DYNAMIC PREDICTIVE HOTSPOT --
                    const predText = document.getElementById('pred-text');
                    const predVol = document.getElementById('pred-vol');
                    const predReadiness = document.getElementById('pred-readiness');
                    const predSla = document.getElementById('pred-sla');
                    
                    if (predText && total > 0) {
                        let topCat = 'General Issues';
                        let max = 0;
                        if(counts.water > max) { max = counts.water; topCat = 'Waterlogging & Leaks'; }
                        if(counts.roads > max) { max = counts.roads; topCat = 'Road Infrastructure'; }
                        if(counts.power > max) { max = counts.power; topCat = 'Power Outages'; }
                        if(counts.cleanliness > max) { max = counts.cleanliness; topCat = 'Waste Management'; }
                        
                        let prob = Math.min(95, Math.max(15, Math.round((max / total) * 100) + 20));
                        
                        predText.innerText = `AI analysis indicates a ${prob}% probability of severe escalation in ${topCat} over the next 72 hours based on recent complaint clusters.`;
                        if (predVol) predVol.innerText = Math.round(max * 1.5);
                        if (predSla) predSla.innerText = Math.round((prob / 100) * 25) + '%';
                        if (predReadiness) {
                            if (prob > 70) {
                                predReadiness.innerText = 'Low';
                                predReadiness.className = 'text-2xl text-red-400 serif mt-1';
                            } else if (prob > 40) {
                                predReadiness.innerText = 'Moderate';
                                predReadiness.className = 'text-2xl text-amber-400 serif mt-1';
                            } else {
                                predReadiness.innerText = 'High';
                                predReadiness.className = 'text-2xl text-green-400 serif mt-1';
                            }
                        }
                    }"""

    new_js = """                    // -- DYNAMIC REPORTS ANALYSIS --
                    let typeText = 0, typePhotoText = 0, typeVoiceText = 0, typeVoice = 0, typeAll = 0;
                    let hasLoc = 0, hasPhoto = 0;
                    
                    data.forEach(msg => {
                        let mediaType = (msg.media_content_type || "").toLowerCase();
                        let isVoice = mediaType.includes("audio");
                        let isPhoto = mediaType.includes("image");
                        let isText = msg.body && msg.body.trim().length > 0;
                        
                        if (isVoice && isPhoto && isText) typeAll++;
                        else if (isVoice && isText) typeVoiceText++;
                        else if (isPhoto && isText) typePhotoText++;
                        else if (isVoice) typeVoice++;
                        else if (isPhoto) typePhotoText++; 
                        else typeText++;
                        
                        if (msg.latitude || msg.extracted_location) hasLoc++;
                        if (isPhoto) hasPhoto++;
                    });
                    
                    if(document.getElementById('type-text')) document.getElementById('type-text').innerText = typeText;
                    if(document.getElementById('type-photo-text')) document.getElementById('type-photo-text').innerText = typePhotoText;
                    if(document.getElementById('type-voice-text')) document.getElementById('type-voice-text').innerText = typeVoiceText;
                    if(document.getElementById('type-voice')) document.getElementById('type-voice').innerText = typeVoice;
                    if(document.getElementById('type-all')) document.getElementById('type-all').innerText = typeAll;
                    
                    if(document.getElementById('track-loc')) document.getElementById('track-loc').innerText = total > 0 ? Math.round((hasLoc/total)*100) + '%' : '0%';
                    if(document.getElementById('track-photo')) document.getElementById('track-photo').innerText = total > 0 ? Math.round((hasPhoto/total)*100) + '%' : '0%';
"""
    
    html = html.replace(old_js, new_js)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("Reports Analysis UI updated successfully!")

if __name__ == "__main__":
    update_ui()
