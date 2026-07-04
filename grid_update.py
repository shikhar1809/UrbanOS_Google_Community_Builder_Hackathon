import re

def update_maps():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Inject the applyLucknowGridAndLock function right after map instances are declared
    grid_func = """
            // --- Lucknow Grid & Lock ---
            function applyLucknowGridAndLock(map, isDark) {
                const southWest = L.latLng(26.70, 80.80);
                const northEast = L.latLng(26.95, 81.10);
                const bounds = L.latLngBounds(southWest, northEast);
                
                map.setMaxBounds(bounds);
                map.options.minZoom = 12;
                
                const latStep = (26.95 - 26.70) / 10;
                const lngStep = (81.10 - 80.80) / 10;
                
                // Subtle glowing grid lines based on theme
                const gridColor = isDark ? 'rgba(59, 130, 246, 0.15)' : 'rgba(59, 130, 246, 0.3)';
                const gridStyle = { color: gridColor, weight: 1.5, dashArray: '4, 4', interactive: false };
                
                for(let lat = 26.70; lat <= 26.95; lat += latStep) {
                    L.polyline([[lat, 80.80], [lat, 81.10]], gridStyle).addTo(map);
                }
                for(let lng = 80.80; lng <= 81.10; lng += lngStep) {
                    L.polyline([[26.70, lng], [26.95, lng]], gridStyle).addTo(map);
                }
            }
            // ---------------------------
"""
    
    # We will inject this right before "if (!window.priorityMap) {"
    html = html.replace('if (!window.priorityMap) {', grid_func + '\n            if (!window.priorityMap) {')

    # 2. Update priorityMap initialization
    old_priority = "window.priorityMap = L.map('priority-map', { zoomControl: false, attributionControl: false }).setView([28.6139, 77.2090], 12); // New Delhi"
    new_priority = """window.priorityMap = L.map('priority-map', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.priorityMap, false);"""
    html = html.replace(old_priority, new_priority)

    # 3. Update coordMap initialization
    old_coord = "window.coordMap = L.map('coordination-map', { zoomControl: false, attributionControl: false }).setView([28.6139, 77.2090], 13);"
    new_coord = """window.coordMap = L.map('coordination-map', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.coordMap, true);"""
    html = html.replace(old_coord, new_coord)

    # 4. Update liveMap initialization
    old_live = "window.liveMap = L.map('live-heatmap', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 13);"
    new_live = """window.liveMap = L.map('live-heatmap', { zoomControl: false, attributionControl: false }).setView([26.8467, 80.9462], 12);
                applyLucknowGridAndLock(window.liveMap, true);"""
    html = html.replace(old_live, new_live)


    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("Maps locked to Lucknow and grids applied!")

if __name__ == "__main__":
    update_maps()
