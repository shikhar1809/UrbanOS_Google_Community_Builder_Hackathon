from bs4 import BeautifulSoup
import re

def fix_aesthetics():
    with open('index.html', 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    head = soup.find('head')
    
    # 1. Update Google Fonts
    fonts_link = soup.new_tag('link', rel='stylesheet', href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Playfair+Display:wght@600;700&display=swap')
    head.append(fonts_link)

    # 2. Inject Premium CSS Overrides
    style_tag = soup.new_tag('style')
    style_tag.string = """
        /* Premium Core Override */
        :root {
            --bg-shell: #141414;
            --bg-card: #1E1E1E;
            --border-card: #2A2A2A;
            --map-cream: #EDE8DC;
        }

        body, html, main {
            background-color: var(--bg-shell) !important;
            font-family: 'Inter', sans-serif !important;
            color: #efdfdc !important;
        }

        /* Enforce Serif for Headers */
        h1, h2, h3, h4, h5, h6, 
        .font-headline-lg, .font-headline-md, .font-display-lg,
        th, .serif-override {
            font-family: 'Playfair Display', serif !important;
            letter-spacing: 0.02em;
        }

        /* Glassmorphism Header */
        header {
            background: rgba(20, 20, 20, 0.75) !important;
            backdrop-filter: blur(16px) !important;
            -webkit-backdrop-filter: blur(16px) !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        /* Flat Cards with strict 20px radius */
        /* Target all cards and sections */
        section, aside > div, .bg-\\[\\#1E1E1E\\], .bg-surface-container, .bg-surface, .bg-surface-dim {
            background-color: var(--bg-card) !important;
            border-radius: 20px !important;
            box-shadow: none !important;
            border: 1px solid var(--border-card) !important;
            transition: border-color 0.2s ease, transform 0.2s ease;
        }
        
        /* Specific override for the map card to ensure it stays cream */
        .bg-\\[\\#EDE8DC\\] {
            background-color: var(--map-cream) !important;
            border: none !important;
        }

        /* Micro-animations */
        tr {
            transition: background-color 0.15s ease-in-out;
        }
        tr:hover {
            background-color: #252525 !important;
        }

        button:hover, .cursor-pointer:hover {
            opacity: 0.85;
            transform: translateY(-1px);
        }
        
        /* Fix Table borders */
        table {
            border-collapse: collapse;
        }
        th, td {
            border-bottom: 1px solid var(--border-card) !important;
        }
    """
    head.append(style_tag)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print("Aesthetics fixed.")

if __name__ == '__main__':
    fix_aesthetics()
