import re

def update_ui():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Update HTML structure of the grid
    old_grid_html = """    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text Only</div>
            <div class="text-xl text-white serif mt-1" id="type-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text+Photo</div>
            <div class="text-xl text-white serif mt-1" id="type-photo-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice+Text</div>
            <div class="text-xl text-white serif mt-1" id="type-voice-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice Only</div>
            <div class="text-xl text-white serif mt-1" id="type-voice">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">All Media</div>
            <div class="text-xl text-white serif mt-1" id="type-all">0</div>
        </div>
    </div>"""

    new_grid_html = """    <div class="grid grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text Only</div>
            <div class="text-xl text-white serif mt-1" id="type-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Photo Only</div>
            <div class="text-xl text-white serif mt-1" id="type-photo">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Text+Photo</div>
            <div class="text-xl text-white serif mt-1" id="type-photo-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice Only</div>
            <div class="text-xl text-white serif mt-1" id="type-voice">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">Voice+Text</div>
            <div class="text-xl text-white serif mt-1" id="type-voice-text">0</div>
        </div>
        <div class="p-3 border border-[#262626] rounded-lg bg-[#0A0A0A]">
            <div class="text-[10px] text-zinc-500 uppercase tracking-wider">All Media</div>
            <div class="text-xl text-white serif mt-1" id="type-all">0</div>
        </div>
    </div>"""

    html = html.replace(old_grid_html, new_grid_html)

    # 2. Update JS logic
    old_js = """                    // -- DYNAMIC REPORTS ANALYSIS --
                    let typeText = 0, typePhotoText = 0, typeVoiceText = 0, typeVoice = 0, typeAll = 0;
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
                        else if (isPhoto) typePhotoText++; 
                        else typeText++;
                        
                        if (msg.latitude || msg.extracted_location) hasLoc++;
                        if (isPhoto) hasPhoto++;
                    });
                    
                    if(document.getElementById('type-text')) document.getElementById('type-text').innerText = typeText;
                    if(document.getElementById('type-photo-text')) document.getElementById('type-photo-text').innerText = typePhotoText;
                    if(document.getElementById('type-voice-text')) document.getElementById('type-voice-text').innerText = typeVoiceText;
                    if(document.getElementById('type-voice')) document.getElementById('type-voice').innerText = typeVoice;
                    if(document.getElementById('type-all')) document.getElementById('type-all').innerText = typeAll;"""

    new_js = """                    // -- DYNAMIC REPORTS ANALYSIS --
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
                    if(document.getElementById('type-all')) document.getElementById('type-all').innerText = typeAll;"""
    
    html = html.replace(old_js, new_js)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("Photo Only UI updated successfully!")

if __name__ == "__main__":
    update_ui()
