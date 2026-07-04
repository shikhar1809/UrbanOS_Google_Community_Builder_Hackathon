import re

def update_logo():
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Update Splash Screen Logo
    splash_old = '<h1 class="text-4xl serif text-white mb-4">UrbanOS</h1>'
    splash_new = """<div class="flex flex-col items-center mb-6">
                <div class="relative flex items-center justify-center w-16 h-16 mb-4 rounded-2xl bg-gradient-to-br from-zinc-800 to-black border border-zinc-700/50 shadow-[0_0_30px_rgba(255,255,255,0.05)]">
                    <svg class="w-10 h-10 text-white drop-shadow-[0_0_8px_rgba(255,255,255,0.4)]" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
                        <path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <h1 class="text-5xl font-bold tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-white via-zinc-100 to-zinc-500">Urban<span class="text-zinc-200">OS</span><span class="text-blue-500">.</span></h1>
            </div>"""
    
    html = html.replace(splash_old, splash_new)

    # 2. Update Sidebar Logo
    sidebar_old = '<span class="serif text-xl text-white">UrbanOS.</span>'
    sidebar_new = """<div class="flex items-center gap-3 group cursor-pointer">
                <div class="relative flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-zinc-800 to-[#0A0A0A] border border-zinc-800 shadow-[0_0_15px_rgba(255,255,255,0.03)] group-hover:shadow-[0_0_20px_rgba(255,255,255,0.1)] transition-all duration-300">
                    <svg class="w-6 h-6 text-zinc-200 group-hover:text-white transition-colors duration-300" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
                        <path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <span class="text-2xl font-bold tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-white to-zinc-400">Urban<span class="text-zinc-100">OS</span><span class="text-blue-500">.</span></span>
            </div>"""
    
    html = html.replace(sidebar_old, sidebar_new)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("Logo updated successfully!")

if __name__ == "__main__":
    update_logo()
