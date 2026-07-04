import re
from bs4 import BeautifulSoup

def add_raw_feed():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Update the Layout to split the bottom area
    view_live = soup.find('div', id='view-live')
    if view_live:
        # Find the heatmap card
        map_card = view_live.find('div', class_=re.compile("min-h-\\[400px\\]"))
        if map_card:
            # Wrap the map_card in a flex container
            wrapper = soup.new_tag('div', **{'class': 'flex flex-1 gap-5 overflow-hidden'})
            map_card.wrap(wrapper)
            
            # The map card should now take remaining space
            classes = map_card.get('class', [])
            if 'flex-1' not in classes:
                classes.append('flex-1')
            map_card['class'] = classes

            # Create the raw feed panel
            raw_feed_html = """
            <aside class="w-[400px] flex flex-col gap-0 card overflow-hidden bg-[#161616] border-[#262626]">
                <div class="p-4 border-b border-[#262626] flex items-center justify-between bg-[#111]">
                    <span class="text-sm font-medium text-white flex items-center gap-2">
                        <span class="pulse-dot"></span> Live Webhook Payload
                    </span>
                    <span class="text-[10px] text-zinc-500 font-mono">application/json</span>
                </div>
                <div id="raw-feed-container" class="flex-1 overflow-y-auto p-4 flex flex-col gap-3 font-mono text-[11px] text-zinc-400 leading-relaxed">
                    <div class="text-center text-zinc-600 my-10 italic">Listening for incoming webhooks...</div>
                </div>
            </aside>
            """
            
            wrapper.append(BeautifulSoup(raw_feed_html, 'html.parser'))

    # 2. Update JavaScript to append to the raw feed container
    script = soup.find_all('script')[-1]
    script_str = script.string
    
    # Locate where the animation packet is spawned
    target = "setTimeout(() => packet.remove(), 1500);"
    replacement = """setTimeout(() => packet.remove(), 1500);
                                
                                // Append raw feed
                                const rawContainer = document.getElementById('raw-feed-container');
                                if(rawContainer) {
                                    // Remove the placeholder if it exists
                                    if(rawContainer.innerHTML.includes('Listening for')) {
                                        rawContainer.innerHTML = '';
                                    }
                                    const rawMsg = data.messages[lastMessageCount + i];
                                    const div = document.createElement('div');
                                    div.className = 'p-3 bg-[#0A0A0A] border border-[#262626] rounded-md shadow-sm break-words';
                                    
                                    // Format JSON
                                    let formattedStr = JSON.stringify(rawMsg, null, 2);
                                    formattedStr = formattedStr.replace(/\\n/g, '<br>').replace(/ /g, '&nbsp;');
                                    
                                    div.innerHTML = `<div class="text-green-400 mb-2">▼ POST /whatsapp</div>${formattedStr}`;
                                    rawContainer.prepend(div);
                                }"""
    
    script_str = script_str.replace(target, replacement)
    script.string = script_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Added raw feed panel next to the heatmap.")

if __name__ == '__main__':
    add_raw_feed()
