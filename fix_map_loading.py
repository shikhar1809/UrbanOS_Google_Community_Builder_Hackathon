import re
from bs4 import BeautifulSoup

def fix_maps():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Remove CSS filter
    style_tag = soup.find('style')
    if style_tag and style_tag.string:
        style_str = style_tag.string
        # Remove the filter line
        style_str = re.sub(r'\.leaflet-tile-pane\s*\{[^\}]+\}', '', style_str)
        style_tag.string = style_str

    # 2. Update switchView function to trigger invalidateSize
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'function switchView(viewId)' in script.string:
            s_str = script.string
            
            # Inject invalidateSize logic inside switchView
            target = "setTimeout(() => { window.dispatchEvent(new Event('resize')); }, 100);"
            injection = target + """
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
            """
            s_str = s_str.replace(target, injection)
            
            # 3. Change all map declarations to window globals
            s_str = s_str.replace("const map1 = L.map", "window.priorityMap = L.map")
            # map1 used below?
            s_str = s_str.replace(".addTo(map1)", ".addTo(window.priorityMap)")
            
            # coordMap is already window.coordMap
            
            s_str = s_str.replace("const map3 = L.map", "window.liveMap = L.map")
            # map3 used below?
            s_str = s_str.replace(".addTo(map3)", ".addTo(window.liveMap)")
            
            # 4. Change Tile URLs
            s_str = s_str.replace("'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'", "'https://tile.openstreetmap.org/{z}/{x}/{y}.png'")

            script.string = s_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
        
    print("Fixed map loading and tiles!")

if __name__ == '__main__':
    fix_maps()
