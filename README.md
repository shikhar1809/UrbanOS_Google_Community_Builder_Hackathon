# UrbanOS.
> Civic intelligence for Indian constituencies — proposals in, decisions out, via WhatsApp.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![Firebase](https://img.shields.io/badge/Firebase-FFCA28?style=flat-square&logo=firebase&logoColor=black)
![Gemini](https://img.shields.io/badge/Gemini_2.5_Flash-4285F4?style=flat-square&logo=google&logoColor=white)
![Twilio](https://img.shields.io/badge/WhatsApp-Twilio-F22F46?style=flat-square&logo=twilio&logoColor=white)
![Live](https://img.shields.io/badge/Live-urbanos.web.app-00C853?style=flat-square&logo=googlechrome&logoColor=white)

---

## What is this?

**Q:** What problem does UrbanOS solve?  
**A:** Most civic feedback dies in suggestion boxes or ignored emails. UrbanOS gives every citizen a direct line to their constituency — via WhatsApp — and gives administrators a ranked, AI-triaged dashboard to act on it.

**Q:** Who is it for?  
**A:** Two users: the **citizen** (anyone with WhatsApp) and the **admin** (MLA/ward officer/civic body). Zero app installs required on either side.

---

## How does it work?

**Q:** Walk me through the citizen flow.  
**A:** Citizen WhatsApps a proposal — text, voice note, or photo with a GPS pin. Gemini auto-classifies it: category, priority, budget estimate, zone, and language. Done. No forms, no logins.

**Q:** How does the admin act on proposals?  
**A:** The dashboard aggregates proposals into ranked projects using a civic impact score: `demand × priority × demographic/infrastructure gaps`. One click to sanction → every citizen who proposed it gets a WhatsApp reply instantly.

**Q:** What's the Insights tab?  
**A:** A RAG AI agent wired to all live Firestore data. Ask it anything in natural language — it answers, generates Mermaid charts, and can export tables as CSV on demand.

**Q:** How do surveys work?  
**A:** Admin creates a targeted survey from the dashboard and broadcasts it to all constituents over WhatsApp. Responses flow back into Firestore automatically.

---

## What's the tech?

| Layer | Stack |
|---|---|
| 🔧 Backend | FastAPI (Python 3.11) → Firebase Cloud Functions Gen 2 |
| 🗄️ Database | Cloud Firestore |
| 🤖 AI | Gemini 2.5 Flash — triage, ranking, RAG, voice transcription |
| 📱 Citizen Interface | WhatsApp via Twilio webhooks |
| 🖥️ Admin Dashboard | Vanilla JS + Tailwind CSS, Firebase Hosting |
| 🌐 Live URL | [urbanos.web.app](https://urbanos.web.app) |

**Notable implementation details:**
- 🔐 Twilio webhook signature validation (cryptographic HMAC, not just IP trust)
- 🎙️ Voice notes transcribed via Gemini multimodal — no separate STT service needed
- 🌏 Auto-detected multi-language support (Hindi, English, and more)
- ⚡ Async architecture: blocking Firestore I/O offloaded to thread pool
- 📍 GPS coordinate validation with geographic bounds checking per constituency

---

## How do I run it?

**Q:** What do I need?  
**A:** Python 3.11+, a Firebase project, a Twilio account (WhatsApp sandbox), and a Gemini API key.

**Q:** How do I set it up?  
**A:** Clone the repo, copy `.env.example` → `.env`, fill in your keys, then:

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

**Q:** What goes in `.env`?  
```env
GEMINI_API_KEY=...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
FIREBASE_PROJECT_ID=...
```

**Q:** What's the WhatsApp webhook URL?  
**A:** Point your Twilio sandbox webhook to:
```
https://<your-function-url>/webhook/whatsapp
```

---

## How do I deploy?

**Q:** How do I ship it?  
**A:** One command:
```bash
firebase deploy
```
This deploys the FastAPI backend as a Cloud Function and the admin dashboard to Firebase Hosting simultaneously.

**Q:** Any gotchas?  
**A:** Set all your `.env` values as Firebase secret config before deploying (`firebase functions:secrets:set`). The function cold-start is ~1s — acceptable for a webhook handler.

---

## What's the status?

**Q:** Is this production-ready?  
**A:** This is a working prototype built for the **Google Community Builder Hackathon**. The core feedback loop — propose → triage → rank → sanction → notify → survey — is fully functional and live at [urbanos.web.app](https://urbanos.web.app).

**Q:** What's next?  
**A:** Multi-constituency support, a public-facing transparency portal, and integration with government scheme databases for automatic budget cross-referencing.

---

<div align="center">
  <sub>Built with 🔥 for the communities that deserve better infrastructure and the engineers who want to help build it.</sub>
</div>
