import re
from bs4 import BeautifulSoup

def fix():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Move Live Feed to the front of the nav and make it the default active
    nav = soup.find('nav')
    if nav:
        live_link = soup.find('a', id='nav-live')
        priority_link = soup.find('a', id='nav-priority')
        
        if live_link and priority_link:
            # Extract live link and insert at 0
            live_link = live_link.extract()
            nav.insert(0, live_link)
            
            # Swap active classes
            live_link['class'] = "text-sm hover:text-white transition nav-link active text-white"
            priority_link['class'] = "text-sm text-zinc-500 hover:text-white transition nav-link"

    # 2. Swap active classes on the views
    view_live = soup.find('div', id='view-live')
    view_priority = soup.find('div', id='view-priority')
    
    if view_live and view_priority:
        # Give view-live the active class
        classes = view_live.get('class', [])
        if 'active' not in classes:
            view_live['class'] = classes + ['active']
            
        # Remove active from priority
        p_classes = view_priority.get('class', [])
        if 'active' in p_classes:
            p_classes.remove('active')
            view_priority['class'] = p_classes

    # 3. Fix Leaflet display:none bug
    # We need to trigger a window resize event when switching tabs so Leaflet maps recalculate
    # We also need to fix the script where map3 (heatmap) is created.
    
    script = soup.find_all('script')[-1]
    script_str = script.string
    
    # Add window resize trigger to switchView
    if "document.getElementById('view-' + viewId).classList.add('active');" in script_str:
        script_str = script_str.replace(
            "document.getElementById('view-' + viewId).classList.add('active');",
            "document.getElementById('view-' + viewId).classList.add('active');\n        setTimeout(() => { window.dispatchEvent(new Event('resize')); }, 100);"
        )

    # Let's also ensure map3 is scoped globally or just let the resize event handle it.
    # Leaflet listens to window resize events automatically.
    
    script.string = script_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Fixed Live Feed order and Map loading.")

if __name__ == '__main__':
    fix()
