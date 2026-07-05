with open('public/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ══════════════════════════════════════════════════════
# 1. REPLACE ENTIRE <style> BLOCK WITH NEW DESIGN SYSTEM
# ══════════════════════════════════════════════════════
old_style_start = '<style>'
old_style_end = '</style>'
idx_start = content.index(old_style_start)
idx_end = content.index(old_style_end) + len(old_style_end)

new_style = """<style>
    /* ── DESIGN TOKENS ──────────────────────────────── */
    :root {
        --bg-page:    #F0F2F5;
        --bg-card:    #FFFFFF;
        --bg-nav:     #0F172A;
        --border:     #E2E8F0;
        --text-primary:   #0F172A;
        --text-secondary: #64748B;
        --text-muted:     #94A3B8;
        --blue:       #3B82F6;
        --blue-dark:  #1D4ED8;
        --radius:     12px;
    }

    /* ── BASE ───────────────────────────────────────── */
    * { box-sizing: border-box; }
    html, body {
        font-family: 'Inter', sans-serif;
        background-color: var(--bg-page);
        color: var(--text-primary);
        margin: 0; padding: 0;
    }
    h1, h2, h3, h4 {
        color: var(--text-primary);
        font-family: 'Inter', sans-serif;
        font-weight: 700;
    }

    /* ── NAVBAR ─────────────────────────────────────── */
    header {
        background: var(--bg-nav) !important;
        border-bottom: 1px solid rgba(255,255,255,0.06) !important;
        box-shadow: 0 1px 20px rgba(0,0,0,0.3) !important;
    }
    /* Nav logo text */
    header span.logo-text {
        color: #FFFFFF !important;
        font-weight: 800 !important;
        letter-spacing: -0.03em !important;
        font-size: 1.4rem !important;
    }
    /* Nav links */
    .nav-link {
        cursor: pointer;
        color: #94A3B8 !important;
        font-size: 0.75rem !important;
        font-weight: 500 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        padding-bottom: 4px !important;
        border-bottom: 2px solid transparent !important;
        transition: color 0.2s, border-color 0.2s !important;
    }
    .nav-link:hover {
        color: #FFFFFF !important;
    }
    .nav-link.active {
        color: #FFFFFF !important;
        border-bottom-color: #3B82F6 !important;
        font-weight: 600 !important;
    }
    /* Profile area in nav */
    header .border-l { border-color: rgba(255,255,255,0.1) !important; }
    header #user-profile-name { color: #F1F5F9 !important; }
    header #user-profile-role { color: #94A3B8 !important; }
    header .material-symbols-outlined { color: #94A3B8 !important; }
    header .expand_more { color: #94A3B8 !important; }

    /* ── LOGO IN NAV ────────────────────────────────── */
    .nav-logo-box {
        background: rgba(59,130,246,0.12) !important;
        border: 1px solid rgba(59,130,246,0.3) !important;
        border-radius: 10px !important;
        box-shadow: 0 0 12px rgba(59,130,246,0.15) !important;
    }

    /* ── CARDS ──────────────────────────────────────── */
    .card {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04) !important;
    }
    .card:hover {
        box-shadow: 0 2px 8px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.06) !important;
        transition: box-shadow 0.2s ease !important;
    }

    /* ── PAGE BACKGROUNDS ───────────────────────────── */
    .view-content { display: none !important; }
    .view-content.active { display: flex !important; }

    /* ── INPUTS & FORMS ─────────────────────────────── */
    input, textarea, select {
        background: #F8FAFC !important;
        border: 1px solid #CBD5E1 !important;
        color: #0F172A !important;
        border-radius: 8px !important;
    }
    input::placeholder, textarea::placeholder {
        color: #94A3B8 !important;
    }
    input:focus, textarea:focus {
        outline: none !important;
        border-color: #3B82F6 !important;
        box-shadow: 0 0 0 3px rgba(59,130,246,0.12) !important;
    }

    /* ── SECTION HEADINGS ───────────────────────────── */
    .section-label {
        font-size: 0.65rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        color: var(--text-muted) !important;
    }

    /* ── BADGES / PILLS ─────────────────────────────── */
    .badge {
        border-radius: 9999px !important;
        padding: 2px 10px !important;
        font-size: 0.65rem !important;
        font-weight: 600 !important;
        border: 1px solid currentColor !important;
    }

    /* ── TABLE ──────────────────────────────────────── */
    table thead th {
        background: #F8FAFC !important;
        color: #64748B !important;
        border-bottom: 1px solid #E2E8F0 !important;
        font-size: 0.7rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
    }
    table tbody tr:hover { background: #F8FAFC !important; }
    table tbody tr { border-bottom: 1px solid #F1F5F9 !important; }

    /* ── SCROLLBAR ──────────────────────────────────── */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }

    /* ── LEAFLET MAP ────────────────────────────────── */
    .leaflet-container { background: #E8EDF2 !important; border-radius: 10px !important; }
    .leaflet-control-zoom { display: none !important; }
    .leaflet-control-attribution { display: none !important; }

    /* ── SPLASH SCREEN ──────────────────────────────── */
    #splash-screen {
        background: var(--bg-nav) !important;
    }

    /* ── PIN GATE / LOGIN ───────────────────────────── */
    #pin-gate {
        background: var(--bg-nav) !important;
    }
    #pin-gate h1 { color: #FFFFFF !important; }
    #pin-gate p { color: #94A3B8 !important; }

    /* ── MAP PIN ────────────────────────────────────── */
    .map-pin {
        width: 28px; height: 28px;
        background: white;
        color: white;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        position: absolute;
        border: 2px solid white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }

    /* ── PULSE DOT ──────────────────────────────────── */
    .pulse-dot {
        width: 8px; height: 8px;
        background-color: #4ade80;
        border-radius: 50%;
        box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7);
        animation: pulse-green 2s infinite;
        display: inline-block;
    }
    @keyframes pulse-green {
        0%   { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74,222,128,0.7); }
        70%  { transform: scale(1);    box-shadow: 0 0 0 6px rgba(74,222,128,0); }
        100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74,222,128,0); }
    }

    /* ── DATA PACKET ANIMATION ──────────────────────── */
    @keyframes flyPacket {
        0%   { transform: translateX(0) scale(1); opacity: 0; }
        10%  { opacity: 1; }
        50%  { transform: translateX(50vw) scale(1.2); opacity: 1; }
        90%  { opacity: 1; }
        100% { transform: translateX(calc(100vw - 300px)) scale(1); opacity: 0; }
    }
    .data-packet {
        position: absolute; left: 100px; top: 50%; margin-top: -6px;
        width: 12px; height: 12px;
        background-color: #3B82F6;
        border-radius: 50%;
        box-shadow: 0 0 10px 2px rgba(59,130,246,0.4);
        z-index: 20;
    }
    .fly-once { animation: flyPacket 1.5s cubic-bezier(0.4,0,0.2,1) forwards; }

    /* ── SEGMENTED BAR ──────────────────────────────── */
    .segmented-bar { display: flex; gap: 2px; height: 12px; width: 100%; }
    .segment { flex: 1; background: #E2E8F0; border-radius: 1px; }
    .segment.active { background: #94A3B8; }

    /* ── MISC TEXT FIXES ────────────────────────────── */
    .text-gray-900 { color: #0F172A !important; }
    .text-gray-700 { color: #374151 !important; }
    .text-gray-600 { color: #4B5563 !important; }
    .text-gray-500 { color: #64748B !important; }
    .text-gray-400 { color: #94A3B8 !important; }

    /* Fix bg shades */
    .bg-gray-50  { background-color: #F8FAFC !important; }
    .bg-gray-100 { background-color: #F1F5F9 !important; }
    .bg-gray-200 { background-color: #E2E8F0 !important; }
    .bg-white     { background-color: #FFFFFF !important; }
    .border-gray-200 { border-color: #E2E8F0 !important; }
    .border-gray-300 { border-color: #CBD5E1 !important; }
</style>"""

content = content[:idx_start] + new_style + content[idx_end:]

# ══════════════════════════════════════════════════════
# 2. REPLACE LOGO IN NAVBAR with clean new version
# ══════════════════════════════════════════════════════
old_logo_block = '''<div class="flex items-center gap-3 group cursor-pointer">
                <div class="relative flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-zinc-800 to-[#ffffff] border border-gray-200 shadow-[0_0_15px_rgba(255,255,255,0.03)] group-hover:shadow-[0_0_20px_rgba(59,130,246,0.15)] transition-all duration-300">
                    <svg class="w-7 h-7 text-blue-400 group-hover:text-blue-300 transition-colors duration-300 drop-shadow-[0_0_8px_rgba(59,130,246,0.4)]" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M12 22s8-6 8-12.5a8 8 0 0 0-16 0C4 16 12 22 12 22z" stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                        <path d="M8 12v-3h2V6h4v4h2v2" stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <span class="text-3xl font-bold tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-white to-zinc-400">Urban<span class="text-gray-800">OS</span><span class="text-blue-500">.</span></span>
            </div>'''

new_logo_block = '''<div class="flex items-center gap-3 group cursor-pointer">
                <div class="nav-logo-box relative flex items-center justify-center w-10 h-10 transition-all duration-300">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class="w-7 h-7">
                        <path d="M12 22s8-6 8-12.5a8 8 0 0 0-16 0C4 16 12 22 12 22z" stroke="#3B82F6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                        <path d="M8 12v-3h2V6h4v4h2v2" stroke="#3B82F6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <span style="font-family:Inter,sans-serif;font-weight:800;font-size:1.35rem;letter-spacing:-0.03em;color:#FFFFFF;">Urban<span style="color:#FFFFFF;">OS</span><span style="color:#3B82F6;">.</span></span>
            </div>'''

content = content.replace(old_logo_block, new_logo_block)

# ══════════════════════════════════════════════════════
# 3. FIX HEADER ELEMENT - ensure dark background
# ══════════════════════════════════════════════════════
content = content.replace(
    '<header class="flex items-center justify-between px-8 py-5 border-b border-gray-200">',
    '<header class="flex items-center justify-between px-8 py-4 sticky top-0 z-50">'
)

# ══════════════════════════════════════════════════════
# 4. FIX MERMAID THEME - light for white bg
# ══════════════════════════════════════════════════════
content = content.replace(
    "mermaid.initialize({ startOnLoad: false, theme: 'dark' });",
    "mermaid.initialize({ startOnLoad: false, theme: 'neutral' });"
)

# ══════════════════════════════════════════════════════
# 5. FIX HTML DARK CLASS - remove dark mode trigger
# ══════════════════════════════════════════════════════
content = content.replace('<html class="dark" lang="en">', '<html lang="en">')

# ══════════════════════════════════════════════════════
# 6. FIX SPLASH SCREEN LOGO TEXT - was gradient from-white which now invisible
# ══════════════════════════════════════════════════════
content = content.replace(
    'text-5xl font-bold tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-white via-zinc-100 to-zinc-500',
    'text-5xl font-bold tracking-tighter'
)
content = content.replace(
    'Urban<span class="text-gray-700">OS</span><span class="text-blue-500">.</span>',
    'Urban<span style="color:#FFFFFF;">OS</span><span style="color:#3B82F6;">.</span>'
)

# ══════════════════════════════════════════════════════
# 7. FIX SPLASH BG - was white after theme swap, restore dark
# ══════════════════════════════════════════════════════
# splash screen should be dark (bg-nav)
content = content.replace(
    'id="splash-screen" class="fixed inset-0 z-[99998] flex flex-col items-center justify-center bg-white',
    'id="splash-screen" class="fixed inset-0 z-[99998] flex flex-col items-center justify-center bg-[#0F172A]'
)

# ══════════════════════════════════════════════════════
# 8. FIX PIN GATE - restore dark background
# ══════════════════════════════════════════════════════
content = content.replace(
    'id="pin-gate" class="fixed inset-0 z-[99999] flex items-center justify-center bg-white',
    'id="pin-gate" class="fixed inset-0 z-[99999] flex items-center justify-center bg-[#0F172A]'
)

with open('public/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Design overhaul complete.")
