import sys

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Add Chart.js to <head>
head_close_idx = html.find("</head>")
if head_close_idx != -1:
    chart_js_script = '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>\n'
    if chart_js_script not in html:
        html = html[:head_close_idx] + chart_js_script + html[head_close_idx:]

# 2. Replace view-insights DOM
view_insights_start = html.find('<div class="view-content w-full h-full gap-5" id="view-insights">')
if view_insights_start != -1:
    view_coordination_start = html.find('<div class="view-content w-full h-full gap-5" id="view-coordination">')
    
    new_view_insights = """<div class="view-content w-full h-full flex-col gap-5" id="view-insights">
<!-- KPI Bar -->
<div class="flex gap-5">
    <div class="card p-5 flex-1 flex flex-col justify-center border-l-4 border-l-blue-500">
        <div class="text-sm text-zinc-500">Annual Allocation</div>
        <div class="text-2xl text-white font-medium">₹5.00 Cr</div>
    </div>
    <div class="card p-5 flex-1 flex flex-col justify-center border-l-4 border-l-amber-500">
        <div class="text-sm text-zinc-500">Est. Funds Requested</div>
        <div class="text-2xl text-white font-medium" id="kpi-funds-requested">₹0</div>
    </div>
    <div class="card p-5 flex-1 flex flex-col justify-center border-l-4 border-l-green-500">
        <div class="text-sm text-zinc-500">Est. Unspent Balance</div>
        <div class="text-2xl text-white font-medium" id="kpi-funds-balance">₹5.00 Cr</div>
    </div>
    <div class="card p-5 flex-1 flex flex-col justify-center border-l-4 border-l-purple-500">
        <div class="text-sm text-zinc-500">Projects Sanctioned</div>
        <div class="text-2xl text-white font-medium" id="kpi-sanctioned-count">0</div>
    </div>
</div>

<!-- Main Area -->
<div class="flex flex-1 gap-5 overflow-hidden">
    <!-- Charts Sidebar -->
    <aside class="w-[400px] flex flex-col gap-5 overflow-y-auto pr-1">
        <div class="card p-6 flex flex-col gap-4">
            <h3 class="text-lg text-white font-medium">Sectoral Fund Demand</h3>
            <div class="relative w-full aspect-square flex items-center justify-center">
                <canvas id="sectorChart"></canvas>
            </div>
        </div>
        <div class="card p-6 flex flex-col gap-4">
            <h3 class="text-lg text-white font-medium">Project Lifecycle</h3>
            <div class="relative w-full h-48">
                <canvas id="lifecycleChart"></canvas>
            </div>
        </div>
    </aside>
    
    <!-- Pipeline Table -->
    <div class="card flex-1 flex flex-col overflow-hidden bg-[#161616]">
        <div class="p-6 border-b border-[#262626] flex justify-between items-center">
            <h3 class="serif text-xl text-white">High-Value Project Pipeline</h3>
            <span class="text-xs text-zinc-500">Sorted by Estimated Budget Requirement</span>
        </div>
        <div class="flex-1 overflow-auto">
            <table class="w-full text-left text-sm whitespace-nowrap">
                <thead class="text-xs text-zinc-500 bg-[#161616] sticky top-0 border-b border-[#262626] z-10">
                    <tr>
                        <th class="font-medium px-6 py-4">Proposed Project</th>
                        <th class="font-medium px-4 py-4">Sector</th>
                        <th class="font-medium px-4 py-4 text-center">Status</th>
                        <th class="font-medium px-6 py-4 text-right">Est. Budget</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-[#262626]" id="pipeline-queue-body"></tbody>
            </table>
        </div>
    </div>
</div>
</div>
"""
    html = html[:view_insights_start] + new_view_insights + html[view_coordination_start:]

# 3. Add JS for Insights Dashboard logic right after `lastMessageCount = data.length;`
target_js = "lastMessageCount = data.length;"
if target_js in html:
    insights_js = """
                // --- MPLADS INSIGHTS LOGIC ---
                let totalRequested = 0;
                let projectsSanctioned = 0;
                let sectorBudgets = { constr: 0, land: 0, infra: 0, util: 0 };
                let lifecycle = { requested: 0, review: 0, sanctioned: 0, completed: 0 };
                let pipelineProjects = [];
                
                data.forEach(msg => {
                    let cat = (msg.category || "").toLowerCase();
                    let feasi = (msg.sentiment || "").toLowerCase();
                    let prio = (msg.priority || "").toLowerCase();
                    
                    // Estimate Budget (Mock logic based on priority)
                    let estBudget = 500000; // base 5 Lakhs
                    if (prio === "critical") estBudget = 5000000;
                    else if (prio === "high") estBudget = 2500000;
                    else if (prio === "medium") estBudget = 1000000;
                    
                    totalRequested += estBudget;
                    
                    if (cat.includes("construction")) sectorBudgets.constr += estBudget;
                    else if (cat.includes("land")) sectorBudgets.land += estBudget;
                    else if (cat.includes("infrastruct") || cat.includes("upgrade")) sectorBudgets.infra += estBudget;
                    else sectorBudgets.util += estBudget;
                    
                    let status = "Requested";
                    if (feasi === "high" || msg.status === "Closed") { status = "Sanctioned"; lifecycle.sanctioned++; projectsSanctioned++; }
                    else if (feasi === "moderate") { status = "Under Review"; lifecycle.review++; }
                    else if (feasi === "complex") { status = "Feasibility Check"; lifecycle.requested++; }
                    else { lifecycle.requested++; }
                    
                    pipelineProjects.push({
                        title: msg.summary || "New " + (msg.category || "Project"),
                        sector: msg.category || "General",
                        status: status,
                        budget: estBudget,
                        loc: msg.extracted_location || "Unknown"
                    });
                });
                
                // Update KPIs
                const fmtMoney = val => '₹' + (val / 100000).toFixed(2) + ' L';
                const fmtCr = val => '₹' + (val / 10000000).toFixed(2) + ' Cr';
                
                const elFundsReq = document.getElementById('kpi-funds-requested');
                if(elFundsReq) elFundsReq.innerText = fmtCr(totalRequested);
                
                const elFundsBal = document.getElementById('kpi-funds-balance');
                if(elFundsBal) elFundsBal.innerText = fmtCr(50000000 - totalRequested);
                
                const elSanc = document.getElementById('kpi-sanctioned-count');
                if(elSanc) elSanc.innerText = projectsSanctioned;
                
                // Populate Pipeline Table
                pipelineProjects.sort((a,b) => b.budget - a.budget);
                const plBody = document.getElementById('pipeline-queue-body');
                if(plBody) {
                    plBody.innerHTML = pipelineProjects.slice(0, 15).map(p => {
                        let statColor = "text-yellow-400";
                        if (p.status === "Sanctioned") statColor = "text-green-400";
                        if (p.status === "Feasibility Check") statColor = "text-red-400";
                        
                        return `<tr>
                            <td class="px-6 py-4">
                                <div class="font-medium text-white">${p.title}</div>
                                <div class="text-xs text-zinc-500">Loc: ${p.loc}</div>
                            </td>
                            <td class="px-4 py-4 text-zinc-300">${p.sector}</td>
                            <td class="px-4 py-4 text-center ${statColor} font-medium">${p.status}</td>
                            <td class="px-6 py-4 text-right font-mono text-white">${fmtMoney(p.budget)}</td>
                        </tr>`;
                    }).join('');
                }
                
                // Update Charts
                if (window.sectorChart) {
                    window.sectorChart.data.datasets[0].data = [sectorBudgets.constr, sectorBudgets.land, sectorBudgets.infra, sectorBudgets.util];
                    window.sectorChart.update();
                }
                if (window.lifecycleChart) {
                    window.lifecycleChart.data.datasets[0].data = [lifecycle.requested, lifecycle.review, lifecycle.sanctioned, lifecycle.completed];
                    window.lifecycleChart.update();
                }
                // --- END MPLADS INSIGHTS LOGIC ---
"""
    html = html.replace(target_js, target_js + insights_js)

# 4. Chart.js Initialization
dom_loaded_idx = html.find('document.addEventListener("DOMContentLoaded", function() {')
if dom_loaded_idx != -1:
    init_charts_js = """
    // Initialize Chart.js
    const ctx1 = document.getElementById('sectorChart');
    if (ctx1) {
        window.sectorChart = new Chart(ctx1, {
            type: 'doughnut',
            data: {
                labels: ['Construction', 'Land Dev', 'Infrastructure', 'Utility'],
                datasets: [{
                    data: [0, 0, 0, 0],
                    backgroundColor: ['#3b82f6', '#f59e0b', '#10b981', '#8b5cf6'],
                    borderWidth: 0
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: {color: '#a1a1aa'} } }, cutout: '70%' }
        });
    }
    const ctx2 = document.getElementById('lifecycleChart');
    if (ctx2) {
        window.lifecycleChart = new Chart(ctx2, {
            type: 'bar',
            data: {
                labels: ['Requested', 'Review', 'Sanctioned', 'Completed'],
                datasets: [{
                    label: 'Projects',
                    data: [0, 0, 0, 0],
                    backgroundColor: '#3b82f6',
                    borderRadius: 4
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { display: false }, x: { grid: {display: false}, ticks: {color: '#a1a1aa'} } } }
        });
    }
"""
    insert_pos = dom_loaded_idx + len('document.addEventListener("DOMContentLoaded", function() {')
    html = html[:insert_pos] + init_charts_js + html[insert_pos:]

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
