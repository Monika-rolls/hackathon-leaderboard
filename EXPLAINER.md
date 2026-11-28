# AI Agent Observability & Hackathon Leaderboard: Complete Explainer

This guide explains **what this system is**, **what we are achieving**, the **end-to-end data flow**, the **architecture layers**, **how the leaderboard connects multiple participants**, and **how remote control from a phone works**.

---

## 1. Executive Summary: What Is This & What Are We Achieving?

### The Core Problem
When engineers code during a hackathon or daily work, they use AI coding agents like **Antigravity**, **Claude Code**, **Cursor**, **GitHub Copilot**, and **OpenCode**. These agents:
- Execute terminal shell commands
- Read, write, and patch files
- Make dozens or hundreds of LLM calls
- Encounter errors, crash, and retry
- Burn tokens and incur API costs

**The blindspot:** All this rich telemetry lives isolated in dark proprietary log files and SQLite databases buried in each developer's laptop (`~/.gemini/antigravity/...`, `~/.cursor/...`, `~/.claude/...`). Neither hackathon judges, team leads, nor the developers themselves have a unified way to see:
- Who is building what?
- Which teams are making steady progress vs who is spinning in an error loop?
- How much did agents cost across teams?
- How healthy is an agent session?

### What We Are Achieving
We created a **two-tier observability and gamification system**:
1. **Local Privacy-Preserving Agent Telemetry (`agentplay`)**:
   - Reads your local AI transcripts.
   - Strips away all private code, secrets, prompts, and file contents (**0% data leakage**).
   - Generates a lightweight, redacted `.json.gz` map containing only metadata (tool verbs, execution times, error classes, token costs).
   - Gives you a private local replay viewer on **port `7317`**.
2. **Multi-User Hackathon Control Leaderboard (`server_7400.py`)**:
   - A central hub running on **port `7400`**.
   - Receives redacted session maps from all participants.
   - Scores session health (`Run Health` from 0 to 100).
   - Displays real-time team rankings, total spend, activity sparklines, and an active event feed.

---

## 2. The Two Ports Explained: `:7317` vs `:7400`

| Feature | **Port `7317`** (`agentplay view`) | **Port `7400`** (`server_7400.py`) |
| :--- | :--- | :--- |
| **Name** | **Local Session Inspector** | **Hackathon Control Leaderboard** |
| **Audience** | Just you on your local machine | The whole hackathon (participants, mentors, judges) |
| **Scope** | Single developer's sessions | Aggregates all teams and participants |
| **What it shows** | Radial tool wheel, step timeline, diffs, exact error logs | Leaderboard ranks, Run Health score, 24h sparklines, live feed |
| **Data Source** | Reads local `~/.agentplay/maps` directly from disk | Accepts uploaded `.json.gz` maps via `POST /api/upload` |
| **Privacy Level** | Local only (unredacted or redacted view) | Strictly redacted maps only |

---

## 3. End-to-End System Architecture

Here is how data moves from a developer's keystroke to the big-screen hackathon leaderboard:

```
[ Developer working in IDE ]
  • Antigravity IDE
  • Cursor / Claude Code / Copilot
          │
          │ 1. Logs raw keystrokes, shell calls, errors
          ▼
[ Local Disk Stores ]
  • ~/.gemini/antigravity/brain/.../transcript_full.jsonl
  • ~/.cursor/.../store.db (SQLite)
  • ~/.claude/projects/.../*.jsonl
          │
          │ 2. `agentplay build` runs
          ▼
[ Ingestion & Normalization Engine (agentplay) ]
  • Discover: Finds transcripts across 11 AI harnesses
  • Materialize: Extracts SQLite DBs into JSON streams
  • Tool Taxonomy: Maps vendor commands -> canonical tools (Bash, Read, Edit, Write, Task)
  • Redactor: STRIPS source code, passwords, API keys, raw prompt text
          │
          │ 3. Produces redacted map file (3 KB - 10 KB)
          ▼
[ Redacted Session Map (agentplay-v1) ]
  • Saved locally: ~/.agentplay/maps/<session-id>.json
  • Exported: session.json.gz
          │
     ┌────┴─────────────────────────────────────────┐
     │                                              │
     ▼                                              ▼
[ Local Inspector (:7317) ]             [ Hub Upload (:7400) ]
  Developer inspects their own            Uploaded via Browser UI or
  radial map, timeline, & fixes.          `python sync_to_hub.py`        
                                                    │
                                                    ▼
                                        [ Hackathon Control Hub ]
                                          • Validates schema
                                          • Computes Run Health (0-100)
                                          • Aggregates Team stats
                                          • Renders live leaderboard
```

---

## 4. The 5 Architecture Layers

### Layer 1: AI Coding Harnesses (The Generators)
The tools you code with. Each writes logs in completely different formats:
- **Antigravity IDE**: Writes `transcript.jsonl` with `PLANNER_RESPONSE` steps and `<USER_REQUEST>` tags.
- **Claude Code**: Writes streaming project logs to `~/.claude/projects/`.
- **Cursor**: Stores transcripts in SQLite tables (`chats/*/store.db` or `state.vscdb`).
- **Copilot / VS Code**: Emits JSON state in `.copilot/session-state/`.
- **Goose / OpenCode / Codex**: SQLite or JSON session dumps.

### Layer 2: Ingestion & Taxonomy Wrappers (`agentplay` Python Core)
- **`discover.py`**: Scans known operating system directories for active agent sessions.
- **`materialize.py`**: Decodes binary SQLite databases into streamable NDJSON files.
- **`tool_taxonomy.py`**: Translates vendor-specific tool names into universal verbs:
  - Antigravity `run_command` / Cursor `run_terminal_command_v2` $\rightarrow$ **`Bash`**
  - Antigravity `read_file` / Cursor `view_file` $\rightarrow$ **`Read`**
  - Antigravity `edit_file` $\rightarrow$ **`Edit`**
  - Antigravity `write_file` $\rightarrow$ **`Write`**
  - Antigravity `invoke_subagent` $\rightarrow$ **`Task`**
- **`builder.py`**: Enforces strict redaction rules. Deletes all prompt sentences and code diff contents. Keeps only counts, tool names, timing, error categories, and token usage.

### Layer 3: Local Session Map (`agentplay-v1`)
The standardized JSON format:
```json
{
  "schema": "agentplay-v1",
  "sessionId": "local:antigravity-cli:5f10a40e-f81b-4bd1-933c-2d5299b1b290",
  "agentTool": "antigravity-cli",
  "totalCalls": 140,
  "totalErrors": 12,
  "totalCost": 0.0,
  "spans": [
    { "name": "Bash", "durationMs": 420, "error": false },
    { "name": "Read", "durationMs": 110, "error": false }
  ]
}
```

### Layer 4: Transmission Wrappers
- **Manual Web Upload**: Drag and drop `session.json.gz` directly onto the `:7400` dashboard modal.
- **CLI Sync Daemon (`sync_to_hub.py`)**: Runs in the background on a participant's laptop. As they code, it builds and posts fresh session maps to `:7400` every 20 seconds, only for sessions run in the project folder and active in the last hour.

### Layer 5: Hackathon Control Hub (`server_7400.py`)
- Python HTTP server with embedded HTML/CSS/JS frontend.
- Uses `agentplay.facts` and `agentplay.health` to compute:
  - **Run Health Score**: Starts at 100, penalized for repetitive errors, long tool timeouts, or high failure rates.
  - **Activity Sparklines**: 24-bucket timeline showing when developers were most active.
  - **Team Leaderboard**: Sums up scores, calls, and agent diversity per hackathon team.
  - **Persistent Storage**: Saves data to `leaderboard_data.json` and raw maps to `uploaded_sessions/`.

---

## 5. Multi-User Networking: Will Everyone See My Name?

### The "Localhost" Reality
When you run `server_7400.py` on your laptop, by default it listens on:
$$\text{http://127.0.0.1:7400}$$
- `127.0.0.1` is the loopback network address. It means **"this exact computer only"**.
- If another hackathon participant types `http://127.0.0.1:7400` on *their* laptop, their browser looks at *their own* machine. They will **not** see your server or your name.

### How to Connect Everyone to One Leaderboard

```
   [ Participant A ]                [ Participant B ]                [ Participant C ]
   (Laptop on Wi-Fi)                (Laptop on Wi-Fi)                (Laptop on Wi-Fi)
           │                                │                                │
           │ POST /api/upload               │ POST /api/upload               │ POST /api/upload
           ▼                                ▼                                ▼
  ═════════════════════════════════════════════════════════════════════════════════════
                       SHARED HUB URL (e.g. http://192.168.1.50:7400)
  ═════════════════════════════════════════════════════════════════════════════════════
                                            │
                                            ▼
                          [ Central Leaderboard Server ]
                            • Running on Organizer Laptop
                            • Renders unified leaderboard
                            • Projected on hackathon stage screen
```

#### Choose One of 3 Deployment Methods:

1. **Local Hackathon Wi-Fi (Simplest for in-person events)**:
   - Organizer starts the server bound to all IPs:
     ```powershell
     python server_7400.py --host 0.0.0.0 --port 7400
     ```
   - Organizer finds their Wi-Fi IP (e.g. `192.168.1.50` via `ipconfig`).
   - Every participant visits and uploads to: `http://192.168.1.50:7400`.
   - Result: Everyone sees every team update in real-time.

2. **Free Cloud Tunnel (Best for mixed remote/in-person)**:
   - Organizer keeps server running locally and opens a Cloudflare or Ngrok tunnel:
     ```powershell
     cloudflared tunnel --url http://127.0.0.1:7400
     # Or: ngrok http 7400
     ```
   - Gives a public HTTPS URL (e.g., `https://hackathon-leaderboard.trycloudflare.com`).
   - Anyone in the world can open the URL or upload sessions without firewall issues.

3. **Cloud VM (Best for 24-48h non-stop hackathons)**:
   - Deploy `server_7400.py` to a cheap cloud server (Railway, Render, AWS Lightsail, Google Cloud Run).
   - Permanent domain, zero laptop battery concerns.

---

## 6. Remote Control: Can I Run or Build Projects From My Phone?

A frequent question is: *"Can I control Antigravity from my phone while I'm at the office and my laptop is at home?"*

There are two distinct situations:

```
┌────────────────────────────────────────────────────────────────────────┐
│ SITUATION A: Home Laptop is ON & Awake                                 │
├────────────────────────────────────────────────────────────────────────┤
│ • Laptop CPU, RAM, and Wi-Fi are powered.                             │
│ • Solution: Antigravity Remote Control / Tailscale / VS Code Tunnels.  │
│ • Feasibility: 100% possible today.                                    │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ SITUATION B: Home Laptop is OFF / Shutdown                             │
├────────────────────────────────────────────────────────────────────────┤
│ • Laptop hardware is unpowered (0 Watts). Motherboard cannot run code. │
│ • Solution: Cloud Workspace / Cloud Agent VM (not your laptop).        │
│ • Feasibility: Possible via Cloud, impossible on dead physical laptop. │
└────────────────────────────────────────────────────────────────────────┘
```

### Situation A: Laptop at Home is ON (Awake & Connected)
If your laptop is plugged in and awake at home, you **can fully remote-control it from your phone**:

1. **Antigravity Remote Control**:
   - Start Antigravity in remote mode or link it via Google Account.
   - You can open the Antigravity mobile-friendly web client on your phone browser, see the chat, prompt the agent, approve commands, and inspect file edits while on the bus or at the office.
2. **VS Code Remote Tunnels**:
   - Run `code tunnel` on your home laptop.
   - Open `vscode.dev` on your phone browser or tablet. You have your full editor, terminal, and agent tools.
3. **Tailscale + SSH / Remote Desktop**:
   - Install **Tailscale** on your laptop and phone (creates a private, encrypted VPN).
   - From your phone (using Termius, Chrome Remote Desktop, or RD Client), you can securely connect to your desktop terminal or GUI from anywhere in the world without port forwarding.

> [!IMPORTANT]
> **Power Settings Tip**: On Windows, go to **Settings > System > Power & Battery** and set *"When plugged in, put my device to sleep"* to **Never**. If your laptop goes to sleep, its Wi-Fi card turns off and you lose connection.

---

### Situation B: Laptop at Home is OFF (Shutdown or Unplugged)
If your laptop is turned off:
- **Physics reality**: When power is cut, the CPU, SSD, and RAM are off. Software cannot execute on a powered-off laptop.
- *(Note on Wake-on-LAN: While Wake-on-LAN exists, it requires Ethernet cables, specialized BIOS configuration, router port forwarding, and is notoriously unreliable on Wi-Fi laptops).*

**How to build projects from your phone when your laptop is OFF:**
You move the execution environment from your physical laptop into the **Cloud**:

| Solution | How It Works | Phone Experience | Cost |
| :--- | :--- | :--- | :--- |
| **Google Cloud / Vertex AI Workbench** | Runs a managed Linux VM in Google Cloud 24/7. | Open web terminal / Jupyter / IDE in phone browser. | Free tier / pennies per hour |
| **GitHub Codespaces** | Spin up a complete dev environment directly from any GitHub repo. | Open `github.dev` in mobile browser; agents run on Microsoft servers. | 60 hours/month free |
| **Replit / Google Project IDX** | Cloud-native development environment designed for multi-device access. | Dedicated mobile app and responsive web interface. | Free / Low tier |
| **Cloud VPS (DigitalOcean / Hetzner)** | A \$4/month Linux virtual machine that never turns off. | Connect via SSH app on phone (Termius); run CLI agents like Claude Code or Gemini CLI. | \$4 - \$6 / month |

---

## 7. Step-by-Step Participant Workflow (How to Upload)

### Step 1: Export your session locally
Export your active or latest agent session to a compressed `.json.gz` map:
```powershell
# For Antigravity sessions:
$env:AGENTPLAY_ANTIGRAVITY_HOMES = "$HOME\.gemini\antigravity"
C:\Users\akhil\AppData\Roaming\Python\Python314\Scripts\agentplay.exe export --latest -o session.json.gz
```

### Step 2: Connect to the Hackathon Hub
**Option A — 1-Click Terminal Quick Join (Fastest, zero manual export)**:
1. Go to `http://<HUB-URL>:7400/join` in your browser.
2. Enter your Name and Team.
3. Copy the 1-line command and paste it into PowerShell or Bash:
   ```powershell
   irm http://127.0.0.1:7400/join.ps1 | iex -args "-Name 'Akhil' -Team 'Observability'"
   ```
   It automatically discovers your agent sessions, uploads your latest map, and keeps live sync running!

**Option B — Web UI (Drag & Drop)**:
1. Open the Hackathon Control Hub URL (e.g., `http://192.168.1.50:7400` or `http://127.0.0.1:7400`).
2. Click **"Upload session"** in the top-right corner.
3. Enter your **Name** and **Team**, select `session.json.gz`, and click **Upload**.

**Option C — Terminal Command (Manual Upload)**:
```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://192.168.1.50:7400/api/upload?participant=Akhil&team=Observability" `
  -InFile .\session.json.gz
```

**Option D — Continuous Live Auto-Sync (`sync_to_hub.py`)**:
Run it from your project folder; it syncs only sessions run in that folder and active in the last hour:
```powershell
python sync_to_hub.py --hub "http://192.168.1.50:7400" --token <TOKEN> --folder .
```

---

## 8. Summary Checklist

- [x] **Agentplay Core** (`:7317`): Strips secrets and maps AI tool actions locally.
- [x] **Hackathon Server** (`:7400`): Ingests maps, computes Run Health, and displays live team scores.
- [x] **Privacy**: Prompts, code files, and secrets are completely stripped before leaving your PC.
- [x] **Multi-User**: Share one hub IP or tunnel URL so everyone appears on the same board.
- [x] **Remote Work**: Control laptop remotely while ON via Antigravity Remote / Tunnels; run on Cloud VMs when laptop is OFF.

