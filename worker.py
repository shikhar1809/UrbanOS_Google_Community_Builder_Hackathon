import os
import httpx
from twilio.rest import Client
from dotenv import load_dotenv
from google import genai
import tempfile
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886") # Fallback to twilio sandbox

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) if os.getenv("GEMINI_API_KEY") else None

def send_whatsapp_message(to_number: str, body: str):
    if not client:
        logger.error(f"[WORKER] Cannot send message to {to_number}: Twilio client not configured.")
        return None
        
    try:
        return client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body
        )
    except Exception as e:
        logger.error(f"[WORKER ERROR] Failed to send message: {e}", exc_info=True)
        return None

def notify_status_change(phone_number: str, reference_id: str, new_status: str, department: str = "Relevant Department"):
    """
    Called from admin dashboard when a report changes status.
    """
    body = f"Update on your report #{reference_id}: It is now '{new_status}' and has been assigned to {department}."
    return send_whatsapp_message(phone_number, body)

async def process_voice_note(media_url: str, phone_number: str):
    # Delayed imports to avoid circular dependency
    from state import update_session
    from main import firestore_db
    
    logger.info(f"[WORKER] Starting transcription for {phone_number}...")
    
    if not gemini_client:
        logger.error(f"[WORKER ERROR] Gemini API key not configured. Cannot transcribe for {phone_number}.")
        send_whatsapp_message(phone_number, "Transcription is currently unavailable. Could you please type your description instead?")
        return
    
    # 1. Download the audio file from Twilio
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
                
        # 2. Upload to Gemini
        logger.info(f"[WORKER] Uploading audio to Gemini for {phone_number}...")
        uploaded_file = gemini_client.files.upload(file=tmp_path)
        
        # 3. Transcribe
        prompt = "Transcribe this audio message exactly. Only return the transcription, nothing else."
        transcription_response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[uploaded_file, prompt]
        )
        
        transcript = transcription_response.text.strip()
        logger.info(f"[WORKER] Transcription success for {phone_number}: {transcript}")
        
        # Clean up
        os.remove(tmp_path)
        gemini_client.files.delete(name=uploaded_file.name)
        
        # 4. Update Session and Reply
        update_session(firestore_db, phone_number, "awaiting_location", {"description": transcript, "voice_url": media_url})
        reply = f"Description saved from your voice note: \"{transcript}\"\n\nNow share your location — send a location pin, or type your area name."
        send_whatsapp_message(phone_number, reply)
        
    except Exception as e:
        logger.error(f"[WORKER ERROR] Voice note processing failed: {e}", exc_info=True)
        send_whatsapp_message(phone_number, "We had trouble transcribing your voice note. Could you please type your description instead?")
