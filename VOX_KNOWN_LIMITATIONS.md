<h2>VOX Known Operational Limitations &amp; Boundaries</h2>

<p><strong>Standard:</strong> Explicit, transparent disclosure of all known technical, hardware, operating system, and environmental boundaries for the VOX release candidate.<br/>
<strong>Governing Principle:</strong> No unsubstantiated claims; honest engineering boundary mapping.</p>

<hr/>

<h3>1. Operational &amp; Environmental Boundaries</h3>

<h4>1. Windows Desktop Window Manager (DWM) Minimization</h4>
<p><strong>Condition:</strong> When an active presentation or video meeting window is minimized by the user or an automated script on Windows, Desktop Window Manager (DWM) halts frame buffer composition for that window surface to save GPU memory.</p>
<p><strong>Impact:</strong> The window capture pipe receives static black frames while minimized.</p>
<p><strong>VOX Handling:</strong> VOX detects black frame sequences, logs the condition, triggers a user toast alert, and automatically falls back to full-monitor recording if local screen sharing is active. However, Windows OS constraints prohibit forcing a minimized window surface to render.</p>

<h4>2. Dynamic VRAM Fallback on Constrained GPUs</h4>
<p><strong>Condition:</strong> When available system VRAM drops below 2.5 GB (e.g. on laptops with integrated GPUs or concurrent gaming/3D workloads), CTranslate2 cannot allocate the full LLaMA-3.2-1B-Instruct model in VRAM.</p>
<p><strong>Impact:</strong> Inference falls back to CPU RAM using INT8 quantization.</p>
<p><strong>VOX Handling:</strong> Meeting Studio detects low VRAM dynamically and offloads inference to CPU threads, preventing Out-Of-Memory application crashes. Generation speed drops from ~45 tokens/sec (GPU) to ~15 tokens/sec (CPU on 8 cores), which slightly increases Stage 4 MoM generation time (~12s vs ~4s).</p>

<h4>3. Ambiguous Temporal Deadline Anchors</h4>
<p><strong>Condition:</strong> Meeting participants utter ambiguous, unanchored deadline expressions such as "couple of weeks", "next sprint", "sometime next quarter", or "when possible".</p>
<p><strong>Impact:</strong> No explicit calendar date exists in the spoken audio.</p>
<p><strong>VOX Handling:</strong> VOX explicitly refuses to fabricate or guess arbitrary calendar dates. It stores <code>resolved_date = None</code>, <code>resolution_status = "ambiguous"</code>, and an explanatory <code>uncertainty_reason</code> in <code>action_items.due_date_resolution</code> JSON.</p>

<h4>4. Bluetooth Hands-Free Profile (HFP) 8kHz Narrowband</h4>
<p><strong>Condition:</strong> When physical Bluetooth headsets operate in simultaneous microphone and speaker mode on Windows, Windows Bluetooth drivers may lock into the 8kHz/16kHz SCO profile.</p>
<p><strong>Impact:</strong> Narrowband audio frequency cutoff at 4kHz can slightly increase Whisper word error rates on accented technical vocabulary.</p>
<p><strong>VOX Handling:</strong> <code>AudioDeviceManager</code> includes profile conflict detection and alerts users to switch to standalone USB mics or A2DP stereo playback where available.</p>

<h4>5. Code Signing Authenticode Certificate</h4>
<p><strong>Condition:</strong> Standalone release executables are built without a corporate EV Authenticode certificate.</p>
<p><strong>Impact:</strong> Windows SmartScreen displays an "Unknown Publisher" prompt upon initial launch.</p>
<p><strong>VOX Handling:</strong> Cryptographic SHA256 checksums are documented for every build artifact. Full Authenticode HSM signing is tracked for Phase 15 release.</p>
