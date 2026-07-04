import sys

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Update loadSurveys to include the Broadcast button and JS function
old_actBtn = """let actBtn = s.is_active ? '' : `<button onclick="activateSurvey(${s.id})" class="px-3 py-1 bg-[#262626] hover:bg-[#333] rounded text-xs text-white transition border border-zinc-700">Set Active</button>`;"""

new_actBtn = """let actBtn = s.is_active ? `<button onclick="broadcastSurvey(${s.id})" class="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs text-white transition font-bold flex items-center justify-center gap-1 w-full mt-2"><span class="material-symbols-outlined" style="font-size:14px;">send</span> Broadcast to All</button>` : `<button onclick="activateSurvey(${s.id})" class="px-3 py-1 bg-[#262626] hover:bg-[#333] rounded text-xs text-white transition border border-zinc-700">Set Active</button>`;"""

if "broadcastSurvey(" not in html:
    html = html.replace(old_actBtn, new_actBtn)
    
    # 2. Add broadcastSurvey JS function
    broadcast_js = """
    async function broadcastSurvey(id) {
        if(!confirm("Are you sure you want to broadcast this survey to all constituents via WhatsApp Direct Message?")) return;
        
        try {
            let res = await fetch(`/surveys/${id}/broadcast`, {method: 'POST'});
            let data = await res.json();
            if(data.status === 'success') {
                alert(`Broadcast sent successfully to ${data.broadcast_count} citizens!`);
            } else {
                alert("Broadcast failed.");
            }
        } catch(e) {
            alert("Error: " + e.message);
        }
    }
    """
    html = html.replace("async function activateSurvey(id) {", broadcast_js + "\n    async function activateSurvey(id) {")

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
