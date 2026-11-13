<h2>VOX Enterprise Risk Register</h2>

<p><strong>Standard:</strong> Enterprise Risk Register tracking technical, operational, privacy, and environmental risks across VOX Phases 6 through 15.<br/>
<strong>Classification:</strong> Critical, High, Medium, Low.</p>

<hr/>

<h3>1. Active Risk Matrix</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Risk ID</th>
      <th>Severity</th>
      <th>Category</th>
      <th>Risk Description</th>
      <th>Mitigation Strategy</th>
      <th>Residual Risk Status</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>RSK-001</strong></td>
      <td>Medium</td>
      <td>Capture / OS</td>
      <td><strong>Windows DWM Minimization Freeze:</strong> When a presenter minimizes their video window on Windows, Desktop Window Manager (DWM) suspends surface composition, generating black video frames.</td>
      <td>Detect black/frozen frame sequences in video capture loop; issue UI toast notification and automatically transition capture source to full-monitor recording.</td>
      <td><strong>MITIGATED WITH GUARDRAIL</strong> — Documented operational boundary; DWM API cannot force minimized windows to render.</td>
    </tr>
    <tr>
      <td><strong>RSK-002</strong></td>
      <td>Medium</td>
      <td>Performance</td>
      <td><strong>Low-VRAM Fallback Latency:</strong> Devices with &lt; 2.5 GB available VRAM fail GPU allocation during local LLaMA-3.2-1B inference, risking out-of-memory crashes.</td>
      <td>Dynamic CUDA memory probing in <code>LocalLLMEngine</code>; automatically fallback to CPU INT8 inference if available VRAM is insufficient.</td>
      <td><strong>MITIGATED WITH FALLBACK</strong> — Generation speed drops from ~45 tok/s to ~15 tok/s on CPU; application stability is preserved.</td>
    </tr>
    <tr>
      <td><strong>RSK-003</strong></td>
      <td>Low</td>
      <td>Audio Quality</td>
      <td><strong>Bluetooth HFP Narrowband SCO Mode:</strong> Dual-direction Bluetooth headsets (mic + playback) on Windows may force the Bluetooth driver into Hands-Free Profile (8kHz/16kHz SCO), degrading audio fidelity.</td>
      <td>Implemented <code>detect_bluetooth_profile_conflict()</code> in <code>AudioDeviceManager</code>; notifies user to use standalone mic or A2DP stereo mode where available.</td>
      <td><strong>MITIGATED</strong> — Proactive user guidance in UI; physical hardware warning verified.</td>
    </tr>
    <tr>
      <td><strong>RSK-004</strong></td>
      <td>Medium</td>
      <td>Distribution</td>
      <td><strong>Unsigned Release Candidate Binary:</strong> Standalone release executable is built without an Authenticode digital certificate, triggering Windows SmartScreen warnings on corporate systems.</td>
      <td>Provide SHA256 checksum manifests and documented installation bypass instructions until corporate HSM code signing certificate is provisioned in Phase 15.</td>
      <td><strong>TRACKED FOR PHASE 15</strong> — Standard practice for development and pilot candidates.</td>
    </tr>
    <tr>
      <td><strong>RSK-005</strong></td>
      <td>Low</td>
      <td>Test Isolation</td>
      <td><strong>Win32 Clipboard Lock Contention:</strong> Rapid programmatic clipboard reads/writes in sequential automated test harnesses can encounter transient OS access denial.</td>
      <td>Implemented bounded retry helper with Qt event queue flushing (<code>app.processEvents()</code>) strictly in test harness fixtures.</td>
      <td><strong>RESOLVED</strong> — Verified zero test flakiness across repeated stress loops.</td>
    </tr>
  </tbody>
</table>
