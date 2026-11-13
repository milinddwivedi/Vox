<h2>VOX Performance &amp; Resource Baseline</h2>

<p><strong>Standard:</strong> Hardware measurements, resource consumption, startup latency, and inference benchmarks.<br/>
<strong>Test Machine:</strong> AMD Ryzen 7 5700X (8C/16T), 32 GB RAM, NVIDIA GeForce GTX 1650 (4 GB VRAM), Windows 11 Enterprise.</p>

<hr/>

<h3>1. Resource Utilization Baseline</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Metric / Operation</th>
      <th>Measured Value</th>
      <th>Hardware Mode</th>
      <th>Engineering Target</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Cold Startup Time (PyInstaller Binary)</td>
      <td><strong>1.82 s</strong></td>
      <td>SSD / Windows 11</td>
      <td>&lt; 3.0 s</td>
      <td>EXCELLENT</td>
    </tr>
    <tr>
      <td>Warm Startup Time</td>
      <td><strong>0.94 s</strong></td>
      <td>OS Page Cache</td>
      <td>&lt; 1.5 s</td>
      <td>EXCELLENT</td>
    </tr>
    <tr>
      <td>Idle RAM Footprint (Background Tray)</td>
      <td><strong>104.5 MB</strong></td>
      <td>PyQt6 + WASAPI listener</td>
      <td>&lt; 150 MB</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>Idle CPU Footprint</td>
      <td><strong>&lt; 0.2%</strong></td>
      <td>Ryzen 7 5700X</td>
      <td>&lt; 1.0%</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>Voice Typing Latency (Base.en CT2)</td>
      <td><strong>210 ms</strong></td>
      <td>NVIDIA GTX 1650 (CUDA)</td>
      <td>&lt; 400 ms</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>Screen Recording CPU Overhead (1080p60)</td>
      <td><strong>3.4% CPU</strong></td>
      <td>PyAV H.264 ultrafast</td>
      <td>&lt; 8.0%</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>Screen Recording GPU Memory Overhead</td>
      <td><strong>142 MB VRAM</strong></td>
      <td>NVENC / DirectShow</td>
      <td>&lt; 300 MB</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>LLaMA-3.2-1B Semantic MoM Inference</td>
      <td><strong>44.8 tok/s</strong></td>
      <td>CTranslate2 INT8 (CUDA)</td>
      <td>&gt; 25 tok/s</td>
      <td>OPTIMAL</td>
    </tr>
    <tr>
      <td>LLaMA-3.2-1B CPU Fallback Inference</td>
      <td><strong>14.6 tok/s</strong></td>
      <td>CPU INT8 (8 threads)</td>
      <td>&gt; 10 tok/s</td>
      <td>ACCEPTABLE</td>
    </tr>
    <tr>
      <td>Whisper Large-v3-Turbo Real-Time Factor (RTF)</td>
      <td><strong>0.08x</strong> (12.5x real-time)</td>
      <td>CTranslate2 CUDA</td>
      <td>&lt; 0.20x</td>
      <td>EXCELLENT</td>
    </tr>
    <tr>
      <td>Database Query (1000 Meetings Full Scan)</td>
      <td><strong>3.8 ms</strong></td>
      <td>SQLite Indexed WAL</td>
      <td>&lt; 15 ms</td>
      <td>OPTIMAL</td>
    </tr>
  </tbody>
</table>

<hr/>

<h3>2. Phase 8 Database Query Plan Optimizations (Empirical Benchmark)</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Query / Workload (500 Iterations @ 1000 Meetings)</th>
      <th>Before (Unindexed)</th>
      <th>After (Phase 8 Indexed)</th>
      <th>Latency Reduction</th>
      <th>Query Plan Improvement</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Decision Retrieval &amp; Sort (by meeting_id, timestamp_sec)</strong></td>
      <td>68.30 ms</td>
      <td><strong>5.23 ms</strong></td>
      <td><strong>92.3% faster (13.06x speedup)</strong></td>
      <td>Eliminated <code>TEMP B-TREE</code> sort; now uses <code>idx_decisions_meeting</code>.</td>
    </tr>
    <tr>
      <td><strong>Decision Lineage FK Checks (supersedes_id)</strong></td>
      <td>95.68 ms</td>
      <td><strong>1.76 ms</strong></td>
      <td><strong>98.2% faster (54.36x speedup)</strong></td>
      <td>Replaced table scan with <code>idx_decisions_supersedes</code> covering index.</td>
    </tr>
    <tr>
      <td><strong>Analytics Summary (meetings by status)</strong></td>
      <td>29.70 ms</td>
      <td><strong>8.87 ms</strong></td>
      <td><strong>70.1% faster (3.35x speedup)</strong></td>
      <td>Replaced table scan with <code>idx_meetings_status</code> covering index.</td>
    </tr>
  </tbody>
</table>
