# Product Design Studio — Setup Guide

## API Keys Required

Add these to `~/.claude/settings.json` under `mcpServers.product-design.env`,
or export as environment variables before starting the swarm.

| Variable               | Where to get it                          |
|------------------------|------------------------------------------|
| `DIGIKEY_CLIENT_ID`    | developer.digikey.com → My Apps          |
| `DIGIKEY_CLIENT_SECRET`| developer.digikey.com → My Apps          |
| `MOUSER_API_KEY`       | mouser.com/api → Request API Access      |
| `NEXAR_CLIENT_ID`      | nexar.com/api → Dashboard                |
| `NEXAR_CLIENT_SECRET`  | nexar.com/api → Dashboard                |

You need **at least one vendor** configured for catalog parts search. A direct
manufacturer PDF URL can be used without a vendor key. The Design Assistant and
multimodal datasheet extraction reuse the local Codex CLI login:

```bash
codex login
codex login status
```
Nexar (Octopart) covers the most vendors in one key.

## Blender (for renders)

```bash
sudo snap install blender --classic
```

Verify: `blender --version`

After installing, `render_product(...)` MCP tool will work.

## MCP Servers registered in Claude Code

| Server name         | Port/Transport | Purpose                              |
|---------------------|---------------|--------------------------------------|
| `product-design`    | stdio         | Full pipeline: design, search, render|
| `antigravity`       | stdio         | AntiGravity model bridge             |
| `anti-gravity-queue`| http :8765    | Swarm task queue                     |

## Using the pipeline from Claude Code

```
Use design_product to build me a portable synthesizer with 4 rotary encoders,
an OLED display, USB-C power, headphone output, black anodized aluminum body.
```

Claude will call `design_product` which runs the full loop autonomously.

## Agent → Model Routing

| Agent                | Model                     | Why                                |
|----------------------|---------------------------|------------------------------------|
| Intent parsing       | Gemini 3.1 Pro (agy)      | Deep product/requirement reasoning |
| Component search     | Vendor APIs (no LLM)      | Structured REST calls              |
| Design Assistant     | Codex app-server / GPT-5.6 Luna xhigh | Controlled product workflow and approvals |
| Datasheet inspection | Codex exec / GPT-5.6 Sol medium | Low-cost visual package/page/region selection |
| Component CAD authoring | Codex exec / GPT-5.6 Luna xhigh | Symbol, SMT/THT footprint, typed CAD commands, STEP preview |
| Simple passives      | Deterministic IPC library | No model call required             |
| Connection design    | Claude Sonnet (direct API)| Best circuit reasoning             |
| Gap detection        | GPT-OSS 120B (agy)        | Independent second opinion         |

## File Structure

```
swarm/
├── vendors/
│   ├── base.py          # PartResult dataclass, HTTP helpers, RateLimiter
│   ├── digikey.py       # DigiKey API v4 (OAuth2)
│   ├── mouser.py        # Mouser Search API v1
│   ├── octopart.py      # Nexar/Octopart GraphQL
│   └── aggregator.py    # Parallel search, dedup, rank
├── agents/
│   ├── intent_agent.py  # NL → ComponentSpec list (Gemini Pro)
│   ├── datasheet_agent.py# PDF → Sol inspection → Luna CAD → preview
│   ├── connection_agent.py# Netlist design + gap detection
│   ├── design_loop.py   # Full pipeline orchestrator
│   └── product_server.py# MCP server (7 tools)
├── render/
│   ├── blender_bridge.py# Headless Blender render (Cycles)
│   └── materials.py     # Blender material definitions
└── materials/
    └── materials.yaml   # Full material + finish catalog
```
