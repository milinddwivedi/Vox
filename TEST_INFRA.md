# E2E Test Infra: VOX vs Wispr Flow Parity & Meeting Lifecycle

## Test Philosophy
- Opaque-box, requirement-driven testing. No dependency on implementation design.
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial Testing + Real-World Workload Testing (Tiers 1–4) + Adversarial Hardening (Tier 5).

## Feature Inventory & Test Coverage
| # | Feature | Source (Requirement) | Tier 1 (Isolated) | Tier 2 (Boundary) | Tier 3 (Cross-Feature) | Tier 4 (Workloads) |
|---|---------|----------------------|:-----------------:|:-----------------:|:----------------------:|:------------------:|
| 1 | Decoupled Meeting Capture State | R3, Lifecycle Survey | 5 | 5 | ✓ | ✓ |
| 2 | Platform Window Transition Engine | R3, Lifecycle Survey | 5 | 5 | ✓ | ✓ |
| 3 | Salted Session ID & Suppression Pruning | R3, Lifecycle Survey | 5 | 5 | ✓ | ✓ |
| 4 | Clean Audio & Detector Teardown | R3, Lifecycle Survey | 5 | 5 | ✓ | ✓ |
| 5 | Guarded UI Signal Routing | R3, Lifecycle Survey | 5 | 5 | ✓ | ✓ |
| 6 | Polymorphic FlowBar 8-State Engine | R1, Toggle Bar Survey | 5 | 5 | ✓ | ✓ |
| 7 | Spring Geometry Morphing | R1, Toggle Bar Survey | 5 | 5 | ✓ | ✓ |
| 8 | Notification Popup Consolidation | R1, Toggle Bar Survey | 5 | 5 | ✓ | ✓ |
| 9 | Mini-Toggle Platform Badges & Styling | R1, Toggle Bar Survey | 5 | 5 | ✓ | ✓ |
| 10| Split Join Button & Auto-Start Progress | R1, Toggle Bar Survey | 5 | 5 | ✓ | ✓ |
| 11| Context-Aware Notetaker Tabs (2+1 Model) | R2, Notetaker Survey | 5 | 5 | ✓ | ✓ |
| 12| Integrated Notetaker Bottom Action Bar | R2, Notetaker Survey | 5 | 5 | ✓ | ✓ |
| 13| Notetaker Screen Edge Snapping | R2, Notetaker Survey | 5 | 5 | ✓ | ✓ |
| 14| Compact Floating Notetaker Bar | R2, Notetaker Survey | 5 | 5 | ✓ | ✓ |
| 15| App-wide Semantic Design Tokens | R4, UX Audit | 5 | 5 | ✓ | ✓ |
| 16| Polished Empty States & Quick Starts | R4, UX Audit | 5 | 5 | ✓ | ✓ |
| 17| Settings UX Polish & Live Mic Meter | R4, UX Audit | 5 | 5 | ✓ | ✓ |
| 18| Screen Reader & Keyboard Accessibility | R4, UX Audit | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Runner**: `pytest` executed inside project virtual environment (`.venv\Scripts\python.exe -m pytest`)
- **Key Test Files**:
  - `tests/test_meeting_lifecycle_simulation.py`: Back-to-back meeting sequences (<1s, 2s, 5s), overlapping meetings, manual stop/start, same-platform transitions.
  - `tests/test_toggle_bar_states_e2e.py`: 8-state machine, spring geometry animations, single window invariance.
  - `tests/test_notetaker_context_aware_e2e.py`: Dynamic tab visibility during recording vs post-meeting, bottom drawer, edge snapping.
  - `tests/test_ux_accessibility_regressions.py`: Semantic tokens, accessibility properties, settings, empty states.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Back-to-Back Same-Platform Meetings | F1, F2, F3, F4, F5 | High |
| 2 | Overlapping Multi-Meeting Discovery & Stack Badge | F1, F3, F6, F9 | High |
| 3 | Active Call -> Live Notes -> Auto Post-Meeting Tasks Reveal | F11, F12, F13, F14 | Medium |
| 4 | Notification Imminent -> Auto-Start Fill -> Active Call Morph | F6, F7, F8, F10 | Medium |
| 5 | Audio Disconnect / Permission Error Recovery | F4, F6, F17 | Medium |

## Publication Signal
When complete, the E2E Testing Track Orchestrator publishes `TEST_READY.md` summarizing pass counts and runner command.
