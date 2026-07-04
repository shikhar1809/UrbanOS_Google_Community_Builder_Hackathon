import re
from bs4 import BeautifulSoup

def inject_leaflet():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Add Leaflet CSS and JS to Head
    head = soup.find('head')
    if not soup.find(href=re.compile("leaflet.css")):
        head.append(BeautifulSoup('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>', 'html.parser'))
        head.append(BeautifulSoup('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>', 'html.parser'))

    # Add Custom CSS to tint the map to Cream #EDE8DC
    style = soup.find('style')
    style.append("""
        .leaflet-container { background: #EDE8DC; }
        .leaflet-tile-pane { filter: sepia(50%) hue-rotate(5deg) saturate(120%) brightness(1.05) contrast(90%); opacity: 0.8; }
        /* Hide default leaflet controls to keep our custom UI clean */
        .leaflet-control-zoom { display: none !important; }
        .leaflet-control-attribution { display: none !important; }
    """)

    # 2. Modify Priority Map Container
    priority_map = soup.find('div', class_=re.compile("card map-bg"))
    if priority_map:
        # Replace the static images with a map div
        for img in priority_map.find_all('img'):
            img.decompose()
        for div in priority_map.find_all('div', class_=re.compile("bg-\\[url")):
            div.decompose()
            
        map_div = soup.new_tag('div', id='priority-map', **{'class': 'absolute inset-0 z-0'})
        priority_map.insert(0, map_div)
        
        # Ensure UI elements stay on top
        for child in priority_map.children:
            if child.name == 'div' and child.get('id') != 'priority-map':
                cls = child.get('class', [])
                if 'z-10' not in cls:
                    child['class'] = cls + ['z-10']

    # 3. Modify Coordination Map Container
    coord_map = soup.find('div', id='view-coordination')
    if coord_map:
        c_map = coord_map.find('div', class_=re.compile("card map-bg"))
        if c_map:
            for img in c_map.find_all('img'):
                img.decompose()
            for div in c_map.find_all('div', class_=re.compile("bg-\\[url")):
                div.decompose()
                
            c_map_div = soup.new_tag('div', id='coordination-map', **{'class': 'absolute inset-0 z-0'})
            c_map.insert(0, c_map_div)
            
            # Ensure UI elements stay on top
            for child in c_map.children:
                if child.name == 'div' and child.get('id') != 'coordination-map':
                    cls = child.get('class', [])
                    if 'z-10' not in cls:
                        child['class'] = cls + ['z-10']

    # 4. Add Leaflet Initialization Script
    script = soup.find_all('script')[-1]
    leaflet_init = """
        // Initialize Open Source Maps (Leaflet + CartoDB Positron)
        setTimeout(() => {
            if(document.getElementById('priority-map')) {
                const map1 = L.map('priority-map', { zoomControl: false, attributionControl: false }).setView([28.6139, 77.2090], 12); // New Delhi
                L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                    maxZoom: 19
                }).addTo(map1);
            }
            if(document.getElementById('coordination-map')) {
                const map2 = L.map('coordination-map', { zoomControl: false, attributionControl: false }).setView([28.6139, 77.2090], 13);
                L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                    maxZoom: 19
                }).addTo(map2);
            }
        }, 1000);
    """
    script.string = script.string + leaflet_init

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Successfully injected Open Source Maps.")

if __name__ == '__main__':
    inject_leaflet()
