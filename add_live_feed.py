import re
from bs4 import BeautifulSoup

def add_live_feed():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Add Leaflet.heat script to head
    head = soup.find('head')
    if not soup.find(href=re.compile("leaflet-heat.js")):
        head.append(BeautifulSoup('<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>', 'html.parser'))

    # 2. Add custom CSS for animations
    style = soup.find('style')
    style.append("""
        @keyframes flyPacket {
            0% { transform: translateX(0) scale(1); opacity: 0; }
            10% { opacity: 1; }
            50% { transform: translateX(50vw) scale(1.2); opacity: 1; box-shadow: 0 0 20px 5px rgba(255,180,168,0.5); }
            90% { opacity: 1; }
            100% { transform: translateX(calc(100vw - 300px)) scale(1); opacity: 0; }
        }
        .data-packet {
            position: absolute;
            left: 100px;
            top: 50%;
            margin-top: -6px;
            width: 12px;
            height: 12px;
            background-color: #ffb4a8;
            border-radius: 50%;
            box-shadow: 0 0 10px 2px rgba(255,180,168,0.4);
            animation: flyPacket 2s infinite ease-in-out;
            z-index: 20;
        }
        .packet-delay-1 { animation-delay: 0.6s; }
        .packet-delay-2 { animation-delay: 1.2s; }
        
        .pulse-dot {
            width: 8px; height: 8px; background-color: #4ade80; border-radius: 50%;
            box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7);
            animation: pulse-green 2s infinite;
        }
        @keyframes pulse-green {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(74, 222, 128, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74, 222, 128, 0); }
        }
    """)

    # 3. Add to Nav
    nav = soup.find('nav')
    if nav:
        a = soup.new_tag('a')
        a['href'] = "javascript:void(0)"
        a['onclick'] = "switchView('live')"
        a['id'] = "nav-live"
        a['class'] = "text-sm text-zinc-500 hover:text-white transition nav-link"
        a.string = "Live Feed"
        nav.append(a)
        
        # update nav links array in js (the existing js is fine because it just matches lowercase text)
        # But wait, the JS matches "live feed". Let's change the JS onclick parameter to just 'live'
        # which matches the view ID "view-live"

    # 4. Create the Live Feed View HTML
    live_feed_html = """
    <div id="view-live" class="view-content w-full h-full flex-col gap-5">
        
        <!-- Top Section: Animation -->
        <div class="card p-8 h-40 relative flex items-center justify-between overflow-hidden bg-[#0A0A0A] border-[#262626]">
            <!-- Grid Background for tech feel -->
            <div class="absolute inset-0 opacity-10" style="background-image: linear-gradient(#333 1px, transparent 1px), linear-gradient(90deg, #333 1px, transparent 1px); background-size: 20px 20px;"></div>
            
            <!-- WhatsApp Node -->
            <div class="z-10 flex flex-col items-center gap-2 ml-8">
                <div class="w-16 h-16 rounded-2xl bg-[#25D366] flex items-center justify-center shadow-[0_0_20px_rgba(37,211,102,0.3)]">
                    <span class="material-symbols-outlined text-white text-[32px]">forum</span>
                </div>
                <span class="text-xs text-zinc-400 font-medium tracking-widest uppercase">WhatsApp API</span>
            </div>

            <!-- Dashed Path -->
            <div class="absolute left-32 right-32 top-1/2 border-t-2 border-dashed border-zinc-700 -mt-px z-0"></div>

            <!-- The flying packets -->
            <div class="data-packet"></div>
            <div class="data-packet packet-delay-1"></div>
            <div class="data-packet packet-delay-2"></div>

            <!-- UrbanOS Node -->
            <div class="z-10 flex flex-col items-center gap-2 mr-8">
                <div class="w-16 h-16 rounded-2xl bg-[#161616] border border-[#333] flex items-center justify-center shadow-xl">
                    <svg class="w-8 h-8 text-white" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2L2 22h20L12 2zm0 4.5l6.5 13h-13L12 6.5z"/></svg>
                </div>
                <span class="text-xs text-white font-medium tracking-widest uppercase">UrbanOS Engine</span>
            </div>
        </div>

        <!-- Middle Section: Telemetry Bar -->
        <div class="flex gap-5">
            <div class="card flex-1 p-5 flex items-center gap-4 bg-[#161616]">
                <div class="w-12 h-12 rounded-full bg-[#0A0A0A] border border-[#262626] flex items-center justify-center">
                    <div class="pulse-dot"></div>
                </div>
                <div>
                    <div class="text-xs text-zinc-500 mb-1">System Health</div>
                    <div class="text-lg text-white font-medium">Optimal <span class="text-sm text-green-400 ml-1">99.9%</span></div>
                </div>
            </div>
            
            <div class="card flex-1 p-5 flex items-center gap-4 bg-[#161616]">
                <div class="w-12 h-12 rounded-full bg-[#0A0A0A] border border-[#262626] flex items-center justify-center">
                    <span class="material-symbols-outlined text-zinc-400">speed</span>
                </div>
                <div>
                    <div class="text-xs text-zinc-500 mb-1">Ingestion Latency</div>
                    <div class="text-lg text-white font-medium">12 ms <span class="text-sm text-zinc-400 ml-1">(Low Stress)</span></div>
                </div>
            </div>

            <div class="card flex-1 p-5 flex items-center gap-4 bg-[#161616]">
                <div class="w-12 h-12 rounded-full bg-[#0A0A0A] border border-[#262626] flex items-center justify-center">
                    <span class="material-symbols-outlined text-blue-400">sync</span>
                </div>
                <div>
                    <div class="text-xs text-zinc-500 mb-1">Webhook Status</div>
                    <div class="text-lg text-white font-medium">Active Polling</div>
                </div>
            </div>
        </div>

        <!-- Bottom Section: Lucknow Heatmap -->
        <div class="card map-bg flex-1 relative overflow-hidden border-none shadow-inner min-h-[400px]">
            <div class="absolute inset-0 z-0" id="live-heatmap"></div>
            
            <div class="absolute top-5 left-5 bg-white text-black px-4 py-2.5 rounded-full text-sm font-medium shadow-[0_8px_30px_rgb(0,0,0,0.12)] z-10 flex items-center gap-2">
                <span class="material-symbols-outlined text-red-500 text-[18px]">local_fire_department</span>
                Lucknow Live Hotspots
            </div>
            
            <!-- Map controls -->
            <div class="absolute top-5 right-5 flex flex-col gap-3 z-10">
                <button class="w-11 h-11 bg-white text-black rounded-full shadow-[0_8px_30px_rgb(0,0,0,0.12)] flex items-center justify-center hover:bg-gray-50 transition">
                    <span class="material-symbols-outlined text-[20px]">fullscreen</span>
                </button>
            </div>
        </div>
    </div>
    """
    
    main = soup.find('main')
    main.append(BeautifulSoup(live_feed_html, 'html.parser'))

    # 5. Add Leaflet map initialization for the heatmap
    script = soup.find_all('script')[-1]
    heatmap_init = """
        // Initialize Lucknow Heatmap
        setTimeout(() => {
            if(document.getElementById('live-heatmap')) {
                const map3 = L.map('live-heatmap', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 13);
                L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                    maxZoom: 19
                }).addTo(map3);
                
                // Generate some dummy heat points around central Lucknow
                const heatPoints = [];
                const centerLat = 26.8467;
                const centerLng = 80.9462;
                
                // Hazratganj cluster
                for(let i=0; i<150; i++) {
                    heatPoints.push([centerLat + (Math.random() - 0.5) * 0.02, centerLng + (Math.random() - 0.5) * 0.02, Math.random()]);
                }
                // Chowk cluster
                for(let i=0; i<80; i++) {
                    heatPoints.push([centerLat + 0.02 + (Math.random() - 0.5) * 0.015, centerLng - 0.02 + (Math.random() - 0.5) * 0.015, Math.random()]);
                }
                // Gomti Nagar cluster
                for(let i=0; i<100; i++) {
                    heatPoints.push([centerLat + 0.01 + (Math.random() - 0.5) * 0.03, centerLng + 0.04 + (Math.random() - 0.5) * 0.03, Math.random()]);
                }

                if(typeof L.heatLayer !== 'undefined') {
                    L.heatLayer(heatPoints, {
                        radius: 20,
                        blur: 15,
                        maxZoom: 14,
                        gradient: {0.4: '#3b82f6', 0.6: '#10b981', 0.8: '#f59e0b', 1.0: '#ef4444'}
                    }).addTo(map3);
                }
            }
        }, 1000);
    """
    
    # Update the JS switchView to match "live feed" exactly if the textContent is used
    # The existing logic: `if(el.textContent.trim().toLowerCase() === viewId) ...`
    # Our viewId is 'live', but the text is 'Live Feed'. So we need to fix the JS to match ID instead.
    # Actually, the switchView function adds active based on textContent in index.html right now:
    # Let's fix that JS slightly in the script tag to be safer.
    script_str = script.string
    script_str = script_str.replace("if(el.textContent.trim().toLowerCase() === viewId)", "if(el.id === 'nav-' + viewId)")
    
    script.string = script_str + heatmap_init

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Successfully added Live Feed page.")

if __name__ == '__main__':
    add_live_feed()
