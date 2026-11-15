# VOX — Pricing, Competitor Validation & Entitlement Specification

**Publication Date:** 2026-11-15  
**Market:** India Launch (INR ₹) with global architecture  
**Currency:** Indian Rupee (INR / ₹)  

---

## 1. Competitor Benchmark & Market Validation

To validate VOX's commercial positioning, we benchmarked official published pricing and policies across the four leading voice AI and meeting productivity tools as of November 2026:

| Product | Free Tier Allowance | Paid Monthly | Paid Annual (Billed Annually) | Processing Architecture | Privacy & Content Policy |
|---|---|---|---|---|---|
| **Wispr Flow** | ~2,000 words/week, limited formatting | $15.00 / month (~₹1,260) | $12.00 / month (~₹1,008/mo, $144/yr) | Cloud AI APIs | Audio streamed to cloud; enterprise privacy tier paid |
| **Aqua Voice** | Limited trial period | $10.00 / month (~₹840) | $8.00 / month (~₹672/mo, $96/yr) | Cloud AI APIs | Audio & transcripts processed on remote servers |
| **Voicenotes** | 1 min per note, basic summary | $10.00 / month (~₹840) | $50.00 / year (~₹350/mo, ₹4,200/yr) | Cloud LLMs | Cloud processing; stored on proprietary servers |
| **Otter.ai** | 300 minutes/month, 30 min/meeting | $16.99 / month (~₹1,425) | $10.00 / month (~₹840/mo, $120/yr) | Cloud transcription | Transcripts hosted in cloud; bot joins meetings |
| **VOX (Our Positioning)** | **3,000 words/wk + 180 min/wk + 5 mtgs/wk** | **₹199 / month** | **₹1,599 / year (~₹133.25 / month)** | **100% Local Inference** (faster-whisper + Llama 3.2) | **100% Zero-Cloud Audio Privacy**; files stay on PC |

### Strategic Insight
Competitors charge between **₹840 to ₹1,425 per month** because they pay hefty third-party cloud API bills for every single audio second transcribed. 

By operating local Whisper and Llama 3.2 on the user's PC (leveraging NVIDIA CUDA or multi-core CPU int8 inference), **VOX does not incur per-minute cloud inference fees**. This gives VOX an unassailable cost advantage in India, enabling a high-volume pricing of **₹199/month** (less than the price of a single coffee or movie ticket) while maintaining near-100% gross margins.

---

## 2. Approved VOX Plans & Financial Sustainability Assessment

### Plan Definitions

| Feature | VOX Free (₹0) | VOX Pro Monthly (₹199/mo) | VOX Pro Annual (₹1,599/yr) |
|---|---|---|---|
| **Price** | **₹0 forever** | **₹199 / month** | **₹1,599 / year** (~₹133.25/mo) |
| **Voice Dictation** | **3,000 committed words / week** | **Unlimited** | **Unlimited** |
| **Meeting Audio Recording** | **180 minutes / week** | **Unlimited** | **Unlimited** |
| **Meeting Sessions** | **5 meetings / week** | **Unlimited** | **Unlimited** |
| **Max Meeting Duration** | **90 minutes per session** | **Unlimited** | **Unlimited** |
| **Ask VOX Q&A** | **10 successful queries / week** | **Unlimited** | **Unlimited** |
| **Language Support** | English, Hindi (Devanagari), Hinglish | All supported languages | All supported languages |
| **Cross-App Insertion** | Included (all Windows apps) | Included | Included |
| **Floating Toggle Bar** | Included (Wispr Flow replica) | Included | Included |
| **Dual-Track Audio** | Mic + WASAPI Loopback included | Mic + WASAPI Loopback | Mic + WASAPI Loopback |
| **Screen Video Sync** | 1080p H.264 @ 15 FPS included | 1080p H.264 @ 15 FPS | 1080p H.264 @ 15 FPS |
| **Meeting Summaries & MoM** | Included | Included | Included |
| **Local Search & History** | Permanent local SQLite (no expiry) | Permanent local SQLite | Permanent local SQLite |
| **Offline Operation** | Bounded 72-hour grace | Bounded 72-hour grace | Bounded 72-hour grace |
| **Customer Support** | Community / standard | Priority email support | Priority email support |

---

## 3. Unit Economics & Contribution Margin Breakdown

Because VOX performs all STT and LLM computation locally on the user's machine, the variable server cost per user is limited to serverless synchronization (tiny JSON payloads on Vercel and Supabase).

### Monthly Pro (₹199.00 / month)
- **Gross Revenue:** ₹199.00
- **GST (18% on digital services):** ₹30.36
- **Net Price (excl. GST):** ₹168.64
- **Razorpay Transaction Fee (2.0% + ₹3.00 on recurring cards/UPI Autopay):**
  - ₹199.00 × 2% = ₹3.98 + ₹3.00 = ₹6.98 (+ 18% GST on fee = ₹8.24)
- **Vercel & Supabase usage metadata hosting:** ~₹1.20 / user / month
- **Estimated Refund/Chargeback reserve (2%):** ₹3.98
- **Net Contribution Margin per Pro Monthly User:** **₹155.22 / month (~78% net margin)**

### Annual Pro (₹1,599.00 / year — ₹133.25 / month equivalent)
- **Gross Revenue:** ₹1,599.00
- **GST (18%):** ₹243.93
- **Net Price (excl. GST):** ₹1,355.07
- **Razorpay Transaction Fee (2.0% on ₹1,599 + GST):** ~₹37.74
- **Hosting metadata amortized over 12 months:** ~₹14.40
- **Reserve for refunds (1.5%):** ₹23.99
- **Net Contribution Margin per Pro Annual User:** **₹1,278.94 / year (~80% net margin)**

**Conclusion:** The approved launch pricing of **₹199/month** and **₹1,599/year** is exceptionally viable and financially sustainable, producing strong positive cash flows from day one.

---

## 4. Quota Reset, Overrun & Downgrade Policies

### 1. Weekly Quota Reset Timing
All weekly quotas (Dictation words, Meeting minutes, Meeting counts, Ask VOX queries) reset strictly on:
$$\mathbf{Monday\ 00:00:00\ UTC\ (05:30:00\ IST)}$$
- The application evaluates resets against UTC timestamps obtained from the server's signed entitlement response or trusted network time, preventing users from altering their local Windows system clock to manipulate quotas.

### 2. Live Meeting Overrun Policy
- At **80% (72 minutes)** of the 90-minute single meeting allowance on Free, a subtle non-intrusive warning chip is displayed on the Contextual Meeting Panel.
- At **90 minutes**, the quota warning indicates session limit reached.
- **Critical Policy:** VOX **never** terminates an ongoing meeting recording abruptly. The user is allowed to conclude their live call safely. The actual full duration is recorded and debited from the account's weekly balance, and subsequent new sessions are prevented once the weekly allowance is exhausted.

### 3. Subscription Downgrade Policy
When a Pro subscription is cancelled or expires:
1. Future limits revert to the standard Free tier.
2. **Zero Data Loss Guarantee:** Every previously recorded meeting, audio file, screen video, transcript segment, scratchpad note, and export remains 100% locally accessible in SQLite and disk.
3. VOX never encrypts, deletes, or holds local user data hostage.
