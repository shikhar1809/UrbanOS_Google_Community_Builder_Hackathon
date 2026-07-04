import re
from bs4 import BeautifulSoup

def make_live():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Update CSS
    style = soup.find('style')
    style_str = style.string
    # Replace the infinite loop with a one-off animation
    style_str = style_str.replace("animation: flyPacket 2s infinite ease-in-out;", "")
    style_str = style_str.replace(".packet-delay-1 { animation-delay: 0.6s; }", "")
    style_str = style_str.replace(".packet-delay-2 { animation-delay: 1.2s; }", "")
    
    style_str += """
        .fly-once {
            animation: flyPacket 1.5s cubic-bezier(0.4, 0, 0.2, 1) forwards;
        }
    """
    style.string = style_str

    # 2. Remove hardcoded packets and give the container an ID
    view_live = soup.find('div', id='view-live')
    if view_live:
        # Give the top card an ID so we can append to it
        top_card = view_live.find('div', class_=re.compile("card p-8 h-40"))
        if top_card:
            top_card['id'] = 'animation-canvas'
            
        # Remove the dummy infinite packets
        for packet in view_live.find_all('div', class_=re.compile('data-packet')):
            packet.decompose()
            
        # Let's change "Webhook Status" text to "Messages Processed" so we can increment it
        webhook_div = view_live.find(string="Webhook Status")
        if webhook_div:
            # The parent of the parent has the value
            val_div = webhook_div.parent.find_next_sibling('div')
            if val_div:
                val_div['id'] = 'msg-counter'
                val_div.string = '0'
                webhook_div.replace_with("Messages Processed")

    # 3. Update JavaScript fetchMessages logic
    script = soup.find_all('script')[-1]
    script_str = script.string
    
    # We need to add a global variable for tracking message count
    if 'let lastMessageCount = 0;' not in script_str:
        script_str = "let lastMessageCount = -1;\n" + script_str
        
    # Replace the inside of fetchMessages() to trigger animation
    old_fetch = """        const res = await fetch('/messages');
        const data = await res.json();
        
        const tbody = document.getElementById('queue-body');"""
        
    new_fetch = """        const res = await fetch('/messages');
        const data = await res.json();
        
        // Trigger live animation if new messages arrived
        if (lastMessageCount !== -1 && data.messages.length > lastMessageCount) {
            const numNew = data.messages.length - lastMessageCount;
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
                    }
                }, i * 300);
            }
        }
        
        if (lastMessageCount === -1) {
            // First load, just set the counter
            const counter = document.getElementById('msg-counter');
            if(counter) counter.innerText = data.messages.length;
        }
        
        lastMessageCount = data.messages.length;
        
        const tbody = document.getElementById('queue-body');"""
        
    script_str = script_str.replace(old_fetch, new_fetch)
    script.string = script_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Made Live Feed reactive to real webhook data.")

if __name__ == '__main__':
    make_live()
