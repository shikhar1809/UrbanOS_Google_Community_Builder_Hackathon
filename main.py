import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# In-memory store for messages (newest first)
messages = []

# Media download helper
async def download_media(media_url: str) -> bytes:
    """
    Downloads media from Twilio using HTTP Basic Auth.
    Ready to be piped into a transcription/processing step later.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("Warning: Twilio credentials not set, media download might fail.")
        
    async with httpx.AsyncClient() as client:
        response = await client.get(
            media_url, 
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        response.raise_for_status()
        return response.content

@app.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request):
    """
    Accepts Twilio's form-encoded webhook payload
    """
    form_data = await request.form()
    
    # Log the raw payload for debugging
    print("\n--- RAW WEBHOOK PAYLOAD ---")
    for key, value in form_data.items():
        print(f"{key}: {value}")
    print("---------------------------\n")
    
    media_content_type = form_data.get("MediaContentType0")
    if media_content_type and media_content_type.startswith("image/"):
        print(f"[IMAGE] Received image: {media_content_type}")

    lat = form_data.get("Latitude")
    lng = form_data.get("Longitude")
    
    if lat and lng:
        print(f"[LOCATION] Received valid location: {lat}, {lng}")
        location_source = "gps_pin"
    else:
        print("[INFO] No location data present in this message.")
        location_source = "needs_nlp_extraction" if form_data.get("Body") else None
    
    msg_data = {
        "timestamp": datetime.now().isoformat(),
        "from": form_data.get("From", "Unknown"),
        "body": form_data.get("Body", ""),
        "media_url": form_data.get("MediaUrl0"),
        "media_content_type": media_content_type,
        "latitude": lat,
        "longitude": lng,
        "category": None,
        "vision_tag": None,
        "location_source": location_source
    }
    
    # Prepend to list so newest is first
    messages.insert(0, msg_data)
    
    # Twilio expects a fast 200 OK
    return HTMLResponse(content="", status_code=200)

@app.get("/messages")
async def get_messages():
    """
    Returns all stored messages as JSON, newest first
    """
    return JSONResponse(content=messages)

@app.get("/media-proxy")
async def media_proxy(url: str):
    """
    Proxies media from Twilio using HTTP Basic Auth and streams it back.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Twilio credentials not configured.")
        
    client = httpx.AsyncClient(auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), follow_redirects=True)
    try:
        req = client.build_request("GET", url)
        response = await client.send(req, stream=True)
        
        if response.status_code != 200:
            await response.aread()
            error_detail = response.text
            await client.aclose()
            # Use 502 Bad Gateway to prevent the browser from showing a Basic Auth login popup for 401s
            raise HTTPException(status_code=502, detail=f"Failed to fetch media from Twilio ({response.status_code}): {error_detail}")
            
        async def stream_generator():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await client.aclose()
                
        return StreamingResponse(
            stream_generator(), 
            media_type=response.headers.get("content-type", "application/octet-stream")
        )
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

@app.get("/messages/summary")
async def messages_summary():
    """
    Returns counts of total messages, media types, and location data.
    """
    total = len(messages)
    audio_count = sum(1 for m in messages if m.get("media_content_type") and ("audio" in m["media_content_type"] or "ogg" in m["media_content_type"]))
    image_count = sum(1 for m in messages if m.get("media_content_type") and m["media_content_type"].startswith("image/"))
    valid_coords = sum(1 for m in messages if m.get("latitude") and m.get("longitude"))
    missing_coords = total - valid_coords
    
    return JSONResponse(content={
        "total_messages": total,
        "media": {
            "audio": audio_count,
            "image": image_count
        },
        "location": {
            "valid_coordinates": valid_coords,
            "missing_coordinates": missing_coords
        }
    })

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """
    Simple HTML dashboard that polls /messages and renders cards.
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>WhatsApp Webhook Dashboard</title>
        <style>
            body {
                background-color: #121212;
                color: #e0e0e0;
                font-family: monospace;
                padding: 20px;
                max-width: 800px;
                margin: 0 auto;
            }
            .message-card {
                background-color: #1e1e1e;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 15px;
            }
            .meta {
                color: #888;
                font-size: 0.9em;
                margin-bottom: 10px;
            }
            .body {
                font-size: 1.1em;
                margin-bottom: 10px;
                white-space: pre-wrap;
            }
            .media {
                margin-top: 10px;
            }
            .location {
                margin-top: 10px;
                color: #00bcd4;
            }
            img {
                max-width: 100%;
                border-radius: 4px;
            }
            audio {
                width: 100%;
                margin-top: 5px;
            }
            h1 {
                color: #fff;
                border-bottom: 1px solid #333;
                padding-bottom: 10px;
            }
        </style>
    </head>
    <body>
        <h1>Live WhatsApp Messages</h1>
        <div id="messages-container">Waiting for messages...</div>

        <script>
            async function fetchMessages() {
                try {
                    const response = await fetch('/messages');
                    const messages = await response.json();
                    const container = document.getElementById('messages-container');
                    
                    if (messages.length === 0) {
                        container.innerHTML = '<p>No messages received yet.</p>';
                        return;
                    }

                    container.innerHTML = messages.map(msg => {
                        let mediaHtml = '';
                        if (msg.media_url) {
                            const proxyUrl = `/media-proxy?url=${encodeURIComponent(msg.media_url)}`;
                            if (msg.media_content_type && msg.media_content_type.startsWith('image/')) {
                                mediaHtml = `<div class="media"><img src="${proxyUrl}" alt="Received Image"></div>`;
                            } else if (msg.media_content_type && (msg.media_content_type.startsWith('audio/') || msg.media_content_type.includes('ogg'))) {
                                mediaHtml = `<div class="media"><audio controls src="${proxyUrl}"></audio></div>`;
                            } else {
                                mediaHtml = `<div class="media"><a href="${proxyUrl}" target="_blank" style="color: #4CAF50;">View Attachment (${msg.media_content_type})</a></div>`;
                            }
                        }

                        let locationHtml = '';
                        if (msg.latitude && msg.longitude) {
                            locationHtml = `<div class="location">📍 Location: ${msg.latitude}, ${msg.longitude} <br> <a href="https://maps.google.com/?q=${msg.latitude},${msg.longitude}" target="_blank" style="color: #00bcd4;">Open in Maps</a></div>`;
                        }

                        const date = new Date(msg.timestamp).toLocaleString();
                        
                        return `
                            <div class="message-card">
                                <div class="meta"><strong>From:</strong> ${msg.from} | <strong>Received:</strong> ${date}</div>
                                ${msg.body ? `<div class="body">${msg.body}</div>` : ''}
                                ${mediaHtml}
                                ${locationHtml}
                            </div>
                        `;
                    }).join('');
                } catch (error) {
                    console.error('Error fetching messages:', error);
                }
            }

            // Poll every 3 seconds
            setInterval(fetchMessages, 3000);
            // Initial fetch
            fetchMessages();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/priority")
async def priority():
    """
    Serves the static Priority Dashboard HTML generated by Stitch.
    """
    return FileResponse("priority.html")
