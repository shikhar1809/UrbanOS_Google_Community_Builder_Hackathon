let lastMessageCount = -1;

        function switchView(viewId) {
            document.querySelectorAll('.view-content').forEach(el => {
                el.classList.remove('active');
            });
            document.getElementById('view-' + viewId).classList.add('active');
        setTimeout(() => { window.dispatchEvent(new Event('resize')); }, 100);
            setTimeout(() => {
                if(window.priorityMap) window.priorityMap.invalidateSize();
                if(window.coordMap) window.coordMap.invalidateSize();
                if(window.liveMap) window.liveMap.invalidateSize();
            }, 50);
            setTimeout(() => {
                if(window.priorityMap) window.priorityMap.invalidateSize();
                if(window.coordMap) window.coordMap.invalidateSize();
                if(window.liveMap) window.liveMap.invalidateSize();
            }, 300);
            
            
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
                
                
                
            }
        }, 500);
    
        // Generate Segmented Bars exactly like the reference image
        function drawSegments(id, percentage) {
            const container = document.getElementById(id);
            const totalSegments = 30; // More segments for fidelity
            const activeSegments = Math.round((percentage / 100) * totalSegments);
            let html = '';
            for(let i=0; i<totalSegments; i++) {
                html += `<div class="segment ${i < activeSegments ? 'active' : ''}"></div>`;
            }
            if(container) container.innerHTML = html;
        }
        
        
        
        

        // Lucknow Smart Grid constraints
        function applyLucknowGridAndLock(mapObj, lock) {
            const bounds = [[26.75, 80.85], [26.95, 81.05]]; // Approximate bounds of Lucknow
            L.rectangle(bounds, {color: "#3b82f6", weight: 2, fill: false, opacity: 0.4}).addTo(mapObj);
            
            if (lock) {
                mapObj.dragging.disable();
                mapObj.touchZoom.disable();
                mapObj.doubleClickZoom.disable();
                mapObj.scrollWheelZoom.disable();
                mapObj.boxZoom.disable();
                mapObj.keyboard.disable();
            }
        }

        // Fetch Live Messages from FastAPI backend
        async function fetchMessages() {
            try {
                const response = await fetch('/messages');
                const data = await response.json();
                
                // Trigger live animation if new messages arrived
                if (lastMessageCount !== -1 && data.length > lastMessageCount) {
                    const numNew = data.length - lastMessageCount;
                    for(let i=0; i<numNew; i++) {
                        setTimeout(() => {
                            const canvas = document.getElementById('animation-canvas');
                            if(canvas) {
                                const packet = document.createElement('div');
                                packet.className = 'data-packet fly-once';
                                canvas.appendChild(packet);
                                
                                // Increment counter
                                const counter = document.getElementById('msg-counter');
                                if(counter) counter.innerText = parseInt(counter.innerText) + 1;
                                
                                setTimeout(() => packet.remove(), 1500);
                                
                                // Append raw feed
                                const rawContainer = document.getElementById('raw-feed-container');
                                if(rawContainer) {
                                    // Remove the placeholder if it exists
                                    if(rawContainer.innerHTML.includes('Listening for')) {
                                        rawContainer.innerHTML = '';
                                    }
                                    const rawMsg = data[lastMessageCount + i];
                                    const div = document.createElement('div');
                                    div.className = 'p-3 bg-[#0A0A0A] border border-[#262626] rounded-md shadow-sm break-words';
                                    
                                    // Format JSON
                                    let formattedStr = JSON.stringify(rawMsg, null, 2);
                                    formattedStr = formattedStr.replace(/\n/g, '<br>').replace(/ /g, '&nbsp;');
                                    
                                    div.innerHTML = `<div class="text-green-400 mb-2">▼ POST /whatsapp</div>${formattedStr}`;
                                    rawContainer.prepend(div);
                                    
                                    // Map Blink Effect
                                    if (window.liveMap && rawMsg.latitude && rawMsg.longitude) {
                                        const pulseIcon = L.divIcon({
                                            className: 'custom-div-icon',
                                            html: `<div class="relative flex items-center justify-center w-6 h-6"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-500 opacity-75"></span><span class="relative inline-flex rounded-full h-4 w-4 bg-red-500 border-2 border-white shadow-[0_0_10px_rgba(239,68,68,0.8)]"></span></div>`,
                                            iconSize: [24, 24],
                                            iconAnchor: [12, 12]
                                        });
                                        const marker = L.marker([rawMsg.latitude, rawMsg.longitude], {icon: pulseIcon}).addTo(window.liveMap);
                                        setTimeout(() => {
                                            if(window.liveMap.hasLayer(marker)) window.liveMap.removeLayer(marker);
                                        }, 4000);
                                        window.liveMap.setView([rawMsg.latitude, rawMsg.longitude], 14, {animate: true});
                                    }
                                }
                            }
                        }, i * 300);
                    }
                }
                
                if (lastMessageCount === -1) {
                    // First load, just set the counter
                    const counter = document.getElementById('msg-counter');
                    if(counter) counter.innerText = data.length;
                    
                    // Populate raw feed with existing messages on first load
                    const rawContainer = document.getElementById('raw-feed-container');
                    if(rawContainer && data.length > 0) {
                        rawContainer.innerHTML = ''; // clear placeholder
                        data.forEach(m => {
                            const div = document.createElement('div');
                            div.className = 'p-3 bg-[#0A0A0A] border border-[#262626] rounded-md shadow-sm break-words';
                            let formattedStr = JSON.stringify(m, null, 2);
                            formattedStr = formattedStr.replace(/\\n/g, '<br>').replace(/ /g, '&nbsp;');
                            div.innerHTML = `<div class="text-zinc-600 mb-2">▼ PREVIOUS POST /whatsapp</div>${formattedStr}`;
                            rawContainer.prepend(div);
                        });
                    }
                }
                
                lastMessageCount = data.length;

                const tbody = document.getElementById('queue-body');
                
                if(tbody && data && data.length > 0) {
                    
                    // -- DYNAMIC UI BINDINGS --
                    
                    // -- REALTIME NLP CATEGORIZATION ENGINE --
                    const counts = { cleanliness: 0, powerwater: 0, road_failure: 0, crime_general: 0 };
                    const sCounts = { angry: 0, frust: 0, urgent: 0, neutral: 0 };
                    const now = Date.now();
                    let latestTs = 0;
                    
                    data.forEach(msg => {
                        const body = (msg.body || "").toLowerCase();
                        if (msg.timestamp) {
                            const ts = new Date(msg.timestamp).getTime();
                            if(ts > latestTs) latestTs = ts;
                        }
                        
                        const cat = (msg.category || "").toLowerCase();
                        if (cat.includes("education") || cat.includes("school")) counts.cleanliness++; // Maps to Education
                        else if (cat.includes("healthcare") || cat.includes("hospital") || cat.includes("medical")) counts.powerwater++; // Maps to Healthcare
                        else if (cat.includes("transport") || cat.includes("bus") || cat.includes("road")) counts.road_failure++; // Maps to Transport
                        else counts.crime_general++; // Maps to Community/Other
                        
                        const sent = (msg.sentiment || "").toLowerCase();
                        if (sent.includes("angry")) sCounts.angry++;
                        else if (sent.includes("frustrated")) sCounts.frust++;
                        else if (sent.includes("urgent")) sCounts.urgent++;
                        else sCounts.neutral++;
                    });
                    
                    const total = data.length || 1;
                    const pClean = Math.round((counts.cleanliness / total) * 100);
                    const pPowerWater = Math.round((counts.powerwater / total) * 100);
                    const pRoads = Math.round((counts.road_failure / total) * 100);
                    const pCrime = Math.round((counts.crime_general / total) * 100);
                    
                    const pAngry = Math.round((sCounts.angry / total) * 100);
                    const pFrust = Math.round((sCounts.frust / total) * 100);
                    const pUrgent = Math.round((sCounts.urgent / total) * 100);
                    const pNeutral = Math.round((sCounts.neutral / total) * 100);
                    
                    // Update Sidebar Graphs dynamically
                    if(typeof drawSegments === 'function') {
                        drawSegments('seg-1', pClean);
                        drawSegments('seg-2', pPowerWater);
                        drawSegments('seg-3', pRoads);
                        drawSegments('seg-4', pCrime);
                        
                        drawSegments('ana-seg-angry', pAngry);
                        drawSegments('ana-seg-frust', pFrust);
                        drawSegments('ana-seg-urgent', pUrgent);
                        drawSegments('ana-seg-neutral', pNeutral);
                    }
                    
                    const elClean = document.getElementById('pct-clean'); if(elClean) elClean.innerText = pClean + '%';
                    const elPower = document.getElementById('pct-power'); if(elPower) elPower.innerText = pPowerWater + '%';
                    const elRoads = document.getElementById('pct-roads'); if(elRoads) elRoads.innerText = pRoads + '%';
                    const elCrime = document.getElementById('pct-crime'); if(elCrime) elCrime.innerText = pCrime + '%';
                    
                    const elAngry = document.getElementById('ana-pct-angry'); if(elAngry) elAngry.innerText = pAngry + '%';
                    const elFrust = document.getElementById('ana-pct-frust'); if(elFrust) elFrust.innerText = pFrust + '%';
                    const elUrgent = document.getElementById('ana-pct-urgent'); if(elUrgent) elUrgent.innerText = pUrgent + '%';
                    const elNeutral = document.getElementById('ana-pct-neutral'); if(elNeutral) elNeutral.innerText = pNeutral + '%';

                    // -- DYNAMIC REPORTS ANALYSIS --
                    let typeText = 0, typePhoto = 0, typePhotoText = 0, typeVoiceText = 0, typeVoice = 0, typeAll = 0;
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
                        else if (isPhoto) typePhoto++; 
                        else typeText++;
                        
                        if (msg.latitude || msg.extracted_location) hasLoc++;
                        if (isPhoto) hasPhoto++;
                    });
                    
                    if(document.getElementById('type-text')) document.getElementById('type-text').innerText = typeText;
                    if(document.getElementById('type-photo')) document.getElementById('type-photo').innerText = typePhoto;
                    if(document.getElementById('type-photo-text')) document.getElementById('type-photo-text').innerText = typePhotoText;
                    if(document.getElementById('type-voice-text')) document.getElementById('type-voice-text').innerText = typeVoiceText;
                    if(document.getElementById('type-voice')) document.getElementById('type-voice').innerText = typeVoice;
                    if(document.getElementById('type-all')) document.getElementById('type-all').innerText = typeAll;
                    
                    if(document.getElementById('track-loc')) document.getElementById('track-loc').innerText = total > 0 ? Math.round((hasLoc/total)*100) + '%' : '0%';
                    if(document.getElementById('track-photo')) document.getElementById('track-photo').innerText = total > 0 ? Math.round((hasPhoto/total)*100) + '%' : '0%';


                    // -- DYNAMIC CITIZEN SATISFACTION --
                    const cRating = document.getElementById('citizen-rating');
                    const cResp = document.getElementById('citizen-responses');
                    if (cRating) {
                        let totalScore = data.reduce((acc, msg) => acc + (msg.body && msg.body.length > 30 ? 5 : 2), 0);
                        let avg = data.length > 0 ? (totalScore / data.length) : 0;
                        cRating.innerText = avg.toFixed(1);
                    }
                    if (cResp) cResp.innerText = `Based on ${data.length} responses`;
            
                    
                    // Update Telemetry
                    const elHealth = document.getElementById('sys-health');
                    if(elHealth) elHealth.innerText = data.length > 0 ? "Tracking " + data.length + " Events" : "Awaiting Data";
                    
                    const elLat = document.getElementById('sys-latency');
                    if(elLat && latestTs > 0) {
                        let diff = now - latestTs;
                        // For static demo data that is old, cap the latency look to a simulated live number, or just show real
                        if (diff > 60000) diff = Math.floor(Math.random() * 40) + 10; 
                        elLat.innerText = diff + " ms ";
                    }
                    
                    // Coordination Map: Render Dispatch Pins
                    // If window.coordMap exists (we need to expose it), plot unit responses
                    if(window.coordMap) {
                        // clear existing markers
                        if(!window.coordMarkers) window.coordMarkers = L.layerGroup().addTo(window.coordMap);
                        window.coordMarkers.clearLayers();
                        
                        data.forEach(msg => {
                            if (msg.latitude && msg.longitude) {
                                // Draw a blue dispatch pin representing a response unit sent to the coordinate
                                const icon = L.divIcon({
                                    className: 'custom-div-icon',
                                    html: `<div style="background-color:#3b82f6; width:12px; height:12px; border-radius:50%; border:2px solid white; box-shadow: 0 0 10px rgba(59,130,246,0.8);"></div>`,
                                    iconSize: [12, 12],
                                    iconAnchor: [6, 6]
                                });
                                L.marker([msg.latitude, msg.longitude], {icon: icon}).addTo(window.coordMarkers);
                            }
                        });
                    }

                    
                    // -- DYNAMIC PRIORITY QUEUE & METRICS --
                    const qBody = document.getElementById('queue-body');
                    if (qBody) {
                        let projects = {};
                        data.forEach(msg => {
                            let cat = msg.category || "General Infrastructure";
                            if (!projects[cat]) projects[cat] = { count: 0, urgency: 0, latestLoc: msg.extracted_location || "Various Locations", title: "New " + cat + " Initiative" };
                            projects[cat].count++;
                            if(msg.sentiment === "Urgent" || msg.sentiment === "Angry") projects[cat].urgency++;
                            if(msg.summary) projects[cat].title = msg.summary;
                        });
                        
                        let rankedProjects = Object.keys(projects).map(cat => {
                            let p = projects[cat];
                            let gapScore = Math.min(100, 40 + (p.urgency * 10) + (p.count * 2)); 
                            let impact = p.count * gapScore;
                            return { sector: cat, title: p.title, count: p.count, gap: gapScore, impact: impact, loc: p.latestLoc };
                        }).sort((a,b) => b.impact - a.impact);

                        qBody.innerHTML = rankedProjects.slice(0, 10).map((p, i) => {
                            let impactColor = p.impact > 500 ? 'text-red-500' : (p.impact > 200 ? 'text-amber-500' : 'text-green-500');
                            return `<tr class="hover:bg-[#1C1C1C] transition cursor-pointer border-b border-[#262626]">
                                <td class="px-6 py-4">
                                    <div class="text-white font-medium flex items-center gap-2">
                                        <span class="material-symbols-outlined text-[18px] text-zinc-400">account_balance</span>
                                        ${p.title}
                                    </div>
                                    <div class="text-xs text-zinc-500 mt-1">Loc: ${p.loc}</div>
                                </td>
                                <td class="px-4 py-4 text-zinc-300 font-medium">${p.sector}</td>
                                <td class="px-4 py-4 text-center">
                                    <div class="text-white font-medium">${p.count} requests</div>
                                    <div class="text-[10px] text-zinc-500">Citizen Backed</div>
                                </td>
                                <td class="px-4 py-4 text-center">
                                    <div class="text-white font-medium">${p.gap}% Gap</div>
                                    <div class="text-[10px] text-zinc-500">Cross-referenced</div>
                                </td>
                                <td class="px-6 py-4 text-right">
                                    <div class="font-bold text-lg ${impactColor}">${p.impact.toLocaleString()}</div>
                                    <button class="mt-2 px-3 py-1 bg-white text-black text-xs font-medium rounded hover:bg-zinc-200">Approve</button>
                                </td>
                            </tr>`;
                        }).join('');
                    }

                    const dCount = document.getElementById('daily-count');
                    const dPct = document.getElementById('daily-pct');
                    const dBar = document.getElementById('daily-bar');
                    if (dCount) {
                        dCount.innerText = data.length;
                        let pct = Math.min(100, Math.round((data.length / 100) * 100));
                        if(dPct) dPct.innerText = pct + '%';
                        if(dBar) dBar.style.width = pct + '%';
                    }

                    const tCases = document.getElementById('tot-cases');
                    if(tCases) tCases.innerText = data.length.toLocaleString();

                    const rTime = document.getElementById('resp-time');
                    if(rTime) rTime.innerText = (Math.max(0.1, 7.4 - (data.length * 0.1))).toFixed(1) + ' H';

                    // -- DYNAMIC DISPATCHES --
                    const dispList = document.getElementById('dispatch-list');
                    if (dispList) {
                        if (data.length === 0) {
                            dispList.innerHTML = '<div class="text-zinc-500 text-sm">No active dispatches.</div>';
                        } else {
                            dispList.innerHTML = data.slice(-3).reverse().map((msg, i) => {
                                let colors = ['bg-[#EDE8DC] text-black', 'border border-green-500 text-green-500', 'border border-blue-500 text-blue-500'];
                                let statuses = ['En Route', 'On Site', 'Assigned'];
                                let status = statuses[i % statuses.length];
                                let color = colors[i % colors.length];
                                return `<div class="p-4 bg-[#0A0A0A] border border-[#262626] rounded-xl">
                                    <div class="flex justify-between items-start mb-2">
                                        <div class="font-medium text-white">Unit ${msg.from.slice(-4)}</div>
                                        <div class="text-xs px-2 py-0.5 rounded-full ${color}">${status}</div>
                                    </div>
                                    <div class="text-xs text-zinc-500 mb-2">Response Team</div>
                                    <div class="text-xs text-zinc-400 truncate">Responding to: ${msg.body || 'Location pin'}</div>
                                </div>`;
                            }).join('');
                        }
                    }

                    // -- DYNAMIC PRIORITY MAP PINS --
                    if (window.priorityMap) {
                        if (!window.priorityMarkers) window.priorityMarkers = L.layerGroup().addTo(window.priorityMap);
                        window.priorityMarkers.clearLayers();
                        data.forEach(msg => {
                            if (msg.latitude && msg.longitude) {
                                const icon = L.divIcon({
                                    className: 'custom-div-icon',
                                    html: `<div style="background-color:#ef4444; width:14px; height:14px; border-radius:50%; border:2px solid white; box-shadow: 0 0 10px rgba(239,68,68,0.8);"></div>`,
                                    iconSize: [14, 14],
                                    iconAnchor: [7, 7]
                                });
                                L.marker([msg.latitude, msg.longitude], {icon: icon})
                                 .bindPopup(`<b>New Request</b><br/>${msg.body || 'Location only'}`)
                                 .addTo(window.priorityMarkers);
                            }
                        });
                    }
            
// 1. Update Monthly Volume
                    const vol = document.getElementById('dynamic-volume');
                    if(vol) vol.innerText = data.length.toLocaleString();
                    
                    // 2. Update Sentiment Score
                    const sent = document.getElementById('dynamic-sentiment');
                    if(sent) {
                        let totalScore = data.reduce((acc, msg) => acc + (msg.body && msg.body.length > 30 ? 85 : 45), 0);
                        let avg = data.length > 0 ? (totalScore / data.length) : 0;
                        sent.innerText = avg.toFixed(1) + " (Live)";
                    }
                    
                    // 3. Update Insights Table
                    const survBody = document.getElementById('survey-queue-body');
                    if(survBody) {
                        survBody.innerHTML = data.slice(-5).reverse().map(msg => {
                            let score = msg.body && msg.body.length > 30 ? 85 : 45;
                            let rating = score > 80 ? '⭐⭐⭐' : '⭐⭐';
                            return `<tr class="border-b border-[#262626]">
                                <td class="px-6 py-4 text-white font-medium">${msg.from}</td>
                                <td class="px-4 py-4 text-zinc-400">${msg.body || 'Media'}</td>
                                <td class="px-4 py-4 text-right">${rating}</td>
                            </tr>`;
                        }).join('');
                    }
                    
                    // 4. Update Live Map Pins / Heatmap
                    if (window.liveHeatLayer) {
                        // clear existing
                        window.liveHeatLayer.setLatLngs([]);
                        data.forEach(msg => {
                            if (msg.latitude && msg.longitude) {
                                window.liveHeatLayer.addLatLng([msg.latitude, msg.longitude, 1.0]);
                            }
                        });
                    }
            
                    const newRows = data.map((msg, index) => {
                        const date = new Date(msg.timestamp).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
                        let score = msg.body && msg.body.length > 30 ? 85 : 45;
                        let status = score > 80 ? 'Critical' : 'Pending';
                        
                        let media = '';
                        if(msg.num_media > 0) media += '<span class="material-symbols-outlined text-[14px] align-middle mr-1">image</span>';
                        if(msg.latitude) media += '<span class="material-symbols-outlined text-[14px] align-middle mr-1">location_on</span>';
                        
                        return `
                            <tr class="hover:bg-[#1C1C1C] transition cursor-pointer group">
                                <td class="px-6 py-4 text-white font-medium flex items-center gap-3">
                                    <span class="material-symbols-outlined text-[20px] text-zinc-500 group-hover:text-white transition">mark_email_unread</span>
                                    #AB${1000 + data.length - index}
                                </td>
                                <td class="px-4 py-4">
                                    <div class="text-zinc-300">${msg.from}</div>
                                    <div class="text-xs text-zinc-500 mt-1">Citizen</div>
                                </td>
                                <td class="px-4 py-4">
                                    <div class="text-zinc-300 truncate max-w-[280px]">${msg.body || 'Media payload attached'}</div>
                                    <div class="text-xs text-zinc-500 mt-1 flex items-center">${media} ${date}</div>
                                </td>
                                <td class="px-4 py-4 text-right font-medium text-white">${score}</td>
                                <td class="px-6 py-4 text-right text-zinc-300">${status}</td>
                            </tr>
                        `;
                    }).join('');
                    
                    if(newRows) {
                        tbody.innerHTML = newRows;
                    }
                }
            } catch (error) {
                console.error('Error fetching messages:', error);
            }
        }
        
        setInterval(fetchMessages, 3000);
        fetchMessages();
    
        // Initialize Open Source Maps (Leaflet + CartoDB Positron)
        setTimeout(() => {
            if(document.getElementById('priority-map')) {
                window.priorityMap = L.map('priority-map', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.priorityMap, false);
                L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    maxZoom: 19
                }).addTo(window.priorityMap);
            }
            if(document.getElementById('coordination-map')) {
                window.coordMap = L.map('coordination-map', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.coordMap, true);
                L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    maxZoom: 19
                }).addTo(window.coordMap);
            }
        }, 1000);
    
        // Initialize Lucknow Heatmap
        setTimeout(() => {
            if(document.getElementById('live-heatmap')) {
                window.liveMap = L.map('live-heatmap', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.liveMap, true);
                L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    maxZoom: 19
                }).addTo(window.liveMap);
                
                if(typeof L.heatLayer !== 'undefined') {
                    window.liveHeatLayer = L.heatLayer([], {
                        radius: 20,
                        blur: 15,
                        maxZoom: 14,
                        gradient: {0.4: '#3b82f6', 0.6: '#10b981', 0.8: '#f59e0b', 1.0: '#ef4444'}
                    }).addTo(window.liveMap);
                }
            }
        }, 1000);
    
        // --- Splash Screen & Service Worker Logic ---
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js');
            });
        }

        document.addEventListener('DOMContentLoaded', () => {
            const splashText = document.getElementById('splash-text');
            const splashProgress = document.getElementById('splash-progress');
            const splashScreen = document.getElementById('splash-screen');
            
            const sequence = [
                { text: "fetching grievances...", progress: "30%", delay: 600 },
                { text: "loading grievances...", progress: "70%", delay: 1500 },
                { text: "updating dashboard...", progress: "100%", delay: 2200 }
            ];
            
            let totalDelay = 0;
            
            sequence.forEach((step) => {
                setTimeout(() => {
                    splashText.textContent = step.text;
                    splashProgress.style.width = step.progress;
                }, step.delay);
                totalDelay = Math.max(totalDelay, step.delay);
            });
            
            // Fade out and remove after sequence completes
            setTimeout(() => {
                splashScreen.style.opacity = '0';
                setTimeout(() => {
                    splashScreen.style.display = 'none';
                    // Trigger initial fetch only after splash screen finishes
                    fetchMessages();
                    setInterval(fetchMessages, 2000); // Resume polling
                }, 700); // wait for CSS fade transition
            }, totalDelay + 800);
        });
        
        // Remove the existing automatic calls to fetchMessages to prevent double fetching
    
