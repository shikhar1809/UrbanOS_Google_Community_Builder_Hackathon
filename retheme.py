with open('public/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. MULTI-TOKEN SPECIFIC FIXES (do first, longest match wins) ──
specific = [
    ('from-zinc-800 to-black',                         'from-blue-50 to-gray-100'),
    ('bg-[#1C1C1C] border border-[#333]',              'bg-white border border-gray-300'),
    ('shadow-[0_0_30px_rgba(255,255,255,0.05)]',       'shadow-md'),
    ('shadow-[0_0_20px_rgba(255,255,255,0.1)]',        'shadow-sm'),
    ('shadow-[0_0_40px_rgba(255,255,255,0.03)]',       'shadow-sm'),
    ('::-webkit-scrollbar-track { background: #0A0A0A; }', '::-webkit-scrollbar-track { background: #f1f5f9; }'),
    ('::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }', '::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }'),
    ('::-webkit-scrollbar-thumb:hover { background: #555; }', '::-webkit-scrollbar-thumb:hover { background: #94a3b8; }'),
    ('background: #0A0A0A',      'background: #ffffff'),
    ('background: #111',         'background: #f9fafb'),
    ('background: #1C1C1C',      'background: #f3f4f6'),
    ('background: #161616',      'background: #f9fafb'),
    ('background: #262626',      'background: #f3f4f6'),
    ('background: #2A2A2A',      'background: #e5e7eb'),
    ('background-color: #0A0A0A','background-color: #ffffff'),
    # Logo icon: remove text-blue-400 (color now hardcoded in stroke)
    ('class="w-10 h-10 text-blue-400"', 'class="w-10 h-10"'),
]

for old, new in specific:
    content = content.replace(old, new)

# Fix currentColor strokes -> explicit blue on the logo paths
content = content.replace(
    'M12 22s8-6 8-12.5a8 8 0 0 0-16 0C4 16 12 22 12 22z" stroke="currentColor"',
    'M12 22s8-6 8-12.5a8 8 0 0 0-16 0C4 16 12 22 12 22z" stroke="#3b82f6"'
)
content = content.replace(
    'M8 12v-3h2V6h4v4h2v2" stroke="currentColor"',
    'M8 12v-3h2V6h4v4h2v2" stroke="#3b82f6"'
)

# ── 2. BACKGROUND CLASSES ──
bg_map = [
    ('bg-[#0A0A0A]', 'bg-white'),
    ('bg-[#111]',    'bg-gray-50'),
    ('bg-[#161616]', 'bg-gray-50'),
    ('bg-[#1C1C1C]', 'bg-gray-100'),
    ('bg-[#262626]', 'bg-gray-100'),
    ('bg-[#2A2A2A]', 'bg-gray-200'),
    ('bg-[#333]',    'bg-gray-200'),
    ('bg-black',     'bg-white'),
    ('bg-zinc-900',  'bg-gray-50'),
    ('bg-zinc-800',  'bg-gray-100'),
    ('bg-zinc-700',  'bg-gray-200'),
    ('bg-zinc-500',  'bg-gray-400'),
    ('bg-zinc-400',  'bg-gray-300'),
]
for old, new in sorted(bg_map, key=lambda x: len(x[0]), reverse=True):
    content = content.replace(old, new)

# ── 3. TEXT CLASSES ──
text_map = [
    ('text-white',    'text-gray-900'),
    ('text-zinc-100', 'text-gray-800'),
    ('text-zinc-200', 'text-gray-700'),
    ('text-zinc-300', 'text-gray-600'),
    ('text-zinc-400', 'text-gray-500'),
    ('text-zinc-500', 'text-gray-500'),
    ('text-zinc-600', 'text-gray-500'),
]
for old, new in sorted(text_map, key=lambda x: len(x[0]), reverse=True):
    content = content.replace(old, new)

# ── 4. BORDER CLASSES ──
border_map = [
    ('border-[#222]',    'border-gray-200'),
    ('border-[#262626]', 'border-gray-200'),
    ('border-[#333]',    'border-gray-300'),
    ('border-[#444]',    'border-gray-300'),
    ('border-zinc-900',  'border-gray-100'),
    ('border-zinc-800',  'border-gray-200'),
    ('border-zinc-700',  'border-gray-300'),
    ('border-zinc-600',  'border-gray-300'),
    ('border-zinc-500',  'border-gray-400'),
]
for old, new in sorted(border_map, key=lambda x: len(x[0]), reverse=True):
    content = content.replace(old, new)

# ── 5. ENSURE NAV + KEY UI ELEMENTS STAY VISIBLE ──
# Navbar: keep white background, dark text
content = content.replace(
    'bg-gray-900 border-b border-gray-200',
    'bg-white border-b border-gray-200'
)
# Make nav brand text dark
content = content.replace(
    'text-xl font-bold text-gray-900 tracking-tight',
    'text-xl font-bold text-gray-900 tracking-tight'
)

# Sign-in button (Google) — already white bg, keep black text
# Chart.js dark backgrounds inside canvas — handled via JS, skip

with open('public/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Theme applied successfully.")
