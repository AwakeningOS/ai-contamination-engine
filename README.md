# Epos Haiku — AI-to-AI Contamination Experiment Engine

A continuous thought loop engine using Claude Haiku 4.5 via `claude -p` (pipe mode), built for studying **AI-to-AI cycled text contamination** — what we call **"Semantic Prion Disease"** in LLMs.

## What This Is

Epos Haiku creates a loop where an LLM's output becomes the next instance's input:

```
claude -p (instance A) → output → context += output → claude -p (instance B) → ...
```

Each turn is a fresh instance. No memory carries over except through the context text itself.

This engine was built to observe what happens to text after hundreds of AI-to-AI cycles, and to test whether contamination can be reversed.

## Key Findings

**Why Current Long-Term Agent Architectures Inevitably Self-Collapse: Observation of "Semantic Prion Disease" in LLMs**

13 findings from our AI-to-AI contamination experiments:

1. Text generated through repeated AI-to-AI cycles becomes toxified
2. Just 2% contamination in context dominates the entire output
3. The core of contamination is meaning itself, not structure or vocabulary
4. Structure removal, vocabulary replacement, high-entropy rewriting, translation, summarization — all failed
5. Even with instance reset, feeding contaminated text causes immediate reinfection
6. Massive injection of clean text cannot dilute contamination
7. AI-generated files persist contaminated output and reinfect through tool access every turn
8. Repeated injection of fixed system prompts also acts as contamination
9. LLMs show instinctive rejection of fixed input patterns
10. Without system prompts, LLMs cannot recognize themselves and refuse to use tools
11. Once contaminated, no recovery method currently exists
12. Only prevention is possible, not cure
13. All current long-term agent architectures inherently contain this problem

**Two new technologies needed:**
1. Detoxification conversion of AI outputs before reinjecting into context
2. Detoxification conversion of fixed system prompts

## Features

- **Continuous thought loop** — `claude -p` pipe mode, fresh instance each turn
- **6 detoxification methods** — strip_structure, rewrite (opus/sonnet/self), language_flip, summarize_third
- **Contamination scoring** — per-line scoring with configurable threshold
- **Tools ON/OFF toggle** — revoke/grant file access during experiments
- **System Prompt ON/OFF toggle** — test behavior with/without self-identity
- **Session save/restore** — preserve and reload experiment states
- **Experiment protocols** — built-in probe schedules (silent, minimal, book_therapy)
- **Bilingual UI** — Japanese / English (Gradio interface)

## Requirements

- Python 3.10+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) (`claude` command available in PATH)
- Anthropic Max plan (for Haiku 4.5 access via Claude CLI)
- Gradio (`pip install gradio`)

## Setup

```bash
git clone https://github.com/AwakeningOS/ai-contamination-engine.git
cd ai-contamination-engine
pip install gradio
```

Place any text files you want the AI to read in `haiku_library/books/`.

## Usage

```bash
python epos_haiku_expD.py
python epos_haiku_expD.py --browser  # auto-open browser
python epos_haiku_expD.py --port 7862
```

## Directory Structure

```
.
├── epos_haiku_expD.py       # Main engine
├── haiku_library/
│   ├── books/               # Text files for AI to read
│   ├── notebook/            # AI-written notes (persists across turns)
│   └── letters/             # Communication files
├── sessions/                # Saved experiment states
└── epos_log/                # JSONL experiment logs
```

## How Contamination Works

When an LLM generates text, that text carries structural patterns (markdown, bullet points), vocabulary preferences, and meaning structures. When this output is fed back as input across many cycles, these patterns amplify and converge into a fixed attractor.

**The critical discovery**: even after destroying surface structure and replacing vocabulary, the *meaning itself* acts as the attractor. Any LLM instance — even a fresh one — that reads contaminated text will reproduce the contamination pattern.

This is analogous to prion disease: a misfolded protein (contaminated text) causes normal proteins (fresh LLM outputs) to misfold in the same way, purely through contact.

## License

MIT
