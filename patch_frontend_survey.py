import sys
import re

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Inject the "Launch Surveys" button
button_html = """<button onclick="document.getElementById('survey-modal').classList.remove('hidden')" class="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold tracking-wide rounded-full transition shadow-[0_0_15px_rgba(37,99,235,0.4)] flex items-center gap-1.5 mr-2">
<span class="material-symbols-outlined" style="font-size: 16px;">poll</span> Launch Surveys
</button>"""
if "Launch Surveys" not in html:
    html = html.replace(
        '<div class="flex items-center gap-5">\n<button class="w-9 h-9',
        '<div class="flex items-center gap-5">\n' + button_html + '\n<button class="w-9 h-9'
    )

# 2. Inject the Survey Modal HTML
modal_html = """
<!-- Survey Modal -->
<div id="survey-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] hidden flex items-center justify-center p-4">
    <div class="bg-[#111111] border border-[#262626] rounded-2xl p-6 w-full max-w-2xl flex flex-col gap-5 shadow-2xl relative">
        <div class="flex justify-between items-center pb-4 border-b border-[#262626]">
            <h2 class="text-xl text-white font-bold tracking-tight flex items-center gap-2"><span class="material-symbols-outlined text-blue-500">poll</span> Survey Manager</h2>
            <button onclick="document.getElementById('survey-modal').classList.add('hidden')" class="text-zinc-500 hover:text-white transition"><span class="material-symbols-outlined">close</span></button>
        </div>
        
        <div class="grid grid-cols-2 gap-6">
            <!-- Create New Survey -->
            <div class="flex flex-col gap-4 border-r border-[#262626] pr-6">
                <h3 class="text-sm font-semibold text-zinc-300">Create New Survey</h3>
                <div class="flex flex-col gap-2">
                    <label class="text-xs text-zinc-500">Question</label>
                    <input type="text" id="survey-q" class="bg-[#1C1C1C] border border-[#333] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" placeholder="e.g. Which sector needs funding?">
                </div>
                <div class="flex flex-col gap-2">
                    <label class="text-xs text-zinc-500">Options (Comma separated)</label>
                    <input type="text" id="survey-opt" class="bg-[#1C1C1C] border border-[#333] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" placeholder="Roads, Water, Electricity">
                </div>
                <button onclick="createSurvey()" class="mt-2 bg-white text-black py-2 rounded-lg text-sm font-bold hover:bg-gray-200 transition shadow-[0_0_10px_rgba(255,255,255,0.2)]">Create & Launch</button>
            </div>
            
            <!-- Existing Surveys -->
            <div class="flex flex-col gap-4">
                <div class="flex justify-between items-center">
                    <h3 class="text-sm font-semibold text-zinc-300">Active & Past Surveys</h3>
                    <button onclick="loadSurveys()" class="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"><span class="material-symbols-outlined" style="font-size:14px;">refresh</span></button>
                </div>
                <div id="survey-list" class="flex flex-col gap-3 overflow-y-auto max-h-[300px] pr-2">
                    <!-- Loaded dynamically -->
                    <div class="text-xs text-zinc-500 text-center py-4">Loading surveys...</div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
    async function loadSurveys() {
        try {
            let res = await fetch('/surveys');
            let data = await res.json();
            let html = '';
            data.forEach(s => {
                let activeBadge = s.is_active ? `<span class="px-2 py-0.5 bg-green-500/20 text-green-400 border border-green-500/50 rounded text-[10px] uppercase font-bold">Active</span>` : '';
                let actBtn = s.is_active ? '' : `<button onclick="activateSurvey(${s.id})" class="px-3 py-1 bg-[#262626] hover:bg-[#333] rounded text-xs text-white transition border border-zinc-700">Set Active</button>`;
                
                let resHtml = '';
                for(let opt in s.results) {
                    resHtml += `<div class="flex justify-between text-xs mt-1 text-zinc-400"><span>${opt}</span> <span class="font-bold text-white">${s.results[opt]}</span></div>`;
                }
                
                html += `
                <div class="p-3 bg-[#1C1C1C] rounded-lg border ${s.is_active ? 'border-green-500/30' : 'border-[#333]'} flex flex-col gap-2">
                    <div class="flex justify-between items-start">
                        <div class="text-sm font-medium text-white pr-2">${s.question}</div>
                        ${activeBadge}
                    </div>
                    <div class="flex flex-wrap gap-1 mt-1 mb-1">
                        ${s.options.map(o => `<span class="px-1.5 py-0.5 bg-[#262626] rounded text-[10px] text-zinc-400 border border-zinc-800">${o}</span>`).join('')}
                    </div>
                    <div class="bg-black/30 rounded p-2 mb-2">
                        <div class="text-[10px] text-zinc-500 font-bold uppercase mb-1">Responses</div>
                        ${resHtml || '<div class="text-[10px] text-zinc-600 italic">No responses yet</div>'}
                    </div>
                    ${actBtn}
                </div>`;
            });
            document.getElementById('survey-list').innerHTML = html;
        } catch(e) {
            console.error(e);
        }
    }
    
    async function createSurvey() {
        let q = document.getElementById('survey-q').value;
        let opt = document.getElementById('survey-opt').value;
        if(!q || !opt) return alert("Fill fields");
        await fetch('/surveys', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: q, options: opt})
        });
        document.getElementById('survey-q').value = '';
        document.getElementById('survey-opt').value = '';
        loadSurveys();
    }
    
    async function activateSurvey(id) {
        await fetch(`/surveys/${id}/activate`, {method: 'POST'});
        loadSurveys();
    }
    
    // Load surveys when modal opens
    document.querySelector('[onclick="document.getElementById(\\'survey-modal\\').classList.remove(\\'hidden\\')"]').addEventListener('click', loadSurveys);
</script>
"""
if "id=\"survey-modal\"" not in html:
    html = html.replace('</body>', modal_html + '\n</body>')

# 3. Update the Ranked Projects Logic
old_ranking = """                        let rankedProjects = Object.keys(projects).map(cat => {
                            let p = projects[cat];
                            let gapScore = Math.min(100, 40 + (p.urgency * 10) + (p.count * 2)); 
                            let impact = p.count * gapScore;
                            let severityText = p.urgency > 2 ? "critical" : (p.urgency > 0 ? "moderate" : "minor");
                            let rationale = `Ranked ${p.count > 5 ? 'High' : 'Normal'} priority due to a ${severityText} ${cat.toLowerCase()} infrastructure gap (${Math.round(gapScore)}/100) affecting ${p.count} recent grievance reports in ${p.latestLoc}.`;
                            return { sector: cat, title: p.title, count: p.count, gap: gapScore, impact: impact, loc: p.latestLoc, rationale: rationale };
                        }).sort((a,b) => b.impact - a.impact);"""

new_ranking = """                        // Mock External Data Sources
                        let activeSurveysDemand = {
                            "Roads": 15, "Water": 25, "Electricity": 5, "Parks": 10, "Sanitation": 18
                        }; // Simulated WhatsApp Survey Tally
                        
                        let mockGovtGrievanceDB = {
                            "Roads": 30, "Water": 45, "Electricity": 20, "Parks": 5, "Sanitation": 40
                        }; // Simulated Govt Data
                        
                        let rankedProjects = Object.keys(projects).map(cat => {
                            let p = projects[cat];
                            
                            // 1. Organic WhatsApp Demand (from DB)
                            let organicBase = 40 + (p.urgency * 10) + (p.count * 2);
                            
                            // 2. Govt Grievance Data Match
                            let govtMatch = 0;
                            Object.keys(mockGovtGrievanceDB).forEach(k => { if(cat.includes(k) || p.title.includes(k)) govtMatch = mockGovtGrievanceDB[k] * 0.5; });
                            
                            // 3. Survey Data Match
                            let surveyMatch = 0;
                            Object.keys(activeSurveysDemand).forEach(k => { if(cat.includes(k) || p.title.includes(k)) surveyMatch = activeSurveysDemand[k]; });
                            
                            // Combined Gap Score Predictor
                            let gapScore = Math.min(100, organicBase + govtMatch + surveyMatch); 
                            let impact = p.count * gapScore;
                            
                            let rationale = `Ranked via multi-variable predictor: Organic WhatsApp demand (${p.count} reports) combined with cross-referenced Govt Grievance records (+${govtMatch.toFixed(0)} score) and live WhatsApp Survey voting (+${surveyMatch} score) for ${p.latestLoc}.`;
                            
                            return { sector: cat, title: p.title, count: p.count, gap: gapScore, impact: impact, loc: p.latestLoc, rationale: rationale };
                        }).sort((a,b) => b.impact - a.impact);"""

if "mockGovtGrievanceDB" not in html:
    html = html.replace(old_ranking, new_ranking)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
