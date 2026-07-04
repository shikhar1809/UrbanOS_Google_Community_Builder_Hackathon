import sys
import re

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Inject Turf.js
if "turf.min.js" not in html:
    html = html.replace(
        '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
        '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>\n<script src="https://unpkg.com/@turf/turf@6/turf.min.js"></script>'
    )

# 2. Extract Map Card and Table Card
map_card_start = html.find('<!-- Map Card -->')
table_card_start = html.find('<!-- Table Card -->')
table_card_end = html.find('</div>\n</div>\n</div>\n<div class="view-content w-full h-full gap-5" id="view-analysis">')

if map_card_start != -1 and table_card_start != -1:
    map_card = html[map_card_start:table_card_start]
    table_card = html[table_card_start:table_card_end]
    
    # Adjust classes for row layout
    map_card_new = map_card.replace('flex-1 relative overflow-hidden min-h-[300px] h-[55%]', 'flex-1 relative overflow-hidden min-h-[300px] h-full')
    table_card_new = table_card.replace('flex-1 flex flex-col', 'flex-[1.5] flex flex-col')
    
    # Reconstruct the section: Left (stats already there before map_card), Center (Table), Right (Map)
    old_right_column = html[html.find('<!-- Right Column -->'):table_card_end]
    
    new_right_column = f"""<!-- Center and Right Columns -->
<div class="flex-1 flex flex-row gap-5 overflow-hidden">
{table_card_new}
{map_card_new}
"""
    
    html = html.replace(old_right_column, new_right_column)
else:
    print("Could not find Map Card or Table Card")

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)
