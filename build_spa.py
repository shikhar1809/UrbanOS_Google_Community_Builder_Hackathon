from bs4 import BeautifulSoup
import re

def build_spa():
    with open('priority.html', 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    # Add SPA styles and JS
    head = soup.find('head')
    
    style_tag = soup.new_tag('style')
    style_tag.string = """
        .view-content { display: none; }
        .view-content.active { display: block; }
        .nav-link { cursor: pointer; }
        .nav-link.active { color: var(--color-primary, #ffb4a8); border-bottom-width: 2px; border-color: var(--color-primary, #ffb4a8); padding-bottom: 0.25rem; }
    """
    head.append(style_tag)

    # Convert nav links to JS triggers
    nav = soup.find('nav')
    if nav:
        links = nav.find_all('a')
        for link in links:
            text = link.text.strip().lower()
            link['onclick'] = f"switchView('{text}')"
            link['href'] = "javascript:void(0)"
            base_class = "font-body-md text-body-md transition-colors cursor-pointer active:opacity-80 nav-link"
            if text == 'priority':
                link['class'] = base_class + " active"
            else:
                link['class'] = base_class + " text-on-surface-variant dark:text-on-surface-variant hover:text-primary dark:hover:text-primary"

    main_tag = soup.find('main')
    
    # Extract priority content and wrap
    priority_content = soup.new_tag('div', id='view-priority', **{'class': 'view-content active'})
    for child in list(main_tag.children):
        priority_content.append(child.extract())
    
    main_tag.append(priority_content)

    # Process other views
    views = [('analysis.html', 'analysis'), ('survey.html', 'survey'), ('coordination.html', 'coordination')]
    
    for filename, view_id in views:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                view_soup = BeautifulSoup(f, 'html.parser')
                view_main = view_soup.find('main')
                
                view_div = soup.new_tag('div', id=f'view-{view_id}', **{'class': 'view-content'})
                if view_main:
                    for child in list(view_main.children):
                        view_div.append(child.extract())
                
                main_tag.append(view_div)
        except Exception as e:
            print(f"Skipping {filename}: {e}")

    script_tag = soup.new_tag('script')
    script_tag.string = """
    function switchView(viewId) {
        document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
        document.getElementById('view-' + viewId).classList.add('active');
        
        document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.nav-link').forEach(el => {
            if(el.textContent.trim().toLowerCase() === viewId) {
                el.classList.add('active');
            }
        });
    }

    async function fetchMessages() {
        try {
            const response = await fetch('/messages');
            const data = await response.json();
            
            const tbody = document.querySelector('#view-priority table tbody');
            if(tbody && data.messages) {
                const newRows = data.messages.map((msg, index) => {
                    const date = new Date(msg.timestamp).toLocaleString();
                    let score = msg.body && msg.body.length > 30 ? 85 : 45;
                    let color = score > 80 ? 'text-error' : 'text-primary';
                    let status = score > 80 ? 'Critical' : 'New';
                    
                    let media = '';
                    if(msg.num_media > 0) media += ' 📸 Photo/Audio ';
                    if(msg.latitude) media += ' 📍 Location ';
                    if(!media && msg.body) media = '📝 Text';

                    return `
                        <tr class="border-b border-[#222] hover:bg-[#222] transition-colors">
                            <td class="py-4 text-on-surface">#${data.messages.length - index}</td>
                            <td class="py-4 text-on-surface">
                                <div>${msg.from}</div>
                                <div class="text-xs text-on-surface-variant">${date}</div>
                            </td>
                            <td class="py-4 text-on-surface-variant">${media}</td>
                            <td class="py-4 text-on-surface-variant line-clamp-1 max-w-xs">${msg.body || 'Media message'}</td>
                            <td class="py-4 font-headline-md ${color}">${score}</td>
                            <td class="py-4">
                                <span class="px-2 py-1 bg-surface-container-high rounded-full text-xs">${status}</span>
                            </td>
                        </tr>
                    `;
                }).join('');
                if(newRows) {
                    tbody.innerHTML = newRows;
                }
            }
        } catch (error) {
            console.error('Error fetching messages:', error);
        }
    }
    
    setInterval(fetchMessages, 3000);
    fetchMessages();
    """
    soup.body.append(script_tag)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    print("Successfully built index.html SPA")

if __name__ == '__main__':
    build_spa()
