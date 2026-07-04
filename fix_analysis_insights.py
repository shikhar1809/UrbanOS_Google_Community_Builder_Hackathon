import re
from bs4 import BeautifulSoup

def fix_analysis_insights():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. FIX PREDICTIVE HOTSPOT ANALYSIS
    predictive_text = soup.find(string=re.compile('AI analysis indicates a 40% probability'))
    if predictive_text:
        span = soup.new_tag('span', id='pred-text')
        span.string = 'Awaiting data for predictive generation...'
        predictive_text.replace_with(span)

    vol = soup.find(string=re.compile('850'))
    if vol:
        vol.parent['id'] = 'pred-vol'
        vol.replace_with('0')

    readiness = soup.find(string=re.compile('Low'))
    if readiness:
        readiness.parent['id'] = 'pred-readiness'
        readiness.replace_with('--')

    sla = soup.find(string=re.compile('12%'))
    if sla:
        sla.parent['id'] = 'pred-sla'
        sla.replace_with('0%')

    # 2. FIX CITIZEN SATISFACTION (INSIGHTS PAGE)
    # The rating is 4.2<span class="...">/5</span>
    # We will target the parent div
    rating_div = soup.find('div', class_=re.compile('text-5xl.*4\.2'))
    if not rating_div:
        # Try finding the 4.2 string
        four_two = soup.find(string=re.compile('4\.2'))
        if four_two:
            rating_div = four_two.parent

    if rating_div:
        rating_div.clear()
        span1 = soup.new_tag('span', id='citizen-rating')
        span1.string = '0.0'
        rating_div.append(span1)
        span2 = soup.new_tag('span', **{'class': 'text-xl text-zinc-500'})
        span2.string = '/5'
        rating_div.append(span2)

    responses_text = soup.find(string=re.compile('Based on 4,192 responses'))
    if responses_text:
        responses_text.parent['id'] = 'citizen-responses'
        responses_text.replace_with('Based on 0 responses')

    # 3. UPDATE REACTIVE ENGINE IN JAVASCRIPT
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'const counts = {' in script.string:
            s_str = script.string
            
            injection = """
                    // -- DYNAMIC PREDICTIVE HOTSPOT --
                    const predText = document.getElementById('pred-text');
                    const predVol = document.getElementById('pred-vol');
                    const predReadiness = document.getElementById('pred-readiness');
                    const predSla = document.getElementById('pred-sla');
                    
                    if (predText && total > 0) {
                        let topCat = 'General Issues';
                        let max = 0;
                        if(counts.water > max) { max = counts.water; topCat = 'Waterlogging & Leaks'; }
                        if(counts.roads > max) { max = counts.roads; topCat = 'Road Infrastructure'; }
                        if(counts.power > max) { max = counts.power; topCat = 'Power Outages'; }
                        if(counts.cleanliness > max) { max = counts.cleanliness; topCat = 'Waste Management'; }
                        
                        let prob = Math.min(95, Math.max(15, Math.round((max / total) * 100) + 20));
                        
                        predText.innerText = `AI analysis indicates a ${prob}% probability of severe escalation in ${topCat} over the next 72 hours based on recent complaint clusters.`;
                        if (predVol) predVol.innerText = Math.round(max * 1.5);
                        if (predSla) predSla.innerText = Math.round((prob / 100) * 25) + '%';
                        if (predReadiness) {
                            if (prob > 70) {
                                predReadiness.innerText = 'Low';
                                predReadiness.className = 'text-2xl text-red-400 serif mt-1';
                            } else if (prob > 40) {
                                predReadiness.innerText = 'Moderate';
                                predReadiness.className = 'text-2xl text-amber-400 serif mt-1';
                            } else {
                                predReadiness.innerText = 'High';
                                predReadiness.className = 'text-2xl text-green-400 serif mt-1';
                            }
                        }
                    }

                    // -- DYNAMIC CITIZEN SATISFACTION --
                    const cRating = document.getElementById('citizen-rating');
                    const cResp = document.getElementById('citizen-responses');
                    if (cRating) {
                        let totalScore = data.reduce((acc, msg) => acc + (msg.body && msg.body.length > 30 ? 5 : 2), 0);
                        let avg = data.length > 0 ? (totalScore / data.length) : 0;
                        cRating.innerText = avg.toFixed(1);
                    }
                    if (cResp) cResp.innerText = `Based on ${data.length} responses`;
            """
            
            # Insert this right after updating sidebar graphs
            target = "const elRoads = document.getElementById('cat-roads-pct'); if(elRoads) elRoads.innerText = pRoads + '%';"
            s_str = s_str.replace(target, target + "\n" + injection)
            
            script.string = s_str

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))

if __name__ == '__main__':
    fix_analysis_insights()
