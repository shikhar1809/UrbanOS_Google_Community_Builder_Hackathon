import re
from bs4 import BeautifulSoup

def update_index_html():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Update Analysis Page (Remove hardcoded numbers)
    # Find Monthly Grievance Volume
    volume_tag = soup.find(string=re.compile(r'12,450'))
    if volume_tag:
        parent = volume_tag.parent
        parent.string = ""
        span = soup.new_tag('span', id='dynamic-volume')
        span.string = '0'
        parent.append(span)

    # 2. Update Survey Page (Remove 4.2/5)
    survey_score = soup.find(string=re.compile(r'4\.2/5'))
    if survey_score:
        parent = survey_score.parent
        parent.string = ""
        span = soup.new_tag('span', id='dynamic-sentiment')
        span.string = 'Awaiting Data...'
        parent.append(span)
        
    # Clear fake survey table
    survey_table = soup.find('div', id='view-survey')
    if survey_table:
        tbody = survey_table.find('tbody')
        if tbody:
            tbody.clear()
            tbody['id'] = 'survey-queue-body'

    # 3. Update Coordination Page Map Pins
    coord_map = soup.find('div', id='coordination-map')
    if coord_map:
        # Remove any floating markers (custom divs) placed over the map
        for pin in coord_map.parent.find_all('div', class_=re.compile(r'absolute.*w-10.*rounded-full')):
            pin.decompose()

    # 4. Remove fake Heatmap points in Javascript & add real ones
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'heatPoints.push' in script.string:
            script_str = script.string
            
            # Remove the fake clusters
            script_str = re.sub(r'// Generate some dummy heat points.*?(?=if\(typeof L\.heatLayer !== \'undefined\')', '', script_str, flags=re.DOTALL)
            
            # Change heatLayer initialization
            script_str = script_str.replace('L.heatLayer(heatPoints, {', 'window.liveHeatLayer = L.heatLayer([], {')
            
            script.string = script_str

    # 5. Update fetchMessages to handle new real data integration
    for script in scripts:
        if script.string and 'async function fetchMessages' in script.string:
            script_str = script.string
            
            # Inject dynamic updates inside the if(tbody && data && data.length > 0) block
            target = "if(tbody && data && data.length > 0) {"
            replacement = """if(tbody && data && data.length > 0) {
                    
                    // -- DYNAMIC UI BINDINGS --
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
                    
                    // 3. Update Survey Table
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
            """
            script_str = script_str.replace(target, replacement)
            script.string = script_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
        
    print("Stripped mock data and bound real data.")

if __name__ == '__main__':
    update_index_html()
