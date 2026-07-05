<div align="center">

<img src="https://img.shields.io/badge/Live%20Demo-urbanos.web.app-blue?style=for-the-badge&logo=firebase" />
<img src="https://img.shields.io/badge/Built%20With-Gemini%202.5%20Flash-orange?style=for-the-badge&logo=google" />
<img src="https://img.shields.io/badge/Citizen%20Interface-WhatsApp-25D366?style=for-the-badge&logo=whatsapp" />
<img src="https://img.shields.io/badge/Backend-FastAPI%20%2B%20Firebase-009688?style=for-the-badge" />
<img src="https://img.shields.io/badge/Status-Deployed%20%26%20Working-success?style=for-the-badge" />

# UrbanOS.
### *The AI layer between a citizen's voice and their MP's decision.*

</div>

---

## The Problem — What We Saw

Picture a Member of Parliament's office on a Monday morning.

The inbox has **400 WhatsApp forwards** from volunteers. There are letters stacked from three different gram panchayats. Someone posted about a broken road on Facebook. A local councillor called about a drainage issue. Two groups came to the office last week — one wanted a school repaired, another wanted a vocational training centre — and both left with a promise and a handshake.

Now the MP has to decide: **which of these 40 competing projects actually gets sanctioned this quarter?**

There is no system for this. The decision gets made on gut feel, political instinct, or whoever shouted loudest. The quietest communities — the ones too far, too busy, or too unconnected to show up — lose by default.

**This is not a technology problem. It is a listening problem.** And we built UrbanOS to fix it.

---

## The Insight — Why Existing Solutions Don't Work

Before writing a single line of code, we asked: *why hasn't this been solved?*

- **Grievance portals** (like CPGRAMS) require citizens to navigate government websites, create accounts, and fill forms. Adoption among rural or semi-literate populations is near zero.
- **Surveys** capture structured opinion but miss spontaneous, lived experience.
- **Social media listening** captures noise, not actionable proposals. A tweet about a pothole looks identical to a tweet about a movie.
- **Town halls** reach the same 50 people who always show up.

The gap is not *willingness* to participate — it is *friction*. Indians already WhatsApp their problems to each other constantly. We needed to intercept that behaviour, not replace it.

---

## The Solution — Following the Simplest Path

### Step 1: Meet People Where They Already Are — WhatsApp

We chose WhatsApp as the citizen interface because it requires **no app download, no account creation, no literacy beyond what people already have**. India has 530 million+ WhatsApp users, including rural populations on ₹5,000 Android phones on 2G connections.

We did not build a mobile app. We did not build a portal. We built a WhatsApp number.

*Tech used: Twilio WhatsApp Business API — the most reliable programmable messaging layer for WhatsApp, with cryptographic webhook validation so no one can fake a message.*

---

### Step 2: Accept Every Format — Text, Voice, Photo, GPS

A farmer in North Lucknow cannot type well, but can record a 20-second voice note explaining that the tube well near his field has been broken for three weeks.

A college student can send a photo of a broken footpath with a GPS pin.

A retired schoolteacher can type a detailed paragraph in Hindi.

**We accept all of these.** The system does not judge the format — it processes whatever comes in.

*Tech used: Gemini 2.5 Flash multimodal — the same AI model reads text, transcribes voice notes (downloaded from Twilio, processed via Gemini's Files API), and extracts context from images.*

---

### Step 3: Understand, Don't Just Store — AI Triage

Most civic tech just stores what citizens send. We go further.

The moment a proposal arrives, Gemini reads it and extracts:

| What We Extract | Why It Matters |
|---|---|
| **Category** | Is this a road issue? A school? A health facility? |
| **Priority** | How urgent is the need, based on language and context? |
| **Constituency Zone** | North / South / East / West / Central — where does it go? |
| **Language** | Hindi? Urdu? Tamil? Telugu? (So we can reply in the same language) |
| **Budget Estimate** | Is this a ₹5 lakh pothole fix or a ₹5 crore hospital? |
| **Semantic Tag** | A normalised label (e.g. `road_repair`) so "MG Road is dark" and "need lights on main road" are recognised as the **same issue** |

This is the intelligence layer that transforms noise into signal.

*Tech used: Gemini 2.5 Flash with structured JSON output (Pydantic schema-constrained) — no hallucinated fields, no freeform text that breaks downstream logic.*

---

### Step 4: Speak Back in the Citizen's Language

A system that asks a Hindi-speaking villager to "please send your location pin" in English is not accessible. It's cosmetically inclusive.

Every reply UrbanOS sends — location prompts, photo requests, confirmation messages, survey questions — is delivered in the **same language the citizen wrote in**. Detection is instant, using Unicode script ranges (Devanagari for Hindi, Arabic script for Urdu, etc.) with no extra API call. Dynamic content like survey questions is translated on the fly via Gemini.

Supported: **Hindi · Urdu · Tamil · Telugu · Bengali · English**

---

### Step 5: Aggregate and Rank — The MP's Intelligence Feed

Individual proposals are grouped semantically and scored using a **civic impact formula**:

```
Impact Score = Demand Count × Priority Score × (1 + Infrastructure Gap / 10)
```

Where:
- **Demand Count** = how many unique citizens raised this issue
- **Priority Score** = weighted sum of AI-assessed urgency levels  
- **Infrastructure Gap** = distance to nearest school or hospital (sourced from UDISE+ 2021-22 and NHM Uttar Pradesh data for Lucknow zones)

A project with 12 people asking for a school repair in North Lucknow — where the nearest school is 7.8 km away — scores higher than 15 people asking for a park in Central Lucknow where three parks already exist. **Demand weighted by deprivation.**

---

### Step 6: Close the Loop — Sanction and Notify

When an MP sanctions a project through the dashboard, every citizen who submitted a related proposal **gets a WhatsApp message back** telling them their voice was heard and action has been taken.

This is the part most civic tech skips. We call it the feedback loop — the moment a system stops being a black box and starts being a promise kept.

---

### Step 7: Ask Follow-Up Questions — Targeted Surveys

After a citizen submits a proposal, the system can immediately present them with a survey question from the MP's office. The survey engine also supports broadcast — push a question to every citizen who has ever engaged with the system.

Survey results are aggregated in real time and displayed in the admin dashboard with response breakdowns.

---

### Step 8: Let the Data Talk — AI Insights Agent

The admin dashboard has an AI chat interface connected to all live data: proposals, survey responses, demographic layers, and any additional documents or datasets the admin uploads.

An MP's aide can ask:
- *"Which zone has the highest demand for road works?"*
- *"Compare education infrastructure gaps across all zones"*
- *"Generate a chart of proposals by category this month"*

The AI responds with analysis, Mermaid charts, and CSV-exportable tables. It is explicitly constrained to only answer from connected data — no hallucination, strict bias guardrails.

*Tech used: Retrieval-Augmented Generation (RAG) with Gemini 2.5 Flash, Firestore as the live data source, and custom document ingestion.*

---

## How It All Fits Together

```
Citizen sends WhatsApp message (text / voice / photo / GPS)
        │
        ▼
Twilio receives it → verifies HMAC signature → sends to UrbanOS API
        │
        ▼
FastAPI (Firebase Cloud Functions, Gen 2) handles the request
        │
        ├─ Detects citizen language (Unicode, no API call)
        ├─ Replies in their language immediately
        └─ Runs Gemini AI triage in background
                │
                ▼
        Structured proposal stored in Cloud Firestore
        (category, priority, zone, semantic_tag, budget, language)
                │
                ▼
        Admin Dashboard (urbanos.web.app)
        ├─ Raw Feed — every incoming message live
        ├─ Planning — AI-ranked projects by impact score
        ├─ Feedback — survey controls + response analysis
        └─ Insights — RAG AI agent connected to all data
                │
                ▼
        MP sanctions a project
                │
                ▼
        All citizens who raised it get a WhatsApp notification back
```

---

## The Tech Stack — And Why We Chose Each Piece

| Layer | Technology | Why This, Not Something Else |
|---|---|---|
| **Citizen Interface** | WhatsApp via Twilio | 530M Indian users. No install. Works on 2G. Twilio gives us HMAC-verified webhooks and global reliability. |
| **AI Brain** | Gemini 2.5 Flash | Multimodal (reads text, audio, images in one model). Fast. Structured JSON output prevents hallucination in triage. |
| **API Backend** | FastAPI (Python 3.11) | Async-native. Pydantic for schema validation. Blocking Firestore calls offloaded to thread pool so the WhatsApp webhook never times out. |
| **Hosting** | Firebase Cloud Functions Gen 2 | Serverless. Auto-scales. Zero infra management. Cold start < 2s with min instances. |
| **Database** | Cloud Firestore | Real-time updates push to dashboard without polling. Scales horizontally. No schema migrations needed during rapid development. |
| **Admin Dashboard** | Vanilla JS + Tailwind CSS | No build step. Instant deploy. The dashboard is a single HTML file — zero webpack, zero npm, zero runtime errors from dependency hell. |
| **Maps** | Leaflet.js + OpenStreetMap | Free. Offline-friendly. No API key required for basic tile serving. |

---

## Running It Yourself

**You need:**
- Python 3.11+
- Firebase CLI (`npm install -g firebase-tools`)
- A Twilio account (for WhatsApp webhooks)
- A Google Cloud project with Gemini API enabled

**Clone and configure:**
```bash
git clone https://github.com/your-username/UrbanOS.git
cd UrbanOS
cp .env.example .env
# Fill in: GEMINI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
```

**Run locally:**
```bash
pip install -r requirements.txt
uvicorn main:app --reload
# Expose with ngrok for WhatsApp webhook testing:
# ngrok http 8000
# Set Twilio webhook URL to: https://<ngrok-url>/webhook/whatsapp
```

**Deploy to Firebase:**
```bash
firebase login
firebase use --add   # select your project
firebase deploy
```

**Your WhatsApp webhook URL after deploy:**
```
https://api-<your-hash>-uc.a.run.app/webhook/whatsapp
```

---

## Environment Variables

```env
GEMINI_API_KEY=your_gemini_api_key
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

---

## FAQ & Defensibility (For Hackathon Judges)

**Q: You are using the Twilio Sandbox. How do you scale past 50 users?**
> **A:** Yes, the sandbox is just for this hackathon demo. For production deployment in a real constituency, we would apply for a verified WhatsApp Business API number through Meta. That approval process takes 2–4 weeks, at which point the 50-user limit is lifted and we can scale to millions.

**Q: The problem statement mentioned letters, public meetings, and grievance portals. Why did you only build WhatsApp?**
> **A:** We focused exclusively on WhatsApp because it removes the most friction. India has 530 million WhatsApp users. A grievance portal requires a citizen to navigate a website; a letter requires them to travel. By using voice notes on WhatsApp, we included the lowest-literacy, lowest-connectivity citizens first. We designed the backend to be channel-agnostic — adding an email or SMS ingestion layer is on our roadmap, but WhatsApp covers 90% of the population today.

---

## Project Status

This is a **working prototype** built for a civic-tech hackathon. The core feedback loop is fully functional and deployed at [urbanos.web.app](https://urbanos.web.app).

**Admin Dashboard PIN:** `1234` *(prototype auth — replace with Firebase Auth in production)*

**What works end-to-end:**
- ✅ WhatsApp intake (text, voice, photo, GPS)
- ✅ Multilingual responses (Hindi, Urdu, Tamil, Telugu, Bengali)
- ✅ AI triage with semantic clustering
- ✅ Impact-scored project ranking
- ✅ One-click project sanction + citizen notification
- ✅ Survey broadcast and response tracking
- ✅ RAG insights agent with chart generation

**What a production version would add:**
- 🔲 Firebase Auth (replace PIN gate)
- 🔲 Multi-tenancy (one deployment, multiple constituencies)
- 🔲 WhatsApp Business API verification (remove sandbox 50-user limit)
- 🔲 Live Census API integration (replace static demographic layer)
- 🔲 USSD/SMS fallback for feature phones without WhatsApp

---

## The Bigger Picture

India has 543 Lok Sabha constituencies. Each has roughly 1.5 million citizens. The current system for capturing their development priorities is a politician's memory, a stack of letters, and a WhatsApp forward chain.

UrbanOS does not replace the MP. It gives the MP's office the data infrastructure to make decisions that are **defensible, transparent, and grounded in actual citizen demand** — not just the loudest voice in the room.

The technology is not the point. The feedback loop is.

---

<div align="center">
Built with intent for the people who don't usually get heard.
<br><br>
<a href="https://urbanos.web.app">🔗 Live Demo</a> &nbsp;|&nbsp;
<a href="https://urbanos.web.app">📊 Admin Dashboard</a>
</div>
