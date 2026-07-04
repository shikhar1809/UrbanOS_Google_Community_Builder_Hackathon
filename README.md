# WhatsApp Twilio Intake & Dashboard

A minimal FastAPI backend to receive, store (in-memory), and display incoming WhatsApp messages from a Twilio Sandbox webhook. This serves as a foundation layer before adding AI processing.

## Prerequisites

- Python 3.11+
- A Twilio account (for the WhatsApp Sandbox)
- [ngrok](https://ngrok.com/) (to expose the local server to the internet)

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy the `.env.example` file to `.env` and fill in your Twilio credentials:
   ```bash
   # On Windows (cmd): copy .env.example .env
   # On Windows (powershell): cp .env.example .env
   ```
   You can find your Account SID and Auth Token on the Twilio Console homepage.

## Running Locally

Start the FastAPI application using uvicorn:
```bash
uvicorn main:app --reload
```
The server will run on `http://127.0.0.1:8000`.

To view the dashboard, navigate to `http://127.0.0.1:8000/dashboard` in your browser.

## Exposing to Twilio via ngrok

Twilio needs a public URL to send webhook events to. We'll use ngrok to expose our local server.

1. In a separate terminal, start ngrok pointing to port 8000:
   ```bash
   ngrok http 8000
   ```
2. Copy the `Forwarding` URL that ngrok provides (e.g., `https://<random-id>.ngrok-free.app`).

## Configuring Twilio Sandbox

1. Go to the [Twilio Console](https://console.twilio.com/).
2. Navigate to **Messaging -> Try it out -> Send a WhatsApp message**.
3. Under the **Sandbox settings** tab, find the field labeled "WHEN A MESSAGE COMES IN".
4. Paste your ngrok forwarding URL and append `/webhook/whatsapp` to it.
   Example: `https://<random-id>.ngrok-free.app/webhook/whatsapp`
5. Save the configuration.

## Joining the Sandbox

1. In the Twilio Console (under the Sandbox settings), you will see a phone number and a join code (e.g., "join something-something").
2. Save the Twilio Sandbox phone number to your phone's contacts.
3. Open WhatsApp on your phone, send a message to that contact with the exact join code.
4. You should receive a confirmation message from Twilio that you are now connected to the sandbox.

## Testing

Once connected, send any message, voice note, photo, or location pin to the sandbox number via WhatsApp.
Watch your running `uvicorn` console for the raw webhook payload logs, and check the dashboard at `http://127.0.0.1:8000/dashboard` to see the messages appear in real-time.
