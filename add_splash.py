import re
import os
from bs4 import BeautifulSoup

def main():
    # 1. Create Service Worker (sw.js)
    sw_code = """
const CACHE_NAME = 'urbanos-cache-v1';
const urlsToCache = [
  '/',
  '/index.html',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('fetch', event => {
  // Only cache GET requests
  if (event.request.method !== 'GET') return;
  // Don't cache API calls
  if (event.request.url.includes('/messages')) return;
  
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        if (response) {
          return response;
        }
        return fetch(event.request);
      })
  );
});
"""
    with open('sw.js', 'w') as f:
        f.write(sw_code)

    # 2. Update index.html
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # Add Splash Screen HTML at the beginning of <body>
    splash_html = """
    <div id="splash-screen" class="fixed inset-0 z-[9999] flex flex-col items-center justify-center bg-[#0A0A0A] transition-opacity duration-700">
        <div class="flex flex-col items-center">
            <!-- Animated Loading Ring -->
            <div class="relative w-24 h-24 mb-8">
                <div class="absolute inset-0 border-4 border-[#262626] rounded-full"></div>
                <div class="absolute inset-0 border-4 border-white rounded-full border-t-transparent animate-spin"></div>
            </div>
            
            <h1 class="text-4xl serif text-white mb-4">UrbanOS</h1>
            <p id="splash-text" class="text-zinc-400 font-mono text-sm tracking-wider uppercase animate-pulse">Initializing System...</p>
            
            <!-- Progress Bar -->
            <div class="w-64 h-1 bg-[#161616] mt-8 rounded-full overflow-hidden">
                <div id="splash-progress" class="h-full bg-white w-0 transition-all duration-300 ease-out"></div>
            </div>
        </div>
    </div>
    """
    
    # Check if splash already exists
    if not soup.find(id="splash-screen"):
        body = soup.find('body')
        if body:
            splash_soup = BeautifulSoup(splash_html, 'html.parser')
            body.insert(0, splash_soup)

    # Convert soup back to string to inject JS securely via regex
    html_str = str(soup)

    # Inject JS for Splash Sequence and Service Worker registration
    splash_js = """
        // --- Splash Screen & Service Worker Logic ---
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js');
            });
        }

        document.addEventListener('DOMContentLoaded', () => {
            const splashText = document.getElementById('splash-text');
            const splashProgress = document.getElementById('splash-progress');
            const splashScreen = document.getElementById('splash-screen');
            
            const sequence = [
                { text: "fetching grievances...", progress: "30%", delay: 600 },
                { text: "loading grievances...", progress: "70%", delay: 1500 },
                { text: "updating dashboard...", progress: "100%", delay: 2200 }
            ];
            
            let totalDelay = 0;
            
            sequence.forEach((step) => {
                setTimeout(() => {
                    splashText.textContent = step.text;
                    splashProgress.style.width = step.progress;
                }, step.delay);
                totalDelay = Math.max(totalDelay, step.delay);
            });
            
            // Fade out and remove after sequence completes
            setTimeout(() => {
                splashScreen.style.opacity = '0';
                setTimeout(() => {
                    splashScreen.style.display = 'none';
                    // Trigger initial fetch only after splash screen finishes
                    fetchMessages();
                    setInterval(fetchMessages, 2000); // Resume polling
                }, 700); // wait for CSS fade transition
            }, totalDelay + 800);
        });
        
        // Remove the existing automatic calls to fetchMessages to prevent double fetching
    """

    # We need to remove the existing `fetchMessages(); setInterval(fetchMessages, 2000);` from the script.
    html_str = re.sub(r'fetchMessages\(\);\s*setInterval\(fetchMessages,\s*2000\);', '', html_str)

    # We also need to inject splash_js right before the closing </script> tag at the bottom
    html_str = html_str.replace("</script>\n</body>", splash_js + "\n</script>\n</body>")

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_str)

    print("Splash screen and Service Worker added successfully!")

if __name__ == "__main__":
    main()
