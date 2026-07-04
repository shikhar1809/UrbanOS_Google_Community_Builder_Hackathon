import re
from bs4 import BeautifulSoup

def restore():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Update Styles
    style = soup.find('style')
    style.append("""
        .view-content { display: none !important; }
        .view-content.active { display: flex !important; }
        .nav-link { cursor: pointer; border-bottom: 2px solid transparent; padding-bottom: 4px; }
        .nav-link.active { color: white !important; border-bottom-color: white !important; font-weight: 500; }
    """)

    # 2. Update Nav
    nav = soup.find('nav')
    if nav:
        nav.clear()
        links = ['Analysis', 'Priority', 'Survey', 'Coordination']
        for link in links:
            a = soup.new_tag('a')
            a['href'] = "javascript:void(0)"
            a['onclick'] = f"switchView('{link.lower()}')"
            a['id'] = f"nav-{link.lower()}"
            base_cls = "text-sm text-zinc-500 hover:text-white transition nav-link"
            if link == 'Priority':
                a['class'] = base_cls + " active"
            else:
                a['class'] = base_cls
            a.string = link
            nav.append(a)

    # 3. Update Main Content (Wrap Priority)
    main = soup.find('main')
    
    priority_div = soup.new_tag('div', id='view-priority', **{'class': 'view-content active w-full h-full gap-5'})
    for child in list(main.children):
        priority_div.append(child.extract())
    
    main.append(priority_div)

    # 4. Create Analysis View
    analysis_html = """
    <div id="view-analysis" class="view-content w-full h-full gap-5">
        <aside class="w-[340px] flex flex-col gap-5 overflow-y-auto pr-1">
            <div class="card p-6 flex flex-col gap-5">
                <h3 class="text-lg text-white font-medium">Monthly Grievance Volume</h3>
                <div class="text-4xl text-white font-medium tracking-tight serif">12,450</div>
                <div class="text-xs text-green-500">+14% vs last month</div>
                <div class="sparkline w-full mt-4"></div>
            </div>
            <div class="card p-6 flex flex-col gap-5">
                <h3 class="text-lg text-white font-medium">Resolution Rates</h3>
                <div class="flex items-center justify-between text-sm">
                    <div class="w-24 text-zinc-400">Sanitation</div>
                    <div class="flex-1 px-4"><div class="segmented-bar" id="ana-seg-1"></div></div>
                    <div class="w-8 text-right text-white">92%</div>
                </div>
                <div class="flex items-center justify-between text-sm">
                    <div class="w-24 text-zinc-400">Roads</div>
                    <div class="flex-1 px-4"><div class="segmented-bar" id="ana-seg-2"></div></div>
                    <div class="w-8 text-right text-white">78%</div>
                </div>
                <div class="flex items-center justify-between text-sm">
                    <div class="w-24 text-zinc-400">Medical</div>
                    <div class="flex-1 px-4"><div class="segmented-bar" id="ana-seg-3"></div></div>
                    <div class="w-8 text-right text-white">95%</div>
                </div>
            </div>
        </aside>
        <div class="card flex-1 flex flex-col overflow-hidden bg-[#161616]">
            <div class="p-6 border-b border-[#262626]">
                <h3 class="serif text-xl text-white">Predictive Hotspot Analysis</h3>
            </div>
            <div class="p-6 text-zinc-400 text-sm">
                AI analysis indicates a 40% probability of severe waterlogging in South Zone over the next 72 hours based on recent complaint clusters and weather forecasts.
                <div class="mt-8 grid grid-cols-3 gap-6">
                    <div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
                        <div class="text-xs text-zinc-500">Predicted Volume</div>
                        <div class="text-2xl text-white serif mt-1">850</div>
                    </div>
                    <div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
                        <div class="text-xs text-zinc-500">Resource Readiness</div>
                        <div class="text-2xl text-red-400 serif mt-1">Low</div>
                    </div>
                    <div class="p-4 border border-[#262626] rounded-xl bg-[#0A0A0A]">
                        <div class="text-xs text-zinc-500">Estimated SLA breach</div>
                        <div class="text-2xl text-white serif mt-1">12%</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    # 5. Create Survey View
    survey_html = """
    <div id="view-survey" class="view-content w-full h-full gap-5">
        <aside class="w-[340px] flex flex-col gap-5 overflow-y-auto pr-1">
            <div class="card p-6 flex flex-col gap-5">
                <h3 class="text-lg text-white font-medium">Citizen Satisfaction</h3>
                <div class="text-5xl text-white font-medium tracking-tight serif text-center my-4">4.2<span class="text-xl text-zinc-500">/5</span></div>
                <div class="flex justify-center gap-1 text-amber-400 mb-2">
                    <span class="material-symbols-outlined">star</span>
                    <span class="material-symbols-outlined">star</span>
                    <span class="material-symbols-outlined">star</span>
                    <span class="material-symbols-outlined">star</span>
                    <span class="material-symbols-outlined text-zinc-600">star</span>
                </div>
                <div class="text-center text-xs text-zinc-500">Based on 4,192 responses</div>
            </div>
        </aside>
        <div class="card flex-1 flex flex-col overflow-hidden bg-[#161616]">
            <div class="p-6 border-b border-[#262626]">
                <h3 class="serif text-xl text-white">Recent Feedback</h3>
            </div>
            <div class="flex-1 overflow-auto">
                <table class="w-full text-left text-sm whitespace-nowrap">
                    <thead class="text-xs text-zinc-500 bg-[#161616] sticky top-0 border-b border-[#262626]">
                        <tr>
                            <th class="font-medium px-6 py-4">Citizen</th>
                            <th class="font-medium px-4 py-4">Category</th>
                            <th class="font-medium px-4 py-4">Feedback</th>
                            <th class="font-medium px-6 py-4 text-right">Rating</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-[#262626]">
                        <tr class="hover:bg-[#1C1C1C] transition">
                            <td class="px-6 py-4 text-white">Rahul K.</td>
                            <td class="px-4 py-4 text-zinc-400">Sanitation</td>
                            <td class="px-4 py-4 text-zinc-300">Resolved quickly, but area was left messy.</td>
                            <td class="px-6 py-4 text-right text-amber-400">⭐⭐⭐</td>
                        </tr>
                        <tr class="hover:bg-[#1C1C1C] transition">
                            <td class="px-6 py-4 text-white">Priya S.</td>
                            <td class="px-4 py-4 text-zinc-400">Medical</td>
                            <td class="px-4 py-4 text-zinc-300">Excellent emergency response time!</td>
                            <td class="px-6 py-4 text-right text-amber-400">⭐⭐⭐⭐⭐</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    """

    # 6. Create Coordination View
    coordination_html = """
    <div id="view-coordination" class="view-content w-full h-full gap-5">
        <div class="card map-bg flex-1 relative overflow-hidden min-h-[300px] border-none shadow-inner">
            <div class="absolute inset-0 opacity-40 bg-[url('https://www.transparenttextures.com/patterns/cartographer.png')] mix-blend-multiply"></div>
            <div class="absolute top-5 left-5 bg-white text-black px-4 py-2.5 rounded-full text-sm font-medium shadow-[0_8px_30px_rgb(0,0,0,0.12)]">Active Field Units</div>
            <!-- Dispatch Pins -->
            <div class="map-pin" style="top: 40%; left: 30%; background: #4f46e5;"><span class="material-symbols-outlined text-[14px]">local_shipping</span></div>
            <div class="map-pin" style="top: 60%; left: 70%; background: #059669;"><span class="material-symbols-outlined text-[14px]">local_hospital</span></div>
        </div>
        <aside class="w-[340px] flex flex-col gap-5 overflow-y-auto pr-1">
            <div class="card p-6 flex flex-col gap-4">
                <h3 class="text-lg text-white font-medium">Active Dispatches</h3>
                <div class="p-4 bg-[#0A0A0A] border border-[#262626] rounded-xl">
                    <div class="flex justify-between items-start mb-2">
                        <div class="font-medium text-white">Unit Alpha-1</div>
                        <div class="text-xs bg-[#EDE8DC] text-black px-2 py-0.5 rounded-full">En Route</div>
                    </div>
                    <div class="text-xs text-zinc-500 mb-2">Sanitation Dept</div>
                    <div class="text-xs text-zinc-400">Heading to Market Square for priority clean-up. ETA 12 mins.</div>
                </div>
                <div class="p-4 bg-[#0A0A0A] border border-[#262626] rounded-xl">
                    <div class="flex justify-between items-start mb-2">
                        <div class="font-medium text-white">Unit Med-4</div>
                        <div class="text-xs border border-green-500 text-green-500 px-2 py-0.5 rounded-full">On Site</div>
                    </div>
                    <div class="text-xs text-zinc-500 mb-2">Health Dept</div>
                    <div class="text-xs text-zinc-400">Attending medical camp at South Zone.</div>
                </div>
            </div>
        </aside>
    </div>
    """

    main.append(BeautifulSoup(analysis_html, 'html.parser'))
    main.append(BeautifulSoup(survey_html, 'html.parser'))
    main.append(BeautifulSoup(coordination_html, 'html.parser'))

    # 7. Add JS for view switching
    script = soup.find_all('script')[-1]
    
    switch_logic = """
        function switchView(viewId) {
            document.querySelectorAll('.view-content').forEach(el => {
                el.classList.remove('active');
            });
            document.getElementById('view-' + viewId).classList.add('active');
            
            document.querySelectorAll('.nav-link').forEach(el => {
                el.classList.remove('active');
                el.classList.remove('text-white');
                el.classList.add('text-zinc-500');
            });
            
            const activeLink = document.getElementById('nav-' + viewId);
            if(activeLink) {
                activeLink.classList.add('active');
                activeLink.classList.add('text-white');
                activeLink.classList.remove('text-zinc-500');
            }
        }

        // Initialize Analysis segments
        setTimeout(() => {
            if(typeof drawSegments === 'function') {
                drawSegments('ana-seg-1', 92);
                drawSegments('ana-seg-2', 78);
                drawSegments('ana-seg-3', 95);
            }
        }, 500);
    """
    script.string = switch_logic + script.string

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Successfully restored SPA pages.")

if __name__ == '__main__':
    restore()
